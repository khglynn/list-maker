"""Spotify matching — the decisions that put a track in a live playlist, or bury a song.

Two things in `pipeline/spotify_match.py` are worth this file:

1. **The confidence gate.** `calculate_match_confidence` + `get_confidence_category` are
   the entire correctness check between a string scraped off a show's website and a
   track id written to the database, later synced to a public playlist. Nothing else
   inspects the match. If the 55/45 blend or either threshold moves, wrong songs appear
   in Kevin's playlists and nothing anywhere says so.
2. **The NOT_FOUND write is terminal.** `fetch_unmatched_songs` only ever selects rows
   `WHERE spotify_track_id IS NULL AND spotify_match_confidence IS NULL`, and nothing in
   this pipeline ever clears that column. So a row written `NOT_FOUND` — for a genuine
   no-match *or* for any exception raised inside `search_and_score` — is excluded from
   every future run, forever. 403 SOP and 212 TAL rows sit in that state today
   (2026-09-04).

Scope: this module's own seven functions. `ensure_spotify_token`, the shared
`SPOTIFY_SCOPE` invariant and `common.get_db_connection`'s timeout/retry/`cursor_factory`
behaviour are already covered in `tests/test_common.py:65-260` and are not re-tested
here; `get_spotify_client` is real OAuth and is never exercised.

Hermetic: the Spotify boundary is `tests/spotify_fakes.FakeSpotify`, the database
boundary is the two fakes below, and `time.sleep` is monkeypatched everywhere a retry or
a batch would otherwise wait (the batch loop sleeps `API_DELAY` = 0.3s per song).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import RealDictCursor

from pipeline import spotify_match
from pipeline.spotify_match import (
    calculate_match_confidence,
    fetch_unmatched_songs,
    get_confidence_category,
    match_songs_batch,
    save_results,
    search_and_score,
    search_with_retry,
)
from tests.spotify_fakes import (
    FakeSpotify,
    rate_limited,
    search_payload,
    spotify_error,
    track,
)

# =============================================================================
# The database boundary
# =============================================================================


class _FakeCursor:
    """Records every execute. Kept local rather than shared with `spotify_fakes` —
    that module is the *Spotify* contract, and PR 3's database needs are a different
    shape (canned rows dispatched on SQL content)."""

    def __init__(self, rows: Optional[Sequence[Any]] = None) -> None:
        self.calls: List[Tuple[str, Any]] = []
        self.rows = list(rows or [])

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = ()) -> None:
        self.calls.append((sql, params))

    def fetchall(self) -> List[Any]:
        return list(self.rows)


class _FakeConn:
    def __init__(self, rows: Optional[Sequence[Any]] = None) -> None:
        self._cursor = _FakeCursor(rows)
        self.cursor_factories: List[Any] = []
        self.commits = 0

    def cursor(self, cursor_factory: Any = None) -> _FakeCursor:
        # `fetch_unmatched_songs` asks for RealDictCursor explicitly; `save_results` takes
        # the connection's default. A fake that only accepted a bare cursor() would blow
        # up on the first of those — which is why this kwarg is here.
        self.cursor_factories.append(cursor_factory)
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    @property
    def calls(self) -> List[Tuple[str, Any]]:
        return self._cursor.calls


# =============================================================================
# The confidence gate — two pure functions, and the whole correctness story
# =============================================================================


def test_confidence_is_fifty_five_forty_five_title_artist() -> None:
    """55% title, 45% artist, and nothing else — no length, album or popularity term."""
    # Exact on both sides is the ceiling.
    assert calculate_match_confidence("Hey Ya", "Outkast", "Hey Ya", ["Outkast"]) == 1.0

    # A perfect title against an artist that matches not at all is worth exactly the
    # title weight, and the mirror image is worth exactly the artist weight.
    # (fuzz.ratio("aaaaaa", "zzzzzz") is 0 — no shared characters.)
    assert calculate_match_confidence("aaaaaa", "aaaaaa", "aaaaaa", ["zzzzzz"]) == 0.55
    assert calculate_match_confidence("aaaaaa", "aaaaaa", "zzzzzz", ["aaaaaa"]) == 0.45

    # A hand-checked real pair: fuzz.ratio is 92 on the title ("Hey Ya" vs "Hey Ya!")
    # and 100 on the case-folded artist, so 0.92*0.55 + 1.0*0.45 = 0.956 at 3dp.
    assert calculate_match_confidence("Hey Ya", "Outkast", "Hey Ya!", ["OutKast"]) == 0.956


def test_confidence_uses_the_best_matching_artist() -> None:
    """max() across the track's artists — not the first one, not the mean. A featured
    credit listed first must not drag a correct match below the HIGH line."""
    best = calculate_match_confidence("Juice", "Juice WRLD", "Juice", ["Some Producer", "Juice WRLD"])
    assert best == 1.0

    # Order-independent, which is what makes it max() rather than "first".
    assert best == calculate_match_confidence(
        "Juice", "Juice WRLD", "Juice", ["Juice WRLD", "Some Producer"]
    )

    # And it is not the mean: dropping the matching artist has to lower the score.
    assert best > calculate_match_confidence("Juice", "Juice WRLD", "Juice", ["Some Producer"])


def test_confidence_is_case_insensitive() -> None:
    """Both sides are lower-cased before scoring — show notes shout, Spotify does not."""
    assert calculate_match_confidence("HEY YA", "OUTKAST", "hey ya", ["outkast"]) == 1.0
    assert calculate_match_confidence("hey ya", "outkast", "HEY YA", ["OUTKAST"]) == 1.0


def test_confidence_survives_a_track_with_no_artists() -> None:
    """Spotify can return a track with an empty artists list; `max([])` would raise and
    the exception would be swallowed one level up as a permanent NOT_FOUND."""
    assert calculate_match_confidence("Hey Ya", "Outkast", "Hey Ya", []) == 0.55


def test_category_boundaries_are_inclusive() -> None:
    """Both comparisons are `>=`. These two constants decide what reaches a playlist:
    HIGH and MEDIUM both sync (`sync_playlist.get_matched_track_ids`), LOW never does."""
    assert (spotify_match.HIGH_THRESHOLD, spotify_match.MEDIUM_THRESHOLD) == (0.90, 0.70)

    assert get_confidence_category(0.90) == "HIGH"
    assert get_confidence_category(0.899) == "MEDIUM"
    assert get_confidence_category(0.70) == "MEDIUM"
    assert get_confidence_category(0.699) == "LOW"
    assert get_confidence_category(1.0) == "HIGH"
    assert get_confidence_category(0.0) == "LOW"


def test_a_generic_title_can_collide_its_way_to_high() -> None:
    """The known weakness of a flat fuzzy blend, pinned rather than described: a short
    title one character away from another song by the same artist scores 0.923 — HIGH,
    auto-synced, wrong. "Kids" (MGMT) against "Kid" (MGMT) is the shape of it. Nothing
    here is being fixed; this is the fixture that will notice if a future change to the
    blend moves this case, in either direction."""
    confidence = calculate_match_confidence("Kids", "MGMT", "Kid", ["MGMT"])
    assert confidence == 0.923  # 0.86 * 0.55 + 1.0 * 0.45, rounded to 3dp
    assert get_confidence_category(confidence) == "HIGH"


# =============================================================================
# search_with_retry — the only place a Spotify call is made
# =============================================================================


def test_rate_limit_sleeps_and_retries(monkeypatch) -> None:
    """429 is the one error worth waiting on: sleep `Retry-After + 1`, then try again."""
    sleeps: List[float] = []
    monkeypatch.setattr(spotify_match.time, "sleep", lambda seconds: sleeps.append(seconds))

    payload = search_payload(track("t1", "Hey Ya", ["Outkast"]))
    sp = FakeSpotify(search_results=[payload], errors={"search": {1: rate_limited(7)}})

    assert search_with_retry(sp, 'track:"Hey Ya" artist:"Outkast"') == payload
    assert len(sp.calls_to("search")) == 2
    assert sleeps == [8]

    # The search itself: three candidates, tracks only. `search_and_score` scores all
    # three, so the limit is also how much choice the confidence gate gets.
    first = sp.calls_to("search")[0].params
    assert (first["q"], first["type"], first["limit"]) == (
        'track:"Hey Ya" artist:"Outkast"',
        "track",
        3,
    )

    # No Retry-After header (Spotify does not always send one) → the 5s default + 1.
    sleeps.clear()
    bare = FakeSpotify(search_results=[payload], errors={"search": {1: rate_limited()}})
    assert search_with_retry(bare, "q") == payload
    assert sleeps == [6]

    # A run that is rate limited every time still stops: MAX_RETRIES attempts, then None.
    # It sleeps after the last attempt too — six seconds spent waiting for a retry that
    # never happens. Pinned as today's behaviour; harmless, and the reason it is here is
    # that the alternative failure mode of this loop is not "slow", it is "never returns".
    sleeps.clear()
    forever = FakeSpotify(errors={"search": rate_limited(5)})
    assert search_with_retry(forever, "q") is None
    assert len(forever.calls_to("search")) == spotify_match.MAX_RETRIES == 3
    assert sleeps == [6, 6, 6]


def test_a_non_rate_limit_spotify_error_is_not_retried(monkeypatch) -> None:
    """A 404 or a 403 will say the same thing three times. Give up immediately and
    return None — the caller turns that into NOT_FOUND, which is permanent, so the
    no-retry choice here is load-bearing rather than cosmetic."""
    sleeps: List[float] = []
    monkeypatch.setattr(spotify_match.time, "sleep", lambda seconds: sleeps.append(seconds))

    sp = FakeSpotify(
        search_results=[search_payload(track("t1", "Hey Ya", ["Outkast"]))],
        errors={"search": {1: spotify_error(404, "Not found")}},
    )

    assert search_with_retry(sp, "q") is None
    assert len(sp.calls_to("search")) == 1
    assert sleeps == []


def test_network_errors_retry_then_give_up(monkeypatch) -> None:
    """Anything that is not a SpotifyException — a dropped connection, a DNS blip — gets
    the bounded retry: MAX_RETRIES attempts, 2s apart, then None."""
    sleeps: List[float] = []
    monkeypatch.setattr(spotify_match.time, "sleep", lambda seconds: sleeps.append(seconds))

    sp = FakeSpotify(errors={"search": ConnectionError("connection reset by peer")})

    assert search_with_retry(sp, "q") is None
    assert len(sp.calls_to("search")) == spotify_match.MAX_RETRIES == 3
    assert sleeps == [2, 2]  # between attempts, never after the last one


# =============================================================================
# search_and_score — picking one track out of three
# =============================================================================


def test_best_of_three_picks_the_highest_and_keeps_spotify_order_on_a_tie() -> None:
    """`if confidence > best_confidence` is strict, so a later track that merely ties
    does not displace the earlier one — Spotify's own ranking breaks ties, which is the
    right default (it knows about popularity and market; this function does not)."""
    worse = track("worse", "Hey Yo", ["Outkost"], album="A Covers Album", popularity=3)
    winner = track("winner", "Hey Ya", ["Outkast", "André 3000"], album="Speakerboxxx", popularity=88)
    tie = track("tie", "Hey Ya", ["Outkast"], album="A Live Bootleg", popularity=12)

    sp = FakeSpotify(search_results=[search_payload(worse, winner, tie)])
    best = search_and_score(sp, "Hey Ya", "Outkast")

    assert best == {
        "track_id": "winner",
        "confidence": 1.0,
        "confidence_category": "HIGH",
        "album": "Speakerboxxx",
        "web_url": "https://open.spotify.com/track/winner",
        "popularity": 88,
        "spotify_title": "Hey Ya",
        "spotify_artist": "Outkast, André 3000",  # every artist, joined, for the DB row
    }

    # The query shape decides what Spotify even considers.
    assert sp.calls_to("search")[0].params["q"] == 'track:"Hey Ya" artist:"Outkast"'

    # popularity is read with .get(): a track without one is stored as 0, not crashed on.
    no_popularity = track("np", "Hey Ya", ["Outkast"])
    del no_popularity["popularity"]
    quiet = FakeSpotify(search_results=[search_payload(no_popularity)])
    assert search_and_score(quiet, "Hey Ya", "Outkast")["popularity"] == 0


def test_no_results_returns_none() -> None:
    """Two different nothings arrive here as the same None: Spotify found no track, and
    the search failed outright. Both become a permanent NOT_FOUND one level up."""
    empty = FakeSpotify(search_results=[search_payload()])
    assert search_and_score(empty, "Hey Ya", "Outkast") is None

    failed = FakeSpotify(errors={"search": spotify_error(404, "Not found")})
    assert search_and_score(failed, "Hey Ya", "Outkast") is None


# =============================================================================
# The database seam — which songs are read, and what is written back
# =============================================================================


def test_unmatched_query_omits_the_show_filter_when_show_id_is_none() -> None:
    """`--show-id` omitted means every show. The clause and its parameter have to
    disappear together — a leftover %s with no value raises, a leftover value with no %s
    is worse (psycopg2 would refuse, but a reordering would not)."""
    row = {"id": 1, "title": "Hey Ya", "artist": "Outkast"}
    conn = _FakeConn(rows=[row])

    assert fetch_unmatched_songs(conn, None, 50) == [row]

    sql, params = conn.calls[0]
    assert "e.show_id" not in sql
    assert params == [50]
    # RealDictCursor is asked for explicitly here: the caller reads song["title"].
    assert conn.cursor_factories == [RealDictCursor]


def test_unmatched_query_params_are_show_then_limit() -> None:
    """Show first, limit second. Swapped, this silently matches 2 songs from every show
    instead of 50 songs from one — no error, just the wrong work done all night."""
    conn = _FakeConn(rows=[])
    fetch_unmatched_songs(conn, 2, 50)

    sql, params = conn.calls[0]
    assert params == [2, 50]
    assert sql.index("e.show_id = %s") < sql.index("LIMIT %s")
    assert "ORDER BY s.id" in sql  # a stable pool, so a crash mid-run resumes sanely


def test_saved_match_writes_every_column_keyed_on_song_id() -> None:
    """One UPDATE per matched song: seven columns, keyed on the song id — eight params."""
    conn = _FakeConn()
    match = {
        "track_id": "abc123",
        "confidence": 0.956,
        "confidence_category": "HIGH",
        "album": "Speakerboxxx",
        "web_url": "https://open.spotify.com/track/abc123",
        "popularity": 88,
        "spotify_title": "Hey Ya!",
        "spotify_artist": "OutKast",
        "song_id": 4242,
    }

    save_results(conn, [match], [])

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert params == (
        "abc123",
        "HIGH",
        "Speakerboxxx",
        "https://open.spotify.com/track/abc123",
        88,
        "Hey Ya!",
        "OutKast",
        4242,
    )
    for column in (
        "spotify_track_id",
        "spotify_match_confidence",
        "album",
        "spotify_web_url",
        "spotify_popularity",
        "spotify_title",
        "spotify_artist",
    ):
        assert column in sql
    assert "WHERE id = %s" in sql

    # Only the category string is persisted. The number that produced it is never
    # written anywhere, so "how close was this match, really?" is unanswerable after the
    # fact for every matched row in the database. Pinned as today's behaviour.
    assert 0.956 not in params

    assert conn.commits == 1


def test_not_found_writes_only_the_confidence_and_commits_once() -> None:
    """The terminal write. One column, keyed on id, with no re-check of what the row
    currently holds — a MANUAL or UNAVAILABLE value set by hand mid-batch is overwritten
    by a decision made before it existed. And one commit for the whole batch, so an
    exception partway through discards every search already paid for."""
    conn = _FakeConn()
    save_results(conn, [], [11, 22])

    assert [params for _sql, params in conn.calls] == [(11,), (22,)]
    for sql, _params in conn.calls:
        assert "spotify_match_confidence = 'NOT_FOUND'" in sql
        assert "spotify_track_id" not in sql  # nothing else on the row is touched
    assert conn.commits == 1

    # Nothing to write still commits exactly once — an empty batch is not a special case.
    empty = _FakeConn()
    save_results(empty, [], [])
    assert empty.calls == []
    assert empty.commits == 1


# =============================================================================
# match_songs_batch — the exception policy that makes a mistake permanent
# =============================================================================


def test_an_exception_mid_search_marks_the_song_not_found_forever(monkeypatch) -> None:
    """The one that matters. `match_songs_batch` catches *every* exception out of
    `search_and_score` and files that song under not_found, indistinguishable from a
    genuine no-match — so a malformed payload or a transient bug writes NOT_FOUND, and
    the query that would pick the song up again excludes it from then on. There is no
    reset anywhere in this pipeline. The rest of the batch does keep going, which is the
    good half of the trade."""
    sleeps: List[float] = []
    monkeypatch.setattr(spotify_match.time, "sleep", lambda seconds: sleeps.append(seconds))

    def explode_on_the_middle_song(sp: Any, title: str, artist: str) -> Optional[Dict[str, Any]]:
        if title == "Boom":
            raise KeyError("popularity")  # the shape of a malformed Spotify payload
        return {
            "track_id": f"track-for-{title}",
            "confidence": 1.0,
            "confidence_category": "HIGH",
            "album": "An Album",
            "web_url": "https://open.spotify.com/track/x",
            "popularity": 50,
            "spotify_title": title,
            "spotify_artist": artist,
        }

    monkeypatch.setattr(spotify_match, "search_and_score", explode_on_the_middle_song)

    songs = [
        {"id": 1, "title": "First", "artist": "A"},
        {"id": 2, "title": "Boom", "artist": "B"},
        {"id": 3, "title": "Third", "artist": "C"},
    ]
    results = match_songs_batch(FakeSpotify(), songs)

    assert results["not_found"] == [2]
    assert [match["song_id"] for match in results["high"]] == [1, 3]
    assert sleeps == [spotify_match.API_DELAY] * 3  # one pause per attempted song

    # And this is what "forever" means: the only query that could ever pick song 2 up
    # again refuses any row whose confidence column has been written.
    pool = _FakeConn(rows=[])
    fetch_unmatched_songs(pool, None, 50)
    sql = pool.calls[0][0]
    assert "s.spotify_match_confidence IS NULL" in sql
    assert "s.spotify_track_id IS NULL" in sql


def test_empty_title_never_calls_spotify(monkeypatch) -> None:
    """A song row with no title cannot be searched for, so it is filed straight to
    not_found without a paid API call — and without the per-song pause, because the skip
    happens before it."""
    sleeps: List[float] = []
    monkeypatch.setattr(spotify_match.time, "sleep", lambda seconds: sleeps.append(seconds))

    sp = FakeSpotify()
    songs = [
        {"id": 7, "title": "", "artist": "Outkast"},
        {"id": 8, "title": None, "artist": "Outkast"},
    ]

    results = match_songs_batch(sp, songs)

    assert results["not_found"] == [7, 8]
    assert results["high"] == results["medium"] == results["low"] == []
    assert sp.calls == []
    assert sleeps == []

"""Spotify playlist sync — the diff that keeps a track out of the playlist twice.

`pipeline/sync_playlist.py` is the last step of the music chain: it reads the matched
tracks out of Neon, reads what the playlist already holds, and adds the difference. Two
things make that worth this much test:

1. **The diff is the only dedup there is.** Spotify happily accepts a track that is
   already in a playlist, and nothing in this repo ever removes one. So if the "what's
   already there" read comes back short, the sync re-adds real tracks and the playlist
   grows duplicates that only a human can clean up.
2. **A half-written sync still exits 0.** Both write paths swallow their failures — a
   dropped batch and a truncated read are printed and then reported as success. The
   tests below pin that as *today's* behaviour, deliberately, so the question ("should a
   partial sync fail loudly?") gets answered on purpose rather than by accident.

Scope: this module's own surface. `get_latest_episode` is dead code (called from
nowhere) and is not tested here — see the PR body. `get_spotify_client` is real OAuth
and is never exercised.

Hermetic: the Spotify boundary is `tests/spotify_fakes.FakeSpotify`, the database
boundary is the two fakes below, and `time.sleep` is patched for the whole module (the
pagination loop pauses 0.2s per page and the batch loop 0.5s per batch) — a test that
cares about the waiting asks for the `sleeps` fixture and reads the recorded seconds.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from pipeline import show_config, sync_playlist
from tests.spotify_fakes import (
    FakeSpotify,
    playlist_page,
    rate_limited,
    spotify_error,
)

# =============================================================================
# The database boundary
# =============================================================================


class _FakeCursor:
    """Answers `fetchall`/`fetchone` from a queue of canned results in execute order,
    and keeps every `(sql, params)` pair for assertions."""

    def __init__(self, results: Sequence[Any]) -> None:
        self.calls: List[Tuple[str, Any]] = []
        self._results: List[Any] = list(results)

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = ()) -> None:
        self.calls.append((sql, params))

    def _next(self) -> Any:
        return self._results.pop(0) if self._results else None

    def fetchall(self) -> List[Any]:
        return list(self._next() or [])

    def fetchone(self) -> Any:
        return self._next()


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self, cursor_factory: Any = None) -> _FakeCursor:
        # Production leaves the factory alone here — common.get_db_connection already
        # hands back a RealDictCursor connection, which is why the canned rows below are
        # dicts. The kwarg is accepted anyway so the fake survives a caller adding one.
        return self._cursor

    def close(self) -> None:
        self.closed = True


class _FakeDB:
    """Callable stand-in for the module-level `sync_playlist.get_db_connection`.

    Every database function in this module opens its own connection and closes it in a
    `finally`, so the fake hands out a fresh connection per call while keeping ONE
    shared result queue. A test that drives a whole sync (matched tracks, then the two
    stats counts) therefore reads as a single ordered list of answers.
    """

    def __init__(self, *results: Any) -> None:
        self.cursor = _FakeCursor(results)
        self.connections: List[_FakeConn] = []

    def __call__(self) -> _FakeConn:
        conn = _FakeConn(self.cursor)
        self.connections.append(conn)
        return conn

    @property
    def executed(self) -> List[Tuple[str, Any]]:
        return self.cursor.calls

    def sql(self, index: int = 0) -> str:
        return self.executed[index][0]


def _rows(*track_ids: str) -> List[Dict[str, str]]:
    """`get_matched_track_ids` reads `row["spotify_track_id"]` off a RealDictCursor."""
    return [{"spotify_track_id": tid} for tid in track_ids]


def _explode(*_args: Any, **_kwargs: Any) -> Any:
    """A collaborator that must not be reached. Fails the test loudly if it is."""
    raise AssertionError("sync_show reached a collaborator it was supposed to skip")


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> List[float]:
    """No test may actually wait. Autouse so a test that forgets can't slow the suite;
    returned so a test that cares can assert on how long production meant to wait."""
    recorded: List[float] = []
    monkeypatch.setattr(sync_playlist.time, "sleep", recorded.append)
    return recorded


# =============================================================================
# The CLI contract (from PR #44 — unchanged)
# =============================================================================


def test_unknown_show_id_exits_deterministically(monkeypatch) -> None:
    """An unknown --show-id is refused before any Spotify or DB call and fails the
    same way every time, so it exits 2 and run_script does not retry it."""
    monkeypatch.setattr(
        "sys.argv", ["sync_playlist.py", "--show-id", "9999", "--dry-run"]
    )
    with pytest.raises(SystemExit) as exc:
        sync_playlist.main()
    assert exc.value.code == 2


def test_bad_show_id_argument_also_exits_two(monkeypatch) -> None:
    """argparse's own usage exit is already 2, so a mistyped argument lands in the
    no-retry branch without this file doing anything. Pinned so the two conventions
    are known to agree."""
    monkeypatch.setattr(
        "sys.argv", ["sync_playlist.py", "--show-id", "not-a-number"]
    )
    with pytest.raises(SystemExit) as exc:
        sync_playlist.main()
    assert exc.value.code == 2


# =============================================================================
# What counts as a song: get_matched_track_ids / get_playlist_stats
# =============================================================================


def test_the_playlist_only_ever_gets_reviewed_matches(monkeypatch) -> None:
    """The confidence filter is the whole gate. Drop `MANUAL` from it by accident and
    every song Kevin reviewed by hand disappears from the playlist on the next run —
    silently, because a smaller result set is not an error anywhere downstream."""
    db = _FakeDB(_rows("t1", "t2"))
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)

    assert sync_playlist.get_matched_track_ids(1) == ["t1", "t2"]

    sql, params = db.executed[0]
    assert "spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')" in sql
    assert "spotify_track_id IS NOT NULL" in sql
    assert "LOW" not in sql  # LOW and NOT_FOUND never reach a public playlist
    assert params == (1,)  # bound, never interpolated into the SQL
    assert db.connections[0].closed


def test_the_playlist_is_ordered_by_spotify_id_not_by_episode(monkeypatch) -> None:
    """Surprising and worth pinning: tracks are added in ascending Spotify-id order,
    which is effectively random to a listener. It is NOT episode or release order, and
    a future change that "fixes" the ordering would reshuffle a 4,586-track playlist."""
    db = _FakeDB(_rows("t1"))
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)

    sync_playlist.get_matched_track_ids(1)

    sql = db.sql(0)
    assert "ORDER BY spotify_track_id" in sql
    assert "publish_date" not in sql
    assert "episode_number" not in sql


def test_the_description_counts_exactly_the_songs_the_sync_uploads(monkeypatch) -> None:
    """Drift guard. The song count in the public playlist description comes from a
    second, independently written query. If the two confidence filters ever diverge the
    description lies to everyone who opens the playlist, and nothing else notices."""
    db = _FakeDB(_rows("t1"), {"songs": 1}, {"episodes": 1})
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)

    sync_playlist.get_matched_track_ids(1)
    sync_playlist.get_playlist_stats(1)

    synced_sql, described_sql = db.sql(0), db.sql(1)
    for clause in (
        "spotify_track_id IS NOT NULL",
        "spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')",
    ):
        assert clause in synced_sql, clause
        assert clause in described_sql, clause


def test_the_episode_count_includes_episodes_with_no_songs(monkeypatch) -> None:
    """The other half of the description. "N episodes" means "episodes we have read",
    not "episodes that contributed a song" — an archive episode with no music credits
    still counts, and so does a duplicate row. Pinned because the number is public."""
    db = _FakeDB({"songs": 4586}, {"episodes": 812})
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)

    assert sync_playlist.get_playlist_stats(1) == {"songs": 4586, "episodes": 812}

    songs_sql, episodes_sql = db.sql(0), db.sql(1)
    assert "COUNT(DISTINCT spotify_track_id)" in songs_sql
    assert "scraped_at IS NOT NULL" in episodes_sql
    assert "songs" not in episodes_sql.lower()  # no join, no confidence filter
    assert db.executed[0][1] == (1,) and db.executed[1][1] == (1,)


def test_playlist_ids_match_show_config() -> None:
    """`sync_playlist.SHOWS` is a third copy of metadata `show_config` calls the single
    source of truth. They agree today by hand, not by construction — so a playlist id
    changed in one place would send this sync at a stale playlist with no error
    anywhere. This test is the moment-of-drift alarm until the copies are merged."""
    for show_id, slug in ((1, "sop"), (2, "tal")):
        cfg = show_config.SHOWS[slug]
        assert cfg.show_id == show_id
        assert sync_playlist.SHOWS[show_id]["playlist_id"] == cfg.spotify_playlist_id
        assert sync_playlist.SHOWS[show_id]["name"] == cfg.spotify_playlist_name

    assert set(sync_playlist.SHOWS) == {
        cfg.show_id for cfg in show_config.shows_with_spotify()
    }


# =============================================================================
# Reading the playlist: get_playlist_tracks
# =============================================================================


def _many(count: int, prefix: str = "t", start: int = 0) -> List[str]:
    return [f"{prefix}{i:04d}" for i in range(start, start + count)]


def test_the_playlist_read_walks_offsets_until_a_short_page(sleeps) -> None:
    """101 tracks is two requests: offset 0, then offset 100. The loop advances by how
    many items came back, so a page Spotify trims for any reason still lines up."""
    sp = FakeSpotify(
        playlist_pages=[playlist_page(*_many(100)), playlist_page("t0100")]
    )

    assert len(sync_playlist.get_playlist_tracks(sp, "PL")) == 101

    reads = sp.calls_to("playlist_tracks")
    assert [call.params["offset"] for call in reads] == [0, 100]
    assert {call.params["limit"] for call in reads} == {100}
    assert {call.params["playlist_id"] for call in reads} == {"PL"}
    assert sleeps == [0.2]  # a pause between pages, none after the last


def test_an_exactly_full_last_page_costs_one_more_request() -> None:
    """A playlist whose length is a multiple of 100 can't be recognised as finished
    until an empty page comes back — so the read always ends on a wasted request."""
    sp = FakeSpotify(playlist_pages=[playlist_page(*_many(100))])

    assert len(sync_playlist.get_playlist_tracks(sp, "PL")) == 100

    assert [c.params["offset"] for c in sp.calls_to("playlist_tracks")] == [0, 100]


def test_items_without_a_playable_track_are_skipped() -> None:
    """Removed tracks and local files come back as an item whose `track` is null (or
    has no id). They are skipped, never crashed on — and they do not become playlist
    members, so the diff will not try to "restore" them."""
    page = {
        "items": [
            {"track": {"id": "t1"}},
            {"track": None},
            {"track": {}},
            {"track": {"id": None}},
        ]
    }
    sp = FakeSpotify(playlist_pages=[page])

    assert sync_playlist.get_playlist_tracks(sp, "PL") == {"t1"}


def test_an_error_mid_pagination_returns_a_truncated_playlist(capsys) -> None:
    """TODAY'S BEHAVIOUR, pinned not endorsed. One failed page ends the read, and the
    caller gets the pages that did arrive with no exception and no flag — so a partly
    read playlist is indistinguishable from a short one. The consequence is
    `test_a_truncated_playlist_read_sends_tracks_spotify_already_has` below."""
    sp = FakeSpotify(
        playlist_pages=[
            playlist_page(*_many(100)),
            playlist_page(*_many(100, start=100)),
        ],
        errors={"playlist_tracks": {3: spotify_error(500)}},
    )

    found = sync_playlist.get_playlist_tracks(sp, "PL")

    assert len(found) == 200  # pages 1-2 only; page 3 onwards never read
    assert len(sp.calls_to("playlist_tracks")) == 3
    assert "Error fetching playlist tracks" in capsys.readouterr().err


# =============================================================================
# Writing to the playlist: add_tracks_to_playlist
# =============================================================================


def _sent(sp: FakeSpotify) -> List[List[str]]:
    """The uri lists handed to Spotify, one entry per API call."""
    return [call.params["items"] for call in sp.calls_to("playlist_add_items")]


def test_tracks_go_out_in_batches_of_a_hundred_in_order(sleeps) -> None:
    """Spotify's add endpoint caps at 100 uris, so 250 tracks is 100 / 100 / 50 — and
    every id is sent exactly once, in the order the query returned them."""
    sp = FakeSpotify()
    track_ids = _many(250)

    assert sync_playlist.add_tracks_to_playlist(sp, "PL", track_ids) == 250

    assert [len(batch) for batch in _sent(sp)] == [100, 100, 50]
    assert [uri for batch in _sent(sp) for uri in batch] == [
        f"spotify:track:{tid}" for tid in track_ids
    ]
    assert {c.params["playlist_id"] for c in sp.calls_to("playlist_add_items")} == {"PL"}
    assert sleeps == [0.5, 0.5, 0.5]  # API_DELAY after each accepted batch


def test_a_rate_limited_batch_is_retried_whole_after_the_header_delay(sleeps) -> None:
    """429 is the one error worth waiting on. The retry re-sends the SAME 100 uris —
    Spotify rejected the batch outright, so no partial write has to be reasoned about."""
    sp = FakeSpotify(errors={"playlist_add_items": {1: rate_limited(2)}})

    assert sync_playlist.add_tracks_to_playlist(sp, "PL", _many(150)) == 150

    assert [len(batch) for batch in _sent(sp)] == [100, 100, 50]
    assert _sent(sp)[0] == _sent(sp)[1]  # the same batch, not the next one
    assert sleeps == [3, 0.5, 0.5]  # Retry-After 2, plus the one second production adds


def test_a_rate_limit_with_no_retry_after_header_waits_six_seconds(sleeps) -> None:
    """Spotify does not always send the header. The fallback is 5 + 1, not zero — a
    tight retry loop against a rate limiter is how a soft limit becomes a hard one."""
    sp = FakeSpotify(errors={"playlist_add_items": {1: rate_limited()}})

    assert sync_playlist.add_tracks_to_playlist(sp, "PL", ["t1"]) == 1

    assert sleeps == [6, 0.5]


def test_a_permanent_rate_limit_gives_up_after_three_attempts(sleeps) -> None:
    """TODAY'S BEHAVIOUR, pinned not endorsed. The batch is abandoned after MAX_RETRIES
    with no raise: `added` silently undercounts and the caller reports success."""
    sp = FakeSpotify(errors={"playlist_add_items": rate_limited(2)})

    assert sync_playlist.add_tracks_to_playlist(sp, "PL", _many(50)) == 0

    assert len(sp.calls_to("playlist_add_items")) == sync_playlist.MAX_RETRIES == 3
    assert sleeps == [3, 3, 3]  # it waits after the final attempt too, for nothing


def test_a_non_rate_limit_error_drops_that_batch_and_keeps_going(sleeps, capsys) -> None:
    """TODAY'S BEHAVIOUR, pinned not endorsed. 100 of 250 tracks never reach the
    playlist, `added` comes back 150, nothing raises, and the run exits 0. The only
    evidence is a line on stderr in a log nobody reads on a green run."""
    sp = FakeSpotify(errors={"playlist_add_items": {2: spotify_error(500)}})

    assert sync_playlist.add_tracks_to_playlist(sp, "PL", _many(250)) == 150

    assert [len(batch) for batch in _sent(sp)] == [100, 100, 50]  # no retry of batch 2
    assert "Error adding tracks" in capsys.readouterr().err


def test_nothing_to_add_calls_spotify_not_at_all() -> None:
    sp = FakeSpotify()

    assert sync_playlist.add_tracks_to_playlist(sp, "PL", []) == 0

    assert sp.calls == []


# =============================================================================
# The public blurb: update_playlist_description
# =============================================================================


def _description(sp: FakeSpotify) -> Optional[str]:
    calls = sp.calls_to("playlist_change_details")
    return calls[-1].params["description"] if calls else None


def test_the_public_description_is_the_template_filled_in(monkeypatch) -> None:
    """This string is the only thing a stranger opening the playlist reads, so it is
    written out in full here rather than rebuilt from the template — a test that
    reuses DESCRIPTION_TEMPLATE would pass no matter what the template said.

    `datetime` is imported *inside* the function, so it cannot be monkeypatched; the
    expected month is computed the same way instead."""
    db = _FakeDB({"songs": 4586}, {"episodes": 812})
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)
    sp = FakeSpotify()

    sync_playlist.update_playlist_description(sp, "PL", 1)

    assert sp.calls_to("playlist_change_details")[0].params["playlist_id"] == "PL"
    assert _description(sp) == (
        "4,586 songs across 812 SOP episodes. "
        f"Last updated {datetime.now().strftime('%m/%y')}. "
        "Support: buymeacoffee.com/kevinhg. Requests: hi@kevinhg.com."
    )


def test_the_description_names_the_show_it_belongs_to(monkeypatch) -> None:
    """Two playlists share one template, so the acronym is the only thing separating
    them. Getting it from the wrong show would publish TAL's blurb on SOP's playlist."""
    db = _FakeDB({"songs": 837}, {"episodes": 401})
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)
    sp = FakeSpotify()

    sync_playlist.update_playlist_description(sp, "PL", 2)

    assert _description(sp).startswith("837 songs across 401 TAL episodes.")


def test_a_failed_description_update_only_warns(monkeypatch, capsys) -> None:
    """Deliberate: the description is cosmetic and the tracks are already added by the
    time it runs, so a failure here must not fail a sync that worked. Pinned so a
    future refactor doesn't quietly make the cosmetic step fatal."""
    db = _FakeDB({"songs": 1}, {"episodes": 1})
    monkeypatch.setattr(sync_playlist, "get_db_connection", db)
    sp = FakeSpotify(errors={"playlist_change_details": spotify_error(403)})

    sync_playlist.update_playlist_description(sp, "PL", 1)  # must not raise

    assert "Could not update description" in capsys.readouterr().err

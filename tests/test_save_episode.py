"""One-off saved episodes: the Taddy upgrade, the link metadata, and the upsert.

`save_episode.py` turns a Castro clip or an Apple-Notes podcast link into a page in
the Transcripts DB. Its riskiest function is `upsert_oneoff`, which carries a
"never downgrade a full transcript to an excerpt" rule — in only one of its two
write paths. This file pins both paths, including the one where a stub CAN
overwrite a full transcript (today's behaviour, flagged in the PR body; fixing it
is a follow-up, not this PR).

Hermetic: `taddy_query`, `get_episode_transcript`, Firecrawl's `scrape_post`,
`httpx.get` and `subprocess.run` are all faked at the import boundary, and every
DB-touching function takes `conn` as a parameter so a fake connection goes
straight in. No live tokens anywhere.

Written for Phase 5 PR 5 (2026-09-04). Tests only: `pipeline/save_episode.py` is
not modified by this file.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from pipeline import save_episode
from pipeline.save_episode import (
    MIN_FULL_TRANSCRIPT_CHARS,
    SAVED_SLUG,
    TADDY_TITLE_MIN_RATIO,
    page_id_for,
    parse_og,
    scrape_link_meta,
    sync_saved_pages,
    taddy_find_episode,
    taddy_transcript_text,
    try_taddy_full,
    upsert_oneoff,
)
from pipeline.scrapers.blog import import_blog
from pipeline.show_config import get_show


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeCursor:
    """Records (sql, params) and serves a queue of canned fetchone rows."""

    def __init__(self, rows: list | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rows = list(rows or [])

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class _FakeConn:
    def __init__(self, rows: list | None = None) -> None:
        self.cursor_obj = _FakeCursor(rows)
        self.commits = 0

    def cursor(self, **_kwargs) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _episode(uuid: str, name: str, series: str, published: int = 1_756_000_000) -> dict:
    return {"uuid": uuid, "name": name, "datePublished": published,
            "podcastSeries": {"uuid": f"series-{series}", "name": series}}


def _fake_search(episodes: list[dict], recorder: list | None = None):
    def _query(query: str, user_id: str = "", api_key: str = ""):
        if recorder is not None:
            recorder.append((query, user_id, api_key))
        return {"search": {"searchId": "s-1", "podcastEpisodes": episodes}}
    return _query


# ── taddy_find_episode: the title bar and the blend ──────────────────────────
#
# The ratios below are difflib.SequenceMatcher on the lower-cased strings, computed
# against the live implementation on 2026-09-04. They are written into the comments
# so a future reader can see WHY a candidate wins without re-deriving them.
#
#   "election night" vs "election night special" -> 0.7778   (below the 0.80 bar)
#   "election night" vs "election night 2026"    -> 0.8485
#   "election night" vs "election nights"        -> 0.9655
#   "science vs"     vs "science vs"             -> 1.0
#   "science vs"     vs "pivot"                  -> 0.2667

def test_a_title_below_the_bar_is_never_selected_however_good_the_show_match(monkeypatch) -> None:
    """TADDY_TITLE_MIN_RATIO is an absolute gate, not one term in a score. The
    rejected candidate here has the HIGHER blended score (0.778*0.7 + 1.0*0.3 =
    0.844 against 0.849*0.7 + 0.267*0.3 = 0.674) and still loses, because it never
    clears 0.80 on the title. If the gate ever became advisory this test fails."""
    monkeypatch.setattr(save_episode, "taddy_query", _fake_search([
        _episode("below-bar", "Election Night Special", "Science Vs"),
        _episode("above-bar", "Election Night 2026", "Pivot"),
    ]))

    hit = taddy_find_episode("Election Night", "Science Vs", "u", "k")

    assert hit is not None and hit["uuid"] == "above-bar"


def test_the_show_orders_candidates_that_already_cleared_the_title_bar(monkeypatch) -> None:
    """Among candidates past the gate, the 0.7 title / 0.3 show blend decides — so a
    slightly worse title on the RIGHT show beats a better title on the wrong one
    (0.849*0.7 + 1.0*0.3 = 0.894 against 0.966*0.7 + 0.267*0.3 = 0.756)."""
    monkeypatch.setattr(save_episode, "taddy_query", _fake_search([
        _episode("right-show", "Election Night 2026", "Science Vs"),
        _episode("better-title", "Election Nights", "Pivot"),
    ]))

    hit = taddy_find_episode("Election Night", "Science Vs", "u", "k")

    assert hit["uuid"] == "right-show"


def test_an_empty_show_name_makes_the_show_stop_deciding(monkeypatch) -> None:
    """With no show to compare, every candidate gets the same show_ratio (0.5 in the
    code), so the wrong-show candidate stops being penalised and the better title
    wins — the same two candidates as the test above, opposite winner.

    Being precise about what this can and cannot pin: the literal 0.5 is not
    observable through the return value, because a constant applied uniformly to
    every candidate cannot change their order whatever its value. What IS observable,
    and what this pins, is that the show stops discriminating at all when the caller
    has no show name — which is the behaviour that matters for a Castro link whose
    colon split gave us no series.
    """
    monkeypatch.setattr(save_episode, "taddy_query", _fake_search([
        _episode("right-show", "Election Night 2026", "Science Vs"),
        _episode("better-title", "Election Nights", "Pivot"),
    ]))

    hit = taddy_find_episode("Election Night", "", "u", "k")

    assert hit["uuid"] == "better-title"


def test_a_tie_keeps_taddys_own_ranking(monkeypatch) -> None:
    """`score > best_score` (strict) means the first candidate wins a tie — so when
    two results are indistinguishable on title and show, Taddy's own relevance order
    decides, rather than the last one it happened to return."""
    monkeypatch.setattr(save_episode, "taddy_query", _fake_search([
        _episode("first", "Election Night", "Science Vs"),
        _episode("second", "Election Night", "Science Vs"),
    ]))

    assert taddy_find_episode("Election Night", "Science Vs", "u", "k")["uuid"] == "first"


def test_nothing_close_enough_returns_no_hit(monkeypatch) -> None:
    monkeypatch.setattr(save_episode, "taddy_query", _fake_search([
        _episode("a", "Election Night Special", "Science Vs"),
        _episode("b", "The Rise of Agents", "Science Vs"),
    ]))

    assert taddy_find_episode("Election Night", "Science Vs", "u", "k") is None


def test_an_empty_result_set_returns_no_hit(monkeypatch) -> None:
    monkeypatch.setattr(save_episode, "taddy_query", lambda *a, **k: {"search": None})

    assert taddy_find_episode("Election Night", "Science Vs", "u", "k") is None


def test_a_perfect_title_from_the_wrong_show_is_still_selected(monkeypatch) -> None:
    """A documented tradeoff, not a bug to fix here: the show never gates, it only
    orders. Two podcasts with the same episode title ("Election Night", "Season
    Finale") can attach the other show's transcript, invisibly. Pinned so the
    behaviour is a decision rather than a surprise — see the PR body."""
    monkeypatch.setattr(save_episode, "taddy_query",
                        _fake_search([_episode("wrong-show", "Election Night", "Pivot")]))

    hit = taddy_find_episode("Election Night", "Science Vs", "u", "k")

    assert hit["uuid"] == "wrong-show"


def test_the_search_term_is_quote_free_and_capped(monkeypatch) -> None:
    """Two Taddy contracts in one: `searchId` must be in the selection set (Taddy
    400s without it), and a double quote in the title would break out of the
    GraphQL string literal, so quotes become spaces before interpolation."""
    sent: list = []
    monkeypatch.setattr(save_episode, "taddy_query", _fake_search([], sent))

    taddy_find_episode('The "Real" Story ' + "x" * 200, "Science Vs", "u", "k")

    query = sent[0][0]
    assert "searchId" in query
    term = re.search(r'term:"([^"]*)"', query).group(1)
    assert term.startswith("The  Real  Story")
    assert len(term) == 120


# ── taddy_transcript_text: the stub gate ─────────────────────────────────────

def test_a_transcript_at_the_boundary_counts_as_full(monkeypatch) -> None:
    monkeypatch.setattr(save_episode, "get_episode_transcript",
                        lambda uuid, user_id="", api_key="": [{"text": "a" * MIN_FULL_TRANSCRIPT_CHARS}])

    text = taddy_transcript_text("uuid", "u", "k")

    assert text is not None and len(text) == MIN_FULL_TRANSCRIPT_CHARS == 1000


def test_one_character_short_of_the_boundary_is_a_stub(monkeypatch) -> None:
    """Below the bar a "transcript" is a fragment — Taddy sometimes returns a teaser.
    Returning None here is what keeps the page honestly labelled as an excerpt."""
    monkeypatch.setattr(save_episode, "get_episode_transcript",
                        lambda uuid, user_id="", api_key="": [{"text": "a" * 999}])

    assert taddy_transcript_text("uuid", "u", "k") is None


def test_paragraphs_join_with_a_blank_line(monkeypatch) -> None:
    monkeypatch.setattr(save_episode, "get_episode_transcript",
                        lambda uuid, user_id="", api_key="": [{"text": "a" * 500}, {"text": "b" * 498}])

    text = taddy_transcript_text("uuid", "u", "k")

    # 500 + 2 (the blank line) + 498 = exactly the boundary
    assert text == "a" * 500 + "\n\n" + "b" * 498
    assert len(text) == MIN_FULL_TRANSCRIPT_CHARS


def test_paragraphs_without_text_do_not_crash(monkeypatch) -> None:
    monkeypatch.setattr(save_episode, "get_episode_transcript",
                        lambda uuid, user_id="", api_key="": [{}, {"text": "  short  "}])

    assert taddy_transcript_text("uuid", "u", "k") is None


# ── try_taddy_full: a Taddy hiccup must never kill the item ──────────────────

def test_a_raising_lookup_degrades_to_no_hit_and_no_transcript(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("taddy 429")

    monkeypatch.setattr(save_episode, "taddy_find_episode", boom)

    assert try_taddy_full("Election Night", "Science Vs", "u", "k") == (None, None)


def test_a_raising_transcript_fetch_also_degrades(monkeypatch) -> None:
    """The try covers BOTH calls: a hit whose transcript fetch fails yields (None,
    None), so the caller falls back to the clip text or the show notes rather than
    saving a page with a title and no body."""
    def boom(*_a, **_k):
        raise RuntimeError("taddy timeout")

    monkeypatch.setattr(save_episode, "taddy_find_episode", lambda *a, **k: {"uuid": "u1"})
    monkeypatch.setattr(save_episode, "taddy_transcript_text", boom)

    assert try_taddy_full("Election Night", "Science Vs", "u", "k") == (None, None)


def test_a_hit_without_a_full_transcript_keeps_the_hit(monkeypatch) -> None:
    """The hit still carries the publish date and the real series name, so it is
    worth returning even when the transcript is a stub."""
    hit = _episode("u1", "Election Night", "Science Vs")
    monkeypatch.setattr(save_episode, "taddy_find_episode", lambda *a, **k: hit)
    monkeypatch.setattr(save_episode, "taddy_transcript_text", lambda *a, **k: None)

    assert try_taddy_full("Election Night", "Science Vs", "u", "k") == (hit, None)


def test_no_hit_never_asks_for_a_transcript(monkeypatch) -> None:
    """No hit means no uuid, so the transcript call must be short-circuited rather
    than attempted and rescued.

    Getting this test to bite took two passes, both worth recording.

    A canary that RAISES is invisible here: try_taddy_full wraps both calls in one
    `except Exception` and returns exactly (None, None) — the value this test
    asserts — so deleting the `if hit else None` at save_episode.py:102 left all 39
    tests green.

    A canary that RECORDS is not enough either, and that is the subtle half:
    without the short-circuit, `hit["uuid"]` raises TypeError while evaluating the
    ARGUMENT, before taddy_transcript_text is ever called. The recorder stays empty
    and the test still passes.

    So the assertion that actually distinguishes the two worlds is that nothing was
    RESCUED: on the no-hit path the except branch must never run. Both checks are
    kept — the recorder says "not called", the warning says "not swallowed"."""
    calls: list = []
    warnings: list = []
    monkeypatch.setattr(save_episode, "taddy_find_episode", lambda *a, **k: None)
    monkeypatch.setattr(save_episode, "taddy_transcript_text",
                        lambda *a, **k: calls.append(a) or "a transcript we never asked for")
    monkeypatch.setattr(save_episode.log, "warning", lambda *a, **k: warnings.append(a))

    assert try_taddy_full("Election Night", "Science Vs", "u", "k") == (None, None)
    assert calls == []
    assert warnings == []  # (None, None) by short-circuit, not by rescue


# ── parse_og ─────────────────────────────────────────────────────────────────

def test_open_graph_tags_are_read_in_both_attribute_orderings() -> None:
    first = '<meta property="og:title" content="Are Ghosts Real?">'
    second = '<meta content="Are Ghosts Real?" property="og:title">'

    assert parse_og(first, "title") == "Are Ghosts Real?"
    assert parse_og(second, "title") == "Are Ghosts Real?"


def test_a_missing_tag_is_an_empty_string_not_none() -> None:
    assert parse_og("<html><head></head></html>", "title") == ""
    assert parse_og('<meta property="og:title" content="x">', "description") == ""


def test_surrounding_whitespace_is_stripped() -> None:
    assert parse_og('<meta property="og:description" content="  spaced out  ">',
                    "description") == "spaced out"


# ── scrape_link_meta: castro.fm titles, and the Firecrawl fallback ───────────

def test_a_castro_title_loses_its_duration_and_offers_both_colon_splits(monkeypatch) -> None:
    """Castro's og:title is "{series}: {episode} (1h51m)" — but either half can
    itself contain a colon, so a single split point is ambiguous. The function
    returns both candidates and the caller tries Taddy with each."""
    page = ('<meta property="og:title" content="Pivot: Tech: The Week in Review (1h51m)">'
            '<meta property="og:description" content="Kara &amp; Scott">')
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(save_episode.httpx, "get", lambda *a, **k: _FakeResponse(page))

    meta = scrape_link_meta("https://castro.fm/episode/abc123")

    assert meta["show"] == "Pivot"
    assert meta["title"] == "Tech: The Week in Review"
    assert meta["alt"] == {"show": "Pivot: Tech", "title": "The Week in Review"}
    assert meta["notes"] == "Kara & Scott"  # entities unescaped


def test_a_short_duration_suffix_is_stripped_too(monkeypatch) -> None:
    page = '<meta property="og:title" content="Science Vs: Are Ghosts Real? (51m)">'
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(save_episode.httpx, "get", lambda *a, **k: _FakeResponse(page))

    meta = scrape_link_meta("https://castro.fm/episode/abc123")

    assert (meta["show"], meta["title"]) == ("Science Vs", "Are Ghosts Real?")


def test_a_non_castro_page_reports_no_show_and_falls_back_to_the_title_tag(monkeypatch) -> None:
    page = "<html><head><title>Ep 42: The Interview</title></head></html>"
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(save_episode.httpx, "get", lambda *a, **k: _FakeResponse(page))

    meta = scrape_link_meta("https://example.com/podcast/42")

    assert meta == {"title": "Ep 42: The Interview", "show": "", "notes": ""}


def test_firecrawl_is_preferred_when_a_key_is_present(monkeypatch) -> None:
    """castro.fm TLS-resets repeated raw hits, so the proxy goes first; when it
    answers, no raw request is made at all.

    Recording rather than raising, for the reason spelled out in
    test_no_hit_never_asks_for_a_transcript: this module rescues broadly, and a
    canary that raises can be swallowed into a passing assertion."""
    raw: list = []
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(import_blog, "scrape_post", lambda url, key: {
        "metadata": {"ogTitle": "Science Vs: Are Ghosts Real? (51m)", "ogDescription": "spooky"}})
    monkeypatch.setattr(save_episode.httpx, "get",
                        lambda *a, **k: raw.append(a) or _FakeResponse(
                            '<meta property="og:title" content="A raw fetch we never asked for">'))

    meta = scrape_link_meta("https://castro.fm/episode/abc123")

    assert (meta["show"], meta["title"], meta["notes"]) == ("Science Vs", "Are Ghosts Real?", "spooky")
    assert raw == []


def test_a_firecrawl_failure_falls_back_to_the_raw_fetch(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("firecrawl 502")

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(import_blog, "scrape_post", boom)
    monkeypatch.setattr(save_episode.httpx, "get", lambda *a, **k: _FakeResponse(
        '<meta property="og:title" content="Are Ghosts Real?">'))

    assert scrape_link_meta("https://example.com/ep/1")["title"] == "Are Ghosts Real?"


# ── upsert_oneoff: dedupe by title, and the never-downgrade rule ─────────────

def _upsert(conn, *, title="Are Ghosts Real?", source_name="Science Vs",
            url_key="castro://clip/9", text="body", source_type="castro_clip"):
    return upsert_oneoff(conn, title, source_name, url_key, date(2026, 9, 1), text, source_type)


def test_the_title_lookup_is_case_insensitive_and_scoped_to_the_saved_show() -> None:
    """Dedupe is by title within the catch-all show: the same episode arrives twice
    (a Castro clip AND an Apple-Notes link) with different url keys, and title
    identity is what makes it one page."""
    conn = _FakeConn(rows=[None, {"id": 5001}])

    _upsert(conn)

    sql, params = conn.cursor_obj.calls[0]
    assert "lower(ep.title) = lower(%s)" in sql
    assert params == (get_show(SAVED_SLUG).show_id, "Are Ghosts Real?")


def test_a_new_title_inserts_the_episode_and_its_transcript() -> None:
    conn = _FakeConn(rows=[None, {"id": 5001}])

    episode_id, created, upgraded = _upsert(conn, text="clip text")

    assert (episode_id, created, upgraded) == (5001, True, False)
    episodes_sql, episodes_params = conn.cursor_obj.calls[1]
    assert "INSERT INTO episodes" in episodes_sql
    assert episodes_params[1:] == ("Are Ghosts Real?", "castro://clip/9", date(2026, 9, 1),
                                   '{"provider": "oneoff_episode", "source_name": "Science Vs"}')
    transcripts_sql, transcripts_params = conn.cursor_obj.calls[2]
    assert "INSERT INTO episode_transcripts" in transcripts_sql
    assert transcripts_params == (5001, "castro_clip", "castro://clip/9", "clip text")
    assert conn.commits == 1


def test_an_existing_page_is_not_rewritten_by_a_lesser_source() -> None:
    """Only ONE statement runs: the lookup. A second arrival of the same episode
    must not restate the body it already has."""
    conn = _FakeConn(rows=[{"id": 4242, "source_type": "show_notes"}])

    assert _upsert(conn, source_type="show_notes") == (4242, False, False)
    assert len(conn.cursor_obj.calls) == 1
    assert conn.commits == 1


def test_an_excerpt_page_is_upgraded_to_a_full_transcript() -> None:
    conn = _FakeConn(rows=[{"id": 4242, "source_type": "show_notes"}])

    episode_id, created, upgraded = _upsert(conn, text="F" * 2000, source_type="taddy_transcript")

    assert (episode_id, created, upgraded) == (4242, False, True)
    sql, params = conn.cursor_obj.calls[1]
    assert sql.startswith("UPDATE episode_transcripts")
    assert params == ("F" * 2000, "taddy_transcript", 4242)
    # The Notion page pointers are cleared so the upgraded text actually re-syncs;
    # leaving them set would keep the old excerpt visible in Notion forever.
    assert "notion_transcript_page_id=NULL" in sql
    assert "notion_transcript_synced_at=NULL" in sql


def test_a_clip_never_downgrades_an_existing_full_transcript() -> None:
    """The guard that gives this file its name: a Castro clip arriving after a Taddy
    transcript leaves the full text alone."""
    conn = _FakeConn(rows=[{"id": 4242, "source_type": "taddy_transcript"}])

    assert _upsert(conn, text="30 seconds of clip", source_type="castro_clip") == (4242, False, False)
    assert len(conn.cursor_obj.calls) == 1


def test_show_notes_never_downgrade_an_existing_full_transcript() -> None:
    conn = _FakeConn(rows=[{"id": 4242, "source_type": "taddy_transcript"}])

    assert _upsert(conn, text="a blurb", source_type="show_notes") == (4242, False, False)
    assert len(conn.cursor_obj.calls) == 1


def test_a_second_taddy_transcript_does_not_rewrite_the_first() -> None:
    conn = _FakeConn(rows=[{"id": 4242, "source_type": "taddy_transcript"}])

    assert _upsert(conn, text="F" * 2000, source_type="taddy_transcript") == (4242, False, False)
    assert len(conn.cursor_obj.calls) == 1


def test_a_stub_can_overwrite_a_full_transcript_on_the_url_path() -> None:
    """TODAY'S BEHAVIOUR, pinned so the fix is a deliberate change rather than a
    surprise — see the PR body.

    upsert_oneoff has two write paths. The title-match branch above checks the
    existing source_type before it writes. This one is taken when the title lookup
    MISSES but `episodes.url` already exists — a re-run where the title drifted
    slightly (Taddy renames, a "Part 1" suffix) and Taddy failed this time. The
    episodes row is safe: its ON CONFLICT sets only `title`. The transcript row is
    not: `ON CONFLICT (episode_id) DO UPDATE SET transcript_text = EXCLUDED.
    transcript_text, source_type = EXCLUDED.source_type` overwrites whatever was
    there with no test on the existing source_type, so a show-notes stub replaces a
    full Taddy transcript with no error and no distinguishing log line.

    Extending the never-downgrade guard to this statement is the follow-up.
    """
    conn = _FakeConn(rows=[None, {"id": 4242}])

    _upsert(conn, title="Are Ghosts Real? (Part 1)", text="a blurb", source_type="show_notes")

    episodes_sql, _ = conn.cursor_obj.calls[1]
    transcripts_sql, transcripts_params = conn.cursor_obj.calls[2]

    # The episodes upsert is NOT the hole: its SET list is the title and nothing else
    # (the reader note put the risk here; the synthesis moved it to the next statement).
    set_clause = episodes_sql.split("DO UPDATE SET", 1)[1].split("RETURNING", 1)[0]
    assert set_clause.split() == ["title", "=", "EXCLUDED.title"]

    # The transcripts upsert IS: an unconditional overwrite of text AND provenance.
    assert "ON CONFLICT (episode_id) DO UPDATE" in transcripts_sql
    assert "transcript_text = EXCLUDED.transcript_text" in transcripts_sql
    assert "source_type = EXCLUDED.source_type" in transcripts_sql
    assert "WHERE" not in transcripts_sql  # no guard on the existing source_type
    assert transcripts_params[1:] == ("show_notes", "castro://clip/9", "a blurb")


# ── the page pointer and the Notion pass ─────────────────────────────────────

def test_the_notion_page_id_comes_back_when_the_sync_has_run() -> None:
    conn = _FakeConn(rows=[{"notion_transcript_page_id": "page-abc"}])

    assert page_id_for(conn, 4242) == "page-abc"
    sql, params = conn.cursor_obj.calls[0]
    assert "FROM episode_transcripts WHERE episode_id=%s" in sql
    assert params == (4242,)


def test_no_row_means_no_page_id() -> None:
    """The caller turns this into "page not created by sync" and fails that one
    highlight rather than writing a callout into the void."""
    assert page_id_for(_FakeConn(rows=[]), 4242) is None


def test_the_saved_pages_sync_targets_the_transcripts_db_for_saved_episodes(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(save_episode.subprocess, "run",
                        lambda cmd, **kw: calls.append((cmd, kw)))

    sync_saved_pages()

    cmd, kwargs = calls[0]
    assert Path(cmd[1]).name == "sync_transcripts_notion.py"
    assert cmd[2:] == ["--target", "transcripts", "--shows", SAVED_SLUG]
    # check=True: a failed sync must raise, because the highlights that follow it
    # need the page it was supposed to create.
    assert kwargs["check"] is True


def test_the_taddy_title_bar_is_where_the_module_says_it_is() -> None:
    # Constants the tests above reason about, asserted once so a change to either
    # shows up here rather than as an unexplained failure three tests away.
    assert TADDY_TITLE_MIN_RATIO == 0.80
    assert MIN_FULL_TRANSCRIPT_CHARS == 1000


def test_saved_episodes_stays_the_catch_all_slug() -> None:
    cfg = get_show(SAVED_SLUG)

    assert SAVED_SLUG == "saved-episodes"
    # No extraction and no entity DB on purpose: these span culture/politics shows
    # whose tech mentions would pollute the shared Tech DB.
    assert cfg.extraction_type is None
    assert cfg.notion_database_id is None


@pytest.mark.parametrize("source_type", ["castro_clip", "show_notes"])
def test_every_excerpt_source_is_upgradable_to_taddy(source_type: str) -> None:
    """Both honest-excerpt labels must be upgradable — a rename on either side of
    this comparison would silently strand those pages as excerpts forever."""
    conn = _FakeConn(rows=[{"id": 4242, "source_type": source_type}])

    _, _, upgraded = _upsert(conn, text="F" * 2000, source_type="taddy_transcript")

    assert upgraded is True

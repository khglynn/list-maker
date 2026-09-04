"""TAL's song scrape: what gets queued, and which page it reads.

These pin a live incident. Between 2026-01 and 2026-09 not one TAL episode gained a
song row, while the Monday cron reported success every week. Two independent faults,
one test class each below:

  1. THE QUEUE. `get_unscraped_episodes` selected `scraped_at IS NULL`. Nothing under
     scrapers/tal/ writes `scraped_at` — the Taddy importer does, on the very INSERT
     that creates the row (import_transcripts.py:397) and on its title+date dedup UPDATE
     (:364). Once TAL discovery started running that importer (2026-08-02) the queue was
     empty by construction: 0 rows matched on 2026-09-04.
  2. THE URL. Even a queued row would have been handed to Firecrawl as
     `episodes.url`, which for a Taddy-discovered episode is
     https://api.taddy.org/podcast-episode/<uuid> — an identity key with no song
     credits on it, so the parse found nothing and the run still exited 0.

`test_taddy_discovered_episode_is_queued_for_the_website_scrape` is the regression pin
for (1) and fails on the pre-fix code. `test_scrape_never_sends_firecrawl_at_a_taddy_url`
is the pin for (2).

Hermetic: no network, no database. The DB is a fake cursor in the house shape
(tests/test_load_entity_batch.py), returning dict-like rows because this path uses
common.get_db_connection's default RealDictCursor.
"""

from __future__ import annotations

import asyncio
from datetime import date

from pipeline.scrapers.tal import fetch
from pipeline.scrapers.gabfest.import_gabfest import parse_feed
from pipeline.show_config import (
    TADDY_EPISODE_URL_PREFIX,
    is_tal_episode_page_url,
    tal_episode_page_url,
)

# ---------------------------------------------------------------- fakes

class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, list]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params=None) -> None:
        self.calls.append((sql, list(params or [])))

    def fetchall(self) -> list[dict]:
        return self.rows


class _FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self._cursor = _FakeCursor(rows)
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _queue(monkeypatch, rows: list[dict]) -> _FakeConn:
    conn = _FakeConn(rows)
    monkeypatch.setattr(fetch, "get_db_connection", lambda: conn)
    return conn


def _taddy_row(**over) -> dict:
    """A row exactly as TAL discovery leaves it: Taddy identity url, scraped_at stamped."""
    row = {
        "id": 8843,
        "url": f"{TADDY_EPISODE_URL_PREFIX}e457f2c3-000d-47ac-a000-000000000000",
        "title": "896: I Know What You Need",
        "publish_date": date(2026, 8, 31),
    }
    row.update(over)
    return row


# ------------------------------------------------- 1. what gets queued

def test_taddy_discovered_episode_is_queued_for_the_website_scrape(monkeypatch) -> None:
    """THE REGRESSION PIN. Fails on the pre-fix code, which asked for scraped_at IS NULL.

    A Taddy-discovered row has scraped_at set the moment it exists, so the old predicate
    excluded every TAL episode published after 2026-08-02 — and, through the importer's
    dedup UPDATE, retroactively excluded older website-url rows it had never read.
    """
    conn = _queue(monkeypatch, [_taddy_row()])

    queued = fetch.get_episodes_missing_songs()

    assert [row["id"] for row in queued] == [8843]
    sql, params = conn.cursor().calls[0]
    assert "scraped_at" not in sql, "scraped_at belongs to the Taddy importer, not to us"
    assert "NOT EXISTS" in sql and "FROM songs" in sql
    assert params[:2] == [fetch.TAL_SHOW_ID, fetch.DEFAULT_SONG_SCRAPE_FLOOR]


def test_archive_episode_without_songs_is_not_queued(monkeypatch) -> None:
    """The 189-row guard: most pre-2026 TAL episodes have no music credits at all, and
    'has no songs' would re-read every one of them on every run without a date floor."""
    _queue(monkeypatch, [])

    fetch.get_episodes_missing_songs()

    # The floor is in the SQL, not applied in Python after the fact — the DB must never
    # hand back 200 archive rows for us to filter.
    sql, params = fetch.get_db_connection().cursor().calls[0]
    assert "publish_date >= %s" in sql
    assert fetch.DEFAULT_SONG_SCRAPE_FLOOR in params
    assert fetch.DEFAULT_SONG_SCRAPE_FLOOR == date(2026, 1, 1)


def test_date_floor_is_a_parameter_so_a_backfill_is_deliberate(monkeypatch) -> None:
    conn = _queue(monkeypatch, [])

    fetch.get_episodes_missing_songs(published_since=date(2011, 1, 1))

    _, params = conn.cursor().calls[0]
    assert params[1] == date(2011, 1, 1)


def test_episode_with_songs_is_not_requeued(monkeypatch) -> None:
    """Rows leave the queue by acquiring songs — that is what makes a failed fetch
    self-healing (retried next run) instead of marked-done by a side effect."""
    conn = _queue(monkeypatch, [])

    assert fetch.get_episodes_missing_songs() == []
    sql, _ = conn.cursor().calls[0]
    assert "NOT EXISTS (SELECT 1 FROM songs s WHERE s.episode_id = e.id)" in " ".join(sql.split())


def test_limit_is_bound_not_interpolated(monkeypatch) -> None:
    conn = _queue(monkeypatch, [])

    fetch.get_episodes_missing_songs(limit=5)

    sql, params = conn.cursor().calls[0]
    assert "LIMIT %s" in sql and params[-1] == 5


def test_the_queue_closes_its_connection(monkeypatch) -> None:
    conn = _queue(monkeypatch, [_taddy_row()])

    fetch.get_episodes_missing_songs()

    assert conn.closed


def test_local_json_cache_is_not_the_queue(monkeypatch, tmp_path) -> None:
    """The cache is git-ignored and empty on a CI runner, so it cannot be the record of
    what has been read — and when it is NOT empty, a JSON left by a bad fetch used to
    exclude that episode from every later run on that machine, permanently."""
    (tmp_path / "8843.json").write_text("{}")
    monkeypatch.setattr(fetch, "OUTPUT_DIR", tmp_path)
    _queue(monkeypatch, [_taddy_row()])
    monkeypatch.setattr(fetch, "fetch_feed_page_links", lambda *a, **k: {})

    resolved, unresolved = fetch.plan_fetch()

    assert fetch.get_already_fetched() == {8843}, "still reported"
    assert [row["id"] for row in resolved] == [8843], "but never subtracted"
    assert unresolved == []


# ------------------------------------------------- 2. which page it reads

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>This American Life</title>
  <item>
    <title>896: I Know What You Need</title>
    <link>https://www.thisamericanlife.org/896/i-know-what-you-need</link>
    <pubDate>Sun, 30 Aug 2026 20:00:00 -0400</pubDate>
  </item>
  <item>
    <title>889: There\xe2\x80\x99s Something About Hail Mary</title>
    <link>https://www.thisamericanlife.org/889/theres-something-about-hail-mary</link>
    <pubDate>Sun, 21 Jun 2026 20:00:00 -0400</pubDate>
  </item>
  <item>
    <title>Ira (Reluctantly) Gives a Graduation Speech</title>
    <link>https://www.thisamericanlife.org/lifepartners</link>
    <pubDate>Fri, 01 May 2026 00:00:00 -0400</pubDate>
  </item>
  <item>
    <title>A Big Announcement</title>
    <link>https://www.thisamericanlife.org</link>
    <pubDate>Wed, 16 Oct 2024 00:00:00 -0400</pubDate>
  </item>
</channel></rss>"""


def test_page_url_comes_from_the_feed_link_when_the_title_matches() -> None:
    links = fetch.page_links_from_feed_items(parse_feed(FEED))
    row = _taddy_row()

    assert fetch.resolve_page_url(row, links) == (
        "https://www.thisamericanlife.org/896/i-know-what-you-need"
    )


def test_page_url_from_the_feed_survives_a_curly_apostrophe() -> None:
    """The DB and the feed do not agree on quote characters episode to episode, and a
    title-keyed lookup that cares would silently fall through to the slug."""
    links = fetch.page_links_from_feed_items(parse_feed(FEED))
    row = _taddy_row(id=7420, title="889: There's Something About Hail Mary")

    assert fetch.resolve_page_url(row, links) == (
        "https://www.thisamericanlife.org/889/theres-something-about-hail-mary"
    )


def test_page_url_from_the_feed_reaches_an_episode_no_slug_could_guess() -> None:
    """Live row 7422: no episode number, and its real page is /lifepartners. Only the
    feed knows — which is why the feed is tried before the derived slug."""
    links = fetch.page_links_from_feed_items(parse_feed(FEED))
    row = _taddy_row(id=7422, title="Ira (Reluctantly) Gives a Graduation Speech")

    assert tal_episode_page_url(row["title"]) is None
    assert fetch.resolve_page_url(row, links) == "https://www.thisamericanlife.org/lifepartners"


def test_the_feed_map_ignores_items_that_link_to_the_site_root() -> None:
    """TAL's bonus items link to the bare homepage. Fetching that returns a 200 with no
    song credits — a wasted call that looks like a successful read."""
    links = fetch.page_links_from_feed_items(parse_feed(FEED))

    assert "a big announcement" not in links
    assert len(links) == 3


def test_page_url_falls_back_to_the_slug_when_the_feed_has_rolled_over() -> None:
    """The feed is a rolling 15-item window (counted 2026-09-04), so anything older than
    roughly four months is only reachable by deriving the slug."""
    assert tal_episode_page_url("896: I Know What You Need") == (
        "https://www.thisamericanlife.org/896/i-know-what-you-need"
    )
    assert tal_episode_page_url("895: Label Maker!") == (
        "https://www.thisamericanlife.org/895/label-maker"
    )
    assert tal_episode_page_url("887: Two Is One, One Is None!") == (
        "https://www.thisamericanlife.org/887/two-is-one-one-is-none"
    )
    # Apostrophes vanish rather than becoming separators — TAL writes it this way.
    assert tal_episode_page_url("894: I Couldn’t Help but Notice") == (
        "https://www.thisamericanlife.org/894/i-couldnt-help-but-notice"
    )
    assert tal_episode_page_url("880: What Is Your Emergency?") == (
        "https://www.thisamericanlife.org/880/what-is-your-emergency"
    )


def test_page_url_is_none_for_an_untitled_or_unnumbered_episode(monkeypatch) -> None:
    """There is one such live row (7422). It must be REPORTED, not quietly dropped and
    not fetched from a guessed url — 'nothing to do' and 'couldn't check' are different
    outcomes and this pipeline has already paid once for conflating them."""
    assert tal_episode_page_url(None) is None
    assert tal_episode_page_url("") is None
    assert tal_episode_page_url("An Update from Ira") is None
    assert tal_episode_page_url("206:   ") is None

    row = _taddy_row(id=7422, title="An Update from Ira", url=None)
    _queue(monkeypatch, [row])
    monkeypatch.setattr(fetch, "fetch_feed_page_links", lambda *a, **k: {})

    resolved, unresolved = fetch.plan_fetch()

    assert resolved == []
    assert [r["id"] for r in unresolved] == [7422]


def test_a_row_that_already_has_a_real_page_url_keeps_it() -> None:
    """Website-discovered rows carry the true page, including the unnumbered ones the
    slug could never reach: /885/bless-this-mess is a 404, /bless-this-mess is the page
    (verified live 2026-09-04). The row's own url outranks anything derived."""
    row = {
        "id": 3025,
        "url": "https://www.thisamericanlife.org/bless-this-mess",
        "title": "885: Bless This Mess",
        "publish_date": date(2026, 4, 12),
    }

    assert fetch.resolve_page_url(row, {}) == "https://www.thisamericanlife.org/bless-this-mess"


def test_scrape_never_sends_firecrawl_at_a_taddy_url(monkeypatch) -> None:
    """The second half of the bug, pinned at the boundary that spends money: the url
    handed to the fetcher. A Taddy identity url has no '## Song:' section on it, so
    fetching one is a paid request that can only ever parse to zero songs."""
    row = _taddy_row()
    _queue(monkeypatch, [row])
    monkeypatch.setattr(fetch, "fetch_feed_page_links", lambda *a, **k: {})
    resolved, unresolved = fetch.plan_fetch()

    assert unresolved == []
    assert not resolved[0]["page_url"].startswith(TADDY_EPISODE_URL_PREFIX)
    assert resolved[0]["page_url"] == "https://www.thisamericanlife.org/896/i-know-what-you-need"
    assert resolved[0]["url"] == row["url"], "episodes.url is the identity — left untouched"

    # And the url that actually reaches the HTTP call is the page url, not the row's.
    asked: list[str] = []

    async def _fake_fetch(client, episode_id, url, semaphore):
        asked.append(url)
        return {"db_id": episode_id, "url": url, "success": True, "markdown": "", "metadata": {}}

    monkeypatch.setattr(fetch, "fetch_episode", _fake_fetch)
    monkeypatch.setattr(fetch, "save_result", lambda result: None)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    asyncio.run(fetch.main(episodes=resolved))

    assert asked == ["https://www.thisamericanlife.org/896/i-know-what-you-need"]


def test_a_taddy_url_is_never_a_page_url() -> None:
    """The invariant on its own, so a future change to resolve_page_url's source order
    cannot reintroduce the bug through a different door."""
    assert not is_tal_episode_page_url(f"{TADDY_EPISODE_URL_PREFIX}abc-123")
    assert not is_tal_episode_page_url("https://www.thisamericanlife.org")
    assert not is_tal_episode_page_url("https://www.thisamericanlife.org/")
    assert not is_tal_episode_page_url(None)
    assert not is_tal_episode_page_url("")
    assert is_tal_episode_page_url("https://www.thisamericanlife.org/886/blackout")
    assert is_tal_episode_page_url("https://thisamericanlife.org/blackjack")


def test_a_feed_outage_degrades_to_slugs_instead_of_failing_the_run(monkeypatch) -> None:
    """A dead feed must not take out the Monday music run — the derived slug resolved
    22 of the 24 live backlog rows on 2026-09-04, so degrading still does real work."""
    import requests

    def _boom(*args, **kwargs):
        raise requests.RequestException("feed down")

    monkeypatch.setattr(requests, "get", _boom)

    assert fetch.fetch_feed_page_links("https://example.invalid/rss.xml") == {}

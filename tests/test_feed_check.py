"""feed_check: the second source every "are we caught up?" verdict leans on.

These pin the module's stated contract: a non-empty list newest-first (all <= today)
when it got a trustworthy answer, None otherwise. Since the move off Taddy (2026-12)
every show's second source is its own RSS feed, read through pipeline/feeds.py, the
module the importers write from; the drift guard below keeps the identity the check
rebuilds equal to the one the importer writes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from pipeline import feed_check


def _rss_cfg(**over):
    base = dict(feed_url="https://example.org/feed.xml", feed_title_prefix=None, episode_identity="rss_guid", importer="rss")
    base.update(over)
    return SimpleNamespace(**base)


def test_feed_recent_dates_drops_future_dated_entries_and_sorts_newest_first(monkeypatch) -> None:
    today = datetime.now(timezone.utc).date()
    tomorrow, yesterday = today + timedelta(days=1), today - timedelta(days=1)
    monkeypatch.setattr(feed_check, "rss_recent_dates", lambda url, title_prefix="", limit=15: [yesterday, tomorrow, today])
    assert feed_check.feed_recent_dates(_rss_cfg()) == [today, yesterday]


def test_feed_recent_dates_is_none_when_nothing_trustworthy_remains(monkeypatch) -> None:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    monkeypatch.setattr(feed_check, "rss_recent_dates", lambda url, title_prefix="", limit=15: [tomorrow])
    assert feed_check.feed_recent_dates(_rss_cfg()) is None
    monkeypatch.setattr(feed_check, "rss_recent_dates", lambda url, title_prefix="", limit=15: None)
    assert feed_check.feed_recent_dates(_rss_cfg()) is None


def test_feed_recent_dates_has_no_source_for_a_curated_show() -> None:
    # No feed: nothing to ask. None, never a fake "caught up".
    assert feed_check.feed_recent_dates(SimpleNamespace(feed_url=None)) is None


# ---- identity readers: WHICH episodes the feed has, not just which dates ----


class _Resp:
    def __init__(self, payload=None, content=b"") -> None:
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_the_identity_the_check_rebuilds_is_the_one_the_importer_writes() -> None:
    """THE drift guard. If the feed check and the importer ever built an episode's
    identity differently, every show would report every episode missing."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline" / "scrapers" / "rss"))
    import feeds
    import import_feed
    from tests.test_import_gabfest import SAMPLE_FEED

    items = feeds.parse_feed(SAMPLE_FEED).items
    planned = import_feed.plan_import(items, import_feed.HeldIndex([]), now=datetime(2026, 6, 10, tzinfo=timezone.utc),
                                      wants_transcript=False, backfill=True)
    written = {a.item.title: a.identity for a in planned}
    rebuilt = {it.title: feeds.episode_identity(it) for it in items}
    assert written == rebuilt


def test_trailers_are_not_expected_in_the_feed_check(monkeypatch) -> None:
    feed = b"""<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>S</title>
      <item><title>Ep</title><guid>g-1</guid><pubDate>Tue, 03 Nov 2026 08:00:00 +0000</pubDate></item>
      <item><title>Welcome</title><guid>g-0</guid><itunes:episodeType>trailer</itunes:episodeType>
        <pubDate>Wed, 16 Sep 2026 08:00:00 +0000</pubDate></item></channel></rss>"""
    monkeypatch.setattr(feed_check.requests, "get", lambda *a, **k: _Resp(content=feed))
    assert [ep.identity for ep in feed_check.rss_recent_episodes("https://example.org/feed.xml")] == ["g-1"]


def test_rss_recent_episodes_uses_the_gabfest_importer_identity(monkeypatch) -> None:
    """Structural equality against import_gabfest.episode_url on a shared fixture — the
    function is reused rather than re-implemented, so the two cannot drift."""
    from pipeline.scrapers.gabfest.import_gabfest import episode_url, parse_feed
    from tests.test_import_gabfest import SAMPLE_FEED

    monkeypatch.setattr(feed_check.requests, "get", lambda *a, **k: _Resp(content=SAMPLE_FEED))

    episodes = feed_check.rss_recent_episodes("https://feeds.megaphone.fm/x", "Culture Gabfest")

    expected = [
        episode_url(it)
        for it in parse_feed(SAMPLE_FEED)
        if it["title"].startswith("Culture Gabfest")
    ]
    assert [ep.identity for ep in episodes] == expected  # guid, newest first
    assert [ep.publish_date for ep in episodes] == [date(2026, 6, 3), date(2026, 5, 27)]
    # The ICYMI item is a different Slate show in the same feed — filtered out.
    assert all("ICYMI" not in ep.title for ep in episodes)


def test_rss_recent_episodes_is_none_on_an_unreachable_feed(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(feed_check.requests, "get", boom)
    assert feed_check.rss_recent_episodes("https://feeds.megaphone.fm/x") is None


def test_feed_recent_episodes_drops_future_dated_entries_and_sorts_newest_first(monkeypatch) -> None:
    today = datetime.now(timezone.utc).date()
    tomorrow, yesterday = today + timedelta(days=1), today - timedelta(days=1)
    monkeypatch.setattr(
        feed_check,
        "rss_recent_episodes",
        lambda url, title_prefix=None, limit=15, synthetic_prefix="rss": [
            feed_check.FeedEpisode("a", yesterday, "A"),
            feed_check.FeedEpisode("b", tomorrow, "Pre-release"),
            feed_check.FeedEpisode("c", today, "C"),
        ],
    )
    cfg = _rss_cfg()

    assert [ep.identity for ep in feed_check.feed_recent_episodes(cfg)] == ["c", "a"]


def test_feed_recent_episodes_has_no_identity_source_for_a_date_compared_show() -> None:
    """SOP has a feed — its second source — but its rows are written by its own
    scraper, so there is no id to compare. The branch is on episode_identity for
    exactly this reason; getting it wrong reported 13 of SOP's 15 feed episodes missing
    (measured against live Neon, 2026-09-03)."""
    sop_shaped = SimpleNamespace(episode_identity=None, feed_url="https://feeds.megaphone.fm/switchedonpop")
    assert feed_check.feed_recent_episodes(sop_shaped) is None


def test_feed_recent_episodes_is_none_when_the_source_could_not_be_verified(monkeypatch) -> None:
    cfg = _rss_cfg()
    monkeypatch.setattr(feed_check, "rss_recent_episodes", lambda *a, **k: None)
    assert feed_check.feed_recent_episodes(cfg) is None
    # Empty after dropping future dates is also unverified, never a green "caught up".
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    monkeypatch.setattr(
        feed_check,
        "rss_recent_episodes",
        lambda *a, **k: [feed_check.FeedEpisode("a", tomorrow, "A")],
    )
    assert feed_check.feed_recent_episodes(cfg) is None


def test_feed_recent_episodes_routes_the_real_gabfest_config_to_its_rss_feed(monkeypatch) -> None:
    """The rss_guid arm runs --strict daily and no test reached it.

    entities.yml runs `data_health.py --strict` unscoped every day, so this dispatch is
    live for culture-gabfest on every run. A guard typo, a renamed attribute or a slipped
    kwarg here returns None, which the check reports as an UNVERIFIED *warning* — and
    warnings never trip --strict. The second source would degrade to a line in the
    Actions log, CI would stay green, and nobody would be paged: the exact silent-green
    this check exists to prevent. So drive the dispatch with the REAL config, not a
    SimpleNamespace, and assert the identity is the one the importer writes.
    """
    from pipeline.scrapers.gabfest.import_gabfest import episode_url, parse_feed
    from pipeline.show_config import SHOWS
    from tests.test_import_gabfest import SAMPLE_FEED

    asked: dict = {}

    def fake_get(url, **kwargs):
        asked["url"] = url
        return _Resp(content=SAMPLE_FEED)

    monkeypatch.setattr(feed_check.requests, "get", fake_get)

    episodes = feed_check.feed_recent_episodes(SHOWS["culture-gabfest"])

    assert asked, "the rss_guid arm never reached rss_recent_episodes"
    assert asked["url"] == SHOWS["culture-gabfest"].feed_url  # the Acast feed Slate moved it to
    expected = [
        episode_url(it)
        for it in parse_feed(SAMPLE_FEED)
        if it["title"].startswith("Culture Gabfest")
    ]
    assert [ep.identity for ep in episodes] == expected  # the guid the importer stores
    # The filter comes from show_config.feed_title_prefix: the ICYMI item is a different
    # Slate show in the same feed.
    assert all("ICYMI" not in ep.title for ep in episodes)


def test_rss_recent_episodes_without_a_prefix_filters_nothing(monkeypatch) -> None:
    """None used to mean "Culture Gabfest's prefix", which would have filtered every
    other show's feed down to nothing once they read RSS (review of PR #84)."""
    from tests.test_import_gabfest import SAMPLE_FEED

    monkeypatch.setattr(feed_check.requests, "get", lambda *a, **k: _Resp(content=SAMPLE_FEED))
    episodes = feed_check.rss_recent_episodes("https://example.org/feed.xml")
    assert len(episodes) == 3 and any("ICYMI" in ep.title for ep in episodes)


def test_a_new_show_is_not_expected_to_hold_its_back_catalogue(monkeypatch) -> None:
    """Machine Gods starts at the cut-over unless Kevin says backfill; its October
    episodes in the feed must not read as an import that fell behind."""
    old, new = date(2026, 10, 21), date(2026, 11, 20)
    monkeypatch.setattr(feed_check, "rss_recent_episodes",
                        lambda *a, **k: [feed_check.FeedEpisode("n", new, "N"), feed_check.FeedEpisode("o", old, "O")])
    cfg = _rss_cfg(import_since=date(2026, 11, 1))
    assert [ep.identity for ep in feed_check.feed_recent_episodes(cfg)] == ["n"]

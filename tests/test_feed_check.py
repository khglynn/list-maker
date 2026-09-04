"""feed_check: the second source every "are we caught up?" verdict leans on.

Every other test mocks feed_recent_dates away, so the real date handling — Taddy's unix
timestamps, RSS pubDate timezone normalization, the future-date filter — had no test of
its own until 2026-09-01. These pin the module's stated contract: a non-empty list of
dates newest-first (all <= today) when it got a trustworthy answer, None otherwise.

The identity readers added 2026-09-03 (taddy_recent_episodes / rss_recent_episodes /
feed_recent_episodes) answer "WHICH episodes?" under the same contract, and carry the
drift guard that keeps the identity they build equal to the url the importer writes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from pipeline import feed_check


def test_rss_date_normalizes_a_late_night_offset_to_the_utc_day() -> None:
    # 11:30pm Central on Aug 31 is already Sep 1 in UTC — the day the feed check compares.
    assert feed_check._rss_date("Mon, 31 Aug 2026 23:30:00 -0500") == date(2026, 9, 1)
    assert feed_check._rss_date("Tue, 01 Sep 2026 12:00:00 GMT") == date(2026, 9, 1)


def test_rss_date_rejects_garbage_without_raising() -> None:
    assert feed_check._rss_date(None) is None
    assert feed_check._rss_date("") is None
    assert feed_check._rss_date("not a date") is None


def test_ts_to_date_reads_taddy_unix_seconds_and_survives_junk() -> None:
    assert feed_check._ts_to_date(1788294624) == date(2026, 9, 1)  # the 09-01 pulse's timestamp
    assert feed_check._ts_to_date("1788294624") == date(2026, 9, 1)
    assert feed_check._ts_to_date(None) is None
    assert feed_check._ts_to_date("nope") is None


def test_feed_recent_dates_drops_future_dated_entries_and_sorts_newest_first(monkeypatch) -> None:
    today = datetime.now(timezone.utc).date()
    tomorrow, yesterday = today + timedelta(days=1), today - timedelta(days=1)
    monkeypatch.setattr(feed_check, "taddy_recent_dates", lambda uuid, limit: [yesterday, tomorrow, today])
    cfg = SimpleNamespace(taddy_uuid="abc", fallback_website_url=None)
    assert feed_check.feed_recent_dates(cfg) == [today, yesterday]


def test_feed_recent_dates_is_none_when_nothing_trustworthy_remains(monkeypatch) -> None:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    monkeypatch.setattr(feed_check, "taddy_recent_dates", lambda uuid, limit: [tomorrow])
    assert feed_check.feed_recent_dates(SimpleNamespace(taddy_uuid="abc", fallback_website_url=None)) is None
    monkeypatch.setattr(feed_check, "taddy_recent_dates", lambda uuid, limit: None)
    assert feed_check.feed_recent_dates(SimpleNamespace(taddy_uuid="abc", fallback_website_url=None)) is None


def test_feed_recent_dates_has_no_source_for_a_curated_show() -> None:
    # No Taddy uuid and no Megaphone feed: nothing to ask. None, never a fake "caught up".
    cfg = SimpleNamespace(taddy_uuid=None, fallback_website_url="https://openai.com/news/")
    assert feed_check.feed_recent_dates(cfg) is None


# ---- identity readers: WHICH episodes the feed has, not just which dates ----


def _taddy_payload(*episodes: tuple[str, int, str]) -> dict:
    return {
        "data": {
            "getLatestPodcastEpisodes": [
                {"uuid": uuid, "datePublished": ts, "name": name} for uuid, ts, name in episodes
            ]
        }
    }


class _Resp:
    def __init__(self, payload=None, content=b"") -> None:
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_taddy_recent_episodes_pairs_identity_with_date_and_sorts(monkeypatch) -> None:
    monkeypatch.setenv("TADDY_USER_ID", "u")
    monkeypatch.setenv("TADDY_API_KEY", "k")
    monkeypatch.setattr(
        feed_check.requests,
        "post",
        lambda *a, **k: _Resp(
            _taddy_payload(
                ("uuid-older", 1786291200, "Older"),
                ("uuid-newer", 1788105600, "Newer"),
            )
        ),
    )

    episodes = feed_check.taddy_recent_episodes("series-1")

    assert [ep.identity for ep in episodes] == [
        "https://api.taddy.org/podcast-episode/uuid-newer",
        "https://api.taddy.org/podcast-episode/uuid-older",
    ]
    assert [ep.title for ep in episodes] == ["Newer", "Older"]
    assert episodes[0].publish_date > episodes[1].publish_date


def test_taddy_recent_episodes_drops_rows_it_cannot_identify(monkeypatch) -> None:
    """A feed row with no uuid or an unparseable date can't be looked up, so it must be
    dropped — not turned into a phantom 'missing episode' no import could ever satisfy."""
    monkeypatch.setenv("TADDY_USER_ID", "u")
    monkeypatch.setenv("TADDY_API_KEY", "k")
    payload = _taddy_payload(("uuid-ok", 1788105600, "Fine"))
    payload["data"]["getLatestPodcastEpisodes"] += [
        {"uuid": None, "datePublished": 1788105600, "name": "No uuid"},
        {"uuid": "uuid-bad-date", "datePublished": "nope", "name": "Bad date"},
    ]
    monkeypatch.setattr(feed_check.requests, "post", lambda *a, **k: _Resp(payload))

    assert [ep.title for ep in feed_check.taddy_recent_episodes("series-1")] == ["Fine"]


def test_taddy_recent_episodes_is_none_when_it_could_not_verify(monkeypatch) -> None:
    """Same None contract as the date reader: a GraphQL 200-with-errors is not 'empty'."""
    monkeypatch.setenv("TADDY_USER_ID", "u")
    monkeypatch.setenv("TADDY_API_KEY", "k")
    monkeypatch.setattr(
        feed_check.requests, "post", lambda *a, **k: _Resp({"errors": [{"message": "nope"}]})
    )
    assert feed_check.taddy_recent_episodes("series-1") is None

    def boom(*a, **k):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(feed_check.requests, "post", boom)
    assert feed_check.taddy_recent_episodes("series-1") is None

    monkeypatch.delenv("TADDY_USER_ID")
    assert feed_check.taddy_recent_episodes("series-1") is None


def test_taddy_identity_matches_what_the_importer_writes() -> None:
    """THE drift guard. The feed check rebuilds an identity string and compares it to
    episodes.url; if the importer ever writes a different shape, every Taddy show reports
    every episode missing.

    episode_url_key delegates to taddy_episode_url today, so this is near-tautological on
    purpose — what it catches is someone re-inlining the f-string in the importer, which
    is exactly how the two would come apart again."""
    from pipeline.scrapers.taddy.import_transcripts import episode_url_key
    from pipeline.show_config import taddy_episode_url

    assert taddy_episode_url("abc-123") == episode_url_key({"uuid": "abc-123"})
    # And the importer still prefers the uuid over a generic show-level websiteUrl.
    assert taddy_episode_url("abc-123") == episode_url_key(
        {"uuid": "abc-123", "websiteUrl": "https://www.nytimes.com/column/hard-fork"}, 48
    )


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
        "taddy_recent_episodes",
        lambda uuid, limit: [
            feed_check.FeedEpisode("a", yesterday, "A"),
            feed_check.FeedEpisode("b", tomorrow, "Pre-release"),
            feed_check.FeedEpisode("c", today, "C"),
        ],
    )
    cfg = SimpleNamespace(episode_identity="taddy_uuid", taddy_uuid="abc", fallback_website_url=None)

    assert [ep.identity for ep in feed_check.feed_recent_episodes(cfg)] == ["c", "a"]


def test_feed_recent_episodes_has_no_identity_source_for_a_date_compared_show() -> None:
    """SOP has a taddy_uuid — Taddy is its second source — but its rows are written by
    its own scraper, so there is no id to compare. The branch is on episode_identity for
    exactly this reason; getting it wrong reported 13 of SOP's 15 feed episodes missing
    (measured against live Neon, 2026-09-03)."""
    sop_shaped = SimpleNamespace(
        episode_identity=None, taddy_uuid="abc", fallback_website_url="https://switchedonpop.com"
    )
    assert feed_check.feed_recent_episodes(sop_shaped) is None


def test_feed_recent_episodes_is_none_when_the_source_could_not_be_verified(monkeypatch) -> None:
    cfg = SimpleNamespace(episode_identity="taddy_uuid", taddy_uuid="abc", fallback_website_url=None)
    monkeypatch.setattr(feed_check, "taddy_recent_episodes", lambda uuid, limit: None)
    assert feed_check.feed_recent_episodes(cfg) is None
    # Empty after dropping future dates is also unverified, never a green "caught up".
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    monkeypatch.setattr(
        feed_check,
        "taddy_recent_episodes",
        lambda uuid, limit: [feed_check.FeedEpisode("a", tomorrow, "A")],
    )
    assert feed_check.feed_recent_episodes(cfg) is None

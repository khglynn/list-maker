"""feed_check: the second source every "are we caught up?" verdict leans on.

Every other test mocks feed_recent_dates away, so the real date handling — Taddy's unix
timestamps, RSS pubDate timezone normalization, the future-date filter — had no test of
its own until 2026-09-01. These pin the module's stated contract: a non-empty list of
dates newest-first (all <= today) when it got a trustworthy answer, None otherwise.
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

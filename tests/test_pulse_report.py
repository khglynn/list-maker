"""Unit tests for the pulse digest logic.

The pulse's core decision is "are we caught up to the real feed?" (second source), and
its job is to never show green when we're behind or can't verify. That logic is pinned
here. Pure formatting + comparison, no DB / no network.
"""

from __future__ import annotations

from datetime import date

from pipeline.data_health import CheckResult
from pipeline.pulse_report import build_digest, show_status

TOTALS = {"entities": 100, "mentions": 200, "notion_transcripts": 50}


def _show(slug: str = "ai-daily-brief", latest: str = "2026-06-06", days: int = 1,
          recent: int = 2, feed_dates: object = "caught_up") -> dict:
    ldate = date.fromisoformat(latest) if latest else None
    fd = feed_dates
    if fd == "caught_up":  # feed agrees with our latest
        fd = [ldate] if ldate else []
    return {"slug": slug, "latest": ldate, "days_since": days, "recent": recent,
            "episodes": 10, "feed_dates": fd, "cfg": None}


def test_caught_up_is_ok() -> None:
    status, state = show_status(_show())
    assert "caught up" in status and state == "ok"


def test_behind_the_feed_flags_and_counts() -> None:
    # Feed has two episodes newer than ours -> BEHIND 2.
    s = _show(latest="2026-06-06", feed_dates=[date(2026, 6, 8), date(2026, 6, 7), date(2026, 6, 6)])
    status, state = show_status(s)
    assert "BEHIND 2" in status and state == "behind"


def test_feed_unverified_is_its_own_state() -> None:
    # Couldn't reach the feed -> "unverified", NOT green, NOT behind.
    status, state = show_status(_show(feed_dates=None))
    assert "unverified" in status and state == "unverified"


def test_quiet_show_caught_up_is_explained() -> None:
    # Caught up but old (25d > TAL's 21d threshold, feed also old) -> ok, but says "quiet".
    s = _show(slug="tal", latest="2026-05-13", days=25, feed_dates=[date(2026, 5, 13)])
    status, state = show_status(s)
    assert "caught up" in status and "quiet" in status and state == "ok"


def test_digest_has_hub_link_and_green_headline() -> None:
    digest = build_digest([_show()], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "Pod Lists hub" in digest
    assert "caught up to every feed" in digest


def test_digest_behind_flips_headline() -> None:
    behind = _show(feed_dates=[date(2026, 6, 8), date(2026, 6, 6)])  # behind 1
    digest = build_digest([behind], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "need attention" in digest


def test_digest_unverified_is_not_claimed_as_every_feed() -> None:
    # An unverified feed must NOT produce "caught up to every feed" — that would be a lie.
    digest = build_digest([_show(feed_dates=None)], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "caught up to every feed" not in digest
    assert "unverified" in digest


def test_digest_warning_only_stays_green_but_counted() -> None:
    checks = [CheckResult("possible_entity_alias_splits", "warn", "25 splits", [])]
    digest = build_digest([_show()], TOTALS, checks)
    assert "caught up to every feed" in digest
    assert "1 warning" in digest
    assert "Needs attention" not in digest


# ---- curated sources, the grace window, and the intake line (2026-09-02) ----

class _Cfg:
    def __init__(self, medium: str = "podcast", grace: int = 2) -> None:
        self.medium = medium
        self.feed_grace_days = grace
        self.notion_database_id = None
        self.spotify_playlist_id = None


def test_curated_source_is_curated_not_unverified() -> None:
    # A blog source has no feed. "Unverified" is for feeds we tried to read and couldn't.
    s = _show(slug="openai-blog", latest="2026-06-10", feed_dates=None)
    s["cfg"] = _Cfg("blog")
    status, state = show_status(s)
    assert state == "curated"
    assert "unverified" not in status and "2026-06-10" in status


def test_digest_does_not_count_curated_as_unverified() -> None:
    s = _show(slug="openai-blog", latest="2026-06-10", feed_dates=None)
    s["cfg"] = _Cfg("blog")
    digest = build_digest([_show(), s], TOTALS, [CheckResult("x", "pass", "ok", [])],
                          intake={"judged": 0, "would_save_backlog": 0})
    assert "unverified" not in digest
    assert "All systems firing" in digest
    assert "Curated sources" in digest and "OpenAI blog" in digest


def test_fresh_episode_inside_the_grace_window_is_caught_up() -> None:
    # Published two days ago, imported daily with a 2-day grace: pending, not behind.
    s = _show(latest="2026-08-29", feed_dates=[date(2026, 8, 31), date(2026, 8, 29)])
    status, state = show_status(s, today=date(2026, 9, 1))
    assert state == "ok" and "pending" in status


def test_stale_missing_episode_is_still_behind(monkeypatch) -> None:
    s = _show(latest="2026-08-29", feed_dates=[date(2026, 8, 31), date(2026, 8, 29)])
    status, state = show_status(s, today=date(2026, 9, 6))
    assert state == "behind" and "BEHIND 1" in status


def test_intake_line_reports_what_the_judge_did() -> None:
    # Changed 2026-09-02: this used to nudge Kevin about unchecked boxes. Nothing waits
    # on a checkbox now, so the pulse reports what the judge DID and what shadow mode
    # has piled up unhandled.
    digest = build_digest([_show()], TOTALS, [], intake={
        "judged": 18, "would_save": 5, "disputed": 2, "would_save_backlog": 9, "held": 1})
    assert "18 judged" in digest and "5 marked save" in digest
    assert "2 disputed" in digest and "9 not yet ingested" in digest


def test_intake_line_keeps_a_stuck_failure_visible() -> None:
    # The weekly Slack line reports only the run that produced a failure. The pulse
    # reads a 15-day window, so a row stuck failing stays visible instead of being
    # mentioned once and then going quiet forever.
    digest = build_digest([_show()], TOTALS, [], intake={
        "judged": 4, "would_save": 1, "would_save_backlog": 1, "failed": 3})
    assert "3 failed" in digest


def test_intake_line_is_quiet_but_present_on_an_empty_period() -> None:
    digest = build_digest([_show()], TOTALS, [], intake={"judged": 0, "would_save_backlog": 0})
    assert "nothing judged this period" in digest


def test_intake_line_is_honest_when_the_table_cannot_be_read() -> None:
    # A missing table or a Neon hiccup must read as "couldn't check", never as zeroes.
    digest = build_digest([_show()], TOTALS, [], intake=None)
    assert "couldn't read" in digest

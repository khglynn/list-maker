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

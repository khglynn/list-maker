"""Unit tests for the pulse digest logic.

The pulse is a heartbeat Kevin reads at a glance, so its core decision — green vs.
needs-attention, and what gets surfaced — is pinned here. Pure formatting, no DB.
"""

from __future__ import annotations

from pipeline.data_health import CheckResult
from pipeline.pulse_report import build_digest

TOTALS = {"entities": 100, "mentions": 200, "notion_transcripts": 50}


def _show(slug: str, days: int | None, eps: int = 10, latest: str = "2026-06-06") -> dict:
    return {"slug": slug, "days_since": days, "latest": latest, "episodes": eps}


def test_all_green_when_no_failures() -> None:
    digest = build_digest([_show("ai-daily-brief", 1)], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "All systems firing" in digest
    assert "Needs attention" not in digest


def test_failure_surfaces_and_flips_headline() -> None:
    checks = [CheckResult("episode_freshness_by_show", "fail", "a show is stale", [])]
    digest = build_digest([_show("ai-daily-brief", 1)], TOTALS, checks)
    assert "issue(s) need attention" in digest
    assert "episode_freshness_by_show: a show is stale" in digest


def test_warning_only_stays_green_but_is_counted_not_listed() -> None:
    checks = [CheckResult("possible_entity_alias_splits", "warn", "25 splits", [])]
    digest = build_digest([_show("ai-daily-brief", 1)], TOTALS, checks)
    assert "All systems firing" in digest
    assert "1 warning" in digest
    assert "Needs attention" not in digest  # advisory warnings aren't listed


def test_stale_show_is_marked() -> None:
    # AI Daily threshold is 3 days; 99 days is well past it.
    digest = build_digest([_show("ai-daily-brief", 99)], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "STALE" in digest


def test_show_with_no_episodes_is_handled() -> None:
    digest = build_digest([_show("hard-fork", None)], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "no episodes yet" in digest

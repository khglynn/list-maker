from datetime import date

from pipeline.data_health import (
    CheckResult,
    _date_lag_days,
    check_episode_freshness,
    render_text,
)


def test_date_lag_days_handles_missing_dates() -> None:
    assert _date_lag_days(None, date(2026, 1, 1)) is None
    assert _date_lag_days(date(2026, 1, 1), None) is None


def test_date_lag_days_counts_episode_minus_transcript_date() -> None:
    assert _date_lag_days(date(2026, 1, 10), date(2026, 1, 3)) == 7


def test_render_text_summarizes_failures_and_warnings() -> None:
    report = render_text(
        [
            CheckResult("clean", "pass", "Looks good.", []),
            CheckResult("needs_attention", "warn", "Review this.", ["one detail"]),
            CheckResult("broken", "fail", "Fix this.", ["bad detail"]),
        ]
    )

    assert "[PASS] clean" in report
    assert "[WARN] needs_attention" in report
    assert "[FAIL] broken" in report
    assert "Totals: 1 failure(s), 1 warning(s), 3 checks" in report


def test_check_episode_freshness_flags_stale(monkeypatch) -> None:
    import pipeline.data_health as dh

    rows = [
        {"slug": "ai-daily-brief", "latest_episode": date(2026, 6, 5), "days_since": 1},
        {"slug": "tal", "latest_episode": date(2026, 4, 27), "days_since": 40},
        {"slug": "sop", "latest_episode": date(2026, 6, 1), "days_since": 5},
    ]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    result = check_episode_freshness(conn=None)
    assert result.status == "fail"
    assert any("tal: no new episode in 40 days" in d for d in result.details)
    assert not any("ai-daily-brief: no new episode" in d for d in result.details)
    assert not any("sop: no new episode" in d for d in result.details)


def test_check_episode_freshness_passes_when_all_recent(monkeypatch) -> None:
    import pipeline.data_health as dh

    rows = [{"slug": "ai-daily-brief", "latest_episode": date(2026, 6, 6), "days_since": 0}]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    assert check_episode_freshness(conn=None).status == "pass"

from datetime import date

from pipeline.data_health import CheckResult, _date_lag_days, render_text


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

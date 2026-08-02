from datetime import date

from pipeline.data_health import (
    CheckResult,
    _date_lag_days,
    check_episode_freshness,
    check_import_caught_up,
    check_notion_sync_freshness,
    render_text,
)


def _patch_notion_freshness(monkeypatch, *, transcript_rows, stale_entities, failed_entities):
    """The check makes one _rows call (transcript backlog) and two _one calls
    (stale entity count, failed entity count) — dispatch _one on SQL content."""
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: transcript_rows)

    def fake_one(conn, sql, params=None):
        if "notion_sync_status" in sql:
            return {"count": failed_entities}
        return {"count": stale_entities}

    monkeypatch.setattr(dh, "_one", fake_one)


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


def test_check_episode_freshness_skips_ended_shows(monkeypatch) -> None:
    """Culture Gabfest ended 2026-07-01. Without this skip the check fails every run
    forever on a show that can never be fresh again — noise, not signal."""
    import pipeline.data_health as dh

    rows = [
        {"slug": "culture-gabfest", "latest_episode": date(2026, 7, 1), "days_since": 400},
        {"slug": "ai-daily-brief", "latest_episode": date(2026, 8, 1), "days_since": 1},
    ]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    result = check_episode_freshness(conn=None)
    assert result.status == "pass"
    assert not any("culture-gabfest: no new episode" in d for d in result.details)
    assert any("culture-gabfest: show ended 2026-07-01" in d for d in result.details)


def test_check_episode_freshness_passes_when_all_recent(monkeypatch) -> None:
    import pipeline.data_health as dh

    rows = [{"slug": "ai-daily-brief", "latest_episode": date(2026, 6, 6), "days_since": 0}]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    assert check_episode_freshness(conn=None).status == "pass"


def test_feed_check_can_scope_to_one_show(monkeypatch) -> None:
    """The music workflow runs this per-show, so it must hit exactly one feed —
    paying for a call per show on every music run is what makes people delete the check."""
    import pipeline.data_health as dh

    monkeypatch.setattr(
        dh, "_rows", lambda *a, **k: [{"slug": "tal", "db_latest": date(2026, 5, 17)}]
    )
    asked: list[str] = []

    def fake_feed(cfg, limit=15):
        asked.append(cfg.slug)
        return [date(2026, 7, 26)]

    monkeypatch.setattr(dh, "feed_recent_dates", fake_feed)

    result = check_import_caught_up(conn=None, slugs=["tal"])

    assert asked == ["tal"]  # not sop, not the whole catalogue
    assert result.status == "fail"
    assert any("tal: BEHIND" in d for d in result.details)


def test_feed_check_unscoped_still_covers_every_show(monkeypatch) -> None:
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: [])
    asked: list[str] = []
    monkeypatch.setattr(
        dh, "feed_recent_dates", lambda cfg, limit=15: asked.append(cfg.slug) or None
    )

    check_import_caught_up(conn=None)

    assert "tal" in asked and "sop" in asked


def test_notion_sync_freshness_fails_on_transcript_backlog(monkeypatch) -> None:
    _patch_notion_freshness(
        monkeypatch,
        transcript_rows=[{"slug": "ai-daily-brief", "unsynced": 3, "oldest": date(2026, 6, 7)}],
        stale_entities=0,
        failed_entities=0,
    )
    result = check_notion_sync_freshness(conn=None)
    assert result.status == "fail"
    assert any("ai-daily-brief: 3 transcript(s) unsynced" in d for d in result.details)


def test_notion_sync_freshness_fails_on_stale_entity_pages(monkeypatch) -> None:
    _patch_notion_freshness(
        monkeypatch, transcript_rows=[], stale_entities=5, failed_entities=0
    )
    result = check_notion_sync_freshness(conn=None)
    assert result.status == "fail"
    assert any("5 entity page(s)" in d for d in result.details)


def test_notion_sync_freshness_warns_on_lingering_failed(monkeypatch) -> None:
    _patch_notion_freshness(
        monkeypatch, transcript_rows=[], stale_entities=0, failed_entities=2
    )
    result = check_notion_sync_freshness(conn=None)
    assert result.status == "warn"


def test_notion_sync_freshness_passes_when_clean(monkeypatch) -> None:
    _patch_notion_freshness(
        monkeypatch, transcript_rows=[], stale_entities=0, failed_entities=0
    )
    assert check_notion_sync_freshness(conn=None).status == "pass"

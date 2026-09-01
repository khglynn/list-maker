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


def test_selfheal_check_passes_when_queue_is_empty(monkeypatch) -> None:
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: [])
    result = dh.check_transcript_race_selfheal(conn=None)
    assert result.status == "pass"


def test_selfheal_check_warns_while_the_queue_is_still_draining(monkeypatch) -> None:
    """A freshly-damaged episode is the system working, not a fault — the next run
    heals it. Failing here would train us to ignore the alert."""
    import pipeline.data_health as dh

    rows = [{
        "slug": "ai-daily-brief", "episode_id": 7261, "mentions": 3,
        "transcript_arrived": date(2026, 8, 1), "days_pending": 1,
    }]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    result = dh.check_transcript_race_selfheal(conn=None)
    assert result.status == "warn"
    assert "queued for self-heal" in result.summary


def test_selfheal_check_fails_when_the_queue_stops_draining(monkeypatch) -> None:
    """Days-old pending episodes mean the heal is failing or never running. Counting
    rows alone could not tell that apart from a heal in progress."""
    import pipeline.data_health as dh

    rows = [{
        "slug": "hard-fork", "episode_id": 5133, "mentions": 7,
        "transcript_arrived": date(2026, 6, 18), "days_pending": 45,
    }]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    result = dh.check_transcript_race_selfheal(conn=None)
    assert result.status == "fail"
    assert "not draining" in result.summary
    assert any("hard-fork ep 5133" in d for d in result.details)


def test_extraction_integrity_no_longer_double_reports_the_race(monkeypatch) -> None:
    """The race has one owner (check_transcript_race_selfheal). This check keeps only
    the orphan case: transcript_id pointing at a transcript that no longer exists."""
    import pipeline.data_health as dh

    seen: list[str] = []

    def fake_one(conn, sql, params=None):
        seen.append(" ".join(sql.split()))
        return {"transcripted_without_mentions": 0, "count": 0}

    monkeypatch.setattr(dh, "_one", fake_one)
    result = dh.check_ai_daily_extraction(conn=None)

    assert result.status == "pass"
    orphan_sql = next(s for s in seen if "transcript_id IS NOT NULL" in s)
    assert "m.transcript_id IS NULL" not in orphan_sql


# ---- feed check grace window (the August-2026 "1 show behind" noise) ----

def _feed_check(monkeypatch, db_latest: dict, feed: dict, today: date, slugs: list[str]):
    import pipeline.data_health as dh

    monkeypatch.setattr(
        dh, "_rows", lambda *a, **k: [{"slug": s, "db_latest": d} for s, d in db_latest.items()]
    )
    monkeypatch.setattr(dh, "feed_recent_dates", lambda cfg, limit=15: feed.get(cfg.slug))
    monkeypatch.setattr(dh, "_today", lambda: today)
    return check_import_caught_up(conn=None, slugs=slugs)


def test_feed_check_tolerates_a_fresh_episode_inside_the_import_window(monkeypatch) -> None:
    # Tuesday: SOP published today; its next import is Wednesday. Not a gap.
    result = _feed_check(
        monkeypatch,
        {"sop": date(2026, 8, 25)},
        {"sop": [date(2026, 9, 1), date(2026, 8, 25)]},
        today=date(2026, 9, 1),
        slugs=["sop"],
    )
    assert result.status == "pass"
    assert any(d.startswith("sop: caught up") and "pending" in d for d in result.details)


def test_feed_check_fails_once_a_missing_episode_is_older_than_the_grace(monkeypatch) -> None:
    # Sunday: the Wed AND Fri imports both had their turn and the 09-01 episode is still absent.
    result = _feed_check(
        monkeypatch,
        {"sop": date(2026, 8, 25)},
        {"sop": [date(2026, 9, 1), date(2026, 8, 25)]},
        today=date(2026, 9, 6),
        slugs=["sop"],
    )
    assert result.status == "fail"
    assert any(d.startswith("sop: BEHIND 1") and "import window" in d for d in result.details)


def test_feed_grace_is_per_show(monkeypatch) -> None:
    # The same 3-day-old feed episode is fine for SOP (4-day window) and a real miss
    # for AI Daily (2-day window, imported every day).
    feed = [date(2026, 9, 1)]
    result = _feed_check(
        monkeypatch,
        {"sop": date(2026, 8, 25), "ai-daily-brief": date(2026, 8, 29)},
        {"sop": feed, "ai-daily-brief": feed},
        today=date(2026, 9, 4),
        slugs=["sop", "ai-daily-brief"],
    )
    assert result.status == "fail"
    assert any(d.startswith("sop: caught up") for d in result.details)
    assert any(d.startswith("ai-daily-brief: BEHIND 1") for d in result.details)


def test_split_missing_feed_dates_partitions_by_grace() -> None:
    from pipeline.data_health import split_missing_feed_dates

    today = date(2026, 9, 10)
    overdue, pending = split_missing_feed_dates(
        [date(2026, 9, 9), date(2026, 9, 5), date(2026, 9, 1)], date(2026, 9, 1), 2, today=today
    )
    assert overdue == [date(2026, 9, 5)]
    assert pending == [date(2026, 9, 9)]
    # Nothing in the DB at all: every feed date is missing, still graded by age.
    assert split_missing_feed_dates([date(2026, 9, 9)], None, 2, today=today) == ([], [date(2026, 9, 9)])
    assert split_missing_feed_dates([date(2026, 9, 1)], None, 2, today=today) == ([date(2026, 9, 1)], [])

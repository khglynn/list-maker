from datetime import date

from pipeline.data_health import (
    CheckResult,
    HeldEpisodes,
    _date_lag_days,
    check_episode_freshness,
    check_import_caught_up,
    check_notion_sync_freshness,
    render_text,
)
from pipeline.feed_check import FeedEpisode


def _held_row(slug: str, url: str | None, title: str | None, publish_date: date | None) -> dict:
    """One row as _held_episodes_by_show's bulk query returns it."""
    return {"slug": slug, "url": url, "title": title, "publish_date": publish_date}


def _held(*episodes: tuple[str, str, date]) -> HeldEpisodes:
    """A HeldEpisodes built from (url, title, publish_date) triples."""
    held = HeldEpisodes(urls=set(), title_dates=set())
    for url, title, published in episodes:
        held.urls.add(url)
        held.title_dates.add((title.strip().lower(), published))
        if held.latest is None or published > held.latest:
            held.latest = published
    return held


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

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: [_held_row("tal", "held-1", "Old one", date(2026, 5, 17))])
    asked: list[str] = []

    def fake_feed(cfg, limit=15):
        asked.append(cfg.slug)
        return [FeedEpisode("tal-new", date(2026, 7, 26), "A new episode")]

    # TAL is identity-compared (its discovery runs the Taddy importer), so the seam
    # this test holds is feed_recent_episodes, not feed_recent_dates.
    monkeypatch.setattr(dh, "feed_recent_episodes", fake_feed)

    result = check_import_caught_up(conn=None, slugs=["tal"])

    assert asked == ["tal"]  # not sop, not the whole catalogue
    assert result.status == "fail"
    assert any("tal: BEHIND" in d for d in result.details)


def test_feed_check_unscoped_still_covers_every_show(monkeypatch) -> None:
    """Both readers must be asked: SOP has no comparable identity and takes the date
    path, every other show takes the identity path. Watching only one seam would let
    half the catalogue go unchecked while the test still passed."""
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: [])
    asked: list[str] = []
    monkeypatch.setattr(
        dh, "feed_recent_dates", lambda cfg, limit=15: asked.append(cfg.slug) or None
    )
    monkeypatch.setattr(
        dh, "feed_recent_episodes", lambda cfg, limit=15: asked.append(cfg.slug) or None
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

def _feed_check(
    monkeypatch,
    *,
    rows: list[dict],
    today: date,
    slugs: list[str],
    feed_dates: dict | None = None,
    feed_episodes: dict | None = None,
):
    """Drive check_import_caught_up with both feed readers stubbed.

    Both seams are always patched, never just the one a given show uses — an unpatched
    reader would reach the real network inside a "hermetic" test, and the identity path
    and the date path are chosen per show by ShowConfig.episode_identity.
    """
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)
    monkeypatch.setattr(dh, "feed_recent_dates", lambda cfg, limit=15: (feed_dates or {}).get(cfg.slug))
    monkeypatch.setattr(
        dh, "feed_recent_episodes", lambda cfg, limit=15: (feed_episodes or {}).get(cfg.slug)
    )
    monkeypatch.setattr(dh, "_today", lambda: today)
    return check_import_caught_up(conn=None, slugs=slugs)


def test_feed_check_tolerates_a_fresh_episode_inside_the_import_window(monkeypatch) -> None:
    # Tuesday: SOP published today; its next import is Wednesday. Not a gap.
    # SOP is the date-compared show (its scraper writes the urls Taddy never sees).
    result = _feed_check(
        monkeypatch,
        rows=[_held_row("sop", "https://switchedonpop.com/episodes/x", "X", date(2026, 8, 25))],
        feed_dates={"sop": [date(2026, 9, 1), date(2026, 8, 25)]},
        today=date(2026, 9, 1),
        slugs=["sop"],
    )
    assert result.status == "pass"
    assert any(d.startswith("sop: caught up") and "pending" in d for d in result.details)


def test_feed_check_fails_once_a_missing_episode_is_older_than_the_grace(monkeypatch) -> None:
    # Sunday: the Wed AND Fri imports both had their turn and the 09-01 episode is still absent.
    result = _feed_check(
        monkeypatch,
        rows=[_held_row("sop", "https://switchedonpop.com/episodes/x", "X", date(2026, 8, 25))],
        feed_dates={"sop": [date(2026, 9, 1), date(2026, 8, 25)]},
        today=date(2026, 9, 6),
        slugs=["sop"],
    )
    assert result.status == "fail"
    assert any(d.startswith("sop: BEHIND 1") and "import window" in d for d in result.details)


def test_feed_grace_is_per_show(monkeypatch) -> None:
    # The same 3-day-old feed episode is fine for SOP (4-day window) and a real miss
    # for AI Daily (2-day window, imported every day). SOP is compared by date, AI Daily
    # by identity — the grace window means the same thing on both paths.
    result = _feed_check(
        monkeypatch,
        rows=[
            _held_row("sop", "https://switchedonpop.com/episodes/x", "X", date(2026, 8, 25)),
            _held_row("ai-daily-brief", "taddy:held", "Held one", date(2026, 8, 29)),
        ],
        feed_dates={"sop": [date(2026, 9, 1)]},
        feed_episodes={"ai-daily-brief": [FeedEpisode("taddy:missing", date(2026, 9, 1), "New")]},
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


# ---- feed check BY EPISODE IDENTITY (the re-dating false positive + mid-series holes) ----

def test_split_missing_feed_episodes_catches_a_mid_series_hole() -> None:
    """THE acceptance case. B is missing and OLDER than the newest episode we hold, so
    MAX(publish_date) can never see it — split_missing_feed_dates would call this show
    caught up forever. Identity is a set question, so the hole is just another entry."""
    from pipeline.data_health import split_missing_feed_episodes

    feed = [
        FeedEpisode("ep-A", date(2026, 9, 1), "A"),
        FeedEpisode("ep-B", date(2026, 8, 25), "B"),
        FeedEpisode("ep-C", date(2026, 8, 18), "C"),
    ]
    held = _held(("ep-A", "A", date(2026, 9, 1)), ("ep-C", "C", date(2026, 8, 18)))

    overdue, pending = split_missing_feed_episodes(feed, held, 2, today=date(2026, 9, 1))

    assert [ep.identity for ep in overdue] == ["ep-B"]
    assert pending == []
    # And proof the old comparison is blind to it: nothing in the feed is newer than
    # the newest date we hold, so the date-only split reports nothing at all.
    from pipeline.data_health import split_missing_feed_dates

    assert split_missing_feed_dates(
        [ep.publish_date for ep in feed], held.latest, 2, today=date(2026, 9, 1)
    ) == ([], [])


def test_split_missing_feed_episodes_ignores_a_redated_episode() -> None:
    """The TAL incident (DEVLOG 2026-09-01): Taddy moved an episode's publish date, the
    date check read the new date as a brand-new missing episode, and the channel got a
    BEHIND that no import could ever clear. Identity does not move when a date does —
    episodes.url is UNIQUE and both upserts COALESCE publish_date ON CONFLICT (url)."""
    from pipeline.data_health import split_missing_feed_episodes

    held = _held(("ep-X", "The Episode", date(2026, 7, 1)))  # stored under its ORIGINAL date
    redated = [FeedEpisode("ep-X", date(2026, 8, 20), "The Episode")]

    # Nothing missing, at any grace window or any "today".
    assert split_missing_feed_episodes(redated, held, 2, today=date(2026, 9, 1)) == ([], [])
    assert split_missing_feed_episodes(redated, held, 0, today=date(2026, 12, 31)) == ([], [])


def test_split_missing_feed_episodes_keeps_the_grace_window() -> None:
    """A missing episode inside the show's import window is pending, not an alarm — the
    contract split_missing_feed_dates set in PR #4, unchanged by the identity switch."""
    from pipeline.data_health import split_missing_feed_episodes

    feed = [FeedEpisode("ep-new", date(2026, 9, 5), "New"), FeedEpisode("ep-old", date(2026, 9, 1), "Old")]
    held = _held(("ep-held", "Held", date(2026, 8, 30)))

    overdue, pending = split_missing_feed_episodes(feed, held, 2, today=date(2026, 9, 6))

    assert [ep.identity for ep in overdue] == ["ep-old"]  # past the 2-day window
    assert [ep.identity for ep in pending] == ["ep-new"]  # published yesterday, still fine


def test_feed_episode_held_by_title_and_date_when_the_url_scheme_is_older() -> None:
    """A row written before a show's importer changed hands holds the same episode under
    an older url. Measured 2026-09-03: 3 of TAL's 15 recent feed episodes are exactly
    this. Falling back to the importer's own title+date dedup rule is what stops them
    reporting BEHIND forever — if the importer would call it present, no import can
    ever create it, so 'missing' would be an alarm nothing could clear."""
    from pipeline.data_health import _feed_episode_is_held

    held = _held(("https://www.thisamericanlife.org/anon", "An Update from Ira", date(2025, 10, 16)))
    legacy = FeedEpisode("taddy:uuid-not-in-db", date(2025, 10, 16), "An Update from Ira")

    assert _feed_episode_is_held(legacy, held) is True
    # Same title, different date = a different episode. Not held.
    assert _feed_episode_is_held(
        FeedEpisode("taddy:other", date(2026, 1, 9), "An Update from Ira"), held
    ) is False
    # An untitled feed row must not match some other episode's title...
    assert _feed_episode_is_held(FeedEpisode("taddy:blank", date(2025, 10, 16), ""), held) is False
    # ...but it must match the title the IMPORTER gives an untitled episode, or we would
    # report an episode we hold — under a title we chose — as missing forever.
    untitled = _held(("legacy://x", "Untitled Episode", date(2026, 8, 20)))
    assert _feed_episode_is_held(FeedEpisode("taddy:blank", date(2026, 8, 20), ""), untitled) is True


def test_feed_check_catches_a_mid_series_hole_end_to_end(monkeypatch) -> None:
    """The same gap through the real check: status fail, and the gap's date named."""
    result = _feed_check(
        monkeypatch,
        rows=[
            _held_row("ai-daily-brief", "taddy:A", "A", date(2026, 9, 1)),
            _held_row("ai-daily-brief", "taddy:C", "C", date(2026, 8, 18)),
        ],
        feed_episodes={
            "ai-daily-brief": [
                FeedEpisode("taddy:A", date(2026, 9, 1), "A"),
                FeedEpisode("taddy:B", date(2026, 8, 25), "B"),
                FeedEpisode("taddy:C", date(2026, 8, 18), "C"),
            ]
        },
        today=date(2026, 9, 1),
        slugs=["ai-daily-brief"],
    )

    assert result.status == "fail"
    assert any(
        d.startswith("ai-daily-brief: BEHIND 1") and "oldest missing 2026-08-25" in d
        for d in result.details
    ), result.details
    # Actionable, not just a count: identity comparison knows exactly which episode is
    # missing, so the alert names it rather than leaving the reader to go find out.
    assert any("missing: 2026-08-25 'B'" in d for d in result.details), result.details
    # We hold the NEWEST episode, so the Slack line still reads "we have 2026-09-01" —
    # which is exactly why the date-only check called this show caught up.
    assert any("we have 2026-09-01" in d for d in result.details)


def test_feed_check_names_a_scheme_change_when_every_episode_looks_missing(monkeypatch) -> None:
    """All 15 missing is either a dead importer or an importer that quietly changed the
    url it writes. The alert has to name both, or the second one reads as the first."""
    result = _feed_check(
        monkeypatch,
        rows=[_held_row("hard-fork", "old-scheme://1", "Held", date(2026, 8, 28))],
        feed_episodes={
            "hard-fork": [
                FeedEpisode("taddy:1", date(2026, 8, 28), "One"),
                FeedEpisode("taddy:2", date(2026, 8, 21), "Two"),
            ]
        },
        today=date(2026, 9, 1),
        slugs=["hard-fork"],
    )

    assert result.status == "fail"
    assert any("EVERY recent feed episode is missing" in d for d in result.details)


def test_feed_check_still_skips_curated_sources(monkeypatch) -> None:
    """Blogs and research docs have no feed of any kind — neither reader is even asked."""
    asked: list[str] = []
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: [])
    monkeypatch.setattr(dh, "feed_recent_dates", lambda cfg, limit=15: asked.append(cfg.slug))
    monkeypatch.setattr(dh, "feed_recent_episodes", lambda cfg, limit=15: asked.append(cfg.slug))

    result = check_import_caught_up(conn=None, slugs=["openai-blog", "agentic-research"])

    assert asked == []
    assert result.status == "pass"
    assert all("curated source" in d for d in result.details)


def test_transcript_coverage_tolerates_a_transcript_that_is_not_out_yet(monkeypatch) -> None:
    """Taddy publishes a transcript about a day after the episode. Yesterday's episode
    without one is 'pending', not a failure — 08-07 and 08-24 reddened the check for
    exactly that."""
    import pipeline.data_health as dh

    today = date(2026, 9, 2)
    coverage_rows = [{
        "slug": "ai-daily-brief", "episodes": 1074, "transcripts": 1073, "missing_transcripts": 1,
        "latest_episode": date(2026, 9, 1), "latest_transcript": date(2026, 8, 31),
    }]
    missing_rows = [{"slug": "ai-daily-brief", "publish_date": date(2026, 9, 1)}]
    calls: list[str] = []

    def fake_rows(conn, sql, params=None):
        calls.append(sql)
        return missing_rows if "et.id IS NULL AND s.slug = ANY" in sql else coverage_rows

    monkeypatch.setattr(dh, "_rows", fake_rows)
    monkeypatch.setattr(dh, "_today", lambda: today)
    result = dh.check_transcript_coverage(conn=None)
    assert result.status == "pass", result.details
    assert any("awaiting transcripts inside the 2-day window" in d for d in result.details)

    # The same episode five days later is a real gap.
    monkeypatch.setattr(dh, "_today", lambda: date(2026, 9, 7))
    coverage_rows[0]["latest_transcript"] = date(2026, 8, 31)
    result = dh.check_transcript_coverage(conn=None)
    assert result.status == "fail"
    assert any("missing transcripts past the 2-day window" in d for d in result.details)
def test_extraction_integrity_ignores_declared_empty_episodes(monkeypatch) -> None:
    """An episode the extractor ran on and kept nothing for is an answer, not a gap —
    otherwise the first legitimately-empty episode pins this check red forever."""
    import pipeline.data_health as dh

    seen: list[str] = []

    def fake_one(conn, sql, params=None):
        flat = " ".join(sql.split())
        seen.append(flat)
        if "completed_empty' AND r.created_at" in flat:
            return {"count": 2}
        return {"transcripted_without_mentions": 0, "count": 0}

    monkeypatch.setattr(dh, "_one", fake_one)
    result = dh.check_ai_daily_extraction(conn=None)

    assert result.status == "pass"  # declared empties are informational
    missing_sql = next(s for s in seen if "transcripted_without_mentions" in s)
    assert "completed_empty" in missing_sql and "6 hours" in missing_sql
    assert any("declared empty" in d and "2" in d for d in result.details)


# --- sponsor share (ads as data, 2026-09-02) ---------------------------------------


def _sponsor_rows(monkeypatch, rows):
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)


def test_sponsor_share_passes_at_observed_levels(monkeypatch) -> None:
    """Measured 2026-09-02 over the 30-day window this check scans, with the retag
    applied: AI Daily 5.8% (21/360), Hard Fork 0%, PCHH 0%. Normal must stay quiet."""
    from pipeline.data_health import check_sponsor_share

    _sponsor_rows(monkeypatch, [
        {"slug": "ai-daily-brief", "mentions": 500, "ads": 28},
        {"slug": "hard-fork", "mentions": 100, "ads": 4},
    ])
    result = check_sponsor_share(None)
    assert result.status == "pass"
    assert len(result.details) == 2


def test_sponsor_share_warns_when_the_detector_over_claims(monkeypatch) -> None:
    """A roster parse that starts matching prose shows up here — it quietly caps real
    entities out of the rankings."""
    from pipeline.data_health import check_sponsor_share

    _sponsor_rows(monkeypatch, [{"slug": "ai-daily-brief", "mentions": 100, "ads": 45}])
    result = check_sponsor_share(None)
    assert result.status == "warn"
    assert "45%" in result.details[0]


def test_sponsor_share_fails_only_when_nothing_editorial_got_through(monkeypatch) -> None:
    """100% is not a heavy ad week, it is the 2026-08-23 shape: a pipeline that stopped
    producing editorial content at all."""
    from pipeline.data_health import check_sponsor_share

    _sponsor_rows(monkeypatch, [{"slug": "ai-daily-brief", "mentions": 40, "ads": 40}])
    result = check_sponsor_share(None)
    assert result.status == "fail"
    assert "no editorial content" in result.details[0]


def test_sponsor_share_does_not_judge_a_quiet_window(monkeypatch) -> None:
    """Grace-window discipline: three mentions, two of them ads, is 67% and means
    nothing. A quiet week must never turn into a red run."""
    from pipeline.data_health import check_sponsor_share

    _sponsor_rows(monkeypatch, [{"slug": "hard-fork", "mentions": 3, "ads": 2}])
    result = check_sponsor_share(None)
    assert result.status == "pass"
    assert "too few to judge" in result.details[0]


def test_sponsor_share_is_a_pass_when_no_show_has_recent_episodes(monkeypatch) -> None:
    from pipeline.data_health import check_sponsor_share

    _sponsor_rows(monkeypatch, [])
    assert check_sponsor_share(None).status == "pass"


def test_sponsor_share_watches_podcasts_not_curated_sources() -> None:
    """Blogs carry no ad reads by construction; an ended show has no recent window.
    Both would report a permanent, meaningless 0%."""
    from pipeline.data_health import SPONSOR_SHARE_SHOWS

    assert "ai-daily-brief" in SPONSOR_SHARE_SHOWS
    assert "hard-fork" in SPONSOR_SHARE_SHOWS
    assert "pchh" in SPONSOR_SHARE_SHOWS
    assert "openai-blog" not in SPONSOR_SHARE_SHOWS
    assert "agentic-research" not in SPONSOR_SHARE_SHOWS
    assert "culture-gabfest" not in SPONSOR_SHARE_SHOWS  # ended 2026-07-01
    assert "sop" not in SPONSOR_SHARE_SHOWS  # song extraction, no entity mentions


def test_sponsor_share_is_in_the_standard_check_set() -> None:
    """A check nothing runs is a check that does not exist."""
    import inspect

    from pipeline import data_health

    assert "check_sponsor_share(conn)" in inspect.getsource(data_health.run_checks)


def test_held_episodes_by_show_keeps_max_publish_date_and_empty_shows(monkeypatch) -> None:
    """The two claims the rest of the check leans on: a show with no episodes still gets
    an entry (so the loop never KeyErrors), and a row with a NULL url still counts toward
    `latest` — that is the "(we have X)" date in the Slack line, and it must stay exactly
    the MAX(publish_date) the old aggregate query returned."""
    import pipeline.data_health as dh

    rows = [
        _held_row("tal", None, "No url row", date(2026, 9, 2)),  # NULL url, newest
        _held_row("tal", "u-1", "Held", date(2026, 8, 1)),
        _held_row("tal", "u-2", None, date(2026, 7, 1)),  # NULL title
        _held_row("empty-show", None, None, None),  # LEFT JOIN, show with no episodes
    ]
    monkeypatch.setattr(dh, "_rows", lambda *a, **k: rows)

    held = dh._held_episodes_by_show(conn=None)

    assert held["tal"].latest == date(2026, 9, 2)  # the NULL-url row still counts
    assert held["tal"].urls == {"u-1", "u-2"}  # ...but is not an identity
    # A NULL-url row is still an episode we hold, and title+date is the only way to
    # match it — so it belongs here. A NULL-title row can't be matched either way.
    assert held["tal"].title_dates == {
        ("no url row", date(2026, 9, 2)),
        ("held", date(2026, 8, 1)),
    }
    assert held["empty-show"].latest is None
    assert held["empty-show"].urls == set()


def test_held_episodes_by_show_reads_one_show_when_the_check_is_scoped(monkeypatch) -> None:
    """The music workflow checks one show. Bounding the query by SHOW is the safe
    optimisation; bounding it by DATE is the forbidden one — ended Culture Gabfest still
    serves 15 pre-July episodes, so a rolling window eventually calls them all missing."""
    import pipeline.data_health as dh

    seen: dict = {}

    def fake_rows(conn, sql, params=None):
        seen["sql"], seen["params"] = sql, params
        return []

    monkeypatch.setattr(dh, "_rows", fake_rows)
    dh._held_episodes_by_show(None, {"tal"})

    assert "s.slug = ANY(%s)" in seen["sql"]
    assert seen["params"] == (["tal"],)
    assert "CURRENT_DATE" not in seen["sql"] and "publish_date >" not in seen["sql"]


def test_feed_check_fails_loudly_on_an_unknown_show_slug(monkeypatch) -> None:
    """A typo'd or renamed slug checks nothing. Reporting "Every show's import is caught
    up" for it is a green nobody earned — and pipeline.yml runs this --strict to prove
    the run it just did actually discovered something."""
    result = _feed_check(monkeypatch, rows=[], today=date(2026, 9, 1), slugs=["taal"])

    assert result.status == "fail"
    assert any("unknown show slug(s) taal" in d for d in result.details), result.details

    # `--shows " "` parses to an empty scope — the same silent green by another route.
    empty = _feed_check(monkeypatch, rows=[], today=date(2026, 9, 1), slugs=[])
    assert empty.status == "fail"
    assert any("the scope given was empty" in d for d in empty.details), empty.details


def test_feed_check_names_the_oldest_missing_episodes_first(monkeypatch) -> None:
    """The message says "oldest missing <date>" and then lists episodes; the list has to
    start with that same episode, or the two halves of one sentence disagree."""
    feed = [
        FeedEpisode(f"taddy:{n}", date(2026, 8, day), f"Ep {n}")
        for n, day in [(1, 28), (2, 26), (3, 24), (4, 22)]
    ]
    result = _feed_check(
        monkeypatch,
        rows=[_held_row("pchh", "taddy:held", "Held", date(2026, 8, 29))],
        feed_episodes={"pchh": feed},
        today=date(2026, 9, 1),
        slugs=["pchh"],
    )

    detail = next(d for d in result.details if d.startswith("pchh: BEHIND"))
    assert "oldest missing 2026-08-22" in detail
    assert "missing: 2026-08-22 'Ep 4'; 2026-08-24 'Ep 3'; 2026-08-26 'Ep 2'; +1 more" in detail

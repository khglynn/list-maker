#!/usr/bin/env python3
"""Read-only data quality checks for the podcast Neon database.

This is deliberately separate from unit tests:
- tests protect the code from drifting
- health checks tell us whether the live data is already clean
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

# Allow running as `python pipeline/data_health.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import get_db_connection, load_environment, post_slack
from feed_check import FeedEpisode, feed_recent_dates, feed_recent_episodes
from show_config import (
    BLOG_NOTION_SHOWS,
    SHOWS,
    TRANSCRIPT_NOTION_SHOWS,
    curated_show_slugs,
    ended_show_slugs,
)


@dataclass
class CheckResult:
    name: str
    status: str  # pass, warn, fail
    summary: str
    details: list[str]


TRANSCRIPT_POLICIES: dict[str, dict[str, Any]] = {
    # These shows are transcript-first. Missing transcripts are real gaps.
    "ai-daily-brief": {"mode": "complete", "max_latest_lag_days": 0},
    "pchh": {"mode": "complete", "max_latest_lag_days": 0},
    # Curated sources: save_item inserts episode + full text atomically, so a
    # missing transcript means a broken ingest, not a transcription lag.
    "openai-blog": {"mode": "complete", "max_latest_lag_days": 0},
    "anthropic-blog": {"mode": "complete", "max_latest_lag_days": 0},
    "saved-articles": {"mode": "complete", "max_latest_lag_days": 0},
    "agentic-research": {"mode": "complete", "max_latest_lag_days": 0},
    # Show-notes-based: extracts from the Megaphone RSS description, NOT transcripts —
    # 0 transcripts is correct, not a gap. (Without this, "cannot compare dates" fails daily.)
    "culture-gabfest": {"mode": "none"},
    # These shows started as music/recommendation pipelines. Historic transcript
    # coverage is allowed to be partial, but the latest transcript should keep up.
    # Music shows match from website song data, so a transcript lag is a WARN, not a FAIL.
    "sop": {"mode": "latest", "max_latest_lag_days": 14, "min_coverage": 0.50},
    "tal": {"mode": "latest", "max_latest_lag_days": 21, "min_coverage": 0.01},
}

# Max days a show may go without a NEW episode before it's flagged stale. Catches
# a feed/import that silently stopped — e.g. AI Daily's 17-day drift in May 2026.
STALENESS_MAX_DAYS: dict[str, int] = {
    "ai-daily-brief": 3,    # daily
    "pchh": 7,              # ~daily weekdays
    "hard-fork": 10,        # ~weekly (once onboarded)
    "culture-gabfest": 10,  # ~weekly (once onboarded)
    "sop": 14,
    "tal": 21,
}
DEFAULT_STALENESS_MAX_DAYS = 14

# Notion syncs run daily; anything unsynced for longer than this means the sync
# itself silently stopped (the failure class behind the June 2026 Transcripts-DB
# freeze: pipeline green every day, Notion frozen at June 6).
NOTION_SYNC_MAX_LAG_DAYS = 2

# How long an episode may wait for its self-heal re-extraction before the queue counts
# as stuck rather than merely pending. The pipeline runs daily and heals up to 3 episodes
# a run, so anything older than this is the heal failing, not the heal working through a
# backlog. Kept above the daily cadence so one skipped run is not an alert.
SELF_HEAL_MAX_PENDING_DAYS = 3

OPTIONAL_NULL_NOTES = {
    "episodes.raw_content": "Stored only for AI Daily/PCHH Taddy imports; null for SOP/TAL is expected.",
    "episodes.has_songs_discussed": "Legacy music-triage field; null means not evaluated or not applicable.",
    "episodes.episode_number": "Provider-specific; AI Daily does not provide it, older rows may not have it.",
    "episodes.audio_url": "Expected on recent Taddy imports; old website-scraped rows may not have it.",
    "episodes.image_url": "Helpful but not source-of-truth; missing art is not a data integrity failure.",
}


DEFAULT_FEED_GRACE_DAYS = 2  # mirrors ShowConfig.feed_grace_days for callers holding no cfg


def _today() -> date:
    return datetime.now(timezone.utc).date()


def split_missing_feed_dates(
    feed: Iterable[date], db_latest: date | None, grace_days: int, today: date | None = None
) -> tuple[list[date], list[date]]:
    """Split the feed dates we don't hold yet into (overdue, pending).

    A feed date is MISSING when it is newer than the newest episode we have. It is only
    OVERDUE — a real gap, worth waking someone — once it is older than the show's grace
    window (ShowConfig.feed_grace_days). Inside the window it is merely PENDING: the
    episode is out, but the scheduled import that normally fetches it hasn't had its
    turn.

    Still live, still used, and NOT the whole story since 2026-09-03: this is the
    comparison for shows with no comparable episode identity (SOP), and it is what
    pulse_report still runs for every show. split_missing_feed_episodes is the
    identity-based twin the daily check uses everywhere else. The two therefore CAN now
    disagree on the identity shows — the pulse can report a BEHIND the daily check
    doesn't, which is the TAL false positive surviving in the biweekly digest. Teaching
    the pulse the identity comparison is the follow-up that closes it.
    """
    today = today or _today()
    cutoff = today - timedelta(days=grace_days)
    missing = [d for d in feed if db_latest is None or d > db_latest]
    return [d for d in missing if d <= cutoff], [d for d in missing if d > cutoff]


@dataclass
class HeldEpisodes:
    """What we hold for one show, in the two forms the importer itself uses for dedup.

    `urls` is `episodes.url` — the identity. `title_dates` is (lower(title),
    publish_date), the fallback the Taddy importer tries FIRST when deciding whether an
    episode is already present (import_transcripts.upsert_episode). Keeping both here is
    what makes "do we hold this feed episode?" answer the same question the importer
    would: if the importer would consider it present, no future import can ever create
    it, so calling it missing would be an alarm nothing can clear.
    """

    urls: set[str]
    title_dates: set[tuple[str, date]]
    latest: date | None = None


def _feed_episode_is_held(episode: FeedEpisode, held: HeldEpisodes) -> bool:
    """Do we already hold this feed episode?

    Identity first: `episodes.url` is UNIQUE and stable across a re-date (both upsert
    paths are ON CONFLICT (url) with COALESCE on publish_date), so a Taddy re-dating
    cannot make a held episode look missing.

    Then title+date, for rows holding the same episode under an older url scheme.
    Measured against live Neon 2026-09-03: 3 of TAL's 15 recent feed episodes are exactly
    this (old bonus episodes Taddy still returns in its "latest 15", stored under
    thisamericanlife.org urls), and without this fallback TAL reports BEHIND 3 forever.

    This fallback is PERMANENT, not transitional. upsert_episode matches
    show_id+lower(title)+publish_date FIRST and its UPDATE branch never writes `url`, so
    those three rows can never acquire a Taddy url no matter how many times the import
    runs — the importer is structurally incapable of migrating them. Which also means
    this check stays green on those episodes only as long as Taddy doesn't edit one of
    their titles; if that ever happens the fix is to repair the row's url, not to loosen
    this.
    """
    if episode.identity in held.urls:
        return True
    # An untitled feed row is stored by the importer as "Untitled Episode"
    # (import_transcripts.upsert_episode), so normalize to the same default rather than
    # refusing to match: refusing would report an episode we hold, under a title the
    # importer itself chose, as missing forever.
    title = episode.title.strip().lower() or "untitled episode"
    return (title, episode.publish_date) in held.title_dates


def split_missing_feed_episodes(
    feed: Iterable[FeedEpisode], held: HeldEpisodes, grace_days: int, today: date | None = None
) -> tuple[list[FeedEpisode], list[FeedEpisode]]:
    """Identity-based twin of split_missing_feed_dates: (overdue, pending).

    A feed episode is MISSING when we do not hold it — a set question, not a date one.
    That is the whole fix: split_missing_feed_dates asks "is this newer than our newest?",
    so an episode we never imported sitting BEHIND our newest was structurally invisible
    no matter how missing it was, and a re-dated episode we do hold read as brand new.
    Here a hole in the middle of a series is just another set-difference entry.

    Grading is unchanged — each missing episode's OWN date against the show's
    feed_grace_days cutoff, so "published but not imported yet" is still pending, not an
    alarm. The grace-window contract is identical; only membership changed.
    """
    today = today or _today()
    cutoff = today - timedelta(days=grace_days)
    missing = [ep for ep in feed if not _feed_episode_is_held(ep, held)]
    return (
        [ep for ep in missing if ep.publish_date <= cutoff],
        [ep for ep in missing if ep.publish_date > cutoff],
    )


def _rows(conn, sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params or ()))
        return [dict(row) for row in cur.fetchall()]


def _one(conn, sql: str, params: Iterable[Any] | None = None) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params or ()))
        row = cur.fetchone()
        return dict(row) if row else {}


def _status_from_count(count: int, *, warn_only: bool = False) -> str:
    if count == 0:
        return "pass"
    return "warn" if warn_only else "fail"


def check_expected_shows(conn) -> CheckResult:
    db_rows = _rows(conn, "SELECT id, slug, name FROM shows ORDER BY id;")
    db_by_slug = {row["slug"]: row for row in db_rows}

    details: list[str] = []
    for slug, cfg in SHOWS.items():
        row = db_by_slug.get(slug)
        if not row:
            details.append(f"missing show row for configured slug `{slug}`")
            continue
        if int(row["id"]) != int(cfg.show_id):
            details.append(
                f"`{slug}` show_id mismatch: code={cfg.show_id}, neon={row['id']}"
            )

    extra = sorted(set(db_by_slug) - set(SHOWS))
    for slug in extra:
        details.append(f"Neon has unconfigured show slug `{slug}`")

    status = _status_from_count(len(details))
    summary = "Configured shows match Neon by slug and id." if status == "pass" else (
        f"{len(details)} show config mismatch(es) found."
    )
    return CheckResult("show_config_matches_neon", status, summary, details)


def check_episode_identity(conn) -> CheckResult:
    rows = _rows(
        conn,
        """
        SELECT
          COALESCE(s.slug, 'unknown') AS slug,
          COUNT(*) AS episodes,
          COUNT(*) FILTER (WHERE e.show_id IS NULL) AS missing_show_id,
          COUNT(*) FILTER (WHERE e.title IS NULL OR BTRIM(e.title) = '') AS missing_title,
          COUNT(*) FILTER (WHERE e.url IS NULL OR BTRIM(e.url) = '') AS missing_url,
          COUNT(*) FILTER (WHERE e.publish_date IS NULL) AS missing_publish_date
        FROM episodes e
        LEFT JOIN shows s ON s.id = e.show_id
        GROUP BY COALESCE(s.slug, 'unknown')
        ORDER BY COALESCE(s.slug, 'unknown');
        """,
    )
    details: list[str] = []
    issue_count = 0
    for row in rows:
        problems = []
        for key in ("missing_show_id", "missing_title", "missing_url", "missing_publish_date"):
            value = int(row[key] or 0)
            issue_count += value
            if value:
                problems.append(f"{key}={value}")
        if problems:
            details.append(f"{row['slug']}: " + ", ".join(problems))

    if issue_count:
        samples = _rows(
            conn,
            """
            SELECT e.id, COALESCE(s.slug, 'unknown') AS slug, e.title, e.url, e.publish_date
            FROM episodes e
            LEFT JOIN shows s ON s.id = e.show_id
            WHERE e.show_id IS NULL
               OR e.title IS NULL OR BTRIM(e.title) = ''
               OR e.url IS NULL OR BTRIM(e.url) = ''
               OR e.publish_date IS NULL
            ORDER BY COALESCE(s.slug, 'unknown'), e.id
            LIMIT 10;
            """,
        )
        details.append("sample bad rows: " + json.dumps(samples, default=str))

    status = _status_from_count(issue_count)
    summary = "Every episode has show, title, URL, and publish date." if status == "pass" else (
        f"{issue_count} required episode identity value(s) are missing."
    )
    return CheckResult("episode_identity_required_fields", status, summary, details)


def check_duplicate_episodes(conn) -> CheckResult:
    rows = _rows(
        conn,
        """
        SELECT
          s.slug,
          LOWER(BTRIM(e.title)) AS title_key,
          e.publish_date,
          COUNT(*) AS duplicate_count,
          ARRAY_AGG(e.id ORDER BY e.id) AS episode_ids
        FROM episodes e
        JOIN shows s ON s.id = e.show_id
        WHERE e.title IS NOT NULL
          AND BTRIM(e.title) <> ''
          AND e.publish_date IS NOT NULL
        GROUP BY s.slug, LOWER(BTRIM(e.title)), e.publish_date
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC, s.slug, publish_date DESC
        LIMIT 25;
        """,
    )
    details = [
        f"{row['slug']} {row['publish_date']} `{row['title_key']}` ids={row['episode_ids']}"
        for row in rows
    ]
    status = _status_from_count(len(rows))
    summary = "No duplicate show/title/date episode rows found." if status == "pass" else (
        f"{len(rows)} duplicate episode key(s) found."
    )
    return CheckResult("duplicate_episodes_by_show_title_date", status, summary, details)


def _date_lag_days(latest_episode: Any, latest_transcript: Any) -> int | None:
    if not latest_episode or not latest_transcript:
        return None
    return (latest_episode - latest_transcript).days


def check_transcript_coverage(conn) -> CheckResult:
    rows = _rows(
        conn,
        """
        SELECT
          s.slug,
          COUNT(e.id) AS episodes,
          COUNT(et.id) AS transcripts,
          COUNT(e.id) FILTER (WHERE et.id IS NULL) AS missing_transcripts,
          MAX(e.publish_date)::date AS latest_episode,
          MAX(CASE WHEN et.id IS NOT NULL THEN e.publish_date END)::date AS latest_transcript
        FROM shows s
        LEFT JOIN episodes e ON e.show_id = s.id
        LEFT JOIN episode_transcripts et ON et.episode_id = e.id
        GROUP BY s.slug
        ORDER BY s.slug;
        """,
    )
    # Transcript-first shows get their missing episodes by date, so a transcript that is
    # simply not out yet (Taddy publishes about a day after the episode; p90 = 1 day,
    # measured 2026-09-01) isn't a failure. Before this, every daily run that saw
    # yesterday's AI Daily episode before its transcript reported "1 episode(s) missing
    # transcripts" and "lags by 1 days" as a FAIL (08-07, 08-24, ...).
    complete_slugs = [s for s, p in TRANSCRIPT_POLICIES.items() if p.get("mode") == "complete"]
    missing_dates: dict[str, list[date]] = {}
    for r in _rows(
        conn,
        """
        SELECT s.slug, e.publish_date::date AS publish_date
        FROM episodes e
        JOIN shows s ON s.id = e.show_id
        LEFT JOIN episode_transcripts et ON et.episode_id = e.id
        WHERE et.id IS NULL AND s.slug = ANY(%s)
        """,
        [complete_slugs],
    ):
        missing_dates.setdefault(r["slug"], []).append(r["publish_date"])
    today = _today()

    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []

    for row in rows:
        slug = row["slug"]
        episodes = int(row["episodes"] or 0)
        transcripts = int(row["transcripts"] or 0)
        missing = int(row["missing_transcripts"] or 0)
        coverage = (transcripts / episodes) if episodes else 1.0
        lag_days = _date_lag_days(row["latest_episode"], row["latest_transcript"])
        policy = TRANSCRIPT_POLICIES.get(slug, {"mode": "latest", "max_latest_lag_days": 30})
        cfg = SHOWS.get(slug)
        grace = getattr(cfg, "feed_grace_days", DEFAULT_FEED_GRACE_DAYS)

        if policy["mode"] == "none":
            details.append(f"{slug}: show-notes based — no transcripts expected (skipped)")
            continue

        if episodes == 0:
            # New/curated show before its first ingest: nothing to measure yet.
            # Without this, complete-mode shows fail "cannot compare dates" daily.
            details.append(f"{slug}: no episodes yet (skipped)")
            continue

        detail = (
            f"{slug}: {transcripts}/{episodes} transcripts "
            f"({coverage:.1%}), latest_episode={row['latest_episode']}, "
            f"latest_transcript={row['latest_transcript']}"
        )
        details.append(detail)

        # Transcript-first shows ('complete'): a missing/lagging transcript is a real
        # failure. Music shows ('latest'): transcripts aren't load-bearing (matched from
        # website song data), so the same gap is only a warning — never page on it.
        is_strict = policy["mode"] == "complete"
        bucket = failures if is_strict else warnings

        if is_strict and missing:
            cutoff = today - timedelta(days=grace)
            overdue = [d for d in missing_dates.get(slug, []) if d and d < cutoff]
            pending = missing - len(overdue)
            if overdue:
                failures.append(
                    f"{slug}: {len(overdue)} episode(s) missing transcripts past the "
                    f"{grace}-day window (oldest {min(overdue)})"
                    + (f"; {pending} newer still pending" if pending else "")
                )
            else:
                details.append(
                    f"{slug}: {missing} recent episode(s) awaiting transcripts inside the "
                    f"{grace}-day window"
                )

        # For transcript-first shows the lag tolerance is the same grace window; the
        # policy's own number still applies where it is larger (music shows: 30).
        max_lag = max(int(policy.get("max_latest_lag_days", 30)), grace if is_strict else 0)
        if lag_days is None:
            bucket.append(f"{slug}: cannot compare latest episode/transcript dates")
        elif lag_days > max_lag:
            bucket.append(f"{slug}: latest transcript lags latest episode by {lag_days} days")

        min_coverage = policy.get("min_coverage")
        if min_coverage is not None and coverage < float(min_coverage):
            warnings.append(
                f"{slug}: transcript coverage {coverage:.1%} is below policy {float(min_coverage):.1%}"
            )

    status = "fail" if failures else ("warn" if warnings else "pass")
    summary = "Transcript coverage matches each show's current policy." if status == "pass" else (
        f"{len(failures)} failure(s), {len(warnings)} warning(s) in transcript coverage."
    )
    return CheckResult("transcript_coverage_by_show", status, summary, failures + warnings + details)


def check_episode_freshness(conn) -> CheckResult:
    """Flag shows whose newest episode is older than their staleness threshold —
    catches a feed/import that silently stopped (e.g. AI Daily's 17-day drift).
    (Sending this to Slack lives in the scheduled workflow; it needs the
    SLACK_WEBHOOK_URL secret — deferred. This check is the detector.)
    """
    rows = _rows(
        conn,
        """
        SELECT
          s.slug,
          MAX(e.publish_date)::date AS latest_episode,
          (CURRENT_DATE - MAX(e.publish_date)::date) AS days_since
        FROM shows s
        JOIN episodes e ON e.show_id = s.id
        GROUP BY s.slug
        ORDER BY s.slug;
        """,
    )
    failures: list[str] = []
    details: list[str] = []
    curated = curated_show_slugs()
    ended = ended_show_slugs()
    for row in rows:
        slug = row["slug"]
        if slug in curated:
            # Curated sources have no publishing cadence — "stale" just means Kevin
            # hasn't pulled anything lately, which is his call, not a pipeline failure.
            details.append(f"{slug}: curated source — staleness not applicable (skipped)")
            continue
        if slug in ended:
            # A concluded show is permanently stale. Failing on it every run forever
            # is the fastest way to teach ourselves to ignore this alert.
            details.append(
                f"{slug}: show ended {SHOWS[slug].ended_on} — staleness not applicable (skipped)"
            )
            continue
        days_since = row["days_since"]
        threshold = STALENESS_MAX_DAYS.get(slug, DEFAULT_STALENESS_MAX_DAYS)
        details.append(
            f"{slug}: latest_episode={row['latest_episode']} "
            f"({days_since}d ago, threshold {threshold}d)"
        )
        if days_since is not None and days_since > threshold:
            failures.append(
                f"{slug}: no new episode in {days_since} days (threshold {threshold}d)"
            )
    status = "fail" if failures else "pass"
    summary = (
        "Every show has a recent episode within its freshness threshold."
        if status == "pass"
        else f"{len(failures)} show(s) stale (no recent episodes)."
    )
    return CheckResult("episode_freshness_by_show", status, summary, failures + details)


def check_notion_sync_freshness(conn) -> CheckResult:
    """Notion is a DESTINATION — a green pipeline run proves data reached Neon, not Notion.

    Two drift detectors, both age-gated to NOTION_SYNC_MAX_LAG_DAYS so same-day work
    (synced later in the same run) never false-positives:
    - transcripts: rows for TRANSCRIPT_NOTION_SHOWS still missing a Notion page id
      (empty transcripts excluded — they're never marked synced by design and belong
      to check_transcript_coverage)
    - entities: rows synced once but whose updates stopped propagating
    Lingering 'failed' entity syncs are a WARN — acute failures already Slack via
    sync_notion's >10%-per-run alert; this is the slow-leak view.
    """
    transcript_rows = _rows(
        conn,
        """
        SELECT s.slug, COUNT(*) AS unsynced, MIN(et.created_at)::date AS oldest
        FROM episode_transcripts et
        JOIN episodes ep ON ep.id = et.episode_id
        JOIN shows s ON s.id = ep.show_id
        WHERE s.slug = ANY(%s)
          AND et.transcript_text IS NOT NULL AND BTRIM(et.transcript_text) <> ''
          AND et.notion_transcript_page_id IS NULL
          AND et.created_at < now() - make_interval(days => %s)
        GROUP BY s.slug
        ORDER BY s.slug;
        """,
        # Watch BOTH full-text mirrors: the Transcripts DB and the Blog Posts DB.
        [list(TRANSCRIPT_NOTION_SHOWS + BLOG_NOTION_SHOWS), NOTION_SYNC_MAX_LAG_DAYS],
    )
    stale_entities = int(
        _one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM ai_entities
            WHERE notion_page_id IS NOT NULL
              AND notion_synced_at < updated_at - make_interval(days => %s);
            """,
            [NOTION_SYNC_MAX_LAG_DAYS],
        ).get("count")
        or 0
    )
    failed_entities = int(
        _one(
            conn,
            "SELECT COUNT(*) AS count FROM ai_entities WHERE notion_sync_status = 'failed';",
        ).get("count")
        or 0
    )

    failures: list[str] = []
    warnings: list[str] = []
    for row in transcript_rows:
        failures.append(
            f"{row['slug']}: {row['unsynced']} transcript(s) unsynced to Notion "
            f"for >{NOTION_SYNC_MAX_LAG_DAYS}d (oldest {row['oldest']})"
        )
    if stale_entities:
        failures.append(
            f"{stale_entities} entity page(s) have Neon updates >{NOTION_SYNC_MAX_LAG_DAYS}d "
            "old that never reached Notion"
        )
    if failed_entities:
        warnings.append(f"{failed_entities} entity(ies) lingering in notion_sync_status='failed'")

    status = "fail" if failures else ("warn" if warnings else "pass")
    summary = (
        "Notion mirrors (transcripts + entities) are keeping up with Neon."
        if status == "pass"
        else f"{len(failures)} Notion sync drift failure(s), {len(warnings)} warning(s)."
    )
    return CheckResult("notion_sync_freshness", status, summary, failures + warnings)


def _held_episodes_by_show(conn, slugs: set[str] | None = None) -> dict[str, HeldEpisodes]:
    """Every episode we hold, per show, in the forms the feed check compares against.

    One round trip, and deliberately UNBOUNDED BY DATE (~4,300 rows fleet-wide on
    2026-09-03). A rolling date window is the obvious optimisation and it is a trap:
    Culture Gabfest ended 2026-07-01 and its RSS still serves 15 pre-July episodes, so
    any window that eventually excludes them would report the whole show as missing.
    Bound it by SHOW instead — which is what `slugs` does, so the music workflow's
    per-show run reads one show's rows rather than the fleet's.

    LEFT JOIN, and no `url IS NOT NULL` filter, so `latest` stays exactly the
    MAX(publish_date) the old query returned (a show with no episodes still gets an
    entry, and a row with a NULL url still counts toward the date shown in Slack).
    """
    where, params = "", ()
    if slugs:
        where, params = "WHERE s.slug = ANY(%s)", (sorted(slugs),)
    rows = _rows(
        conn,
        f"""
        SELECT s.slug, e.url, e.title, e.publish_date::date AS publish_date
        FROM shows s LEFT JOIN episodes e ON e.show_id = s.id
        {where}
        """,
        params,
    )
    held: dict[str, HeldEpisodes] = {}
    for row in rows:
        entry = held.setdefault(row["slug"], HeldEpisodes(urls=set(), title_dates=set()))
        url, title, published = row.get("url"), row.get("title"), row.get("publish_date")
        if url:
            entry.urls.add(url)
        if title and published:
            entry.title_dates.add((title.strip().lower(), published))
        if published and (entry.latest is None or published > entry.latest):
            entry.latest = published
    return held


def check_import_caught_up(conn, slugs: Iterable[str] | None = None) -> CheckResult:
    """SECOND-SOURCE freshness: is our import behind each show's REAL feed?

    episode_freshness_by_show only knows "days since OUR latest", which can't tell a show
    on break from an import that silently broke. This asks each feed (Taddy / Megaphone
    RSS via feed_check) which episodes it has, and compares that to what we hold. A feed
    we can't reach is reported, not failed (don't cry wolf on a flaky feed); a confirmed
    BEHIND is a real, actionable failure.

    Two comparisons, chosen per show by ShowConfig.episode_identity:
      - BY IDENTITY where the feed's episode ids are the ids we store (every show but
        SOP). A set difference, so it sees a hole in the MIDDLE of a series — which
        MAX(publish_date) never could — and a re-dated episode we hold is a non-event,
        because identity doesn't move when a date does (the TAL false BEHIND, DEVLOG
        2026-09-01).
      - BY DATE for SOP, whose rows come from its website scraper while Taddy is only its
        second source, so there is no id to compare. Unchanged from before.

    `slugs` narrows the check to specific shows. The music workflow passes the one show
    it just ran, so a single Taddy call proves that run actually discovered something,
    without paying for a feed call per show on every music run.

    A missing episode only counts as BEHIND once it is older than the show's
    feed_grace_days (see show_config) — newer ones are reported as pending. Without
    that, this check fired on nearly every August-2026 run for SOP, whose Tuesday
    episode simply hadn't met its Wednesday import yet.
    """
    wanted = set(slugs) if slugs is not None else None
    held_by_show = _held_episodes_by_show(conn, wanted)
    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    # A slug that isn't a show checks nothing and would otherwise report "Every show's
    # import is caught up" with an empty detail list — a green nobody earned, from a
    # typo or a renamed show. pipeline.yml runs this --strict to prove the run it just
    # did discovered something, so a silent pass there is the exact failure this check
    # exists to prevent.
    # An EMPTY scope is the same silent green by another route (`--shows " "` parses to
    # nothing), so it is named too rather than reported as a clean run of zero shows.
    if wanted is not None and not wanted:
        failures.append("no show slugs to check — the scope given was empty")
    unknown = sorted(wanted - set(SHOWS)) if wanted else []
    if unknown:
        failures.append(
            f"unknown show slug(s) {', '.join(unknown)} — nothing was checked for them "
            f"(known: {', '.join(sorted(SHOWS))})"
        )
    curated = curated_show_slugs()
    for slug, cfg in SHOWS.items():
        if wanted is not None and slug not in wanted:
            continue
        if slug in curated:
            # No feed to verify against — skipping avoids a permanent UNVERIFIED warn.
            details.append(f"{slug}: curated source — no feed second-source (skipped)")
            continue
        held = held_by_show.get(slug) or HeldEpisodes(urls=set(), title_dates=set())
        latest = held.latest
        # UNVERIFIED = None from either reader: couldn't get a trustworthy answer
        # (unreachable / error / empty). A persistent one means the second source itself
        # is broken — surface it as a WARN so it can't hide as a silent pass, without
        # crying wolf on a flaky run.
        if cfg.episode_identity:
            feed_episodes = feed_recent_episodes(cfg)
            if not feed_episodes:
                warnings.append(f"{slug}: feed UNVERIFIED — second source unreachable")
                continue
            # Some rows in this window carry a legacy url scheme and match only via the
            # title+date fallback (3 of TAL's 15 today — see _feed_episode_is_held).
            # Raising `limit` widens the window into older episodes, where more rows are
            # legacy-keyed and titles have had longer to be edited: a bigger window
            # bought with a softer identity. Deliberate or not at all.
            overdue_eps, pending_eps = split_missing_feed_episodes(
                feed_episodes, held, cfg.feed_grace_days
            )
            overdue = [ep.publish_date for ep in overdue_eps]
            pending = [ep.publish_date for ep in pending_eps]
            feed_latest = feed_episodes[0].publish_date
            everything_missing = len(overdue) + len(pending) == len(feed_episodes)
            # Name the episodes, not just a count: identity comparison knows exactly
            # WHICH ones are missing, and "BEHIND 3" alone leaves the reader to go find
            # out. Oldest first, so the list starts with the same episode the message's
            # "oldest missing" names; capped at 3 so a url-scheme change can't dump 15
            # titles into Slack.
            oldest_first = sorted(overdue_eps, key=lambda ep: ep.publish_date)
            named_missing = "; ".join(
                f"{ep.publish_date} {ep.title[:60]!r}" for ep in oldest_first[:3]
            )
            if len(oldest_first) > 3:
                named_missing += f"; +{len(oldest_first) - 3} more"
        else:
            # No comparable identity (SOP: its scraper writes the urls, Taddy is only the
            # second source). Dates are all we can honestly compare — see
            # ShowConfig.episode_identity.
            feed_dates = feed_recent_dates(cfg)
            if not feed_dates:
                warnings.append(f"{slug}: feed UNVERIFIED — second source unreachable")
                continue
            overdue, pending = split_missing_feed_dates(feed_dates, latest, cfg.feed_grace_days)
            feed_latest = feed_dates[0]
            everything_missing = False  # a date compare cannot tell this apart
            named_missing = ""  # the feed's dates are all we have; no titles to name
        if overdue:
            # "Every one of them" is also the signature of an importer that changed its
            # url scheme, which would otherwise read as a catastrophic outage. Say both,
            # so the alert names what to check instead of just a number.
            scheme_hint = (
                " — EVERY recent feed episode is missing; check the importer, and whether"
                " the url scheme it writes still matches ShowConfig.episode_identity"
                if everything_missing
                else ""
            )
            failures.append(
                f"{slug}: BEHIND {len(overdue)} — feed at {feed_latest}, we have {latest} "
                f"(oldest missing {min(overdue)}, past the {cfg.feed_grace_days}-day import "
                f"window)"
                + (f" — missing: {named_missing}" if named_missing else "")
                + scheme_hint
            )
        elif pending:
            details.append(
                f"{slug}: caught up ({latest}) — {len(pending)} feed episode(s) pending "
                f"inside the {cfg.feed_grace_days}-day import window (feed at {feed_latest})"
            )
        else:
            details.append(f"{slug}: caught up ({latest})")
    if failures:
        status, summary = "fail", f"{len(failures)} show(s) behind their feed (missing episodes)."
    elif warnings:
        status, summary = "warn", f"{len(warnings)} show(s) could not be verified against their feed."
    else:
        status, summary = "pass", "Every show's import is caught up to its feed."
    return CheckResult("import_caught_up_to_feed", status, summary, failures + warnings + details)


def check_ai_daily_extraction(conn) -> CheckResult:
    row = _one(
        conn,
        """
        WITH ai_show AS (
          SELECT id FROM shows WHERE slug = 'ai-daily-brief'
        )
        SELECT
          COUNT(*) FILTER (
            WHERE NOT EXISTS (
              SELECT 1 FROM ai_mentions m WHERE m.episode_id = ep.id
            )
          ) AS transcripted_without_mentions
        FROM episodes ep
        JOIN ai_show s ON s.id = ep.show_id
        JOIN episode_transcripts et ON et.episode_id = ep.id
        -- A transcript that landed within the last few hours is simply waiting for its
        -- extraction (the daily run imports, then extracts ~3 minutes later). Any reader
        -- in that gap — the pulse did on 2026-09-01 — would otherwise report a phantom
        -- integrity issue. 6h is far past that gap and far short of the 1–2 day real
        -- holes this check caught on 2026-08-01.
        WHERE et.created_at < NOW() - INTERVAL '6 hours'
          -- An episode the extractor ran on and kept nothing for is a declared empty
          -- result (ai_runs.status = 'completed_empty', reasons in parameters), not a
          -- missing extraction. Counting it here would pin this check red forever.
          AND NOT EXISTS (
            SELECT 1 FROM ai_runs r
            WHERE r.status = 'completed_empty'
              AND r.parameters->'episodes' @> to_jsonb(ep.id)
          );
        """,
    )
    missing_mentions = int(row.get("transcripted_without_mentions") or 0)
    declared_empty_runs = int(
        _one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM ai_runs r
            WHERE r.status = 'completed_empty'
              AND r.created_at >= NOW() - INTERVAL '30 days';
            """,
        ).get("count")
        or 0
    )
    # Orphans only: transcript_id points at a transcript row that no longer exists.
    # The other shape of broken provenance — NULL transcript_id on an episode that HAS a
    # transcript — is the transcript race, and check_transcript_race_selfheal owns it. It
    # used to be counted here too, which meant one problem raised two alerts and neither
    # said whether the pipeline was already fixing it.
    orphan_transcript_mentions = int(
        _one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM ai_mentions m
            WHERE m.transcript_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM episode_transcripts et WHERE et.id = m.transcript_id);
            """,
        ).get("count")
        or 0
    )
    zero_mention_runs = int(
        _one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM (
              SELECT r.id
              FROM ai_runs r
              LEFT JOIN ai_mentions m ON m.run_id = r.id
              WHERE r.status = 'completed'
              GROUP BY r.id
              HAVING COUNT(m.id) = 0
            ) x;
            """,
        ).get("count")
        or 0
    )

    issue_count = missing_mentions + orphan_transcript_mentions + zero_mention_runs
    details = []
    if missing_mentions:
        details.append(
            f"AI Daily episodes transcripted >6h ago without mentions: {missing_mentions}"
        )
    if orphan_transcript_mentions:
        details.append(
            f"AI mentions pointing at a deleted transcript: {orphan_transcript_mentions}"
        )
    if zero_mention_runs:
        details.append(f"completed AI runs with zero mentions: {zero_mention_runs}")
    if declared_empty_runs:
        # Informational — a declared empty result is an answer, not an issue. Listed so
        # a sudden run of them (a broken prompt, a filter that eats everything) is
        # visible without turning the check red.
        details.append(
            f"batches declared empty after extraction in the last 30 days: {declared_empty_runs} "
            f"(ai_runs.status = 'completed_empty'; reasons in parameters.dropped)"
        )

    status = _status_from_count(issue_count)
    summary = "AI Daily transcripts, mentions, transcripts, and runs line up." if status == "pass" else (
        f"{issue_count} AI extraction integrity issue(s) found."
    )
    return CheckResult("ai_daily_extraction_integrity", status, summary, details)


def check_transcript_race_selfheal(conn) -> CheckResult:
    """Is the transcript-race self-heal keeping up?

    A pending episode is one whose mentions carry no transcript_id even though the episode
    has a transcript — extracted from show notes before the real text arrived. The pipeline
    re-extracts these on its own (run_new_episodes.step_self_heal_transcript_race), bounded
    per run, so a small pending count right after a transcript lands is the system working,
    not a fault.

    What deserves an alert is a queue that is not draining: an episode still pending days
    after its transcript arrived means the heal is failing or never running, and the check
    that only counted rows could never tell those apart.
    """
    rows = _rows(
        conn,
        """
        SELECT s.slug,
               m.episode_id,
               COUNT(*) AS mentions,
               MAX(et.created_at)::date AS transcript_arrived,
               (CURRENT_DATE - MAX(et.created_at)::date) AS days_pending
        FROM ai_mentions m
        JOIN episodes ep ON ep.id = m.episode_id
        JOIN shows s ON s.id = ep.show_id
        JOIN episode_transcripts et ON et.episode_id = m.episode_id
        WHERE m.transcript_id IS NULL
        GROUP BY s.slug, m.episode_id
        ORDER BY days_pending DESC, m.episode_id;
        """,
    )
    if not rows:
        return CheckResult(
            "transcript_race_selfheal",
            "pass",
            "No episodes are waiting to be re-extracted from a late transcript.",
            [],
        )

    stuck = [r for r in rows if int(r["days_pending"] or 0) > SELF_HEAL_MAX_PENDING_DAYS]
    details = [
        f"{r['slug']} ep {r['episode_id']}: {r['mentions']} show-notes mention(s), "
        f"transcript arrived {r['transcript_arrived']} ({r['days_pending']}d pending)"
        for r in rows
    ]
    if stuck:
        status = "fail"
        summary = (
            f"{len(stuck)} episode(s) still not re-extracted more than "
            f"{SELF_HEAL_MAX_PENDING_DAYS} days after their transcript arrived — "
            "the self-heal is not draining."
        )
    else:
        status = "warn"
        summary = (
            f"{len(rows)} episode(s) queued for self-heal re-extraction; "
            "the next pipeline run should clear them."
        )
    return CheckResult("transcript_race_selfheal", status, summary, details)


def check_ai_mention_fields(conn) -> CheckResult:
    row = _one(
        conn,
        """
        SELECT
          COUNT(*) FILTER (WHERE mention_text IS NULL OR BTRIM(mention_text) = '') AS missing_mention_text,
          COUNT(*) FILTER (WHERE canonical_name IS NULL OR BTRIM(canonical_name) = '') AS missing_canonical_name,
          COUNT(*) FILTER (WHERE context_snippet IS NULL OR BTRIM(context_snippet) = '') AS missing_context,
          COUNT(*) FILTER (WHERE confidence IS NOT NULL AND (confidence < 0 OR confidence > 1)) AS bad_confidence,
          COUNT(*) FILTER (WHERE mention_count < 1) AS bad_mention_count
        FROM ai_mentions;
        """,
    )
    details = []
    issue_count = 0
    for key, value in row.items():
        count = int(value or 0)
        issue_count += count
        if count:
            details.append(f"{key}={count}")

    status = _status_from_count(issue_count)
    summary = "AI mention required fields and numeric ranges are clean." if status == "pass" else (
        f"{issue_count} AI mention field issue(s) found."
    )
    return CheckResult("ai_mention_required_fields", status, summary, details)


# Share of a show's recent mentions that may be sponsor reads before it looks wrong.
# Measured 2026-09-02 over the 30-day window this check actually scans, with the retag
# applied: AI Daily 5.8% (21/360), Hard Fork 0% (0/33), PCHH 0% (0/121). 30% is ~5x the
# observed ceiling, so it fires on a real regime change (a roster parse that starts
# matching prose, a cue phrase that starts matching editorial speech) and not on a week
# with more ads than usual.
SPONSOR_SHARE_WARN_THRESHOLD = 0.30
SPONSOR_SHARE_WINDOW_DAYS = 30
# Below this many mentions the ratio is noise — three mentions, two of them ads, is 67%
# and means nothing. Shows with a quiet month are reported as "too few to judge".
SPONSOR_SHARE_MIN_MENTIONS = 20
# Podcasts only, and only those still publishing. Curated sources (blogs, saved
# articles) carry no ad reads by construction, and an ended show has no recent window —
# both would report a permanent, meaningless 0%. Derived from show_config rather than
# listed here so onboarding a podcast does not silently leave it unwatched.
SPONSOR_SHARE_SHOWS = {
    slug
    for slug, cfg in SHOWS.items()
    if cfg.medium == "podcast"
    and cfg.extraction_type in {"entity_extraction", "media_extraction"}
    and slug not in ended_show_slugs()
}


def check_sponsor_share(conn) -> CheckResult:
    """What fraction of each tech show's recent mentions are tagged as sponsor reads.

    Two different failures show up here, which is why the thresholds are asymmetric.
    A HIGH share means the detector has started over-claiming — a roster entry parsed
    out of prose, or a cue phrase that matches ordinary speech — and that quietly caps
    real entities out of the rankings. A share of exactly 100% means the opposite kind
    of broken: nothing editorial got through at all, which is not a bad week, it is a
    pipeline that stopped working (the 2026-08-23 shape, where post-filters removed
    every candidate).

    Grace-window discipline, as elsewhere in this file: a show with too few recent
    mentions to form a meaningful ratio is reported, not judged. A quiet week must never
    turn into a red run.
    """
    rows = _rows(
        conn,
        """
        SELECT s.slug,
               COUNT(*) AS mentions,
               COUNT(*) FILTER (WHERE m.sponsor_source IS NOT NULL) AS ads
        FROM ai_mentions m
        JOIN episodes ep ON ep.id = m.episode_id
        JOIN shows s ON s.id = ep.show_id
        WHERE ep.publish_date >= CURRENT_DATE - make_interval(days => %s)
          AND s.slug = ANY(%s)
        GROUP BY s.slug
        ORDER BY s.slug;
        """,
        (SPONSOR_SHARE_WINDOW_DAYS, sorted(SPONSOR_SHARE_SHOWS)),
    )

    details: list[str] = []
    status = "pass"
    for row in rows:
        mentions = int(row["mentions"] or 0)
        ads = int(row["ads"] or 0)
        if mentions < SPONSOR_SHARE_MIN_MENTIONS:
            details.append(
                f"{row['slug']}: {mentions} mention(s) in {SPONSOR_SHARE_WINDOW_DAYS}d "
                f"— too few to judge"
            )
            continue
        share = ads / mentions
        if ads == mentions:
            status = "fail"
            details.append(
                f"{row['slug']}: ALL {mentions} recent mention(s) are sponsor reads — "
                f"no editorial content got through"
            )
        elif share > SPONSOR_SHARE_WARN_THRESHOLD:
            if status != "fail":
                status = "warn"
            details.append(
                f"{row['slug']}: {ads}/{mentions} ({share:.0%}) sponsor reads, over the "
                f"{SPONSOR_SHARE_WARN_THRESHOLD:.0%} threshold"
            )
        else:
            details.append(f"{row['slug']}: {ads}/{mentions} ({share:.0%}) sponsor reads")

    summary = {
        "pass": "Sponsor-read share is within range for every tech show.",
        "warn": "A tech show's sponsor-read share is above the expected range.",
        "fail": "A tech show has no editorial mentions at all in the recent window.",
    }[status]
    return CheckResult("sponsor_share", status, summary, details)


def check_possible_entity_alias_splits(conn) -> CheckResult:
    rows = _rows(
        conn,
        """
        SELECT
          entity_type,
          REGEXP_REPLACE(normalized_name, '[^a-z0-9]', '', 'g') AS compact_key,
          COALESCE(platform, '') AS platform_key,
          COUNT(*) AS entity_count,
          ARRAY_AGG(id ORDER BY id) AS entity_ids,
          ARRAY_AGG(canonical_name ORDER BY id) AS names
        FROM ai_entities
        GROUP BY entity_type, REGEXP_REPLACE(normalized_name, '[^a-z0-9]', '', 'g'), COALESCE(platform, '')
        HAVING COUNT(*) > 1
        ORDER BY entity_count DESC, entity_type, compact_key
        LIMIT 25;
        """,
    )
    details = [
        (
            f"{row['entity_type']} compact_key=`{row['compact_key']}` "
            f"ids={row['entity_ids']} names={row['names']}"
        )
        for row in rows
    ]
    status = _status_from_count(len(rows), warn_only=True)
    summary = "No likely entity alias splits found with compact matching." if status == "pass" else (
        f"{len(rows)} possible entity alias split(s) found."
    )
    return CheckResult("possible_entity_alias_splits", status, summary, details)


def check_optional_null_map(conn) -> CheckResult:
    columns = [
        "description_body",
        "episode_number",
        "audio_url",
        "image_url",
        "raw_content",
        "has_songs_discussed",
    ]
    selects = ", ".join(
        f"COUNT(*) FILTER (WHERE e.{column} IS NULL) AS {column}_nulls"
        for column in columns
    )
    rows = _rows(
        conn,
        f"""
        SELECT s.slug, COUNT(*) AS episodes, {selects}
        FROM shows s
        JOIN episodes e ON e.show_id = s.id
        GROUP BY s.slug
        ORDER BY s.slug;
        """,
    )
    details = [json.dumps(row, default=str) for row in rows]
    details.extend(f"{key}: {note}" for key, note in OPTIONAL_NULL_NOTES.items())
    return CheckResult(
        "optional_null_map",
        "pass",
        "Optional/null-prone episode fields mapped for human review.",
        details,
    )


def run_checks(conn, include_feed_check: bool = False) -> list[CheckResult]:
    checks = [
        check_expected_shows(conn),
        check_episode_identity(conn),
        check_duplicate_episodes(conn),
        check_transcript_coverage(conn),
        check_episode_freshness(conn),
        check_notion_sync_freshness(conn),
        check_ai_daily_extraction(conn),
        check_transcript_race_selfheal(conn),
        check_ai_mention_fields(conn),
        check_sponsor_share(conn),
        check_possible_entity_alias_splits(conn),
        check_optional_null_map(conn),
    ]
    if include_feed_check:
        # Opt-in: makes external Taddy/RSS calls. The CLI enables it (the daily alarm);
        # the pulse omits it because it does its own per-show feed display.
        checks.append(check_import_caught_up(conn))
    return checks


def render_text(results: list[CheckResult]) -> str:
    lines = [
        "Podcast data health report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for result in results:
        lines.append(f"[{result.status.upper()}] {result.name}")
        lines.append(f"  {result.summary}")
        for detail in result.details:
            lines.append(f"  - {detail}")
        lines.append("")
    failures = sum(1 for result in results if result.status == "fail")
    warnings = sum(1 for result in results if result.status == "warn")
    lines.append(f"Totals: {failures} failure(s), {warnings} warning(s), {len(results)} checks")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only Neon data health checks.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any check fails. Use this in automation.",
    )
    parser.add_argument(
        "--feed-check-only",
        action="store_true",
        help=(
            "Run ONLY the second-source feed comparison. The music workflow uses this "
            "so a show that stopped discovering fails its own run instead of exiting 0."
        ),
    )
    parser.add_argument(
        "--shows",
        help="Comma-separated slugs to limit the feed check to (default: all shows).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()
    slugs = [s.strip() for s in args.shows.split(",") if s.strip()] if args.shows else None
    conn = get_db_connection()
    try:
        if args.feed_check_only:
            # One feed comparison, scoped to the show(s) that just ran. Cheap enough to
            # sit at the end of every music run, which is the point: the music pipeline
            # had no way to fail when its discovery stopped finding anything.
            results = [check_import_caught_up(conn, slugs)]
        else:
            # Daily CLI run includes the second-source feed check (the loud import-behind alarm).
            results = run_checks(conn, include_feed_check=True)
    finally:
        conn.close()

    if args.json:
        print(json.dumps([asdict(result) for result in results], default=str, indent=2))
    else:
        print(render_text(results))

    # Alert to Slack when a check fails — especially staleness, where a show has silently
    # stopped updating. This is the backstop for a partial pipeline failure that didn't crash
    # the run. post_slack is a no-op without SLACK_WEBHOOK_URL, so local runs stay quiet.
    failed = [r for r in results if r.status == "fail"]
    if failed:
        post_slack(
            ":warning: *list-maker data health* — "
            + "; ".join(f"{r.name}: {r.summary}" for r in failed)
        )

    if args.strict and failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Allow running as `python pipeline/data_health.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import get_db_connection, load_environment, post_slack
from feed_check import feed_recent_dates
from show_config import SHOWS


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

OPTIONAL_NULL_NOTES = {
    "episodes.raw_content": "Stored only for AI Daily/PCHH Taddy imports; null for SOP/TAL is expected.",
    "episodes.has_songs_discussed": "Legacy music-triage field; null means not evaluated or not applicable.",
    "episodes.episode_number": "Provider-specific; AI Daily does not provide it, older rows may not have it.",
    "episodes.audio_url": "Expected on recent Taddy imports; old website-scraped rows may not have it.",
    "episodes.image_url": "Helpful but not source-of-truth; missing art is not a data integrity failure.",
}


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

        if policy["mode"] == "none":
            details.append(f"{slug}: show-notes based — no transcripts expected (skipped)")
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
            failures.append(f"{slug}: {missing} episode(s) missing transcripts")

        max_lag = int(policy.get("max_latest_lag_days", 30))
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
    for row in rows:
        slug = row["slug"]
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


def check_import_caught_up(conn) -> CheckResult:
    """SECOND-SOURCE freshness: is our import behind each show's REAL feed?

    episode_freshness_by_show only knows "days since OUR latest", which can't tell a show
    on break from an import that silently broke. This asks each feed (Taddy / Megaphone
    RSS via feed_check) what the latest episode is — if the feed is ahead of us, we're
    behind and missing episodes. A feed we can't reach is reported, not failed (don't cry
    wolf on a flaky feed); a confirmed BEHIND is a real, actionable failure.
    """
    rows = _rows(
        conn,
        """
        SELECT s.slug, MAX(e.publish_date)::date AS db_latest
        FROM shows s LEFT JOIN episodes e ON e.show_id = s.id
        GROUP BY s.slug
        """,
    )
    db_latest = {r["slug"]: r["db_latest"] for r in rows}
    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    for slug, cfg in SHOWS.items():
        latest = db_latest.get(slug)
        feed = feed_recent_dates(cfg)
        if not feed:
            # None = couldn't get a trustworthy answer (unreachable / error / empty). A
            # persistent one means the second source itself is broken — surface it as a
            # WARN so it can't hide as a silent pass, without crying wolf on a flaky run.
            warnings.append(f"{slug}: feed UNVERIFIED — second source unreachable")
            continue
        behind = sum(1 for d in feed if latest is None or d > latest)
        if behind:
            failures.append(f"{slug}: BEHIND {behind} — feed at {feed[0]}, we have {latest}")
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
        JOIN episode_transcripts et ON et.episode_id = ep.id;
        """,
    )
    missing_mentions = int(row.get("transcripted_without_mentions") or 0)
    # A NULL transcript_id is only an issue if the episode actually HAS a transcript that
    # should have been linked. Show-notes-based shows (Culture Gabfest) extract from the
    # RSS description and legitimately have no transcript, so their mentions are excluded.
    # Still flag orphans (transcript_id points to a transcript that no longer exists).
    null_transcript_mentions = int(
        _one(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM ai_mentions m
            WHERE (
                    m.transcript_id IS NULL
                    AND EXISTS (SELECT 1 FROM episode_transcripts et2 WHERE et2.episode_id = m.episode_id)
                  )
               OR (
                    m.transcript_id IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM episode_transcripts et WHERE et.id = m.transcript_id)
                  );
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

    issue_count = missing_mentions + null_transcript_mentions + zero_mention_runs
    details = []
    if missing_mentions:
        details.append(f"AI Daily transcripted episodes without mentions: {missing_mentions}")
    if null_transcript_mentions:
        details.append(f"AI mentions without a valid transcript_id: {null_transcript_mentions}")
    if zero_mention_runs:
        details.append(f"completed AI runs with zero mentions: {zero_mention_runs}")

    status = _status_from_count(issue_count)
    summary = "AI Daily transcripts, mentions, transcripts, and runs line up." if status == "pass" else (
        f"{issue_count} AI extraction integrity issue(s) found."
    )
    return CheckResult("ai_daily_extraction_integrity", status, summary, details)


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
        check_ai_daily_extraction(conn),
        check_ai_mention_fields(conn),
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()
    conn = get_db_connection()
    try:
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

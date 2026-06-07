#!/usr/bin/env python3
"""Biweekly 'pulse' report to Slack — a POSITIVE heartbeat for the pipeline.

The failure alerts (data_health staleness, workflow-failure, eval regression, trigger
failure) are PUSH: they fire when something breaks *while the system is running*. The
pulse is the complement — a regular "here's the state, everything's firing" digest. Two
jobs:
  1. Useful at-a-glance health: per-show freshness, library counts, any open issues.
  2. A heartbeat. If the pulse stops arriving, the trigger (Cloudflare Worker) is down —
     the one blind spot the push-alerts can't cover (they all come from runs). For an
     ACTIVE dead-Worker alert, pair this with a Sentry Cron Monitor check-in in the Worker.

Always posts unless --dry-run. Reuses data_health's checks for the issue list (so the
pulse and the failure-alerts agree on what "a problem" is).

    ./venv/bin/python pulse_report.py            # post the digest to Slack
    ./venv/bin/python pulse_report.py --dry-run  # print only
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import get_db_connection, load_environment, post_slack
from data_health import DEFAULT_STALENESS_MAX_DAYS, STALENESS_MAX_DAYS, run_checks

SHOW_SHORT = {
    "ai-daily-brief": "AI Daily",
    "hard-fork": "Hard Fork",
    "pchh": "PCHH",
    "culture-gabfest": "Culture Gabfest",
    "sop": "SOP",
    "tal": "TAL",
}


def _rows(conn, sql: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]


def gather(conn) -> tuple[list[dict], dict]:
    shows = _rows(
        conn,
        """
        SELECT s.id, s.slug,
               COUNT(e.id) AS episodes,
               MAX(e.publish_date)::date AS latest,
               (CURRENT_DATE - MAX(e.publish_date)::date) AS days_since
        FROM shows s LEFT JOIN episodes e ON e.show_id = s.id
        GROUP BY s.id, s.slug
        ORDER BY s.id
        """,
    )
    totals = _rows(
        conn,
        """
        SELECT
          (SELECT COUNT(*) FROM ai_entities) AS entities,
          (SELECT COUNT(*) FROM ai_mentions) AS mentions,
          (SELECT COUNT(*) FROM episode_transcripts WHERE notion_transcript_page_id IS NOT NULL) AS notion_transcripts
        """,
    )[0]
    return shows, totals


def build_digest(shows: list[dict], totals: dict, checks: list) -> str:
    today = datetime.now(timezone.utc).date()
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]

    lines: list[str] = [f"📊 *list-maker pulse* — {today.isoformat()}", ""]
    if fails:
        lines.append(f"⚠️ *{len(fails)} issue(s) need attention* (+{len(warns)} warning(s))")
    else:
        suffix = f" ({len(warns)} warning(s))" if warns else ""
        lines.append(f"✅ *All systems firing.*{suffix}")
    lines.append("")

    lines.append("*Shows* — latest episode · freshness · count")
    for s in shows:
        short = SHOW_SHORT.get(s["slug"], s["slug"])
        days = s["days_since"]
        threshold = STALENESS_MAX_DAYS.get(s["slug"], DEFAULT_STALENESS_MAX_DAYS)
        if days is None:
            mark, fresh, latest = "❓", "no episodes yet", "—"
        elif days > threshold:
            mark, fresh, latest = "⚠️", f"{days}d ago — STALE (>{threshold}d)", str(s["latest"])
        else:
            mark, fresh, latest = "✅", f"{days}d ago", str(s["latest"])
        lines.append(f"• {short} — {latest} · {mark} {fresh} · {s['episodes']} eps")
    lines.append("")

    lines.append(
        f"*Library:* {totals['entities']:,} entities · {totals['mentions']:,} mentions · "
        f"{totals['notion_transcripts']:,} transcripts in Notion"
    )

    # Only list actionable FAILURES. Warnings are advisory (e.g. standing entity-dedup
    # candidates) and just get a headline count — listing them every pulse is noise.
    if fails:
        lines.append("")
        lines.append("*Needs attention:*")
        for c in fails:
            lines.append(f"• {c.name}: {c.summary}")
        lines.append("(run `data_health.py` for full detail)")

    nxt = today + timedelta(days=14)
    lines.append("")
    lines.append(f"_Next pulse ~{nxt.isoformat()}. If a pulse doesn't arrive, the trigger is down._")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Post the biweekly list-maker pulse to Slack")
    ap.add_argument("--dry-run", action="store_true", help="Print the digest, don't post")
    args = ap.parse_args()

    load_environment()
    conn = get_db_connection()
    try:
        shows, totals = gather(conn)
        checks = run_checks(conn)
    finally:
        conn.close()

    digest = build_digest(shows, totals, checks)
    print(digest)
    if not args.dry_run:
        ok = post_slack(digest)
        print(f"\n[posted to Slack: {ok}]")


if __name__ == "__main__":
    main()

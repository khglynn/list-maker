#!/usr/bin/env python3
"""Biweekly 'pulse' report to Slack — a TRUSTWORTHY heartbeat for the pipeline.

The failure alerts (data_health, workflow-failure, eval, trigger-failure) are PUSH: they
fire when something breaks while the system is running. The pulse is the complement — a
regular "here's the state, and the green is earned" digest. Three jobs:

  1. Health at a glance: per-show, is our import CAUGHT UP TO THE REAL FEED (second source
     via feed_check) — not just "days since our own latest", which can't tell "the show is
     on break" from "our import silently broke". Plus recent-episode counts + a link to
     each destination, and a link to the Pod Lists hub up top (the table of contents).
  2. A heartbeat: if the pulse stops arriving, the trigger (Cloudflare Worker) is down.
  3. Honest: a "behind the feed" or "couldn't verify" never shows as green.

Always posts unless --dry-run, and FAILS (exit non-zero) if it can't post — a silently
unposted heartbeat would defeat "no pulse = trigger down".
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import get_db_connection, load_environment, post_slack
from data_health import DEFAULT_STALENESS_MAX_DAYS, STALENESS_MAX_DAYS, run_checks
from feed_check import feed_recent_dates
from show_config import SHOWS

HUB_URL = "https://www.notion.so/31c0501ef95080d1a3fde8fa8d5ce907"  # Pod Lists hub
RECENT_WINDOW_DAYS = 15  # ~the pulse cadence; "recent episodes we've processed"

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


def destination_link(cfg) -> str:
    """A Slack <url|label> link to where this show's output lives."""
    if cfg is None:
        return ""
    if cfg.spotify_playlist_id:
        return f"<https://open.spotify.com/playlist/{cfg.spotify_playlist_id}|Spotify ▶>"
    if cfg.notion_database_id:
        return f"<https://www.notion.so/{cfg.notion_database_id.replace('-', '')}|Notion ↗>"
    return ""


def gather(conn) -> tuple[list[dict], dict]:
    shows = _rows(
        conn,
        f"""
        SELECT s.id, s.slug,
               COUNT(e.id) AS episodes,
               MAX(e.publish_date)::date AS latest,
               (CURRENT_DATE - MAX(e.publish_date)::date) AS days_since,
               COUNT(e.id) FILTER (WHERE e.publish_date >= CURRENT_DATE - {RECENT_WINDOW_DAYS}) AS recent
        FROM shows s LEFT JOIN episodes e ON e.show_id = s.id
        GROUP BY s.id, s.slug
        ORDER BY s.id
        """,
    )
    # Second source: ask each show's real feed what the latest episode is.
    for s in shows:
        cfg = SHOWS.get(s["slug"])
        s["cfg"] = cfg
        s["feed_dates"] = feed_recent_dates(cfg) if cfg else None

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


def show_status(s: dict) -> tuple[str, bool]:
    """Return (status_text, is_behind) by comparing our latest vs the real feed."""
    db_latest = s["latest"]
    feed = s["feed_dates"]
    if feed is None:
        return f"❓ feed unverified — we have {db_latest}", False
    if not feed:
        return f"❓ feed empty — we have {db_latest}", False

    feed_latest = feed[0]
    behind = sum(1 for d in feed if db_latest is None or d > db_latest)
    if behind > 0:
        return f"🚨 BEHIND {behind} — feed at {feed_latest}, we have {db_latest}", True

    age = s["days_since"]
    threshold = STALENESS_MAX_DAYS.get(s["slug"], DEFAULT_STALENESS_MAX_DAYS)
    if age is not None and age > threshold:
        # Caught up, but the show itself is quiet — say so, so the green is explained.
        return f"✅ caught up — show quiet {age}d ({db_latest})", False
    return f"✅ caught up ({db_latest})", False


def build_digest(shows: list[dict], totals: dict, checks: list) -> str:
    today = datetime.now(timezone.utc).date()
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]

    show_lines: list[str] = []
    behind_count = 0
    for s in shows:
        status, is_behind = show_status(s)
        if is_behind:
            behind_count += 1
        short = SHOW_SHORT.get(s["slug"], s["slug"])
        dest = destination_link(s.get("cfg"))
        show_lines.append(f"• *{short}*  {status}  ·  +{s['recent']} recent  ·  {dest}")

    lines: list[str] = [f"📊 *list-maker pulse* — {today.isoformat()}", f"<{HUB_URL}|→ Pod Lists hub (all links)>", ""]
    if behind_count or fails:
        lines.append(f"⚠️ *{behind_count + len(fails)} issue(s) need attention* (+{len(warns)} warning(s))")
    else:
        suffix = f" ({len(warns)} warning(s))" if warns else ""
        lines.append(f"✅ *All systems firing — caught up to every feed.*{suffix}")
    lines.append("")

    lines.append("*Shows* — are we caught up to the real feed?")
    lines.extend(show_lines)
    lines.append("")
    lines.append(
        f"*Library:* {totals['entities']:,} entities · {totals['mentions']:,} mentions · "
        f"{totals['notion_transcripts']:,} transcripts in Notion"
    )

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
        # The pulse IS the heartbeat — a "success" that didn't actually post would make a
        # silently-broken webhook look healthy, defeating "no pulse = trigger down".
        if not post_slack(digest):
            print(
                "ERROR: pulse digest could not be posted to Slack — is SLACK_WEBHOOK_URL set?",
                file=sys.stderr,
            )
            sys.exit(1)
        print("\n[posted to Slack]")


if __name__ == "__main__":
    main()

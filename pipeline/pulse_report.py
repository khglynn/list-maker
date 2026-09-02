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
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import get_db_connection, load_environment, post_slack
from data_health import (
    DEFAULT_FEED_GRACE_DAYS,
    DEFAULT_STALENESS_MAX_DAYS,
    STALENESS_MAX_DAYS,
    _today,
    run_checks,
    split_missing_feed_dates,
)
from feed_check import feed_recent_dates
from show_config import SHOWS

HUB_URL = "https://www.notion.so/31c0501ef95080d1a3fde8fa8d5ce907"  # Pod Lists hub
QUEUE_URL = "https://www.notion.so/37c0501ef9508139b52be5d5f7d71f53"  # Blog Pull Queue
RECENT_WINDOW_DAYS = 15  # ~the pulse cadence; "recent episodes we've processed"

SHOW_SHORT = {
    "ai-daily-brief": "AI Daily",
    "hard-fork": "Hard Fork",
    "pchh": "PCHH",
    "culture-gabfest": "Culture Gabfest",
    "sop": "SOP",
    "tal": "TAL",
    "openai-blog": "OpenAI blog",
    "anthropic-blog": "Anthropic blog",
    "saved-articles": "Saved articles",
    "agentic-research": "Research docs",
    "saved-episodes": "Saved episodes",
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


def show_status(s: dict, today: date | None = None) -> tuple[str, str]:
    """Return (status_text, state), state in 'ok' | 'behind' | 'unverified' | 'curated'.

    'unverified' (feed_check returned None — unreachable / error / empty) is its own state
    on purpose: it must NEVER be counted as green, or the pulse lies by omission.

    'curated' is for sources with no feed at all (blogs, saved articles, research docs):
    their "latest" is just the last time something was saved by hand. Until 2026-09-01
    they rendered as "❓ feed unverified" — five alarms on every pulse for something that
    was working exactly as designed.
    """
    cfg = s.get("cfg")
    db_latest = s["latest"]
    if cfg is not None and getattr(cfg, "medium", "podcast") != "podcast":
        return f"📌 curated — {s['episodes']} item(s), last saved {db_latest}", "curated"

    feed = s["feed_dates"]
    if not feed:  # None — couldn't get a trustworthy answer from the feed
        return f"❓ feed unverified — we have {db_latest}", "unverified"

    feed_latest = feed[0]
    grace = getattr(cfg, "feed_grace_days", DEFAULT_FEED_GRACE_DAYS)
    overdue, pending = split_missing_feed_dates(feed, db_latest, grace, today=today)
    if overdue:
        return f"🚨 BEHIND {len(overdue)} — feed at {feed_latest}, we have {db_latest}", "behind"
    if pending:
        # Published, not yet imported, inside the show's normal import window — fine.
        return f"✅ caught up ({db_latest}) — {len(pending)} newer pending import", "ok"

    age = s["days_since"]
    threshold = STALENESS_MAX_DAYS.get(s["slug"], DEFAULT_STALENESS_MAX_DAYS)
    if age is not None and age > threshold:
        # Caught up, but the show itself is quiet — say so, so the green is explained.
        return f"✅ caught up — show quiet {age}d ({db_latest})", "ok"
    return f"✅ caught up ({db_latest})", "ok"


def pull_queue_counts() -> dict | None:
    """Candidate / checked counts from the Blog Pull Queue, or None if it can't be read.

    The queue is the one place the blog sources wait on a human, and nothing else
    nudges: between 2026-06-14 and 2026-09-01 it held 31 unchecked candidates and no
    report said so. The pulse is the natural place — it is already the note that says
    where things stand. A Notion hiccup must not sink the heartbeat, so this never
    raises; the digest says "couldn't read it" instead of pretending.
    """
    token = os.getenv("NOTION_TOKEN")
    if not token:
        return None
    try:
        from build_pull_queue import QUEUE_DB_ID, queue_counts  # the queue's own I/O module

        return queue_counts(token, QUEUE_DB_ID)
    except Exception as exc:  # noqa: BLE001 — see docstring
        print(f"WARNING: could not read the Blog Pull Queue: {exc}", file=sys.stderr)
        return None


def build_digest(
    shows: list[dict], totals: dict, checks: list, queue: dict | None = None,
    today: date | None = None,
) -> str:
    today = today or _today()
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]

    show_lines: list[str] = []
    curated_lines: list[str] = []
    behind_count = 0
    unverified_count = 0
    for s in shows:
        status, state = show_status(s, today=today)
        if state == "behind":
            behind_count += 1
        elif state == "unverified":
            unverified_count += 1
        short = SHOW_SHORT.get(s["slug"], s["slug"])
        dest = destination_link(s.get("cfg"))
        if state == "curated":
            curated_lines.append(f"• *{short}*  {status}  ·  {dest}".rstrip(" ·"))
        else:
            show_lines.append(f"• *{short}*  {status}  ·  +{s['recent']} recent  ·  {dest}")

    lines: list[str] = [f"📊 *list-maker pulse* — {today.isoformat()}", f"<{HUB_URL}|→ Pod Lists hub (all links)>", ""]
    warn_suffix = f" (+{len(warns)} warning(s))" if warns else ""
    if behind_count or fails:
        unv = f" · {unverified_count} feed(s) unverified" if unverified_count else ""
        lines.append(f"⚠️ *{behind_count + len(fails)} issue(s) need attention*{unv}{warn_suffix}")
    elif unverified_count:
        # We didn't actually check every feed — say so; don't claim "every feed".
        lines.append(f"✅ *Caught up where we could check* — ⚠️ {unverified_count} feed(s) unverified{warn_suffix}")
    else:
        lines.append(f"✅ *All systems firing — caught up to every feed.*{warn_suffix}")
    lines.append("")

    lines.append("*Shows* — are we caught up to the real feed?")
    lines.extend(show_lines)
    if curated_lines:
        lines.append("")
        lines.append("*Curated sources* — pulled by hand, no feed to be behind")
        lines.extend(curated_lines)
    lines.append("")
    lines.append(
        f"*Library:* {totals['entities']:,} entities · {totals['mentions']:,} mentions · "
        f"{totals['notion_transcripts']:,} transcripts in Notion"
    )
    if queue is None:
        lines.append("📥 *Blog Pull Queue:* couldn't read it this time (NOTION_TOKEN missing or Notion error)")
    elif queue["candidates"] == 0:
        lines.append(f"📥 *Blog Pull Queue:* empty — nothing waiting on you · <{QUEUE_URL}|queue>")
    else:
        checked = (
            f" ({queue['checked']} checked, ingesting Monday)" if queue["checked"] else ""
        )
        oldest = f", oldest {queue['oldest_days']}d" if queue.get("oldest_days") else ""
        lines.append(
            f"📥 *Blog Pull Queue:* {queue['candidates']} candidate(s) waiting for your "
            f"checkbox{checked}{oldest} · <{QUEUE_URL}|open the queue>"
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
        # One snapshot for both reads. The freshness lines and the health checks must
        # describe the same instant, or the digest contradicts itself — on 2026-09-01
        # it said "we have 08-29" next to "08-31's transcript has no mentions yet",
        # because the day's import committed between the two reads.
        conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
        shows, totals = gather(conn)
        checks = run_checks(conn)
    finally:
        conn.close()
    queue = pull_queue_counts()

    digest = build_digest(shows, totals, checks, queue)
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

#!/usr/bin/env python3
"""Fetch publisher transcripts for episodes we already hold that have none.

The feed importer only sees what a show's feed lists today (16 items for This
American Life), so the archive needs its own pass. This walks every held episode of a
show with no transcript, rebuilds the feed item from what the row kept (title, link,
duration, date), and asks the show's reader (scrapers/transcripts) for it. Rows Taddy
or the feed importer wrote keep their link in raw_content; TAL's older rows ARE their
page url (thisamericanlife.org/<number>/<slug>), which is all the TAL reader needs.

It only ever adds a transcript to an episode that has none (ON CONFLICT DO NOTHING),
so it is safe to re-run and never replaces a better one. It is polite by default: one
request every 2 seconds, and it stops at the first 429.

    cd ~/DevKev/personal/list-maker/pipeline
    ./venv/bin/python scrapers/rss/backfill_transcripts.py --shows tal --dry-run
    ./venv/bin/python scrapers/rss/backfill_transcripts.py --shows tal

Prints counts and episode labels only, never transcript text (the repo is public and
its run logs are too).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

PIPELINE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PIPELINE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import get_db_connection, load_environment  # noqa: E402
from feeds import FeedItem  # noqa: E402
from import_feed import add_transcript  # noqa: E402
from scrapers.transcripts import reader_for  # noqa: E402
from scrapers.transcripts.base import NotYet, Transcript  # noqa: E402
from show_config import SHOWS  # noqa: E402

PAUSE_SECONDS = 2.0


def item_from_row(row: dict) -> FeedItem:
    """The feed item a held row stands for, from what the row kept."""
    raw: dict = {}
    if (row.get("raw_content") or "").startswith("{"):
        try:
            raw = json.loads(row["raw_content"])
        except ValueError:
            raw = {}
    # Taddy rows kept the episode's own fields one level down, under "episode_raw".
    taddy = raw.get("episode_raw") if isinstance(raw.get("episode_raw"), dict) else {}
    url = row.get("url") or ""
    page_url = url if url.startswith("http") and "api.taddy.org" not in url else None
    link = raw.get("link") or taddy.get("websiteUrl") or raw.get("websiteUrl") or page_url
    duration = raw.get("duration_seconds") or taddy.get("duration") or raw.get("duration")
    published = row.get("publish_date")
    if isinstance(published, str):
        published = date.fromisoformat(published[:10])
    return FeedItem(
        title=row.get("title") or "",
        guid=raw.get("guid") or taddy.get("guid"),
        link=link,
        enclosure_url=raw.get("enclosure_url") or taddy.get("audioUrl") or row.get("audio_url"),
        pubdate_raw=None,
        published_at=datetime(published.year, published.month, published.day, 12, tzinfo=timezone.utc) if published else None,
        description_html="",
        duration_seconds=int(duration) if duration else None,
    )


def missing_rows(conn, show_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.title, e.url, e.publish_date::date AS publish_date, e.audio_url, e.raw_content
            FROM episodes e
            LEFT JOIN episode_transcripts et ON et.episode_id = e.id
            WHERE e.show_id = %s AND et.id IS NULL
            ORDER BY e.publish_date DESC NULLS LAST, e.id DESC
            """,
            (show_id,),
        )
        cols = [c[0] for c in cur.description]
        # common.get_db_connection's cursor already returns dict rows; a plain one doesn't.
        return [dict(r) if isinstance(r, dict) else dict(zip(cols, r)) for r in cur.fetchall()]


def run_show(conn, slug: str, *, dry_run: bool, limit: Optional[int], session=None, pause: float = PAUSE_SECONDS) -> dict:
    cfg = SHOWS[slug]
    if not cfg.transcript_source:
        return {"show": slug, "error": "this show has no transcript source"}
    reader = reader_for(cfg.transcript_source)
    rows = missing_rows(conn, cfg.show_id)
    if limit is not None:
        rows = rows[:limit]
    counts = {"show": slug, "missing": len(rows), "added": 0, "not_available": 0, "failed": 0, "rate_limited": False}
    reasons: dict[str, int] = {}
    for n, row in enumerate(rows):
        if n:
            time.sleep(pause)
        result = reader.fetch(item_from_row(row), session=session)
        if isinstance(result, Transcript):
            if dry_run or add_transcript(conn, row["id"], result):
                counts["added"] += 1
            continue
        if isinstance(result, NotYet):
            counts["not_available"] += 1
            if result.rate_limited:
                counts["rate_limited"] = True
                break
        else:
            counts["failed"] += 1
        key = result.reason.split(": ", 1)[-1][:80]
        reasons[key] = reasons.get(key, 0) + 1
    counts["reasons"] = dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:8])
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shows", required=True, help="comma-separated show slugs")
    ap.add_argument("--dry-run", action="store_true", help="fetch and check, write nothing")
    ap.add_argument("--limit", type=int, help="at most this many episodes per show")
    args = ap.parse_args(argv)
    load_environment()
    conn = get_db_connection()
    if args.dry_run:
        conn.set_session(readonly=True)
    worst = 0
    try:
        for slug in [s.strip() for s in args.shows.split(",") if s.strip()]:
            if slug not in SHOWS:
                print(f"unknown show {slug!r}", file=sys.stderr)
                return 2
            counts = run_show(conn, slug, dry_run=args.dry_run, limit=args.limit)
            verb = "would add" if args.dry_run else "added"
            print(f"[{slug}] missing={counts.get('missing')} {verb}={counts.get('added')} "
                  f"not available={counts.get('not_available')} failed={counts.get('failed')}"
                  + (" — stopped at HTTP 429" if counts.get("rate_limited") else ""))
            for reason, k in (counts.get("reasons") or {}).items():
                print(f"    {k} × {reason}")
            if counts.get("error"):
                print(f"    {counts['error']}")
                worst = max(worst, 2)
            elif counts.get("failed") or counts.get("rate_limited"):
                worst = max(worst, 1)
    finally:
        conn.close()
    return worst


if __name__ == "__main__":
    sys.exit(main())

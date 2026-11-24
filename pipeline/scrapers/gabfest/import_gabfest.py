#!/usr/bin/env python3
"""Culture Gabfest importer — Megaphone RSS show-notes (not Taddy).

Taddy won't transcribe Gabfest (iHeart distribution rights), but the Megaphone
feed carries each episode's show-notes, which describe the cultural items the
hosts discuss/endorse. We import those notes as episode raw_content so the media
extraction profile can pull media recommendations from them.

Slate moved the feed to Acast by 2026-11 (show_config.feed_url; the Megaphone url
redirects). The show ended 2026-07-01, so this now finds nothing new unless it returns.

Caveats baked into the design (verified 2026-06-07 against the live feed):
  - feeds.megaphone.fm/slatesculturegabfest is the broad Slate Culture feed and
    MIXES shows, so we filter to titles starting with "Culture Gabfest".
  - Show-notes are prose summaries, not clean endorsement lists — a thinner
    signal than a transcript, but real.

Standalone by design: imports only common + show_config (NOT extract_entities or
load_entity_batch), so it never interferes with a running extraction backfill.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import requests

# pipeline/ on the path so `common` / `show_config` import (file is pipeline/scrapers/gabfest/).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common import get_db_connection, get_logger, load_environment  # noqa: E402
import feeds  # noqa: E402  (the one RSS parser: pipeline/feeds.py)

log = get_logger("import_gabfest")

SHOW_SLUG = "culture-gabfest"
TITLE_PREFIX = "Culture Gabfest"
DEFAULT_FEED_URL = "https://feeds.megaphone.fm/slatesculturegabfest"

def clean_html(raw: str) -> str:
    """Plain text from show-note HTML (feeds.clean_html, kept here under its old name)."""
    return feeds.clean_html(raw)


def parse_pubdate(raw: Optional[str]) -> Optional[date]:
    """The UTC day of a pubDate (feeds.py, rule 1). Slate's feed dates every item in GMT,
    so this is the same day the importer always stored; checked 2026-11-23."""
    dt = feeds.parse_pubdate_utc(raw)
    return dt.date() if dt else None


def episode_url(item: dict[str, Any]) -> str:
    """Per-episode-unique dedup key (episodes.url is UNIQUE): the guid, else the
    enclosure, else the link, else a synthetic key. The same chain as
    feeds.episode_identity, which the feed check uses to rebuild it; tests/
    test_import_gabfest.py holds the two equal. <link> comes last because it can be a
    generic show page (that was the Hard Fork url-collapse bug)."""
    return (
        item.get("guid")
        or item.get("enclosure_url")
        or item.get("link")
        or f"gabfest:{item.get('title', 'unknown')}:{item.get('pubdate_raw', '')}"
    )


def parse_feed(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Every item in the feed (all shows, unfiltered), as the dicts this importer and
    scrapers/tal/fetch.py read. Parsing itself is feeds.parse_feed."""
    return [
        {
            "title": it.title,
            "guid": it.guid,
            "link": it.link,
            "enclosure_url": it.enclosure_url,
            "pubdate_raw": it.pubdate_raw,
            "publish_date": it.publish_date,
            "description_html": it.description_html,
            "description_text": it.description_text,
        }
        for it in feeds.parse_feed(xml_bytes).items
    ]


def filter_gabfest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only Culture Gabfest episodes (the feed mixes Slate shows)."""
    return [it for it in items if it["title"].startswith(TITLE_PREFIX)]


def upsert_episode(conn, show_id: int, item: dict[str, Any]) -> bool:
    """Idempotent upsert keyed on url. Returns True if newly inserted, False if updated."""
    url = episode_url(item)
    raw_content = json.dumps(
        {
            "provider": "megaphone-rss",
            "imported_at": datetime.utcnow().isoformat() + "Z",
            "guid": item.get("guid"),
            "link": item.get("link"),
            "enclosure_url": item.get("enclosure_url"),
            "description": item.get("description_text"),
        }
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episodes (show_id, title, url, publish_date, description_body, raw_content, scraped_at, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (url) DO UPDATE SET
                title = EXCLUDED.title,
                publish_date = COALESCE(EXCLUDED.publish_date, episodes.publish_date),
                description_body = COALESCE(EXCLUDED.description_body, episodes.description_body),
                raw_content = COALESCE(EXCLUDED.raw_content, episodes.raw_content),
                scraped_at = NOW()
            RETURNING (xmax = 0) AS inserted;
            """,
            (
                show_id,
                (item["title"][:500] or "Untitled Episode"),
                url,
                item["publish_date"],
                item["description_text"],
                raw_content,
            ),
        )
        inserted = bool(cur.fetchone()["inserted"])
    conn.commit()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Culture Gabfest episodes from Megaphone RSS")
    parser.add_argument("--limit", type=int, default=None, help="Max Gabfest episodes to upsert (feed order, newest first)")
    parser.add_argument("--feed-url", type=str, default=None, help="Override the feed URL")
    parser.add_argument("--dry-run", action="store_true", help="Parse + filter but do not write")
    args = parser.parse_args()

    load_environment()
    log = get_logger("import_gabfest")  # refresh after env load (LOG_LEVEL)

    from show_config import get_show  # local import: keeps the module test-importable without the DB

    show = get_show(SHOW_SLUG)
    feed_url = args.feed_url or show.feed_url or DEFAULT_FEED_URL

    log.info("Fetching Gabfest feed: %s", feed_url)
    resp = requests.get(feed_url, timeout=60, headers={"User-Agent": feeds.USER_AGENT})
    resp.raise_for_status()

    all_items = parse_feed(resp.content)
    gabfest = filter_gabfest(all_items)
    log.info("Feed has %d items; %d are Culture Gabfest", len(all_items), len(gabfest))

    if args.limit:
        gabfest = gabfest[: args.limit]

    if args.dry_run:
        for it in gabfest[:10]:
            log.info("  would import: %s | %s", it["publish_date"], it["title"][:70])
        log.info("[dry-run] %d Gabfest episodes parsed (not written)", len(gabfest))
        return

    conn = get_db_connection()
    try:
        inserted = updated = 0
        for it in gabfest:
            if upsert_episode(conn, show.show_id, it):
                inserted += 1
            else:
                updated += 1
        log.info("[culture-gabfest] done: inserted=%d, updated=%d, total=%d", inserted, updated, inserted + updated)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

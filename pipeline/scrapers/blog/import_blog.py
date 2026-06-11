#!/usr/bin/env python3
"""Ingest a blog post / article into Neon: scrape via Firecrawl, store as an
episode of a curated show with the full markdown in episode_transcripts.

This is the storage PRIMITIVE for curated sources (openai-blog, anthropic-blog,
saved-articles). The user-facing flow — resolve show by domain, extract entities,
sync Notion — lives in save_item.py; the scheduled orchestrator never calls this
(curated shows have no feed or cadence).

    ./venv/bin/python import_blog.py --show anthropic-blog --url https://www.anthropic.com/news/...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pipeline.common import get_db_connection, get_logger, load_environment  # noqa: E402
from pipeline.show_config import get_show  # noqa: E402

FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"

# Tracking params stripped during canonicalization. Only KNOWN-tracking keys are
# removed — a whitelist-strip-all would break legitimately query-addressed pages.
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid"}

log = get_logger("pipeline.import_blog")


def canonicalize_url(url: str) -> str:
    """Normalize a post URL into the episodes.url dedup key.

    episodes.url is UNIQUE and the only dedup gate — http/https, trailing-slash,
    and utm-tagged variants of the same post would otherwise create duplicate
    episodes and double-extract.
    """
    url = (url or "").strip()
    scheme, netloc, path, query, _fragment = urlsplit(url)
    scheme = "https" if scheme in ("http", "https", "") else scheme
    netloc = netloc.lower()
    if path != "/":
        path = path.rstrip("/")
    kept = [
        (k, v) for k, v in parse_qsl(query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
        and not k.lower().startswith(TRACKING_PARAM_PREFIXES)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def is_probable_post_url(url: str) -> bool:
    """Heuristic: does this URL point at a specific post (pullable) vs a domain
    root or section index (not)? Two path segments, or one long/sluggy segment,
    reads as a post: /news/claude-fable-5 yes, /news no, bare domain no."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if len(segments) >= 2:
        return True
    if len(segments) == 1:
        return "-" in segments[0] or len(segments[0]) >= 20
    return False


def scrape_post(url: str, api_key: str) -> dict:
    """Scrape a post via Firecrawl. Returns {markdown, metadata}."""
    response = httpx.post(
        f"{FIRECRAWL_API_URL}/scrape",
        json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    return {"markdown": data.get("markdown", ""), "metadata": data.get("metadata", {})}


def parse_publish_date(metadata: dict, url: str = "") -> Optional[date]:
    """Best-effort publish date: page metadata first, then a /YYYY/MM/DD/ URL path
    (news sites like MIT Tech Review encode it there but omit it from og tags).
    None if neither says — the caller falls back to today's date, an honest
    "when we saved it" approximation that keeps the episode-identity check green.
    """
    candidates = [
        metadata.get("article:published_time"),
        metadata.get("publishedTime"),
        metadata.get("og:published_time"),
        metadata.get("date"),
    ]
    for raw in candidates:
        if not raw:
            continue
        raw = str(raw).strip()
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
            if match:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    url_match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/", url)
    if url_match:
        try:
            return date(int(url_match.group(1)), int(url_match.group(2)), int(url_match.group(3)))
        except ValueError:
            pass  # e.g. /2026/99/99/ — a path that merely looks like a date
    return None


def pick_title(metadata: dict, url: str) -> str:
    for key in ("ogTitle", "og:title", "title"):
        value = (metadata.get(key) or "").strip()
        if value:
            # Strip " | Site Name" suffixes that og pages often carry.
            return re.split(r"\s*[|—]\s+(?=[^|]*$)", value)[0].strip() or value
    return url


def upsert_post(conn, show_id: int, url: str, title: str, publish_date: date,
                markdown: str, metadata: dict) -> tuple[int, bool]:
    """Insert/refresh the episode + full-text rows. Returns (episode_id, created).

    Re-ingesting the same canonical URL refreshes title/text instead of duplicating
    (ON CONFLICT (url) / ON CONFLICT (episode_id)). NOTE: a refreshed text does NOT
    re-extract mentions — find_unextracted_episodes skips episodes that already
    have mentions. Documented limitation until a content-hash re-extract gate exists.
    """
    raw_content = json.dumps({
        "provider": "firecrawl_blog",
        "source_url": metadata.get("sourceURL") or url,
        "og_description": metadata.get("ogDescription") or metadata.get("description") or "",
    })
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episodes (show_id, title, url, publish_date, scraped_at, raw_content)
            VALUES (%s, %s, %s, %s, now(), %s)
            ON CONFLICT (url) DO UPDATE
              SET title = EXCLUDED.title, publish_date = EXCLUDED.publish_date
            RETURNING id, (xmax = 0) AS created
            """,
            (show_id, title, url, publish_date, raw_content),
        )
        row = cur.fetchone()
        episode_id, created = row["id"], row["created"]
        cur.execute(
            """
            INSERT INTO episode_transcripts (episode_id, source_type, source_url, transcript_text)
            VALUES (%s, 'blog_post', %s, %s)
            ON CONFLICT (episode_id) DO UPDATE
              SET transcript_text = EXCLUDED.transcript_text, updated_at = now()
            """,
            (episode_id, url, markdown),
        )
    conn.commit()
    return episode_id, created


def ingest_url(conn, show_slug: str, url: str, api_key: str) -> int:
    """Scrape + store one post under a curated show. Returns the episode id."""
    cfg = get_show(show_slug)
    canonical = canonicalize_url(url)
    scraped = scrape_post(canonical, api_key)
    markdown = (scraped["markdown"] or "").strip()
    if len(markdown) < 200:
        # A near-empty scrape is a paywall/JS failure, not an article — storing it
        # would create a junk episode that extraction then "succeeds" on.
        raise RuntimeError(
            f"scrape too thin ({len(markdown)} chars) for {canonical} — not storing"
        )
    metadata = scraped["metadata"]
    title = pick_title(metadata, canonical)
    publish_date = parse_publish_date(metadata, canonical) or date.today()
    episode_id, created = upsert_post(
        conn, cfg.show_id, canonical, title, publish_date, markdown, metadata
    )
    log.info(
        "%s ep %s (%s): %r (%s, %d chars)",
        "created" if created else "refreshed", episode_id, show_slug, title,
        publish_date, len(markdown),
    )
    return episode_id


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest blog post URLs into Neon")
    p.add_argument("--show", required=True, help="Curated show slug (e.g. anthropic-blog)")
    p.add_argument("--url", action="append", required=True, help="Post URL (repeatable)")
    args = p.parse_args()

    load_environment()
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise SystemExit("FIRECRAWL_API_KEY is required")

    conn = get_db_connection()
    failed = 0
    try:
        for url in args.url:
            try:
                ingest_url(conn, args.show, url, api_key)
            except Exception as exc:  # noqa: BLE001 — isolate one bad URL, keep going
                conn.rollback()
                failed += 1
                log.error("FAILED %s: %s", url, exc)
    finally:
        conn.close()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

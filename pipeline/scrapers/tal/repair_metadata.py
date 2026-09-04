#!/usr/bin/env python3
"""Repair TAL episode title/date metadata from official episode pages.

Dry-run by default. Use --execute to write updates to Neon.
Targets:
- TAL rows missing title or publish_date
- TAL rows in duplicate show/title/date groups, where stale metadata is likely
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


REQUEST_TIMEOUT = 30
TAL_SLUG = "tal"


@dataclass
class PageMetadata:
    title: str | None
    publish_date: str | None
    episode_number: int | None


def load_environment() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def get_db_connection():
    """Connect to Neon database (delegates to common.get_db_connection)."""
    # This is a manual tool run by hand, not on the cron; its private copy had no connect
    # timeout, keepalives or retry — exactly the hand-run that would rediscover the 08-31
    # 41-minute hang. Lazy import so this file still runs as a script from its own directory.
    pipeline_dir = str(Path(__file__).resolve().parents[2])
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from common import get_db_connection as shared_connection

    return shared_connection()


def extract_meta_content(markup: str, key: str) -> str | None:
    escaped_key = re.escape(key)
    patterns = [
        rf"<meta\b(?=[^>]*(?:property|name)=['\"]{escaped_key}['\"])[^>]*content=['\"]([^'\"]+)['\"][^>]*>",
        rf"<meta\b(?=[^>]*content=['\"]([^'\"]+)['\"])[^>]*(?:property|name)=['\"]{escaped_key}['\"][^>]*>",
    ]
    for pattern in patterns:
        match = re.search(pattern, markup, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1)).strip()
    return None


def clean_title(value: str | None) -> str | None:
    if not value:
        return None
    title = html.unescape(value).strip()
    title = re.sub(r"\s+[-|]\s+This American Life\s*$", "", title, flags=re.I).strip()
    title = re.sub(r"\s+", " ", title)
    return title or None


def extract_title(markup: str) -> str | None:
    for key in ("og:title", "twitter:title"):
        title = clean_title(extract_meta_content(markup, key))
        if title:
            return title
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, flags=re.I | re.S)
    return clean_title(match.group(1)) if match else None


def extract_publish_date(markup: str) -> str | None:
    for key in ("article:published_time", "datePublished", "publish_date"):
        value = extract_meta_content(markup, key)
        if value:
            match = re.search(r"\d{4}-\d{2}-\d{2}", value)
            if match:
                return match.group(0)

    # JSON-LD pages often include "datePublished":"YYYY-MM-DD..."
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', markup)
    if match:
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", match.group(1))
        if date_match:
            return date_match.group(0)
    return None


def extract_episode_number(url: str) -> int | None:
    match = re.search(r"thisamericanlife\.org/(\d+)(?:/|$)", url)
    return int(match.group(1)) if match else None


def parse_page_metadata(url: str, markup: str) -> PageMetadata:
    return PageMetadata(
        title=extract_title(markup),
        publish_date=extract_publish_date(markup),
        episode_number=extract_episode_number(url),
    )


def fetch_page_metadata(url: str) -> PageMetadata:
    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "list-maker metadata repair (+https://github.com/khglynn/list-maker)"},
    )
    response.raise_for_status()
    return parse_page_metadata(url, response.text)


def get_candidate_rows(conn, limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        WITH duplicate_ids AS (
          SELECT UNNEST(ARRAY_AGG(e.id ORDER BY e.id)) AS id
          FROM episodes e
          JOIN shows s ON s.id = e.show_id
          WHERE s.slug = %s
            AND e.title IS NOT NULL
            AND BTRIM(e.title) <> ''
            AND e.publish_date IS NOT NULL
          GROUP BY LOWER(BTRIM(e.title)), e.publish_date
          HAVING COUNT(*) > 1
        )
        SELECT DISTINCT e.id, e.url, e.title, e.publish_date, e.episode_number
        FROM episodes e
        JOIN shows s ON s.id = e.show_id
        LEFT JOIN duplicate_ids d ON d.id = e.id
        WHERE s.slug = %s
          AND e.url IS NOT NULL
          AND (
            e.title IS NULL OR BTRIM(e.title) = ''
            OR e.publish_date IS NULL
            OR d.id IS NOT NULL
          )
        ORDER BY e.id
    """
    params: list[Any] = [TAL_SLUG, TAL_SLUG]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def update_episode(conn, episode_id: int, metadata: PageMetadata) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE episodes
            SET title = COALESCE(%s, title),
                publish_date = COALESCE(%s::date, publish_date),
                episode_number = COALESCE(%s, episode_number)
            WHERE id = %s;
            """,
            (metadata.title, metadata.publish_date, metadata.episode_number, episode_id),
        )


def describe_change(row: dict[str, Any], metadata: PageMetadata) -> list[str]:
    changes: list[str] = []
    current_date = row.get("publish_date")
    current_date_str = current_date.isoformat() if isinstance(current_date, date) else (
        str(current_date) if current_date else None
    )
    if metadata.title and metadata.title != row.get("title"):
        changes.append(f"title: {row.get('title')!r} -> {metadata.title!r}")
    if metadata.publish_date and metadata.publish_date != current_date_str:
        changes.append(f"publish_date: {current_date_str!r} -> {metadata.publish_date!r}")
    if metadata.episode_number and metadata.episode_number != row.get("episode_number"):
        changes.append(f"episode_number: {row.get('episode_number')!r} -> {metadata.episode_number!r}")
    return changes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair TAL episode metadata from official pages.")
    parser.add_argument("--execute", action="store_true", help="Write changes to Neon.")
    parser.add_argument("--limit", type=int, help="Limit candidate rows.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()

    conn = get_db_connection()
    changed = 0
    fetched = 0
    errors: list[str] = []
    try:
        rows = get_candidate_rows(conn, limit=args.limit)
        print(f"Candidate TAL rows: {len(rows)}")
        for row in rows:
            try:
                fetched += 1
                metadata = fetch_page_metadata(row["url"])
                changes = describe_change(row, metadata)
                if not changes:
                    continue
                changed += 1
                print(f"\n[{row['id']}] {row['url']}")
                for change in changes:
                    print(f"  {change}")
                if args.execute:
                    update_episode(conn, int(row["id"]), metadata)
            except Exception as exc:
                errors.append(f"{row['id']} {row['url']}: {exc}")

        if args.execute:
            conn.commit()
    finally:
        conn.close()

    print(f"\nFetched: {fetched}")
    print(f"Rows with changes: {changed}")
    print(f"Errors: {len(errors)}")
    for error in errors[:20]:
        print(f"  - {error}")
    if not args.execute:
        print("\nDry run only. Re-run with --execute to write these metadata repairs.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)

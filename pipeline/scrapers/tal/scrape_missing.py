#!/usr/bin/env python3
"""
TAL Missing Episode Scraper

Scrapes episodes that exist in DB but don't have JSON files.

Usage:
    python scrape_missing.py --dry-run           # Show what would be scraped
    python scrape_missing.py --limit 10          # Scrape first 10 missing
    python scrape_missing.py --execute           # Scrape all missing
    python scrape_missing.py --execute --skip-after 877  # Skip recent episodes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import requests
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv


def get_db_connection():
    """Connect to Neon database."""
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        cursor_factory=RealDictCursor,
    )


def get_missing_episodes(conn, json_dir: Path, skip_after_episode: int = None) -> list[dict]:
    """Get episodes that don't have JSON files.

    Args:
        conn: Database connection
        json_dir: Directory containing JSON files
        skip_after_episode: Skip episodes with numbers > this value
    """
    # Get all TAL episodes
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, episode_number, title, url
            FROM episodes
            WHERE show_id = 2 AND title IS NOT NULL
            ORDER BY id
        """)
        episodes = cur.fetchall()

    # Get existing JSON files
    existing_ids = set(int(f.stem) for f in json_dir.glob("*.json"))

    # Find missing
    missing = []
    for ep in episodes:
        if ep['id'] not in existing_ids:
            # Check skip_after filter
            if skip_after_episode and ep['episode_number'] > skip_after_episode:
                continue
            missing.append(ep)

    return missing


def scrape_url(url: str, api_key: str) -> dict | None:
    """Scrape a URL using Firecrawl.

    Returns the scraped data or None on error.
    """
    api_url = "https://api.firecrawl.dev/v1/scrape"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()

        if data.get("success"):
            return data.get("data", {})
        return None
    except Exception as e:
        print(f"  Scrape error for {url}: {e}")
        return None


def save_json(db_id: int, url: str, scraped_data: dict, output_dir: Path):
    """Save scraped data to JSON file."""
    json_data = {
        "db_id": db_id,
        "url": url,
        "success": True,
        "markdown": scraped_data.get("markdown", ""),
        "metadata": scraped_data.get("metadata", {}),
        "fetched_at": datetime.now().isoformat()
    }

    filepath = output_dir / f"{db_id}.json"
    with open(filepath, 'w') as f:
        json.dump(json_data, f, indent=2)

    return filepath


def main():
    parser = argparse.ArgumentParser(description="Scrape missing TAL episodes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be scraped without making changes")
    parser.add_argument("--execute", action="store_true",
                        help="Actually scrape (default is dry-run)")
    parser.add_argument("--limit", type=int,
                        help="Process only first N episodes")
    parser.add_argument("--dir", default="fetched/tal",
                        help="Output directory for JSON files")
    parser.add_argument("--skip-after", type=int, default=877,
                        help="Skip episodes after this number (default: 877)")
    args = parser.parse_args()

    # Load env
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    load_dotenv(project_root / ".env.local")

    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("Error: FIRECRAWL_API_KEY not found in environment")
        sys.exit(1)

    output_dir = script_dir / args.dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get missing episodes
    conn = get_db_connection()
    try:
        episodes = get_missing_episodes(conn, output_dir, args.skip_after)
        print(f"Found {len(episodes)} episodes without JSON files")

        if args.skip_after:
            print(f"(Skipping episodes after #{args.skip_after})")

        if args.limit:
            episodes = episodes[:args.limit]
            print(f"Processing first {args.limit} episodes")

        if not args.execute:
            print("\n--- DRY RUN (use --execute to scrape) ---\n")
            print("Sample episodes to scrape:")
            for ep in episodes[:10]:
                print(f"  [{ep['id']}] #{ep['episode_number']} - {ep['title'][:50]}...")
            if len(episodes) > 10:
                print(f"  ... and {len(episodes) - 10} more")
            return

        # Execute scraping
        print(f"\n--- SCRAPING {len(episodes)} EPISODES ---\n")

        success = 0
        failed = 0

        for i, ep in enumerate(episodes, 1):
            db_id = ep['id']
            ep_num = ep['episode_number']
            url = ep['url']
            title = ep['title'][:40] if ep['title'] else 'Untitled'

            print(f"[{i}/{len(episodes)}] #{ep_num} - {title}...")

            # Scrape the page
            scraped = scrape_url(url, api_key)

            if not scraped:
                print(f"  ❌ Scrape failed")
                failed += 1
                time.sleep(0.3)
                continue

            # Check if it's a 404
            status = scraped.get("metadata", {}).get("statusCode", 200)
            if status == 404:
                print(f"  ❌ Page returned 404")
                failed += 1
                time.sleep(0.3)
                continue

            # Save JSON
            filepath = save_json(db_id, url, scraped, output_dir)
            print(f"  ✅ Saved to {filepath.name}")

            success += 1

            # Rate limiting
            time.sleep(0.3)

        print(f"\n--- Summary ---")
        print(f"Success: {success}")
        print(f"Failed: {failed}")

        if success > 0:
            print(f"\nNext steps:")
            print(f"  1. Run: python tal_fill_songs.py --execute")
            print(f"  2. Run: python spotify_match.py --show-id 2")
            print(f"  3. Run: python sync_playlist.py --show-id 2")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

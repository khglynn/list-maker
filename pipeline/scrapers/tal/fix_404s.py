#!/usr/bin/env python3
"""
TAL 404 Episode Fixer

Finds correct URLs for 404 episodes using Firecrawl search,
scrapes them, and saves to JSON files.

Usage:
    python fix_404s.py --dry-run           # Show what would be fixed
    python fix_404s.py --limit 5           # Process first 5 episodes
    python fix_404s.py --execute           # Process all 404 episodes
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


def get_404_episodes(conn, skip_after_episode: int = 877) -> list[dict]:
    """Get all 404 episodes from database.

    Args:
        skip_after_episode: Skip episodes with numbers > this value (for cron testing)
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, url,
                   SUBSTRING(url FROM '/([0-9]+)/') as episode_number
            FROM episodes
            WHERE show_id = 2 AND title IS NULL
            ORDER BY id
        """)
        episodes = cur.fetchall()

    # Filter out episodes after the cutoff
    filtered = []
    for ep in episodes:
        ep_num = int(ep['episode_number']) if ep['episode_number'] else 0
        if ep_num <= skip_after_episode:
            filtered.append(ep)
        else:
            print(f"  Skipping episode {ep_num} (after cutoff {skip_after_episode})")

    return filtered


def search_tal_episode(episode_number: str, api_key: str) -> str | None:
    """Search TAL for the correct episode URL.

    Returns the episode page URL (not transcript) or None if not found.
    """
    url = "https://api.firecrawl.dev/v1/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "query": f"site:thisamericanlife.org {episode_number}",
        "limit": 10  # Get more results for reliability
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = data.get("data", [])

        # Look for the episode page (not transcript, not act pages)
        for result in results:
            result_url = result.get("url", "")
            # Match pattern: /123/slug (exactly, not /123/slug/act-one)
            if re.match(rf"https://www\.thisamericanlife\.org/{episode_number}/[a-z0-9-]+$", result_url):
                if "/transcript" not in result_url:
                    return result_url

        return None
    except Exception as e:
        print(f"  Search error for episode {episode_number}: {e}")
        return None


def try_existing_url(url: str, api_key: str) -> dict | None:
    """Try scraping the existing URL first.

    Returns scraped data if successful (status 200), None if 404 or error.
    """
    scraped = scrape_url(url, api_key)
    if scraped:
        status = scraped.get("metadata", {}).get("statusCode", 200)
        if status == 200:
            return scraped
    return None


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


def update_db_url(conn, db_id: int, new_url: str):
    """Update episode URL in database."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE episodes SET url = %s WHERE id = %s",
            (new_url, db_id)
        )


def main():
    parser = argparse.ArgumentParser(description="Fix TAL 404 episodes")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be fixed without making changes")
    parser.add_argument("--execute", action="store_true",
                        help="Actually process and save (default is dry-run)")
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

    # Get 404 episodes
    conn = get_db_connection()
    try:
        episodes = get_404_episodes(conn, args.skip_after)
        print(f"Found {len(episodes)} 404 episodes to fix")

        if args.limit:
            episodes = episodes[:args.limit]
            print(f"Processing first {args.limit} episodes")

        if not args.execute:
            print("\n--- DRY RUN (use --execute to process) ---\n")

        fixed = 0
        failed = 0
        skipped = 0

        for i, ep in enumerate(episodes, 1):
            db_id = ep['id']
            ep_num = ep['episode_number']
            old_url = ep['url']

            print(f"[{i}/{len(episodes)}] Episode {ep_num} (db_id: {db_id})")
            print(f"  URL: {old_url}")

            if not args.execute:
                print(f"  [DRY RUN] Would try existing URL, then search if needed")
                fixed += 1
                continue

            # Strategy 1: Try existing URL first (many were just temporary 404s)
            scraped = try_existing_url(old_url, api_key)
            final_url = old_url

            if scraped:
                print(f"  ✅ Existing URL works now")
            else:
                # Strategy 2: Search for correct URL
                print(f"  Existing URL still 404, searching...")
                correct_url = search_tal_episode(ep_num, api_key)

                if not correct_url:
                    print(f"  ❌ Could not find correct URL")
                    failed += 1
                    time.sleep(0.3)
                    continue

                if correct_url != old_url:
                    print(f"  Found different URL: {correct_url}")

                scraped = scrape_url(correct_url, api_key)
                final_url = correct_url

                if not scraped:
                    print(f"  ❌ Scrape failed")
                    failed += 1
                    time.sleep(0.3)
                    continue

                # Check if it's actually a 404
                status = scraped.get("metadata", {}).get("statusCode", 200)
                if status == 404:
                    print(f"  ❌ Page returned 404")
                    failed += 1
                    time.sleep(0.3)
                    continue

            # Save JSON
            filepath = save_json(db_id, final_url, scraped, output_dir)
            print(f"  ✅ Saved to {filepath.name}")

            # Update DB if URL changed
            if final_url != old_url:
                update_db_url(conn, db_id, final_url)
                print(f"  ✅ Updated URL in database")

            fixed += 1

            # Rate limiting - be nice to the API
            time.sleep(0.3)

        conn.commit()

        print(f"\n--- Summary ---")
        print(f"Fixed: {fixed}")
        print(f"Failed: {failed}")
        print(f"Skipped: {skipped}")

        if fixed > 0 and args.execute:
            print(f"\nNext steps:")
            print(f"  1. Run: python tal_fill_songs.py --execute")
            print(f"  2. Run: python spotify_match.py --show-id 2")
            print(f"  3. Run: python sync_playlist.py --show-id 2")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

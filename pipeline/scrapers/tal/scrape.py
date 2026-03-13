#!/usr/bin/env python3
"""
TAL (This American Life) Episode Scraper - Unified pipeline entry point.

Chains the existing TAL scripts: fetch → parse → fill_songs.
Designed to be called by the orchestrator (run_pipeline.py).

For the full individual scripts, see:
  - fetch.py: Fetches raw markdown via Firecrawl
  - parse.py: Parses episode JSON files
  - fill_songs.py: Inserts missing songs to database

Usage:
    python scrape.py --dry-run           # Preview what would be scraped
    python scrape.py --execute           # Fetch, parse, and insert
    python scrape.py --execute --yes     # No confirmation prompt
"""

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Import sibling modules
sys.path.insert(0, str(Path(__file__).parent))
from fetch import main as fetch_main, get_unscraped_episodes, get_already_fetched, OUTPUT_DIR
from parse import parse_episode
from fill_songs import (
    get_existing_songs,
    cleanup_existing_songs,
    fix_has_songs_flags,
    check_duplicates,
    remove_duplicates,
)


SHOW_ID = 2  # TAL is show_id=2


def get_db_connection():
    """Connect to Neon database."""
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in environment")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def scrape_new_episodes(
    dry_run: bool = True,
    limit: Optional[int] = None,
    yes: bool = False,
) -> dict:
    """
    Discover, fetch, parse, and insert new TAL episodes.

    Returns summary dict: {fetched, parsed, songs_inserted, errors}
    """
    summary = {
        "fetched": 0,
        "parsed": 0,
        "songs_inserted": 0,
        "errors": [],
    }

    # Step 1: Find unscraped episodes
    episodes = get_unscraped_episodes(limit)
    already_fetched = get_already_fetched()

    to_fetch = [e for e in episodes if e["id"] not in already_fetched]
    print(f"TAL: {len(episodes)} unscraped in DB, {len(already_fetched)} already fetched locally")
    print(f"  {len(to_fetch)} episodes need fetching")

    if dry_run:
        print(f"\n--- DRY RUN ---")
        if to_fetch:
            print(f"Would fetch {len(to_fetch)} episodes via Firecrawl")
            for ep in to_fetch[:5]:
                print(f"  {ep['id']}: {ep['url']}")
            if len(to_fetch) > 5:
                print(f"  ... and {len(to_fetch) - 5} more")

        # Check for unfilled songs in existing JSON files
        all_fetched = get_already_fetched()
        if all_fetched:
            print(f"\nWould parse {len(all_fetched)} cached JSON files for missing songs")

        return summary

    if not yes and to_fetch:
        print(f"\nAbout to fetch {len(to_fetch)} episodes via Firecrawl.")
        print("Press Enter to continue or Ctrl+C to abort...")
        try:
            input()
        except KeyboardInterrupt:
            print("\nAborted.")
            return summary

    # Step 2: Fetch new episodes via Firecrawl
    if to_fetch:
        print(f"\nFetching {len(to_fetch)} episodes...")
        asyncio.run(fetch_main(limit=limit, dry_run=False))
        summary["fetched"] = len(to_fetch)

    # Step 3: Parse all JSON files and find missing songs
    fetched_dir = OUTPUT_DIR
    if not fetched_dir.exists():
        print("No fetched JSON files found.")
        return summary

    json_files = sorted(fetched_dir.glob("*.json"))
    print(f"\nParsing {len(json_files)} JSON files...")

    parsed_data = []
    for filepath in json_files:
        try:
            result = parse_episode(filepath)
            if not result.get("is_404") and result.get("songs"):
                parsed_data.append(result)
        except Exception as e:
            summary["errors"].append(f"Parse {filepath.stem}: {e}")

    summary["parsed"] = len(parsed_data)
    total_parsed_songs = sum(len(ep["songs"]) for ep in parsed_data)
    print(f"  {len(parsed_data)} episodes with songs ({total_parsed_songs} total songs)")

    # Step 4: Insert missing songs to database
    conn = get_db_connection()
    try:
        episode_ids = [ep["db_id"] for ep in parsed_data]
        existing_songs = get_existing_songs(conn, episode_ids)
        total_existing = sum(len(songs) for songs in existing_songs.values())

        # Find missing songs
        missing_songs = []
        for ep in parsed_data:
            db_id = ep["db_id"]
            existing = existing_songs.get(db_id, set())
            for song in ep["songs"]:
                key = (song["title"], song["artist"])
                if key not in existing:
                    missing_songs.append((db_id, song["title"], song["artist"]))

        print(f"  {total_existing} songs already in DB, {len(missing_songs)} missing")

        if missing_songs:
            # Clean existing titles first
            cleaned = cleanup_existing_songs(conn)
            if cleaned:
                print(f"  Cleaned {cleaned} song titles (stripped quotes)")

                # Re-query after cleanup
                existing_songs = get_existing_songs(conn, episode_ids)
                missing_songs = []
                for ep in parsed_data:
                    db_id = ep["db_id"]
                    existing = existing_songs.get(db_id, set())
                    for song in ep["songs"]:
                        key = (song["title"], song["artist"])
                        if key not in existing:
                            missing_songs.append((db_id, song["title"], song["artist"]))

            # Insert missing songs
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO songs (episode_id, title, artist) VALUES (%s, %s, %s)",
                    missing_songs,
                )
            summary["songs_inserted"] = len(missing_songs)
            print(f"  Inserted {len(missing_songs)} songs")

        # Fix flags and remove duplicates
        fixed_true, fixed_false = fix_has_songs_flags(conn)
        if fixed_true or fixed_false:
            print(f"  Fixed has_songs flags: {fixed_true} set true, {fixed_false} set false")

        dupes = check_duplicates(conn)
        if dupes > 0:
            removed = remove_duplicates(conn)
            print(f"  Removed {removed} duplicate songs")

        conn.commit()

    finally:
        conn.close()

    print(f"\nTAL done! Fetched {summary['fetched']}, inserted {summary['songs_inserted']} songs")
    return summary


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI entry point."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent.parent

    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(project_root / ".env.local")

    parser = argparse.ArgumentParser(description="Scrape new TAL episodes")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--execute", action="store_true", help="Actually fetch and insert")
    parser.add_argument("--limit", type=int, help="Max episodes to fetch")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if not args.execute:
        args.dry_run = True

    scrape_new_episodes(dry_run=args.dry_run, limit=args.limit, yes=args.yes)


if __name__ == "__main__":
    main()

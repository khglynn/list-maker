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
import sys
from datetime import date
from pathlib import Path
from typing import Optional

# Import sibling modules
sys.path.insert(0, str(Path(__file__).parent))
from fetch import (
    main as fetch_main,
    get_already_fetched,
    plan_fetch,
    OUTPUT_DIR,
)
from parse import parse_episode
from fill_songs import (
    get_existing_songs,
    cleanup_existing_songs,
    fix_has_songs_flags,
    check_duplicates,
    remove_duplicates,
)


def get_db_connection():
    """Connect to Neon database (delegates to common.get_db_connection)."""
    # One implementation for the scheduled path — pipeline/common.py carries the connect
    # timeout, keepalives, and bounded retry. This module's private copy had none, and it
    # sits on pipeline.yml's Mon/Wed/Fri chain (rewired 2026-09-01 after the 08-31 41-minute
    # hang). Lazy import so this file still runs as a script from its own directory.
    pipeline_dir = str(Path(__file__).resolve().parents[2])
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from common import get_db_connection as shared_connection

    return shared_connection()


def scrape_new_episodes(
    dry_run: bool = True,
    limit: Optional[int] = None,
    yes: bool = False,
    published_since: Optional[date] = None,
) -> dict:
    """
    Fetch, parse, and insert songs for TAL episodes that don't have any yet.

    Returns summary dict: {fetched, parsed, songs_inserted, unresolved, errors}
    """
    summary = {
        "fetched": 0,
        "parsed": 0,
        "songs_inserted": 0,
        "unresolved": 0,
        "errors": [],
    }

    # Step 1: which episodes still need songs, and which page each one lives on.
    # Resolved ONCE here and handed to the fetcher, so the preview and the fetch cannot
    # disagree — and so the RSS is read once per run rather than once per caller.
    to_fetch, unresolved = plan_fetch(limit, published_since)
    already_fetched = get_already_fetched()

    print(f"TAL: {len(to_fetch) + len(unresolved)} episodes missing songs")
    print(f"  {len(to_fetch)} with a page url, {len(unresolved)} without")
    if already_fetched:
        # Reported, never subtracted. The local JSON cache is git-ignored and empty on a
        # CI runner, so it can't be the record of what has been read; the DB predicate in
        # get_episodes_missing_songs is. Treating it as authority used to mean a bad JSON
        # (e.g. of an api.taddy.org url) excluded that episode from every later run on
        # this machine. Cost of not skipping: an episode whose page genuinely lists no
        # songs is re-fetched each run — 2 of the 24 in the current queue.
        print(f"  ({len(already_fetched)} local JSON files cached from previous runs)")
    for ep in unresolved:
        summary["errors"].append(f"No page URL for {ep['id']}: {ep.get('title')!r}")
        print(f"  NO PAGE URL for {ep['id']}: {ep.get('title')!r}")
    summary["unresolved"] = len(unresolved)

    if dry_run:
        print(f"\n--- DRY RUN ---")
        if to_fetch:
            print(f"Would fetch {len(to_fetch)} episodes via Firecrawl")
            for ep in to_fetch[:5]:
                print(f"  {ep['id']}: {ep['page_url']}")
            if len(to_fetch) > 5:
                print(f"  ... and {len(to_fetch) - 5} more")

        if already_fetched:
            print(f"\nWould parse {len(already_fetched)} cached JSON files for missing songs")

        return summary

    if not yes and to_fetch:
        print(f"\nAbout to fetch {len(to_fetch)} episodes via Firecrawl.")
        print("Press Enter to continue or Ctrl+C to abort...")
        try:
            input()
        except KeyboardInterrupt:
            print("\nAborted.")
            return summary

    # Step 2: Fetch those pages via Firecrawl
    if to_fetch:
        print(f"\nFetching {len(to_fetch)} episodes...")
        asyncio.run(fetch_main(episodes=to_fetch, dry_run=False))
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
    # The shared loader, same as fetch.py's. The hand-rolled pair this replaced read
    # ~/.env and the repo's .env.local but not pipeline/.env.local, so a standalone run
    # saw a different environment than the orchestrator that normally calls in.
    from common import load_environment

    load_environment()

    parser = argparse.ArgumentParser(description="Scrape songs for TAL episodes missing them")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--execute", action="store_true", help="Actually fetch and insert")
    parser.add_argument("--limit", type=int, help="Max episodes to fetch")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Date floor for the queue (default fetch.DEFAULT_SONG_SCRAPE_FLOOR)",
    )
    args = parser.parse_args()

    if not args.execute:
        args.dry_run = True

    scrape_new_episodes(
        dry_run=args.dry_run, limit=args.limit, yes=args.yes, published_since=args.since
    )


if __name__ == "__main__":
    main()

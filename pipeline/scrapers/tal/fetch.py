#!/usr/bin/env python3
"""
TAL Episode Fetcher - Dumb Fetch, Smart Parse Strategy

This script ONLY fetches raw HTML/markdown from TAL episode URLs.
It does NOT parse or interpret the content - that's for Claude to do.

Usage:
    python tal_fetch.py              # Fetch all unscraped episodes
    python tal_fetch.py --limit 50   # Fetch up to 50 episodes
    python tal_fetch.py --dry-run    # Show what would be fetched

Output:
    JSON files in fetched/tal/{db_id}.json containing:
    - db_id: Database row ID (NOT the TAL episode number)
    - url: The episode URL
    - markdown: Full page content
    - metadata: All metadata from Firecrawl
    - fetched_at: Timestamp
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# =============================================================================
# Configuration
# =============================================================================

MAX_CONCURRENT = 5  # Firecrawl hobby tier limit
FIRECRAWL_TIMEOUT = 30  # seconds per request
OUTPUT_DIR = Path(__file__).parent / "fetched" / "tal"

# =============================================================================
# Database
# =============================================================================

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


def get_unscraped_episodes(limit: int = None) -> list[dict]:
    """Get episodes that haven't been scraped yet."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT id, url
                FROM episodes
                WHERE show_id = 2 AND scraped_at IS NULL AND url IS NOT NULL
                ORDER BY id
            """
            if limit:
                sql += f" LIMIT {limit}"
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_already_fetched() -> set[int]:
    """Get episode IDs that already have JSON files."""
    if not OUTPUT_DIR.exists():
        return set()

    fetched = set()
    for f in OUTPUT_DIR.glob("*.json"):
        try:
            episode_id = int(f.stem)
            fetched.add(episode_id)
        except ValueError:
            pass
    return fetched


# =============================================================================
# Firecrawl
# =============================================================================

async def fetch_episode(
    client: httpx.AsyncClient,
    episode_id: int,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Fetch a single episode via Firecrawl API."""
    async with semaphore:
        try:
            response = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                json={
                    "url": url,
                    "formats": ["markdown"],
                },
                timeout=FIRECRAWL_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            return {
                "db_id": episode_id,  # Database row ID, NOT the TAL episode number
                "url": url,
                "success": True,
                "markdown": data.get("data", {}).get("markdown", ""),
                "metadata": data.get("data", {}).get("metadata", {}),
                "fetched_at": datetime.now().isoformat(),
            }
        except httpx.TimeoutException:
            return {
                "db_id": episode_id,
                "url": url,
                "success": False,
                "error": "Timeout",
                "fetched_at": datetime.now().isoformat(),
            }
        except httpx.HTTPStatusError as e:
            return {
                "db_id": episode_id,
                "url": url,
                "success": False,
                "error": f"HTTP {e.response.status_code}",
                "fetched_at": datetime.now().isoformat(),
            }
        except Exception as e:
            return {
                "db_id": episode_id,
                "url": url,
                "success": False,
                "error": str(e),
                "fetched_at": datetime.now().isoformat(),
            }


def save_result(result: dict):
    """Save fetch result to JSON file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = OUTPUT_DIR / f"{result['db_id']}.json"
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)


# =============================================================================
# Main
# =============================================================================

async def main(limit: int = None, dry_run: bool = False):
    """Fetch all unscraped TAL episodes."""

    # Get episodes to fetch
    episodes = get_unscraped_episodes(limit)
    print(f"Found {len(episodes)} unscraped episodes in database")

    # Skip already fetched
    already_fetched = get_already_fetched()
    if already_fetched:
        print(f"Skipping {len(already_fetched)} already fetched (JSON exists)")
        episodes = [e for e in episodes if e["id"] not in already_fetched]

    if not episodes:
        print("Nothing to fetch!")
        return

    print(f"Will fetch {len(episodes)} episodes")

    if dry_run:
        print("\nDry run - would fetch:")
        for ep in episodes[:10]:
            print(f"  {ep['id']}: {ep['url']}")
        if len(episodes) > 10:
            print(f"  ... and {len(episodes) - 10} more")
        return

    # Fetch with concurrency limit
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("Error: FIRECRAWL_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    success_count = 0
    error_count = 0

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        # Process in batches for progress reporting
        batch_size = 20
        for i in range(0, len(episodes), batch_size):
            batch = episodes[i:i + batch_size]

            tasks = [
                fetch_episode(client, ep["id"], ep["url"], semaphore)
                for ep in batch
            ]

            results = await asyncio.gather(*tasks)

            for result in results:
                save_result(result)
                if result["success"]:
                    success_count += 1
                else:
                    error_count += 1
                    print(f"  Error: {result['db_id']} - {result.get('error', 'Unknown')}")

            print(f"Progress: {i + len(batch)}/{len(episodes)} ({success_count} ok, {error_count} errors)")

    print(f"\nDone! Fetched {success_count} episodes, {error_count} errors")
    print(f"JSON files saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    # Load env vars
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # Firecrawl API key
    load_dotenv(os.path.expanduser("~/.env"))

    # Database URL
    load_dotenv(project_root / ".env.local")

    parser = argparse.ArgumentParser(description="Fetch TAL episodes via Firecrawl")
    parser.add_argument("--limit", type=int, help="Max episodes to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched")
    args = parser.parse_args()

    asyncio.run(main(limit=args.limit, dry_run=args.dry_run))

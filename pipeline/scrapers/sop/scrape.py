#!/usr/bin/env python3
"""
SOP (Switched On Pop) Episode Scraper - Python version.

Discovers new episodes from switchedonpop.com/episodes, scrapes each page,
parses "Songs Discussed" sections, and inserts to Neon database.

Port of web/src/lib/scraper/sop.ts for use in the automated pipeline.

Usage:
    python scrape.py --dry-run           # Preview what would be scraped
    python scrape.py --limit 10          # Scrape up to 10 new episodes
    python scrape.py --execute           # Scrape and insert to DB
    python scrape.py --execute --yes     # No confirmation prompt
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# =============================================================================
# Constants
# =============================================================================

SOP_BASE_URL = "https://switchedonpop.com"
SOP_EPISODES_URL = f"{SOP_BASE_URL}/episodes"
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1"
SCRAPE_DELAY = 0.5  # Seconds between requests (be nice to the server)
SHOW_ID = 1  # SOP is show_id=1 in the database


# =============================================================================
# Database
# =============================================================================

def get_db_connection():
    """Connect to Neon database."""
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set in environment")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def get_existing_episode_urls(conn) -> set:
    """Get all episode URLs already in the database for SOP."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT url FROM episodes WHERE show_id = %s AND url IS NOT NULL",
            (SHOW_ID,),
        )
        return {row["url"] for row in cur.fetchall()}


def insert_episode(conn, episode: dict) -> int:
    """Insert or update an episode. Returns episode ID."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO episodes (show_id, title, url, publish_date, raw_content,
                                  has_songs_discussed, description_body, scraped_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (url) DO UPDATE SET
                title = EXCLUDED.title,
                raw_content = EXCLUDED.raw_content,
                has_songs_discussed = EXCLUDED.has_songs_discussed,
                description_body = EXCLUDED.description_body,
                scraped_at = NOW()
            RETURNING id
        """, (
            SHOW_ID,
            episode["title"],
            episode["url"],
            episode.get("publish_date"),
            episode.get("raw_content"),
            episode.get("has_songs_discussed"),
            episode.get("description_body"),
        ))
        return cur.fetchone()["id"]


def insert_songs(conn, episode_id: int, songs: list[dict]) -> int:
    """Insert songs for an episode. Skips duplicates. Returns count inserted."""
    if not songs:
        return 0

    # Get existing songs for this episode to avoid duplicates
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, artist FROM songs WHERE episode_id = %s",
            (episode_id,),
        )
        existing = {(row["title"], row["artist"]) for row in cur.fetchall()}

    new_songs = [
        (episode_id, s["title"], s["artist"])
        for s in songs
        if (s["title"], s["artist"]) not in existing
    ]

    if not new_songs:
        return 0

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO songs (episode_id, title, artist) VALUES (%s, %s, %s)",
            new_songs,
        )

    return len(new_songs)


# =============================================================================
# Firecrawl
# =============================================================================

def scrape_url(url: str, api_key: str) -> dict:
    """Scrape a URL via Firecrawl API. Returns {markdown, metadata}."""
    response = httpx.post(
        f"{FIRECRAWL_API_URL}/scrape",
        json={"url": url, "formats": ["markdown"]},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    return {
        "markdown": data.get("markdown", ""),
        "metadata": data.get("metadata", {}),
    }


# =============================================================================
# Parsing (ported from sop.ts)
# =============================================================================

def parse_episode_list(markdown: str) -> list[dict]:
    """Parse episode list page to extract episode URLs, titles, and dates."""
    episodes = []

    # Pattern: # [Title](URL)
    pattern = re.compile(
        r'# \[([^\]]+)\]\((https://switchedonpop\.com/episodes/[^)]+)\)'
    )

    for match in pattern.finditer(markdown):
        title = match.group(1)
        url = match.group(2)

        # Try to extract date from text before the title (format: MM/DD/YY)
        before_title = markdown[max(0, match.start() - 100):match.start()]
        date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2})', before_title)

        publish_date = None
        if date_match:
            try:
                publish_date = datetime.strptime(date_match.group(1), "%m/%d/%y").date()
            except ValueError:
                pass

        episodes.append({
            "title": title,
            "url": url,
            "publish_date": publish_date,
        })

    return episodes


def parse_songs_discussed(markdown: str) -> dict:
    """
    Parse an episode page to extract songs discussed.

    Handles multiple formats:
    - "- Artist -- Song Title" (bullet + en-dash)
    - 'Artist "Song Title"' (quotes around song)

    Returns: {songs: [{artist, title}], has_songs_section: bool}
    """
    songs = []

    # Find the "Songs Discussed" section (case-insensitive)
    songs_section_match = re.search(
        r'\*\*Songs Discussed\*\*([\s\S]*?)(?=\n\n\[|$)', markdown, re.IGNORECASE
    )
    if not songs_section_match:
        return {"songs": [], "has_songs_section": False}

    songs_text = songs_section_match.group(1)

    # Pattern 1: - Artist -- Song Title (bullet + en-dash or hyphen)
    dash_pattern = re.compile(r'- ([^\u2013\-\n]+)[\u2013-]\s*([^\n]+)')

    for match in dash_pattern.finditer(songs_text):
        artist = match.group(1).strip()
        title = match.group(2).strip()
        if artist and title and "Previous" not in title and "Next" not in title:
            songs.append({"artist": artist, "title": title})

    # If no songs found with dash pattern, try quote pattern
    if not songs:
        # Pattern 2: Artist "Song Title" (quotes around song, one per line)
        # Handle both straight quotes and curly quotes
        quote_pattern = re.compile(
            r'^([^"\u201c\u201d\n]+)\s+["\u201c]([^"\u201c\u201d]+)["\u201d]$',
            re.MULTILINE,
        )
        for match in quote_pattern.finditer(songs_text):
            artist = match.group(1).strip()
            title = match.group(2).strip()
            if artist and title and "(Album)" not in title and not artist.startswith("_"):
                songs.append({"artist": artist, "title": title})

    return {"songs": songs, "has_songs_section": True}


def parse_description_body(markdown: str) -> str:
    """Extract the description body (content before Songs Discussed section)."""
    body = markdown

    # Remove everything up to and including the episode title header
    title_match = re.search(r'^#\s+[^\n]+\n', body, re.MULTILINE)
    if title_match:
        body = body[title_match.end():]

    # Remove the "Songs Discussed" section and everything after
    songs_idx = re.search(r'\*\*Songs Discussed\*\*', body, re.IGNORECASE)
    if songs_idx:
        body = body[:songs_idx.start()]

    # Remove footer elements
    for pattern in [
        r'\[Previous[\s\S]*$',
        r'\[!\[Apple-Podcasts[\s\S]*$',
        r'Switched On Pop \\\| Substack[\s\S]*$',
    ]:
        body = re.sub(pattern, '', body)

    return body.strip()


# =============================================================================
# Main Pipeline
# =============================================================================

def scrape_new_episodes(
    dry_run: bool = True,
    limit: Optional[int] = None,
    yes: bool = False,
) -> dict:
    """
    Discover and scrape new SOP episodes.

    Returns summary dict: {discovered, skipped, scraped, songs_found, errors}
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY not set in environment")

    conn = get_db_connection()

    try:
        # Step 1: Scrape the episode list page
        print("Scraping SOP episode list...")
        list_result = scrape_url(SOP_EPISODES_URL, api_key)
        all_episodes = parse_episode_list(list_result["markdown"])
        print(f"  Found {len(all_episodes)} episodes on list page")

        # Step 2: Filter to only new episodes
        existing_urls = get_existing_episode_urls(conn)
        new_episodes = [ep for ep in all_episodes if ep["url"] not in existing_urls]
        print(f"  {len(new_episodes)} new episodes (not in database)")

        if limit:
            new_episodes = new_episodes[:limit]
            print(f"  Limited to {len(new_episodes)} episodes")

        summary = {
            "discovered": len(all_episodes),
            "skipped": len(all_episodes) - len(new_episodes),
            "scraped": 0,
            "songs_found": 0,
            "errors": [],
        }

        if not new_episodes:
            print("No new episodes to scrape.")
            return summary

        if dry_run:
            print(f"\n--- DRY RUN: would scrape {len(new_episodes)} episodes ---")
            for ep in new_episodes[:10]:
                print(f"  {ep['title']}")
            if len(new_episodes) > 10:
                print(f"  ... and {len(new_episodes) - 10} more")
            return summary

        # Confirmation prompt
        if not yes:
            print(f"\nAbout to scrape {len(new_episodes)} new episodes.")
            print("Press Enter to continue or Ctrl+C to abort...")
            try:
                input()
            except KeyboardInterrupt:
                print("\nAborted.")
                return summary

        # Step 3: Scrape each new episode
        for i, ep in enumerate(new_episodes):
            try:
                print(f"  [{i+1}/{len(new_episodes)}] {ep['title'][:60]}...", end="")

                page = scrape_url(ep["url"], api_key)
                parsed = parse_songs_discussed(page["markdown"])
                desc_body = parse_description_body(page["markdown"])

                # Insert episode
                episode_id = insert_episode(conn, {
                    "title": ep["title"],
                    "url": ep["url"],
                    "publish_date": ep.get("publish_date"),
                    "raw_content": page["markdown"],
                    "has_songs_discussed": parsed["has_songs_section"],
                    "description_body": desc_body,
                })

                # Insert songs
                song_count = insert_songs(conn, episode_id, parsed["songs"])
                summary["songs_found"] += song_count
                summary["scraped"] += 1

                print(f" {len(parsed['songs'])} songs")

                conn.commit()
                time.sleep(SCRAPE_DELAY)

            except Exception as e:
                error_msg = f"{ep['title']}: {e}"
                print(f" ERROR: {e}")
                summary["errors"].append(error_msg)
                conn.rollback()

        print(f"\nDone! Scraped {summary['scraped']} episodes, "
              f"found {summary['songs_found']} songs, "
              f"{len(summary['errors'])} errors")

        return summary

    finally:
        conn.close()


# =============================================================================
# CLI
# =============================================================================

def main():
    """CLI entry point."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent.parent  # pipeline/scrapers/sop -> project root

    # Load env vars
    load_dotenv(os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.env"))
    load_dotenv(project_root / ".env.local")

    parser = argparse.ArgumentParser(description="Scrape new SOP episodes")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--execute", action="store_true", help="Actually scrape and insert")
    parser.add_argument("--limit", type=int, help="Max episodes to scrape")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if not args.execute:
        args.dry_run = True

    scrape_new_episodes(dry_run=args.dry_run, limit=args.limit, yes=args.yes)


if __name__ == "__main__":
    main()

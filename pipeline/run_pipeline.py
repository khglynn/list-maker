#!/usr/bin/env python3
"""
list-maker Pipeline Orchestrator

Runs the full pipeline for a given show:
  discover new episodes → scrape pages → parse content → insert to DB →
  match to Spotify → sync playlist → update description → output summary

Supports three shows:
  1 = SOP (Switched On Pop) - music extraction from website
  2 = TAL (This American Life) - music extraction from website
  3 = AI Daily Brief - entity extraction from transcripts (no Spotify sync)

Usage:
    python run_pipeline.py --show-id 1                    # SOP (interactive)
    python run_pipeline.py --show-id 1 --dry-run          # Preview only
    python run_pipeline.py --show-id 1 --yes              # No prompts (CI mode)
    python run_pipeline.py --show-id 1 --yes --cache-path .cache  # CI with custom cache
    python run_pipeline.py --show-id all --yes            # Run all shows

Environment variables (loaded from .env files locally, from secrets in CI):
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
    DATABASE_URL (or NEON_DATABASE_URL)
    FIRECRAWL_API_KEY
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


# =============================================================================
# Show Configuration
# =============================================================================

SHOWS = {
    1: {"name": "Switched On Pop", "slug": "sop", "type": "music"},
    2: {"name": "This American Life", "slug": "tal", "type": "music"},
    3: {"name": "AI Daily Brief", "slug": "ai_daily", "type": "entities"},
}


# =============================================================================
# Pipeline Steps
# =============================================================================

def run_scrape(show_id: int, dry_run: bool, yes: bool) -> dict:
    """Run the scraping step for a show. Returns scrape summary."""
    if show_id == 1:
        # SOP scraper
        sys.path.insert(0, str(Path(__file__).parent / "scrapers" / "sop"))
        from scrapers.sop.scrape import scrape_new_episodes
        return scrape_new_episodes(dry_run=dry_run, yes=yes)

    elif show_id == 2:
        # TAL scraper
        sys.path.insert(0, str(Path(__file__).parent / "scrapers" / "tal"))
        from scrapers.tal.scrape import scrape_new_episodes
        return scrape_new_episodes(dry_run=dry_run, yes=yes)

    elif show_id == 3:
        # AI Daily - entity extraction (different pipeline)
        print("AI Daily: Entity extraction pipeline")
        print("  (Transcript fetch + extraction not yet automated)")
        return {"status": "skipped", "reason": "AI Daily automation not yet implemented"}

    else:
        raise ValueError(f"Unknown show_id: {show_id}")


def run_match(show_id: int, dry_run: bool, cache_path: str = None) -> dict:
    """Run Spotify matching for a show. Returns match counts."""
    from spotify_match import match_songs_for_show
    return match_songs_for_show(
        show_id=show_id,
        dry_run=dry_run,
        yes=True,  # Always skip prompts in orchestrator
        cache_path=cache_path,
    )


def run_sync(show_id: int, dry_run: bool, cache_path: str = None) -> dict:
    """Run Spotify playlist sync for a show. Returns sync stats."""
    from sync_playlist import sync_show
    return sync_show(
        show_id=show_id,
        dry_run=dry_run,
        cache_path=cache_path,
    )


# =============================================================================
# Orchestrator
# =============================================================================

def run_pipeline(
    show_id: int,
    dry_run: bool = False,
    yes: bool = False,
    cache_path: str = None,
) -> dict:
    """
    Run the full pipeline for a single show.

    Returns a summary dict suitable for JSON output.
    """
    show = SHOWS.get(show_id)
    if not show:
        raise ValueError(f"Unknown show_id: {show_id}. Valid: {list(SHOWS.keys())}")

    started_at = datetime.utcnow().isoformat()
    print("\n" + "=" * 60)
    print(f"PIPELINE: {show['name']} (show_id={show_id})")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print("=" * 60)

    summary = {
        "show_id": show_id,
        "show_name": show["name"],
        "show_slug": show["slug"],
        "started_at": started_at,
        "dry_run": dry_run,
        "steps": {},
        "success": True,
        "error": None,
    }

    # Step 1: Scrape new episodes
    try:
        print(f"\n--- Step 1: Scrape new episodes ---")
        scrape_result = run_scrape(show_id, dry_run, yes)
        summary["steps"]["scrape"] = scrape_result
    except Exception as e:
        summary["steps"]["scrape"] = {"error": str(e)}
        summary["success"] = False
        summary["error"] = f"Scrape failed: {e}"
        print(f"\nERROR in scraping: {e}")
        traceback.print_exc()
        return summary

    # Step 2 & 3: Spotify match + sync (only for music shows)
    if show["type"] == "music":
        # Step 2: Match songs to Spotify
        try:
            print(f"\n--- Step 2: Match songs to Spotify ---")
            match_result = run_match(show_id, dry_run, cache_path)
            summary["steps"]["match"] = match_result
        except Exception as e:
            summary["steps"]["match"] = {"error": str(e)}
            summary["success"] = False
            summary["error"] = f"Matching failed: {e}"
            print(f"\nERROR in matching: {e}")
            traceback.print_exc()
            return summary

        # Step 3: Sync playlist
        try:
            print(f"\n--- Step 3: Sync Spotify playlist ---")
            sync_result = run_sync(show_id, dry_run, cache_path)
            summary["steps"]["sync"] = sync_result
        except Exception as e:
            summary["steps"]["sync"] = {"error": str(e)}
            summary["success"] = False
            summary["error"] = f"Playlist sync failed: {e}"
            print(f"\nERROR in sync: {e}")
            traceback.print_exc()
            return summary

    summary["completed_at"] = datetime.utcnow().isoformat()

    # Print final summary
    print("\n" + "=" * 60)
    print(f"COMPLETE: {show['name']}")
    print("=" * 60)

    if show["type"] == "music":
        scrape = summary["steps"].get("scrape", {})
        match = summary["steps"].get("match", {})
        sync = summary["steps"].get("sync", {})

        # Handle different scrape summary shapes (SOP vs TAL)
        episodes_scraped = scrape.get("scraped", scrape.get("fetched", 0))
        songs_found = scrape.get("songs_found", scrape.get("songs_inserted", 0))

        print(f"  Episodes scraped: {episodes_scraped}")
        print(f"  Songs found: {songs_found}")
        print(f"  Matched - HIGH: {match.get('high', 0)}, "
              f"MEDIUM: {match.get('medium', 0)}, "
              f"LOW: {match.get('low', 0)}, "
              f"NOT_FOUND: {match.get('not_found', 0)}")
        print(f"  Tracks added to playlist: {sync.get('added', 0)}")

    return summary


# =============================================================================
# CLI
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="list-maker Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Shows:
  1  SOP (Switched On Pop) - music from website
  2  TAL (This American Life) - music from website
  3  AI Daily Brief - entities from transcripts
  all  Run all shows sequentially
        """,
    )
    parser.add_argument(
        "--show-id",
        required=True,
        help="Show ID (1, 2, 3) or 'all' for all shows",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, no database writes or API calls",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip all confirmation prompts (required for CI)",
    )
    parser.add_argument(
        "--cache-path",
        help="Path to Spotify OAuth cache file (for CI environments)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary as JSON (for GitHub Actions)",
    )
    return parser.parse_args()


def load_env():
    """Load environment variables from local .env files."""
    project_root = Path(__file__).parent.parent

    # Spotify credentials
    spotify_env = os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.env")
    if os.path.exists(spotify_env):
        load_dotenv(spotify_env)

    # Project-specific vars (DATABASE_URL, FIRECRAWL_API_KEY)
    env_local = project_root / ".env.local"
    if env_local.exists():
        load_dotenv(env_local)

    # Also check ~/.env for Firecrawl key
    home_env = os.path.expanduser("~/.env")
    if os.path.exists(home_env):
        load_dotenv(home_env)


def main():
    """CLI entry point."""
    args = parse_args()

    # Load env vars (only from files - in CI these come from GitHub secrets)
    load_env()

    # Determine which shows to run
    if args.show_id == "all":
        show_ids = list(SHOWS.keys())
    else:
        try:
            show_ids = [int(args.show_id)]
        except ValueError:
            print(f"Error: --show-id must be a number (1-3) or 'all'", file=sys.stderr)
            sys.exit(1)

    # Run pipeline for each show
    all_summaries = []
    any_failed = False

    for show_id in show_ids:
        try:
            summary = run_pipeline(
                show_id=show_id,
                dry_run=args.dry_run,
                yes=args.yes,
                cache_path=args.cache_path,
            )
            all_summaries.append(summary)
            if not summary["success"]:
                any_failed = True
        except Exception as e:
            all_summaries.append({
                "show_id": show_id,
                "success": False,
                "error": str(e),
            })
            any_failed = True

    # Output JSON summary if requested
    if args.json:
        output = {
            "summaries": all_summaries,
            "all_success": not any_failed,
            "timestamp": datetime.utcnow().isoformat(),
        }
        print("\n--- JSON SUMMARY ---")
        print(json.dumps(output, indent=2))

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

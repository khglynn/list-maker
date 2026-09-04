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
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent
# Allow `from common import ...` both when run as a script from pipeline/ and when
# imported as pipeline.run_pipeline (tests) — same pattern as run_new_episodes.py.
sys.path.insert(0, str(PIPELINE_DIR))
from common import get_db_connection  # noqa: E402

# Prefer the project venv locally; fall back to the running interpreter — CI runs
# on the runner's Python (deps installed there) where the venv path doesn't exist.
_VENV_PYTHON = PIPELINE_DIR / "venv" / "bin" / "python"
VENV_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

TAL_SHOW_ID = 2


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

def count_tal_episodes() -> int:
    """Row count for TAL, used to report how many episodes discovery actually added."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM episodes WHERE show_id = %s;", (TAL_SHOW_ID,))
            return int(cur.fetchone()["n"])
    finally:
        conn.close()


def discover_tal_episodes(dry_run: bool, limit: int = 25) -> dict:
    """Insert newly published TAL episodes into `episodes` via the Taddy importer.

    TAL had no discovery step at all. Its scraper starts from
    `get_episodes_missing_songs` (scrapers/tal/fetch.py), which reads rows ALREADY in
    the table — so it can only fill songs for episodes something else inserted. SOP
    discovers by diffing its episode-list page (scrapers/sop/scrape.py:271), which is
    why SOP stayed current while TAL silently froze at 2026-05-17 and drifted 6
    episodes behind its feed. Every Monday run still reported success in under a
    minute, because finding no work is not an error.

    The Taddy importer is already the discovery mechanism for every show with a
    taddy_uuid, and it upserts ON CONFLICT (url), so re-running is safe.

    IT ALSO STAMPS `scraped_at` (import_transcripts.py:364 and :397) — on the INSERT
    that creates the row, and on the title+date dedup UPDATE of a row that already
    existed. That is harmless for the shows this importer was written for, and it was
    NOT harmless here: it emptied the TAL song scrape's old `scraped_at IS NULL` queue
    from the day this step was added (2026-08-02) until the queue was rewritten to ask
    "has this episode got songs" instead. Adding a discovery step to another
    website-scraped show means checking what its scraper keys on first.
    """
    if dry_run:
        print("  [dry-run] would import new TAL episodes from Taddy")
        return {"discovered": 0, "dry_run": True}

    before = count_tal_episodes()
    result = subprocess.run(
        [
            VENV_PYTHON,
            str(PIPELINE_DIR / "scrapers" / "taddy" / "import_transcripts.py"),
            "--shows", "tal",
            "--per-show-limit", str(limit),
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    print(result.stdout[-2000:] if result.stdout else "")
    if result.returncode != 0:
        # Surface it: a silent discovery failure is exactly how we got here.
        raise RuntimeError(
            f"TAL episode discovery failed (exit {result.returncode}): "
            f"{(result.stderr or '')[-500:]}"
        )

    # Report the row delta rather than parsing the importer's stdout — the count is
    # what actually happened, and "discovered 0" every week is the signal that this
    # step has stopped working. Silence is what cost us ten weeks.
    discovered = count_tal_episodes() - before
    print(f"  Discovered {discovered} new TAL episode(s)")
    return {"discovered": discovered}


def run_scrape(show_id: int, dry_run: bool, yes: bool) -> dict:
    """Run the scraping step for a show. Returns scrape summary."""
    if show_id == 1:
        # SOP scraper
        sys.path.insert(0, str(Path(__file__).parent / "scrapers" / "sop"))
        from scrapers.sop.scrape import scrape_new_episodes
        return scrape_new_episodes(dry_run=dry_run, yes=yes)

    elif show_id == 2:
        # TAL: discover first (insert new episode rows), then scrape songs for them.
        discovery = discover_tal_episodes(dry_run)
        sys.path.insert(0, str(Path(__file__).parent / "scrapers" / "tal"))
        from scrapers.tal.scrape import scrape_new_episodes
        return {**scrape_new_episodes(dry_run=dry_run, yes=yes), **discovery}

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

        # Only shows with a discovery step report this. A run that scrapes 0 because
        # it discovered 0 looks identical to "nothing published" without this line.
        if "discovered" in scrape:
            print(f"  Episodes discovered: {scrape['discovered']}")
        print(f"  Episodes scraped: {episodes_scraped}")
        # Episodes we know need songs but could not find a page for (TAL only today).
        # Printed only when non-zero, and printed at all because a skipped episode that
        # nothing mentions is indistinguishable from an episode that didn't need doing —
        # the exact confusion that let the TAL scrape sit dead for eight months.
        if scrape.get("unresolved"):
            print(f"  Episodes SKIPPED (no page URL found): {scrape['unresolved']}")
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

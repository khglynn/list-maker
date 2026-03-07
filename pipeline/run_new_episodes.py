#!/usr/bin/env python3
"""
Fetch new episodes and process them through the full pipeline.

Per-show pipeline:
1. Import new episodes + transcripts from Taddy
2. Extract entities (for entity-type shows like AI Daily)
3. Normalize aliases (dedup)
4. Sync to Notion (for shows with Notion DBs)
5. Sync to Spotify (for shows with playlists)

Usage:
    python run_new_episodes.py --shows ai-daily-brief --dry-run
    python run_new_episodes.py --shows ai-daily-brief
    python run_new_episodes.py --shows ai-daily-brief,sop
    python run_new_episodes.py --all
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Allow imports from pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_environment, get_db_connection
from show_config import SHOWS, get_show, ShowConfig

PIPELINE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = str(PIPELINE_DIR / "venv" / "bin" / "python")
SCRAPERS_DIR = PIPELINE_DIR / "scrapers"


def run_script(script_path: str, args: list[str], dry_run: bool, label: str) -> bool:
    """Run a pipeline script as a subprocess. Returns True on success."""
    cmd = [VENV_PYTHON, script_path] + args
    if dry_run:
        print(f"  [dry-run] Would run: {' '.join(cmd)}")
        return True

    print(f"  Running: {label}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  FAILED ({label}):")
        print(f"  stderr: {result.stderr[-500:]}" if result.stderr else "  (no stderr)")
        return False
    # Print last few lines of output
    lines = result.stdout.strip().split("\n")
    for line in lines[-5:]:
        print(f"    {line}")
    return True


def find_unextracted_episodes(conn, show_id: int) -> list[int]:
    """Find episodes that have transcripts but no entity extraction run."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ep.id
            FROM episodes ep
            JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.show_id = %s
              AND ep.id NOT IN (
                  SELECT DISTINCT m.episode_id FROM ai_mentions m
              )
            ORDER BY ep.id;
            """,
            (show_id,),
        )
        return [row["id"] for row in cur.fetchall()]


def step_taddy_import(cfg: ShowConfig, dry_run: bool, per_show_limit: int = 50) -> bool:
    """Step 1: Import new episodes + transcripts from Taddy."""
    if not cfg.taddy_uuid:
        print(f"  Skipping Taddy import (no UUID for {cfg.slug})")
        return True

    script = str(SCRAPERS_DIR / "taddy" / "import_transcripts.py")
    args = ["--shows", cfg.slug, "--per-show-limit", str(per_show_limit)]
    if dry_run:
        args.append("--dry-run")
    return run_script(script, args, dry_run=False, label=f"Taddy import ({cfg.slug})")


def step_entity_extraction(cfg: ShowConfig, episode_ids: list[int], dry_run: bool) -> bool:
    """Step 2: Extract entities from new episodes."""
    if not episode_ids:
        print(f"  No new episodes to extract for {cfg.slug}")
        return True

    print(f"  {len(episode_ids)} episodes need entity extraction")
    # Entity extraction uses extract_entities.py which processes from cached transcripts
    # For new episodes, we run the guarded backfill with a small chunk
    script = str(SCRAPERS_DIR / "ai_daily" / "run_guarded_backfill.py")
    args = [
        "--chunk-size", str(min(len(episode_ids), 20)),
        "--max-episodes", str(len(episode_ids)),
    ]
    return run_script(script, args, dry_run, label=f"Entity extraction ({len(episode_ids)} eps)")


def step_normalize_aliases(dry_run: bool) -> bool:
    """Step 3: Normalize aliases (dedup entities)."""
    script = str(SCRAPERS_DIR / "ai_daily" / "normalize_aliases.py")
    return run_script(script, [], dry_run, label="Normalize aliases")


def step_notion_sync(cfg: ShowConfig, dry_run: bool) -> bool:
    """Step 4: Sync to Notion."""
    if not cfg.notion_database_id:
        print(f"  Skipping Notion sync (no DB for {cfg.slug})")
        return True

    script = str(PIPELINE_DIR / "sync_notion.py")
    args = ["--show", cfg.slug]
    if dry_run:
        args.append("--dry-run")
    return run_script(script, args, dry_run=False, label=f"Notion sync ({cfg.slug})")


def step_spotify_sync(cfg: ShowConfig, dry_run: bool) -> bool:
    """Step 5: Sync to Spotify playlist."""
    if not cfg.spotify_playlist_id:
        print(f"  Skipping Spotify sync (no playlist for {cfg.slug})")
        return True

    script = str(PIPELINE_DIR / "sync_playlist.py")
    args = ["--show-id", str(cfg.show_id)]
    if dry_run:
        args.append("--dry-run")
    return run_script(script, args, dry_run=False, label=f"Spotify sync ({cfg.slug})")


def process_show(cfg: ShowConfig, dry_run: bool) -> None:
    """Run the full pipeline for a single show."""
    print(f"\n{'='*60}")
    print(f"Processing: {cfg.name} ({cfg.slug})")
    print(f"{'='*60}")

    # Step 1: Taddy import
    print("\n[1/5] Taddy import")
    if not step_taddy_import(cfg, dry_run):
        print("  WARNING: Taddy import failed, continuing anyway...")

    # Step 2: Entity extraction (only for entity-type shows)
    print("\n[2/5] Entity extraction")
    if cfg.extraction_type == "entity_extraction":
        conn = get_db_connection()
        try:
            unextracted = find_unextracted_episodes(conn, cfg.show_id)
        finally:
            conn.close()
        if not step_entity_extraction(cfg, unextracted, dry_run):
            print("  WARNING: Entity extraction failed, continuing...")
    else:
        print(f"  Skipping (extraction_type={cfg.extraction_type})")

    # Step 3: Normalize aliases
    print("\n[3/5] Normalize aliases")
    if cfg.extraction_type == "entity_extraction":
        if not step_normalize_aliases(dry_run):
            print("  WARNING: Alias normalization failed, continuing...")
    else:
        print("  Skipping (not an entity show)")

    # Step 4: Notion sync
    print("\n[4/5] Notion sync")
    if not step_notion_sync(cfg, dry_run):
        print("  WARNING: Notion sync failed.")

    # Step 5: Spotify sync
    print("\n[5/5] Spotify sync")
    if not step_spotify_sync(cfg, dry_run):
        print("  WARNING: Spotify sync failed.")

    print(f"\nDone: {cfg.name}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="New episode pipeline orchestrator")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--shows", help="Comma-separated show slugs (e.g., ai-daily-brief,sop)")
    group.add_argument("--all", action="store_true", help="Process all configured shows")
    p.add_argument("--dry-run", action="store_true", help="Preview actions without executing")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()

    if args.all:
        slugs = list(SHOWS.keys())
    else:
        slugs = [s.strip() for s in args.shows.split(",")]

    print(f"Pipeline: {', '.join(slugs)}")
    if args.dry_run:
        print("Mode: DRY RUN")

    for slug in slugs:
        cfg = get_show(slug)
        process_show(cfg, args.dry_run)

    print(f"\n{'='*60}")
    print("All shows processed.")
    print(f"{'='*60}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

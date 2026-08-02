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
import csv
import subprocess
import sys
import time
from pathlib import Path

# Allow imports from pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_environment, get_db_connection, get_logger
from show_config import SHOWS, get_show, ShowConfig

PIPELINE_DIR = Path(__file__).resolve().parent
# Prefer the project venv locally; fall back to the running interpreter — CI runs
# on the runner's Python (deps installed there) where the venv path doesn't exist.
_VENV_PYTHON = PIPELINE_DIR / "venv" / "bin" / "python"
VENV_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
SCRAPERS_DIR = PIPELINE_DIR / "scrapers"

# Episodes older than this are skipped by default — they pre-date the current
# extraction quality bar and aren't worth re-processing. Pass recent_only=False
# (the --backfill flag) to extract the full archive, e.g. when onboarding a show.
RECENT_EPISODE_WINDOW_DAYS = 90

# Bounded retry for transient subprocess (step) failures. Steps are idempotent
# (Taddy upserts, A2 delete-then-insert load, incremental Notion sync), so a
# retry is safe and recovers transient API/network blips.
MAX_STEP_RETRIES = 2

log = get_logger("pipeline.run_new_episodes")


def run_script(script_path: str, args: list[str], dry_run: bool, label: str, timeout: int = 600) -> bool:
    """Run a pipeline script as a subprocess, with bounded retry + backoff on
    failure. Pipeline steps are idempotent (Taddy upserts, A2 delete-then-insert
    load, incremental Notion sync), so a retry is safe and recovers transient
    API/network blips. Returns True on success.
    """
    cmd = [VENV_PYTHON, script_path] + args
    if dry_run:
        print(f"  [dry-run] Would run: {' '.join(cmd)}")
        return True

    attempts = MAX_STEP_RETRIES + 1
    for attempt in range(1, attempts + 1):
        suffix = "" if attempt == 1 else f" (retry {attempt - 1}/{MAX_STEP_RETRIES})"
        print(f"  Running: {label}{suffix}...")
        result = None
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            ok = result.returncode == 0
            tail = "" if ok else (result.stderr[-500:] if result.stderr else "(no stderr)")
        except subprocess.TimeoutExpired:
            # A timeout is the canonical transient failure — retry it like any other.
            ok = False
            tail = f"timed out after {timeout}s"
        if ok:
            if attempt > 1:
                log.info("step recovered on retry %d: %s", attempt - 1, label)
            for line in result.stdout.strip().split("\n")[-5:]:
                print(f"    {line}")
            return True
        if attempt < attempts:
            backoff = 5 * (2 ** (attempt - 1))
            log.warning(
                "step attempt %d/%d failed, retrying in %ds: %s — %s",
                attempt, attempts, backoff, label, tail,
            )
            time.sleep(backoff)
        else:
            log.error("step failed after %d attempts: %s — %s", attempts, label, tail)
            print(f"  FAILED ({label}) after {attempts} attempts")
    return False


def find_unextracted_episodes(
    conn,
    show_id: int,
    recent_only: bool = True,
    require_transcript: bool = False,
) -> list[int]:
    """Find episodes that have source text (transcript OR show-notes) but no extraction run.

    If recent_only=True (default), only returns episodes published within the last
    RECENT_EPISODE_WINDOW_DAYS days — this avoids re-processing old episodes that
    pre-date the current quality bar. Use recent_only=False (the --backfill flag)
    for the full archive, e.g. when onboarding a show.

    require_transcript=True holds an episode back until its transcript exists, instead
    of falling back to show notes. Callers set it for transcript-based (Taddy) shows,
    because that fallback is a RACE, not a safety net: Taddy publishes a transcript
    about a day after the episode, so a same-day run would extract the show-notes blurb
    instead — and since the episode then HAS mentions, the exclusion below means it is
    never re-extracted once the real transcript lands. That silently cost us episodes
    5133 (hard-fork) and 7261 (ai-daily-brief), whose only mentions are boilerplate like
    "The AI Daily Brief Newsletter". Waiting one run is strictly better than mining the
    wrong text once and never revisiting it. Show-notes-only shows (Gabfest) pass False.
    """
    with conn.cursor() as cur:
        source_text = (
            "et.transcript_text" if require_transcript
            else "COALESCE(et.transcript_text, ep.description_body)"
        )
        sql = f"""
            SELECT DISTINCT ep.id
            FROM episodes ep
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.show_id = %s
              AND {source_text} IS NOT NULL
              AND ep.id NOT IN (
                  SELECT DISTINCT m.episode_id FROM ai_mentions m
              )
        """
        params: list = [show_id]
        if recent_only:
            sql += "  AND ep.publish_date >= CURRENT_DATE - make_interval(days => %s)\n"
            params.append(RECENT_EPISODE_WINDOW_DAYS)
        sql += "ORDER BY ep.id;"
        cur.execute(sql, params)
        return [row["id"] for row in cur.fetchall()]


def step_import(cfg: ShowConfig, dry_run: bool, per_show_limit: int = 50) -> bool:
    """Step 1: Import new episodes. Taddy shows get transcripts; cfg.importer routes
    non-Taddy scheduled sources (Gabfest's Megaphone RSS show-notes). Curated sources
    (blogs, research — cfg.importer None) are ingested via save_item/the pull queue,
    never here, so skipping them is correct rather than a gap."""
    if cfg.taddy_uuid:
        script = str(SCRAPERS_DIR / "taddy" / "import_transcripts.py")
        args = ["--shows", cfg.slug, "--per-show-limit", str(per_show_limit)]
        if dry_run:
            args.append("--dry-run")
        return run_script(script, args, dry_run=False, label=f"Taddy import ({cfg.slug})")

    if cfg.importer == "gabfest_rss":
        script = str(SCRAPERS_DIR / "gabfest" / "import_gabfest.py")
        args = ["--limit", str(per_show_limit)]
        if dry_run:
            args.append("--dry-run")
        return run_script(script, args, dry_run=False, label="Gabfest RSS import")

    print(f"  Skipping import (no scheduled source for {cfg.slug})")
    return True


def prepare_extraction_inputs(conn, episode_ids: list[int]) -> tuple[Path, Path]:
    """Export transcripts from Neon to file cache and generate a CSV for extract_entities.py.

    Returns (csv_path, transcripts_dir).
    """
    transcripts_dir = PIPELINE_DIR / "_cache" / "ai_daily" / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    csv_path = PIPELINE_DIR / "_cache" / "ai_daily" / "unextracted_episodes.csv"

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.id AS episode_id, ep.title, ep.publish_date,
                   ep.url AS episode_url,
                   COALESCE(et.transcript_text, ep.description_body) AS transcript_text
            FROM episodes ep
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.id = ANY(%s)
            ORDER BY ep.publish_date DESC
            """,
            (episode_ids,),
        )
        rows = cur.fetchall()

    # Write transcript files + CSV
    csv_rows = []
    written = 0
    for row in rows:
        eid = row["episode_id"]
        slug = row["title"][:80].lower().replace(" ", "-").replace("/", "-")
        txt_path = transcripts_dir / f"{eid}-{slug}.txt"
        if not txt_path.exists():
            txt_path.write_text(row["transcript_text"] or "", encoding="utf-8")
            written += 1
        csv_rows.append({
            "episode_id": eid,
            "title": row["title"],
            "publish_date": str(row["publish_date"]),
            "episode_url": row.get("episode_url") or "",
        })

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["episode_id", "title", "publish_date", "episode_url"])
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"  Prepared {len(csv_rows)} episodes ({written} new transcripts cached)")
    return csv_path, transcripts_dir


def step_entity_extraction(cfg: ShowConfig, episode_ids: list[int], dry_run: bool) -> bool:
    """Step 2: Extract entities from new episodes."""
    if not episode_ids:
        print(f"  No new episodes to extract for {cfg.slug}")
        return True

    print(f"  {len(episode_ids)} episodes need entity extraction")

    # Prepare inputs: export transcripts from Neon and generate CSV
    conn = get_db_connection()
    try:
        csv_path, transcripts_dir = prepare_extraction_inputs(conn, episode_ids)
    finally:
        conn.close()

    extract_script = str(SCRAPERS_DIR / "ai_daily" / "extract_entities.py")
    load_script = str(SCRAPERS_DIR / "ai_daily" / "load_entity_batch.py")
    output_root = str(PIPELINE_DIR.parent / "codex-notes" / "ai-daily-entity-extraction")

    # Process in batches of 5 (each episode takes ~60-90s for OpenAI extraction)
    batch_size = 5
    total_ok = True
    for start in range(0, len(episode_ids), batch_size):
        batch = episode_ids[start:start + batch_size]
        ids_str = ",".join(str(eid) for eid in batch)
        batch_name = f"incremental-{batch[0]}-to-{batch[-1]}"
        extract_args = [
            "--episodes", ids_str,
            "--limit", str(len(batch)),
            "--episodes-csv", str(csv_path),
            "--transcripts-dir", str(transcripts_dir),
            "--batch-name", batch_name,
            "--output-dir", output_root,
            "--extraction-type", cfg.extraction_type or "entity_extraction",
        ]
        batch_num = start // batch_size + 1
        total_batches = (len(episode_ids) + batch_size - 1) // batch_size
        label = f"Entity extraction (batch {batch_num}/{total_batches}, {len(batch)} eps)"
        if not run_script(extract_script, extract_args, dry_run, label=label, timeout=900):
            print(f"  WARNING: {label} failed, continuing with next batch...")
            total_ok = False
            continue

        # Load extracted batch into Neon
        batch_dir = str(Path(output_root) / batch_name)
        load_args = ["--batch-dir", batch_dir, "--show-slug", cfg.slug]
        if not run_script(load_script, load_args, dry_run, label=f"Load batch {batch_num}/{total_batches}"):
            print(f"  WARNING: Load batch {batch_num} failed, continuing...")
            total_ok = False
    return total_ok


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
    args = ["--show", cfg.slug, "--min-mentions", str(cfg.notion_min_mentions)]
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


def process_show(cfg: ShowConfig, dry_run: bool, backfill: bool = False) -> list[str]:
    """Run the full pipeline for a single show. Returns the list of failed step names
    (empty = all OK) so the caller can surface a partial failure instead of swallowing it.
    Steps stay resilient (one failure doesn't block the rest), but the failure is recorded.

    backfill=True extracts the full archive (recent_only=False) and raises the
    Taddy per-run import cap — use it when onboarding a show or catching up history.
    """
    started = time.time()
    failed: list[str] = []
    print(f"\n{'='*60}")
    print(f"Processing: {cfg.name} ({cfg.slug}){' [BACKFILL]' if backfill else ''}")
    print(f"{'='*60}")

    # Step 1: import (Taddy transcripts, or Megaphone RSS for Gabfest)
    print("\n[1/5] Import")
    if not step_import(cfg, dry_run, per_show_limit=500 if backfill else 50):
        print("  WARNING: import failed, continuing...")
        failed.append("import")

    # Step 2: Entity/media extraction (shows whose content the LLM extractor handles)
    print("\n[2/5] Entity extraction")
    if cfg.extraction_type in ("entity_extraction", "media_extraction"):
        conn = get_db_connection()
        try:
            unextracted = find_unextracted_episodes(
                conn,
                cfg.show_id,
                recent_only=not backfill,
                # Taddy shows are transcript-based; anything else (Gabfest) is
                # legitimately show-notes-only.
                require_transcript=bool(cfg.taddy_uuid),
            )
        finally:
            conn.close()
        if not step_entity_extraction(cfg, unextracted, dry_run):
            print("  WARNING: Entity extraction failed, continuing...")
            failed.append("extraction")
    else:
        print(f"  Skipping (extraction_type={cfg.extraction_type})")

    # Step 3: Normalize aliases
    print("\n[3/5] Normalize aliases")
    if cfg.extraction_type == "entity_extraction":
        if not step_normalize_aliases(dry_run):
            print("  WARNING: Alias normalization failed, continuing...")
            failed.append("normalize")
    else:
        # Media relies on load-time exact-name dedup; the fuzzy alias rules are tech-specific.
        print(f"  Skipping alias normalization (extraction_type={cfg.extraction_type})")

    # Step 4: Notion sync
    print("\n[4/5] Notion sync")
    if not step_notion_sync(cfg, dry_run):
        print("  WARNING: Notion sync failed.")
        failed.append("notion_sync")

    # Step 5: Spotify sync
    print("\n[5/5] Spotify sync")
    if not step_spotify_sync(cfg, dry_run):
        print("  WARNING: Spotify sync failed.")
        failed.append("spotify_sync")

    elapsed = time.time() - started
    log.info("show=%s done in %.1fs (backfill=%s, failed=%s)", cfg.slug, elapsed, backfill, failed)
    print(f"\nDone: {cfg.name} ({elapsed:.1f}s){' — FAILED: ' + ','.join(failed) if failed else ''}")
    return failed


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="New episode pipeline orchestrator")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--shows", help="Comma-separated show slugs (e.g., ai-daily-brief,sop)")
    group.add_argument("--all", action="store_true", help="Process all configured shows")
    p.add_argument("--dry-run", action="store_true", help="Preview actions without executing")
    p.add_argument(
        "--backfill",
        action="store_true",
        help="Extract the full archive (ignore the recent-only window) and raise the "
        "Taddy import cap — for onboarding a show or catching up history.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()
    get_logger("pipeline.run_new_episodes")  # refresh level now LOG_LEVEL (.env.local) is loaded
    run_started = time.time()

    if args.all:
        slugs = list(SHOWS.keys())
    else:
        slugs = [s.strip() for s in args.shows.split(",")]

    print(f"Pipeline: {', '.join(slugs)}")
    if args.dry_run:
        print("Mode: DRY RUN")

    failures: dict[str, list[str]] = {}
    for slug in slugs:
        cfg = get_show(slug)
        failed = process_show(cfg, args.dry_run, backfill=args.backfill)
        if failed:
            failures[slug] = failed

    print(f"\n{'='*60}")
    print("All shows processed.")
    print(f"{'='*60}")
    log.info("run complete: %d show(s) in %.1fs", len(slugs), time.time() - run_started)

    # A partial failure (one show/step) must NOT look like success: exit non-zero so the
    # CI failure path fires (Slack + issue). Skipped steps don't count. Dry runs never fail.
    if failures and not args.dry_run:
        summary = "; ".join(f"{slug}: {', '.join(steps)}" for slug, steps in failures.items())
        print(f"\n⚠️  PARTIAL FAILURE — {summary}", file=sys.stderr)
        log.error("partial failure — %s", summary)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

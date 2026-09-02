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
import json
import subprocess
import sys
import time
from dataclasses import dataclass
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

# How long a transcript-based show waits for Taddy before giving up and mining the
# show notes anyway. Taddy publishes ~24h after an episode, so a week is many times
# the normal wait: past it, the transcript is not coming (rights pulled, a Taddy gap,
# a feed mismatch) and holding out forever would silently drop the episode from the
# dataset. Falling back is announced, and the recovery loop below re-extracts the
# episode for real if a transcript ever does land.
TRANSCRIPT_GRACE_DAYS = 7

# Per-run cap on self-healing re-extractions. Each one costs an OpenAI call, so a
# large backlog drains over several days instead of arriving as one surprise bill.
# Whole batches are always taken together (see find_transcript_race_batches).
SELF_HEAL_MAX_EPISODES_PER_RUN = 3

log = get_logger("pipeline.run_new_episodes")


@dataclass(frozen=True)
class EpisodeSource:
    """An episode awaiting extraction, and which text the extractor will read.

    `source` is recorded rather than re-derived later: it is the difference between
    provenance we know and provenance we assume.
    """

    episode_id: int
    source: str  # "transcript" | "show_notes"


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
    grace_days: int = TRANSCRIPT_GRACE_DAYS,
) -> list[EpisodeSource]:
    """Find episodes that have source text (transcript OR show-notes) but no extraction run.

    If recent_only=True (default), only returns episodes published within the last
    RECENT_EPISODE_WINDOW_DAYS days — this avoids re-processing old episodes that
    pre-date the current quality bar. Use recent_only=False (the --backfill flag)
    for the full archive, e.g. when onboarding a show.

    require_transcript=True makes a transcript-based (Taddy) show PREFER its transcript
    and wait for it, because the show-notes fallback is a RACE, not a safety net: Taddy
    publishes a transcript about a day after the episode, so a same-day run would mine
    the show-notes blurb instead — and since the episode then HAS mentions, the exclusion
    below means it is never re-extracted once the real transcript lands. That silently
    cost us episodes 5133 (hard-fork) and 7261 (ai-daily-brief), whose only mentions are
    boilerplate like "The AI Daily Brief Newsletter".

    The wait is bounded by grace_days. An episode whose transcript never arrives still
    gets extracted from its notes once it is older than that — degraded but present, and
    the returned `source` says so out loud. Waiting forever would trade one silent data
    loss for another. Show-notes-only shows (Gabfest) pass require_transcript=False and
    take the notes immediately.

    Episodes covered by a declared-empty run (status completed_empty — the extractor ran,
    the filters kept nothing, the reasons are on the run row) are excluded too: one
    declared answer is final. Re-asking the model daily is how a sponsor read got stored
    as editorial content on 2026-08-24.
    """
    with conn.cursor() as cur:
        params: list = [show_id]
        if require_transcript:
            # Transcript if we have one; notes only once the grace window has expired.
            source_predicate = """(
                    et.transcript_text IS NOT NULL
                    OR (
                        ep.description_body IS NOT NULL
                        AND ep.publish_date < CURRENT_DATE - make_interval(days => %s)
                    )
                  )"""
            params.append(grace_days)
        else:
            source_predicate = "COALESCE(et.transcript_text, ep.description_body) IS NOT NULL"

        sql = f"""
            SELECT ep.id,
                   CASE WHEN et.transcript_text IS NOT NULL THEN 'transcript'
                        ELSE 'show_notes' END AS source
            FROM episodes ep
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.show_id = %s
              AND {source_predicate}
              AND ep.id NOT IN (
                  SELECT DISTINCT m.episode_id FROM ai_mentions m
              )
              -- An extraction that kept nothing is recorded as a declared empty run
              -- (load_entity_batch.record_empty_batch). Without this clause such an
              -- episode has no mentions and would be re-extracted every day until it
              -- aged out of the window — ~90 model calls and 90 red runs for one
              -- episode whose answer was "nothing worth storing".
              AND NOT EXISTS (
                  SELECT 1 FROM ai_runs r
                  WHERE r.show_id = ep.show_id
                    AND r.status = 'completed_empty'
                    AND r.parameters->'episodes' @> to_jsonb(ep.id)
              )
        """
        if recent_only:
            sql += "  AND ep.publish_date >= CURRENT_DATE - make_interval(days => %s)\n"
            params.append(RECENT_EPISODE_WINDOW_DAYS)
        sql += "ORDER BY ep.id;"
        cur.execute(sql, params)
        return [EpisodeSource(row["id"], row["source"]) for row in cur.fetchall()]


def find_transcript_race_batches(
    conn, show_id: int, max_episodes: int = SELF_HEAL_MAX_EPISODES_PER_RUN
) -> list[tuple[str, list[int]]]:
    """Find extraction batches damaged by the transcript race, ready to re-extract.

    A damaged episode is one whose mentions carry no transcript_id even though the
    episode HAS a transcript now — i.e. it was mined from show notes and the real text
    landed afterwards. Nothing revisits it on its own, because find_unextracted_episodes
    skips any episode that already has mentions. This is that revisit.

    Returns whole BATCHES, not loose episodes, and that is the load-bearing detail:
    load_entity_batch.delete_existing_run keys on (show_id, batch_name), so re-loading
    under a batch name wipes every mention that name owns. Re-extracting only the damaged
    episode of a mixed batch would delete its healthy siblings and never replace them —
    batch incremental-7261-to-7262 is exactly that shape (7261 damaged, 7262 fine).

    Bounded by max_episodes to cap per-run OpenAI spend, but a single batch is never split
    and is always returned even if it alone exceeds the cap; refusing an oversized batch
    would park it in the queue forever, which is the failure this whole function exists
    to end.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH damaged_batches AS (
                SELECT DISTINCT r.batch_name
                FROM ai_mentions m
                JOIN ai_runs r ON r.id = m.run_id
                JOIN episodes ep ON ep.id = m.episode_id
                JOIN episode_transcripts et ON et.episode_id = m.episode_id
                WHERE ep.show_id = %s
                  AND r.show_id = %s
                  AND m.transcript_id IS NULL
            )
            SELECT db.batch_name,
                   ARRAY_AGG(DISTINCT m2.episode_id ORDER BY m2.episode_id) AS episode_ids
            FROM damaged_batches db
            JOIN ai_runs r2 ON r2.batch_name = db.batch_name AND r2.show_id = %s
            JOIN ai_mentions m2 ON m2.run_id = r2.id
            GROUP BY db.batch_name
            ORDER BY db.batch_name;
            """,
            [show_id, show_id, show_id],
        )
        candidates = [(row["batch_name"], list(row["episode_ids"])) for row in cur.fetchall()]

    return _take_batches_within_budget(candidates, max_episodes)


def _take_batches_within_budget(
    candidates: list[tuple[str, list[int]]], max_episodes: int
) -> list[tuple[str, list[int]]]:
    """Take whole batches until the episode budget is spent. Always takes at least one."""
    selected: list[tuple[str, list[int]]] = []
    spent = 0
    for batch_name, episode_ids in candidates:
        if selected and spent + len(episode_ids) > max_episodes:
            break
        selected.append((batch_name, episode_ids))
        spent += len(episode_ids)
        if spent >= max_episodes:
            break
    return selected


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


def prepare_extraction_inputs(conn, episode_ids: list[int]) -> tuple[Path, Path, Path]:
    """Export source text from Neon to the file cache and generate extractor inputs.

    Returns (csv_path, transcripts_dir, provenance_path).

    The provenance file is the point of this function beyond file-shuffling. It records,
    per episode, the transcript row whose text was actually handed to the extractor — or
    null when the text came from show notes. load_entity_batch reads it instead of asking
    the database "does this episode have a transcript?" at load time, which is a different
    question with a different answer: extraction of a five-episode batch takes minutes, and
    a transcript landing inside that window would otherwise stamp mentions mined from show
    notes with a transcript_id they never came from. That fabricated provenance is invisible
    afterwards and would make the self-heal check below permanently blind to the episode.
    """
    transcripts_dir = PIPELINE_DIR / "_cache" / "ai_daily" / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    csv_path = PIPELINE_DIR / "_cache" / "ai_daily" / "unextracted_episodes.csv"
    provenance_path = PIPELINE_DIR / "_cache" / "ai_daily" / "extraction_provenance.json"

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.id AS episode_id, ep.title, ep.publish_date,
                   ep.url AS episode_url,
                   et.id AS transcript_id,
                   COALESCE(et.transcript_text, ep.description_body) AS source_text,
                   (et.transcript_text IS NOT NULL) AS from_transcript
            FROM episodes ep
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.id = ANY(%s)
            ORDER BY ep.publish_date DESC
            """,
            (episode_ids,),
        )
        rows = cur.fetchall()

    csv_rows = []
    provenance: dict[str, int | None] = {}
    written = 0
    refreshed = 0
    for row in rows:
        eid = row["episode_id"]
        slug = row["title"][:80].lower().replace(" ", "-").replace("/", "-")
        txt_path = transcripts_dir / f"{eid}-{slug}.txt"
        source_text = row["source_text"] or ""
        # Overwrite when the text has changed rather than skipping on mere existence.
        # A cached file can hold the show-notes blurb from the run that lost the race;
        # trusting it would re-extract the same wrong text and defeat the self-heal.
        if not txt_path.exists():
            txt_path.write_text(source_text, encoding="utf-8")
            written += 1
        elif txt_path.read_text(encoding="utf-8") != source_text:
            txt_path.write_text(source_text, encoding="utf-8")
            refreshed += 1

        provenance[str(eid)] = int(row["transcript_id"]) if row["from_transcript"] else None
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

    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")

    from_transcript = sum(1 for v in provenance.values() if v is not None)
    print(
        f"  Prepared {len(csv_rows)} episodes "
        f"({from_transcript} from transcript, {len(csv_rows) - from_transcript} from show notes; "
        f"{written} newly cached, {refreshed} refreshed)"
    )
    if refreshed:
        log.warning("refreshed %d stale cached source file(s) before extraction", refreshed)
    return csv_path, transcripts_dir, provenance_path


EXTRACTION_BATCH_SIZE = 5  # each episode takes ~60-90s of OpenAI time


def extract_and_load_batch(
    cfg: ShowConfig,
    episode_ids: list[int],
    batch_name: str,
    csv_path: Path,
    transcripts_dir: Path,
    provenance_path: Path,
    dry_run: bool,
    label: str,
) -> bool:
    """Extract one batch and load it into Neon under `batch_name`.

    The batch name is a parameter rather than derived here because the self-heal path
    must re-use the ORIGINAL name: that is what makes delete_existing_run replace the
    damaged run instead of leaving its mentions behind as duplicates.
    """
    extract_script = str(SCRAPERS_DIR / "ai_daily" / "extract_entities.py")
    load_script = str(SCRAPERS_DIR / "ai_daily" / "load_entity_batch.py")
    output_root = str(PIPELINE_DIR.parent / "codex-notes" / "ai-daily-entity-extraction")

    extract_args = [
        "--episodes", ",".join(str(eid) for eid in episode_ids),
        "--limit", str(len(episode_ids)),
        "--episodes-csv", str(csv_path),
        "--transcripts-dir", str(transcripts_dir),
        "--batch-name", batch_name,
        "--output-dir", output_root,
        "--extraction-type", cfg.extraction_type or "entity_extraction",
    ]
    if not run_script(extract_script, extract_args, dry_run, label=label, timeout=900):
        print(f"  WARNING: {label} failed.")
        return False

    load_args = [
        "--batch-dir", str(Path(output_root) / batch_name),
        "--show-slug", cfg.slug,
        "--provenance-json", str(provenance_path),
    ]
    if not run_script(load_script, load_args, dry_run, label=f"Load {batch_name}"):
        print(f"  WARNING: Load {batch_name} failed.")
        return False
    return True


def step_entity_extraction(cfg: ShowConfig, episodes: list[EpisodeSource], dry_run: bool) -> bool:
    """Step 2: Extract entities from new episodes."""
    if not episodes:
        print(f"  No new episodes to extract for {cfg.slug}")
        return True

    episode_ids = [e.episode_id for e in episodes]
    degraded = [e.episode_id for e in episodes if e.source == "show_notes"]
    print(f"  {len(episode_ids)} episodes need entity extraction")
    if degraded and cfg.taddy_uuid:
        # A transcript show falling back to notes is a real (bounded) quality loss.
        # Say it in the run output and the log rather than letting it pass as normal.
        print(
            f"  ⚠️  {len(degraded)} episode(s) past the {TRANSCRIPT_GRACE_DAYS}-day transcript "
            f"grace window — extracting from show notes: {degraded}"
        )
        log.warning(
            "show=%s extracting %d episode(s) from show notes after the %d-day transcript "
            "grace window expired: %s",
            cfg.slug, len(degraded), TRANSCRIPT_GRACE_DAYS, degraded,
        )

    conn = get_db_connection()
    try:
        csv_path, transcripts_dir, provenance_path = prepare_extraction_inputs(conn, episode_ids)
    finally:
        conn.close()

    total_ok = True
    total_batches = (len(episode_ids) + EXTRACTION_BATCH_SIZE - 1) // EXTRACTION_BATCH_SIZE
    for start in range(0, len(episode_ids), EXTRACTION_BATCH_SIZE):
        batch = episode_ids[start:start + EXTRACTION_BATCH_SIZE]
        batch_num = start // EXTRACTION_BATCH_SIZE + 1
        ok = extract_and_load_batch(
            cfg,
            batch,
            f"incremental-{batch[0]}-to-{batch[-1]}",
            csv_path,
            transcripts_dir,
            provenance_path,
            dry_run,
            label=f"Entity extraction (batch {batch_num}/{total_batches}, {len(batch)} eps)",
        )
        if not ok:
            print("  Continuing with next batch...")
            total_ok = False
    return total_ok


def step_self_heal_transcript_race(cfg: ShowConfig, dry_run: bool) -> tuple[bool, int]:
    """Re-extract episodes that were mined from show notes before their transcript landed.

    Returns (ok, episodes_healed). Runs before the normal extraction step so a heal and a
    fresh episode never contend for the same batch name.
    """
    conn = get_db_connection()
    try:
        batches = find_transcript_race_batches(conn, cfg.show_id)
    finally:
        conn.close()

    if not batches:
        print("  Nothing to heal — no episodes were extracted before their transcript.")
        return True, 0

    episode_count = sum(len(ids) for _, ids in batches)
    print(
        f"  ⚠️  SELF-HEAL: {len(batches)} batch(es) / {episode_count} episode(s) were extracted "
        f"from show notes before their transcript arrived. Re-extracting from the real text."
    )
    for batch_name, ids in batches:
        print(f"      {batch_name}: episodes {ids}")
    log.warning(
        "show=%s self-healing transcript race: %d batch(es), %d episode(s): %s",
        cfg.slug, len(batches), episode_count, [b for b, _ in batches],
    )

    all_ids = sorted({eid for _, ids in batches for eid in ids})
    conn = get_db_connection()
    try:
        csv_path, transcripts_dir, provenance_path = prepare_extraction_inputs(conn, all_ids)
    finally:
        conn.close()

    healed = 0
    ok = True
    for batch_name, ids in batches:
        if extract_and_load_batch(
            cfg, ids, batch_name, csv_path, transcripts_dir, provenance_path, dry_run,
            label=f"Self-heal re-extraction ({batch_name}, {len(ids)} eps)",
        ):
            healed += len(ids)
        else:
            ok = False

    if dry_run:
        return ok, healed

    # Confirm the heal actually took. A re-extraction that "succeeded" but left the
    # episode damaged would otherwise be retried silently every run, spending money on
    # a loop nobody can see. Naming it here turns that into one visible failure, and
    # data_health fails outright once an episode sits unhealed for a few days.
    #
    # A re-extraction that keeps no mentions is recorded by the loader as a declared
    # empty run under the ORIGINAL batch name (replacing the damaged run), so the
    # episode leaves the race queue with its reasons on record rather than sitting
    # damaged and retried daily. (Until 2026-09-01 the loader raised on an empty
    # mentions.csv instead — "add a guard only if it ever fires" — and it fired on
    # 2026-08-23 in the normal extraction path.)
    conn = get_db_connection()
    try:
        still_damaged = find_transcript_race_batches(conn, cfg.show_id, max_episodes=len(all_ids))
    finally:
        conn.close()
    unresolved = [b for b, _ in still_damaged if b in {name for name, _ in batches}]
    if unresolved:
        ok = False
        print(f"  ERROR: still damaged after re-extraction: {unresolved}")
        log.error(
            "show=%s self-heal did not resolve batch(es) %s — they will be retried, "
            "but something is wrong with the re-extraction",
            cfg.slug, unresolved,
        )
    return ok, healed


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


def process_show(cfg: ShowConfig, dry_run: bool, backfill: bool = False) -> tuple[list[str], int]:
    """Run the full pipeline for a single show. Returns (failed_step_names, episodes_healed)
    so the caller can surface a partial failure instead of swallowing it, and report any
    self-healing that happened. Steps stay resilient (one failure doesn't block the rest),
    but the failure is recorded.

    backfill=True extracts the full archive (recent_only=False) and raises the
    Taddy per-run import cap — use it when onboarding a show or catching up history.
    """
    started = time.time()
    failed: list[str] = []
    healed = 0
    print(f"\n{'='*60}")
    print(f"Processing: {cfg.name} ({cfg.slug}){' [BACKFILL]' if backfill else ''}")
    print(f"{'='*60}")

    # Step 1: import (Taddy transcripts, or Megaphone RSS for Gabfest)
    print("\n[1/6] Import")
    if not step_import(cfg, dry_run, per_show_limit=500 if backfill else 50):
        print("  WARNING: import failed, continuing...")
        failed.append("import")

    extracts = cfg.extraction_type in ("entity_extraction", "media_extraction")

    # Step 2: heal episodes mined from show notes before their transcript arrived.
    # Runs after the import (so a transcript that just landed counts) and before the
    # normal extraction (so the two never contend for one batch name).
    print("\n[2/6] Self-heal transcript race")
    if extracts:
        heal_ok, healed = step_self_heal_transcript_race(cfg, dry_run)
        if not heal_ok:
            print("  WARNING: self-heal failed, continuing...")
            failed.append("self_heal")
    else:
        print(f"  Skipping (extraction_type={cfg.extraction_type})")

    # Step 3: Entity/media extraction (shows whose content the LLM extractor handles)
    print("\n[3/6] Entity extraction")
    if extracts:
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

    # Step 4: Normalize aliases
    print("\n[4/6] Normalize aliases")
    if cfg.extraction_type == "entity_extraction":
        if not step_normalize_aliases(dry_run):
            print("  WARNING: Alias normalization failed, continuing...")
            failed.append("normalize")
    else:
        # Media relies on load-time exact-name dedup; the fuzzy alias rules are tech-specific.
        print(f"  Skipping alias normalization (extraction_type={cfg.extraction_type})")

    # Step 5: Notion sync
    print("\n[5/6] Notion sync")
    if not step_notion_sync(cfg, dry_run):
        print("  WARNING: Notion sync failed.")
        failed.append("notion_sync")

    # Step 6: Spotify sync
    print("\n[6/6] Spotify sync")
    if not step_spotify_sync(cfg, dry_run):
        print("  WARNING: Spotify sync failed.")
        failed.append("spotify_sync")

    elapsed = time.time() - started
    log.info(
        "show=%s done in %.1fs (backfill=%s, healed=%d, failed=%s)",
        cfg.slug, elapsed, backfill, healed, failed,
    )
    print(f"\nDone: {cfg.name} ({elapsed:.1f}s){' — FAILED: ' + ','.join(failed) if failed else ''}")
    return failed, healed


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
    healed: dict[str, int] = {}
    for slug in slugs:
        cfg = get_show(slug)
        failed, show_healed = process_show(cfg, args.dry_run, backfill=args.backfill)
        if failed:
            failures[slug] = failed
        if show_healed:
            healed[slug] = show_healed

    print(f"\n{'='*60}")
    print("All shows processed.")
    if healed:
        # Loud in the summary on purpose: a heal means data that was wrong is now right,
        # and a heal happening every single run would mean the prevention has a hole.
        total_healed = sum(healed.values())
        detail = ", ".join(f"{slug}: {n}" for slug, n in healed.items())
        print(f"🩹 SELF-HEALED {total_healed} episode(s) re-extracted from transcript — {detail}")
        log.warning("self-healed %d episode(s) — %s", total_healed, detail)
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

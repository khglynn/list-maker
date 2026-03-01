#!/usr/bin/env python3
"""
Run AI Daily mention extraction until all transcripted episodes are processed.

This runner:
- Selects only AI Daily episodes that already have transcripts and no ai_mentions rows.
- Processes in chunks with the existing extractor.
- Applies quality gates between batches.
- Loads only passing batches.
- Stops immediately on quality failure or command failure.
"""

from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import fcntl
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from pipeline.scrapers.ai_daily.transcripts import write_cache
except ModuleNotFoundError:
    from transcripts import write_cache  # type: ignore


SHOW_ID_AI_DAILY = 3
CORE_TYPES = {
    "software_product",
    "model",
    "benchmark",
    "report",
    "survey",
    "paper",
    "account",
    "social_post",
    "blog_post",
}


@dataclass
class QualityResult:
    mention_count: int
    review_count: int
    episode_count: int
    review_rate: float
    mentions_per_episode: float
    core_mentions_per_episode: float
    passed: bool
    reason: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run AI Daily mention extraction until finished")
    p.add_argument("--chunk-size", type=int, default=10, help="Episodes per extraction batch")
    p.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help="How many extraction batches to run in parallel per wave (default: 1)",
    )
    p.add_argument("--max-batches", type=int, default=0, help="Optional cap; 0 = no cap")
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument(
        "--episodes-csv",
        default="codex-notes/2026-02-07-ai-daily-all-episodes.csv",
        help="Episode metadata CSV path",
    )
    p.add_argument("--prompt-version", default="extract_entities_v2_lean_recent50_guarded_cost")
    p.add_argument("--max-review-rate", type=float, default=0.40)
    p.add_argument("--min-mentions-per-episode", type=float, default=5.0)
    p.add_argument("--max-mentions-per-episode", type=float, default=30.0)
    p.add_argument("--min-core-per-episode", type=float, default=3.0)
    p.add_argument("--extract-max-attempts", type=int, default=3)
    p.add_argument("--load-max-attempts", type=int, default=4)
    p.add_argument("--retry-backoff-seconds", type=float, default=4.0)
    p.add_argument(
        "--extract-timeout-seconds",
        type=int,
        default=2400,
        help="Per extraction subprocess timeout (default: 40m)",
    )
    p.add_argument(
        "--load-timeout-seconds",
        type=int,
        default=900,
        help="Per load subprocess timeout (default: 15m)",
    )
    p.add_argument(
        "--run-label",
        default="",
        help="Optional label for this run; default is utc timestamp label",
    )
    return p.parse_args()


def load_environment(repo_root: Path) -> None:
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def get_db_url() -> str:
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return db_url


def get_conn(db_url: str):
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("Missing dependency: psycopg2-binary") from exc
    return psycopg2.connect(db_url)


def is_transient_subprocess_error(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    transient_markers = [
        "rate limit",
        "too many requests",
        "429",
        "timeout",
        "timed out",
        "connection reset",
        "connection aborted",
        "ssl connection has been closed unexpectedly",
        "server disconnected",
        "bad gateway",
        "gateway timeout",
        "service unavailable",
        "temporarily unavailable",
        "remote end closed connection",
    ]
    return any(marker in text for marker in transient_markers)


def run_cmd_with_retries(
    cmd: list[str],
    cwd: Path,
    *,
    max_attempts: int,
    initial_backoff_seconds: float,
    timeout_seconds: int | None = None,
    log_path: Path | None = None,
    cleanup_dir: Path | None = None,
) -> str:
    if max_attempts < 1:
        max_attempts = 1

    for attempt in range(1, max_attempts + 1):
        if cleanup_dir is not None and cleanup_dir.exists():
            shutil.rmtree(cleanup_dir, ignore_errors=True)

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"=== attempt {attempt}/{max_attempts} timeout ===\n")
                    f.write("$ " + " ".join(cmd) + "\n")
                    f.write(f"timeout_seconds={timeout_seconds}\n\n")
            if attempt >= max_attempts:
                raise RuntimeError(
                    f"command timed out after {timeout_seconds}s"
                    + (f"; see log: {log_path}" if log_path else "")
                ) from exc
            backoff = initial_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
            print(
                f"Command timeout (attempt {attempt}/{max_attempts}); retrying in {backoff:.1f}s",
                flush=True,
            )
            time.sleep(backoff)
            continue
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"=== attempt {attempt}/{max_attempts} ===\n")
                f.write("$ " + " ".join(cmd) + "\n\n")
                if stdout:
                    f.write(stdout + "\n")
                if stderr:
                    f.write(stderr + "\n")

        if proc.returncode == 0:
            return stdout

        transient = is_transient_subprocess_error(stdout, stderr)
        if attempt >= max_attempts or not transient:
            raise RuntimeError(
                f"command failed ({proc.returncode})"
                + (f"; see log: {log_path}" if log_path else "")
            )

        backoff = initial_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0, 1.0)
        print(
            f"Transient command failure (attempt {attempt}/{max_attempts}); retrying in {backoff:.1f}s",
            flush=True,
        )
        time.sleep(backoff)

    raise RuntimeError("unreachable")


def acquire_runner_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(
            f"Another run_mentions_until_done.py process appears active (lock: {lock_path})"
        ) from exc

    lock_file.write(f"pid={os.getpid()} started_utc={datetime.utcnow().isoformat()}Z\n")
    lock_file.flush()

    def _cleanup() -> None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            lock_file.close()
        except Exception:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    atexit.register(_cleanup)
    return lock_file


def parse_run_id(loader_output: str) -> int:
    m = re.search(r"Run ID:\s*(\d+)", loader_output)
    if not m:
        raise RuntimeError("Could not parse Run ID from loader output")
    return int(m.group(1))


def fetch_unprocessed_chunk(db_url: str, chunk_size: int) -> list[dict[str, Any]]:
    with get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  e.id,
                  e.title,
                  e.publish_date,
                  et.transcript_text,
                  et.source_type,
                  et.source_url,
                  et.is_generated,
                  et.model
                FROM episodes e
                JOIN shows s ON s.id = e.show_id
                JOIN episode_transcripts et ON et.episode_id = e.id
                WHERE s.id = %s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM ai_mentions m
                    WHERE m.episode_id = e.id
                  )
                ORDER BY e.publish_date DESC NULLS LAST, e.id DESC
                LIMIT %s;
                """,
                (SHOW_ID_AI_DAILY, chunk_size),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "episode_id": int(r[0]),
                "title": r[1] or f"episode-{r[0]}",
                "publish_date": r[2].isoformat() if r[2] else "",
                "transcript_text": r[3] or "",
                "source_type": r[4] or "unknown",
                "source_url": r[5],
                "is_generated": bool(r[6]),
                "model": r[7],
            }
        )
    return out


def split_chunks(rows: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + chunk_size] for i in range(0, len(rows), chunk_size)]


def count_unprocessed(db_url: str) -> int:
    with get_conn(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM episodes e
                JOIN shows s ON s.id = e.show_id
                JOIN episode_transcripts et ON et.episode_id = e.id
                WHERE s.id = %s
                  AND NOT EXISTS (
                    SELECT 1
                    FROM ai_mentions m
                    WHERE m.episode_id = e.id
                  );
                """,
                (SHOW_ID_AI_DAILY,),
            )
            return int(cur.fetchone()[0])


def evaluate_manifest(
    manifest_path: Path,
    *,
    max_review_rate: float,
    min_mentions_per_episode: float,
    max_mentions_per_episode: float,
    min_core_per_episode: float,
) -> tuple[QualityResult, float]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = manifest.get("summary", {}) if isinstance(manifest, dict) else {}
    mention_count = int(summary.get("mention_count", 0) or 0)
    review_count = int(summary.get("review_count", 0) or 0)
    episodes = manifest.get("episodes", []) if isinstance(manifest, dict) else []
    episode_count = max(1, len(episodes))

    counts_by_type = summary.get("counts_by_type", {}) if isinstance(summary, dict) else {}
    core_mentions = 0
    if isinstance(counts_by_type, dict):
        for k, v in counts_by_type.items():
            if k in CORE_TYPES:
                try:
                    core_mentions += int(v)
                except (TypeError, ValueError):
                    pass

    review_rate = (review_count / mention_count) if mention_count > 0 else 1.0
    mentions_per_episode = mention_count / episode_count
    core_mentions_per_episode = core_mentions / episode_count

    if mention_count <= 0:
        result = QualityResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "zero_mentions",
        )
    elif review_rate > max_review_rate:
        result = QualityResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "review_rate_too_high",
        )
    elif mentions_per_episode < min_mentions_per_episode:
        result = QualityResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "mentions_per_episode_too_low",
        )
    elif mentions_per_episode > max_mentions_per_episode:
        result = QualityResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "mentions_per_episode_too_high",
        )
    elif core_mentions_per_episode < min_core_per_episode:
        result = QualityResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "core_mentions_per_episode_too_low",
        )
    else:
        result = QualityResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            True,
            "ok",
        )

    usage = manifest.get("usage_summary", {}) if isinstance(manifest, dict) else {}
    batch_cost = float(usage.get("estimated_total_cost_usd", 0.0) or 0.0)
    return result, batch_cost


def write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def extraction_progress_line(repo_root: Path, spec: dict[str, Any]) -> str:
    batch_name = str(spec["batch_name"])
    episode_ids = spec["episode_ids"]
    total = len(episode_ids)
    batch_dir = repo_root / "codex-notes" / "ai-daily-entity-extraction" / batch_name / "episodes"
    completed = 0
    if batch_dir.exists():
        try:
            completed = len(list(batch_dir.glob("*.json")))
        except OSError:
            completed = 0
    first_ep = episode_ids[0] if episode_ids else "?"
    return f"{batch_name} first_ep={first_ep} progress={completed}/{total}"


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)

    episodes_csv = (repo_root / args.episodes_csv).resolve()
    if not episodes_csv.exists():
        raise FileNotFoundError(f"Episodes CSV not found: {episodes_csv}")

    run_label = args.run_label.strip()
    if not run_label:
        run_label = "fullrun-" + datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    run_dir = repo_root / "codex-notes" / "ai-daily-entity-extraction" / "_full_runs" / run_label
    lock_path = repo_root / "codex-notes" / "ai-daily-entity-extraction" / "_full_runs" / ".runner.lock"
    _runner_lock = acquire_runner_lock(lock_path)
    progress_file = run_dir / "progress.json"
    cache_dir = repo_root / "pipeline" / "_cache" / "ai_daily" / "transcripts"
    python_bin = repo_root / "pipeline" / "venv" / "bin" / "python"
    extract_script = repo_root / "pipeline" / "scrapers" / "ai_daily" / "extract_entities.py"
    load_script = repo_root / "pipeline" / "scrapers" / "ai_daily" / "load_entity_batch.py"

    db_url = get_db_url()
    try:
        remaining_start = count_unprocessed(db_url)
        print(f"Starting unprocessed count: {remaining_start}", flush=True)
        if remaining_start <= 0:
            print("No unprocessed episodes found.", flush=True)
            write_progress(
                progress_file,
                {
                    "run_label": run_label,
                    "status": "completed",
                    "started_at_utc": datetime.utcnow().isoformat() + "Z",
                    "remaining_start": remaining_start,
                    "remaining_now": 0,
                    "batches": [],
                    "total_cost_usd": 0.0,
                },
            )
            return

        batches: list[dict[str, Any]] = []
        total_cost = 0.0
        batch_index = 0

        while True:
            if args.max_batches > 0 and batch_index >= args.max_batches:
                print(f"Reached max_batches={args.max_batches}; stopping.", flush=True)
                break

            workers = max(1, args.parallel_workers)
            if args.max_batches > 0:
                batches_left = args.max_batches - batch_index
                workers = max(1, min(workers, batches_left))

            wave_rows = fetch_unprocessed_chunk(db_url, args.chunk_size * workers)
            if not wave_rows:
                print("No remaining unprocessed episodes.", flush=True)
                break

            chunk_groups = split_chunks(wave_rows, args.chunk_size)
            wave_specs: list[dict[str, Any]] = []
            for chunk in chunk_groups:
                batch_index += 1
                batch_name = f"{run_label}-b{batch_index:03d}"
                episode_ids = [row["episode_id"] for row in chunk]
                wave_specs.append(
                    {
                        "batch_index": batch_index,
                        "batch_name": batch_name,
                        "episode_ids": episode_ids,
                        "rows": chunk,
                    }
                )

            print(
                f"\nWave start: {len(wave_specs)} batch(es), "
                f"episodes={sum(len(s['episode_ids']) for s in wave_specs)}",
                flush=True,
            )
            for spec in wave_specs:
                print(
                    f"- Batch {spec['batch_index']}: {spec['batch_name']} "
                    f"episodes={len(spec['episode_ids'])} first_ep={spec['episode_ids'][0]}",
                    flush=True,
                )
                for row in spec["rows"]:
                    write_cache(
                        cache_dir=cache_dir,
                        episode_id=row["episode_id"],
                        episode_title=row["title"],
                        transcript_text=row["transcript_text"],
                        source_type=row["source_type"],
                        source_url=row["source_url"],
                        is_generated=row["is_generated"],
                        model=row["model"],
                    )

            def run_extract_for_spec(spec: dict[str, Any]) -> dict[str, Any]:
                episodes_arg = ",".join(str(eid) for eid in spec["episode_ids"])
                log_path = run_dir / "logs" / f"{spec['batch_name']}.extract.log"
                batch_dir = repo_root / "codex-notes" / "ai-daily-entity-extraction" / spec["batch_name"]
                run_cmd_with_retries(
                    [
                        str(python_bin),
                        str(extract_script),
                        "--episodes",
                        episodes_arg,
                        "--episodes-csv",
                        str(episodes_csv),
                        "--model",
                        args.model,
                        "--batch-name",
                        spec["batch_name"],
                    ],
                    cwd=repo_root,
                    max_attempts=args.extract_max_attempts,
                    initial_backoff_seconds=args.retry_backoff_seconds,
                    timeout_seconds=args.extract_timeout_seconds,
                    log_path=log_path,
                    cleanup_dir=batch_dir,
                )
                return spec

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(wave_specs)) as executor:
                future_to_spec = {executor.submit(run_extract_for_spec, spec): spec for spec in wave_specs}
                pending = set(future_to_spec.keys())
                heartbeat_every_seconds = 30.0
                last_heartbeat = time.monotonic()

                while pending:
                    done, pending = concurrent.futures.wait(
                        pending,
                        timeout=5.0,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    for fut in done:
                        spec = fut.result()
                        print(f"Extraction completed: {spec['batch_name']}", flush=True)

                    now = time.monotonic()
                    if pending and (now - last_heartbeat) >= heartbeat_every_seconds:
                        lines = [
                            extraction_progress_line(repo_root, future_to_spec[fut])
                            for fut in sorted(
                                pending,
                                key=lambda f: int(future_to_spec[f]["batch_index"]),
                            )
                        ]
                        print("Heartbeat: " + " | ".join(lines), flush=True)
                        last_heartbeat = now

            wave_records: list[dict[str, Any]] = []
            any_quality_fail = False
            fail_reason = ""
            fail_batch_name = ""

            for spec in sorted(wave_specs, key=lambda x: x["batch_index"]):
                batch_dir = repo_root / "codex-notes" / "ai-daily-entity-extraction" / spec["batch_name"]
                manifest_path = batch_dir / "batch_manifest.json"
                if not manifest_path.exists():
                    raise RuntimeError(f"Missing batch manifest: {manifest_path}")

                quality, batch_cost = evaluate_manifest(
                    manifest_path,
                    max_review_rate=args.max_review_rate,
                    min_mentions_per_episode=args.min_mentions_per_episode,
                    max_mentions_per_episode=args.max_mentions_per_episode,
                    min_core_per_episode=args.min_core_per_episode,
                )
                total_cost += batch_cost
                print(
                    f"Quality {spec['batch_name']}: "
                    f"mentions={quality.mention_count}, review_rate={quality.review_rate:.3f}, "
                    f"mentions_per_episode={quality.mentions_per_episode:.2f}, "
                    f"core_per_episode={quality.core_mentions_per_episode:.2f}, "
                    f"status={quality.reason}, batch_cost=${batch_cost:.6f}",
                    flush=True,
                )

                record: dict[str, Any] = {
                    "batch_index": spec["batch_index"],
                    "batch_name": spec["batch_name"],
                    "episode_ids": spec["episode_ids"],
                    "quality": {
                        "mention_count": quality.mention_count,
                        "review_count": quality.review_count,
                        "episode_count": quality.episode_count,
                        "review_rate": quality.review_rate,
                        "mentions_per_episode": quality.mentions_per_episode,
                        "core_mentions_per_episode": quality.core_mentions_per_episode,
                        "passed": quality.passed,
                        "reason": quality.reason,
                    },
                    "estimated_cost_usd": batch_cost,
                    "loaded_run_id": None,
                }
                wave_records.append(record)

                if not quality.passed:
                    any_quality_fail = True
                    fail_reason = quality.reason
                    fail_batch_name = spec["batch_name"]

            if any_quality_fail:
                batches.extend(wave_records)
                remaining_now = count_unprocessed(db_url)
                write_progress(
                    progress_file,
                    {
                        "run_label": run_label,
                        "status": "stopped_quality_failure",
                        "remaining_start": remaining_start,
                        "remaining_now": remaining_now,
                        "batches": batches,
                        "total_cost_usd": total_cost,
                        "failed_batch": fail_batch_name,
                        "failed_reason": fail_reason,
                    },
                )
                raise RuntimeError(
                    f"Quality gate failed for {fail_batch_name}: {fail_reason} "
                    f"(remaining={remaining_now})"
                )

            for record in wave_records:
                batch_dir = repo_root / "codex-notes" / "ai-daily-entity-extraction" / record["batch_name"]
                load_output = run_cmd_with_retries(
                    [
                        str(python_bin),
                        str(load_script),
                        "--batch-dir",
                        str(batch_dir),
                        "--prompt-version",
                        args.prompt_version,
                    ],
                    cwd=repo_root,
                    max_attempts=args.load_max_attempts,
                    initial_backoff_seconds=args.retry_backoff_seconds,
                    timeout_seconds=args.load_timeout_seconds,
                )
                run_id = parse_run_id(load_output)
                record["loaded_run_id"] = run_id
                print(f"Loaded {record['batch_name']} as run_id={run_id}", flush=True)

            batches.extend(wave_records)
            remaining_now = count_unprocessed(db_url)
            write_progress(
                progress_file,
                {
                    "run_label": run_label,
                    "status": "running",
                    "remaining_start": remaining_start,
                    "remaining_now": remaining_now,
                    "batches": batches,
                    "total_cost_usd": total_cost,
                    "last_updated_utc": datetime.utcnow().isoformat() + "Z",
                },
            )
            print(f"Wave complete. Remaining unprocessed episodes: {remaining_now}.", flush=True)

        remaining_now = count_unprocessed(db_url)
        write_progress(
            progress_file,
            {
                "run_label": run_label,
                "status": "completed",
                "remaining_start": remaining_start,
                "remaining_now": remaining_now,
                "batches": batches,
                "total_cost_usd": total_cost,
                "completed_at_utc": datetime.utcnow().isoformat() + "Z",
            },
        )
        print(
            f"\nCompleted run '{run_label}'. "
            f"Remaining unprocessed={remaining_now}. Total estimated cost=${total_cost:.6f}",
            flush=True,
        )
        print(f"Progress file: {progress_file}", flush=True)
    finally:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

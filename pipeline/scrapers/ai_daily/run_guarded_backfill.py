#!/usr/bin/env python3
"""
Guarded AI Daily backfill runner.

Purpose:
- Preflight on a small episode set before large spend.
- Enforce quality gates per extraction batch.
- Stop automatically if quality drifts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv


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
class QualityCheckResult:
    mention_count: int
    review_count: int
    episode_count: int
    review_rate: float
    mentions_per_episode: float
    core_mentions_per_episode: float
    passed: bool
    reason: str


def run_cmd(cmd: Sequence[str], cwd: Path) -> str:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout, flush=True)
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc.stdout


def parse_run_id(load_output: str) -> int:
    m = re.search(r"Run ID:\s*(\d+)", load_output)
    if not m:
        raise RuntimeError("Could not parse Run ID from loader output")
    return int(m.group(1))


def chunked(values: list[int], size: int) -> list[list[int]]:
    out: list[list[int]] = []
    for i in range(0, len(values), size):
        out.append(values[i : i + size])
    return out


def evaluate_batch_manifest(
    manifest_path: Path,
    *,
    max_review_rate: float,
    min_mentions_per_episode: float,
    max_mentions_per_episode: float,
    min_core_per_episode: float,
) -> QualityCheckResult:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = manifest.get("summary", {})
    mention_count = int(summary.get("mention_count", 0))
    review_count = int(summary.get("review_count", 0))
    episodes = manifest.get("episodes", [])
    episode_count = max(1, len(episodes))

    review_rate = (review_count / mention_count) if mention_count > 0 else 1.0
    mentions_per_episode = mention_count / episode_count
    counts_by_type = summary.get("counts_by_type", {}) if isinstance(summary, dict) else {}
    core_mentions = 0
    if isinstance(counts_by_type, dict):
        for k, v in counts_by_type.items():
            if k in CORE_TYPES:
                try:
                    core_mentions += int(v)
                except (TypeError, ValueError):
                    pass
    core_mentions_per_episode = core_mentions / episode_count

    if mention_count <= 0:
        return QualityCheckResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "zero_mentions",
        )
    if review_rate > max_review_rate:
        return QualityCheckResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "review_rate_too_high",
        )
    if mentions_per_episode < min_mentions_per_episode:
        return QualityCheckResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "mentions_per_episode_too_low",
        )
    if mentions_per_episode > max_mentions_per_episode:
        return QualityCheckResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "mentions_per_episode_too_high",
        )
    if core_mentions_per_episode < min_core_per_episode:
        return QualityCheckResult(
            mention_count,
            review_count,
            episode_count,
            review_rate,
            mentions_per_episode,
            core_mentions_per_episode,
            False,
            "core_mentions_per_episode_too_low",
        )
    return QualityCheckResult(
        mention_count,
        review_count,
        episode_count,
        review_rate,
        mentions_per_episode,
        core_mentions_per_episode,
        True,
        "ok",
    )


def load_environment(repo_root: Path) -> None:
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run guarded AI Daily transcript/entity backfill")
    p.add_argument("--since-date", required=True, help="YYYY-MM-DD")
    p.add_argument("--feed-limit", type=int, default=400, help="RSS items to scan per transcript pass")
    p.add_argument("--preflight-new", type=int, default=10, help="How many new transcripts to do in preflight")
    p.add_argument("--run-full", action="store_true", help="If set, continue with full backfill after preflight")
    p.add_argument("--full-max-new", type=int, default=0, help="Cap full run transcripts (0=no cap)")
    p.add_argument("--chunk-size", type=int, default=20, help="Extraction chunk size for full run")
    p.add_argument("--stt-model", default="whisper-1")
    p.add_argument("--extract-model", default="gpt-4.1-mini")
    p.add_argument("--prompt-version", default="extract_entities_v2_lean_guarded")
    p.add_argument("--max-review-rate", type=float, default=0.40)
    p.add_argument("--min-mentions-per-episode", type=float, default=5.0)
    p.add_argument("--max-mentions-per-episode", type=float, default=30.0)
    p.add_argument("--min-core-per-episode", type=float, default=3.0)
    p.add_argument("--skip-link-discovery", action="store_true")
    p.add_argument("--link-limit", type=int, default=5000)
    return p.parse_args()


def run_transcript_pass(
    *,
    repo_root: Path,
    since_date: str,
    feed_limit: int,
    stt_model: str,
    max_new: int,
    label: str,
) -> dict:
    summary_path = (
        repo_root
        / "codex-notes"
        / "ai-daily-entity-extraction"
        / "_guarded_runs"
        / f"{label}-transcripts-summary.json"
    )
    cmd = [
        str(repo_root / "pipeline" / "venv" / "bin" / "python"),
        "pipeline/scrapers/ai_daily/transcripts.py",
        "--limit",
        str(feed_limit),
        "--since-date",
        since_date,
        "--model",
        stt_model,
        "--summary-out",
        str(summary_path),
    ]
    if max_new > 0:
        cmd.extend(["--max-new", str(max_new)])
    run_cmd(cmd, cwd=repo_root)
    return json.loads(summary_path.read_text(encoding="utf-8"))


def run_extract_and_load(
    *,
    repo_root: Path,
    episode_ids: list[int],
    batch_name: str,
    extract_model: str,
    prompt_version: str,
    thresholds: argparse.Namespace,
) -> tuple[int, QualityCheckResult, Path]:
    episodes_csv = ",".join(str(e) for e in episode_ids)
    extract_cmd = [
        str(repo_root / "pipeline" / "venv" / "bin" / "python"),
        "pipeline/scrapers/ai_daily/extract_entities.py",
        "--episodes",
        episodes_csv,
        "--model",
        extract_model,
        "--batch-name",
        batch_name,
    ]
    run_cmd(extract_cmd, cwd=repo_root)

    batch_dir = repo_root / "codex-notes" / "ai-daily-entity-extraction" / batch_name
    manifest_path = batch_dir / "batch_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"Missing batch manifest: {manifest_path}")

    quality = evaluate_batch_manifest(
        manifest_path,
        max_review_rate=thresholds.max_review_rate,
        min_mentions_per_episode=thresholds.min_mentions_per_episode,
        max_mentions_per_episode=thresholds.max_mentions_per_episode,
        min_core_per_episode=thresholds.min_core_per_episode,
    )
    print(
        f"Quality check [{batch_name}]: mentions={quality.mention_count}, review_rate={quality.review_rate:.3f}, "
        f"mentions/ep={quality.mentions_per_episode:.2f}, core/ep={quality.core_mentions_per_episode:.2f}, "
        f"status={quality.reason}",
        flush=True,
    )
    if not quality.passed:
        raise RuntimeError(f"Quality gate failed for {batch_name}: {quality.reason}")

    load_cmd = [
        str(repo_root / "pipeline" / "venv" / "bin" / "python"),
        "pipeline/scrapers/ai_daily/load_entity_batch.py",
        "--batch-dir",
        str(batch_dir),
        "--prompt-version",
        prompt_version,
    ]
    load_output = run_cmd(load_cmd, cwd=repo_root)
    run_id = parse_run_id(load_output)
    return run_id, quality, batch_dir


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)

    firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    print(f"Firecrawl key present: {'yes' if firecrawl_key else 'no'}", flush=True)

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    run_ids_loaded: list[int] = []

    # 1) Preflight transcript generation
    preflight_label = f"guarded-preflight-{args.since_date}-{stamp}"
    pre_summary = run_transcript_pass(
        repo_root=repo_root,
        since_date=args.since_date,
        feed_limit=args.feed_limit,
        stt_model=args.stt_model,
        max_new=args.preflight_new,
        label=preflight_label,
    )
    pre_ids = [int(x) for x in pre_summary.get("created_episode_ids", [])]
    if not pre_ids:
        print("No new transcripts created in preflight. Nothing to process.", flush=True)
        return

    # 2) Preflight extract + quality gate + load
    pre_batch_name = f"{preflight_label}-entities"
    pre_run_id, _, _ = run_extract_and_load(
        repo_root=repo_root,
        episode_ids=pre_ids,
        batch_name=pre_batch_name,
        extract_model=args.extract_model,
        prompt_version=args.prompt_version,
        thresholds=args,
    )
    run_ids_loaded.append(pre_run_id)

    # 3) Full mode (optional)
    if args.run_full:
        full_label = f"guarded-full-{args.since_date}-{stamp}"
        full_summary = run_transcript_pass(
            repo_root=repo_root,
            since_date=args.since_date,
            feed_limit=args.feed_limit,
            stt_model=args.stt_model,
            max_new=args.full_max_new,
            label=full_label,
        )
        full_ids = [int(x) for x in full_summary.get("created_episode_ids", [])]
        # Remove anything already handled in preflight.
        full_ids = [x for x in full_ids if x not in set(pre_ids)]
        if full_ids:
            for idx, chunk in enumerate(chunked(full_ids, args.chunk_size), start=1):
                batch_name = f"{full_label}-chunk-{idx:02d}"
                run_id, _, _ = run_extract_and_load(
                    repo_root=repo_root,
                    episode_ids=chunk,
                    batch_name=batch_name,
                    extract_model=args.extract_model,
                    prompt_version=args.prompt_version,
                    thresholds=args,
                )
                run_ids_loaded.append(run_id)

    # 4) Alias normalization
    run_cmd(
        [
            str(repo_root / "pipeline" / "venv" / "bin" / "python"),
            "pipeline/scrapers/ai_daily/normalize_aliases.py",
        ],
        cwd=repo_root,
    )

    # 5) Link discovery (optional)
    if not args.skip_link_discovery:
        if firecrawl_key:
            run_cmd(
                [
                    str(repo_root / "pipeline" / "venv" / "bin" / "python"),
                    "pipeline/scrapers/ai_daily/discover_links.py",
                    "--run-ids",
                    ",".join(str(x) for x in run_ids_loaded),
                    "--limit",
                    str(args.link_limit),
                ],
                cwd=repo_root,
            )
        else:
            print("Skipping link discovery: FIRECRAWL_API_KEY missing.", flush=True)

    if run_ids_loaded:
        run_cmd(
            [
                str(repo_root / "pipeline" / "venv" / "bin" / "python"),
                "pipeline/scrapers/ai_daily/report_summary.py",
                "--run-id",
                str(run_ids_loaded[-1]),
                "--top",
                "25",
            ],
            cwd=repo_root,
        )
    print(f"Guarded backfill complete. Runs loaded: {run_ids_loaded}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Run the extraction eval: re-extract the frozen episodes with the CURRENT model and
prompt, and score the result two ways.

Why this exists (AI-memory primer): "evals are the single highest-leverage investment;
the first thing you do before any architecture change is build the eval harness." Right
now a model bump or prompt edit ships on vibes. This is the gradient you run before and
after such a change — and it also answers the laptop primer's fifth question, "how will
you know the output stayed good?"

It scores deterministically — precision/recall/type-accuracy/drift — never an LLM judge,
because LLM judges have transitivity violations and position bias. (A narrow LLM check
like "is this canonical_name a real product?" could slot in later; it is deliberately
NOT used to grade overall quality.)

Cost: one real OpenAI call per fixture episode (re-extraction). Use --limit for a quick
read; the full set is ~30 episodes.

  ./pipeline/venv/bin/python evals/extraction/run_eval.py                 # both, current model
  ./pipeline/venv/bin/python evals/extraction/run_eval.py --limit 6       # quick
  ./pipeline/venv/bin/python evals/extraction/run_eval.py --model gpt-5-mini --json out.json
  ./pipeline/venv/bin/python evals/extraction/run_eval.py --ci            # gate + Slack on fail
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evals.extraction.metrics import (  # noqa: E402
    aggregate,
    collapse_to_entities,
    confidence_report,
    distribution_shift,
    score_precision_recall,
    score_regression,
    type_counts,
)
from pipeline.common import get_db_connection, load_environment, post_slack  # noqa: E402
from pipeline.scrapers.ai_daily.sponsors import roster_from_raw_content  # noqa: E402
from pipeline.scrapers.ai_daily.extract_entities import (  # noqa: E402
    DEFAULT_MODEL,
    EpisodeInput,
    get_profile,
    openai_extract,
    process_episode_mentions,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# CI gate floors. These are the contract: cross them and the eval fails loudly.
#
# Calibrated to MEASURED same-model noise, not vibes (see evals/README.md). gpt-4.1-mini
# extraction has ~40% run-to-run SET churn at temp 0, so per-episode Jaccard / core_recall
# are REPORTED diagnostics, NOT gates — gating on them would cry wolf every run. The gate
# is the stable aggregate signals: the confidence contract, no failed episodes, entity
# YIELD staying in a band (same-model yield was ~0.92), the type-distribution mix not
# lurching, and gold precision/recall when a hand-verified fixture exists.
#
# Tune deliberately (note why in the commit) — loosening a floor is a real decision.
FLOORS = {
    "yield_ratio_min": 0.55,      # extracting far fewer entities than baseline = regression
    "yield_ratio_max": 1.70,      # or far more (over-extraction / noise)
    "type_shift_max": 0.20,       # max per-type proportion move in the entity mix
    "gold_recall": 0.60,          # catch >=60% of hand-verified entities (raise after calibrating same-model gold)
    "gold_type_accuracy": 0.80,   # type matched entities right >=80% of the time
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the extraction eval harness")
    p.add_argument("--mode", choices=["both", "baseline", "gold"], default="both")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Model to re-extract with (override to test a candidate)")
    p.add_argument("--limit", type=int, default=0, help="Only score the first N fixture episodes (0 = all)")
    p.add_argument("--workers", type=int, default=3, help="Concurrent re-extractions")
    p.add_argument("--json", default="", help="Write the full machine-readable report here")
    p.add_argument("--ci", action="store_true", help="Exit non-zero (and Slack) if floors are breached")
    p.add_argument("--baseline-fixture", default=str(FIXTURES_DIR / "golden_baseline.json"))
    p.add_argument("--gold-fixture", default=str(FIXTURES_DIR / "gold_verified.json"))
    return p.parse_args()


def load_fixture(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def index_by_episode(fixture: dict) -> dict[int, dict]:
    return {ep["episode_id"]: ep for ep in fixture.get("episodes", [])}


def pull_transcript(conn, episode_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.id, ep.title, ep.publish_date, s.slug AS show_slug,
                   ep.raw_content,
                   COALESCE(et.transcript_text, ep.description_body) AS transcript_text
            FROM episodes ep
            JOIN shows s ON s.id = ep.show_id
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.id = %s
            """,
            (episode_id,),
        )
        return cur.fetchone()


def reextract(api_key: str, model: str, row: dict, max_chars: int) -> tuple[dict, dict]:
    """Re-extract one episode through the EXACT production path and collapse to entities.
    Returns (extracted_entities, confidence_report)."""
    transcript = (row["transcript_text"] or "")[:max_chars]
    episode = EpisodeInput(
        episode_id=row["id"],
        publish_date=str(row["publish_date"]) if row["publish_date"] else "",
        title=row["title"] or "",
        episode_url="",
        transcript_path=Path("/dev/null"),  # unused by openai_extract
    )
    profile = get_profile("entity_extraction")
    raw, _usage = openai_extract(api_key, model, episode, transcript, profile)
    # Sponsor detection is part of the production path as of 2026-09-02 (ads are kept
    # and tagged, not dropped), so the eval feeds it the same two inputs the orchestrator
    # does — the episode's declared roster and the truncated transcript. Without them
    # this function would quietly stop being "the EXACT production path" it claims to be,
    # and the yield it measures would be a number production never produces.
    mentions = process_episode_mentions(
        raw,
        row["id"],
        profile,
        roster=roster_from_raw_content(row.get("raw_content")),
        transcript_text=transcript,
    )
    return collapse_to_entities(mentions), confidence_report(mentions)


def input_sha(transcript_text: str, max_chars: int) -> str:
    return hashlib.sha256(((transcript_text or "")[:max_chars]).encode("utf-8")).hexdigest()


def score_episode(api_key, model, episode_id, row, baseline_ep, gold_ep) -> dict:
    """Re-extract one episode (transcript already fetched) and score it.

    Takes a pre-fetched `row` rather than a DB connection: transcripts are pulled
    serially up front so the concurrent path here touches only OpenAI + pure scoring.
    A single psycopg2 connection is NOT safe for concurrent use across threads.
    """
    ref = baseline_ep or gold_ep
    max_chars = ref.get("max_chars", 50000)
    if not row or not row.get("transcript_text"):
        return {"episode_id": episode_id, "error": "no transcript in Neon"}

    drifted = input_sha(row["transcript_text"], max_chars) != ref.get("input_sha256")
    extracted, conf = reextract(api_key, model, row, max_chars)

    result: dict = {
        "episode_id": episode_id,
        "show_slug": row["show_slug"],
        "title": row["title"],
        "input_drifted": drifted,
        "n_extracted": len(extracted),
        "extracted_type_counts": type_counts(extracted.values()),
        "confidence": conf,
    }
    if baseline_ep is not None:
        result["regression"] = score_regression(extracted, baseline_ep["entities"])
        result["baseline_type_counts"] = type_counts(baseline_ep["entities"].values())
    if gold_ep is not None:
        result["gold"] = score_precision_recall(extracted, gold_ep["entities"])
    return result


def build_report(args, results: list[dict], baseline_meta: dict, gold_meta: dict) -> dict:
    ok = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]

    reg_results = [r for r in ok if "regression" in r]
    regs = [r["regression"] for r in reg_results]
    golds = [r["gold"] for r in ok if "gold" in r]
    confs = [r["confidence"] for r in ok]

    conf_all_in_range = all(c["all_in_range"] for c in confs) if confs else True
    # Out-of-range breaches the contract; missing does not (see confidence_report's
    # docstring — a NULL confidence is the sanitizer being honest, not a defect).
    # Both are reported, so a run that starts producing many NULLs is still visible.
    conf_out = sum(c["n_out_of_range"] for c in confs)
    conf_missing = sum(c["n_missing"] for c in confs)

    baseline_section = None
    if regs:
        # Pool entity yield + type distribution over the episodes that have a baseline —
        # these aggregate signals are the stable ones (per-episode set identity is noise).
        pooled_now: Counter = Counter()
        pooled_base: Counter = Counter()
        for r in reg_results:
            pooled_now.update(r.get("extracted_type_counts", {}))
            pooled_base.update(r.get("baseline_type_counts", {}))
        n_now = sum(reg["n_now"] for reg in regs)
        n_base = sum(reg["n_baseline"] for reg in regs)
        baseline_section = {
            "n_episodes": len(regs),
            "yield_ratio": round(n_now / n_base, 4) if n_base else None,
            "n_now_total": n_now,
            "n_baseline_total": n_base,
            "type_shift": distribution_shift(dict(pooled_now), dict(pooled_base)),
            # Reported diagnostics — NOT gated (per-episode set churn is inherent noise):
            **aggregate(regs, ["jaccard", "core_recall", "mean_abs_conf_delta"]),
            "total_dropped": sum(reg["n_dropped"] for reg in regs),
            "total_added": sum(reg["n_added"] for reg in regs),
            "total_type_changes": sum(reg["n_type_changes"] for reg in regs),
        }

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "model": args.model,
        "baseline_model": baseline_meta.get("baseline_model"),
        "n_scored": len(ok),
        "n_failed": len(failed),
        "n_input_drifted": sum(1 for r in ok if r.get("input_drifted")),
        "baseline": baseline_section,
        "gold": {
            "n_episodes": len(golds),
            **aggregate(golds, ["precision", "recall", "f1", "type_accuracy"]),
        }
        if golds
        else None,
        "confidence": {
            "all_in_range": conf_all_in_range,
            "n_out_of_range": conf_out,
            "n_missing": conf_missing,
        },
        "failed_episodes": [{"episode_id": r["episode_id"], "error": r["error"]} for r in failed],
        "episodes": results,
    }
    return report


def check_floors(report: dict) -> list[str]:
    """Return a list of floor-breach messages (empty = pass).

    Gates on STABLE signals only — the confidence contract, no failed episodes, entity
    yield, type-distribution mix, and gold precision/recall. Per-episode Jaccard and
    core_recall are deliberately NOT gated: measured same-model churn is ~40%, so gating
    on set identity would fail every run (the whole reason this gate was recalibrated).
    """
    breaches: list[str] = []
    if report["n_failed"]:
        breaches.append(f"{report['n_failed']} episode(s) failed to extract")
    if not report["confidence"]["all_in_range"]:
        breaches.append(
            f"{report['confidence']['n_out_of_range']} confidence value(s) outside [0,1]"
        )
    base = report.get("baseline")
    if base:
        yr = base.get("yield_ratio")
        if yr is not None and (yr < FLOORS["yield_ratio_min"] or yr > FLOORS["yield_ratio_max"]):
            breaches.append(
                f"entity yield ratio {yr} outside [{FLOORS['yield_ratio_min']}, {FLOORS['yield_ratio_max']}]"
            )
        shift = base.get("type_shift", {}).get("max_abs_delta")
        if shift is not None and shift > FLOORS["type_shift_max"]:
            worst = next(iter(base["type_shift"]["deltas"].items()), (None, None))
            breaches.append(
                f"type-distribution shift {shift} > {FLOORS['type_shift_max']} (worst: {worst[0]} {worst[1]:+})"
            )
    gold = report.get("gold")
    if gold:
        if gold["recall"] is not None and gold["recall"] < FLOORS["gold_recall"]:
            breaches.append(f"gold recall {gold['recall']} < {FLOORS['gold_recall']}")
        if gold["type_accuracy"] is not None and gold["type_accuracy"] < FLOORS["gold_type_accuracy"]:
            breaches.append(f"gold type_accuracy {gold['type_accuracy']} < {FLOORS['gold_type_accuracy']}")
    return breaches


def print_report(report: dict) -> None:
    print("\n=== Extraction Eval ===", flush=True)
    print(f"Generated: {report['generated_at']}", flush=True)
    print(f"Model: {report['model']}  (baseline captured with {report['baseline_model']})", flush=True)
    print(f"Scored: {report['n_scored']} episodes  |  failed: {report['n_failed']}  |  input drifted: {report['n_input_drifted']}", flush=True)

    base = report.get("baseline")
    if base:
        ts = base["type_shift"]
        print("\nvs known-good baseline — GATED aggregate signals:", flush=True)
        print(f"  entity yield ratio:  {base['yield_ratio']}   ({base['n_now_total']} now / {base['n_baseline_total']} baseline)", flush=True)
        print(f"  type-distribution shift: {ts['max_abs_delta']}   (max per-type proportion move)", flush=True)
        print("  diagnostics (NOT gated — extraction has ~40% inherent set churn):", flush=True)
        print(f"    macro Jaccard {base['jaccard']} | macro core_recall {base['core_recall']} | mean |Δconf| {base['mean_abs_conf_delta']}", flush=True)
        print(f"    dropped/added/type-changes: {base['total_dropped']}/{base['total_added']}/{base['total_type_changes']}", flush=True)

    gold = report.get("gold")
    if gold:
        print(f"\nCORRECTNESS vs hand-verified gold ({gold['n_episodes']} eps, must-include set):", flush=True)
        print(f"  recall: {gold['recall']}   type-accuracy: {gold['type_accuracy']}   <- GATED", flush=True)
        print(f"  precision: {gold['precision']} / F1 {gold['f1']}   (NOT gated — gold is must-include, not exhaustive)", flush=True)
    else:
        print("\nCORRECTNESS: no gold_verified fixture for these episodes (baseline-only).", flush=True)

    c = report["confidence"]
    print(
        f"\nCONFIDENCE contract: {'PASS' if c['all_in_range'] else 'FAIL'}  "
        f"({c['n_out_of_range']} outside [0,1], gated; {c['n_missing']} missing, reported)",
        flush=True,
    )

    if report["failed_episodes"]:
        print("\nFAILED:", flush=True)
        for f in report["failed_episodes"]:
            print(f"  - episode {f['episode_id']}: {f['error']}", flush=True)


def main() -> None:
    args = parse_args()
    load_environment(REPO_ROOT)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required")

    baseline_fx = load_fixture(Path(args.baseline_fixture)) if args.mode in ("both", "baseline") else {}
    gold_fx = load_fixture(Path(args.gold_fixture)) if args.mode in ("both", "gold") else {}
    baseline_idx = index_by_episode(baseline_fx)
    gold_idx = index_by_episode(gold_fx)

    episode_ids = sorted(set(baseline_idx) | set(gold_idx))
    if not episode_ids:
        raise SystemExit("No fixture episodes found — build the baseline first (build_baseline.py).")
    if args.limit:
        episode_ids = episode_ids[: args.limit]

    # Pull all transcripts SERIALLY first (a single connection isn't thread-safe), then
    # parallelize only the OpenAI re-extraction + pure scoring.
    conn = get_db_connection()
    try:
        rows = {eid: pull_transcript(conn, eid) for eid in episode_ids}
    finally:
        conn.close()

    print(f"Re-extracting {len(episode_ids)} episodes with {args.model} ({args.workers} workers)...", flush=True)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                score_episode, api_key, args.model, eid, rows.get(eid), baseline_idx.get(eid), gold_idx.get(eid)
            ): eid
            for eid in episode_ids
        }
        done = 0
        for fut in as_completed(futures):
            eid = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001 — isolate a bad episode, keep the run
                results.append({"episode_id": eid, "error": f"{type(exc).__name__}: {exc}"})
            done += 1
            print(f"  [{done}/{len(episode_ids)}] episode {eid}", flush=True)

    results.sort(key=lambda r: r["episode_id"])
    report = build_report(args, results, baseline_fx.get("_meta", {}), gold_fx.get("_meta", {}))
    print_report(report)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"\nWrote {args.json}", flush=True)

    breaches = check_floors(report)
    if breaches:
        msg = "extraction eval FAILED:\n- " + "\n- ".join(breaches)
        print(f"\n❌ {msg}", flush=True)
        if args.ci:
            post_slack(f":rotating_light: *list-maker {msg}* (model={args.model})")
            sys.exit(1)
    else:
        print("\n✅ all floors held", flush=True)


if __name__ == "__main__":
    main()

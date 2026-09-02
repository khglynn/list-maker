#!/usr/bin/env python3
"""The intake judge's honest gradient: run the frozen labeled set, score it, gate it.

    ./pipeline/venv/bin/python evals/intake/run_eval.py                 # both judges, full set
    ./pipeline/venv/bin/python evals/intake/run_eval.py --limit 10      # a quick read
    ./pipeline/venv/bin/python evals/intake/run_eval.py --model openai/gpt-5.6-luna   # one model alone
    ./pipeline/venv/bin/python evals/intake/run_eval.py --ci --json report.json      # the weekly gate

Ground truth: evals/intake/fixtures/labeled_candidates.json — real posts with Kevin's
save/skip label (pre-labeled by a model panel against the rubric, corrected by him in
one pass, 2026-09). Metrics are arithmetic on those labels (evals/intake/metrics.py).
Floors: recall on `save` >= 0.90 (missing a report he needed is the expensive error),
precision >= 0.70 (an extra launch post is cheap). Auto-ingest stays off until this
passes (arc plan, "Eval").

Texts are not in the repo (public; the posts are other people's copyright): the
fixture holds a sha256 of what the judge saw, texts live in pipeline/_cache/intake-eval/
(gitignored) and are re-scraped when missing. A page whose text changed since labeling
is reported as drifted, not failed — a live edit upstream is not a judge regression.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evals.intake.metrics import agreement, by_source, check_floors, confusion, mismatches, recall_precision  # noqa: E402
from pipeline.common import get_logger, load_environment, post_slack  # noqa: E402
from pipeline.scrapers.intake import judge as J  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "labeled_candidates.json"
TEXT_CACHE = REPO_ROOT / "pipeline" / "_cache" / "intake-eval"
FLOORS = {"recall_save": 0.90, "precision_save": 0.70}
log = get_logger("evals.intake")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_fixture(path: Path = FIXTURE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cached_text(cand: dict, scrape: Optional[Callable[[str], str]] = None, cache: Path = TEXT_CACHE) -> tuple[str, bool]:
    """(text, drifted). Scrapes into the cache when missing; drifted = sha differs from labeling time."""
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{cand['id']}.txt"
    if not f.exists():
        if scrape is None:
            raise FileNotFoundError(f"no cached text for {cand['id']} and no scraper given")
        f.write_text(scrape(cand["url"]), encoding="utf-8")
    text = f.read_text(encoding="utf-8")
    return text, sha(text) != cand.get("text_sha256")


def default_scrape(url: str) -> str:
    from pipeline.scrapers.blog.import_blog import scrape_post
    key = os.getenv("FIRECRAWL_API_KEY")
    if not key:
        raise SystemExit("FIRECRAWL_API_KEY is required to fetch missing eval texts")
    return (scrape_post(url, key)["markdown"] or "").strip()


def judge_row(cand: dict, text: str, decide: Callable[..., J.Decision]) -> dict:
    d = decide(title=cand["title"], source=cand["source"], published_on=cand.get("published_on") or "",
               category=cand.get("category") or [], words=cand.get("words"), links_out=cand.get("links_out"),
               found_via=cand.get("found_via") or cand["source"], text=text)
    return {
        "id": cand["id"], "title": cand["title"], "source": cand["source"], "label": cand["label"],
        "note": cand.get("note"), "verdict": d.verdict, "confidence": d.confidence, "reason": d.reason,
        "judge_verdict": d.judge.verdict, "judge_model": d.judge.model,
        "checker_verdict": d.checker.verdict if d.checker else None,
        "checker_model": d.checker.model if d.checker else None,
        "disputed": d.disputed, "prompt_version": d.prompt_version,
        "rule": d.rule, "job": d.job,
    }


def run(candidates: list[dict], decide: Callable[..., J.Decision], text_for: Callable[[dict], tuple[str, bool]],
        workers: int = 4) -> dict:
    rows: list[dict] = []
    drifted: list[str] = []
    failed: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for c in candidates:
            try:
                text, drift = text_for(c)
            except Exception as exc:  # noqa: BLE001 — one unreachable page must not sink the run
                failed.append({"id": c["id"], "title": c["title"], "error": str(exc)[:200]})
                continue
            if drift:
                drifted.append(c["id"])
            futures[pool.submit(judge_row, c, text, decide)] = c
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                rows.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                failed.append({"id": c["id"], "title": c["title"], "error": str(exc)[:200]})
    rows.sort(key=lambda r: r["id"])
    conf = confusion(rows)
    scores = recall_precision(conf)
    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n_labeled": len(candidates), "n_scored": len(rows), "n_failed": len(failed), "n_drifted": len(drifted),
        "confusion": conf, **scores, "agreement": agreement(rows), "by_source": by_source(rows),
        "mismatches": mismatches(rows), "failed": failed, "drifted": drifted, "rows": rows,
        "prompt_version": rows[0]["prompt_version"] if rows else None,
        "models": sorted({r["judge_model"] for r in rows} | {r["checker_model"] for r in rows if r["checker_model"]}),
    }
    report["breaches"] = check_floors(scores, FLOORS)
    if failed:
        report["breaches"].append(f"{len(failed)} candidate(s) could not be judged")
    return report


def print_report(rep: dict) -> None:
    print(f"\nintake eval — {rep['n_scored']}/{rep['n_labeled']} scored, {rep['n_failed']} failed, {rep['n_drifted']} drifted"
          f" · rubric {rep['prompt_version']} · models {', '.join(rep['models'])}")
    c = rep["confusion"]
    print(f"  recall(save)={rep['recall_save']}  precision(save)={rep['precision_save']}  "
          f"tp={c['tp']} fp={c['fp']} fn={c['fn']} tn={c['tn']}")
    a = rep["agreement"]
    print(f"  judges agreed {a['agreement_rate']} of {a['judged_twice']}; disputed {a['disputed']} "
          f"(of which correct after the save-first rule: {a['disputed_correct']})")
    for src, s in rep["by_source"].items():
        print(f"  {src:22} n={s['n']:3} recall={s['recall_save']} precision={s['precision_save']}")
    if rep["mismatches"]:
        print("  mismatches (false negatives first):")
        for m in rep["mismatches"]:
            print(f"   - [{m['label']}→{m['verdict']} {m['confidence']}] {m['title'][:60]} — {m['reason'][:90]}")
    for f in rep["failed"]:
        print(f"  FAILED {f['title'][:60]}: {f['error']}")
    print("  FLOORS:", "ok" if not rep["breaches"] else "; ".join(rep["breaches"]))


def main() -> None:
    p = argparse.ArgumentParser(description="Run the intake judge eval")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--model", default="", help="One model alone (no checker), e.g. openai/gpt-5.6-luna")
    p.add_argument("--json", default="", help="Write the full report here")
    p.add_argument("--ci", action="store_true", help="Exit 1 + Slack on a floor breach")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    load_environment()
    fixture = load_fixture()
    cands = fixture["candidates"][: args.limit or None]
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required")
    rubric, version = J.load_rubric()

    def decide(**kw) -> J.Decision:
        msgs = J.build_messages(rubric, **kw)
        if args.model:
            return J.decide(J.judge_once(msgs, (args.model,), api_key), None, version)
        return J.decide(J.judge_once(msgs, J.JUDGE_MODELS, api_key), J.judge_once(msgs, J.CHECKER_MODELS, api_key), version)

    rep = run(cands, decide, lambda c: cached_text(c, default_scrape), workers=args.workers)
    print_report(rep)
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    if rep["breaches"] and args.ci:
        post_slack(":rotating_light: *list-maker: intake judge eval FAILED* — " + "; ".join(rep["breaches"]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

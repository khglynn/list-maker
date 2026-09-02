"""Deterministic graders for the intake judge. No model in the loop here, on purpose:
"LLM-as-judge for evaluating your own outputs" is the failure mode the memory
rollup warns about; the ground truth is Kevin's label and the metric is arithmetic."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional


def confusion(rows: list[dict[str, Any]]) -> dict[str, int]:
    """rows: [{label: save|skip, verdict: save|skip}]. Positive class = save."""
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for r in rows:
        want, got = r["label"] == "save", r["verdict"] == "save"
        if want and got:
            c["tp"] += 1
        elif got:
            c["fp"] += 1
        elif want:
            c["fn"] += 1
        else:
            c["tn"] += 1
    return c


def recall_precision(c: dict[str, int]) -> dict[str, Optional[float]]:
    """None when undefined (no positives labeled / none predicted) — a floor can't
    pass on an empty denominator, and a 0.0 there would hide that."""
    pos = c["tp"] + c["fn"]
    pred = c["tp"] + c["fp"]
    return {
        "recall_save": round(c["tp"] / pos, 4) if pos else None,
        "precision_save": round(c["tp"] / pred, 4) if pred else None,
        "n": sum(c.values()),
    }


def agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the two judges agreed, and how the disputed rows landed."""
    both = [r for r in rows if r.get("checker_verdict")]
    agreed = sum(1 for r in both if r["checker_verdict"] == r["judge_verdict"])
    disputed = [r for r in both if r["checker_verdict"] != r["judge_verdict"]]
    return {
        "judged_twice": len(both),
        "agreement_rate": round(agreed / len(both), 4) if both else None,
        "disputed": len(disputed),
        "disputed_correct": sum(1 for r in disputed if r["verdict"] == r["label"]),
    }


def by_source(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r.get("source", "?")].append(r)
    return {src: {**recall_precision(confusion(rs)), "confusion": confusion(rs)} for src, rs in sorted(groups.items())}


def mismatches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows worth a human's eyes, false negatives first (they cost the most)."""
    wrong = [r for r in rows if r["label"] != r["verdict"]]
    wrong.sort(key=lambda r: (r["label"] != "save", -(r.get("confidence") or 0)))
    return [{k: r.get(k) for k in ("id", "title", "source", "label", "verdict", "confidence", "reason", "disputed", "note")} for r in wrong]


def check_floors(scores: dict[str, Optional[float]], floors: dict[str, float]) -> list[str]:
    """Breaches as plain sentences; an undefined score is a breach (it can't have passed)."""
    out = []
    for key, floor in floors.items():
        value = scores.get(key)
        if value is None:
            out.append(f"{key} undefined (no rows to score)")
        elif value < floor:
            out.append(f"{key} {value:.3f} < floor {floor:.2f}")
    return out

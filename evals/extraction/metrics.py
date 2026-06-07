#!/usr/bin/env python3
"""Deterministic scorers for the extraction eval harness.

Per the AI-memory primer ("How AI Systems Remember"): evals are the only honest
gradient, and deterministic metrics — not an LLM judge — are what you trust for
grading. LLM-as-judge has transitivity violations and position bias; reserve it for
narrow checks ("is this canonical_name a real product?"), never for overall quality.

So everything here is pure: no DB, no network, no LLM. These functions turn two sets
of extracted entities into honest numbers. They unit-test fast and can't drift.

Two reference modes, because they answer different questions:

  score_precision_recall(extracted, expected)
      `expected` is HAND-VERIFIED ground truth (complete for the episode). This is
      the honest correctness measure: precision, recall, F1, type accuracy.

  score_regression(extracted, baseline)
      `baseline` is a captured known-good snapshot (NOT necessarily complete truth).
      This is the drift measure for "did behavior move when the model under us
      shifted?": set overlap (Jaccard), what dropped/was added, type changes, and
      confidence drift. You run this before/after a model or prompt change.

Entity matching uses the SAME normalize_name() the loader uses to dedup entities in
production, so "did the run find entity X" means exactly what it means downstream.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

# Reuse production's entity-dedup normalization so eval matching == how the pipeline
# actually collapses mentions into entities. Reimplementing it here would risk the
# eval scoring matches differently than production does — silently wrong.
from pipeline.scrapers.ai_daily.load_entity_batch import normalize_name

# Entities the baseline was confident about. Measured reality (see evals README):
# gpt-4.1-mini extraction has ~40% run-to-run SET churn at temperature 0, and that
# churn is NOT concentrated in low-confidence entities — dropped and retained entities
# have nearly identical mean confidence. So per-episode set identity is too noisy to
# gate on; we report it and lean on stable AGGREGATE signals (yield, type distribution,
# gold precision/recall). core_recall (below) tracks the more-stable high-confidence
# subset as a diagnostic.
CORE_CONFIDENCE = 0.9


def collapse_to_entities(mentions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse one episode's mention rows into per-entity summaries.

    Keyed by normalize_name(canonical_name) — the production dedup key. When an
    entity is mentioned multiple times (possibly with different types), the
    representative entity_type is the type of its highest-confidence mention (ties
    broken by frequency), confidence is the max across mentions, and mention_count is
    how many times it appeared. Mentions with an empty canonical_name are skipped.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in mentions:
        canonical = str(m.get("canonical_name", "")).strip()
        if not canonical:
            continue
        key = normalize_name(canonical)
        if not key:
            continue
        grouped.setdefault(key, []).append(m)

    summaries: dict[str, dict[str, Any]] = {}
    for key, group in grouped.items():
        # Representative type: highest-confidence mention wins; ties -> most frequent.
        type_counts = Counter(str(m.get("entity_type", "other")) for m in group)

        def _sort_key(m: dict[str, Any]) -> tuple[float, int]:
            etype = str(m.get("entity_type", "other"))
            return (_as_float(m.get("confidence"), 0.0), type_counts[etype])

        best = max(group, key=_sort_key)
        summaries[key] = {
            "canonical_name": str(best.get("canonical_name", "")).strip(),
            "entity_type": str(best.get("entity_type", "other")),
            "confidence": max(_as_float(m.get("confidence"), 0.0) for m in group),
            "mention_count": len(group),
        }
    return summaries


def score_precision_recall(
    extracted: dict[str, dict[str, Any]],
    expected: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Score an extraction against HAND-VERIFIED, complete ground truth.

    precision = |found AND expected| / |found|      (how much of the output is right)
    recall    = |found AND expected| / |expected|   (how much of the truth we caught)
    type_accuracy = of the matched entities, the fraction whose entity_type agrees.

    Empty-truth and empty-output edge cases resolve to the mathematically honest
    value (recall is 1.0 when there's nothing to find; precision is 1.0 when nothing
    was emitted) so a clean episode doesn't punish the aggregate.
    """
    found = set(extracted)
    truth = set(expected)
    matched = found & truth
    missing = truth - found       # expected but not found  (recall misses)
    spurious = found - truth      # found but not expected   (precision misses)

    precision = len(matched) / len(found) if found else 1.0
    recall = len(matched) / len(truth) if truth else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    type_matches = sum(
        1 for k in matched if extracted[k]["entity_type"] == expected[k]["entity_type"]
    )
    type_accuracy = type_matches / len(matched) if matched else 1.0
    type_mismatches = [
        {
            "name": expected[k].get("canonical_name") or k,
            "expected_type": expected[k]["entity_type"],
            "got_type": extracted[k]["entity_type"],
        }
        for k in sorted(matched)
        if extracted[k]["entity_type"] != expected[k]["entity_type"]
    ]

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "type_accuracy": round(type_accuracy, 4),
        "n_expected": len(truth),
        "n_found": len(found),
        "n_matched": len(matched),
        "missing": sorted(expected[k].get("canonical_name") or k for k in missing),
        "spurious": sorted(extracted[k].get("canonical_name") or k for k in spurious),
        "type_mismatches": type_mismatches,
    }


def score_regression(
    extracted: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compare an extraction to a captured known-good baseline (drift, not truth).

    jaccard = |both| / |either| — 1.0 means the entity set is unchanged.
    retained/dropped/added describe the set delta from baseline -> extracted.
    type_changes = entities present in both whose entity_type changed.
    mean_abs_conf_delta = average |conf_now - conf_baseline| over retained entities
        (None when nothing is retained) — a soft signal that scoring shifted even when
        the set held.
    """
    now = set(extracted)
    base = set(baseline)
    both = now & base
    either = now | base

    jaccard = len(both) / len(either) if either else 1.0
    dropped = base - now          # were in baseline, gone now (regressions to inspect)
    added = now - base            # new vs baseline (could be good or noise)

    type_changes = [
        {
            "name": baseline[k].get("canonical_name") or k,
            "baseline_type": baseline[k]["entity_type"],
            "now_type": extracted[k]["entity_type"],
        }
        for k in sorted(both)
        if baseline[k]["entity_type"] != extracted[k]["entity_type"]
    ]

    conf_deltas = [
        abs(_as_float(extracted[k].get("confidence"), 0.0) - _as_float(baseline[k].get("confidence"), 0.0))
        for k in both
    ]
    mean_abs_conf_delta = round(sum(conf_deltas) / len(conf_deltas), 4) if conf_deltas else None

    # core_recall: of the entities the baseline was CONFIDENT about (conf >= CORE_CONFIDENCE),
    # how many did this run reproduce? More stable than full Jaccard (the low-confidence
    # tail churns most), so it's the better drift diagnostic — but still per-episode noisy,
    # so it's reported, not hard-gated. None when the baseline had no high-confidence entities.
    core = {k for k in base if _as_float(baseline[k].get("confidence"), 0.0) >= CORE_CONFIDENCE}
    core_recall = round(len(core & now) / len(core), 4) if core else None

    return {
        "jaccard": round(jaccard, 4),
        "core_recall": core_recall,
        "n_core": len(core),
        "n_baseline": len(base),
        "n_now": len(now),
        "n_retained": len(both),
        "n_dropped": len(dropped),
        "n_added": len(added),
        "dropped": sorted(baseline[k].get("canonical_name") or k for k in dropped),
        "added": sorted(extracted[k].get("canonical_name") or k for k in added),
        "n_type_changes": len(type_changes),
        "type_changes": type_changes,
        "mean_abs_conf_delta": mean_abs_conf_delta,
    }


def confidence_report(mentions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Contract check at the OUTPUT: every confidence must be a real number in [0,1].

    This duplicates what the sanitizers promise — on purpose. The way-of-working here
    is "verify the output, never just the code": if a model/prompt change ever emits a
    confidence the sanitizer doesn't catch, this fails loudly instead of trusting that
    the guard held.
    """
    values = [_as_float(m.get("confidence"), None) for m in mentions]
    present = [v for v in values if v is not None]
    out_of_range = [v for v in present if v < 0.0 or v > 1.0]
    n_missing = sum(1 for v in values if v is None)
    return {
        "n": len(values),
        "all_in_range": not out_of_range and n_missing == 0,
        "n_out_of_range": len(out_of_range),
        "n_missing": n_missing,
        "min": round(min(present), 4) if present else None,
        "max": round(max(present), 4) if present else None,
        # Degenerate distributions (everything pinned at one value) are a calibration
        # smell even when all in range — surface it without failing on it.
        "distinct_values": len(set(round(v, 2) for v in present)),
    }


def aggregate(per_episode: list[dict[str, Any]], keys: list[str]) -> dict[str, Optional[float]]:
    """Macro-average the named numeric fields across episodes, ignoring None/missing.

    Macro (mean of per-episode scores) answers "how does it do on a typical episode,"
    which is what you want when episodes vary wildly in entity count.
    """
    out: dict[str, Optional[float]] = {}
    for key in keys:
        vals = [
            ep[key]
            for ep in per_episode
            if isinstance(ep.get(key), (int, float)) and not isinstance(ep.get(key), bool)
        ]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
    return out


def type_counts(entities: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Pooled entity_type histogram for a set of entity summaries."""
    return dict(Counter(str(e.get("entity_type", "other")) for e in entities))


def distribution_shift(now_counts: dict[str, int], baseline_counts: dict[str, int]) -> dict[str, Any]:
    """How far the entity_type MIX moved, as proportions (robust to total-count change).

    The aggregate type distribution is far more stable run-to-run than which specific
    entities appear, so a real shift here (the model stops producing 'model' types, say)
    is a trustworthy signal where per-entity churn is just noise. Returns the largest
    absolute proportion delta across types, plus per-type deltas sorted by magnitude.
    """
    types = set(now_counts) | set(baseline_counts)
    now_total = sum(now_counts.values()) or 1
    base_total = sum(baseline_counts.values()) or 1
    deltas = {
        t: round(now_counts.get(t, 0) / now_total - baseline_counts.get(t, 0) / base_total, 4)
        for t in types
    }
    max_abs = max((abs(d) for d in deltas.values()), default=0.0)
    return {
        "max_abs_delta": round(max_abs, 4),
        "deltas": dict(sorted(deltas.items(), key=lambda kv: -abs(kv[1]))),
    }


def _as_float(value: Any, default: Optional[float]) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

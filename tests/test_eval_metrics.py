"""Unit tests for the extraction eval scorers (evals/extraction/metrics.py).

These are the deterministic gradient the AI-memory primer calls for: pure functions,
no DB / no network / no LLM, so they pin the scoring contract and run in milliseconds.
If these numbers ever move, the eval's verdicts moved — which must be deliberate.
"""

from evals.extraction.metrics import (
    aggregate,
    collapse_to_entities,
    confidence_report,
    distribution_shift,
    score_precision_recall,
    score_regression,
    type_counts,
)


def _ent(entity_type: str, confidence: float, canonical: str = "") -> dict:
    return {"entity_type": entity_type, "confidence": confidence, "canonical_name": canonical}


# --------------------------------------------------------------------------- collapse


def test_collapse_merges_same_name_and_keeps_max_confidence() -> None:
    mentions = [
        {"canonical_name": "ChatGPT", "entity_type": "software_product", "confidence": 0.7},
        {"canonical_name": "chatgpt", "entity_type": "software_product", "confidence": 0.95},
    ]
    ents = collapse_to_entities(mentions)
    assert set(ents) == {"chatgpt"}  # both normalize to the same production key
    assert ents["chatgpt"]["mention_count"] == 2
    assert ents["chatgpt"]["confidence"] == 0.95


def test_collapse_skips_empty_canonical_name() -> None:
    mentions = [
        {"canonical_name": "", "entity_type": "other", "confidence": 0.9},
        {"canonical_name": "   ", "entity_type": "other", "confidence": 0.9},
        {"canonical_name": "Claude", "entity_type": "model", "confidence": 0.9},
    ]
    ents = collapse_to_entities(mentions)
    assert set(ents) == {"claude"}


def test_collapse_representative_type_is_highest_confidence_mention() -> None:
    # Same entity tagged two ways; the higher-confidence mention's type wins.
    mentions = [
        {"canonical_name": "Gemini", "entity_type": "organization", "confidence": 0.6},
        {"canonical_name": "Gemini", "entity_type": "model", "confidence": 0.92},
    ]
    ents = collapse_to_entities(mentions)
    assert ents["gemini"]["entity_type"] == "model"


# ----------------------------------------------------------------- precision / recall


def test_precision_recall_perfect_match() -> None:
    extracted = {"a": _ent("model", 0.9, "A"), "b": _ent("software_product", 0.9, "B")}
    expected = {"a": _ent("model", 1.0, "A"), "b": _ent("software_product", 1.0, "B")}
    s = score_precision_recall(extracted, expected)
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["f1"] == 1.0
    assert s["type_accuracy"] == 1.0
    assert s["missing"] == [] and s["spurious"] == []


def test_precision_recall_partial() -> None:
    extracted = {"a": _ent("model", 0.9, "A"), "b": _ent("model", 0.9, "B"), "c": _ent("model", 0.9, "C")}
    expected = {"a": _ent("model", 1.0, "A"), "b": _ent("model", 1.0, "B"), "d": _ent("model", 1.0, "D")}
    s = score_precision_recall(extracted, expected)
    assert s["precision"] == round(2 / 3, 4)
    assert s["recall"] == round(2 / 3, 4)
    assert s["missing"] == ["D"]
    assert s["spurious"] == ["C"]


def test_precision_recall_type_mismatch_counts() -> None:
    extracted = {"a": _ent("organization", 0.9, "A")}
    expected = {"a": _ent("model", 1.0, "A")}
    s = score_precision_recall(extracted, expected)
    assert s["recall"] == 1.0 and s["precision"] == 1.0  # found the entity...
    assert s["type_accuracy"] == 0.0                      # ...but mistyped it
    assert s["type_mismatches"] == [{"name": "A", "expected_type": "model", "got_type": "organization"}]


def test_precision_recall_empty_output_flags_via_f1() -> None:
    # Emitting nothing is vacuously precise but catches nothing; F1 must read 0.
    s = score_precision_recall({}, {"a": _ent("model", 1.0, "A")})
    assert s["recall"] == 0.0
    assert s["f1"] == 0.0
    assert s["missing"] == ["A"]


def test_precision_recall_empty_truth_is_clean() -> None:
    # A genuinely empty episode shouldn't punish the aggregate.
    s = score_precision_recall({}, {})
    assert s["precision"] == 1.0 and s["recall"] == 1.0


# ------------------------------------------------------------------------- regression


def test_regression_identical_sets() -> None:
    a = {"x": _ent("model", 0.9, "X"), "y": _ent("paper", 0.8, "Y")}
    s = score_regression(dict(a), dict(a))
    assert s["jaccard"] == 1.0
    assert s["n_dropped"] == 0 and s["n_added"] == 0
    assert s["n_type_changes"] == 0
    assert s["mean_abs_conf_delta"] == 0.0


def test_regression_drop_and_add() -> None:
    baseline = {"x": _ent("model", 0.9, "X"), "y": _ent("paper", 0.9, "Y")}
    extracted = {"x": _ent("model", 0.9, "X"), "z": _ent("benchmark", 0.9, "Z")}
    s = score_regression(extracted, baseline)
    assert s["jaccard"] == round(1 / 3, 4)  # {x} shared / {x,y,z} union
    assert s["dropped"] == ["Y"]
    assert s["added"] == ["Z"]


def test_regression_type_change_and_conf_drift() -> None:
    baseline = {"x": _ent("organization", 0.6, "X")}
    extracted = {"x": _ent("model", 0.9, "X")}
    s = score_regression(extracted, baseline)
    assert s["n_type_changes"] == 1
    assert s["type_changes"][0]["baseline_type"] == "organization"
    assert s["type_changes"][0]["now_type"] == "model"
    assert s["mean_abs_conf_delta"] == 0.3


def test_regression_empty_both_is_vacuously_stable() -> None:
    s = score_regression({}, {})
    assert s["jaccard"] == 1.0
    assert s["mean_abs_conf_delta"] is None


def test_regression_core_recall_tracks_high_confidence_baseline() -> None:
    # Baseline has two high-confidence entities (x, y) and one low (z). The run keeps x,
    # drops y, keeps z. core_recall = 1 of 2 high-conf reproduced = 0.5.
    baseline = {
        "x": _ent("model", 0.95, "X"),
        "y": _ent("model", 0.92, "Y"),
        "z": _ent("other", 0.7, "Z"),
    }
    extracted = {"x": _ent("model", 0.9, "X"), "z": _ent("other", 0.7, "Z")}
    s = score_regression(extracted, baseline)
    assert s["n_core"] == 2
    assert s["core_recall"] == 0.5


def test_regression_core_recall_none_without_high_conf() -> None:
    baseline = {"z": _ent("other", 0.7, "Z")}
    s = score_regression({"z": _ent("other", 0.7, "Z")}, baseline)
    assert s["core_recall"] is None


# ----------------------------------------------------------------- type distribution


def test_type_counts_histogram() -> None:
    ents = [_ent("model", 0.9), _ent("model", 0.9), _ent("software_product", 0.9)]
    assert type_counts(ents) == {"model": 2, "software_product": 1}


def test_distribution_shift_identical_is_zero() -> None:
    counts = {"model": 5, "software_product": 5}
    s = distribution_shift(counts, counts)
    assert s["max_abs_delta"] == 0.0


def test_distribution_shift_detects_mix_change() -> None:
    # Baseline 50/50; now 100% model -> 'model' proportion moves +0.5.
    s = distribution_shift({"model": 10}, {"model": 5, "software_product": 5})
    assert s["max_abs_delta"] == 0.5
    assert s["deltas"]["model"] == 0.5


# ------------------------------------------------------------------------ confidence


def test_confidence_all_in_range() -> None:
    r = confidence_report([{"confidence": 0.9}, {"confidence": 0.7}, {"confidence": 0.95}])
    assert r["all_in_range"] is True
    assert r["min"] == 0.7 and r["max"] == 0.95
    assert r["distinct_values"] == 3


def test_confidence_out_of_range_fails() -> None:
    r = confidence_report([{"confidence": 0.9}, {"confidence": 1.5}])
    assert r["all_in_range"] is False
    assert r["n_out_of_range"] == 1


def test_confidence_missing_value_does_not_break_the_episode_contract() -> None:
    """A NULL confidence is the sanitizer being honest (it stopped fabricating 0.5 on
    2026-09-03), so it does not fail the per-episode contract. Whether a RUN has too
    many of them is judged at the rollup as a ratio — see the check_floors tests."""
    r = confidence_report([{"confidence": 0.9}, {"sentiment_label": "positive"}])
    assert r["all_in_range"] is True
    assert r["n_missing"] == 1
    assert r["n_out_of_range"] == 0
    # The denominator the rollup needs to turn this count into a ratio.
    assert r["n"] == 2


def test_confidence_out_of_range_and_missing_are_tracked_independently() -> None:
    """The one that gates is out-of-range; a missing value alongside it must not
    inflate the breach count, and must not be what fails the run."""
    r = confidence_report([{"confidence": 0.9}, {}, {"confidence": 1.5}])
    assert r["all_in_range"] is False  # the 1.5, not the missing one
    assert r["n_out_of_range"] == 1
    assert r["n_missing"] == 1


def test_confidence_degenerate_distribution_is_visible() -> None:
    r = confidence_report([{"confidence": 0.9}, {"confidence": 0.9}, {"confidence": 0.9}])
    assert r["all_in_range"] is True
    assert r["distinct_values"] == 1  # calibration smell, surfaced not failed


# ------------------------------------------------------------------------- aggregate


def test_aggregate_macro_average_ignores_none() -> None:
    per_episode = [{"recall": 1.0}, {"recall": 0.5}, {"recall": None}, {}]
    out = aggregate(per_episode, ["recall"])
    assert out["recall"] == 0.75  # mean of 1.0 and 0.5


def test_aggregate_ignores_bools() -> None:
    per_episode = [{"flag": True}, {"flag": 0.4}]
    out = aggregate(per_episode, ["flag"])
    assert out["flag"] == 0.4  # True is not counted as a number


# ------------------------------------------------------- the CI gate (check_floors)
# check_floors is what reddens the weekly eval workflow. It had no test until the
# confidence contract was loosened on 2026-09-03 (missing is now reported, not gated),
# and "never loosen a check without a test" is the rule that loosening had to satisfy.
# Imported inside each test so this module keeps its no-DB/no-network import surface.


def _conf(n: int = 100, n_missing: int = 0, n_out_of_range: int = 0) -> dict:
    """A rolled-up confidence section, with the ratio derived the way build_report
    derives it — so a test can never state a ratio the real rollup wouldn't."""
    return {
        "all_in_range": not n_out_of_range,
        "n": n,
        "n_out_of_range": n_out_of_range,
        "n_missing": n_missing,
        "missing_ratio": round(n_missing / n, 4) if n else None,
    }


def _floor_report(**overrides) -> dict:
    """The smallest report check_floors will read: no failed episodes, a clean
    contract, and no baseline/gold section (both optional and separately gated)."""
    report = {
        "n_failed": 0,
        "confidence": _conf(),
        "baseline": None,
        "gold": None,
    }
    report.update(overrides)
    return report


def test_check_floors_passes_when_a_few_confidences_are_missing() -> None:
    """The PR's whole argument: a handful of honest NULLs is the sanitizer doing the
    right thing and must never fail the weekly eval."""
    from evals.extraction.run_eval import check_floors

    assert check_floors(_floor_report(confidence=_conf(n=30, n_missing=1))) == []


def test_check_floors_breaches_when_the_model_stops_emitting_confidence() -> None:
    """The other side of that argument, and the reason the ratio ceiling exists: with
    no ceiling this run passes green and pages nobody, while every mention it produced
    lands in needs_review."""
    from evals.extraction.run_eval import check_floors, FLOORS

    breaches = check_floors(_floor_report(confidence=_conf(n=30, n_missing=30)))

    assert len(breaches) == 1
    assert "30/30" in breaches[0] and "100%" in breaches[0]
    assert FLOORS["confidence_missing_ratio_max"] == 0.25


def test_check_floors_breaches_on_a_partial_swing_to_missing() -> None:
    """8 of 30 is 27% — over the ceiling. Not every mention has to go missing before
    this is a regression worth a human's attention."""
    from evals.extraction.run_eval import check_floors

    breaches = check_floors(_floor_report(confidence=_conf(n=30, n_missing=8)))

    assert len(breaches) == 1
    assert "8/30" in breaches[0]


def test_check_floors_ratio_gate_reads_the_denominator_not_the_count() -> None:
    """12 missing is a regression out of 12 and a Tuesday out of 5,000. The count alone
    could not tell those apart, which is why the rollup carries `n`."""
    from evals.extraction.run_eval import check_floors

    assert check_floors(_floor_report(confidence=_conf(n=5000, n_missing=12))) == []
    assert len(check_floors(_floor_report(confidence=_conf(n=12, n_missing=12)))) == 1


def test_check_floors_survives_an_empty_run() -> None:
    """No scored mentions at all -> missing_ratio is None, not a ZeroDivisionError and
    not a breach. n_failed is what catches a run that produced nothing."""
    from evals.extraction.run_eval import check_floors

    assert check_floors(_floor_report(confidence=_conf(n=0))) == []


def test_check_floors_still_breaches_on_out_of_range_confidence() -> None:
    from evals.extraction.run_eval import check_floors

    breaches = check_floors(
        _floor_report(confidence=_conf(n=100, n_missing=12, n_out_of_range=3))
    )
    assert len(breaches) == 1
    # 12/100 is under the ceiling, so the only breach is the out-of-range one — and its
    # count must not be inflated by the missing values.
    assert "3 confidence value(s) outside" in breaches[0]
    assert "missing" not in breaches[0]


def test_out_of_range_and_a_missing_swing_are_reported_as_two_breaches() -> None:
    """Independent gates, independently named — one message per real problem."""
    from evals.extraction.run_eval import check_floors

    breaches = check_floors(
        _floor_report(confidence=_conf(n=40, n_missing=40, n_out_of_range=2))
    )
    assert len(breaches) == 2
    assert any("outside [0,1]" in b for b in breaches)
    assert any("40/40" in b for b in breaches)


def test_the_real_rollup_produces_what_the_gate_reads(monkeypatch) -> None:
    """The tests above hand-build the confidence section, so this one closes the loop:
    real per-episode confidence_report -> real build_report -> real check_floors, with
    a model that emitted no confidence at all. If the rollup ever stops carrying `n` or
    `missing_ratio`, the hand-built fixture above would keep passing and only this
    fails."""
    from argparse import Namespace

    from evals.extraction.run_eval import build_report, check_floors

    mentions = [{"canonical_name": f"Tool {i}"} for i in range(30)]  # no confidence key
    episodes = [
        {"episode_id": 1, "confidence": confidence_report(mentions[:15])},
        {"episode_id": 2, "confidence": confidence_report(mentions[15:])},
    ]

    report = build_report(
        Namespace(model="gpt-4.1-mini"), episodes, {"model": "gpt-4.1-mini"}, {}
    )

    assert report["confidence"]["n"] == 30
    assert report["confidence"]["n_missing"] == 30
    assert report["confidence"]["missing_ratio"] == 1.0
    breaches = check_floors(report)
    assert any("30/30" in b for b in breaches)

"""The intake eval's graders and runner, with a fake judge — the arithmetic is the contract."""

from __future__ import annotations

from evals.intake import metrics as M
from evals.intake import run_eval as E
from evals.intake.build_fixture import build
from pipeline.scrapers.intake import judge as J


def _rows():
    return [
        {"label": "save", "verdict": "save", "judge_verdict": "save", "checker_verdict": "save", "source": "a", "confidence": 0.9},
        {"label": "save", "verdict": "skip", "judge_verdict": "skip", "checker_verdict": "skip", "source": "a", "confidence": 0.8, "title": "missed", "reason": "r"},
        {"label": "skip", "verdict": "save", "judge_verdict": "save", "checker_verdict": "skip", "source": "b", "confidence": 0.6, "title": "extra", "reason": "r", "disputed": True},
        {"label": "skip", "verdict": "skip", "judge_verdict": "skip", "checker_verdict": "skip", "source": "b", "confidence": 0.95},
    ]


def test_confusion_recall_precision_and_undefined() -> None:
    c = M.confusion(_rows())
    assert c == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert M.recall_precision(c) == {"recall_save": 0.5, "precision_save": 0.5, "n": 4}
    assert M.recall_precision({"tp": 0, "fp": 0, "fn": 0, "tn": 3})["recall_save"] is None


def test_agreement_by_source_and_mismatch_order() -> None:
    a = M.agreement(_rows())
    assert a == {"judged_twice": 4, "agreement_rate": 0.75, "disputed": 1, "disputed_correct": 0}
    assert M.by_source(_rows())["a"]["recall_save"] == 0.5
    mm = M.mismatches(_rows())
    assert [m["title"] for m in mm] == ["missed", "extra"]  # false negative first


def test_floors_report_breaches_and_undefined() -> None:
    assert M.check_floors({"recall_save": 0.95, "precision_save": 0.6}, E.FLOORS) == ["precision_save 0.600 < floor 0.70"]
    assert M.check_floors({"recall_save": None, "precision_save": 0.9}, E.FLOORS) == ["recall_save undefined (no rows to score)"]
    assert M.check_floors({"recall_save": 0.9, "precision_save": 0.7}, E.FLOORS) == []


def test_run_with_a_fake_judge_scores_and_flags_drift(tmp_path) -> None:
    cands = [
        {"id": "a1", "url": "u1", "source": "openai-rss", "title": "Usage report", "label": "save", "text_sha256": E.sha("t1")},
        {"id": "b2", "url": "u2", "source": "openai-rss", "title": "Customer story", "label": "skip", "text_sha256": "stale"},
        {"id": "c3", "url": "u3", "source": "queue", "title": "Unreachable", "label": "save", "text_sha256": "x"},
    ]

    def decide(**kw) -> J.Decision:
        v = "save" if "report" in kw["title"].lower() else "skip"
        j = J.Verdict(v, 0.9, "because", "flash")
        return J.decide(j, J.Verdict(v, 0.8, "agree", "luna"), "v1")

    def text_for(c):
        if c["id"] == "c3":
            raise RuntimeError("404")
        return "t1", c["id"] == "b2"

    rep = E.run(cands, decide, text_for, workers=2)
    assert rep["n_scored"] == 2 and rep["n_failed"] == 1 and rep["drifted"] == ["b2"]
    assert rep["recall_save"] == 1.0 and rep["precision_save"] == 1.0
    assert rep["breaches"] == ["1 candidate(s) could not be judged"]
    assert rep["agreement"]["agreement_rate"] == 1.0 and rep["prompt_version"] == "v1"


def test_cached_text_scrapes_once_and_detects_drift(tmp_path) -> None:
    calls = []
    cand = {"id": "z9", "url": "https://x/z", "text_sha256": E.sha("hello")}
    text, drift = E.cached_text(cand, lambda u: calls.append(u) or "hello", cache=tmp_path)
    assert (text, drift, calls) == ("hello", False, ["https://x/z"])
    text2, drift2 = E.cached_text({**cand, "text_sha256": "other"}, lambda u: calls.append(u), cache=tmp_path)
    assert text2 == "hello" and drift2 and calls == ["https://x/z"]  # cache hit, no second scrape


def test_build_fixture_keeps_only_labeled_rows() -> None:
    pool = [{"id": "a", "url": "u", "source": "s", "title": "T", "date": "2026-09-01", "words": 900, "links_out": 3},
            {"id": "b", "url": "u2", "source": "s", "title": "U"}]
    fx = build(pool, {"a": "text a", "b": "text b"}, {"a": {"label": "save", "note": "yes"}, "b": {"label": "maybe"}}, "kevin")
    assert fx["_meta"]["n"] == 1 and fx["_meta"]["n_save"] == 1 and fx["_meta"]["labeled_by"] == "kevin"
    row = fx["candidates"][0]
    assert row["id"] == "a" and row["label"] == "save" and row["text_sha256"] == E.sha("text a") and row["note"] == "yes"

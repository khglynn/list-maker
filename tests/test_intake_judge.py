"""The judge's pure parts: pre-checks, verdict parsing, the decide rule, the prompt."""

from __future__ import annotations

import pytest

from pipeline.scrapers.intake import judge as J


def test_precheck_orders_duplicate_pdf_dead_thin() -> None:
    assert J.precheck("https://x/a", already_ingested=True, words=5000, scrape_error=None).skip_reason == "duplicate"
    held = J.precheck("https://x/report.pdf?dl=1", already_ingested=False, words=None, scrape_error=None)
    assert held.skip_reason == "pdf" and held.status == "held"
    assert J.precheck("https://x/a", already_ingested=False, words=None, scrape_error="404").skip_reason == "dead"
    assert J.precheck("https://x/a", already_ingested=False, words=22, scrape_error=None).skip_reason == "thin"
    ok = J.precheck("https://x/a", already_ingested=False, words=900, scrape_error=None)
    assert ok.skip_reason is None and ok.status == "judged"


def test_parse_verdict_tolerates_fences_and_clamps_confidence() -> None:
    v = J.parse_verdict('```json\n{"verdict": "Save", "confidence": 1.4, "reason": "usage numbers"}\n```', "m")
    assert (v.verdict, v.confidence, v.reason, v.model) == ("save", 1.0, "usage numbers", "m")
    with pytest.raises(ValueError):
        J.parse_verdict('{"verdict": "maybe", "confidence": 0.5}', "m")
    with pytest.raises(ValueError):
        J.parse_verdict("I think you should save it.", "m")


def test_decide_agrees_averages_and_disagreement_saves_disputed() -> None:
    a = J.Verdict("skip", 0.9, "customer story", "flash")
    b = J.Verdict("skip", 0.7, "no data", "luna")
    d = J.decide(a, b, "v1")
    assert (d.verdict, d.confidence, d.disputed, d.reason) == ("skip", 0.8, False, "customer story")
    c = J.Verdict("save", 0.6, "first-party usage figures", "luna")
    d2 = J.decide(a, c, "v1")
    assert d2.verdict == "save" and d2.disputed and d2.reason == "first-party usage figures" and d2.confidence == 0.6
    solo = J.decide(a, None, "v1")
    assert solo.verdict == "skip" and not solo.disputed and solo.confidence == 0.9


def test_build_messages_truncates_and_names_the_fields() -> None:
    msgs = J.build_messages("RUBRIC", title="T", source="openai-rss", published_on="2026-09-01", category=["Research"],
                            words=4000, links_out=12, found_via="feed", text="x" * (J.MAX_TEXT_CHARS + 10))
    assert msgs[0] == {"role": "system", "content": "RUBRIC"}
    user = msgs[1]["content"]
    assert "TITLE: T" in user and "CATEGORY: Research" in user and "TEXT (first part):" in user
    assert user.count("x") == J.MAX_TEXT_CHARS and '"verdict": "save" | "skip"' in user


def test_load_rubric_versions_by_content(tmp_path) -> None:
    p = tmp_path / "r.md"
    p.write_text("rules v1")
    text, v1 = J.load_rubric(p)
    p.write_text("rules v2")
    _, v2 = J.load_rubric(p)
    assert text == "rules v1" and v1 != v2 and len(v1) == 12


def test_judge_once_falls_through_to_the_next_model(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResp:
        def __init__(self, model):
            self.model = model
        def raise_for_status(self):
            if self.model == "bad/model":
                raise J.httpx.HTTPError("503")
        def json(self):
            return {"choices": [{"message": {"content": '{"verdict":"save","confidence":0.8,"reason":"ok"}'}}]}

    class FakeClient:
        def post(self, url, headers, json):
            calls.append(json["model"])
            return FakeResp(json["model"])

    v = J.judge_once([{"role": "user", "content": "x"}], ("bad/model", "good/model"), "k", client=FakeClient())
    assert v.model == "good/model" and calls == ["bad/model", "good/model"]

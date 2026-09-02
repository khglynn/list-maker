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


def test_precheck_structural_skips_from_the_rubric_panel() -> None:
    from datetime import date
    base = dict(already_ingested=False, words=900, scrape_error=None, today=date(2026, 9, 2))
    assert J.precheck("https://x/a", category=["OpenAI Academy"], **base).skip_reason == "academy"
    assert J.precheck("https://x/a", title="Tino Cuéllar to join Anthropic as Chief Global Affairs Officer", **base).skip_reason == "people-news"
    assert J.precheck("https://x/a", title="Introducing Claude Opus 5", **base).skip_reason is None
    assert J.precheck("https://x/a", published_on=date(2025, 6, 1), source="openai-rss", **base).skip_reason == "stale"
    # a show just cited it: history is judged, not dropped
    assert J.precheck("https://x/a", published_on=date(2024, 10, 1), source="podcast-cited", **base).skip_reason is None
    assert J.precheck("https://x/a", published_on=date(2026, 8, 1), source="openai-rss", **base).skip_reason is None


def test_parse_verdict_tolerates_fences_and_clamps_confidence() -> None:
    v = J.parse_verdict('```json\n{"verdict": "Save", "confidence": 1.4, "reason": "usage numbers", "rule": "S1", "job": "Deck"}\n```', "m")
    assert (v.verdict, v.confidence, v.reason, v.model, v.rule, v.job) == ("save", 1.0, "usage numbers", "m", "S1", "deck")
    assert J.parse_verdict('{"verdict":"skip","confidence":0.9,"reason":"k1","job":null}', "m").job is None
    with pytest.raises(ValueError):
        J.parse_verdict('{"verdict": "maybe", "confidence": 0.5}', "m")
    with pytest.raises(ValueError):
        J.parse_verdict("I think you should save it.", "m")


def test_decide_agrees_averages_and_disagreement_saves_disputed() -> None:
    a = J.Verdict("skip", 0.9, "customer story", "flash")
    b = J.Verdict("skip", 0.7, "no data", "luna")
    d = J.decide(a, b, "v1")
    assert (d.verdict, d.confidence, d.disputed, d.reason) == ("skip", 0.8, False, "customer story")
    assert d.rule is None and d.job is None
    c = J.Verdict("save", 0.6, "first-party usage figures", "luna", rule="S1", job="deck")
    d2 = J.decide(a, c, "v1")
    assert d2.verdict == "save" and d2.disputed and d2.reason == "first-party usage figures" and d2.confidence == 0.6
    assert (d2.rule, d2.job) == ("S1", "deck")  # the save side's provenance rides with the disputed save
    solo = J.decide(a, None, "v1")
    assert solo.verdict == "skip" and not solo.disputed and solo.confidence == 0.9


def test_build_messages_truncates_and_names_the_fields() -> None:
    msgs = J.build_messages("RUBRIC", title="T", source="openai-rss", published_on="2026-09-01", category=["Research"],
                            words=4000, links_out=12, found_via="feed", text="x" * (J.MAX_TEXT_CHARS + 10))
    assert msgs[0] == {"role": "system", "content": "RUBRIC"}
    user = msgs[1]["content"]
    assert "TITLE: T" in user and "CATEGORY: Research" in user and "TEXT (first part):" in user
    assert "FLAGS: HAS_PERCENT: no HAS_SAMPLE: no" in user and "FOUND_VIA: feed" in user
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


def test_flags_are_whole_document_facts() -> None:
    from pipeline.scrapers.intake.flags import compute_flags
    text = ("Getting started with ChatGPT. Contact sales today.\n\n" + "filler " * 3000 +
            "In a randomized experiment with more than 1,000 students, 43% improved. "
            "Pricing is $0.20 per million input tokens.\n| a | b |\n|---|---|\n| 1 | 2 |\n"
            "A retailer in the apparel industry deployed it.")
    f = compute_flags(text, title="How a footwear retailer scaled support")
    assert f == {"HAS_PERCENT": True, "HAS_SAMPLE": True, "HAS_PRICE": True, "HAS_TABLE": True,
                 "CUSTOMER_STORY": True, "PEER_INDUSTRY": True}
    assert compute_flags("Introducing our new office in Brazil.") == {k: False for k in f}
    # PEER_INDUSTRY reads the title + lede only — a passing "retail" deep in the body is not a peer
    assert compute_flags("filler " * 500 + "the retail sector", title="How Acme Bank ships faster")["PEER_INDUSTRY"] is False

"""Link resolution scored against a frozen probe of 14 real podcast-cited reports
(Firecrawl search results captured 2026-09-02). The fixture is the ground truth for
what "primary source first, generic names never guessed" means in practice."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.scrapers.intake import links as L

PROBE = json.loads((Path(__file__).parent / "fixtures" / "intake" / "link-probe-2026-09-02.json").read_text())


def _probe(name: str) -> dict:
    for p in PROBE:
        if p["mention"]["canonical_name"] == name:
            m = {**p["mention"], "mention_id": 1, "episode_id": 9, "context_snippet": p["mention"]["ctx"]}
            return {"mention": m, "results": p["results"]}
    raise KeyError(name)


def test_primary_sources_win() -> None:
    expect = {
        "RAMP AI Index": "https://ramp.com/data/ai-index",
        "Enterprise Signals, What Frontier Firms Are Doing Differently": "https://openai.com/signals/enterprise-data/",
        "The Tragedy of the Cognitive Commons": "https://arxiv.org/abs/2607.29380",
        "Machines of Loving Grace": "https://darioamodei.com/essay/machines-of-loving-grace",
        "The Adolescence of Technology": "https://darioamodei.com/essay/the-adolescence-of-technology",
    }
    for name, url in expect.items():
        p = _probe(name)
        r = L.resolve_one(p["mention"], p["results"], "q")
        assert r.url == url, (name, r.candidates[:2])
        assert r.candidates[0]["url"] == url and r.confidence >= L.AUTO_RESOLVE_SCORE


def test_generic_names_are_never_guessed() -> None:
    for name in ("BCG paper", "Meter investigation"):
        p = _probe(name)
        r = L.resolve_one(p["mention"], p["results"], "q")
        assert r.url is None, (name, r.candidates[0])
        assert r.candidates  # but the hits are kept for a human or a later pass


def test_secondary_coverage_does_not_resolve_when_no_primary_exists() -> None:
    p = _probe("White House AI safety testing framework")
    r = L.resolve_one(p["mention"], p["results"], "q")
    assert r.url is None and all(c["score"] < L.AUTO_RESOLVE_SCORE for c in r.candidates)


def test_query_adds_the_org_only_for_generic_names() -> None:
    assert L.build_query(_probe("RAMP AI Index")["mention"]) == '"RAMP AI Index"'
    kp = L.build_query(_probe("KPMG adaptability report")["mention"])
    assert kp == '"KPMG adaptability report"'  # two content tokens: stands alone
    meter = L.build_query(_probe("Meter investigation")["mention"])
    assert meter.startswith('"Meter investigation" ') and "openai" in meter


def test_social_penalty_is_lifted_for_social_posts() -> None:
    m = {"mention_id": 1, "mention_type": "social_post", "canonical_name": "Andrew Ng AI Engineering Skills Map", "context_snippet": ""}
    s_social, _ = L.score(m, "https://x.com/AndrewYNg/article/1", "AI Engineering Skills Map")
    m2 = {**m, "mention_type": "blog_post"}
    s_blog, _ = L.score(m2, "https://x.com/AndrewYNg/article/1", "AI Engineering Skills Map")
    assert s_social > s_blog


def test_resolve_mentions_survives_a_failed_search_and_builds_candidates() -> None:
    p = _probe("RAMP AI Index")
    calls = []

    def search(q, n):
        calls.append(q)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return p["results"]

    m_bad = {**p["mention"], "mention_id": 7, "canonical_name": "Ghost Report XYZ"}
    res = L.resolve_mentions([m_bad, p["mention"]], search)
    assert res[0].url is None and "search failed" in res[0].query
    assert res[1].url == "https://ramp.com/data/ai-index"
    cand = L.as_candidate(p["mention"], res[1])
    assert cand.source == "podcast-cited" and cand.url == res[1].url and cand.category == ["report"]
    assert cand.discovered_via["mention_id"] == 1 and cand.discovered_via["link_confidence"] == res[1].confidence

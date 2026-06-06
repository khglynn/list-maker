"""Extraction-time data contract (Workstream A8).

sanitize_mention / sanitize_fact / parse_json_object are pure (no OpenAI call),
so we can pin the contract that protects the DB before any rows are written:
confidence stays in [0,1], core fields are required, unknown entity_types fall
back to 'other' + needs_review, and low-confidence mentions are flagged.
"""

import pytest

from pipeline.scrapers.ai_daily.extract_entities import (
    parse_json_object,
    sanitize_fact,
    sanitize_mention,
)


def _mention(**overrides):
    base = {
        "mention_text": "ChatGPT",
        "canonical_name": "ChatGPT",
        "context_snippet": "They talked about ChatGPT at length.",
        "entity_type": "software_product",
        "sentiment_label": "positive",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def test_parse_json_object_plain_and_fenced() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 2}\n```') == {"a": 2}


def test_parse_json_object_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        parse_json_object("not json at all")
    with pytest.raises(ValueError):
        parse_json_object("[1, 2, 3]")  # a list, not an object


def test_sanitize_fact_clamps_confidence_and_requires_key() -> None:
    assert sanitize_fact({"fact_key": "modality", "confidence": 1.5})["confidence"] == 1.0
    assert sanitize_fact({"fact_key": "x", "confidence": "bad"})["confidence"] == 0.5
    assert sanitize_fact({"fact_value": "no key"}) is None
    assert sanitize_fact("not a dict") is None


def test_sanitize_mention_clamps_confidence_to_unit_interval() -> None:
    assert sanitize_mention(_mention(confidence=1.5), 1, 0.4)["confidence"] == 1.0
    assert sanitize_mention(_mention(confidence=-0.2), 1, 0.4)["confidence"] == 0.0
    assert sanitize_mention(_mention(confidence="nope"), 1, 0.4)["confidence"] == 0.5


def test_sanitize_mention_flags_low_confidence_for_review() -> None:
    out = sanitize_mention(_mention(confidence=0.1), 1, 0.4)
    assert out["needs_review"] is True
    assert out["review_reason"] == "low_confidence"


def test_sanitize_mention_unknown_type_becomes_other_and_needs_review() -> None:
    out = sanitize_mention(_mention(entity_type="dragon"), 1, 0.4)
    assert out["entity_type"] == "other"
    assert out["needs_review"] is True
    assert out["review_reason"] == "model_proposed_unknown_type"

    # The unknown-type reason is NOT overwritten by the later low-confidence check.
    combined = sanitize_mention(_mention(entity_type="dragon", confidence=0.05), 1, 0.4)
    assert combined["review_reason"] == "model_proposed_unknown_type"


def test_sanitize_mention_requires_core_fields() -> None:
    assert sanitize_mention(_mention(canonical_name=""), 1, 0.4) is None
    assert sanitize_mention(_mention(mention_text=""), 1, 0.4) is None
    assert sanitize_mention(_mention(context_snippet=""), 1, 0.4) is None
    assert sanitize_mention("not a dict", 1, 0.4) is None


def test_sanitize_mention_confidence_always_in_unit_interval() -> None:
    for value in [-5, 0, 0.5, 1, 99, "x", None]:
        out = sanitize_mention(_mention(confidence=value), 1, 0.0)
        assert out is not None
        assert 0.0 <= out["confidence"] <= 1.0

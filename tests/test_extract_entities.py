"""Extraction-time data contract (Workstream A8).

sanitize_mention / sanitize_fact / parse_json_object are pure (no OpenAI call),
so we can pin the contract that protects the DB before any rows are written:
confidence stays in [0,1], core fields are required, unknown entity_types fall
back to 'other' + needs_review, and low-confidence mentions are flagged.
"""

import pytest

from pipeline.scrapers.ai_daily.extract_entities import (
    LOCKED_TYPES,
    MEDIA_TYPES,
    get_profile,
    parse_json_object,
    postprocess_mention_types,
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


# --- media extraction profile (Workstream D: PCHH + Culture Gabfest) ---


def test_get_profile_selects_tech_vs_media() -> None:
    tech = get_profile("entity_extraction")
    assert tech.name == "tech"
    assert "software_product" in tech.types and "movie" not in tech.types
    assert tech.apply_tech_heuristics is True

    media = get_profile("media_extraction")
    assert media.name == "media"
    assert "movie" in media.types and "book" in media.types
    assert "software_product" not in media.types
    assert media.apply_tech_heuristics is False
    assert "What's Making Me Happy" in media.system_prompt  # segment-aware media prompt


def test_get_profile_defaults_to_tech() -> None:
    assert get_profile(None).name == "tech"
    assert get_profile("song_extraction").name == "tech"  # non-media slug → tech default


def test_sanitize_mention_media_type_survives_under_media_profile() -> None:
    m = sanitize_mention(
        _mention(entity_type="movie", canonical_name="Dune: Part Two", mention_text="Dune"),
        episode_id=1,
        confidence_review_threshold=0.5,
        valid_types=MEDIA_TYPES,
    )
    assert m is not None and m["entity_type"] == "movie"  # not forced to "other"


def test_sanitize_mention_cross_profile_type_falls_back() -> None:
    # A tech type isn't valid under the media taxonomy → other + needs_review.
    m = sanitize_mention(
        _mention(entity_type="software_product"),
        episode_id=1,
        confidence_review_threshold=0.5,
        valid_types=MEDIA_TYPES,
    )
    assert m["entity_type"] == "other" and m["needs_review"] is True


def test_sanitize_mention_default_valid_types_is_tech() -> None:
    # Back-compat: the default taxonomy stays tech, so a media type falls back.
    m = sanitize_mention(
        _mention(entity_type="movie"), episode_id=1, confidence_review_threshold=0.5
    )
    assert m["entity_type"] == "other"


def test_postprocess_skips_tech_heuristics_for_media() -> None:
    base = {
        "entity_type": "other",
        "mention_text": "X",
        "canonical_name": "X",
        "context_snippet": "they discussed a big survey of fans",
        "needs_review": False,
        "review_reason": None,
    }
    # Tech profile: "other" + "survey" context → retyped to survey by the heuristic.
    tech = postprocess_mention_types(dict(base), valid_types=LOCKED_TYPES, apply_tech_heuristics=True)
    assert tech["entity_type"] == "survey"
    # Media profile: heuristics skipped → stays "other".
    media = postprocess_mention_types(dict(base), valid_types=MEDIA_TYPES, apply_tech_heuristics=False)
    assert media["entity_type"] == "other"


def test_postprocess_media_type_survives() -> None:
    m = postprocess_mention_types(
        {
            "entity_type": "book",
            "mention_text": "X",
            "canonical_name": "X",
            "context_snippet": "a wonderful novel",
            "needs_review": False,
            "review_reason": None,
        },
        valid_types=MEDIA_TYPES,
        apply_tech_heuristics=False,
    )
    assert m["entity_type"] == "book"  # survived the media post-process


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

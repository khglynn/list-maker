"""Extraction-time data contract (Workstream A8).

sanitize_mention / sanitize_fact / parse_json_object are pure (no OpenAI call),
so we can pin the contract that protects the DB before any rows are written:
confidence stays in [0,1], core fields are required, unknown entity_types fall
back to 'other' + needs_review, and low-confidence mentions are flagged.
"""

from pathlib import Path

import pytest

from pipeline.scrapers.ai_daily.extract_entities import (
    EPISODE_SUMMARY_FIELDS,
    FILTER_STAT_KEYS,
    LOCKED_TYPES,
    MEDIA_TYPES,
    EpisodeInput,
    UsageInfo,
    episode_summary_row,
    get_profile,
    parse_json_object,
    postprocess_mention_types,
    process_episode_mentions,
    sanitize_fact,
    sanitize_mention,
    write_csv,
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


# --- process_episode_mentions: the shared sanitize->postprocess->filter pipeline ---
# This is the single definition of "what production extracts," reused by the eval
# harness, so the filter behavior is pinned here.


def test_process_episode_mentions_drops_non_editorial_by_default() -> None:
    tech = get_profile("entity_extraction")
    raw = {
        "mentions": [
            _mention(canonical_name="ChatGPT", entity_type="software_product", is_editorial=True),
            _mention(canonical_name="SponsorCo", entity_type="organization", is_editorial=False),
        ]
    }
    out = process_episode_mentions(raw, 1, tech)
    names = {m["canonical_name"] for m in out}
    assert "ChatGPT" in names
    assert "SponsorCo" not in names  # ad read dropped


def test_process_episode_mentions_focus_core_types_keeps_other() -> None:
    tech = get_profile("entity_extraction")
    raw = {
        "mentions": [
            _mention(canonical_name="ChatGPT", entity_type="software_product"),  # core -> kept
            _mention(canonical_name="Sam Altman", entity_type="person"),         # non-core -> dropped
            _mention(canonical_name="Mystery Thing", entity_type="dragon"),      # unknown -> 'other' -> kept
        ]
    }
    out = process_episode_mentions(raw, 1, tech)
    types = {m["canonical_name"]: m["entity_type"] for m in out}
    assert types.get("ChatGPT") == "software_product"
    assert "Sam Altman" not in types          # non-core filtered out under focus_core_types
    assert types.get("Mystery Thing") == "other"  # unknown kept for review


def test_process_episode_mentions_keeps_ads_tagged_by_default() -> None:
    """Ads are kept and tagged, not dropped (Kevin, 2026-09-01).

    Before this, an ad the model flagged was discarded — which is why the only ads in
    the database are the ones the model MISSED, stored as editorial at full weight.
    """
    tech = get_profile("entity_extraction")
    raw = {
        "mentions": [
            _mention(canonical_name="Sam Altman", entity_type="person", is_editorial=True),
            _mention(canonical_name="SponsorCo", entity_type="organization", is_editorial=False),
        ]
    }
    out = process_episode_mentions(raw, 1, tech, focus_core_types=False)
    by_name = {m["canonical_name"]: m for m in out}
    assert set(by_name) == {"Sam Altman", "SponsorCo"}
    assert by_name["SponsorCo"]["is_editorial"] is False
    assert by_name["SponsorCo"]["sponsor_source"] == "model"
    # Editorial keeps a NULL source — the absence of evidence, not a fourth value.
    assert by_name["Sam Altman"]["is_editorial"] is True
    assert by_name["Sam Altman"]["sponsor_source"] is None


def test_process_episode_mentions_can_still_drop_ads_on_request() -> None:
    tech = get_profile("entity_extraction")
    raw = {
        "mentions": [
            _mention(canonical_name="Sam Altman", entity_type="person", is_editorial=True),
            _mention(canonical_name="SponsorCo", entity_type="organization", is_editorial=False),
        ]
    }
    out = process_episode_mentions(
        raw, 1, tech, drop_sponsor_mentions=True, focus_core_types=False
    )
    assert {m["canonical_name"] for m in out} == {"Sam Altman"}


def test_process_episode_mentions_handles_bad_input() -> None:
    tech = get_profile("entity_extraction")
    assert process_episode_mentions({}, 1, tech) == []
    assert process_episode_mentions({"mentions": "nope"}, 1, tech) == []


# ---- filter stats: an empty result must be explainable (2026-08-23, episode 8429) ----

def test_filter_stats_explain_an_all_filtered_result() -> None:
    from pipeline.scrapers.ai_daily.extract_entities import process_episode_mentions_with_stats

    tech = get_profile("entity_extraction")
    raw = {"mentions": [
        _mention(canonical_name="Every", mention_text="Every", entity_type="organization",
                 context_snippet="Dan Shipper of Every wrote the essay."),
        {"mention_text": "", "canonical_name": "", "context_snippet": ""},  # invalid
    ]}
    kept, stats = process_episode_mentions_with_stats(raw, 8429, tech)
    assert kept == []
    assert stats == {"raw": 2, "sanitize_dropped": 1, "non_editorial_dropped": 0,
                     "non_core_type_dropped": 1, "sponsor_tagged": 0, "kept": 0}


def test_an_ad_only_episode_is_not_a_declared_empty_result() -> None:
    """The 2026-08-23 failure in its new form.

    Episode 8429's candidates were all removed by the filters, the loader saw an empty
    file, and the day went red. An episode whose only content is a sponsor read used to
    land in that same hole — every mention dropped for being non-editorial. Now the ad
    is KEPT and tagged, so the batch has mentions, the loader takes the normal path, and
    the stats say plainly that the content was advertising.
    """
    from pipeline.scrapers.ai_daily.extract_entities import process_episode_mentions_with_stats

    tech = get_profile("entity_extraction")
    raw = {"mentions": [
        _mention(canonical_name="HyperAgent", mention_text="HyperAgent",
                 context_snippet="This episode is brought to you by HyperAgent.", is_editorial=False),
    ]}
    kept, stats = process_episode_mentions_with_stats(raw, 8429, tech)
    assert len(kept) == 1
    assert stats["kept"] == 1 and stats["sponsor_tagged"] == 1
    assert stats["non_editorial_dropped"] == 0
    assert kept[0]["is_editorial"] is False and kept[0]["sponsor_source"] == "model"


def test_filter_stats_count_what_survives() -> None:
    from pipeline.scrapers.ai_daily.extract_entities import process_episode_mentions_with_stats

    tech = get_profile("entity_extraction")
    raw = {"mentions": [_mention(), _mention(canonical_name="Claude", mention_text="Claude", entity_type="model")]}
    kept, stats = process_episode_mentions_with_stats(raw, 1, tech)
    assert len(kept) == 2 and stats["raw"] == 2 and stats["kept"] == 2
    # The plain wrapper returns the same mentions — the eval harness relies on it.
    assert process_episode_mentions(raw, 1, tech) == kept


def test_filter_stats_on_bad_input_are_all_zero() -> None:
    from pipeline.scrapers.ai_daily.extract_entities import FILTER_STAT_KEYS, process_episode_mentions_with_stats

    kept, stats = process_episode_mentions_with_stats({"mentions": "nope"}, 1, get_profile("entity_extraction"))
    assert kept == [] and stats == {k: 0 for k in FILTER_STAT_KEYS}


def test_episode_summary_row_matches_the_csv_columns(tmp_path) -> None:
    """The per-episode row and the episode_summary.csv column list stay in lockstep.

    PR #23 (2026-09-01) added four filter counters to the row and not to the column
    list; csv.DictWriter then refused every batch ("dict contains fields not in
    fieldnames") and the media backfill failed 64/64 batches before a single mention
    loaded. A row is built through the real function and written through the real
    writer, so the next added stat fails here instead of in production.
    """
    episode = EpisodeInput(1, "2026-09-01", "t", "https://x", tmp_path / "1.txt")
    usage = UsageInfo(10, 5, 15, None, None, None)
    row = episode_summary_row(episode, [_mention(needs_review=True)], dict.fromkeys(FILTER_STAT_KEYS, 0), usage)
    assert list(row) == EPISODE_SUMMARY_FIELDS
    assert row["review_count"] == 1 and row["mention_count"] == 1
    write_csv(tmp_path / "s.csv", [row], EPISODE_SUMMARY_FIELDS)
    assert (tmp_path / "s.csv").read_text().splitlines()[0] == ",".join(EPISODE_SUMMARY_FIELDS)


def test_mention_csv_columns_match_what_the_loader_reads(tmp_path) -> None:
    """mentions.csv gets the same lockstep guard as episode_summary.csv.

    That file is the loader's actual input, so a column added to the row and not to the
    list fails the whole batch the same way PR #23 did — and a column added to the list
    and not read by the loader is a value that silently never lands. Both halves are
    pinned here: the writer accepts the row, and load_entity_batch names every field it
    consumes.
    """
    from pipeline.scrapers.ai_daily.extract_entities import MENTION_CSV_FIELDS
    from pipeline.scrapers.ai_daily import load_entity_batch as leb

    row = {
        "episode_id": 1,
        "entity_type": "software_product",
        "canonical_name": "Blitzy",
        "mention_text": "Blitzy",
        "platform": "",
        "source_url": "",
        "sentiment_label": "neutral",
        "is_editorial": "false",
        "sponsor_source": "roster",
        "confidence": "0.9000",
        "needs_review": "false",
        "review_reason": "",
        "context_snippet": "Brought to you by Blitzy.",
        "quoted_text": "",
        "facts_json": "[]",
    }
    assert sorted(row) == sorted(MENTION_CSV_FIELDS)
    write_csv(tmp_path / "m.csv", [row], MENTION_CSV_FIELDS)
    assert (tmp_path / "m.csv").read_text().splitlines()[0] == ",".join(MENTION_CSV_FIELDS)

    # The loader reads these by key; a rename here would KeyError at load time.
    source = (Path(leb.__file__)).read_text(encoding="utf-8")
    for field in ("sponsor_source", "is_editorial", "context_snippet", "facts_json"):
        assert f'"{field}"' in source or f"'{field}'" in source, field


def test_sponsor_source_is_written_as_an_empty_cell_not_the_string_none() -> None:
    """The loader turns "" into SQL NULL; the string "none" would trip sql/009's CHECK."""
    from pipeline.scrapers.ai_daily.extract_entities import process_episode_mentions

    tech = get_profile("entity_extraction")
    kept = process_episode_mentions({"mentions": [_mention()]}, 1, tech)
    assert kept[0]["sponsor_source"] is None
    assert (kept[0].get("sponsor_source") or "") == ""

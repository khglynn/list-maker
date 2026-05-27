from pipeline.scrapers.ai_daily.load_entity_batch import (
    derive_tags,
    merge_aliases,
    normalize_name,
    parse_aliases,
    parse_facts_json,
)


def test_normalize_name_removes_punctuation_and_extra_spacing() -> None:
    assert normalize_name("  GPT-4.1 / Mini!!  ") == "gpt 41 mini"


def test_parse_aliases_dedupes_by_normalized_name() -> None:
    assert parse_aliases(["OpenAI", " open-ai ", "Open AI", "", "ChatGPT"]) == [
        "OpenAI",
        "open-ai",
        "ChatGPT",
    ]


def test_merge_aliases_preserves_existing_and_adds_new_unique_values() -> None:
    assert merge_aliases(["OpenAI"], ["open-ai", "ChatGPT"]) == [
        "OpenAI",
        "open-ai",
        "ChatGPT",
    ]


def test_parse_facts_json_ignores_invalid_or_non_list_json() -> None:
    assert parse_facts_json("") == []
    assert parse_facts_json("not json") == []
    assert parse_facts_json('{"fact_key": "domain"}') == []


def test_parse_facts_json_keeps_dict_items_from_list() -> None:
    assert parse_facts_json('[{"fact_key": "domain"}, "bad"]') == [
        {"fact_key": "domain"}
    ]


def test_derive_tags_from_platform_type_and_facts() -> None:
    tags = derive_tags(
        "survey",
        "X",
        [
            {"fact_key": "benchmark_domain", "fact_value": "video"},
            {"fact_key": "contains_survey_questions", "fact_value": True},
            {"fact_key": "untracked", "fact_value": "ignored"},
        ],
    )

    assert tags == {
        "platform": "x",
        "is_survey": True,
        "benchmark_domain": "video",
        "contains_survey_questions": True,
    }

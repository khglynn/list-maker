from pipeline.scrapers.ai_daily.extract_entities import LOCKED_TYPES, MEDIA_TYPES
from pipeline.scrapers.ai_daily.load_entity_batch import (
    VALID_ENTITY_TYPES,
    delete_existing_run,
    derive_tags,
    merge_aliases,
    normalize_entity_type,
    normalize_name,
    parse_aliases,
    parse_facts_json,
)


def test_valid_entity_types_covers_both_taxonomies() -> None:
    # Drift guard: the loader's accepted set must include every tech + media type the
    # extractor can emit — otherwise those mentions silently fall back to "other".
    missing = (set(LOCKED_TYPES) | set(MEDIA_TYPES)) - VALID_ENTITY_TYPES
    assert not missing, f"VALID_ENTITY_TYPES missing extractor types: {sorted(missing)}"


def test_normalize_entity_type_accepts_media() -> None:
    assert normalize_entity_type("movie") == "movie"
    assert normalize_entity_type("Tv_Series") == "tv_series"  # case-insensitive
    assert normalize_entity_type("not_a_real_type") == "other"


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = 2

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, params))


class _FakeConn:
    def __init__(self) -> None:
        self._cursor = _FakeCursor()
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


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


def test_normalize_entity_type_falls_back_to_other() -> None:
    assert normalize_entity_type("  Software_Product ") == "software_product"
    assert normalize_entity_type("model") == "model"
    assert normalize_entity_type("dragon") == "other"
    assert normalize_entity_type("") == "other"
    assert normalize_entity_type(None) == "other"  # None-tolerant guard (no crash)


def test_delete_existing_run_scopes_to_show_and_batch_then_commits() -> None:
    conn = _FakeConn()

    removed = delete_existing_run(conn, show_id=3, batch_name="incremental-1-to-2")

    calls = conn._cursor.calls
    assert len(calls) == 2
    # First: delete the batch's mentions, scoped via the run subquery.
    assert "DELETE FROM ai_mentions" in calls[0][0]
    assert "ai_runs" in calls[0][0]
    assert calls[0][1] == (3, "incremental-1-to-2")
    # Second: delete the run rows themselves, same scope.
    assert "DELETE FROM ai_runs" in calls[1][0]
    assert calls[1][1] == (3, "incremental-1-to-2")
    # Both deletes commit together; returns the removed-run count (rowcount).
    assert conn.committed is True
    assert removed == 2

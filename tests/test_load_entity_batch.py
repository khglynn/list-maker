from __future__ import annotations

import json

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
    read_provenance,
    resolve_transcript_map,
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


class _LookupCursor:
    """Cursor that answers the load-time transcript lookup."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.executed = False

    def __enter__(self) -> "_LookupCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed = True

    def fetchall(self) -> list[dict]:
        return self.rows


class _LookupConn:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._cursor = _LookupCursor(rows or [])

    def cursor(self) -> _LookupCursor:
        return self._cursor


def test_recorded_provenance_wins_over_the_load_time_lookup() -> None:
    """The database can only say whether a transcript exists NOW. Extraction takes
    minutes, so a transcript landing mid-batch would otherwise be stamped onto mentions
    that were mined from show notes — provenance nobody could later tell was fabricated."""
    conn = _LookupConn(rows=[{"episode_id": 7261, "id": 2385}])

    mapping, inferred = resolve_transcript_map(conn, [7261], provenance={7261: None})

    assert mapping == {7261: None}
    assert inferred == []
    assert conn._cursor.executed is False  # never asked the DB


def test_recorded_provenance_carries_the_transcript_actually_read() -> None:
    conn = _LookupConn()
    mapping, inferred = resolve_transcript_map(conn, [7262], provenance={7262: 2384})
    assert mapping == {7262: 2384}
    assert inferred == []


def test_missing_provenance_falls_back_to_lookup_and_says_so() -> None:
    """Hand-run batches have no provenance file. They still load — the fallback is
    named in the return value so the caller can label the provenance inferred."""
    conn = _LookupConn(rows=[{"episode_id": 999, "id": 42}])

    mapping, inferred = resolve_transcript_map(conn, [999], provenance=None)

    assert mapping == {999: 42}
    assert inferred == [999]


def test_partial_provenance_mixes_recorded_and_inferred() -> None:
    conn = _LookupConn(rows=[{"episode_id": 999, "id": 42}])

    mapping, inferred = resolve_transcript_map(conn, [7261, 999], provenance={7261: None})

    assert mapping == {7261: None, 999: 42}
    assert inferred == [999]


def test_read_provenance_round_trips_ints_and_nulls(tmp_path) -> None:
    path = tmp_path / "provenance.json"
    path.write_text(json.dumps({"7261": None, "7262": 2384}), encoding="utf-8")

    assert read_provenance(str(path)) == {7261: None, 7262: 2384}
    assert read_provenance(None) is None

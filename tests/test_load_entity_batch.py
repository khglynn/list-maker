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


# ---- declared empty batches (2026-08-23): recorded, not raised ----

class _RunCursor(_FakeCursor):
    def __init__(self) -> None:
        super().__init__()
        self.rowcount = 0

    def fetchone(self) -> dict:
        return {"id": 77}


class _RunConn(_FakeConn):
    def __init__(self) -> None:
        super().__init__()
        self._cursor = _RunCursor()


def test_record_empty_batch_writes_a_completed_empty_run_with_reasons() -> None:
    from pathlib import Path

    from pipeline.scrapers.ai_daily.load_entity_batch import EMPTY_RUN_STATUS, record_empty_batch

    conn = _RunConn()
    manifest = {
        "episodes": [{"episode_id": 8429}],
        "filter_summary": {"raw": 5, "sanitize_dropped": 0, "non_editorial_dropped": 3,
                           "non_core_type_dropped": 2, "kept": 0},
    }
    run_id, episodes = record_empty_batch(
        conn, show_id=3, batch_name="incremental-8429-to-8429", model="gpt-4.1-mini",
        prompt_version="v1", manifest=manifest, batch_dir=Path("/tmp/b"),
    )
    assert run_id == 77 and episodes == [8429]
    insert_sql, params = next((s, p) for s, p in conn._cursor.calls if "INSERT INTO ai_runs" in s)
    assert params[-1] == EMPTY_RUN_STATUS == "completed_empty"
    recorded = json.loads(params[4])
    assert recorded["episodes"] == [8429] and recorded["empty_result"] is True
    assert recorded["raw_mention_count"] == 5
    assert recorded["dropped"] == {"non_editorial": 3, "non_core_type": 2, "invalid": 0}
    # Idempotent like every other load: the prior run for this batch name is cleared first.
    assert any("DELETE FROM ai_runs" in s for s, _ in conn._cursor.calls)
    assert conn.committed


def test_insert_run_defaults_to_completed() -> None:
    from pipeline.scrapers.ai_daily.load_entity_batch import insert_run

    conn = _RunConn()
    insert_run(conn, show_id=3, batch_name="b", model="m", prompt_version="v", parameters={})
    _, params = conn._cursor.calls[-1]
    assert params[-1] == "completed"


# --- sponsor provenance (sql/009) -------------------------------------------------


def test_sponsor_source_normalizes_to_the_closed_vocabulary() -> None:
    """sql/009 has a CHECK constraint; an unrecognized cell must become NULL rather than
    failing the whole batch on one bad value."""
    from pipeline.scrapers.ai_daily.load_entity_batch import normalize_sponsor_source

    assert normalize_sponsor_source("roster") == "roster"
    assert normalize_sponsor_source("  PHRASE ") == "phrase"
    assert normalize_sponsor_source("model") == "model"
    # Editorial is an absence, not a fourth value.
    assert normalize_sponsor_source("") is None
    assert normalize_sponsor_source(None) is None
    assert normalize_sponsor_source("none") is None
    assert normalize_sponsor_source("sponsored") is None


def test_insert_mention_writes_sponsor_source() -> None:
    from pipeline.scrapers.ai_daily.load_entity_batch import insert_mention

    conn = _RecordingConn()
    insert_mention(
        conn,
        run_id=1,
        transcript_map={7: 70},
        row={
            "episode_id": "7",
            "entity_type": "software_product",
            "canonical_name": "Blitzy",
            "mention_text": "Blitzy",
            "platform": "",
            "source_url": "",
            "sentiment_label": "neutral",
            "is_editorial": "false",
            "sponsor_source": "roster",
            "confidence": "0.9",
            "needs_review": "false",
            "review_reason": "",
            "context_snippet": "Brought to you by Blitzy.",
            "quoted_text": "",
            "facts_json": "[]",
        },
        entity_id=42,
    )
    assert "sponsor_source" in conn.cur.sql
    assert False in conn.cur.params and "roster" in conn.cur.params


def test_insert_mention_writes_null_for_an_editorial_row() -> None:
    from pipeline.scrapers.ai_daily.load_entity_batch import insert_mention

    conn = _RecordingConn()
    insert_mention(
        conn,
        run_id=1,
        transcript_map={},
        row={
            "episode_id": "7",
            "entity_type": "model",
            "canonical_name": "Gemini",
            "mention_text": "Gemini",
            "platform": "",
            "source_url": "",
            "sentiment_label": "neutral",
            "is_editorial": "true",
            "sponsor_source": "",
            "confidence": "0.9",
            "needs_review": "false",
            "review_reason": "",
            "context_snippet": "Gemini models were a distant second.",
            "quoted_text": "",
            "facts_json": "[]",
        },
        entity_id=43,
    )
    assert True in conn.cur.params and None in conn.cur.params


def test_first_seen_as_ad_only_writes_when_absent_and_earliest() -> None:
    """The guard clauses are the whole point: a sponsor read that FOLLOWS real coverage
    must not rewrite an entity's origin story, and a re-load must be a no-op."""
    from pipeline.scrapers.ai_daily.load_entity_batch import record_first_seen_as_ad

    conn = _RecordingConn()
    record_first_seen_as_ad(conn, 42, "2026-08-31")
    sql = " ".join(conn.cur.sql.split())
    assert "first_seen_as_ad" in sql
    assert "NOT (COALESCE(e.attributes, '{}'::jsonb) ? 'first_seen_as_ad')" in sql
    assert "publish_date < %s::date" in sql

    # No date, no claim — never a guessed one.
    conn2 = _RecordingConn()
    assert record_first_seen_as_ad(conn2, 42, None) is False
    assert conn2.cur.sql == ""


class _RecordingCursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple = ()
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = tuple(params)

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RecordingConn:
    def __init__(self) -> None:
        self.cur = _RecordingCursor()
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True

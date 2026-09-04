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
    # Status-blind on purpose: this is what lets a retry replace the 'loading' row a
    # crashed batch leaves behind. A status predicate here would strand it forever.
    assert "status" not in calls[0][0] and "status" not in calls[1][0]
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
    # params[5] is status, params[6] the completed_at CASE flag (see insert_run). A
    # declared-empty batch IS finished, so it keeps its completion timestamp.
    assert params[5] == EMPTY_RUN_STATUS == "completed_empty"
    assert params[6] is True
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
    sql, params = conn._cursor.calls[-1]
    # completed_at is a CASE over the DATABASE clock, not a Python timestamp, so every
    # caller keeps writing the value it wrote before this parameter existed.
    assert "CASE WHEN %s THEN NOW() END" in sql
    assert params[5] == "completed"
    assert params[6] is True
    assert conn.committed is True


def test_insert_run_as_loading_leaves_completed_at_null() -> None:
    """A run still loading has not completed, and a completed_at saying otherwise is the
    plausible-but-false value docs/principles.md says to write NULL for."""
    from pipeline.scrapers.ai_daily.load_entity_batch import LOADING_RUN_STATUS, insert_run

    conn = _RunConn()
    insert_run(
        conn, show_id=3, batch_name="b", model="m", prompt_version="v",
        parameters={}, status=LOADING_RUN_STATUS,
    )
    _, params = conn._cursor.calls[-1]
    assert params[5] == "loading"
    assert params[6] is False  # the CASE writes NULL, not a fake completion time


def test_insert_run_can_defer_its_commit() -> None:
    from pipeline.scrapers.ai_daily.load_entity_batch import insert_run

    conn = _RunConn()
    insert_run(
        conn, show_id=3, batch_name="b", model="m", prompt_version="v",
        parameters={}, commit=False,
    )
    assert conn.committed is False


def test_finalize_run_completed_flips_status_and_honours_commit() -> None:
    from pipeline.scrapers.ai_daily.load_entity_batch import finalize_run_completed

    conn = _RunConn()
    conn._cursor.rowcount = 1
    finalize_run_completed(conn, 77, commit=False)
    sql, params = conn._cursor.calls[-1]
    assert "UPDATE ai_runs" in sql
    assert "status = 'completed'" in sql
    assert params == (77,)
    assert conn.committed is False  # the caller's single commit is what makes it durable

    conn2 = _RunConn()
    conn2._cursor.rowcount = 1
    finalize_run_completed(conn2, 77)
    assert conn2.committed is True


def test_finalize_run_completed_raises_when_the_run_row_is_gone() -> None:
    """Silently updating nothing would report success for a batch of mentions attached
    to a run that no longer exists. Raising rolls the whole batch back instead."""
    import pytest

    from pipeline.scrapers.ai_daily.load_entity_batch import finalize_run_completed

    conn = _RunConn()
    conn._cursor.rowcount = 0
    with pytest.raises(RuntimeError, match="expected 1"):
        finalize_run_completed(conn, 77)
    assert conn.committed is False


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


def test_first_seen_as_ad_is_stamped_after_the_batch_not_during_it() -> None:
    """A batch arrives newest-episode-first, so an inline stamp can be wrong.

    mentions.csv is written in episode order and a multi-episode catch-up runs
    newest-first (Taddy inserts newest-first, and the newer episode gets the smaller
    id). record_first_seen_as_ad's guard asks "does an earlier mention exist?", which is
    only as good as what has been inserted when it runs — so stamping inline lets an ad
    in the NEWER episode claim first-seen before the OLDER episode's editorial mention
    has landed, writing a date that is real but wrong.

    Pinned structurally: the batch loader must collect stamps during the row loop and
    apply them after it, rather than calling record_first_seen_as_ad inside the loop.

    Both passes moved from main() into load_batch_rows when the batch became one
    transaction (2026-09-03); this test follows them. The behavioural twin below is what
    actually proves the ordering — this one only proves the shape is still deliberate.
    """
    import inspect

    from pipeline.scrapers.ai_daily import load_entity_batch as leb

    source = inspect.getsource(leb.load_batch_rows)
    loop_at = source.index("for row in rows:")
    stamp_at = source.index("record_first_seen_as_ad(")
    collect_at = source.index("sponsor_stamps.append(")
    second_pass_at = source.index("for entity_id, publish_date in sponsor_stamps:")

    assert loop_at < collect_at, "stamps are collected inside the row loop"
    assert collect_at < second_pass_at < stamp_at, (
        "record_first_seen_as_ad must be called from the second pass, after the loop"
    )


def test_first_seen_as_ad_runs_after_every_mention_of_the_batch_has_landed() -> None:
    """The behavioural twin of the test above — reads executed SQL, not source text.

    A source-index test proves the code is SHAPED right; it cannot prove the shape has
    the effect it claims. This one watches what actually reaches the database: every
    ai_mentions insert of the batch must precede the first first_seen_as_ad update, so
    the update's "is there an earlier mention?" guard sees the whole batch.
    """
    from pipeline.scrapers.ai_daily.load_entity_batch import load_batch_rows

    conn = _BatchConn()
    load_batch_rows(
        conn,
        run_id=9,
        rows=[
            _csv_row(episode_id="2", canonical_name="Vanta", sponsor_source="roster"),
            _csv_row(episode_id="3", canonical_name="Vanta"),
        ],
        transcript_map={2: None, 3: None},
        publish_dates={2: "2026-08-31", 3: "2026-08-24"},
    )

    executed = [sql for sql, _ in conn.cur.calls]
    last_mention = max(i for i, s in enumerate(executed) if "INSERT INTO ai_mentions" in s)
    first_stamp = min(i for i, s in enumerate(executed) if "first_seen_as_ad" in s)
    assert last_mention < first_stamp


# ---- one transaction per batch (2026-09-03) --------------------------------------
#
# Before this, insert_run committed status='completed' before the first mention existed
# and every helper inside the loop committed on its own, so a process killed mid-batch
# left a 'completed' run beside a partial, permanently-wrong mention count — and
# find_unextracted_episodes, which decides "already extracted" on the presence of
# mentions alone, never retried whichever episodes happened to land first.


def _csv_row(**overrides: str) -> dict[str, str]:
    """One mentions.csv row, with every column the loader reads."""
    row = {
        "episode_id": "1",
        "entity_type": "software_product",
        "canonical_name": "Cursor",
        "mention_text": "Cursor",
        "platform": "",
        "confidence": "0.9",
        "is_editorial": "true",
        "sponsor_source": "",
        "needs_review": "false",
        "sentiment_label": "positive",
        "source_url": "",
        "quoted_text": "",
        "context_snippet": "they mentioned it",
        "review_reason": "",
        "facts_json": "",
    }
    row.update(overrides)
    return row


class _BatchCursor:
    """Answers the loader's fetching queries; records every statement in order."""

    def __init__(self, raise_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = 1
        self._last_sql = ""
        self._next_entity_id = 100
        self._raise_on_call = raise_on_call

    def __enter__(self) -> "_BatchCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, tuple(params)))
        if self._raise_on_call is not None and len(self.calls) >= self._raise_on_call:
            raise RuntimeError("simulated mid-batch crash")
        self._last_sql = sql

    def fetchone(self):
        if "INSERT INTO ai_entities" in self._last_sql:
            self._next_entity_id += 1
            return {"id": self._next_entity_id}
        return None  # the entity lookup misses, so upsert_entity takes the insert branch


class _BatchConn:
    def __init__(self, raise_on_call: int | None = None) -> None:
        self.cur = _BatchCursor(raise_on_call)
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> _BatchCursor:
        return self.cur

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def test_load_batch_rows_never_commits_on_its_own() -> None:
    """The load-bearing one. Entities and mentions must accumulate in the caller's open
    transaction so the single commit that follows is what makes them durable."""
    from pipeline.scrapers.ai_daily.load_entity_batch import load_batch_rows

    conn = _BatchConn()
    mentions, review_open, sponsor, first_seen, cache = load_batch_rows(
        conn,
        run_id=9,
        rows=[
            _csv_row(episode_id="1", canonical_name="Cursor", needs_review="true"),
            _csv_row(episode_id="1", canonical_name="Claude Code", sponsor_source="phrase"),
        ],
        transcript_map={1: 42},
        publish_dates={1: "2026-08-31"},
    )

    assert conn.committed is False
    assert (mentions, review_open, sponsor) == (2, 1, 1)
    assert first_seen == 1 and len(cache) == 2
    assert sum(1 for sql, _ in conn.cur.calls if "INSERT INTO ai_mentions" in sql) == 2


def test_load_batch_rows_leaves_nothing_committed_when_a_row_crashes() -> None:
    """The test that would have caught the original bug: a crash after the first row's
    writes must propagate with NOTHING committed, so the batch is retried whole."""
    import pytest

    from pipeline.scrapers.ai_daily.load_entity_batch import load_batch_rows

    # Row 1 takes 3 statements (entity lookup, entity insert, mention insert); blow up
    # on the 4th, i.e. partway through row 2.
    conn = _BatchConn(raise_on_call=4)
    with pytest.raises(RuntimeError, match="simulated mid-batch crash"):
        load_batch_rows(
            conn,
            run_id=9,
            rows=[
                _csv_row(episode_id="1", canonical_name="Cursor"),
                _csv_row(episode_id="1", canonical_name="Claude Code"),
            ],
            transcript_map={1: None},
            publish_dates={1: "2026-08-31"},
        )

    assert conn.committed is False
    assert any("INSERT INTO ai_mentions" in sql for sql, _ in conn.cur.calls), (
        "the first row really did write — this is a mid-batch crash, not a pre-flight one"
    )


# ---- the assembled main() path --------------------------------------------------
#
# load_batch_rows is tested in isolation above; these two run the whole loader with a
# fake connection, because the property that matters is a property of the ASSEMBLY: how
# many times the process commits, and in what order relative to the status flip.


class _MainCursor:
    """Answers every query main() makes, dispatching on the SQL."""

    def __init__(self, raise_on_sql: str | None = None, nth: int = 1) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = 1
        self._last_sql = ""
        self._raise_on_sql = raise_on_sql
        self._nth = nth
        self._seen = 0
        self._entity_id = 100

    def __enter__(self) -> "_MainCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, tuple(params)))
        self._last_sql = sql
        if self._raise_on_sql and self._raise_on_sql in sql:
            self._seen += 1
            if self._seen >= self._nth:
                raise RuntimeError("simulated crash mid-batch")

    def fetchone(self):
        if "FROM shows" in self._last_sql:
            return {"id": 3}
        if "INSERT INTO ai_runs" in self._last_sql:
            return {"id": 77}
        if "INSERT INTO ai_entities" in self._last_sql:
            self._entity_id += 1
            return {"id": self._entity_id}
        return None  # entity lookup misses -> insert branch

    def fetchall(self):
        return []  # no transcripts, no publish dates


class _MainConn:
    def __init__(self, raise_on_sql: str | None = None, nth: int = 1) -> None:
        self.cur = _MainCursor(raise_on_sql, nth)
        self.commits = 0
        self.rolled_back = False
        self.closed = False
        # SQL executed at the moment of each commit, so a test can ask what was durable.
        self.committed_through: list[int] = []

    def cursor(self) -> _MainCursor:
        return self.cur

    def commit(self) -> None:
        self.commits += 1
        self.committed_through.append(len(self.cur.calls))

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _run_main(monkeypatch, tmp_path, conn) -> None:
    import sys

    from pipeline.scrapers.ai_daily import load_entity_batch as leb

    (tmp_path / "batch_manifest.json").write_text(
        json.dumps({"batch_name": "incremental-1-to-2", "model": "gpt-4.1-mini"}),
        encoding="utf-8",
    )
    header = ",".join(_csv_row().keys())
    rows = [_csv_row(canonical_name="Cursor"), _csv_row(canonical_name="Claude Code")]
    body = "\n".join(",".join(r[k] for k in _csv_row()) for r in rows)
    (tmp_path / "mentions.csv").write_text(f"{header}\n{body}\n", encoding="utf-8")

    # Hermetic: the real load_environment reads ~/.env, outside the repo.
    monkeypatch.setattr(leb, "load_environment", lambda repo_root: None)
    monkeypatch.setattr(leb, "get_db_connection", lambda: conn)
    monkeypatch.setattr(
        sys, "argv",
        ["load_entity_batch.py", "--batch-dir", str(tmp_path), "--show-slug", "ai-daily-brief"],
    )
    leb.main()


def test_main_commits_the_whole_batch_exactly_once(monkeypatch, tmp_path, capsys) -> None:
    """Three commits and no more: the delete, the 'loading' run row, and then ONE that
    carries every entity, every mention and the flip to 'completed' together."""
    conn = _MainConn()
    _run_main(monkeypatch, tmp_path, conn)

    assert conn.commits == 3
    executed = [sql for sql, _ in conn.cur.calls]
    flip_at = next(i for i, s in enumerate(executed) if "SET status = 'completed'" in s)
    first_mention_at = next(i for i, s in enumerate(executed) if "INSERT INTO ai_mentions" in s)
    # Nothing was made durable between the first mention and the status flip.
    assert not [c for c in conn.committed_through if first_mention_at < c <= flip_at]
    assert conn.committed_through[-1] > flip_at
    assert "Mentions inserted: 2" in capsys.readouterr().out


def test_main_leaves_a_loading_row_and_no_mentions_when_a_row_crashes(
    monkeypatch, tmp_path
) -> None:
    """The acceptance case. A crash mid-batch must leave the run at 'loading' with zero
    durable mentions — never 'completed' with a partial count — so the next run sees
    every episode of the batch as unextracted and delete_existing_run replaces the row.

    It crashes on the SECOND mention, not the first, on purpose: row one has to have
    fully written before the failure, or the test would pass just as happily against
    the per-row-commit code this replaces.
    """
    import pytest

    conn = _MainConn(raise_on_sql="INSERT INTO ai_mentions", nth=2)
    with pytest.raises(RuntimeError, match="simulated crash mid-batch"):
        _run_main(monkeypatch, tmp_path, conn)

    run_sql, run_params = next(
        (s, p) for s, p in conn.cur.calls if "INSERT INTO ai_runs" in s
    )
    assert run_params[5] == "loading" and run_params[6] is False
    # Two commits only: the delete and the 'loading' row. Nothing after.
    assert conn.commits == 2
    assert conn.rolled_back is True
    assert conn.closed is True
    assert not any("SET status = 'completed'" in s for s, _ in conn.cur.calls)

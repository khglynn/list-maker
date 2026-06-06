from pipeline.scrapers.ai_daily.load_entity_batch import (
    delete_existing_run,
    derive_tags,
    merge_aliases,
    normalize_name,
    parse_aliases,
    parse_facts_json,
)


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

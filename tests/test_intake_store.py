"""What the intake's SQL promises: which columns, which statuses, which re-runs are free.

These pin the contract rather than the syntax — a mocked cursor records the statement
and the params, and the assertions are about the decisions encoded in them: the first
discovery wins, a pre-check outcome never gets re-judged when the rubric changes, a
missing table produces the paste instruction instead of a traceback, and no writer
invents a value for something it doesn't know.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from pipeline.scrapers.intake import store
from pipeline.scrapers.intake.judge import Decision, Precheck, Verdict
from pipeline.scrapers.intake.sources import Candidate


class _Cursor:
    """Records every statement; returns whatever the connection was primed with."""

    def __init__(self, conn: "_Conn") -> None:
        self.conn = conn

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params=()) -> None:
        self.conn.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.conn.rows.pop(0) if self.conn.rows else None

    def fetchall(self):
        return self.conn.rows.pop(0) if self.conn.rows else []


class _Conn:
    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[str, object]] = []
        self.commits = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1


def _candidate(**kw) -> Candidate:
    base = dict(source="openai-rss", title="How people are using ChatGPT",
                url="https://openai.com/index/how-people-are-using-chatgpt/",
                published_on=date(2026, 9, 1), category=["Research"],
                discovered_via={"feed": "https://openai.com/news/rss.xml"})
    base.update(kw)
    return Candidate(**base)


# ── the table has to exist ──────────────────────────────────────────────────

def test_require_table_gives_the_paste_instruction_not_a_traceback() -> None:
    conn = _Conn([{"reg": None}])
    with pytest.raises(SystemExit) as exc:
        store.require_table(conn)
    message = str(exc.value)
    assert store.TABLE in message and store.MIGRATION_PATH in message
    assert "init_entity_schema.py" in message  # a runnable paste, not just "it's missing"


def test_table_exists_reads_the_registry() -> None:
    conn = _Conn([{"reg": "intake_candidates"}])
    assert store.table_exists(conn) is True
    assert conn.calls[0][1] == ("public.intake_candidates",)


# ── upsert: the first discovery wins ────────────────────────────────────────

def test_upsert_canonicalizes_and_counts_new_versus_known() -> None:
    conn = _Conn([{"id": 1, "created": True}, {"id": 2, "created": False}])
    new, existing = store.upsert_candidates(conn, [
        _candidate(url="HTTP://WWW.openai.com/index/a/?utm_source=x"),
        _candidate(url="https://openai.com/index/b"),
    ])
    assert (new, existing) == (1, 1)
    # canonicalize_url is applied at the write boundary, so no code path can create a
    # second row for the http:// or utm-tagged twin of a URL already in the table.
    assert conn.calls[0][1][0] == "https://openai.com/index/a"
    assert conn.commits == 1


def test_upsert_keeps_the_first_discovery_and_records_the_second_source() -> None:
    sql = " ".join(store._UPSERT_SQL.split())
    assert "ON CONFLICT (url) DO UPDATE" in sql
    for field in ("title", "published_on"):
        assert f"{field} = COALESCE(intake_candidates.{field}" in sql
    # existing keys win the jsonb merge; a NEW source is appended, once
    assert "EXCLUDED.discovered_via || intake_candidates.discovered_via" in sql
    assert "also_sources" in sql and "@> to_jsonb(EXCLUDED.source)" in sql
    # nothing here can move a row backwards through the lifecycle
    assert "status" not in sql.split("DO UPDATE")[1]


def test_upsert_skips_candidates_with_no_url() -> None:
    conn = _Conn([])
    assert store.upsert_candidates(conn, [_candidate(url="")]) == (0, 0)
    assert conn.calls == []  # an unresolved citation is not a row


# ── the work list ───────────────────────────────────────────────────────────

def test_needs_judging_re_judges_a_new_rubric_but_not_pre_check_outcomes() -> None:
    conn = _Conn([[]])
    store.needs_judging(conn, "abc123def456")
    sql, params = conn.calls[0]
    assert "status = %s OR (status = ANY(%s)" in sql
    assert "precheck IS NULL" in sql       # a dead link stays dead when the rubric changes
    assert "prompt_version IS DISTINCT FROM %s" in sql
    assert params == ("discovered", ["judged", "skipped"], "abc123def456")
    assert "saved" not in params[1] and "held" not in params[1]


def test_pending_and_limit_are_ordered_newest_post_first() -> None:
    conn = _Conn([[]])
    store.pending(conn, store.STATUS_JUDGED, limit=5)
    sql, params = conn.calls[0]
    assert "ORDER BY published_on DESC NULLS LAST, discovered_at DESC" in sql
    assert sql.endswith("LIMIT %s") and params == ("judged", 5)


def test_needs_mirroring_finds_rows_the_log_never_got_or_got_stale() -> None:
    conn = _Conn([[]])
    store.needs_mirroring(conn, limit=100)
    sql, params = conn.calls[0]
    # never mirrored, or mirrored before the row last changed
    assert "notion_page_id IS NULL OR notion_synced_at IS NULL" in sql
    assert "notion_synced_at < updated_at" in sql
    # a row still at `discovered` has nothing to show yet — no verdict, no scrape
    assert "status <> %s" in sql and params == ("discovered", 100)


def test_already_ingested_urls_short_circuits_on_an_empty_list() -> None:
    conn = _Conn()
    assert store.already_ingested_urls(conn, []) == set()
    assert conn.calls == []  # never send `= ANY('{}')` to Neon to learn nothing


def test_already_ingested_urls_compares_against_episodes() -> None:
    conn = _Conn([[{"url": "https://a"}]])
    assert store.already_ingested_urls(conn, ["https://a", "https://b"]) == {"https://a"}
    assert "FROM episodes WHERE url = ANY(%s)" in conn.calls[0][0]


# ── writers ─────────────────────────────────────────────────────────────────

def test_record_scrape_fills_gaps_and_never_invents_a_zero() -> None:
    conn = _Conn()
    store.record_scrape(conn, 7, words=None, links_out=None, text_sha256=None,
                        title="", published_on=None)
    sql, params = conn.calls[0]
    assert params[:3] == (None, None, None)   # a failed scrape is not zero words
    assert "title = COALESCE(title, NULLIF(%s, ''))" in sql
    assert "published_on = COALESCE(published_on, %s)" in sql


def test_record_precheck_stores_the_bare_token_and_the_detail_separately() -> None:
    conn = _Conn()
    store.record_precheck(conn, 3, Precheck("dead"), detail="404 Not Found")
    sql, params = conn.calls[0]
    # the token stays groupable for the weekly line; the specifics ride in failed_reason
    assert params == ("dead", "skipped", "404 Not Found", 3)
    assert "precheck = %s" in sql and "failed_reason = %s" in sql


def test_record_precheck_refuses_a_candidate_that_passed() -> None:
    with pytest.raises(ValueError):
        store.record_precheck(_Conn(), 3, Precheck(None, status="judged"))


def test_record_precheck_holds_a_pdf_rather_than_skipping_it() -> None:
    conn = _Conn()
    store.record_precheck(conn, 3, Precheck("pdf", status="held"))
    assert conn.calls[0][1][:2] == ("pdf", "held")


def _decision(verdict="save", disputed=False) -> Decision:
    judge = Verdict(verdict, 0.82, "first-party usage figures", "google/gemini-3.7-flash",
                    rule="S1", job="deck")
    checker = Verdict(verdict, 0.7, "same", "openai/gpt-5.6-luna", rule="S1", job="deck")
    return Decision(verdict, 0.76, judge.reason, judge, checker, disputed, "v0abc",
                    rule="S1", job="deck")


def test_record_decision_stores_both_models_and_the_rubric_version() -> None:
    conn = _Conn()
    status = store.record_decision(conn, 9, _decision(disputed=True))
    sql, params = conn.calls[0]
    assert status == "judged"  # shadow mode: a save with nothing ingested
    # rule and job ride with the verdict: a one-line reason ages into prose, a rule
    # id stays checkable against the rubric version that produced it
    assert params == ("save", 0.76, "first-party usage figures", "S1", "deck",
                      "google/gemini-3.7-flash", "openai/gpt-5.6-luna", "save",
                      True, "v0abc", "judged", 9)
    assert "judged_at = now()" in sql
    # a re-judge clears the stale pre-check, or the row would claim two causes
    assert "precheck = NULL" in sql


def test_record_decision_maps_skip_to_skipped_and_honours_an_override() -> None:
    conn = _Conn()
    assert store.record_decision(conn, 9, _decision(verdict="skip")) == "skipped"
    assert store.record_decision(conn, 9, _decision(), status=store.STATUS_SAVED) == "saved"


def test_record_decision_survives_a_single_model_run() -> None:
    judge = Verdict("save", 0.9, "why", "google/gemini-3.7-flash")
    conn = _Conn()
    store.record_decision(conn, 1, Decision("save", 0.9, "why", judge, None, False, "v1"))
    params = conn.calls[0][1]
    assert params[3:5] == (None, None)   # a verdict with no rule stays NULL, not ""
    assert params[6:8] == (None, None)   # no checker model, no checker verdict


def test_mark_saved_and_failed_record_provenance() -> None:
    conn = _Conn()
    store.mark_saved(conn, 4, 991, override_by="kevin")
    assert conn.calls[0][1] == ("saved", 991, "kevin", 4)
    assert "COALESCE(%s, override_by)" in conn.calls[0][0]  # a later run can't erase it

    conn = _Conn()
    store.mark_failed(conn, 4, "x" * 900)
    assert len(conn.calls[0][1][1]) == 500  # truncated, not dropped


# ── what the weekly line reads ──────────────────────────────────────────────

def test_record_notion_page_does_not_bump_updated_at() -> None:
    conn = _Conn()
    store.record_notion_page(conn, 4, "page-9")
    sql, params = conn.calls[0]
    assert params == ("page-9", 4)
    # `updated_at` means "content last changed"; needs_mirroring compares the two, so
    # bumping it here would re-push the same row to Notion on every run forever
    assert "updated_at" not in sql
    assert "notion_synced_at = now()" in sql


def test_weekly_counts_asks_the_table_and_splits_the_precheck_reasons() -> None:
    conn = _Conn([
        {"judged": 12, "would_save": 4, "held": 1},
        [{"precheck": "thin", "n": 6}, {"precheck": "duplicate", "n": 2}],
    ])
    counts = store.weekly_counts(conn, datetime(2026, 9, 2, 12, 0))
    assert counts["judged"] == 12
    assert counts["precheck_reasons"] == {"thin": 6, "duplicate": 2}
    # PDFs are reported under `held`; counting them here too would stop the line adding up
    assert "AND status = %s" in conn.calls[1][0]
    assert conn.calls[1][1][1] == store.STATUS_SKIPPED


def test_titles_prefers_a_title_and_falls_back_to_the_url() -> None:
    conn = _Conn([[{"label": "How people are using ChatGPT"}]])
    assert store.titles(conn, datetime(2026, 9, 2), store.STATUS_JUDGED) == [
        "How people are using ChatGPT"]
    assert "COALESCE(NULLIF(title, ''), url)" in conn.calls[0][0]


def test_select_columns_covers_everything_the_notion_mirror_reads() -> None:
    from pipeline.scrapers.intake import notion_log

    columns = {c.strip() for c in store.SELECT_COLUMNS.split(",")}
    row = {c: None for c in columns} | {"url": "https://x/a", "status": "judged"}
    notion_log.build_properties(row)  # a missing column would KeyError here, in CI

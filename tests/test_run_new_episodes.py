"""Orchestrator episode-selection + --backfill flag (Workstream A3).

The window toggle is verified with a fake DB connection (no live DB): we assert
the SQL parameterizes the window via make_interval (no hardcoded literal) when
recent_only, and omits it entirely under backfill.
"""

from __future__ import annotations

import pytest

from pipeline.run_new_episodes import (
    RECENT_EPISODE_WINDOW_DAYS,
    SELF_HEAL_MAX_EPISODES_PER_RUN,
    TRANSCRIPT_GRACE_DAYS,
    _take_batches_within_budget,
    find_transcript_race_batches,
    find_unextracted_episodes,
    parse_args,
)


class _Cursor:
    def __init__(self, rows: list | None = None) -> None:
        self.sql = ""
        self.params: list = []
        self.rows = rows or []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: list) -> None:
        self.sql = sql
        self.params = list(params)

    def fetchall(self) -> list:
        return self.rows


class _Conn:
    def __init__(self, rows: list | None = None) -> None:
        self.cursor_obj = _Cursor(rows)

    def cursor(self) -> _Cursor:
        return self.cursor_obj


def test_recent_only_applies_parameterized_window() -> None:
    conn = _Conn()
    find_unextracted_episodes(conn, 3, recent_only=True)
    assert "make_interval" in conn.cursor_obj.sql
    assert "INTERVAL '90 days'" not in conn.cursor_obj.sql  # no hardcoded literal
    assert conn.cursor_obj.params == [3, RECENT_EPISODE_WINDOW_DAYS]
    assert conn.cursor_obj.sql.count("%s") == len(conn.cursor_obj.params)


def test_backfill_omits_the_window() -> None:
    conn = _Conn()
    find_unextracted_episodes(conn, 3, recent_only=False)
    assert "make_interval" not in conn.cursor_obj.sql
    assert conn.cursor_obj.params == [3]
    assert conn.cursor_obj.sql.count("%s") == len(conn.cursor_obj.params)


def test_require_transcript_waits_for_the_transcript_within_the_grace_window() -> None:
    """Transcript-based (Taddy) shows wait for the transcript rather than mining the
    blurb. The show-notes fallback is a race — Taddy publishes a transcript ~a day late,
    and an episode extracted from its blurb was never re-extracted once the real text
    landed. The notes are reachable only through the grace-window branch."""
    conn = _Conn()
    find_unextracted_episodes(conn, 3, require_transcript=True)
    sql = conn.cursor_obj.sql
    assert "et.transcript_text IS NOT NULL" in sql
    # Notes are gated behind the age test, never offered as an immediate alternative.
    assert "COALESCE(et.transcript_text, ep.description_body)" not in sql
    assert "ep.publish_date < CURRENT_DATE - make_interval(days => %s)" in sql
    # grace window is parameterized ahead of the recency window
    assert conn.cursor_obj.params == [3, TRANSCRIPT_GRACE_DAYS, RECENT_EPISODE_WINDOW_DAYS]
    assert sql.count("%s") == len(conn.cursor_obj.params)


def test_transcript_wait_is_bounded_not_forever() -> None:
    """An episode whose transcript never arrives must still get extracted eventually.
    Blocking forever would trade a wrong-source extraction for a missing one."""
    conn = _Conn()
    find_unextracted_episodes(conn, 3, require_transcript=True, grace_days=2)
    assert "ep.description_body IS NOT NULL" in conn.cursor_obj.sql
    assert conn.cursor_obj.params[1] == 2


def test_selection_reports_which_source_each_episode_will_use() -> None:
    """Provenance is recorded, not re-derived: the caller learns up front whether an
    episode is being extracted from its transcript or from its notes."""
    conn = _Conn(rows=[{"id": 7261, "source": "transcript"}, {"id": 9000, "source": "show_notes"}])
    episodes = find_unextracted_episodes(conn, 3, require_transcript=True)
    assert [(e.episode_id, e.source) for e in episodes] == [
        (7261, "transcript"),
        (9000, "show_notes"),
    ]


def test_show_notes_fallback_stays_for_shows_without_transcripts() -> None:
    """Gabfest-style shows have no transcripts by design — the COALESCE is correct there."""
    conn = _Conn()
    find_unextracted_episodes(conn, 54, require_transcript=False)
    assert "COALESCE(et.transcript_text, ep.description_body)" in conn.cursor_obj.sql
    assert conn.cursor_obj.params == [54, RECENT_EPISODE_WINDOW_DAYS]
    assert conn.cursor_obj.sql.count("%s") == len(conn.cursor_obj.params)


def test_race_batches_return_whole_batches_not_loose_episodes() -> None:
    """The heal re-extracts by original batch name because delete_existing_run keys on
    it. Healing episode 7261 alone would delete healthy sibling 7262 and not replace it."""
    conn = _Conn(rows=[{"batch_name": "incremental-7261-to-7262", "episode_ids": [7261, 7262]}])
    batches = find_transcript_race_batches(conn, 3)
    assert batches == [("incremental-7261-to-7262", [7261, 7262])]
    assert "m.transcript_id IS NULL" in conn.cursor_obj.sql
    assert "JOIN episode_transcripts et" in conn.cursor_obj.sql  # only if a transcript exists NOW
    assert conn.cursor_obj.sql.count("%s") == len(conn.cursor_obj.params)


def test_race_batch_budget_takes_whole_batches() -> None:
    candidates = [("a", [1, 2]), ("b", [3, 4]), ("c", [5])]
    # Budget of 3 fits batch "a" (2 episodes); "b" would take it to 4, so it waits.
    assert _take_batches_within_budget(candidates, 3) == [("a", [1, 2])]


def test_race_batch_budget_never_parks_an_oversized_batch_forever() -> None:
    """A batch bigger than the per-run budget still runs — refusing it would leave the
    episode permanently damaged, which is the exact failure the heal exists to end."""
    assert _take_batches_within_budget([("big", [1, 2, 3, 4, 5])], 3) == [("big", [1, 2, 3, 4, 5])]


def test_self_heal_budget_is_bounded() -> None:
    assert 0 < SELF_HEAL_MAX_EPISODES_PER_RUN <= 8


def test_backfill_flag_parses(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["run_new_episodes.py", "--shows", "ai-daily-brief", "--backfill"]
    )
    assert parse_args().backfill is True

    monkeypatch.setattr(
        "sys.argv", ["run_new_episodes.py", "--shows", "ai-daily-brief"]
    )
    assert parse_args().backfill is False


def test_run_script_retries_then_succeeds(monkeypatch) -> None:
    from pipeline import run_new_episodes as rne

    class _Result:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stdout = "done"
            self.stderr = ""

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return _Result(1) if calls["n"] == 1 else _Result(0)  # fail once, then succeed

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    monkeypatch.setattr(rne.time, "sleep", lambda _s: None)

    assert rne.run_script("x.py", [], dry_run=False, label="step") is True
    assert calls["n"] == 2  # one retry, then success


def test_run_script_gives_up_after_max_retries(monkeypatch) -> None:
    from pipeline import run_new_episodes as rne

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "boom"

    calls = {"n": 0}
    sleeps: list = []

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    monkeypatch.setattr(rne.time, "sleep", lambda s: sleeps.append(s))

    assert rne.run_script("x.py", [], dry_run=False, label="step") is False
    assert calls["n"] == rne.MAX_STEP_RETRIES + 1
    assert sleeps == [5, 10]  # exponential backoff between the 3 attempts


# --- retryable vs deterministic (exit 2) ---
# A missing credential or an unknown show slug fails identically on every attempt, so
# retrying spends 15s of backoff to relearn it and buries the cause under two more
# identical tracebacks. Exit 2 opts a step out of the retry; everything else keeps it.


def _never_sleep(_s) -> None:
    raise AssertionError("run_script slept — it retried when it should not have")


def test_run_script_does_not_retry_on_deterministic_exit_code(monkeypatch) -> None:
    from pipeline import run_new_episodes as rne

    class _Result:
        returncode = rne.DETERMINISTIC_EXIT_CODE
        stdout = ""
        stderr = "OPENAI_API_KEY is required"

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    # Sleeping is what proves a retry happened; a fast test would pass either way.
    monkeypatch.setattr(rne.time, "sleep", _never_sleep)

    assert rne.run_script("x.py", [], dry_run=False, label="step") is False
    assert calls["n"] == 1


def test_run_script_still_retries_other_nonzero_exits(monkeypatch) -> None:
    """The deterministic branch must not swallow the ordinary failure path: only
    exit 2 opts out, and 3 (or 127, or anything else) is still worth another try."""
    from pipeline import run_new_episodes as rne

    class _Result:
        returncode = 3
        stdout = ""
        stderr = "boom"

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    monkeypatch.setattr(rne.time, "sleep", lambda _s: None)

    assert rne.run_script("x.py", [], dry_run=False, label="step") is False
    assert calls["n"] == rne.MAX_STEP_RETRIES + 1


def test_run_script_timeout_is_not_mistaken_for_deterministic(monkeypatch) -> None:
    """On TimeoutExpired `result` stays None, so the exit-code check must not read it.
    A timeout is the canonical transient failure and has to keep retrying."""
    from pipeline import run_new_episodes as rne

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        raise rne.subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 600))

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    monkeypatch.setattr(rne.time, "sleep", lambda _s: None)

    assert rne.run_script("x.py", [], dry_run=False, label="step") is False
    assert calls["n"] == rne.MAX_STEP_RETRIES + 1


def test_deterministic_exit_code_matches_argparse_usage_convention() -> None:
    """2 is not an invented number — argparse already exits 2 on a bad invocation, so
    a step called with wrong arguments lands in the no-retry branch for free."""
    import argparse

    from pipeline import run_new_episodes as rne

    parser = argparse.ArgumentParser()
    parser.add_argument("--show-id", type=int, required=True)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--show-id", "not-a-number"])
    assert exc.value.code == rne.DETERMINISTIC_EXIT_CODE


def test_run_script_retries_on_timeout(monkeypatch) -> None:
    from pipeline import run_new_episodes as rne

    class _Result:
        returncode = 0
        stdout = "done"
        stderr = ""

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rne.subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 600))
        return _Result()

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    monkeypatch.setattr(rne.time, "sleep", lambda _s: None)

    assert rne.run_script("x.py", [], dry_run=False, label="step") is True
    assert calls["n"] == 2  # a timeout is treated as a retryable failure


class _PrepCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def __enter__(self) -> "_PrepCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params=()) -> None:
        pass

    def fetchall(self) -> list[dict]:
        return self.rows


class _PrepConn:
    def __init__(self, rows: list[dict]) -> None:
        self._cursor = _PrepCursor(rows)

    def cursor(self) -> _PrepCursor:
        return self._cursor

    def close(self) -> None:
        pass


def _prep_row(
    episode_id: int,
    title: str,
    text: str,
    transcript_id: int | None,
    raw_content: str | None = None,
) -> dict:
    return {
        "episode_id": episode_id,
        "title": title,
        "publish_date": "2026-07-30",
        "episode_url": "https://example.test/ep",
        "transcript_id": transcript_id,
        "source_text": text,
        "from_transcript": transcript_id is not None,
        "raw_content": raw_content,
    }


def test_prepare_inputs_records_provenance_per_episode(monkeypatch, tmp_path) -> None:
    from pipeline import run_new_episodes as rne

    monkeypatch.setattr(rne, "PIPELINE_DIR", tmp_path)
    conn = _PrepConn([
        _prep_row(7261, "notes only", "blurb", None),
        _prep_row(7262, "real transcript", "the actual episode text", 2384),
    ])

    _csv, _dir, provenance_path, _roster = rne.prepare_extraction_inputs(conn, [7261, 7262])

    import json as _json

    assert _json.loads(provenance_path.read_text()) == {"7261": None, "7262": 2384}


def test_prepare_inputs_writes_the_sponsor_roster_sidecar(monkeypatch, tmp_path) -> None:
    """The roster travels to the extractor as a sidecar keyed by episode id.

    It is written even when empty: a leftover file from a previous batch would hand the
    extractor another episode's sponsors, and a wrong roster is worse than none.
    """
    from pipeline import run_new_episodes as rne

    monkeypatch.setattr(rne, "PIPELINE_DIR", tmp_path)
    sponsored = _json_raw_content(
        "<p>Today's news.</p><p><strong>Brought to you by:</strong></p>"
        '<p><strong>Blitzy - </strong>Build faster <a href="https://blitzy.com/">x</a></p>'
    )
    conn = _PrepConn([
        _prep_row(7261, "sponsored", "the transcript", 2384, raw_content=sponsored),
        _prep_row(7262, "no sponsors", "the transcript", 2385, raw_content=None),
    ])

    *_rest, roster_path = rne.prepare_extraction_inputs(conn, [7261, 7262])

    import json as _json

    rosters = _json.loads(roster_path.read_text())
    assert rosters == {"7261": [{"name": "Blitzy", "url": "https://blitzy.com/"}]}


def _json_raw_content(description: str) -> str:
    """episodes.raw_content is a TEXT column holding the Taddy JSON payload."""
    import json as _json

    return _json.dumps({"provider": "taddy", "description": description})


def test_prepare_inputs_refreshes_a_stale_cached_source_file(monkeypatch, tmp_path) -> None:
    """The cache can hold the show-notes blurb written by the run that lost the race.
    Skipping the write because the file merely exists would re-extract the same wrong
    text and make the self-heal a no-op."""
    from pipeline import run_new_episodes as rne

    monkeypatch.setattr(rne, "PIPELINE_DIR", tmp_path)
    transcripts_dir = tmp_path / "_cache" / "ai_daily" / "transcripts"
    transcripts_dir.mkdir(parents=True)
    stale = transcripts_dir / "7261-late-transcript.txt"
    stale.write_text("Our Newsletter is BACK", encoding="utf-8")

    conn = _PrepConn([_prep_row(7261, "late transcript", "the real transcript text", 2385)])
    rne.prepare_extraction_inputs(conn, [7261])

    assert stale.read_text(encoding="utf-8") == "the real transcript text"


def _heal_fixture(monkeypatch, tmp_path, *, post_heal, extract_ok=True):
    """Wire step_self_heal_transcript_race with fake DB + extraction."""
    from pipeline import run_new_episodes as rne

    monkeypatch.setattr(rne, "get_db_connection", lambda: _PrepConn([]))
    monkeypatch.setattr(
        rne, "prepare_extraction_inputs",
        lambda conn, ids: (
            tmp_path / "e.csv", tmp_path, tmp_path / "p.json", tmp_path / "r.json"
        ),
    )
    calls = {"n": 0}

    def fake_find(conn, show_id, max_episodes=rne.SELF_HEAL_MAX_EPISODES_PER_RUN):
        calls["n"] += 1
        return [("incremental-7261-to-7262", [7261, 7262])] if calls["n"] == 1 else post_heal

    monkeypatch.setattr(rne, "find_transcript_race_batches", fake_find)
    monkeypatch.setattr(rne, "extract_and_load_batch", lambda *a, **k: extract_ok)
    return rne


def test_self_heal_reports_success_when_the_damage_is_gone(monkeypatch, tmp_path) -> None:
    from pipeline.show_config import get_show

    rne = _heal_fixture(monkeypatch, tmp_path, post_heal=[])
    ok, healed = rne.step_self_heal_transcript_race(get_show("ai-daily-brief"), dry_run=False)

    assert (ok, healed) == (True, 2)


def test_self_heal_fails_loudly_when_the_episode_is_still_damaged(monkeypatch, tmp_path) -> None:
    """A re-extraction that reports success but leaves the episode damaged would be
    retried silently every run. Verifying afterwards turns that into one visible failure."""
    from pipeline.show_config import get_show

    rne = _heal_fixture(
        monkeypatch, tmp_path, post_heal=[("incremental-7261-to-7262", [7261, 7262])]
    )
    ok, _healed = rne.step_self_heal_transcript_race(get_show("ai-daily-brief"), dry_run=False)

    assert ok is False


def test_self_heal_is_a_noop_when_nothing_is_damaged(monkeypatch, tmp_path) -> None:
    from pipeline import run_new_episodes as rne
    from pipeline.show_config import get_show

    monkeypatch.setattr(rne, "get_db_connection", lambda: _PrepConn([]))
    monkeypatch.setattr(rne, "find_transcript_race_batches", lambda *a, **k: [])

    assert rne.step_self_heal_transcript_race(get_show("pchh"), dry_run=False) == (True, 0)


def test_declared_empty_episodes_are_not_requeued() -> None:
    """One declared answer is final. Re-asking the model daily is how a sponsor read
    got stored as editorial content on 2026-08-24 (episode 8429)."""
    conn = _Conn()
    find_unextracted_episodes(conn, 3, recent_only=True)
    sql = conn.cursor_obj.sql
    assert "completed_empty" in sql
    assert "r.parameters->'episodes' @> to_jsonb(ep.id)" in sql
    assert sql.count("%s") == len(conn.cursor_obj.params)

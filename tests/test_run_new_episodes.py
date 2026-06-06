"""Orchestrator episode-selection + --backfill flag (Workstream A3).

The window toggle is verified with a fake DB connection (no live DB): we assert
the SQL parameterizes the window via make_interval (no hardcoded literal) when
recent_only, and omits it entirely under backfill.
"""

from pipeline.run_new_episodes import (
    RECENT_EPISODE_WINDOW_DAYS,
    find_unextracted_episodes,
    parse_args,
)


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params: list = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: list) -> None:
        self.sql = sql
        self.params = list(params)

    def fetchall(self) -> list:
        return []


class _Conn:
    def __init__(self) -> None:
        self.cursor_obj = _Cursor()

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


def test_backfill_flag_parses(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["run_new_episodes.py", "--shows", "ai-daily-brief", "--backfill"]
    )
    assert parse_args().backfill is True

    monkeypatch.setattr(
        "sys.argv", ["run_new_episodes.py", "--shows", "ai-daily-brief"]
    )
    assert parse_args().backfill is False

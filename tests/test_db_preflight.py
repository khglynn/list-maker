"""DB preflight: one bounded attempt, diagnostics in the message, non-zero exit on failure.

The 2026-08-31 run spent 41 minutes rediscovering a dead runner→Neon path in every step.
The preflight is the step that ends that in about a minute — so it must (a) try exactly
once, (b) say WHERE it failed (host, addresses, runner), and (c) exit non-zero.
"""

from __future__ import annotations

import pytest

from pipeline import db_preflight


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        assert sql == "SELECT 1"


class _Conn:
    closed = False

    def cursor(self):
        return _Cursor()

    def close(self):
        self.closed = True


def test_preflight_ok_uses_a_single_attempt(monkeypatch) -> None:
    seen: list[dict] = []

    def fake_connect(**kwargs):
        seen.append(kwargs)
        return _Conn()

    monkeypatch.setattr(db_preflight, "get_db_connection", fake_connect)
    ok, message = db_preflight.check("postgresql://u:p@ep-x-pooler.neon.tech/db")
    assert ok and "ep-x-pooler.neon.tech" in message
    assert seen == [{"attempts": 1}]


def test_preflight_failure_names_host_addresses_and_runner(monkeypatch) -> None:
    def dead(**kwargs):
        raise RuntimeError("could not connect to Neon after 1 attempt(s): Network is unreachable")

    monkeypatch.setattr(db_preflight, "get_db_connection", dead)
    monkeypatch.setattr(db_preflight, "resolved_addresses", lambda host: ["3.132.12.55", "3.137.42.68"])
    monkeypatch.setenv("RUNNER_NAME", "GitHub-Actions-42")
    monkeypatch.setenv("GITHUB_WORKFLOW", "Entities + Media")

    ok, message = db_preflight.check("postgresql://u:p@ep-x-pooler.neon.tech/db")
    assert not ok
    for needle in ("ep-x-pooler.neon.tech", "3.132.12.55", "GitHub-Actions-42", "Entities + Media", "Network is unreachable"):
        assert needle in message, needle


def test_preflight_main_posts_and_exits_nonzero_on_failure(monkeypatch, capsys) -> None:
    posted: list[str] = []
    monkeypatch.setattr(db_preflight, "load_environment", lambda: None)
    monkeypatch.setattr(db_preflight, "check", lambda url: (False, "DB preflight failed: boom"))
    monkeypatch.setattr(db_preflight, "post_slack", lambda text: posted.append(text) or True)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")

    with pytest.raises(SystemExit) as exc:
        db_preflight.main()
    assert exc.value.code == 1
    assert posted == ["DB preflight failed: boom"]
    assert "boom" in capsys.readouterr().err


def test_preflight_main_is_quiet_on_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(db_preflight, "load_environment", lambda: None)
    monkeypatch.setattr(db_preflight, "check", lambda url: (True, "DB preflight ok"))
    monkeypatch.setattr(db_preflight, "post_slack", lambda text: (_ for _ in ()).throw(AssertionError("no Slack on success")))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    db_preflight.main()
    assert "DB preflight ok" in capsys.readouterr().out

"""Shared logging foundation (Workstream A5)."""

import logging

import pytest

from pipeline.common import ensure_spotify_token, get_logger, post_slack


def test_get_logger_is_idempotent_and_configured(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    name = "pipeline.test.a5"
    logging.getLogger(name).handlers.clear()  # clean slate

    first = get_logger(name)
    assert first.level == logging.DEBUG
    assert len(first.handlers) == 1
    assert first.propagate is False

    # Repeated calls don't stack handlers (idempotent).
    second = get_logger(name)
    assert second is first
    assert len(second.handlers) == 1


def test_get_logger_defaults_to_info(monkeypatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    name = "pipeline.test.a5_default"
    logging.getLogger(name).handlers.clear()
    assert get_logger(name).level == logging.INFO


def test_get_logger_falls_back_on_bogus_level(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "NONSENSE")
    name = "pipeline.test.a5_bogus"
    logging.getLogger(name).handlers.clear()
    assert get_logger(name).level == logging.INFO


def test_post_slack_noop_without_webhook(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert post_slack("hello") is False


def test_post_slack_posts_when_webhook_set(monkeypatch) -> None:
    import requests

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    sent = {}

    class _Resp:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        sent["url"] = url
        sent["text"] = json["text"]
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    assert post_slack("hello") is True
    assert sent == {"url": "https://hooks.slack.test/x", "text": "hello"}


# --- ensure_spotify_token (Workstream E — Spotify auth fail-fast hardening) ---


class _FakeStdin:
    """Stand-in for sys.stdin so tests control isatty() (CI vs real terminal)."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _FakeAuthManager:
    """Minimal SpotifyOAuth stand-in for ensure_spotify_token."""

    def __init__(self, *, token, expired: bool) -> None:
        self._token = token
        self._expired = expired
        self.refreshed_with = None

    def get_cached_token(self):
        return self._token

    def is_token_expired(self, token) -> bool:
        return self._expired

    def refresh_access_token(self, refresh_token):
        self.refreshed_with = refresh_token
        return {"access_token": "new", "refresh_token": refresh_token}


def test_ensure_spotify_token_valid_passes() -> None:
    am = _FakeAuthManager(token={"access_token": "a", "refresh_token": "r"}, expired=False)
    ensure_spotify_token(am)  # valid → no raise, no refresh
    assert am.refreshed_with is None


def test_ensure_spotify_token_expired_refreshes() -> None:
    am = _FakeAuthManager(token={"access_token": "a", "refresh_token": "r"}, expired=True)
    ensure_spotify_token(am)  # expired but refreshable → no raise
    assert am.refreshed_with == "r"


def test_ensure_spotify_token_refresh_returns_none_headless_raises(monkeypatch) -> None:
    # spotipy may return None/empty (not raise) on a failed refresh — still no usable token.
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=False))
    am = _FakeAuthManager(token={"access_token": "a", "refresh_token": "r"}, expired=True)
    am.refresh_access_token = lambda refresh_token: None
    with pytest.raises(RuntimeError, match="re-auth"):
        ensure_spotify_token(am)


def test_ensure_spotify_token_refresh_failure_headless_raises(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=False))
    am = _FakeAuthManager(token={"access_token": "a", "refresh_token": "r"}, expired=True)

    def _boom(refresh_token):
        raise RuntimeError("spotify refresh boom")

    am.refresh_access_token = _boom
    with pytest.raises(RuntimeError, match="re-auth"):
        ensure_spotify_token(am)


def test_ensure_spotify_token_missing_headless_raises(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=False))
    am = _FakeAuthManager(token=None, expired=True)
    with pytest.raises(RuntimeError, match="re-auth"):
        ensure_spotify_token(am)


def test_ensure_spotify_token_missing_interactive_passes(monkeypatch) -> None:
    # A real terminal → let spotipy run its interactive flow (local re-auth still works).
    monkeypatch.setattr("sys.stdin", _FakeStdin(tty=True))
    am = _FakeAuthManager(token=None, expired=True)
    ensure_spotify_token(am)  # no raise


def test_spotify_builders_share_canonical_scope() -> None:
    """Three scripts share ONE token cache; per-script scopes mint tokens the
    others reject (the 2026-06-11 incident: a user-library-read re-auth broke
    the playlist sync). Source-shape guard: every SpotifyOAuth builder must use
    common.SPOTIFY_SCOPE — a literal scope= string anywhere is the bug returning."""
    from pathlib import Path

    builders = [
        "pipeline/sync_playlist.py",
        "pipeline/spotify_match.py",
        "pipeline/scrapers/tal/scoring_match.py",
    ]
    for rel in builders:
        src = Path(rel).read_text()
        assert "scope=SPOTIFY_SCOPE" in src, f"{rel}: must use the canonical scope"
        assert 'scope="' not in src, f"{rel}: literal scope string found"


# ---- Neon connection: connect timeout + bounded retry (the 2026-08-31 41-minute run) ----

def test_get_db_connection_passes_a_timeout_and_retries_transient_failures(monkeypatch) -> None:
    import psycopg2

    from pipeline import common

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    calls: list[dict] = []

    def fake_connect(url, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise psycopg2.OperationalError("connection to server at h, port 5432 failed: timed out")
        return "conn"

    sleeps: list[int] = []
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    monkeypatch.setattr(common.time, "sleep", lambda s: sleeps.append(s))

    assert common.get_db_connection() == "conn"
    assert len(calls) == 3
    assert all(kw["connect_timeout"] == common.DB_CONNECT_TIMEOUT_SECONDS for kw in calls)
    assert sleeps == list(common.DB_CONNECT_BACKOFF_SECONDS)


def test_get_db_connection_gives_up_with_one_clear_error(monkeypatch) -> None:
    import psycopg2

    from pipeline import common

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")

    def always_fail(url, **kwargs):
        raise psycopg2.OperationalError("Network is unreachable")

    monkeypatch.setattr(psycopg2, "connect", always_fail)
    monkeypatch.setattr(common.time, "sleep", lambda _s: None)

    with pytest.raises(RuntimeError, match=f"after {common.DB_CONNECT_ATTEMPTS} attempts"):
        common.get_db_connection()


def test_get_db_connection_requires_a_url(monkeypatch) -> None:
    from pipeline import common

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        common.get_db_connection()

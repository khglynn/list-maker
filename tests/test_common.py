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

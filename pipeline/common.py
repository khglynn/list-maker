"""Shared utilities for pod-lists pipeline scripts."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a configured structured logger (timestamp + level + name).

    Idempotent — repeated calls don't stack handlers. Level comes from the
    LOG_LEVEL env var (default INFO), so autonomous/CI runs are diagnosable.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
        logger.propagate = False
    level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    return logger


def post_slack(text: str) -> bool:
    """Post a message to Slack via the SLACK_WEBHOOK_URL env var.

    No-op (logged) when the webhook isn't set, and never raises — alerting must
    not break a pipeline run. Returns True if Slack accepted the message.
    """
    url = os.getenv("SLACK_WEBHOOK_URL")
    logger = get_logger("pipeline.slack")
    if not url:
        logger.info("Slack alert (no SLACK_WEBHOOK_URL): %s", text)
        return False
    try:
        import requests

        resp = requests.post(url, json={"text": text}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:  # alerting must never break the run
        logger.warning("Slack post failed: %s", exc)
        return False


def get_repo_root() -> Path:
    """Return the repo root (parent of pipeline/)."""
    return Path(__file__).resolve().parent.parent


def load_environment(repo_root: Path | None = None) -> None:
    """Load env vars from standard pod-lists locations."""
    if repo_root is None:
        repo_root = get_repo_root()
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


# A Neon connection that can't be made should fail in seconds, not minutes. libpq's
# default is NO connect timeout, and the pooler hostname resolves to six addresses
# (three IPv6 that GitHub runners can't route, three IPv4). On 2026-08-31 one GitHub
# runner VM lost its egress path to those three IPv4 addresses for the life of the job
# (SYN blackhole, no reset) — Neon itself was up: two sibling jobs dispatched the same
# minute connected to the same pooler and did real work. Every step rediscovered the
# hole on its own and waited ~135s per address × 3 before giving up: 41 minutes to
# report one fact. libpq applies connect_timeout PER ADDRESS, so one attempt here is
# bounded at ~3 × DB_CONNECT_TIMEOUT_SECONDS on a runner (the IPv6 ones fail at once);
# three attempts with short backoff still ride out a brief blip, and a dead path
# surfaces as one clear error in a few minutes. db_preflight.py runs first in every
# workflow with a single attempt so the job stops in about a minute, not per step.
DB_CONNECT_TIMEOUT_SECONDS = int(os.getenv("DB_CONNECT_TIMEOUT") or "20")
DB_CONNECT_ATTEMPTS = 3
DB_CONNECT_BACKOFF_SECONDS = (10, 30)
# TCP keepalives so a connection held across a long step (an LLM batch, a Notion
# sync) is noticed as dead in ~1 minute instead of hanging on a silent NAT drop.
DB_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}


def get_db_connection(attempts: int | None = None):
    """Connect to Neon with RealDictCursor, a connect timeout, and a bounded retry.

    `attempts` overrides DB_CONNECT_ATTEMPTS — the preflight passes 1 so a dead
    network path fails the job in about a minute rather than after the full retry.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError("Missing dependency: psycopg2-binary") from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")

    total = attempts or DB_CONNECT_ATTEMPTS
    last_exc: Exception | None = None
    for attempt in range(1, total + 1):
        try:
            return psycopg2.connect(
                db_url,
                cursor_factory=RealDictCursor,
                connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
                **DB_KEEPALIVE_KWARGS,
            )
        except psycopg2.OperationalError as exc:
            last_exc = exc
            if attempt == total:
                break
            wait = DB_CONNECT_BACKOFF_SECONDS[min(attempt, len(DB_CONNECT_BACKOFF_SECONDS)) - 1]
            first_line = (str(exc).strip().splitlines() or ["?"])[0][:200]
            get_logger("pipeline.common").warning(
                "db connect attempt %d/%d failed, retrying in %ds: %s",
                attempt, total, wait, first_line,
            )
            time.sleep(wait)
    raise RuntimeError(
        f"could not connect to Neon after {total} attempt(s) "
        f"({DB_CONNECT_TIMEOUT_SECONDS}s connect timeout per address): {last_exc}"
    ) from last_exc


# THE canonical Spotify scope — the UNION of every script's needs. All three
# get_spotify_client builders share ONE cache file; if they request different
# scopes, whichever script authenticates last mints a token the others reject
# (spotipy refuses a cached token whose scope doesn't cover the request). That
# exact failure hit on 2026-06-11: a spotify_match re-auth wrote a
# user-library-read-only token and sync_playlist went dark. One scope, one cache.
SPOTIFY_SCOPE = (
    "user-library-read playlist-read-private "
    "playlist-modify-public playlist-modify-private"
)


def ensure_spotify_token(auth_manager) -> None:
    """Fail fast on a missing/expired Spotify token instead of hanging on spotipy's
    interactive auth flow.

    spotipy's SpotifyOAuth, given no usable cached token, calls input() to read the
    redirect URL — which blocks forever in a headless/CI runner with no stdin. That
    silently broke SOP/TAL: the music pipeline hung 30 min on every scheduled run with
    real work, got cancelled, and never synced the playlists.

    Validate the cached token up front (refreshing if expired). If there's still no
    usable token, raise a clear error *only when headless* — in a real terminal we let
    spotipy run its normal interactive flow so local re-auth still works.
    """
    cached = auth_manager.get_cached_token()
    if cached and not auth_manager.is_token_expired(cached):
        return
    if cached:  # present but expired — try a silent refresh
        refresh_token = cached.get("refresh_token")
        if refresh_token:
            try:
                if auth_manager.refresh_access_token(refresh_token):
                    return  # refreshed to a usable token
                # spotipy can return None/empty instead of raising — treat as no token.
            except Exception:  # noqa: BLE001 — fall through to the no-token handling
                pass
    # No usable token. Headless → fail loudly + fast; interactive → allow re-auth.
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Spotify token missing/expired in a non-interactive context — re-auth locally "
            "(python spotify_match.py --show-id 1 --limit 1) and update the SPOTIFY_CACHE_JSON secret"
        )

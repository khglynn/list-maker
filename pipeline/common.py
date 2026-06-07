"""Shared utilities for pod-lists pipeline scripts."""

from __future__ import annotations

import logging
import os
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


def get_db_connection():
    """Connect to Neon database with RealDictCursor."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError("Missing dependency: psycopg2-binary") from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)

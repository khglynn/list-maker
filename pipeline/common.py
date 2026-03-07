"""Shared utilities for pod-lists pipeline scripts."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


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

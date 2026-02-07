#!/usr/bin/env python3
"""
Remove duplicate rows in ai_episode_reference_links for link discovery output.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_environment(repo_root: Path) -> None:
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def get_db_connection():
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("Missing dependency: psycopg2-binary") from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(1)
                FROM ai_episode_reference_links
                WHERE source_kind = 'link_discovery';
                """
            )
            before = int(cur.fetchone()[0])

            cur.execute(
                """
                WITH ranked AS (
                  SELECT id,
                         ROW_NUMBER() OVER (
                           PARTITION BY episode_id, url, COALESCE(linked_entity_id, 0), source_kind
                           ORDER BY id
                         ) AS rn
                  FROM ai_episode_reference_links
                  WHERE source_kind = 'link_discovery'
                )
                DELETE FROM ai_episode_reference_links l
                USING ranked r
                WHERE l.id = r.id
                  AND r.rn > 1;
                """
            )
            deleted = int(cur.rowcount)

            cur.execute(
                """
                SELECT COUNT(1)
                FROM ai_episode_reference_links
                WHERE source_kind = 'link_discovery';
                """
            )
            after = int(cur.fetchone()[0])

        conn.commit()
        print(f"before={before} deleted={deleted} after={after}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

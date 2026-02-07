#!/usr/bin/env python3
"""
Create AI Daily entity schema tables in Neon.

This is non-destructive and only creates ai_* tables.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv


TYPE_DEFINITIONS = {
    "software_product": "Software product/app/platform.",
    "model": "AI model family/version.",
    "benchmark": "Benchmark or evaluation framework.",
    "report": "Named report or analysis publication.",
    "survey": "Survey instrument or survey result set.",
    "paper": "Research paper or preprint.",
    "account": "Creator/account identity on a platform.",
    "social_post": "Specific social post/thread/message.",
    "blog_post": "Blog/article/newsletter post.",
    "organization": "Company/lab/university/media/nonprofit.",
    "person": "Individual person.",
    "other": "Unresolved/unknown mention pending review.",
}


def get_db_connection():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: psycopg2-binary. Install pipeline requirements first."
        ) from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def load_environment(repo_root: Path) -> None:
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def apply_schema(conn, schema_sql: str) -> None:
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


def seed_type_definitions(conn) -> None:
    with conn.cursor() as cur:
        for key, description in TYPE_DEFINITIONS.items():
            cur.execute(
                """
                INSERT INTO ai_entity_type_definitions (type_key, description, is_active, created_at, updated_at)
                VALUES (%s, %s, TRUE, NOW(), NOW())
                ON CONFLICT (type_key) DO UPDATE
                  SET description = EXCLUDED.description,
                      is_active = TRUE,
                      updated_at = NOW();
                """,
                (key, description),
            )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize AI Daily entity schema in Neon")
    parser.add_argument(
        "--schema-file",
        default="pipeline/scrapers/ai_daily/sql/001_ai_entity_schema.sql",
        help="Schema SQL file path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)

    schema_path = (repo_root / args.schema_file).resolve()
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = get_db_connection()
    try:
        apply_schema(conn, schema_sql=schema_sql)
        seed_type_definitions(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM ai_entity_type_definitions;")
            type_count = int(cur.fetchone()["cnt"])
        print(f"Schema applied: {schema_path}")
        print(f"Type definitions active: {type_count}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

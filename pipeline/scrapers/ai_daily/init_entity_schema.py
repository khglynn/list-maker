#!/usr/bin/env python3
"""
Initialize AI Daily entity schema in Neon.

Default behavior creates the lean v2 schema (ai_runs, ai_entities, ai_mentions).
Use --reset to remove existing ai_* tables/views first.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize AI Daily entity schema in Neon")
    parser.add_argument(
        "--schema-file",
        default="pipeline/scrapers/ai_daily/sql/001_ai_entity_schema.sql",
        help="Schema SQL file path",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop existing ai_* tables/views before creating schema",
    )
    return parser.parse_args()


def drop_ai_objects(conn) -> tuple[list[str], list[str]]:
    dropped_views: list[str] = []
    dropped_tables: list[str] = []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'public'
              AND table_name LIKE 'ai_%'
            ORDER BY table_name;
            """
        )
        view_names = [r["table_name"] for r in cur.fetchall()]
        for name in view_names:
            cur.execute(f'DROP VIEW IF EXISTS "{name}" CASCADE;')
            dropped_views.append(name)

        cur.execute(
            """
            SELECT matviewname
            FROM pg_matviews
            WHERE schemaname = 'public'
              AND matviewname LIKE 'ai_%'
            ORDER BY matviewname;
            """
        )
        matview_names = [r["matviewname"] for r in cur.fetchall()]
        for name in matview_names:
            cur.execute(f'DROP MATERIALIZED VIEW IF EXISTS "{name}" CASCADE;')
            dropped_views.append(name)

        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name LIKE 'ai_%'
            ORDER BY table_name;
            """
        )
        table_names = [r["table_name"] for r in cur.fetchall()]
        for name in table_names:
            cur.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE;')
            dropped_tables.append(name)

    conn.commit()
    return dropped_views, dropped_tables


def apply_schema(conn, schema_sql: str) -> None:
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


def summarize_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
              AND table_name LIKE 'ai_%'
            ORDER BY table_name;
            """
        )
        return [r["table_name"] for r in cur.fetchall()]


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
        if args.reset:
            dropped_views, dropped_tables = drop_ai_objects(conn)
            print(f"Dropped views: {len(dropped_views)}")
            print(f"Dropped tables: {len(dropped_tables)}")

        apply_schema(conn, schema_sql=schema_sql)
        current_tables = summarize_tables(conn)
        print(f"Schema applied: {schema_path}")
        print(f"AI tables now present ({len(current_tables)}):")
        for name in current_tables:
            print(f"- {name}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

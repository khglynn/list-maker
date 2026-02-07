#!/usr/bin/env python3
"""
Simple quality report for lean AI Daily schema.

No dependency on custom SQL views.
"""

from __future__ import annotations

import argparse
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
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError("Missing dependency: psycopg2-binary") from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Show AI extraction summary")
    p.add_argument("--run-id", type=int, default=1, help="Run ID for focused summary")
    p.add_argument("--top", type=int, default=15, help="Rows to show in top lists")
    return p.parse_args()


def print_runs(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id AS run_id,
                   r.batch_name,
                   r.model,
                   COUNT(DISTINCT m.episode_id) AS episodes,
                   COUNT(DISTINCT m.id) AS mentions,
                   COUNT(DISTINCT m.entity_id) AS entities,
                   COUNT(*) FILTER (WHERE m.needs_review) AS needs_review,
                   COUNT(*) FILTER (WHERE m.source_url IS NOT NULL AND BTRIM(m.source_url) <> '') AS with_links
            FROM ai_runs r
            LEFT JOIN ai_mentions m ON m.run_id = r.id
            GROUP BY r.id, r.batch_name, r.model
            ORDER BY r.id;
            """
        )
        rows = cur.fetchall()

    print("=== RUNS ===")
    for r in rows:
        print(
            f"run {r['run_id']}: {r['batch_name']} ({r['model']}) | "
            f"episodes={r['episodes']} mentions={r['mentions']} entities={r['entities']} "
            f"needs_review={r['needs_review']} with_links={r['with_links']}"
        )
    print()


def print_top_entities(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.mention_type, e.canonical_name, COUNT(*) AS mention_count
            FROM ai_mentions m
            JOIN ai_entities e ON e.id = m.entity_id
            WHERE m.run_id = %s
            GROUP BY m.mention_type, e.canonical_name
            ORDER BY mention_count DESC, e.canonical_name
            LIMIT %s;
            """,
            (run_id, top_n),
        )
        rows = cur.fetchall()
    print(f"=== TOP ENTITIES (run {run_id}) ===")
    for r in rows:
        print(f"{r['mention_count']:>3}  {r['mention_type']:<14} {r['canonical_name']}")
    print()


def print_overlap(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.mention_type,
                   e.canonical_name,
                   COUNT(DISTINCT m.episode_id) AS episodes,
                   COUNT(*) AS mentions
            FROM ai_mentions m
            JOIN ai_entities e ON e.id = m.entity_id
            WHERE m.run_id = %s
            GROUP BY m.mention_type, e.canonical_name
            HAVING COUNT(DISTINCT m.episode_id) >= 2
            ORDER BY episodes DESC, mentions DESC, e.canonical_name
            LIMIT %s;
            """,
            (run_id, top_n),
        )
        rows = cur.fetchall()
    print(f"=== OVERLAP (run {run_id}, appears in 2+ episodes) ===")
    if not rows:
        print("No repeated entities yet in this run.")
    for r in rows:
        print(f"{r['episodes']} eps, {r['mentions']} mentions  {r['mention_type']:<14} {r['canonical_name']}")
    print()


def print_surveys(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.episode_id,
                   ep.title AS episode_title,
                   m.canonical_name,
                   COALESCE(m.source_url, '') AS source_url,
                   LEFT(COALESCE(m.context_snippet, ''), 160) AS snippet
            FROM ai_mentions m
            JOIN episodes ep ON ep.id = m.episode_id
            WHERE m.run_id = %s
              AND m.mention_type = 'survey'
            ORDER BY m.episode_id, m.canonical_name
            LIMIT %s;
            """,
            (run_id, top_n),
        )
        rows = cur.fetchall()
    print(f"=== SURVEYS (run {run_id}) ===")
    if not rows:
        print("No survey mentions in this run.")
    for r in rows:
        link = r["source_url"] if r["source_url"] else "(missing link)"
        print(f"ep {r['episode_id']} | {r['canonical_name']} | {link}")
        if r["snippet"]:
            print(f"  {r['snippet']}")
    print()


def print_links(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.episode_id,
                   m.mention_type,
                   m.canonical_name,
                   m.link_status,
                   m.link_confidence,
                   COALESCE(m.source_url, '') AS source_url
            FROM ai_mentions m
            WHERE m.run_id = %s
              AND m.mention_type IN ('account', 'report', 'survey', 'paper', 'blog_post', 'social_post')
            ORDER BY
              CASE WHEN m.source_url IS NULL OR BTRIM(m.source_url) = '' THEN 1 ELSE 0 END,
              m.link_confidence DESC NULLS LAST,
              m.canonical_name
            LIMIT %s;
            """,
            (run_id, top_n),
        )
        rows = cur.fetchall()
    print(f"=== LINK STATUS (run {run_id}) ===")
    if not rows:
        print("No link-target mention rows in this run.")
    for r in rows:
        score = "n/a" if r["link_confidence"] is None else f"{float(r['link_confidence']):.3f}"
        url = r["source_url"] if r["source_url"] else "(missing)"
        print(
            f"ep {r['episode_id']} | {r['mention_type']:<12} {r['canonical_name']} | "
            f"{r['link_status']:<13} {score} | {url}"
        )
    print()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)
    conn = get_db_connection()
    try:
        print_runs(conn)
        print_top_entities(conn, run_id=args.run_id, top_n=args.top)
        print_overlap(conn, run_id=args.run_id, top_n=args.top)
        print_surveys(conn, run_id=args.run_id, top_n=args.top)
        print_links(conn, run_id=args.run_id, top_n=args.top)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

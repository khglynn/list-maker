#!/usr/bin/env python3
"""
Quick summary report for AI Daily extraction runs.

This is intentionally simple so we can sanity-check output quality before
scaling extraction to more episodes.
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
    p.add_argument("--run-id", type=int, default=4, help="Run ID for focused summary")
    p.add_argument("--top", type=int, default=15, help="Rows to show in top lists")
    return p.parse_args()


def print_run_summary(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id AS run_id,
                   r.batch_name,
                   r.model,
                   COUNT(DISTINCT m.id) AS mention_rows,
                   COUNT(DISTINCT m.entity_id) AS distinct_entities,
                   COUNT(DISTINCT f.id) AS fact_rows,
                   COUNT(DISTINCT q.id) AS review_rows,
                   COUNT(DISTINCT m.episode_id) AS episodes_processed
            FROM ai_extraction_runs r
            LEFT JOIN ai_entity_mentions m ON m.run_id = r.id
            LEFT JOIN ai_entity_facts f ON f.run_id = r.id
            LEFT JOIN ai_mention_review_queue q ON q.mention_id = m.id
            GROUP BY r.id, r.batch_name, r.model
            ORDER BY r.id;
            """
        )
        rows = cur.fetchall()
    print("=== RUNS ===")
    for r in rows:
        model_name = r.get("model_name") or r.get("model") or "unknown_model"
        episodes = r.get("episodes_processed") or 0
        mentions = r.get("mentions_total") or r.get("mention_rows") or r.get("mention_count") or 0
        entities = r.get("distinct_entities") or r.get("entity_count") or 0
        facts = r.get("facts_total") or r.get("fact_rows") or r.get("fact_count") or 0
        reviews = r.get("review_rows") or r.get("review_count") or 0
        print(
            f"run {r['run_id']}: {r['batch_name']} ({model_name}) | "
            f"episodes={episodes} mentions={mentions} "
            f"entities={entities} facts={facts} review_rows={reviews}"
        )
    print()


def print_link_summary(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(1) AS c FROM ai_reference_link_candidates;")
        candidates = int(cur.fetchone()["c"])
        cur.execute("SELECT COUNT(1) AS c FROM ai_episode_reference_links;")
        promoted = int(cur.fetchone()["c"])
        cur.execute(
            """
            SELECT COUNT(1) AS c
            FROM ai_entity_mentions
            WHERE source_url IS NOT NULL AND BTRIM(source_url) <> '';
            """
        )
        mentions_with_links = int(cur.fetchone()["c"])
    print("=== LINKS ===")
    print(f"candidates={candidates} promoted_links={promoted} mentions_with_source_url={mentions_with_links}")
    print()


def print_top_entities(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.mention_type, e.canonical_name, COUNT(1) AS mention_count
            FROM ai_entity_mentions m
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
            SELECT m.mention_type, e.canonical_name,
                   COUNT(DISTINCT m.episode_id) AS episodes,
                   COUNT(1) AS mentions
            FROM ai_entity_mentions m
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
            SELECT m.episode_id, ep.title AS episode_title, e.canonical_name,
                   COALESCE(m.source_url, '') AS source_url,
                   LEFT(COALESCE(m.context_snippet, ''), 140) AS snippet
            FROM ai_entity_mentions m
            JOIN ai_entities e ON e.id = m.entity_id
            JOIN episodes ep ON ep.id = m.episode_id
            WHERE m.run_id = %s
              AND m.mention_type = 'survey'
            ORDER BY m.episode_id, e.canonical_name
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


def print_auto_links(conn, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.episode_id,
                   ep.title AS episode_title,
                   COALESCE(e.canonical_name, '(no entity)') AS canonical_name,
                   l.url,
                   l.verification_status
            FROM ai_episode_reference_links l
            JOIN episodes ep ON ep.id = l.episode_id
            LEFT JOIN ai_entities e ON e.id = l.linked_entity_id
            WHERE l.source_kind = 'link_discovery'
            ORDER BY l.created_at DESC
            LIMIT %s;
            """,
            (top_n,),
        )
        rows = cur.fetchall()
    print("=== AUTO LINKS ===")
    if not rows:
        print("No auto-discovered links yet.")
    for r in rows:
        print(f"ep {r['episode_id']} | {r['canonical_name']} | {r['verification_status']} | {r['url']}")
    print()


def print_auto_link_candidates(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.mention_type,
                   COALESCE(e.canonical_name, m.mention_text) AS canonical_name,
                   c.candidate_url,
                   c.match_confidence
            FROM ai_reference_link_candidates c
            JOIN ai_entity_mentions m ON m.id = c.mention_id
            LEFT JOIN ai_entities e ON e.id = m.entity_id
            WHERE m.run_id = %s
              AND c.verification_status = 'auto_verified'
            ORDER BY c.match_confidence DESC, canonical_name
            LIMIT %s;
            """,
            (run_id, top_n),
        )
        rows = cur.fetchall()
    print(f"=== AUTO-VERIFIED CANDIDATES (run {run_id}) ===")
    if not rows:
        print("No auto-verified candidates in this run.")
    for r in rows:
        score = f"{float(r['match_confidence']):.3f}" if r["match_confidence"] is not None else "n/a"
        print(f"{score}  {r['mention_type']:<14} {r['canonical_name']} -> {r['candidate_url']}")
    print()


def print_link_gaps(conn, run_id: int, top_n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.mention_type,
                   COALESCE(e.canonical_name, m.mention_text) AS canonical_name,
                   COUNT(1) AS missing_rows
            FROM ai_entity_mentions m
            LEFT JOIN ai_entities e ON e.id = m.entity_id
            WHERE m.run_id = %s
              AND m.is_editorial = TRUE
              AND m.mention_type IN ('account', 'report', 'survey', 'paper', 'blog_post', 'social_post')
              AND (m.source_url IS NULL OR BTRIM(m.source_url) = '')
            GROUP BY m.mention_type, COALESCE(e.canonical_name, m.mention_text)
            ORDER BY missing_rows DESC, canonical_name
            LIMIT %s;
            """,
            (run_id, top_n),
        )
        rows = cur.fetchall()
    print(f"=== MISSING LINK QUEUE (run {run_id}) ===")
    if not rows:
        print("No missing links for target types in this run.")
    for r in rows:
        print(f"{r['missing_rows']:>3}  {r['mention_type']:<14} {r['canonical_name']}")
    print()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)
    conn = get_db_connection()
    try:
        print_run_summary(conn)
        print_link_summary(conn)
        print_top_entities(conn, run_id=args.run_id, top_n=args.top)
        print_overlap(conn, run_id=args.run_id, top_n=args.top)
        print_surveys(conn, run_id=args.run_id, top_n=args.top)
        print_auto_links(conn, top_n=args.top)
        print_auto_link_candidates(conn, run_id=args.run_id, top_n=args.top)
        print_link_gaps(conn, run_id=args.run_id, top_n=args.top)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

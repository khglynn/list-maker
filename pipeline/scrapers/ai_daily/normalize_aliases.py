#!/usr/bin/env python3
"""
Normalize obvious aliases in ai_entities.

Scope:
- Safe dedupe: same entity_type + same normalized_name
- Curated merges for high-confidence variants discovered in the first batches
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


CURATED_TARGETS = [
    # Surveys: collapse clear naming variants.
    ("survey", "AI Usage Pulse Survey", "AI Usage Pulse Survey for January"),
    ("survey", "AI Usage Pulse Survey", "January AI Pulse survey"),
    ("survey", "AI Usage Pulse Survey", "January AI Usage Pulse Survey"),
    # Software products: common transcript variants.
    ("software_product", "Claude Code", "Clawed Code"),
    ("software_product", "Moltbook", "Multbook"),
    ("software_product", "Claudbot", "Claudebot"),
    # Account variants.
    ("account", "Moltbook", "Multbook"),
    # Model variants.
    ("model", "Claude Sonnet 5", "Sonnet 5"),
]


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


def normalize_alias(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def ensure_alias(conn, entity_id: int, alias_text: str, alias_kind: str = "merge") -> None:
    normalized = normalize_alias(alias_text)
    if not normalized:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_entity_aliases (entity_id, alias_text, normalized_alias, alias_kind, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (entity_id, normalized_alias) DO NOTHING;
            """,
            (entity_id, alias_text, normalized, alias_kind),
        )


def get_entity(conn, entity_type: str, canonical_name: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, entity_type, canonical_name, normalized_name, platform
            FROM ai_entities
            WHERE entity_type = %s AND canonical_name = %s
            ORDER BY id
            LIMIT 1;
            """,
            (entity_type, canonical_name),
        )
        return cur.fetchone()


def merge_entity_into(conn, winner_id: int, loser_id: int, loser_name: str, dry_run: bool) -> None:
    if winner_id == loser_id:
        return
    if dry_run:
        return
    with conn.cursor() as cur:
        # Re-point references.
        cur.execute("UPDATE ai_entity_mentions SET entity_id = %s WHERE entity_id = %s;", (winner_id, loser_id))
        cur.execute("UPDATE ai_entity_facts SET entity_id = %s WHERE entity_id = %s;", (winner_id, loser_id))
        cur.execute(
            "UPDATE ai_reference_link_candidates SET entity_id = %s WHERE entity_id = %s;",
            (winner_id, loser_id),
        )
        cur.execute(
            "UPDATE ai_episode_reference_links SET linked_entity_id = %s WHERE linked_entity_id = %s;",
            (winner_id, loser_id),
        )

        # Move aliases to winner first.
        cur.execute(
            """
            SELECT alias_text, normalized_alias, alias_kind
            FROM ai_entity_aliases
            WHERE entity_id = %s;
            """,
            (loser_id,),
        )
        aliases = cur.fetchall()
        for a in aliases:
            cur.execute(
                """
                INSERT INTO ai_entity_aliases (entity_id, alias_text, normalized_alias, alias_kind, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (entity_id, normalized_alias) DO NOTHING;
                """,
                (winner_id, a["alias_text"], a["normalized_alias"], a["alias_kind"]),
            )

        # Record loser canonical name as alias on winner.
        ensure_alias(conn, winner_id, loser_name, alias_kind="merge")

        # Remove loser aliases + loser entity.
        cur.execute("DELETE FROM ai_entity_aliases WHERE entity_id = %s;", (loser_id,))
        cur.execute("DELETE FROM ai_entities WHERE id = %s;", (loser_id,))


def dedupe_exact_normalized(conn, dry_run: bool) -> int:
    merged = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_type, normalized_name,
                   ARRAY_AGG(id ORDER BY id) AS ids,
                   ARRAY_AGG(canonical_name ORDER BY id) AS names
            FROM ai_entities
            GROUP BY entity_type, normalized_name
            HAVING COUNT(*) > 1
            ORDER BY entity_type, normalized_name;
            """
        )
        dupes = cur.fetchall()

    for row in dupes:
        ids = row["ids"]
        names = row["names"]
        winner_id = int(ids[0])
        for loser_id, loser_name in zip(ids[1:], names[1:]):
            print(
                f"[exact] {row['entity_type']} {row['normalized_name']}: "
                f"keep {winner_id}, merge {int(loser_id)} ({loser_name})"
            )
            if not dry_run:
                merge_entity_into(conn, winner_id, int(loser_id), loser_name, dry_run=False)
            merged += 1

    if not dry_run:
        conn.commit()
    return merged


def apply_curated_merges(conn, dry_run: bool) -> int:
    merged = 0
    for entity_type, winner_name, loser_name in CURATED_TARGETS:
        winner = get_entity(conn, entity_type, winner_name)
        loser = get_entity(conn, entity_type, loser_name)
        if not winner or not loser:
            continue
        winner_id = int(winner["id"])
        loser_id = int(loser["id"])
        if winner_id == loser_id:
            continue
        print(f"[curated] {entity_type}: keep {winner_name} ({winner_id}), merge {loser_name} ({loser_id})")
        if not dry_run:
            merge_entity_into(conn, winner_id, loser_id, loser_name, dry_run=False)
        merged += 1

    if not dry_run:
        conn.commit()
    return merged


def backfill_aliases_for_entities(conn, dry_run: bool) -> int:
    added = 0
    with conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name FROM ai_entities;")
        rows = cur.fetchall()

    for row in rows:
        entity_id = int(row["id"])
        alias_text = row["canonical_name"]
        normalized = normalize_alias(alias_text)
        if not normalized:
            continue
        if dry_run:
            added += 1
            continue
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_entity_aliases (entity_id, alias_text, normalized_alias, alias_kind, created_at)
                VALUES (%s, %s, %s, 'canonical', NOW())
                ON CONFLICT (entity_id, normalized_alias) DO NOTHING;
                """,
                (entity_id, alias_text, normalized),
            )
            if cur.rowcount > 0:
                added += 1
    if not dry_run:
        conn.commit()
    return added


def summarize(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM ai_entities;")
        entities = int(cur.fetchone()["cnt"])
        cur.execute("SELECT COUNT(*) AS cnt FROM ai_entity_aliases;")
        aliases = int(cur.fetchone()["cnt"])
    print(f"entities={entities}, aliases={aliases}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Normalize AI entity aliases")
    p.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)
    conn = get_db_connection()
    try:
        print("Before:")
        summarize(conn)

        merged_exact = dedupe_exact_normalized(conn, dry_run=args.dry_run)
        merged_curated = apply_curated_merges(conn, dry_run=args.dry_run)
        aliases_added = backfill_aliases_for_entities(conn, dry_run=args.dry_run)

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        print("")
        print(
            f"Done. merged_exact={merged_exact}, merged_curated={merged_curated}, "
            f"aliases_added={aliases_added}, dry_run={args.dry_run}"
        )
        print("After:")
        summarize(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

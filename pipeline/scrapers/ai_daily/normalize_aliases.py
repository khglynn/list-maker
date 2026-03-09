#!/usr/bin/env python3
"""
Normalize obvious aliases in lean ai_entities schema.

Scope:
- Safe dedupe: same entity_type + normalized_name
- Curated merges for high-confidence transcript variants
- Backfill aliases from mention_text variants
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Allow imports from pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common import load_environment, get_db_connection


CURATED_TARGETS = [
    # --- existing ---
    ("survey", "AI Usage Pulse Survey", "AI Usage Pulse Survey for January"),
    ("survey", "AI Usage Pulse Survey", "January AI Pulse survey"),
    ("survey", "AI Usage Pulse Survey", "January AI Usage Pulse Survey"),
    ("software_product", "Claude Code", "Clawed Code"),
    ("software_product", "Moltbook", "Multbook"),
    ("software_product", "Claudbot", "Claudebot"),
    ("software_product", "Claudbot", "ClaudeBot"),
    ("model", "Claude Sonnet 5", "Sonnet 5"),
    # --- accounts: name variants ---
    ("account", "AI Safety Memes", "AI safety memes account"),
    ("account", "Chubby", "Chubby Kimonismis"),
    ("account", "Chubby", "ChubbyOnX"),
    ("account", "Google DeepMind", "Google DeepMinder"),
    ("account", "Google", "Google LLC"),
    ("account", "Jimmy Apples", "Jimmy Apples (Twitter account)"),
    ("account", "Swix", "LatentspacesSwix"),
    ("account", "Antoine Osika", "Antoine Osica"),
    ("account", "Antoine Osika", "Antoine"),
    ("account", "I Rule the World", "I rule the world M.O."),
    ("account", "Hater", "Hater at Slow Developer"),
    ("account", "Boris Power", "Boris"),
    ("account", "Logan Kilpatrick", "Logan"),
    # --- models: name variants ---
    ("model", "Gemini", "Google Gemini"),
    ("model", "Flux", "Flux1"),
    ("model", "Bard", "Bard (Google AI)"),
    ("model", "Bard", "Google Bard"),
    ("model", "ChatGPT-4", "ChatGPT"),  # model-type ChatGPT → ChatGPT-4
    # --- other: platform consolidation ---
    ("other", "X (formerly Twitter)", "X"),
    ("other", "X (formerly Twitter)", "X (social media platform)"),
    # --- software: name variants ---
    ("software_product", "Amazon Bedrock", "AWS Bedrock"),
    ("software_product", "Google Bard", "Google Bard"),  # exact dedup handles this
    ("software_product", "N8N", "N8n"),
]


def normalize_alias(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_aliases(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen = set()
    for v in raw:
        text = str(v).strip()
        key = normalize_alias(text)
        if not key or key in seen:
            continue
        out.append(text)
        seen.add(key)
    return out


def merge_alias_lists(*alias_lists: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for aliases in alias_lists:
        for text in aliases:
            key = normalize_alias(text)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out


def get_entity(conn, entity_type: str, canonical_name: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, entity_type, canonical_name, normalized_name, platform, aliases
            FROM ai_entities
            WHERE entity_type = %s AND canonical_name = %s
            ORDER BY id
            LIMIT 1;
            """,
            (entity_type, canonical_name),
        )
        return cur.fetchone()


def merge_entity_into(conn, winner: dict, loser: dict, dry_run: bool) -> None:
    if int(winner["id"]) == int(loser["id"]):
        return

    winner_id = int(winner["id"])
    loser_id = int(loser["id"])

    winner_aliases = parse_aliases(winner["aliases"])
    loser_aliases = parse_aliases(loser["aliases"])
    merged_aliases = merge_alias_lists(
        winner_aliases,
        loser_aliases,
        [winner["canonical_name"]],
        [loser["canonical_name"]],
    )

    if dry_run:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_mentions
            SET entity_id = %s,
                canonical_name = %s,
                updated_at = NOW()
            WHERE entity_id = %s;
            """,
            (winner_id, winner["canonical_name"], loser_id),
        )

        cur.execute(
            """
            UPDATE ai_entities
            SET aliases = %s::jsonb,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (json.dumps(merged_aliases), winner_id),
        )

        cur.execute("DELETE FROM ai_entities WHERE id = %s;", (loser_id,))


def dedupe_exact(conn, dry_run: bool) -> int:
    merged = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT entity_type, normalized_name,
                   ARRAY_AGG(id ORDER BY id) AS ids
            FROM ai_entities
            GROUP BY entity_type, normalized_name
            HAVING COUNT(*) > 1
            ORDER BY entity_type, normalized_name;
            """
        )
        rows = cur.fetchall()

    for row in rows:
        ids = [int(x) for x in row["ids"]]
        winner_id = ids[0]
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ai_entities WHERE id = %s;", (winner_id,))
            winner = cur.fetchone()
        for loser_id in ids[1:]:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM ai_entities WHERE id = %s;", (loser_id,))
                loser = cur.fetchone()
            if not loser:
                continue
            print(
                f"[exact] {row['entity_type']}::{row['normalized_name']} keep {winner_id}, "
                f"merge {loser_id} ({loser['canonical_name']})"
            )
            merge_entity_into(conn, winner=winner, loser=loser, dry_run=dry_run)
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
        if int(winner["id"]) == int(loser["id"]):
            continue
        print(
            f"[curated] {entity_type}: keep {winner_name} ({winner['id']}), "
            f"merge {loser_name} ({loser['id']})"
        )
        merge_entity_into(conn, winner=winner, loser=loser, dry_run=dry_run)
        merged += 1
        if not dry_run:
            conn.commit()
    return merged


def backfill_aliases_from_mentions(conn, dry_run: bool) -> int:
    updated = 0
    with conn.cursor() as cur:
        cur.execute("SELECT id, canonical_name, aliases FROM ai_entities ORDER BY id;")
        entities = cur.fetchall()

    for entity in entities:
        entity_id = int(entity["id"])
        current_aliases = parse_aliases(entity["aliases"])
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT mention_text
                FROM ai_mentions
                WHERE entity_id = %s
                ORDER BY mention_text;
                """,
                (entity_id,),
            )
            mention_aliases = [r["mention_text"] for r in cur.fetchall() if r["mention_text"]]
        merged = merge_alias_lists(current_aliases, mention_aliases, [entity["canonical_name"]])
        if len(merged) == len(current_aliases):
            continue
        updated += 1
        if dry_run:
            continue
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ai_entities
                SET aliases = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (json.dumps(merged), entity_id),
            )
        conn.commit()
    return updated


def summarize(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM ai_entities;")
        entities = int(cur.fetchone()["cnt"])
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM ai_mentions
            WHERE source_url IS NOT NULL AND BTRIM(source_url) <> '';
            """
        )
        mentions_with_links = int(cur.fetchone()["cnt"])
    print(f"entities={entities}, mentions_with_links={mentions_with_links}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Normalize aliases in lean AI schema")
    p.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()
    conn = get_db_connection()
    try:
        print("before:")
        summarize(conn)
        exact = dedupe_exact(conn, dry_run=args.dry_run)
        curated = apply_curated_merges(conn, dry_run=args.dry_run)
        alias_updates = backfill_aliases_from_mentions(conn, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(f"merged_exact={exact}")
        print(f"merged_curated={curated}")
        print(f"alias_rows_updated={alias_updates}")
        print("after:")
        summarize(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""
Load one extracted entity batch from codex-notes artifacts into Neon ai_* tables.

This script is for schema validation and review visibility in Database Studio.
It does not alter SOP/TAL tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def normalize_name(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[\s\-_]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_environment(repo_root: Path) -> None:
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load AI Daily entity batch into Neon")
    parser.add_argument(
        "--batch-dir",
        required=True,
        help="Path to extraction batch dir (contains batch_manifest.json + mentions.csv)",
    )
    parser.add_argument(
        "--show-slug",
        default="ai-daily-brief",
        help="Show slug for extraction run metadata",
    )
    parser.add_argument(
        "--prompt-version",
        default="extract_entities_v1",
        help="Prompt version label for run metadata",
    )
    return parser.parse_args()


def get_show_id(conn, show_slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM shows WHERE slug = %s LIMIT 1;", (show_slug,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Show slug not found: {show_slug}")
        return int(row["id"])


def get_transcript_map(conn, episode_ids: list[int]) -> dict[int, int]:
    if not episode_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT episode_id, id
            FROM episode_transcripts
            WHERE episode_id = ANY(%s);
            """,
            (episode_ids,),
        )
        rows = cur.fetchall()
    result: dict[int, int] = {}
    for row in rows:
        result[int(row["episode_id"])] = int(row["id"])
    return result


def insert_run(
    conn,
    *,
    show_id: int,
    batch_name: str,
    model: str,
    prompt_version: str,
    parameters: dict[str, Any],
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_extraction_runs (
              show_id, batch_name, run_type, provider, model, prompt_version, parameters, status, started_at, completed_at, created_at
            )
            VALUES (%s, %s, 'entity_extraction', 'openai', %s, %s, %s::jsonb, 'completed', NOW(), NOW(), NOW())
            RETURNING id;
            """,
            (show_id, batch_name, model, prompt_version, json.dumps(parameters)),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def upsert_entity(
    conn,
    *,
    entity_type: str,
    canonical_name: str,
    platform: str | None,
) -> int:
    normalized = normalize_name(canonical_name)
    platform_value = platform or ""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM ai_entities
            WHERE entity_type = %s
              AND normalized_name = %s
              AND COALESCE(platform, '') = %s
            LIMIT 1;
            """,
            (entity_type, normalized, platform_value),
        )
        row = cur.fetchone()
        if row:
            entity_id = int(row["id"])
            cur.execute(
                """
                UPDATE ai_entities
                SET canonical_name = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (canonical_name, entity_id),
            )
            conn.commit()
            return entity_id

        cur.execute(
            """
            INSERT INTO ai_entities (
              entity_type, canonical_name, normalized_name, platform, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            RETURNING id;
            """,
            (entity_type, canonical_name, normalized, platform if platform else None),
        )
        new_row = cur.fetchone()
    conn.commit()
    return int(new_row["id"])


def insert_mention(
    conn,
    *,
    run_id: int,
    transcript_map: dict[int, int],
    row: dict[str, str],
    entity_id: int,
) -> int:
    episode_id = int(row["episode_id"])
    transcript_id = transcript_map.get(episode_id)

    confidence = float(row["confidence"]) if row["confidence"] else None
    is_editorial = row["is_editorial"].strip().lower() == "true"
    needs_review = row["needs_review"].strip().lower() == "true"
    sentiment = row["sentiment_label"] or "unknown"
    platform = row["platform"].strip() or None
    source_url = row["source_url"].strip() or None
    quoted_text = row["quoted_text"].strip() or None
    context_snippet = row["context_snippet"].strip()
    review_reason = row["review_reason"].strip() or None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_entity_mentions (
              episode_id, transcript_id, entity_id, run_id,
              mention_text, mention_type, mention_count,
              sentiment_label, confidence, needs_review, review_reason,
              is_editorial, context_snippet, quoted_text, source_url, platform, metadata, created_at
            )
            VALUES (
              %s, %s, %s, %s,
              %s, %s, 1,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s, '{}'::jsonb, NOW()
            )
            RETURNING id;
            """,
            (
                episode_id,
                transcript_id,
                entity_id,
                run_id,
                row["mention_text"],
                row["entity_type"],
                sentiment,
                confidence,
                needs_review,
                review_reason,
                is_editorial,
                context_snippet,
                quoted_text,
                source_url,
                platform,
            ),
        )
        mention_row = cur.fetchone()
    conn.commit()
    return int(mention_row["id"])


def insert_review_queue_if_needed(conn, mention_id: int, row: dict[str, str]) -> None:
    needs_review = row["needs_review"].strip().lower() == "true"
    if not needs_review:
        return
    issue_type = row["review_reason"].strip() or "needs_review"
    issue_type = re.sub(r"\s+", "_", issue_type.lower())
    issue_type = re.sub(r"[^a-z0-9_]+", "", issue_type)
    issue_type = issue_type[:64] if issue_type else "needs_review"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_mention_review_queue (
              mention_id, issue_type, status, created_at
            )
            VALUES (%s, %s, 'open', NOW());
            """,
            (mention_id, issue_type),
        )
    conn.commit()


def parse_facts_json(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def insert_facts(
    conn,
    *,
    run_id: int,
    episode_id: int,
    entity_id: int,
    mention_id: int,
    facts: list[dict[str, Any]],
) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for fact in facts:
            key = str(fact.get("fact_key", "")).strip()
            if not key:
                continue
            value = fact.get("fact_value")
            confidence = fact.get("confidence")
            try:
                confidence = float(confidence) if confidence is not None else None
            except (TypeError, ValueError):
                confidence = None

            cur.execute(
                """
                INSERT INTO ai_entity_facts (
                  entity_id, fact_key, fact_value, confidence,
                  source_episode_id, source_mention_id, run_id, created_at
                )
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, NOW());
                """,
                (
                    entity_id,
                    key,
                    json.dumps(value),
                    confidence,
                    episode_id,
                    mention_id,
                    run_id,
                ),
            )
            inserted += 1
    conn.commit()
    return inserted


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)

    batch_dir = Path(args.batch_dir).expanduser().resolve()
    manifest_path = batch_dir / "batch_manifest.json"
    mentions_path = batch_dir / "mentions.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing batch manifest: {manifest_path}")
    if not mentions_path.exists():
        raise FileNotFoundError(f"Missing mentions.csv: {mentions_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch_name = manifest.get("batch_name") or batch_dir.name
    model = manifest.get("model") or "unknown"

    with mentions_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise RuntimeError("mentions.csv is empty")

    episode_ids = sorted({int(r["episode_id"]) for r in rows})

    conn = get_db_connection()
    try:
        show_id = get_show_id(conn, args.show_slug)
        transcript_map = get_transcript_map(conn, episode_ids)
        run_id = insert_run(
            conn,
            show_id=show_id,
            batch_name=batch_name,
            model=model,
            prompt_version=args.prompt_version,
            parameters={
                "batch_dir": str(batch_dir),
                "episodes": episode_ids,
                "source": "extract_entities.py",
                "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )

        mention_inserted = 0
        fact_inserted = 0
        review_inserted = 0
        entity_cache: dict[tuple[str, str, str], int] = {}

        for row in rows:
            entity_type = row["entity_type"].strip()
            canonical_name = row["canonical_name"].strip()
            platform = row["platform"].strip() or None
            key = (entity_type, normalize_name(canonical_name), platform or "")
            entity_id = entity_cache.get(key)
            if entity_id is None:
                entity_id = upsert_entity(
                    conn,
                    entity_type=entity_type,
                    canonical_name=canonical_name,
                    platform=platform,
                )
                entity_cache[key] = entity_id

            mention_id = insert_mention(
                conn,
                run_id=run_id,
                transcript_map=transcript_map,
                row=row,
                entity_id=entity_id,
            )
            mention_inserted += 1

            if row["needs_review"].strip().lower() == "true":
                insert_review_queue_if_needed(conn, mention_id, row)
                review_inserted += 1

            facts = parse_facts_json(row.get("facts_json", ""))
            if facts:
                fact_inserted += insert_facts(
                    conn,
                    run_id=run_id,
                    episode_id=int(row["episode_id"]),
                    entity_id=entity_id,
                    mention_id=mention_id,
                    facts=facts,
                )

        print(f"Loaded batch: {batch_name}")
        print(f"Run ID: {run_id}")
        print(f"Episodes: {len(episode_ids)}")
        print(f"Entities upserted/used: {len(entity_cache)}")
        print(f"Mentions inserted: {mention_inserted}")
        print(f"Facts inserted: {fact_inserted}")
        print(f"Review queue rows inserted: {review_inserted}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

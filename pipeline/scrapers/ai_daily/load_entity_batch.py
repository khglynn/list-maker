#!/usr/bin/env python3
"""
Load extracted entity batch into lean AI Daily schema (ai_runs, ai_entities, ai_mentions).
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


VALID_ENTITY_TYPES = {
    "software_product",
    "model",
    "benchmark",
    "report",
    "survey",
    "paper",
    "account",
    "social_post",
    "blog_post",
    "organization",
    "person",
    "other",
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
        default="extract_entities_v2_lean",
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
    return {int(r["episode_id"]): int(r["id"]) for r in rows}


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
            INSERT INTO ai_runs (
              show_id, batch_name, run_type, provider, model, prompt_version,
              parameters, status, started_at, completed_at, created_at
            )
            VALUES (%s, %s, 'entity_extraction', 'openai', %s, %s, %s::jsonb,
                    'completed', NOW(), NOW(), NOW())
            RETURNING id;
            """,
            (show_id, batch_name, model, prompt_version, json.dumps(parameters)),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def parse_aliases(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = [str(v).strip() for v in raw if str(v).strip()]
    else:
        values = []
    deduped: list[str] = []
    seen = set()
    for v in values:
        key = normalize_name(v)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(v)
    return deduped


def merge_aliases(existing: list[str], additions: list[str]) -> list[str]:
    return parse_aliases([*existing, *additions])


def upsert_entity(
    conn,
    *,
    entity_type: str,
    canonical_name: str,
    platform: str | None,
    source_alias: str | None,
) -> int:
    normalized = normalize_name(canonical_name)
    platform_value = platform or ""

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, canonical_name, aliases
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
            existing_aliases = parse_aliases(row["aliases"])
            additions = [source_alias] if source_alias else []
            if canonical_name != row["canonical_name"]:
                additions.append(row["canonical_name"])
            merged_aliases = merge_aliases(existing_aliases, additions)
            cur.execute(
                """
                UPDATE ai_entities
                SET canonical_name = %s,
                    aliases = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (canonical_name, json.dumps(merged_aliases), entity_id),
            )
            conn.commit()
            return entity_id

        aliases = parse_aliases([source_alias] if source_alias else [])
        cur.execute(
            """
            INSERT INTO ai_entities (
              entity_type, canonical_name, normalized_name, platform,
              aliases, attributes, review_status, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, '{}'::jsonb, 'auto', NOW(), NOW())
            RETURNING id;
            """,
            (entity_type, canonical_name, normalized, platform if platform else None, json.dumps(aliases)),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def parse_facts_json(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    return []


def derive_tags(mention_type: str, platform: str | None, facts: list[dict[str, Any]]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    if platform:
        tags["platform"] = platform.lower()
    if mention_type == "account":
        tags["is_account"] = True
    if mention_type == "survey":
        tags["is_survey"] = True

    for fact in facts:
        key = str(fact.get("fact_key", "")).strip().lower()
        value = fact.get("fact_value")
        if not key:
            continue
        if key in {"modality", "model_modality", "benchmark_domain", "domain", "category"}:
            tags[key] = value
        if key in {"contains_survey_questions", "has_survey_questions"}:
            tags["contains_survey_questions"] = bool(value)
    return tags


def insert_mention(
    conn,
    *,
    run_id: int,
    transcript_map: dict[int, int],
    row: dict[str, str],
    entity_id: int,
) -> None:
    episode_id = int(row["episode_id"])
    transcript_id = transcript_map.get(episode_id)
    entity_type = row["entity_type"].strip().lower()
    if entity_type not in VALID_ENTITY_TYPES:
        entity_type = "other"

    confidence = float(row["confidence"]) if row["confidence"] else None
    is_editorial = row["is_editorial"].strip().lower() == "true"
    needs_review = row["needs_review"].strip().lower() == "true"
    sentiment = (row["sentiment_label"] or "unknown").strip().lower() or "unknown"
    platform = row["platform"].strip() or None
    source_url = row["source_url"].strip() or None
    quoted_text = row["quoted_text"].strip() or None
    context_snippet = row["context_snippet"].strip()
    review_reason = row["review_reason"].strip() or None
    facts = parse_facts_json(row.get("facts_json", ""))
    tags = derive_tags(entity_type, platform, facts)

    link_status = "missing"
    link_confidence = None
    if source_url:
        link_status = "manual_verified"
        link_confidence = 1.0

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_mentions (
              run_id, episode_id, transcript_id, entity_id,
              mention_text, canonical_name, mention_type, mention_count, platform,
              context_snippet, quoted_text, source_url,
              link_status, link_confidence, link_candidates,
              sentiment_label, confidence, is_editorial,
              needs_review, review_reason, review_status,
              facts, tags, created_at, updated_at
            )
            VALUES (
              %s, %s, %s, %s,
              %s, %s, %s, 1, %s,
              %s, %s, %s,
              %s, %s, '[]'::jsonb,
              %s, %s, %s,
              %s, %s, 'open',
              %s::jsonb, %s::jsonb, NOW(), NOW()
            );
            """,
            (
                run_id,
                episode_id,
                transcript_id,
                entity_id,
                row["mention_text"],
                row["canonical_name"],
                entity_type,
                platform,
                context_snippet,
                quoted_text,
                source_url,
                link_status,
                link_confidence,
                sentiment,
                confidence,
                is_editorial,
                needs_review,
                review_reason,
                json.dumps(facts),
                json.dumps(tags),
            ),
        )
    conn.commit()


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
        review_open = 0
        entity_cache: dict[tuple[str, str, str], int] = {}

        for row in rows:
            entity_type = row["entity_type"].strip().lower()
            if entity_type not in VALID_ENTITY_TYPES:
                entity_type = "other"
            canonical_name = row["canonical_name"].strip()
            mention_text = row["mention_text"].strip()
            platform = row["platform"].strip() or None
            key = (entity_type, normalize_name(canonical_name), platform or "")

            entity_id = entity_cache.get(key)
            if entity_id is None:
                entity_id = upsert_entity(
                    conn,
                    entity_type=entity_type,
                    canonical_name=canonical_name,
                    platform=platform,
                    source_alias=mention_text if mention_text != canonical_name else None,
                )
                entity_cache[key] = entity_id

            insert_mention(
                conn,
                run_id=run_id,
                transcript_map=transcript_map,
                row=row,
                entity_id=entity_id,
            )
            mention_inserted += 1
            if row["needs_review"].strip().lower() == "true":
                review_open += 1

        print(f"Loaded batch: {batch_name}")
        print(f"Run ID: {run_id}")
        print(f"Episodes: {len(episode_ids)}")
        print(f"Entities upserted/used: {len(entity_cache)}")
        print(f"Mentions inserted: {mention_inserted}")
        print(f"Mentions needing review: {review_open}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

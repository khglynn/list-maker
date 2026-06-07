#!/usr/bin/env python3
"""Snapshot the current known-good extraction for the tech shows into a frozen
baseline fixture (fixtures/golden_baseline.json).

This is the REGRESSION reference the eval runner scores against: "we were happy with
this output; tell me if a model or prompt change moves it." It reads the extraction
that already lives in Neon (no OpenAI calls — fast and free), collapses each episode's
mentions into per-entity summaries with the SAME normalization production uses, and
freezes the result against explicit episode_ids so the set can't silently drift.

Per the AI-memory primer (provenance + time): the fixture records the model and the
date it was captured, plus a per-episode hash of the exact text fed to extraction, so
the runner can tell when a transcript underneath has changed.

Run it once, commit the fixture, and regenerate only on an intentional re-baseline
(e.g., after deliberately improving the prompt and confirming the new output is good):

    ./pipeline/venv/bin/python evals/extraction/build_baseline.py --per-show 15
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from evals.extraction.metrics import collapse_to_entities  # noqa: E402
from pipeline.common import get_db_connection, load_environment  # noqa: E402

DEFAULT_SHOWS = "ai-daily-brief,hard-fork"
DEFAULT_MAX_CHARS = 50000  # matches extract_entities.py's default truncation
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the frozen extraction baseline fixture")
    p.add_argument("--shows", default=DEFAULT_SHOWS, help="Comma-separated show slugs (tech only)")
    p.add_argument("--per-show", type=int, default=15, help="Episodes per show (most recent with extraction)")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="Transcript truncation (match the extractor)")
    p.add_argument("--out", default=str(FIXTURES_DIR / "golden_baseline.json"), help="Output fixture path")
    return p.parse_args()


def input_hash(transcript_text: str, max_chars: int) -> tuple[str, int]:
    """Hash the EXACT text the extractor sees (truncated), so drift detection matches
    what actually changes the output — not edits past the truncation point."""
    truncated = (transcript_text or "")[:max_chars]
    return hashlib.sha256(truncated.encode("utf-8")).hexdigest(), len(truncated)


def fetch_show_id(conn, slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM shows WHERE slug = %s", (slug,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"Unknown show slug: {slug}")
    return row["id"]


def fetch_recent_extracted_episodes(conn, show_id: int, limit: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.id, ep.title, ep.publish_date, s.slug AS show_slug,
                   COALESCE(et.transcript_text, ep.description_body) AS transcript_text
            FROM episodes ep
            JOIN shows s ON s.id = ep.show_id
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.show_id = %s
              AND COALESCE(et.transcript_text, ep.description_body) IS NOT NULL
              AND EXISTS (SELECT 1 FROM ai_mentions m WHERE m.episode_id = ep.id)
            ORDER BY ep.publish_date DESC NULLS LAST, ep.id DESC
            LIMIT %s
            """,
            (show_id, limit),
        )
        return list(cur.fetchall())


def fetch_episode_mentions(conn, episode_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_name, mention_type AS entity_type, confidence
            FROM ai_mentions
            WHERE episode_id = %s
            """,
            (episode_id,),
        )
        rows = cur.fetchall()
    # confidence is NUMERIC -> Decimal; collapse_to_entities coerces via float().
    return [
        {
            "canonical_name": r["canonical_name"],
            "entity_type": r["entity_type"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
        }
        for r in rows
    ]


def dominant_model(conn, episode_ids: list[int]) -> str:
    if not episode_ids:
        return "unknown"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.model, COUNT(*) AS c
            FROM ai_mentions m JOIN ai_runs r ON r.id = m.run_id
            WHERE m.episode_id = ANY(%s)
            GROUP BY r.model ORDER BY c DESC NULLS LAST
            """,
            (episode_ids,),
        )
        rows = cur.fetchall()
    return rows[0]["model"] if rows and rows[0]["model"] else "unknown"


def main() -> None:
    args = parse_args()
    load_environment(REPO_ROOT)
    conn = get_db_connection()

    shows = [s.strip() for s in args.shows.split(",") if s.strip()]
    episodes_out: list[dict] = []
    all_ids: list[int] = []
    try:
        for slug in shows:
            show_id = fetch_show_id(conn, slug)
            rows = fetch_recent_extracted_episodes(conn, show_id, args.per_show)
            print(f"{slug}: {len(rows)} episodes", flush=True)
            for row in rows:
                mentions = fetch_episode_mentions(conn, row["id"])
                entities = collapse_to_entities(mentions)
                sha, n_chars = input_hash(row["transcript_text"], args.max_chars)
                episodes_out.append(
                    {
                        "episode_id": row["id"],
                        "show_slug": row["show_slug"],
                        "title": row["title"],
                        "publish_date": str(row["publish_date"]) if row["publish_date"] else None,
                        "input_sha256": sha,
                        "input_chars": n_chars,
                        "max_chars": args.max_chars,
                        "n_entities": len(entities),
                        "entities": entities,
                    }
                )
                all_ids.append(row["id"])

        model = dominant_model(conn, all_ids)
    finally:
        conn.close()

    type_counts = Counter()
    for ep in episodes_out:
        for ent in ep["entities"].values():
            type_counts[ent["entity_type"]] += 1

    fixture = {
        "_meta": {
            "kind": "golden_baseline",
            "description": (
                "Frozen known-good extraction for the tech shows. The eval runner re-extracts "
                "these episodes with the current model/prompt and reports drift vs this snapshot. "
                "Regenerate only on an intentional re-baseline."
            ),
            "extraction_type": "entity_extraction",
            "baseline_model": model,
            "shows": shows,
            "per_show": args.per_show,
            "max_chars": args.max_chars,
            "n_episodes": len(episodes_out),
            "n_entities": sum(ep["n_entities"] for ep in episodes_out),
            "type_counts": dict(sorted(type_counts.items(), key=lambda x: (-x[1], x[0]))),
        },
        "episodes": episodes_out,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(
        f"\nWrote {out_path} — {len(episodes_out)} episodes, "
        f"{fixture['_meta']['n_entities']} entities, model={model}",
        flush=True,
    )


if __name__ == "__main__":
    main()

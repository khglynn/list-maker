#!/usr/bin/env python3
"""Reclassify already-stored mentions as sponsor reads. Never deletes anything.

WHY. Until 2026-09-02 extraction DROPPED every mention the model flagged as an ad, so
the ads that reached the database are exactly the ones the model MISSED — all sitting at
is_editorial = true, counted at full weight. Blitzy carries 76 of them. New extractions
tag ads as they arrive; this script is the one-time pass over the backlog, and the only
way to run the same detector over history that runs over new episodes.

WHAT IT CHANGES. Only two columns, only in one direction:
    is_editorial  true  -> false
    sponsor_source NULL -> 'roster' | 'phrase' | 'model'
No row is deleted, no mention is re-worded, no entity is merged. A mention already
tagged is left alone unless its verdict changed (the detector is deterministic, so that
only happens after a detector change — which is exactly when you want to see the diff).

HOW TO USE IT. `--dry-run` (the default) writes the full would-change list to
pipeline/_cache/retag-sponsors-<date>.json and prints a summary grouped by entity.
`--apply` performs the update inside ONE transaction, so a failure halfway leaves the
table exactly as it was.

    ./venv/bin/python scrapers/ai_daily/retag_sponsor_mentions.py --dry-run
    ./venv/bin/python scrapers/ai_daily/retag_sponsor_mentions.py --apply

PREREQUISITE: sql/009_sponsor_provenance.sql must have been run (Kevin's paste). The
script checks and stops with a clear message rather than failing mid-update.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parents[2]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from common import get_db_connection, get_logger, load_environment  # noqa: E402
from show_config import SHOWS  # noqa: E402

try:  # same dual-entry-point dance as extract_entities.py
    from .sponsors import (
        classify_sponsor,
        normalize_text_for_matching,
        roster_from_raw_content,
        sponsor_windows,
    )
except ImportError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sponsors import (  # type: ignore[no-redef]
        classify_sponsor,
        normalize_text_for_matching,
        roster_from_raw_content,
        sponsor_windows,
    )

log = get_logger("pipeline.retag_sponsor_mentions")

CACHE_DIR = PIPELINE_DIR / "_cache"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reclassify stored mentions as sponsor reads (never deletes)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and write the JSON report (default).",
    )
    mode.add_argument(
        "--apply", action="store_true", help="Write the changes, in one transaction."
    )
    parser.add_argument(
        "--shows",
        default="",
        help="Comma-separated show slugs. Default: every podcast with an entity or "
        "media extraction pipeline.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Override the report path (default: _cache/retag-sponsors-<date>.json).",
    )
    return parser.parse_args()


def default_show_slugs() -> list[str]:
    return sorted(
        slug
        for slug, cfg in SHOWS.items()
        if cfg.medium == "podcast"
        and cfg.extraction_type in {"entity_extraction", "media_extraction"}
    )


def sponsor_source_column_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'ai_mentions' AND column_name = 'sponsor_source';
            """
        )
        return cur.fetchone() is not None


def fetch_mentions(
    conn, show_slugs: list[str], has_sponsor_source: bool = True
) -> list[dict[str, Any]]:
    """Every mention for these shows, with the episode text needed to re-derive a
    verdict. The transcript is joined via ai_mentions.transcript_id when the extractor
    read one and falls back to the episode's show-notes body otherwise — the same
    COALESCE prepare_extraction_inputs used, so the retag sees what extraction saw.

    `has_sponsor_source` exists so a DRY RUN works BEFORE sql/009 has been run: that is
    the order Kevin actually needs (preview the change, then decide to migrate), and
    selecting a column that does not exist yet would make the preview impossible.
    Without the column every stored row is untagged, which is exactly what NULL means.
    """
    sponsor_column = "m.sponsor_source" if has_sponsor_source else "NULL::text AS sponsor_source"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.id, m.episode_id, m.entity_id, m.canonical_name, m.mention_text,
                   m.context_snippet, m.is_editorial, {sponsor_column},
                   s.slug AS show_slug, ep.publish_date, ep.title AS episode_title,
                   ep.raw_content,
                   COALESCE(et.transcript_text, ep.description_body) AS source_text
            FROM ai_mentions m
            JOIN episodes ep ON ep.id = m.episode_id
            JOIN shows s ON s.id = ep.show_id
            LEFT JOIN episode_transcripts et ON et.id = m.transcript_id
            WHERE s.slug = ANY(%s)
            ORDER BY ep.publish_date, m.id;
            """,
            (show_slugs,),
        )
        return [dict(r) for r in cur.fetchall()]


def original_model_flag(row: dict[str, Any]) -> bool:
    """Recover the EXTRACTOR's is_editorial, not the value a previous retag wrote.

    classify_sponsor treats is_editorial=False as the weakest evidence ('model'). On a
    re-run that would be circular: a row this script already tagged has
    is_editorial=False *because of this script*, so feeding it back in makes every
    tagged row permanently re-confirm itself and no verdict can ever be reversed —
    which is exactly what a detector fix needs to be able to do.

    The one case where the stored False IS the model's own opinion is a row whose
    sponsor_source is 'model': that verdict came from the flag in the first place.
    Everything else previously tagged reports "the model said editorial", so the roster
    and phrase rules have to justify the tag again on their own.
    """
    source = row.get("sponsor_source")
    if source is None:
        return bool(row.get("is_editorial", True))
    return source != "model"


def plan_changes(mentions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Re-classify every mention and return the rows whose verdict differs from what is
    stored, plus counters. Pure apart from its input, so the tests can drive it with
    fixture rows and no database.

    Episode-level work (parsing the roster, normalizing a 50k-character transcript,
    scanning for cue phrases) is done once per episode, not once per mention.
    """
    stats = {
        "examined": 0,
        "already_tagged": 0,
        "would_tag": 0,
        "would_untag": 0,
        "unchanged": 0,
        "episodes_with_roster": 0,
    }
    episode_cache: dict[int, tuple[list, str, list]] = {}
    changes: list[dict[str, Any]] = []

    for row in mentions:
        stats["examined"] += 1
        episode_id = row["episode_id"]
        if episode_id not in episode_cache:
            roster = roster_from_raw_content(row.get("raw_content"))
            normalized = normalize_text_for_matching(row.get("source_text") or "")
            episode_cache[episode_id] = (
                roster,
                normalized,
                sponsor_windows(row.get("source_text")),
            )
            if roster:
                stats["episodes_with_roster"] += 1
        roster, normalized, windows = episode_cache[episode_id]

        stored_source = row.get("sponsor_source")
        stored_is_ad = stored_source is not None
        verdict = classify_sponsor(
            {**row, "is_editorial": original_model_flag(row)},
            roster,
            windows,
            normalized_transcript=normalized,
        )

        if verdict.is_sponsor and stored_source == verdict.source:
            stats["already_tagged"] += 1
            continue
        if not verdict.is_sponsor and not stored_is_ad:
            stats["unchanged"] += 1
            continue

        if verdict.is_sponsor:
            stats["would_tag"] += 1
        else:
            # Only reachable when a previous retag tagged a row that the current
            # detector no longer considers an ad. Surfaced rather than skipped: a
            # detector change that un-tags rows is exactly what a review should see.
            stats["would_untag"] += 1
        changes.append(
            {
                "mention_id": row["id"],
                "entity_id": row["entity_id"],
                "canonical_name": row["canonical_name"],
                "show_slug": row["show_slug"],
                "episode_id": episode_id,
                "episode_title": row.get("episode_title"),
                "publish_date": str(row["publish_date"]) if row.get("publish_date") else None,
                "from": {"is_editorial": row["is_editorial"], "sponsor_source": stored_source},
                "to": {
                    "is_editorial": not verdict.is_sponsor,
                    "sponsor_source": verdict.source,
                },
                "matched": verdict.matched,
                "context_snippet": (row.get("context_snippet") or "")[:300],
            }
        )
    return changes, stats


def summarize_by_entity(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the change list by entity — the unit Kevin reviews in. A per-mention list
    of 900 rows is unreadable; "Blitzy, 76 mentions, all roster" is a decision."""
    grouped: dict[tuple[Any, str], dict[str, Any]] = {}
    for change in changes:
        key = (change["entity_id"], change["canonical_name"])
        entry = grouped.setdefault(
            key,
            {
                "entity_id": change["entity_id"],
                "canonical_name": change["canonical_name"],
                "count": 0,
                "sources": defaultdict(int),
                "shows": set(),
                "first_date": None,
                "last_date": None,
            },
        )
        entry["count"] += 1
        entry["sources"][change["to"]["sponsor_source"] or "untag"] += 1
        entry["shows"].add(change["show_slug"])
        pd = change["publish_date"]
        if pd:
            entry["first_date"] = min(entry["first_date"] or pd, pd)
            entry["last_date"] = max(entry["last_date"] or pd, pd)
    rows = []
    for entry in grouped.values():
        rows.append(
            {
                **entry,
                "sources": dict(entry["sources"]),
                "shows": sorted(entry["shows"]),
            }
        )
    rows.sort(key=lambda r: (-r["count"], r["canonical_name"].lower()))
    return rows


def apply_changes(conn, changes: list[dict[str, Any]]) -> tuple[int, int]:
    """Write every change in ONE transaction — all or nothing. Returns (mentions, entities).

    A partial retag is the worst outcome available here: the rollup would show some of
    an entity's ads capped and the rest not, and there would be no way to tell how far
    the run got. psycopg2 opens a transaction implicitly, so the single commit at the
    end is the boundary; any exception propagates with nothing committed.

    IT ALSO TOUCHES ai_entities.updated_at, and that is not incidental. Notion's
    incremental sync decides what to re-push from sync_notion.compute_diff, which
    re-sends an entity only when its own row changed (updated_at > notion_synced_at) or
    a newer episode arrived. A retag moves NEITHER — it edits ai_mentions — so without
    this the capped Mentions count and the new Sponsor / Ad mentions columns would sit
    in Neon and never reach the page. Worst for exactly the entities this is for: a
    lapsed sponsor gets no new mentions to carry the change for it, so its page would
    stay wrong indefinitely. Bumping the entity row inside the same transaction makes
    the next scheduled sync republish it.

    updated_at is safe to use this way: load_entity_batch writes it on upsert and
    compute_diff is its only reader, so it means "something about this entity changed"
    and nothing else.
    """
    if not changes:
        return 0, 0
    # entity_id is nullable — ai_mentions.entity_id is ON DELETE SET NULL.
    entity_ids = sorted({c["entity_id"] for c in changes if c.get("entity_id") is not None})
    with conn.cursor() as cur:
        for change in changes:
            cur.execute(
                """
                UPDATE ai_mentions
                SET is_editorial = %s,
                    sponsor_source = %s,
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (change["to"]["is_editorial"], change["to"]["sponsor_source"], change["mention_id"]),
            )
        if entity_ids:
            cur.execute(
                "UPDATE ai_entities SET updated_at = NOW() WHERE id = ANY(%s);",
                (entity_ids,),
            )
    conn.commit()
    return len(changes), len(entity_ids)


def render_summary(stats: dict[str, int], by_entity: list[dict[str, Any]], limit: int = 40) -> str:
    lines = [
        f"Examined {stats['examined']} mention(s) across "
        f"{stats['episodes_with_roster']} episode(s) with a parsed sponsor roster.",
        f"  would tag as ads : {stats['would_tag']}",
        f"  would un-tag     : {stats['would_untag']}",
        f"  already tagged   : {stats['already_tagged']}",
        f"  unchanged        : {stats['unchanged']}",
        "",
        f"Entities affected: {len(by_entity)}",
        "",
        f"{'mentions':>9}  {'sources':<28} entity",
    ]
    for row in by_entity[:limit]:
        sources = ", ".join(f"{k}={v}" for k, v in sorted(row["sources"].items()))
        lines.append(f"{row['count']:>9}  {sources:<28} {row['canonical_name']}")
    if len(by_entity) > limit:
        lines.append(f"  … and {len(by_entity) - limit} more (full list in the report)")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    load_environment()
    apply = args.apply  # dry-run is the default; --apply is the only way to write

    show_slugs = (
        [s.strip() for s in args.shows.split(",") if s.strip()]
        if args.shows.strip()
        else default_show_slugs()
    )
    print(f"Shows: {', '.join(show_slugs)}")
    print(f"Mode: {'APPLY (writes)' if apply else 'DRY RUN (no writes)'}")

    conn = get_db_connection()
    try:
        # A dry run works before the migration on purpose — previewing the change is
        # how you decide whether to run it. Only --apply needs the column to exist.
        has_column = sponsor_source_column_exists(conn)
        if not has_column:
            if apply:
                print(
                    "\nERROR: ai_mentions.sponsor_source does not exist.\n"
                    "Run pipeline/scrapers/ai_daily/sql/009_sponsor_provenance.sql first "
                    "(DDL is Kevin's paste), then re-run with --apply.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(
                "NOTE: ai_mentions.sponsor_source does not exist yet (sql/009 not run). "
                "Every stored mention counts as untagged, which is what the dry run wants."
            )

        mentions = fetch_mentions(conn, show_slugs, has_sponsor_source=has_column)
        print(f"Loaded {len(mentions)} mention(s).")
        changes, stats = plan_changes(mentions)
        by_entity = summarize_by_entity(changes)

        print()
        print(render_summary(stats, by_entity))

        report_path = (
            Path(args.report_path).expanduser()
            if args.report_path
            else CACHE_DIR / f"retag-sponsors-{date.today().isoformat()}.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "mode": "apply" if apply else "dry-run",
                    "shows": show_slugs,
                    "stats": stats,
                    "by_entity": by_entity,
                    "changes": changes,
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nReport: {report_path}")

        if not apply:
            print("\nDry run — nothing was written. Re-run with --apply to commit.")
            return

        written, touched = apply_changes(conn, changes)
        log.info(
            "retagged %d mention(s) as sponsor reads across %d entity/entities",
            written, touched,
        )
        print(f"\nApplied: {written} mention(s) updated. No rows deleted.")
        print(
            f"Touched {touched} entity row(s) so the next Notion sync republishes them "
            f"with the capped Mentions count and the Sponsor / Ad mentions columns."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

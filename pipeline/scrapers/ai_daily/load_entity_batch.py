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
    # Tech taxonomy (AI Daily, Hard Fork) — mirrors extract_entities.LOCKED_TYPES.
    # tests/test_load_entity_batch.py guards both halves against drift.
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
    # Media taxonomy (PCHH, Culture Gabfest) — mirrors extract_entities.MEDIA_TYPES.
    "movie",
    "tv_series",
    "book",
    "music_album",
    "music_track",
    "game",
    "podcast_series",
    "theater_production",
    "social_account",
    "artist_profile",
    "visual_media_other",
}


def get_db_connection():
    # Shared implementation (connect timeout + bounded retry) — see pipeline/common.py.
    # Lazy path insert + import so this file still runs as a script from pipeline/ AND
    # imports cleanly as pipeline.scrapers.ai_daily.load_entity_batch under pytest.
    pipeline_dir = str(Path(__file__).resolve().parents[2])
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from common import get_db_connection as shared_connection

    return shared_connection()


def normalize_name(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[\s\-_]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_entity_type(raw: str) -> str:
    """Normalize a CSV entity_type to a known value, else fall back to 'other'."""
    value = (raw or "").strip().lower()
    return value if value in VALID_ENTITY_TYPES else "other"


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
    parser.add_argument(
        "--provenance-json",
        help="Path to the extraction provenance map written by run_new_episodes."
        " Records which transcript (if any) was actually extracted, per episode."
        " Omit only for a hand-run batch, where provenance falls back to a load-time lookup.",
    )
    return parser.parse_args()


def get_show_id(conn, show_slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM shows WHERE slug = %s LIMIT 1;", (show_slug,))
        row = cur.fetchone()
        if not row:
            # exit 2 = deterministic; the orchestrator must not retry it (see
            # run_new_episodes.DETERMINISTIC_EXIT_CODE). Inline rather than a
            # `except RuntimeError` handler in __main__, because RuntimeError is ALSO
            # how finalize_run_completed reports a row-count anomaly from inside the
            # batch transaction — and that one must keep its retry, since the retry
            # deleting and replacing the 'loading' row is the whole point of the
            # transactional load. A type-based handler would silently take that away.
            print(f"Show slug not found: {show_slug}", file=sys.stderr)
            sys.exit(2)
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


def read_provenance(path: str | None) -> dict[int, int | None] | None:
    """Load the extraction provenance map, if one was passed. Keys are episode ids;
    a null value means the extractor read show notes, not a transcript."""
    if not path:
        return None
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return {int(k): (int(v) if v is not None else None) for k, v in raw.items()}


def resolve_transcript_map(
    conn, episode_ids: list[int], provenance: dict[int, int | None] | None
) -> tuple[dict[int, int | None], list[int]]:
    """Decide each mention's transcript_id. Returns (map, episodes_inferred).

    Recorded provenance wins wherever it exists, including an explicit null — that null
    is the whole point. Asking the database "does this episode have a transcript?" at load
    time answers a different question: extraction runs for minutes, and a transcript that
    lands mid-batch would otherwise be stamped onto mentions mined from show notes. Those
    mentions would then look transcript-derived forever, and the self-heal check in
    run_new_episodes would never revisit the episode.

    Episodes with no recorded provenance (a hand-run batch, or one extracted before this
    field existed) fall back to the load-time lookup and are named in the return value so
    the caller can say the provenance was inferred rather than observed.
    """
    inferred = [eid for eid in episode_ids if provenance is None or eid not in provenance]
    resolved: dict[int, int | None] = {}
    if inferred:
        lookup = get_transcript_map(conn, inferred)
        resolved.update({eid: lookup.get(eid) for eid in inferred})
    if provenance:
        resolved.update({eid: provenance[eid] for eid in episode_ids if eid in provenance})
    return resolved, inferred


LOADING_RUN_STATUS = "loading"


def insert_run(
    conn,
    *,
    show_id: int,
    batch_name: str,
    model: str,
    prompt_version: str,
    parameters: dict[str, Any],
    status: str = "completed",
    commit: bool = True,
) -> int:
    """Write the ai_runs row for one batch.

    `completed_at` used to be a hardcoded NOW() for every status, which was harmless
    while every run was born 'completed' — but a LOADING_RUN_STATUS row has not
    completed, and a timestamp saying otherwise is exactly the kind of plausible-but-
    false value docs/principles.md says to write NULL for instead. It stays on the
    DATABASE clock (a CASE over NOW(), not a Python datetime) so every existing caller
    — record_empty_batch above all — keeps writing the identical value it writes today.

    `commit=False` lets the caller make the whole batch one transaction; see main().
    """
    has_completed = status != LOADING_RUN_STATUS
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_runs (
              show_id, batch_name, run_type, provider, model, prompt_version,
              parameters, status, started_at, completed_at, created_at
            )
            VALUES (%s, %s, 'entity_extraction', 'openai', %s, %s, %s::jsonb,
                    %s, NOW(), CASE WHEN %s THEN NOW() END, NOW())
            RETURNING id;
            """,
            (show_id, batch_name, model, prompt_version, json.dumps(parameters),
             status, has_completed),
        )
        row = cur.fetchone()
    if commit:
        conn.commit()
    return int(row["id"])


def finalize_run_completed(conn, run_id: int, *, commit: bool = True) -> None:
    """Flip a 'loading' run to 'completed' once every entity and mention has landed.

    This is the LAST statement of the batch transaction — its commit is what makes the
    entities, the mentions, and the run's completed status appear atomically together.
    A row count other than 1 means the run row vanished under us, which would leave a
    batch of mentions attached to nothing; raising rolls the whole batch back rather
    than reporting a success nobody can trace.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_runs
            SET status = 'completed', completed_at = NOW()
            WHERE id = %s;
            """,
            (run_id,),
        )
        updated = cur.rowcount
    if updated != 1:
        raise RuntimeError(
            f"finalize_run_completed matched {updated} rows for run {run_id} (expected 1)"
        )
    if commit:
        conn.commit()


EMPTY_RUN_STATUS = "completed_empty"


def record_empty_batch(
    conn,
    *,
    show_id: int,
    batch_name: str,
    model: str,
    prompt_version: str,
    manifest: dict[str, Any],
    batch_dir: Path,
) -> tuple[int, list[int]]:
    """Record a batch whose extraction kept zero mentions as a DECLARED outcome.

    Why a run row instead of an error: on 2026-08-23 the model returned ~5,000 tokens
    of candidates for episode 8429 and the editorial / core-type filters removed every
    one. The loader raised on the empty file, the orchestrator retried a deterministic
    result three times, the day went red, and the next day's re-extraction "succeeded"
    by storing two sponsor reads as editorial mentions. An empty result with its
    reasons on record is what lets the orchestrator stop re-queuing the episode and
    lets data_health tell "nothing worth storing" from "extraction broken".

    Status is EMPTY_RUN_STATUS, not 'completed', so the integrity check's existing
    "completed runs with zero mentions" term keeps meaning what it meant: a run that
    claimed success and loaded nothing.
    """
    episodes = sorted(
        {int(e["episode_id"]) for e in manifest.get("episodes", []) if e.get("episode_id") is not None}
    )
    filter_summary = manifest.get("filter_summary") or {}
    removed = delete_existing_run(conn, show_id=show_id, batch_name=batch_name)
    if removed:
        print(f"Idempotent re-load: removed {removed} prior run(s) for batch '{batch_name}'.")
    run_id = insert_run(
        conn,
        show_id=show_id,
        batch_name=batch_name,
        model=model,
        prompt_version=prompt_version,
        status=EMPTY_RUN_STATUS,
        parameters={
            "batch_dir": str(batch_dir),
            "episodes": episodes,
            "source": "extract_entities.py",
            "empty_result": True,
            "raw_mention_count": filter_summary.get("raw"),
            "dropped": {
                "non_editorial": filter_summary.get("non_editorial_dropped"),
                "non_core_type": filter_summary.get("non_core_type_dropped"),
                "invalid": filter_summary.get("sanitize_dropped"),
            },
            "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return run_id, episodes


def delete_existing_run(
    conn, *, show_id: int, batch_name: str, commit: bool = True
) -> int:
    """Make batch (re)loads idempotent: remove any prior run — and its mentions —
    for this (show_id, batch_name) before inserting a fresh one. A re-load thus
    replaces rather than duplicates, and a partially-loaded run self-heals on the
    next run. Scoped + via psycopg2 (not the Neon MCP, so the destructive-op guard
    correctly doesn't gate it). No-op on first load (no matching run).
    """
    with conn.cursor() as cur:
        # ai_mentions.run_id is ON DELETE CASCADE, so deleting the runs alone
        # would clear these too — the explicit delete makes the intent obvious.
        cur.execute(
            """
            DELETE FROM ai_mentions
            WHERE run_id IN (
                SELECT id FROM ai_runs WHERE show_id = %s AND batch_name = %s
            );
            """,
            (show_id, batch_name),
        )
        cur.execute(
            "DELETE FROM ai_runs WHERE show_id = %s AND batch_name = %s;",
            (show_id, batch_name),
        )
        removed_runs = cur.rowcount
    if commit:
        conn.commit()
    return removed_runs


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
    commit: bool = True,
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
            if commit:
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
    if commit:
        conn.commit()
    return int(row["id"])


def record_first_seen_as_ad(
    conn, entity_id: int, publish_date: Any, *, commit: bool = True
) -> bool:
    """Stamp attributes.first_seen_as_ad when an entity's EARLIEST mention is an ad.

    Why this is worth a column: "we only know about this product because someone paid to
    tell us" is a different provenance from "the hosts brought it up", and it is exactly
    the thing that disappears once the entity accumulates later editorial mentions. It
    is written only when absent, and only when the ad is at or before every other
    mention of that entity — so a sponsor read that follows real coverage does not
    rewrite the entity's origin story.

    Returns True if it wrote. Idempotent: a re-load of the same batch is a no-op because
    the key already exists.
    """
    if publish_date is None:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_entities e
            SET attributes = jsonb_set(
                    COALESCE(e.attributes, '{}'::jsonb),
                    '{first_seen_as_ad}', to_jsonb(%s::text), true
                ),
                updated_at = NOW()
            WHERE e.id = %s
              AND NOT (COALESCE(e.attributes, '{}'::jsonb) ? 'first_seen_as_ad')
              AND NOT EXISTS (
                  SELECT 1 FROM ai_mentions m
                  JOIN episodes ep ON ep.id = m.episode_id
                  WHERE m.entity_id = e.id AND ep.publish_date < %s::date
              );
            """,
            (str(publish_date), entity_id, str(publish_date)),
        )
        wrote = cur.rowcount > 0
    if commit:
        conn.commit()
    return wrote


def get_episode_publish_dates(conn, episode_ids: list[int]) -> dict[int, Any]:
    """publish_date per episode — needed to decide whether an ad is an entity's first
    sighting. One query for the batch rather than one per mention."""
    if not episode_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, publish_date FROM episodes WHERE id = ANY(%s);", (episode_ids,)
        )
        return {int(r["id"]): r["publish_date"] for r in cur.fetchall()}


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


VALID_SPONSOR_SOURCES = {"roster", "phrase", "model"}


def normalize_sponsor_source(raw: str | None) -> str | None:
    """CSV cell -> ai_mentions.sponsor_source, or None for an editorial mention.

    None becomes SQL NULL: "no sponsor evidence" is an absence, not a category, and
    sql/009's CHECK constraint only admits the three real values. An unrecognized
    string is dropped to NULL rather than smuggled past the constraint and failing the
    whole batch on one bad cell.
    """
    value = (raw or "").strip().lower()
    return value if value in VALID_SPONSOR_SOURCES else None


def insert_mention(
    conn,
    *,
    run_id: int,
    transcript_map: dict[int, int | None],
    row: dict[str, str],
    entity_id: int,
    commit: bool = True,
) -> None:
    episode_id = int(row["episode_id"])
    transcript_id = transcript_map.get(episode_id)
    entity_type = normalize_entity_type(row["entity_type"])

    # An empty cell is an UNKNOWN confidence, and it stays unknown: SQL NULL, never a
    # default. Since 2026-09-03 the extractor writes an empty cell whenever the model
    # omitted or mangled the field, instead of fabricating 0.5 — a number nobody could
    # tell apart from a model that really said 0.5. This line was written defensively
    # before any caller could produce an empty cell; it is now the live path.
    confidence = float(row["confidence"]) if row["confidence"] else None
    is_editorial = row["is_editorial"].strip().lower() == "true"
    sponsor_source = normalize_sponsor_source(row.get("sponsor_source"))
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
              sentiment_label, confidence, is_editorial, sponsor_source,
              needs_review, review_reason, review_status,
              facts, tags, created_at, updated_at
            )
            VALUES (
              %s, %s, %s, %s,
              %s, %s, %s, 1, %s,
              %s, %s, %s,
              %s, %s, '[]'::jsonb,
              %s, %s, %s, %s,
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
                sponsor_source,
                needs_review,
                review_reason,
                json.dumps(facts),
                json.dumps(tags),
            ),
        )
    if commit:
        conn.commit()


def load_batch_rows(
    conn,
    *,
    run_id: int,
    rows: list[dict[str, str]],
    transcript_map: dict[int, int | None],
    publish_dates: dict[int, Any],
) -> tuple[int, int, int, int, dict[tuple[str, str, str], int]]:
    """Insert every entity and mention for one batch. NEVER commits.

    The caller commits exactly once, together with the run's flip to 'completed'
    (finalize_run_completed) — that single commit is the whole fix. Before it, each
    helper committed on its own, so a process killed mid-loop left a run already
    marked 'completed' next to some fraction of its mentions, and nothing downstream
    could tell that from a healthy batch: find_unextracted_episodes decides "already
    extracted" on the presence of mentions alone, so whichever episodes happened to
    land first were never retried.

    Returns (mention_inserted, review_open, sponsor_inserted, first_seen_as_ad,
    entity_cache).
    """
    mention_inserted = 0
    review_open = 0
    sponsor_inserted = 0
    first_seen_as_ad = 0
    entity_cache: dict[tuple[str, str, str], int] = {}
    # (entity_id, publish_date) per ad mention, stamped after the batch lands.
    sponsor_stamps: list[tuple[int, Any]] = []

    for row in rows:
        entity_type = normalize_entity_type(row["entity_type"])
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
                commit=False,
            )
            entity_cache[key] = entity_id

        insert_mention(
            conn,
            run_id=run_id,
            transcript_map=transcript_map,
            row=row,
            entity_id=entity_id,
            commit=False,
        )
        mention_inserted += 1
        if row["needs_review"].strip().lower() == "true":
            review_open += 1
        if normalize_sponsor_source(row.get("sponsor_source")):
            sponsor_inserted += 1
            sponsor_stamps.append(
                (entity_id, publish_dates.get(int(row["episode_id"])))
            )

    # Stamp first_seen_as_ad only once the WHOLE batch has landed. The guard inside
    # record_first_seen_as_ad asks "does an earlier mention of this entity exist?",
    # and mentions.csv arrives in episode order — which for a multi-episode catch-up
    # is newest-first, because Taddy inserts newest-first and the newer episode gets
    # the smaller id. Stamping inline therefore let an ad in the NEWER episode claim
    # "first seen" before the older episode's editorial mention had been inserted,
    # writing a date that is real but wrong. A second pass sees every row — including,
    # now that nothing commits mid-batch, the batch's own uncommitted mentions, which
    # are visible to this same transaction exactly as the committed ones used to be.
    for entity_id, publish_date in sponsor_stamps:
        if record_first_seen_as_ad(conn, entity_id, publish_date, commit=False):
            first_seen_as_ad += 1

    return mention_inserted, review_open, sponsor_inserted, first_seen_as_ad, entity_cache


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)

    batch_dir = Path(args.batch_dir).expanduser().resolve()
    manifest_path = batch_dir / "batch_manifest.json"
    mentions_path = batch_dir / "mentions.csv"
    # Both of these, and the unknown-slug refusal below, run BEFORE get_db_connection()
    # — which is what makes it safe for __main__ to map FileNotFoundError to exit 2
    # (deterministic, not retried). Nothing inside the transaction can raise it.
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing batch manifest: {manifest_path}")
    if not mentions_path.exists():
        raise FileNotFoundError(f"Missing mentions.csv: {mentions_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch_name = manifest.get("batch_name") or batch_dir.name
    model = manifest.get("model") or "unknown"

    with mentions_path.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    provenance = read_provenance(args.provenance_json)

    conn = get_db_connection()
    try:
        show_id = get_show_id(conn, args.show_slug)
        if not rows:
            # Nothing kept is an outcome, not a failure — record it (see record_empty_batch).
            run_id, episodes = record_empty_batch(
                conn,
                show_id=show_id,
                batch_name=batch_name,
                model=model,
                prompt_version=args.prompt_version,
                manifest=manifest,
                batch_dir=batch_dir,
            )
            fs = manifest.get("filter_summary") or {}
            print(f"Loaded batch: {batch_name}")
            print(f"Run ID: {run_id}")
            print(
                f"Declared EMPTY: 0 mentions kept of {fs.get('raw', '?')} candidate(s) — "
                f"dropped non-editorial {fs.get('non_editorial_dropped', '?')}, "
                f"non-core type {fs.get('non_core_type_dropped', '?')}, "
                f"invalid {fs.get('sanitize_dropped', '?')}. "
                f"Episodes {episodes} will not be re-queued."
            )
            return

        episode_ids = sorted({int(r["episode_id"]) for r in rows})
        transcript_map, inferred = resolve_transcript_map(conn, episode_ids, provenance)
        if inferred:
            print(
                f"WARNING: no recorded extraction provenance for episode(s) {inferred} — "
                "falling back to a load-time transcript lookup, which cannot tell whether "
                "the extractor actually read that transcript."
            )
        # The delete rides the 'loading' insert's commit: "replace" is then atomic at
        # the row-existence level, so a crash between them can never leave the batch
        # with its old run deleted and no new row at all — a state with nothing for the
        # health check to see.
        removed_runs = delete_existing_run(
            conn, show_id=show_id, batch_name=batch_name, commit=False
        )
        if removed_runs:
            print(
                f"Idempotent re-load: removed {removed_runs} prior run(s) "
                f"for batch '{batch_name}' before reloading."
            )
        # The run is born 'loading', with its own commit, so the row is visible in Neon
        # while the batch is in flight — that is what data_health's ai_run_stuck_loading
        # check reads. expected_mentions is the number of rows THIS process read out of
        # mentions.csv, not a count copied from batch_manifest.json: the evidence and the
        # thing it will be compared against then come from one file, read once, here.
        run_id = insert_run(
            conn,
            show_id=show_id,
            batch_name=batch_name,
            model=model,
            prompt_version=args.prompt_version,
            status=LOADING_RUN_STATUS,
            parameters={
                "batch_dir": str(batch_dir),
                "episodes": episode_ids,
                "source": "extract_entities.py",
                "expected_mentions": len(rows),
                "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )

        publish_dates = get_episode_publish_dates(conn, episode_ids)

        # One transaction for the whole batch. Every entity, every mention and the flip
        # to 'completed' land on one commit, or none of them do — so a crash can only
        # ever leave a 'loading' row with ZERO mentions, which the next attempt replaces
        # (delete_existing_run is status-blind) and which the health check can see.
        # Safe to hold open: the daily path batches EXTRACTION_BATCH_SIZE=5 episodes and
        # the largest run ever recorded holds 74 mentions (measured 2026-09-03; the
        # backfill era's 10- and 25-episode batches are the outliers). That is a few
        # hundred statements of pure DB work with no network call between them, and
        # Neon's idle_in_transaction_session_timeout of 5 minutes is measured on IDLE
        # time, of which this transaction has none.
        try:
            (
                mention_inserted,
                review_open,
                sponsor_inserted,
                first_seen_as_ad,
                entity_cache,
            ) = load_batch_rows(
                conn,
                run_id=run_id,
                rows=rows,
                transcript_map=transcript_map,
                publish_dates=publish_dates,
            )
            finalize_run_completed(conn, run_id, commit=False)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                # A dead connection is the canonical crash this whole transaction
                # defends against, and rollback() raises on one. The server aborts the
                # open transaction when the socket closes, so the rollback is a courtesy
                # — never let its failure replace the error that caused it. __main__
                # prints only str(exc), so a masked error is an error nobody sees.
                pass
            raise

        from_transcript = sum(1 for eid in episode_ids if transcript_map.get(eid) is not None)
        print(f"Loaded batch: {batch_name}")
        print(f"Run ID: {run_id}")
        print(f"Episodes: {len(episode_ids)}")
        print(
            f"Provenance: {from_transcript} from transcript, "
            f"{len(episode_ids) - from_transcript} from show notes"
            f"{' (inferred)' if inferred else ''}"
        )
        print(f"Entities upserted/used: {len(entity_cache)}")
        print(f"Mentions inserted: {mention_inserted}")
        print(
            f"  sponsor reads: {sponsor_inserted} "
            f"({first_seen_as_ad} entity/entities first seen in an ad)"
        )
        print(f"Mentions needing review: {review_open}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        # A missing batch manifest or mentions.csv will still be missing on the next
        # attempt: deterministic (exit 2), so run_script reports it instead of retrying
        # twice. Safe as a type handler because both raises happen before the database
        # connection is opened — nothing inside the batch transaction can raise this.
        print(f"Missing input file: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        # Everything else — including any database error that rolled the batch back —
        # exits 1 and IS retried. That retry is what makes the transactional load work:
        # the next attempt's delete_existing_run clears the abandoned 'loading' row and
        # re-runs the batch whole. Do not widen this into exit 2.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""
Sync AI entities from Neon to a Notion database.

Usage:
    python sync_notion.py --show ai-daily-brief --dry-run       # preview changes
    python sync_notion.py --show ai-daily-brief                 # incremental sync
    python sync_notion.py --show ai-daily-brief --full-reset    # wipe + re-create
    python sync_notion.py --show ai-daily-brief --min-mentions 5

Required env vars:
    - DATABASE_URL (or NEON_DATABASE_URL)
    - NOTION_TOKEN (Notion internal integration token)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

# Allow imports from pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import get_db_connection, get_logger, load_environment, post_slack
from show_config import SHOWS, get_show

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
RATE_LIMIT_DELAY = 0.35  # ~3 req/s

log = get_logger("pipeline.sync_notion")
FAILURE_ALERT_THRESHOLD = 0.10  # Slack-alert if >10% of a phase's entities fail


# ---------------------------------------------------------------------------
# Notion API helpers
# ---------------------------------------------------------------------------

def notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def notion_request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    """Make a Notion API request with retry on rate limit."""
    headers = notion_headers(token)
    for attempt in range(5):
        try:
            if method == "POST":
                resp = requests.post(url, headers=headers, json=body, timeout=30)
            elif method == "PATCH":
                resp = requests.patch(url, headers=headers, json=body, timeout=30)
            else:
                resp = requests.get(url, headers=headers, timeout=30)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            # ReadTimeout is a Timeout (NOT a ConnectionError) — retry both so a
            # transient slow Notion response doesn't kill a long full-reset.
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 1))
            print(f"  Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            continue
        if resp.status_code in (502, 503, 504):
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
        if resp.status_code >= 400:
            print(f"  Notion API error {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
        time.sleep(RATE_LIMIT_DELAY)
        return resp.json()
    raise RuntimeError("Notion API: too many retries")


def build_notion_properties(entity: dict) -> dict:
    """Convert a Neon entity rollup row to Notion page properties."""
    props: dict = {
        "Name": {"title": [{"text": {"content": str(entity["canonical_name"])[:2000]}}]},
        "Type": {"select": {"name": entity["entity_type"]}},
        "Mentions": {"number": int(entity["mention_count"])},
        # "Items" not "Episodes": the count spans episodes, blog posts, and research
        # docs since the 2026-06-11 curated-sources build (renamed in Notion same day).
        "Items": {"number": int(entity["episode_count"])},
    }

    if entity.get("first_date"):
        props["First Mentioned"] = {"date": {"start": str(entity["first_date"])}}
    if entity.get("last_date"):
        props["Last Mentioned"] = {"date": {"start": str(entity["last_date"])}}

    context = entity.get("latest_context") or ""
    if context:
        props["Context"] = {"rich_text": [{"text": {"content": context[:2000]}}]}

    url = entity.get("primary_url") or ""
    if url:
        props["URL"] = {"url": url[:2000]}

    show_names = entity.get("show_names") or []
    if show_names:
        # "Sources" not "Shows": podcasts, blogs, and research runs all tag here.
        props["Sources"] = {"multi_select": [{"name": str(n)[:100]} for n in show_names]}

    return props


def create_notion_page(token: str, database_id: str, properties: dict) -> str:
    """Create a Notion page. Returns the page ID."""
    body = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    result = notion_request("POST", f"{NOTION_API}/pages", token, body)
    return result["id"]


def update_notion_page(token: str, page_id: str, properties: dict) -> None:
    """Update an existing Notion page."""
    notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token, {"properties": properties})


def archive_notion_page(token: str, page_id: str) -> bool:
    """Archive (soft-delete) a Notion page. Returns False if already archived."""
    try:
        notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token, {"archived": True})
        return True
    except (requests.HTTPError, RuntimeError):
        return False  # already archived or transient error


def query_all_notion_pages(token: str, database_id: str) -> list[dict]:
    """Query all pages in a Notion database (paginated)."""
    pages = []
    start_cursor = None
    while True:
        body: dict = {"page_size": 100}
        if start_cursor:
            body["start_cursor"] = start_cursor
        result = notion_request("POST", f"{NOTION_API}/databases/{database_id}/query", token, body)
        pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        start_cursor = result.get("next_cursor")
    return pages


# ---------------------------------------------------------------------------
# Neon queries
# ---------------------------------------------------------------------------

def fetch_entity_rollup(
    conn, show_ids: list[int], show_names: dict[int, str], min_mentions: int = 2,
    curated_show_ids: list[int] | None = None,
) -> list[dict]:
    """Get entities with aggregated mention stats across a GROUP of shows (the shows
    sharing one Notion DB — Option A). Counts are global within the group; each entity
    carries the list of show names that mention it (for the Notion "Sources" property).

    Qualifier: group-global mentions >= min_mentions, OR >= 1 mention from a CURATED
    show. A curated pull is deliberate — Kevin chose that post because its citations
    matter — so its resources surface at 1 mention, while podcast chatter still needs
    min_mentions to filter noise. (Without the OR, blog-cited resources would hide;
    with min_mentions=1 globally, every 1-mention podcast entity would flood the DB.)"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                e.id AS entity_id,
                e.entity_type,
                e.canonical_name,
                e.primary_url,
                e.notion_page_id,
                e.updated_at,
                e.notion_synced_at,
                COALESCE(agg.mention_count, 0) AS mention_count,
                COALESCE(agg.episode_count, 0) AS episode_count,
                agg.first_date,
                agg.last_date,
                agg.latest_context,
                agg.show_ids
            FROM ai_entities e
            JOIN (
                SELECT
                    m.entity_id,
                    COUNT(*) AS mention_count,
                    COUNT(DISTINCT m.episode_id) AS episode_count,
                    MIN(ep.publish_date)::date AS first_date,
                    MAX(ep.publish_date)::date AS last_date,
                    (ARRAY_AGG(m.context_snippet ORDER BY ep.publish_date DESC NULLS LAST))[1] AS latest_context,
                    ARRAY_AGG(DISTINCT ep.show_id) AS show_ids
                FROM ai_mentions m
                JOIN episodes ep ON ep.id = m.episode_id
                WHERE ep.show_id = ANY(%s)
                GROUP BY m.entity_id
                HAVING COUNT(*) >= %s
                    OR COUNT(*) FILTER (WHERE ep.show_id = ANY(%s)) >= 1
            ) agg ON agg.entity_id = e.id
            ORDER BY agg.mention_count DESC;
            """,
            (show_ids, min_mentions, curated_show_ids or []),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["show_names"] = [
            show_names[sid] for sid in (r.get("show_ids") or []) if sid in show_names
        ]
    return rows


def save_notion_page_id(conn, entity_id: int, page_id: str) -> None:
    """Write back a Notion page ID to Neon after creating a page (marks synced)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_entities
            SET notion_page_id = %s, notion_synced_at = NOW(),
                notion_sync_status = 'synced', notion_sync_error = NULL,
                notion_sync_attempt_at = NOW()
            WHERE id = %s;
            """,
            (page_id, entity_id),
        )
    conn.commit()


def mark_synced(conn, entity_id: int) -> None:
    """Mark an entity successfully (re)synced to Notion."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_entities
            SET notion_synced_at = NOW(), notion_sync_status = 'synced',
                notion_sync_error = NULL, notion_sync_attempt_at = NOW()
            WHERE id = %s;
            """,
            (entity_id,),
        )
    conn.commit()


def mark_sync_failed(conn, entity_id: int, error: str) -> None:
    """Record a failed Notion sync attempt instead of swallowing it, so it can be
    retried and counted. Leaves notion_page_id / notion_synced_at intact.

    Best-effort — never raises, so persisting telemetry can't turn a recoverable
    per-entity failure into a whole-run crash.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ai_entities
                SET notion_sync_status = 'failed',
                    notion_sync_error = %s,
                    notion_sync_attempt_at = NOW()
                WHERE id = %s;
                """,
                (error[:500], entity_id),
            )
        conn.commit()
    except Exception as exc:  # recording a failure must not itself break the run
        log.warning("Could not record sync failure for entity %s: %s", entity_id, exc)


def clear_notion_ids_for_group(conn, show_ids: list[int]) -> int:
    """Clear notion_page_id/synced_at for entities mentioned by a show GROUP, so a
    full-reset re-creates only that group's pages. Scoped (not global) so a tech reset
    never wipes a media group's notion_page_ids, or vice versa. Returns count cleared."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_entities
            SET notion_page_id = NULL, notion_synced_at = NULL
            WHERE notion_page_id IS NOT NULL
              AND id IN (
                  SELECT DISTINCT m.entity_id
                  FROM ai_mentions m
                  JOIN episodes ep ON ep.id = m.episode_id
                  WHERE ep.show_id = ANY(%s)
              );
            """,
            (show_ids,),
        )
        count = cur.rowcount
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def compute_diff(entities: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split entities into to_create and to_update lists.

    An entity needs updating if:
    - Its ai_entities row was modified after last sync (updated_at > notion_synced_at)
    - OR its latest mention is newer than last sync (new mentions were added)
    """
    to_create = []
    to_update = []
    for e in entities:
        if not e["notion_page_id"]:
            to_create.append(e)
        else:
            synced = e.get("notion_synced_at")
            if not synced:
                to_update.append(e)
                continue
            # Entity row itself was modified (e.g., merge, alias update)
            updated = e.get("updated_at")
            if updated and updated > synced:
                to_update.append(e)
                continue
            # New mentions were added since last sync
            last_date = e.get("last_date")
            if last_date and last_date > synced.date():
                to_update.append(e)
    return to_create, to_update


def alert_on_failure_rate(phase: str, succeeded: int, failed: int) -> None:
    """Log a phase's failure count, and Slack-alert if the failure rate exceeds the
    threshold — so a silently-degrading Notion sync becomes loud instead of hiding.
    """
    total = succeeded + failed
    if total == 0 or failed == 0:
        return
    rate = failed / total
    msg = f"Notion sync — {phase}: {failed}/{total} failed ({rate:.0%})"
    log.warning(msg)
    if rate > FAILURE_ALERT_THRESHOLD:
        post_slack(f":warning: list-maker {msg}")


def run_full_reset(token: str, database_id: str, show_ids: list[int], show_names: dict[int, str], min_mentions: int, dry_run: bool,
                   curated_show_ids: list[int] | None = None) -> None:
    """Archive all existing pages, clear IDs, re-create everything.

    Manages its own DB connections (long Notion operations cause timeouts).
    """
    print("\n--- FULL RESET ---")

    # Archive existing pages
    print("Querying existing Notion pages...")
    existing_pages = query_all_notion_pages(token, database_id)
    print(f"Found {len(existing_pages)} existing pages to archive.")

    if not dry_run:
        archived = 0
        skipped = 0
        for i, page in enumerate(existing_pages):
            if page.get("archived"):
                skipped += 1
            elif archive_notion_page(token, page["id"]):
                archived += 1
            else:
                skipped += 1
            if (i + 1) % 100 == 0:
                print(f"  Progress: {i + 1}/{len(existing_pages)} (archived={archived}, skipped={skipped})")
        print(f"  Done: archived={archived}, already_archived={skipped}")

        # Fresh connection after long archiving phase
        conn = get_db_connection()
        try:
            cleared = clear_notion_ids_for_group(conn, show_ids)
            print(f"  Cleared {cleared} notion_page_id values in Neon.")
        finally:
            conn.close()
    else:
        print(f"  [dry-run] Would archive {len(existing_pages)} pages.")

    # Fresh connection for create phase
    conn = get_db_connection()
    try:
        entities = fetch_entity_rollup(conn, show_ids, show_names, min_mentions, curated_show_ids)

        # Create all entities
        print(f"\nCreating {len(entities)} pages...")
        if not dry_run:
            created = 0
            failed = 0
            for i, entity in enumerate(entities):
                try:
                    props = build_notion_properties(entity)
                    page_id = create_notion_page(token, database_id, props)
                    save_notion_page_id(conn, int(entity["entity_id"]), page_id)
                    created += 1
                except Exception as exc:
                    failed += 1
                    mark_sync_failed(conn, int(entity["entity_id"]), str(exc))
                    print(f"  SKIP: {entity['canonical_name']} — {exc}")
                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i + 1}/{len(entities)} (created={created}, failed={failed})")
            print(f"  Done: created={created}, failed={failed}")
            alert_on_failure_rate("full-reset create", created, failed)
        else:
            print(f"  [dry-run] Would create {len(entities)} pages.")
    finally:
        conn.close()


def run_incremental_sync(token: str, database_id: str, conn, entities: list[dict], dry_run: bool) -> None:
    """Create new pages, update changed pages."""
    to_create, to_update = compute_diff(entities)
    print(f"\n--- INCREMENTAL SYNC ---")
    print(f"  To create: {len(to_create)}")
    print(f"  To update: {len(to_update)}")
    print(f"  Up to date: {len(entities) - len(to_create) - len(to_update)}")

    if to_create:
        print(f"\nCreating {len(to_create)} new pages...")
        if not dry_run:
            created = 0
            failed = 0
            for i, entity in enumerate(to_create):
                try:
                    props = build_notion_properties(entity)
                    page_id = create_notion_page(token, database_id, props)
                    save_notion_page_id(conn, int(entity["entity_id"]), page_id)
                    created += 1
                except Exception as exc:
                    failed += 1
                    mark_sync_failed(conn, int(entity["entity_id"]), str(exc))
                    print(f"  SKIP: {entity['canonical_name']} — {exc}")
                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i + 1}/{len(to_create)} (created={created}, failed={failed})")
            print(f"  Created {created} pages." + (f" ({failed} failed)" if failed else ""))
            alert_on_failure_rate("incremental create", created, failed)
        else:
            print(f"  [dry-run] Would create {len(to_create)} pages.")

    if to_update:
        print(f"\nUpdating {len(to_update)} pages...")
        if not dry_run:
            updated = 0
            failed = 0
            for i, entity in enumerate(to_update):
                try:
                    props = build_notion_properties(entity)
                    update_notion_page(token, entity["notion_page_id"], props)
                    mark_synced(conn, int(entity["entity_id"]))
                    updated += 1
                except Exception as exc:
                    failed += 1
                    mark_sync_failed(conn, int(entity["entity_id"]), str(exc))
                    print(f"  SKIP update: {entity['canonical_name']} — {exc}")
                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i + 1}/{len(to_update)} (updated={updated}, failed={failed})")
            print(f"  Updated {updated} pages." + (f" ({failed} failed)" if failed else ""))
            alert_on_failure_rate("incremental update", updated, failed)
        else:
            print(f"  [dry-run] Would update {len(to_update)} pages.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync entities from Neon to Notion")
    p.add_argument("--show", required=True, help="Show slug (e.g., ai-daily-brief)")
    p.add_argument("--min-mentions", type=int, default=2, help="Minimum mention count (default: 2)")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p.add_argument("--full-reset", action="store_true", help="Wipe and re-create all pages")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()

    show = get_show(args.show)
    if not show.notion_database_id:
        print(f"Error: show '{args.show}' has no Notion database configured.")
        sys.exit(1)

    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("Error: NOTION_TOKEN env var is required.")
        sys.exit(1)

    conn = get_db_connection()
    try:
        # Option A: shows sharing a Notion DB form a group → one shared DB, one page per
        # entity, a "Sources" tag, and counts global within the group.
        group = [s for s in SHOWS.values() if s.notion_database_id == show.notion_database_id]
        show_ids = [s.show_id for s in group]
        show_names = {s.show_id: s.name for s in group}
        curated_ids = [s.show_id for s in group if s.medium != "podcast"]
        entities = fetch_entity_rollup(conn, show_ids, show_names, args.min_mentions, curated_ids)
        print(f"Notion DB group: {[s.slug for s in group]} (show_ids={show_ids})")
        print(f"Entities qualifying ({args.min_mentions}+ mentions, or any curated mention): {len(entities)}")

        if args.full_reset:
            run_full_reset(token, show.notion_database_id, show_ids, show_names, args.min_mentions, args.dry_run,
                           curated_ids)
        else:
            run_incremental_sync(token, show.notion_database_id, conn, entities, args.dry_run)

        print("\nDone.")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

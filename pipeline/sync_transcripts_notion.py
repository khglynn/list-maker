#!/usr/bin/env python3
"""Mirror tech-show transcripts into a Notion "Transcripts" database (Workstream 3b).

The Notion-AI half of "transcripts searchable BOTH" (Primer 2): one page per episode
with the FULL transcript in the page body and clean Show/Date/Episode-ID properties, so
Kevin can ask Notion AI natural-language questions across the whole corpus and get cited
answers. (The power-search half is search_transcripts.py over Neon FTS.)

Idempotent + resumable: only episodes whose episode_transcripts.notion_transcript_page_id
is NULL are processed, so a re-run never duplicates a page and a crash just resumes.
Batched + rate-limited via the hardened notion_request from sync_notion.

    # one-time: create the DB under the Pod Lists page, then paste the id below / pass --database-id
    ./venv/bin/python sync_transcripts_notion.py --create-db

    ./venv/bin/python sync_transcripts_notion.py --limit 3          # small verify batch
    ./venv/bin/python sync_transcripts_notion.py                    # full resumable backfill
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, get_logger, load_environment, post_slack  # noqa: E402
from pipeline.show_config import BLOG_NOTION_SHOWS, SHOWS, TRANSCRIPT_NOTION_SHOWS  # noqa: E402
from pipeline.sync_notion import NOTION_API, notion_request  # noqa: E402 — reuse hardened client

# Parent: the "Pod Lists" hub page. DB ids set via the one-time --create-db per target.
POD_LISTS_PAGE_ID = "31c0501e-f950-80d1-a3fd-e8fa8d5ce907"
TRANSCRIPTS_DB_ID = "3780501e-f950-81c9-a3e3-eca7f1162c9d"  # "Transcripts" DB under Pod Lists
BLOG_POSTS_DB_ID = "37c0501e-f950-8119-ab65-e0bfddf093f5"  # "Blog Posts" DB under Pod Lists

# One engine, two mirrors: which shows feed which Notion DB, and how pages differ.
# Blog pages carry URL + Links Out (the curation signal: outbound-link density).
SYNC_TARGETS: dict[str, dict] = {
    "transcripts": {
        "db_id": lambda: TRANSCRIPTS_DB_ID,
        "shows": TRANSCRIPT_NOTION_SHOWS,
        # Podcast vocabulary: a "Show" has "Episode" rows, plus a Source select
        # ("transcript") distinguishing how the text was obtained.
        "group_prop": "Show",
        "id_prop": "Episode ID",
        "source_label": "transcript",
        "title": "Transcripts",
        "description": ("Full episode transcripts for the tech shows (AI Daily, Hard Fork) — "
                        "ask Notion AI across them, or power-search via Neon FTS."),
        "icon": "\U0001F4DD",
        "blog_props": False,
    },
    "blog-posts": {
        "db_id": lambda: BLOG_POSTS_DB_ID,
        "shows": BLOG_NOTION_SHOWS,
        # Curated vocabulary: a post's "Source" is its publication, rows are "Items".
        # No source_label select — every row here is a blog post, a constant column
        # is noise (dropped in the 2026-06-11 UX pass).
        "group_prop": "Source",
        "id_prop": "Item ID",
        "source_label": None,
        "title": "Blog Posts",
        "description": ("Full text of curated blog posts/articles (OpenAI, Anthropic, one-off saves) — "
                        "pulled via the Blog Pull Queue; Links Out = outbound-link density, the pull signal."),
        "icon": "\U0001F4F0",
        "blog_props": True,
    },
}

SHOW_DISPLAY = {"ai-daily-brief": "AI Daily", "hard-fork": "Hard Fork"}
DEFAULT_SHOWS = ",".join(TRANSCRIPT_NOTION_SHOWS)
RICH_TEXT_LIMIT = 1900   # under Notion's 2000-char/rich-text cap, with margin
BLOCKS_PER_REQUEST = 100  # Notion's max children per create/append call

log = get_logger("pipeline.transcripts_notion")


def display_name(slug: str) -> str:
    """Short display override if one exists, else the configured show name."""
    if slug in SHOW_DISPLAY:
        return SHOW_DISPLAY[slug]
    return SHOWS[slug].name if slug in SHOWS else slug


def count_links_out(text: str) -> int:
    """Outbound-link density — Kevin's pull-worthiness signal for blog posts."""
    import re
    return len(re.findall(r"https?://", text or ""))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync full texts (transcripts/blog posts) to a Notion DB")
    p.add_argument("--target", default="transcripts", choices=sorted(SYNC_TARGETS),
                   help="Which Notion mirror to sync (picks DB + default shows)")
    p.add_argument("--shows", default="", help="Comma-separated show slugs (default: the target's shows)")
    p.add_argument("--limit", type=int, default=0, help="Max episodes this run (0 = all unsynced)")
    p.add_argument("--database-id", default="", help="Notion DB id (overrides the target's configured id)")
    p.add_argument("--create-db", action="store_true", help="Create the target's DB under Pod Lists and exit")
    p.add_argument("--dry-run", action="store_true", help="Show what would sync; no Notion writes")
    return p.parse_args()


def chunk_text(text: str, limit: int = RICH_TEXT_LIMIT) -> list[str]:
    """Split transcript into <=limit-char pieces, breaking at whitespace for readability."""
    text = (text or "").strip()
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks


def paragraph_block(content: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


def build_blocks(ep: dict, transcript: str, source_label: str = "transcript") -> list[dict]:
    show = display_name(ep["show_slug"])
    kind = "Full episode transcript." if source_label == "transcript" else "Full post text."
    intro = f"{show} — {ep['publish_date'] or 'date unknown'}. {kind}"
    return [paragraph_block(intro)] + [paragraph_block(c) for c in chunk_text(transcript)]


def build_properties(ep: dict, target: dict) -> dict:
    props = {
        "Name": {"title": [{"text": {"content": (ep["title"] or "Untitled")[:2000]}}]},
        target["group_prop"]: {"select": {"name": display_name(ep["show_slug"])}},
        target["id_prop"]: {"number": ep["episode_id"]},
        "Characters": {"number": ep["chars"]},
    }
    if target["source_label"]:
        props["Source"] = {"select": {"name": target["source_label"]}}
    if ep["publish_date"]:
        props["Date"] = {"date": {"start": str(ep["publish_date"])}}
    if target["blog_props"]:
        if ep.get("url"):
            props["URL"] = {"url": ep["url"]}
        props["Links Out"] = {"number": count_links_out(ep.get("transcript_text") or "")}
    return props


def create_database(token: str, target: dict) -> str:
    show_options = [{"name": display_name(slug), "color": "default"} for slug in target["shows"]]
    properties = {
        "Name": {"title": {}},
        target["group_prop"]: {"select": {"options": show_options}},
        "Date": {"date": {}},
        target["id_prop"]: {"number": {}},
        "Characters": {"number": {}},
    }
    if target["source_label"]:
        properties["Source"] = {"select": {"options": [{"name": target["source_label"], "color": "default"}]}}
    if target["blog_props"]:
        properties["URL"] = {"url": {}}
        properties["Links Out"] = {"number": {}}
    body = {
        "parent": {"type": "page_id", "page_id": POD_LISTS_PAGE_ID},
        "title": [{"type": "text", "text": {"content": target["title"]}}],
        "description": [{"type": "text", "text": {"content": target["description"]}}],
        "icon": {"type": "emoji", "emoji": target["icon"]},
        "properties": properties,
    }
    result = notion_request("POST", f"{NOTION_API}/databases", token, body)
    return result["id"]


def create_transcript_page(token: str, db_id: str, ep: dict, transcript: str, target: dict) -> str:
    blocks = build_blocks(ep, transcript, target["source_label"])
    first, rest = blocks[:BLOCKS_PER_REQUEST], blocks[BLOCKS_PER_REQUEST:]
    result = notion_request(
        "POST", f"{NOTION_API}/pages", token,
        {"parent": {"database_id": db_id}, "properties": build_properties(ep, target), "children": first},
    )
    page_id = result["id"]
    for i in range(0, len(rest), BLOCKS_PER_REQUEST):
        notion_request(
            "PATCH", f"{NOTION_API}/blocks/{page_id}/children", token,
            {"children": rest[i:i + BLOCKS_PER_REQUEST]},
        )
    return page_id


def find_unsynced(conn, shows: list[str], limit: int) -> list[dict]:
    # Don't filter empty transcripts here — let them enter the loop so they're explicitly
    # logged + counted (skipped without marking synced) instead of silently excluded.
    sql = """
        SELECT ep.id AS episode_id, ep.title, ep.publish_date, ep.url, s.slug AS show_slug,
               length(et.transcript_text) AS chars, et.transcript_text
        FROM episode_transcripts et
        JOIN episodes ep ON ep.id = et.episode_id
        JOIN shows s ON s.id = ep.show_id
        WHERE s.slug = ANY(%s)
          AND et.transcript_text IS NOT NULL
          AND et.notion_transcript_page_id IS NULL
        ORDER BY ep.publish_date DESC NULLS LAST, ep.id DESC
    """
    params: list = [shows]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_existing_notion_pages(token: str, db_id: str, id_prop: str = "Episode ID") -> dict[int, str]:
    """Map episode_id -> page_id for full-text pages already in the Notion DB.

    Closes the create/commit atomicity gap: if a previous run created a Notion page but
    crashed before committing notion_transcript_page_id, a resumed run ADOPTS that page
    (marks it synced) instead of creating a duplicate. One upfront paginated scan, not a
    per-episode query. id_prop is the target's row-id property (Episode ID / Item ID).
    """
    existing: dict[int, str] = {}
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
        for page in result.get("results", []):
            num = page.get("properties", {}).get(id_prop, {}).get("number")
            if num is not None:
                existing[int(num)] = page["id"]
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return existing


def mark_synced(conn, episode_id: int, page_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE episode_transcripts SET notion_transcript_page_id=%s, notion_transcript_synced_at=now() "
            "WHERE episode_id=%s",
            (page_id, episode_id),
        )
    conn.commit()


def main() -> None:
    args = parse_args()
    load_environment()
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise SystemExit("NOTION_TOKEN is required")

    target = SYNC_TARGETS[args.target]

    if args.create_db:
        db_id = create_database(token, target)
        print(f"Created {target['title']} DB: {db_id}")
        print(f"Set the {args.target} db id constant in sync_transcripts_notion.py to this value (then commit).")
        return

    db_id = args.database_id or target["db_id"]()
    if not db_id or "PLACEHOLDER" in db_id:
        raise SystemExit(
            f"No {target['title']} DB id — run --create-db --target {args.target} first, "
            "then set the constant (or pass --database-id)."
        )

    shows = [s.strip() for s in (args.shows or ",".join(target["shows"])).split(",") if s.strip()]
    conn = get_db_connection()
    try:
        episodes = find_unsynced(conn, shows, args.limit)
        log.info("Found %d unsynced transcript(s) for %s", len(episodes), shows)

        # Adopt-don't-duplicate: a page already in Notion (from a run that crashed before
        # its DB marker committed) is healed, not recreated. Skipped when there's nothing
        # to sync — the daily no-new-episodes run shouldn't pay a full DB pagination scan.
        existing = {} if (args.dry_run or not episodes) else fetch_existing_notion_pages(
            token, db_id, target["id_prop"]
        )
        if existing:
            log.info("%d transcript page(s) already in Notion — adopt on match", len(existing))

        synced = healed = skipped_empty = failed = 0
        for i, ep in enumerate(episodes, 1):
            label = f"[{i}/{len(episodes)}] ep {ep['episode_id']} {ep['show_slug']} ({ep['chars']} chars)"
            transcript = (ep["transcript_text"] or "").strip()
            if not transcript:
                # Don't mark synced — surfaces the gap and lets a real transcript retry later.
                skipped_empty += 1
                log.warning("skip ep %s: empty/whitespace transcript (NOT marked synced)", ep["episode_id"])
                continue
            if args.dry_run:
                print(f"DRY-RUN {label}: {ep['title'][:60]}")
                continue
            try:
                if ep["episode_id"] in existing:
                    mark_synced(conn, ep["episode_id"], existing[ep["episode_id"]])
                    healed += 1
                    continue
                page_id = create_transcript_page(token, db_id, ep, transcript, target)
                mark_synced(conn, ep["episode_id"], page_id)
                synced += 1
                if (synced + healed) % 25 == 0:
                    log.info("progress %d/%d", synced + healed, len(episodes))
            except Exception as exc:  # noqa: BLE001 — isolate one bad episode, keep going
                # Clear any aborted transaction so a single DB hiccup can't cascade-fail
                # every remaining episode ("current transaction is aborted").
                conn.rollback()
                failed += 1
                log.error("FAILED ep %s: %s", ep["episode_id"], exc)
    finally:
        conn.close()

    if not args.dry_run:
        msg = (f"{args.target} -> Notion: synced {synced}, healed {healed}, "
               f"skipped_empty {skipped_empty}, failed {failed} (of {len(episodes)})")
        log.info(msg)
        if failed or skipped_empty:
            post_slack(f":warning: *list-maker {msg}*")
        if failed:
            # Exit non-zero so the CI step goes red — a green step with lost work is
            # the silent-failure class this pipeline hunts. (skipped_empty stays
            # warn-only: an empty transcript is a data gap owned by data_health's
            # coverage check, and it retries naturally on every run.)
            raise SystemExit(1)


if __name__ == "__main__":
    main()

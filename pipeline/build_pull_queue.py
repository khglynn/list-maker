#!/usr/bin/env python3
"""Blog Pull Queue: candidate discovery + Kevin's checkbox curation, in Notion.

The curation loop (weekly via blogs.yml, or on demand):
  1. --build   DISCOVER candidate posts from the mentions DB (posts the podcasts
               cited: registered blog domains + any blog_post-typed mention with a
               source_url), ENRICH each new one via Firecrawl (word count, outbound
               links — Links Out is THE pull signal: link-dense posts improve the
               mentions DB most), and upsert rows into the "Blog Pull Queue" Notion DB.
  2. Kevin checks the Pull box on rows worth ingesting, whenever, in Notion.
  3. --ingest  ingest every checked-but-still-candidate row via save_item, mark
               Status=pulled (or failed). PDFs are marked pdf-report at build time
               and never auto-ingested (they belong in the Obsidian research folder).

Kevin's checks vs. the Links Out ranking are the ground truth for a future
auto-pull threshold — don't automate the choice until the marks validate a rule.

    ./venv/bin/python build_pull_queue.py --create-db     # one-time
    ./venv/bin/python build_pull_queue.py --build         # discover + enrich
    ./venv/bin/python build_pull_queue.py --build --ingest  # the weekly run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, get_logger, load_environment, post_slack  # noqa: E402
from pipeline.scrapers.blog.import_blog import canonicalize_url, is_probable_post_url, scrape_post  # noqa: E402
from pipeline.show_config import SHOWS  # noqa: E402
from pipeline.sync_notion import NOTION_API, notion_request  # noqa: E402

POD_LISTS_PAGE_ID = "31c0501e-f950-80d1-a3fd-e8fa8d5ce907"
QUEUE_DB_ID = "37c0501e-f950-8139-b52b-e5d5f7d71f53"  # "Blog Pull Queue" under Pod Lists

MAX_NEW_CANDIDATES_PER_RUN = 25  # enrichment scrapes cost Firecrawl credits; cap + log the drop

log = get_logger("pipeline.pull_queue")


def registered_blog_domains() -> set[str]:
    domains = set()
    for cfg in SHOWS.values():
        if cfg.medium == "blog" and cfg.fallback_website_url:
            host = urlsplit(cfg.fallback_website_url).netloc.lower().removeprefix("www.")
            if host:
                domains.add(host)
    return domains


def discover_candidates(conn) -> list[dict]:
    """Candidate post URLs from the mentions DB, newest-cited first.

    Scope: source_urls on mentions that are (a) on a registered blog domain, or
    (b) typed blog_post — i.e. things the podcasts actually pointed at. Already-
    ingested URLs (episodes.url) are excluded after canonicalization in Python.
    """
    domains = sorted(registered_blog_domains())
    domain_pattern = "|".join(re.escape(d) for d in domains) or "^$"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.source_url,
                   COUNT(DISTINCT m.episode_id) AS cited_in_episodes,
                   MAX(ep.publish_date)::date AS last_cited,
                   (ARRAY_AGG(m.context_snippet ORDER BY ep.publish_date DESC NULLS LAST))[1] AS why,
                   ARRAY_AGG(DISTINCT s.name) AS shows
            FROM ai_mentions m
            JOIN episodes ep ON ep.id = m.episode_id
            JOIN shows s ON s.id = ep.show_id
            WHERE m.source_url IS NOT NULL AND m.source_url <> ''
              AND (
                    m.source_url ~* ('^https?://(www\\.)?(' || %s || ')/')
                 OR m.mention_type = 'blog_post'
              )
            GROUP BY m.source_url
            ORDER BY MAX(ep.publish_date) DESC NULLS LAST;
            """,
            (domain_pattern,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    by_canonical: dict[str, dict] = {}
    for row in rows:
        url = canonicalize_url(row["source_url"])
        if not is_probable_post_url(url):
            continue  # domain roots / section indexes aren't pullable posts
        merged = by_canonical.setdefault(url, {**row, "url": url, "cited_in_episodes": 0})
        merged["cited_in_episodes"] += int(row["cited_in_episodes"] or 0)

    if not by_canonical:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT url FROM episodes WHERE url = ANY(%s)", (list(by_canonical),))
        already = {r["url"] for r in cur.fetchall()}
    return [c for u, c in by_canonical.items() if u not in already]


# ── Notion queue I/O ─────────────────────────────────────────────────────────

def create_queue_db(token: str) -> str:
    body = {
        "parent": {"type": "page_id", "page_id": POD_LISTS_PAGE_ID},
        "title": [{"type": "text", "text": {"content": "Blog Pull Queue"}}],
        "description": [{"type": "text", "text": {"content":
            "Candidate posts discovered from the mentions DB. Check Pull on anything worth "
            "ingesting — the weekly run pulls checked rows into Neon + the Blog Posts DB. "
            "Links Out = outbound-link density, the pull-worthiness signal."}}],
        "icon": {"type": "emoji", "emoji": "\U0001F4E5"},
        "properties": {
            "Name": {"title": {}},
            "URL": {"url": {}},
            "Source": {"select": {}},
            "Found Via": {"rich_text": {}},
            "Last Cited": {"date": {}},
            "Words": {"number": {}},
            "Links Out": {"number": {}},
            "Why": {"rich_text": {}},
            "Pull": {"checkbox": {}},
            "Status": {"select": {"options": [
                {"name": "candidate", "color": "blue"},
                {"name": "pulled", "color": "green"},
                {"name": "pdf-report", "color": "orange"},
                {"name": "failed", "color": "red"},
                {"name": "skipped", "color": "gray"},
            ]}},
        },
    }
    return notion_request("POST", f"{NOTION_API}/databases", token, body)["id"]


def queue_urls(token: str, db_id: str) -> set[str]:
    """All URLs already in the queue (any status) — the dedup gate for --build."""
    urls: set[str] = set()
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
        for page in result.get("results", []):
            url = page.get("properties", {}).get("URL", {}).get("url")
            if url:
                urls.add(url)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return urls


def checked_candidates(token: str, db_id: str) -> list[dict]:
    """Rows Kevin checked that haven't been pulled yet: Pull=true AND Status=candidate."""
    out: list[dict] = []
    cursor = None
    body_filter = {"and": [
        {"property": "Pull", "checkbox": {"equals": True}},
        {"property": "Status", "select": {"equals": "candidate"}},
    ]}
    while True:
        body: dict = {"page_size": 100, "filter": body_filter}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
        for page in result.get("results", []):
            url = page.get("properties", {}).get("URL", {}).get("url")
            if url:
                out.append({"page_id": page["id"], "url": url})
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return out


def set_row_status(token: str, page_id: str, status: str) -> None:
    notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token,
                   {"properties": {"Status": {"select": {"name": status}}}})


def create_queue_row(token: str, db_id: str, cand: dict) -> None:
    props = {
        "Name": {"title": [{"text": {"content": (cand.get("title") or cand["url"])[:2000]}}]},
        "URL": {"url": cand["url"]},
        "Source": {"select": {"name": urlsplit(cand["url"]).netloc.removeprefix("www.")[:100]}},
        "Found Via": {"rich_text": [{"text": {"content":
            f"{', '.join(cand.get('shows') or [])} — {cand['cited_in_episodes']} episode(s)"[:2000]}}]},
        "Pull": {"checkbox": False},
        "Status": {"select": {"name": cand.get("status", "candidate")}},
    }
    if cand.get("last_cited"):
        props["Last Cited"] = {"date": {"start": str(cand["last_cited"])}}
    if cand.get("words") is not None:
        props["Words"] = {"number": cand["words"]}
    if cand.get("links_out") is not None:
        props["Links Out"] = {"number": cand["links_out"]}
    if cand.get("why"):
        props["Why"] = {"rich_text": [{"text": {"content": str(cand["why"])[:1900]}}]}
    notion_request("POST", f"{NOTION_API}/pages", token,
                   {"parent": {"database_id": db_id}, "properties": props})


# ── The two run modes ────────────────────────────────────────────────────────

def enrich(cand: dict, api_key: str) -> dict:
    """Firecrawl the candidate for the decide-worthy stats Kevin asked for."""
    if cand["url"].lower().endswith(".pdf"):
        return {**cand, "status": "pdf-report", "title": Path(urlsplit(cand["url"]).path).name}
    try:
        scraped = scrape_post(cand["url"], api_key)
        text = scraped["markdown"] or ""
        meta = scraped["metadata"]
        title = (meta.get("ogTitle") or meta.get("title") or cand["url"]).strip()
        return {**cand, "title": title, "words": len(text.split()),
                "links_out": len(re.findall(r"https?://", text))}
    except Exception as exc:  # noqa: BLE001 — a failed enrich still queues, just stat-less
        log.warning("enrich failed for %s: %s", cand["url"], exc)
        return {**cand, "title": cand["url"]}


def run_build(token: str, db_id: str, conn, api_key: str) -> int:
    candidates = discover_candidates(conn)
    existing = queue_urls(token, db_id)
    fresh = [c for c in candidates if c["url"] not in existing]
    dropped = len(fresh) - MAX_NEW_CANDIDATES_PER_RUN
    if dropped > 0:
        log.warning("capping new candidates at %d (%d more wait for the next run)",
                    MAX_NEW_CANDIDATES_PER_RUN, dropped)
        fresh = fresh[:MAX_NEW_CANDIDATES_PER_RUN]
    for cand in fresh:
        create_queue_row(token, db_id, enrich(cand, api_key))
    log.info("queue build: %d candidate(s) discovered, %d new row(s) added", len(candidates), len(fresh))
    return len(fresh)


def ingest_checked_rows(save_fn: Callable[[str, Optional[str]], bool],
                        token: str | None = None, db_id: str | None = None) -> bool:
    """Pull every checked candidate via save_fn(url, show=None). Returns all-ok."""
    token = token or os.getenv("NOTION_TOKEN")
    db_id = db_id or QUEUE_DB_ID
    if not (token and db_id):
        raise SystemExit("NOTION_TOKEN and the queue DB id are required for --ingest")
    rows = checked_candidates(token, db_id)
    log.info("%d checked row(s) to ingest", len(rows))
    ok_count = fail_count = 0
    for row in rows:
        try:
            ok = save_fn(row["url"], None)
        except Exception as exc:  # noqa: BLE001 — one bad row must not strand the rest
            log.error("ingest FAILED %s: %s", row["url"], exc)
            ok = False
        set_row_status(token, row["page_id"], "pulled" if ok else "failed")
        ok_count += ok
        fail_count += (not ok)
    if rows:
        post_slack(f":inbox_tray: *list-maker pull queue*: ingested {ok_count}, failed {fail_count}")
    return fail_count == 0


def main() -> None:
    p = argparse.ArgumentParser(description="Build/ingest the Blog Pull Queue (Notion)")
    p.add_argument("--create-db", action="store_true")
    p.add_argument("--build", action="store_true", help="Discover + enrich + upsert candidates")
    p.add_argument("--ingest", action="store_true", help="Ingest checked rows via save_item")
    args = p.parse_args()

    load_environment()
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise SystemExit("NOTION_TOKEN is required")

    if args.create_db:
        print(f"Created Blog Pull Queue DB: {create_queue_db(token)}")
        print("Set QUEUE_DB_ID in build_pull_queue.py (then commit).")
        return
    if not QUEUE_DB_ID:
        raise SystemExit("QUEUE_DB_ID not set — run --create-db first.")

    new_rows = 0
    if args.build:
        api_key = os.getenv("FIRECRAWL_API_KEY")
        if not api_key:
            raise SystemExit("FIRECRAWL_API_KEY is required for --build")
        conn = get_db_connection()
        try:
            new_rows = run_build(token, QUEUE_DB_ID, conn, api_key)
        finally:
            conn.close()
        if new_rows:
            post_slack(f":mag: *list-maker pull queue*: {new_rows} new candidate(s) await your checkbox")

    if args.ingest:
        from pipeline.save_item import save_url  # late import: avoids a module cycle at load time
        if not ingest_checked_rows(save_url, token, QUEUE_DB_ID):
            raise SystemExit(1)
    elif not args.build:
        p.error("nothing to do — pass --build and/or --ingest")


if __name__ == "__main__":
    main()

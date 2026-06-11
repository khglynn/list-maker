#!/usr/bin/env python3
"""Save one item into list-maker: scrape → store → extract mentions → sync Notion.

The user-facing entry point for curated saves ("save this blog post please"):

    ./venv/bin/python save_item.py --url https://www.anthropic.com/news/claude-fable-5
    ./venv/bin/python save_item.py --url https://... --show openai-blog   # override resolution
    ./venv/bin/python save_item.py --from-queue                            # ingest checked queue rows

Show resolution: a registered blog show whose domain matches the URL, else the
saved-articles catch-all. PDFs/long reports skip the DB (Kevin's rule: those live
as files in the Obsidian research folder) — set RESEARCH_DOCS_DIR to override the
default vault path; in CI the queue build marks PDFs so this path never runs there.

Composes existing steps rather than reimplementing: import_blog.ingest_url for
storage, run_new_episodes.step_entity_extraction for mentions (skipped when the
episode already has mentions — re-saving is safe), step_notion_sync for entities,
sync_transcripts_notion --target blog-posts for the full-text mirror.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, get_logger, load_environment  # noqa: E402
from pipeline.run_new_episodes import step_entity_extraction, step_notion_sync  # noqa: E402
from pipeline.scrapers.blog.import_blog import canonicalize_url, ingest_url  # noqa: E402
from pipeline.show_config import SHOWS, get_show  # noqa: E402

PIPELINE_DIR = Path(__file__).resolve().parent
DEFAULT_RESEARCH_DOCS_DIR = (
    Path.home() / "Documents" / "HG Main" / "0.2 Clips + Social + AI"
    / "Agentic Research" / "Documents"
)

log = get_logger("pipeline.save_item")


def domain_to_show() -> dict[str, str]:
    """Map registered blog-show domains -> slug (derived, no second list to drift)."""
    mapping: dict[str, str] = {}
    for slug, cfg in SHOWS.items():
        if cfg.medium == "blog" and cfg.fallback_website_url:
            host = urlsplit(cfg.fallback_website_url).netloc.lower().removeprefix("www.")
            if host:
                mapping[host] = slug
    return mapping


def resolve_show(url: str) -> str:
    host = urlsplit(canonicalize_url(url)).netloc.removeprefix("www.")
    return domain_to_show().get(host, "saved-articles")


def is_pdf(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def save_pdf(url: str) -> Path:
    """Download a PDF/report into the Obsidian research Documents folder (no DB row).
    Fails loudly if the folder doesn't exist (e.g. CI) — that's a local-only action."""
    import httpx

    docs_dir = Path(os.getenv("RESEARCH_DOCS_DIR") or DEFAULT_RESEARCH_DOCS_DIR)
    if not docs_dir.parent.exists():
        raise SystemExit(
            f"Research folder not found ({docs_dir.parent}) — PDF saves are local-only. "
            "Set RESEARCH_DOCS_DIR or run on the machine with the Obsidian vault."
        )
    docs_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlsplit(url).path).name or "document.pdf"
    target = docs_dir / name
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    log.info("saved PDF to %s (link it from Obsidian; not ingested into Neon)", target)
    return target


def episode_has_mentions(conn, episode_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM ai_mentions WHERE episode_id = %s LIMIT 1", (episode_id,))
        return cur.fetchone() is not None


def sync_blog_mirror() -> bool:
    """Mirror any unsynced blog full texts to the Blog Posts Notion DB."""
    result = subprocess.run(
        [sys.executable, str(PIPELINE_DIR / "sync_transcripts_notion.py"), "--target", "blog-posts"],
        cwd=str(PIPELINE_DIR),
    )
    return result.returncode == 0


def save_url(url: str, show_slug: str | None, skip_extract: bool = False) -> bool:
    """Full save flow for one URL. Returns True on success."""
    if is_pdf(url):
        save_pdf(url)
        return True

    slug = show_slug or resolve_show(url)
    cfg = get_show(slug)
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise SystemExit("FIRECRAWL_API_KEY is required")

    conn = get_db_connection()
    try:
        episode_id = ingest_url(conn, slug, url, api_key)
        already_extracted = episode_has_mentions(conn, episode_id)
    finally:
        conn.close()

    ok = True
    if skip_extract:
        log.info("ep %s stored (extraction skipped by flag)", episode_id)
    elif already_extracted:
        log.info("ep %s already has mentions — extraction skipped (idempotent re-save)", episode_id)
    else:
        ok = step_entity_extraction(cfg, [episode_id], dry_run=False)

    # Sync even when extraction partially failed — whatever DID load should reach
    # Notion; the False return still fails the run so the gap is visible.
    ok = step_notion_sync(cfg, dry_run=False) and ok
    ok = sync_blog_mirror() and ok
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description="Save a blog post/article into list-maker")
    p.add_argument("--url", action="append", default=[], help="Article URL (repeatable)")
    p.add_argument("--show", default="", help="Force a show slug (default: resolve by domain)")
    p.add_argument("--podcast", default="", help="(deferred) one-off podcast episode saves")
    p.add_argument("--from-queue", action="store_true",
                   help="Ingest all checked-but-unpulled rows from the Blog Pull Queue")
    p.add_argument("--skip-extract", action="store_true", help="Store full text only")
    args = p.parse_args()

    load_environment()

    if args.podcast:
        raise SystemExit(
            "One-off podcast saves aren't built yet (no concrete case has needed them — "
            "narrated articles save better via --url). If the episode's show is registered, "
            "run the normal pipeline; otherwise this is the 'taddy episode lookup' backlog item."
        )

    if args.from_queue:
        from pipeline.build_pull_queue import ingest_checked_rows  # late import — needs NOTION_TOKEN
        raise SystemExit(0 if ingest_checked_rows(save_url) else 1)

    if not args.url:
        raise SystemExit("Provide --url (repeatable) or --from-queue")

    failed = 0
    for url in args.url:
        try:
            if not save_url(url, args.show or None, skip_extract=args.skip_extract):
                failed += 1
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 — isolate one bad URL, keep going
            failed += 1
            log.error("FAILED %s: %s", url, exc)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

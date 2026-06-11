#!/usr/bin/env python3
"""Ingest Agentic Research run docs (local Obsidian markdown) into Neon for
mention extraction. LOCAL-ONLY: the vault lives on Kevin's Mac, so this never
runs in CI — the agentic-research show is excluded from all scheduled paths.

The docs' canonical home stays Obsidian (no Notion full-text mirror); ingestion
exists so their citations (papers, tools, benchmarks) join the shared mentions
DB. The episodes.url key is a real obsidian:// URI — vault-relative (machine-
independent), UNIQUE-stable across re-runs, and clickable. A renamed file reads
as a new doc; acceptable for an archive that rarely renames.

    ./venv/bin/python scrapers/research/import_research.py --limit 5   # validation
    ./venv/bin/python scrapers/research/import_research.py             # full corpus
    # then extract:
    ./venv/bin/python run_new_episodes.py --shows agentic-research --backfill
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pipeline.common import get_db_connection, get_logger, load_environment  # noqa: E402
from pipeline.show_config import get_show  # noqa: E402

VAULT_NAME = "HG Main"
DEFAULT_RESEARCH_DIR = (
    Path.home() / "Documents" / VAULT_NAME / "0.2 Clips + Social + AI" / "Agentic Research"
)
MIN_CHARS = 500  # below this a file is a stub/index, not a research doc worth extracting

# The archive mixes research OUTPUTS (what we extract) with workflow infrastructure
# (what we must not — extracting Kevin's own CLAUDE.md/backlog/prompt bodies would
# pollute the mentions DB with his tooling instead of researched sources).
EXCLUDE_NAMES = {
    "claude.md", "readme.md", "backlog.md", "setup.md", "agents.md",
    "gemini.md", "now.md", "devlog.md", "inventory.md",
}
EXCLUDE_DIR_PARTS = {"claude plans", "prompts", "scripts", "templates"}


def is_research_output(path: Path) -> bool:
    if path.name.lower() in EXCLUDE_NAMES:
        return False
    return not any(part.lower() in EXCLUDE_DIR_PARTS for part in path.parts)

log = get_logger("pipeline.import_research")


def obsidian_uri(vault_relative: Path) -> str:
    """Stable per-file key + working deep link: obsidian://open?vault=...&file=..."""
    return f"obsidian://open?vault={quote(VAULT_NAME)}&file={quote(str(vault_relative))}"


def doc_title(path: Path, text: str) -> str:
    for line in text.splitlines()[:10]:
        if line.startswith("# "):
            return line[2:].strip()[:500]
    return path.stem[:500]


def doc_date(path: Path, text: str) -> Optional[date]:
    """Date priority: YYYY-MM-DD in the filename, then frontmatter date:, then None
    (caller falls back to file mtime — better than nothing for ordering)."""
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", path.name)
    if not match:
        match = re.search(r"^date:\s*[\"']?(20\d{2})-(\d{2})-(\d{2})", text[:500], re.MULTILINE)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    return None


def find_docs(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.md") if p.is_file() and is_research_output(p))


def upsert_doc(conn, show_id: int, key: str, title: str, publish_date: date, text: str) -> tuple[int, bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episodes (show_id, title, url, publish_date, scraped_at, raw_content)
            VALUES (%s, %s, %s, %s, now(), %s)
            ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title
            RETURNING id, (xmax = 0) AS created
            """,
            (show_id, title, key, publish_date, '{"provider": "obsidian_research"}'),
        )
        row = cur.fetchone()
        episode_id, created = row["id"], row["created"]
        cur.execute(
            """
            INSERT INTO episode_transcripts (episode_id, source_type, source_url, transcript_text)
            VALUES (%s, 'research_run', %s, %s)
            ON CONFLICT (episode_id) DO UPDATE
              SET transcript_text = EXCLUDED.transcript_text, updated_at = now()
            """,
            (episode_id, key, text),
        )
    conn.commit()
    return episode_id, created


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest Agentic Research markdown into Neon")
    p.add_argument("--root", default="", help="Research folder (default: the vault path)")
    p.add_argument("--subdir", default="", help="Only files under this subfolder (e.g. 'Cowork Agentic Research')")
    p.add_argument("--limit", type=int, default=0, help="Max files this run (validation batches)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_environment()
    root = Path(args.root or os.getenv("RESEARCH_RUNS_DIR") or DEFAULT_RESEARCH_DIR)
    if not root.is_dir():
        raise SystemExit(f"Research folder not found: {root} — this importer is local-only.")

    vault_root = Path.home() / "Documents" / VAULT_NAME
    try:
        # Keys are vault-relative obsidian:// URIs — a root outside the vault has no
        # valid key and would crash mid-import; fail clearly before touching the DB.
        root.resolve().relative_to(vault_root.resolve())
    except ValueError:
        raise SystemExit(f"--root must be inside the Obsidian vault ({vault_root}).")

    cfg = get_show("agentic-research")
    docs = find_docs(root / args.subdir if args.subdir else root)

    created = refreshed = skipped_small = 0
    conn = get_db_connection()
    try:
        for path in docs:
            if args.limit and (created + refreshed) >= args.limit:
                break
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) < MIN_CHARS:
                skipped_small += 1
                continue
            key = obsidian_uri(path.relative_to(vault_root))
            title = doc_title(path, text)
            publish_date = doc_date(path, text) or date.fromtimestamp(path.stat().st_mtime)
            if args.dry_run:
                print(f"DRY-RUN {title!r} ({publish_date}, {len(text)} chars) <- {key}")
                created += 1
                continue
            _episode_id, was_created = upsert_doc(conn, cfg.show_id, key, title, publish_date, text)
            created += was_created
            refreshed += (not was_created)
    finally:
        conn.close()

    log.info(
        "research import: created %d, refreshed %d, skipped_small %d (of %d files under %s)",
        created, refreshed, skipped_small, len(docs), root,
    )


if __name__ == "__main__":
    main()

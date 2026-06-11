#!/usr/bin/env python3
"""One-off podcast episodes → "Saved Episodes" pages in the Transcripts DB.

For episodes of shows we don't carry (Kevin's Castro clips from Science Vs,
Pivot, Vergecast…, plus episode links saved in Apple Notes), this creates an
honest page per episode under the saved-episodes catch-all show:

  - TADDY UPGRADE first: search the episode by title; if Taddy has a transcript,
    the page gets the FULL transcript and the clip highlight anchors inside it.
  - Otherwise the page is an honest excerpt: the clip's own transcription
    (source_type='castro_clip') or the episode show-notes (source_type='show_notes')
    — labeled as such, never pretending to be the full episode.

Pages show the REAL show name (per-episode source_name → the Notion Show select).
No entity extraction on purpose: these span culture/politics shows whose
tech-profile mentions would pollute the shared Tech DB; the value is the
highlight, not the mentions. Local-only; shares highlight_clips' manifest so
clip ids never double-process.

    ./venv/bin/python save_episode.py --dry-run
    ./venv/bin/python save_episode.py                # clips pass + links pass
    ./venv/bin/python save_episode.py --links-file pipeline/_cache/apple-notes/podcast-links.txt
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, get_logger, load_environment  # noqa: E402
from pipeline.highlight_clips import (  # noqa: E402 — the clip toolkit is the reuse surface
    CACHE_DIR, DEFAULT_CLIPS_DIR, build_highlight, discover_clips, existing_highlight,
    extract_audio, find_anchor_block, anchor_url, insert_highlight, load_manifest,
    locate_span, match_slug, page_blocks, probe_tags, quote_from_span, save_manifest,
    transcribe, upload_audio,
)
from pipeline.scrapers.taddy.import_transcripts import (  # noqa: E402
    epoch_to_date, get_episode_transcript, taddy_query,
)
from pipeline.show_config import get_show  # noqa: E402

SAVED_SLUG = "saved-episodes"
DEFAULT_LINKS_FILE = CACHE_DIR.parent / "apple-notes" / "podcast-links.txt"
TADDY_TITLE_MIN_RATIO = 0.80
MIN_FULL_TRANSCRIPT_CHARS = 1000  # below this a "transcript" is a stub, not an upgrade

log = get_logger("pipeline.save_episode")


# ── Taddy episode lookup (the one-off path the registry never needed) ───────

def taddy_find_episode(episode_title: str, show_name: str, user_id: str, api_key: str) -> Optional[dict]:
    term = episode_title.replace('"', " ").strip()[:120]
    # searchId is REQUIRED in the selection set — Taddy 400s without it.
    query = f"""
    query {{
      search(term:"{term}", filterForTypes:PODCASTEPISODE, limitPerPage:8) {{
        searchId
        podcastEpisodes {{ uuid name datePublished podcastSeries {{ uuid name }} }}
      }}
    }}
    """
    data = taddy_query(query, user_id=user_id, api_key=api_key)
    episodes = (data.get("search") or {}).get("podcastEpisodes") or []
    want_title = episode_title.lower()
    want_show = (show_name or "").lower()
    best, best_score = None, 0.0
    for ep in episodes:
        title_ratio = difflib.SequenceMatcher(None, want_title, (ep.get("name") or "").lower()).ratio()
        series = ((ep.get("podcastSeries") or {}).get("name") or "").lower()
        show_ratio = difflib.SequenceMatcher(None, want_show, series).ratio() if want_show else 0.5
        score = title_ratio * 0.7 + show_ratio * 0.3
        if title_ratio >= TADDY_TITLE_MIN_RATIO and score > best_score:
            best, best_score = ep, score
    return best


def taddy_transcript_text(episode_uuid: str, user_id: str, api_key: str) -> Optional[str]:
    paragraphs = get_episode_transcript(episode_uuid, user_id=user_id, api_key=api_key)
    text = "\n\n".join(p.get("text", "") for p in paragraphs).strip()
    return text if len(text) >= MIN_FULL_TRANSCRIPT_CHARS else None


def try_taddy_full(title: str, show: str, user_id: str, api_key: str) -> tuple[Optional[dict], Optional[str]]:
    """Best-effort Taddy upgrade. Any Taddy failure degrades to (None, None) — the
    honest fallbacks (clip text / show notes) exist precisely for that, so a Taddy
    hiccup must never kill the item."""
    try:
        hit = taddy_find_episode(title, show, user_id, api_key)
        full = taddy_transcript_text(hit["uuid"], user_id, api_key) if hit else None
        return hit, full
    except Exception as exc:  # noqa: BLE001
        log.warning("taddy lookup failed for %r (%s) — falling back: %s", title[:50], show, exc)
        return None, None


# ── link metadata (castro.fm / spotify episode pages) ────────────────────────

def parse_og(html: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+property="og:{prop}"[^>]+content="([^"]*)"', html) or \
        re.search(rf'<meta[^>]+content="([^"]*)"[^>]+property="og:{prop}"', html)
    return (m.group(1) if m else "").strip()


def scrape_link_meta(url: str) -> dict:
    import html as html_mod

    title = notes = ""
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if api_key:
        # Firecrawl first: castro.fm TLS-resets repeated raw-httpx hits (it served
        # the dry-run, then started refusing) — the proxy absorbs that.
        try:
            from pipeline.scrapers.blog.import_blog import scrape_post
            meta = scrape_post(url, api_key)["metadata"]
            title = (meta.get("ogTitle") or meta.get("title") or "").strip()
            notes = (meta.get("ogDescription") or meta.get("description") or "").strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("firecrawl scrape failed for %s — raw fallback: %s", url, exc)
    if not title:
        resp = httpx.get(url, follow_redirects=True, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (list-maker save_episode)"})
        resp.raise_for_status()
        page = resp.text
        title = parse_og(page, "title") or (re.search(r"<title>([^<]+)</title>", page) or [None, ""])[1]
        notes = parse_og(page, "description")
    title = html_mod.unescape(title).strip()
    notes = html_mod.unescape(notes).strip()
    show = ""
    if "castro.fm" in url:
        # Castro og:title = "{series}: {episode} (1h51m)" — but series AND episode
        # names can themselves contain colons, so a single split point is ambiguous.
        # Return both split candidates; the caller tries Taddy with each.
        title = re.sub(r"\s*\((?:\d+h)?\d+m\)$", "", title)
        if ":" in title:
            first_show, first_title = (s.strip() for s in title.split(":", 1))
            last_show, last_title = (s.strip() for s in title.rsplit(":", 1))
            return {"title": first_title, "show": first_show, "notes": notes,
                    "alt": {"title": last_title, "show": last_show}}
    return {"title": title, "show": show, "notes": notes}


# ── Neon upsert (dedupe by title within the catch-all show) ──────────────────

def upsert_oneoff(conn, title: str, source_name: str, url_key: str,
                  publish_date: date, text: str, source_type: str) -> tuple[int, bool, bool]:
    """Returns (episode_id, created, upgraded). Dedupe by case-insensitive title —
    the same episode can arrive twice (a clip AND a note link) with different
    url keys; title identity is what makes it one page."""
    show_id = get_show(SAVED_SLUG).show_id
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.id, et.source_type
            FROM episodes ep LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE ep.show_id = %s AND lower(ep.title) = lower(%s)
            """,
            (show_id, title),
        )
        row = cur.fetchone()
        if row:
            episode_id, upgraded = row["id"], False
            # Upgrade an excerpt page to a full transcript; never downgrade.
            if source_type == "taddy_transcript" and row["source_type"] != "taddy_transcript":
                cur.execute(
                    "UPDATE episode_transcripts SET transcript_text=%s, source_type=%s, "
                    "updated_at=now(), notion_transcript_page_id=NULL, notion_transcript_synced_at=NULL "
                    "WHERE episode_id=%s",
                    (text, source_type, episode_id),
                )
                upgraded = True
            conn.commit()
            return episode_id, False, upgraded
        raw = json.dumps({"provider": "oneoff_episode", "source_name": source_name})
        cur.execute(
            """
            INSERT INTO episodes (show_id, title, url, publish_date, scraped_at, raw_content)
            VALUES (%s, %s, %s, %s, now(), %s)
            ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title
            RETURNING id
            """,
            (show_id, title, url_key, publish_date, raw),
        )
        episode_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO episode_transcripts (episode_id, source_type, source_url, transcript_text) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (episode_id) DO UPDATE "
            "SET transcript_text = EXCLUDED.transcript_text, source_type = EXCLUDED.source_type, updated_at = now()",
            (episode_id, source_type, url_key, text),
        )
    conn.commit()
    return episode_id, True, False


def sync_saved_pages() -> None:
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "sync_transcripts_notion.py"),
         "--target", "transcripts", "--shows", SAVED_SLUG],
        check=True, cwd=str(Path(__file__).resolve().parent),
    )


def page_id_for(conn, episode_id: int) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT notion_transcript_page_id FROM episode_transcripts WHERE episode_id=%s",
                    (episode_id,))
        row = cur.fetchone()
        return row["notion_transcript_page_id"] if row else None


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:  # noqa: PLR0915 — an orchestrator reads better linear than shredded
    p = argparse.ArgumentParser(description="One-off podcast episodes → Saved Episodes pages")
    p.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    p.add_argument("--links-file", default=str(DEFAULT_LINKS_FILE))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_environment()
    token = os.getenv("NOTION_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")
    taddy_user, taddy_key = os.getenv("TADDY_USER_ID"), os.getenv("TADDY_API_KEY")
    if not (token and openai_key and taddy_user and taddy_key):
        raise SystemExit("NOTION_TOKEN, OPENAI_API_KEY and TADDY_USER_ID/API_KEY are required")

    manifest = load_manifest()
    buckets = {"taddy_full": 0, "clip_only": 0, "notes_only": 0, "done_before": 0,
               "already_in_db": 0, "failed": []}
    pending_highlights: list[dict] = []
    done = 0

    conn = get_db_connection()
    try:
        # ── Pass A: unmatched clips ──────────────────────────────────────────
        clips_dir = Path(os.path.expanduser(args.clips_dir))
        for cid, path in (discover_clips(clips_dir) if clips_dir.is_dir() else {}).items():
            if cid in manifest:
                buckets["done_before"] += 1
                continue
            if args.limit and done >= args.limit:
                break
            try:
                tags = probe_tags(path)
                if match_slug(tags["publisher"]):
                    continue  # in-DB shows belong to highlight_clips.py
                show_name = tags["publisher"].split(" (")[0].strip()
                title = tags["episode_title"]
                if args.dry_run:
                    print(f"DRY-RUN clip: {title[:55]!r} ({show_name})")
                    done += 1
                    continue
                hit, full = try_taddy_full(title, show_name, taddy_user, taddy_key)
                audio = extract_audio(path, CACHE_DIR / f"castro-{cid}.m4a")
                clip_text = transcribe(audio, openai_key)
                text = full or clip_text
                source = "taddy_transcript" if full else "castro_clip"
                pub = (epoch_to_date(hit.get("datePublished")) if hit else None) \
                    or (date.fromisoformat(tags["clipped_at"]) if tags["clipped_at"] else date.today())
                episode_id, _, _ = upsert_oneoff(conn, title, show_name, f"castro://clip/{cid}",
                                                 pub, text, source)
                buckets["taddy_full" if full else "clip_only"] += 1
                pending_highlights.append({"cid": cid, "episode_id": episode_id, "tags": tags,
                                           "audio": audio, "clip_text": clip_text, "full_text": text})
                done += 1
            except Exception as exc:  # noqa: BLE001
                buckets["failed"].append(f"{path.name}: {exc}")
                log.error("FAILED clip %s: %s", path.name, exc)

        # ── Pass B: episode links from Apple Notes ───────────────────────────
        links_file = Path(os.path.expanduser(args.links_file))
        links = [ln.split()[0] for ln in links_file.read_text().splitlines()
                 if ln.strip() and not ln.startswith("#")] if links_file.exists() else []
        for url in links:
            if args.limit and done >= args.limit:
                break
            try:
                meta = scrape_link_meta(url)
                if not meta["title"]:
                    raise RuntimeError("no title in page metadata")
                if args.dry_run:
                    print(f"DRY-RUN link: {meta['title'][:55]!r} ({meta['show'] or '?'}) {url}")
                    done += 1
                    continue
                in_db_slug = match_slug(meta["show"])
                if in_db_slug:
                    # A link to a show we already carry (e.g. AI Daily) — its episode
                    # belongs to that show, not the catch-all. Skip if present.
                    from pipeline.highlight_clips import find_episode
                    if find_episode(conn, in_db_slug, meta["title"]):
                        buckets["already_in_db"] += 1
                        done += 1
                        log.info("already in DB under %s: %r", in_db_slug, meta["title"][:50])
                        continue
                hit, full = try_taddy_full(meta["title"], meta["show"], taddy_user, taddy_key)
                if not hit and meta.get("alt"):
                    # Ambiguous colon split: retry with the other candidate, and
                    # adopt it wholesale on a hit (its names are the clean ones).
                    hit, full = try_taddy_full(meta["alt"]["title"], meta["alt"]["show"],
                                               taddy_user, taddy_key)
                    if hit:
                        meta["title"], meta["show"] = meta["alt"]["title"], meta["alt"]["show"]
                show_name = meta["show"] or ((hit.get("podcastSeries") or {}).get("name") if hit else "") or "Podcast"
                text = full or meta["notes"] or meta["title"]
                source = "taddy_transcript" if full else "show_notes"
                pub = (epoch_to_date(hit.get("datePublished")) if hit else None) or date.today()
                _, created, upgraded = upsert_oneoff(conn, meta["title"], show_name, url, pub, text, source)
                buckets["taddy_full" if full else "notes_only"] += 1
                done += 1
                log.info("link %s: %r (%s)", "created" if created else ("upgraded" if upgraded else "existed"),
                         meta["title"][:50], source)
            except Exception as exc:  # noqa: BLE001
                buckets["failed"].append(f"{url}: {exc}")
                log.error("FAILED link %s: %s", url, exc)

        if args.dry_run:
            print(f"\nDRY-RUN: {done} item(s) would process")
            return

        # ── pages, then highlights on top ────────────────────────────────────
        sync_saved_pages()
        for item in pending_highlights:
            cid = item["cid"]
            try:
                page_id = page_id_for(conn, item["episode_id"])
                if not page_id:
                    raise RuntimeError("page not created by sync")
                blocks = page_blocks(token, page_id)
                if existing_highlight(blocks, cid):
                    manifest[cid] = {"episode_id": item["episode_id"], "page_id": page_id,
                                     "title": item["tags"]["episode_title"], "adopted": True}
                    save_manifest(manifest)
                    continue
                span = locate_span(item["clip_text"], item["full_text"])
                jump = None
                if span:
                    anchor = find_anchor_block(blocks, span["head"])
                    if anchor:
                        jump = anchor_url(page_id, anchor)
                fid = upload_audio(token, item["audio"])
                callout = build_highlight(cid, item["tags"], fid,
                                          quote_from_span(item["clip_text"]), jump)
                insert_highlight(token, page_id, blocks[0]["id"], callout)
                manifest[cid] = {"episode_id": item["episode_id"], "page_id": page_id,
                                 "title": item["tags"]["episode_title"], "anchored": bool(jump)}
                save_manifest(manifest)
            except Exception as exc:  # noqa: BLE001
                buckets["failed"].append(f"highlight castro {cid}: {exc}")
                log.error("FAILED highlight castro %s: %s", cid, exc)
    finally:
        conn.close()

    print(f"\ntaddy_full {buckets['taddy_full']}, clip_only {buckets['clip_only']}, "
          f"notes_only {buckets['notes_only']}, already_in_db {buckets['already_in_db']}, "
          f"done_before {buckets['done_before']}, failed {len(buckets['failed'])}")
    for item in buckets["failed"]:
        print(f"  - {item}")
    if buckets["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

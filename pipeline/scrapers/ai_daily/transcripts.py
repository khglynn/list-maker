#!/usr/bin/env python3
"""
AI Daily Brief transcript backfill + refresh.

What this script does:
1. Pull the latest episodes from podcast RSS.
2. Upsert show/episode records in Neon.
3. Get transcript from official URL when available.
4. Otherwise generate a full transcript from audio via OpenAI STT.
5. Save transcript to BOTH:
   - Neon: episode_transcripts table
   - Local cache: pipeline/_cache/ai_daily/transcripts/

Usage:
    python transcripts.py --limit 25
    python transcripts.py --limit 25 --dry-run
    python transcripts.py --limit 25 --force
    python transcripts.py --limit 300 --since-date 2025-08-08 --max-new 10
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

DEFAULT_FEED_URL = "https://anchor.fm/s/f7cac464/podcast/rss"
SHOW_NAME = "The AI Daily Brief: Artificial Intelligence News and Analysis"
SHOW_SLUG = "ai-daily-brief"
SHOW_WEBSITE = "https://www.aidailybrief.ai/"

OPENAI_STT_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_STT_MODEL = "whisper-1"
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
CHUNK_UPLOAD_BYTES = 18 * 1024 * 1024

REQUEST_TIMEOUT = 60
AUDIO_TIMEOUT = 300
TRANSCRIBE_TIMEOUT = 1800


# -----------------------------------------------------------------------------
# Data shapes
# -----------------------------------------------------------------------------

@dataclass
class FeedEpisode:
    title: str
    link: str
    guid: str
    publish_date: Optional[date]
    audio_url: Optional[str]
    official_transcript_url: Optional[str]


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def local_name(tag: str) -> str:
    """Return XML local name without namespace."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def to_slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return clean[:120] if clean else "episode"


def parse_pub_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date()
    except Exception:
        return None


def strip_html(raw_html: str) -> str:
    """Very small HTML -> text cleaner for transcript pages."""
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\\s*>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def convert_vtt_or_srt(raw_text: str) -> str:
    """Convert subtitle-like text to plain transcript."""
    lines = raw_text.splitlines()
    keep: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.upper().startswith("WEBVTT"):
            continue
        if re.match(r"^\\d+$", s):
            continue
        if "-->" in s:
            continue
        if re.match(r"^\\d{2}:\\d{2}:\\d{2}[\\.,]\\d{3}$", s):
            continue
        keep.append(s)
    text = "\n".join(keep)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -----------------------------------------------------------------------------
# Feed + transcript fetch
# -----------------------------------------------------------------------------

def fetch_feed(feed_url: str, limit: int) -> list[FeedEpisode]:
    resp = requests.get(feed_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    channel = root.find("channel")
    if channel is None:
        raise RuntimeError("RSS feed does not contain channel")

    episodes: list[FeedEpisode] = []
    for item in channel.findall("item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link or title).strip()
        pub_date = parse_pub_date((item.findtext("pubDate") or "").strip())

        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url") if enclosure is not None else None

        official_transcript_url = None
        for child in item:
            if local_name(child.tag) == "transcript":
                official_transcript_url = child.attrib.get("url")
                if official_transcript_url:
                    break

        episodes.append(
            FeedEpisode(
                title=title,
                link=link,
                guid=guid,
                publish_date=pub_date,
                audio_url=audio_url,
                official_transcript_url=official_transcript_url,
            )
        )

    return episodes


def fetch_official_transcript(transcript_url: str) -> Optional[str]:
    if not transcript_url:
        return None

    resp = requests.get(transcript_url, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 400:
        return None

    content_type = (resp.headers.get("content-type") or "").lower()
    body = resp.text.strip()
    if not body:
        return None

    if transcript_url.endswith(".vtt") or "text/vtt" in content_type:
        return convert_vtt_or_srt(body)
    if transcript_url.endswith(".srt") or "application/x-subrip" in content_type:
        return convert_vtt_or_srt(body)
    return strip_html(body)


def transcribe_with_openai(audio_url: str, model: str, api_key: str) -> str:
    if not audio_url:
        raise RuntimeError("No audio URL provided for STT")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for STT")

    def transcribe_file(path: Path) -> str:
        headers = {"Authorization": f"Bearer {api_key}"}
        data = {"model": model}
        with path.open("rb") as f:
            files = {"file": (path.name, f, "audio/mpeg")}
            resp = requests.post(
                OPENAI_STT_ENDPOINT,
                headers=headers,
                data=data,
                files=files,
                timeout=TRANSCRIBE_TIMEOUT,
            )
        if resp.status_code == 413:
            raise RuntimeError("OpenAI STT payload too large (413)")
        resp.raise_for_status()
        payload = resp.json()
        text = (payload.get("text") or "").strip()
        if not text:
            raise RuntimeError("OpenAI STT returned empty transcript")
        return text

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as tmp:
        with requests.get(audio_url, stream=True, timeout=AUDIO_TIMEOUT) as audio_resp:
            audio_resp.raise_for_status()
            for chunk in audio_resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    tmp.write(chunk)
        tmp.flush()
        tmp_path = Path(tmp.name)
        file_size = tmp_path.stat().st_size

        # Fast path: single upload.
        if file_size <= MAX_UPLOAD_BYTES:
            return transcribe_file(tmp_path)

        # Fallback: byte-chunk uploads for large files.
        # This avoids hard-failing on long episodes that exceed upload limits.
        chunk_texts: list[str] = []
        with tmp_path.open("rb") as src:
            chunk_index = 0
            while True:
                data = src.read(CHUNK_UPLOAD_BYTES)
                if not data:
                    break
                chunk_index += 1
                with tempfile.NamedTemporaryFile(suffix=f".part{chunk_index}.mp3", delete=True) as part:
                    part.write(data)
                    part.flush()
                    part_path = Path(part.name)
                    text = transcribe_file(part_path)
                    chunk_texts.append(text)

        full_text = "\n\n".join(chunk_texts).strip()
        if not full_text:
            raise RuntimeError("Chunked STT produced empty transcript")
        return full_text


# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------

def get_db_connection():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: psycopg2-binary. "
            "Install deps with `cd pipeline && pip install -r requirements.txt`."
        ) from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS episode_transcripts (
              id SERIAL PRIMARY KEY,
              episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
              source_type VARCHAR(64) NOT NULL,
              source_url TEXT,
              transcript_text TEXT NOT NULL,
              is_generated BOOLEAN NOT NULL DEFAULT FALSE,
              model VARCHAR(128),
              created_at TIMESTAMP DEFAULT NOW(),
              updated_at TIMESTAMP DEFAULT NOW(),
              UNIQUE (episode_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_transcripts_source_type
              ON episode_transcripts(source_type);
            """
        )
    conn.commit()


def upsert_show(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shows (name, slug, website_url, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (slug) DO UPDATE
              SET name = EXCLUDED.name,
                  website_url = EXCLUDED.website_url
            RETURNING id;
            """,
            (SHOW_NAME, SHOW_SLUG, SHOW_WEBSITE),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def upsert_episode(conn, show_id: int, ep: FeedEpisode) -> int:
    episode_url = ep.link or ep.guid
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episodes (
              show_id, title, url, publish_date, scraped_at, created_at
            )
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (url) DO UPDATE
              SET show_id = EXCLUDED.show_id,
                  title = EXCLUDED.title,
                  publish_date = EXCLUDED.publish_date,
                  scraped_at = NOW()
            RETURNING id;
            """,
            (show_id, ep.title, episode_url, ep.publish_date),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def transcript_exists(conn, episode_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM episode_transcripts WHERE episode_id = %s LIMIT 1;",
            (episode_id,),
        )
        return cur.fetchone() is not None


def upsert_episode_transcript(
    conn,
    episode_id: int,
    transcript_text: str,
    source_type: str,
    source_url: Optional[str],
    is_generated: bool,
    model: Optional[str],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episode_transcripts (
              episode_id, source_type, source_url, transcript_text, is_generated, model, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (episode_id) DO UPDATE
              SET source_type = EXCLUDED.source_type,
                  source_url = EXCLUDED.source_url,
                  transcript_text = EXCLUDED.transcript_text,
                  is_generated = EXCLUDED.is_generated,
                  model = EXCLUDED.model,
                  updated_at = NOW();
            """,
            (episode_id, source_type, source_url, transcript_text, is_generated, model),
        )
    conn.commit()


# -----------------------------------------------------------------------------
# Local transcript cache
# -----------------------------------------------------------------------------

def write_cache(
    cache_dir: Path,
    episode_id: int,
    episode_title: str,
    transcript_text: str,
    source_type: str,
    source_url: Optional[str],
    is_generated: bool,
    model: Optional[str],
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{episode_id}-{to_slug(episode_title)}"
    txt_path = cache_dir / f"{stem}.txt"
    meta_path = cache_dir / f"{stem}.json"

    txt_path.write_text(transcript_text, encoding="utf-8")
    meta = {
        "episode_id": episode_id,
        "title": episode_title,
        "source_type": source_type,
        "source_url": source_url,
        "is_generated": is_generated,
        "model": model,
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "text_file": txt_path.name,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def run(
    feed_url: str,
    limit: int,
    model: str,
    since_date: Optional[date],
    max_new: int,
    summary_out: Optional[Path],
    force: bool,
    dry_run: bool,
) -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    repo_root = Path(__file__).resolve().parents[3]
    cache_dir = repo_root / "pipeline" / "_cache" / "ai_daily" / "transcripts"

    print(f"Fetching feed: {feed_url}", flush=True)
    episodes = fetch_feed(feed_url=feed_url, limit=limit)
    print(f"Found {len(episodes)} episodes in feed window", flush=True)
    if since_date:
        episodes = [e for e in episodes if e.publish_date and e.publish_date >= since_date]
        print(
            f"After since-date filter ({since_date.isoformat()}): {len(episodes)} episodes",
            flush=True,
        )

    conn = get_db_connection()
    try:
        ensure_schema(conn)
        show_id = upsert_show(conn)
        print(f"Using show_id={show_id} ({SHOW_SLUG})", flush=True)

        created = 0
        skipped = 0
        filtered = 0
        created_episode_ids: list[int] = []
        skipped_episode_ids: list[int] = []
        failed_episode_ids: list[int] = []
        for idx, ep in enumerate(episodes, start=1):
            if max_new > 0 and created >= max_new:
                print(f"Reached --max-new={max_new}; stopping early.", flush=True)
                break

            if since_date and ep.publish_date and ep.publish_date < since_date:
                filtered += 1
                continue

            episode_id = upsert_episode(conn, show_id, ep)
            already = transcript_exists(conn, episode_id)
            if already and not force:
                skipped += 1
                skipped_episode_ids.append(episode_id)
                print(f"[{idx}/{len(episodes)}] Skip existing: {ep.title}", flush=True)
                continue

            transcript_text = None
            source_type = None
            source_url = None
            is_generated = False
            model_used = None

            # 1) Official transcript first (if present)
            if ep.official_transcript_url:
                try:
                    official_text = fetch_official_transcript(ep.official_transcript_url)
                    if official_text and len(official_text) > 500:
                        transcript_text = official_text
                        source_type = "official_transcript"
                        source_url = ep.official_transcript_url
                        is_generated = False
                except Exception as e:
                    print(f"  Official transcript fetch failed: {e}", flush=True)

            # 2) Generate full transcript via OpenAI if needed
            if not transcript_text:
                if not ep.audio_url:
                    print(f"[{idx}/{len(episodes)}] Missing audio URL: {ep.title}", flush=True)
                    failed_episode_ids.append(episode_id)
                    continue
                if not api_key:
                    raise RuntimeError("OPENAI_API_KEY missing; cannot generate transcript")
                print(f"[{idx}/{len(episodes)}] Transcribing audio: {ep.title}", flush=True)
                transcript_text = transcribe_with_openai(ep.audio_url, model=model, api_key=api_key)
                source_type = "openai_stt"
                source_url = ep.audio_url
                is_generated = True
                model_used = model

            if dry_run:
                preview = transcript_text[:200].replace("\n", " ")
                print(f"  DRY RUN transcript ({source_type}): {preview}...", flush=True)
                continue

            upsert_episode_transcript(
                conn=conn,
                episode_id=episode_id,
                transcript_text=transcript_text,
                source_type=source_type,
                source_url=source_url,
                is_generated=is_generated,
                model=model_used,
            )
            write_cache(
                cache_dir=cache_dir,
                episode_id=episode_id,
                episode_title=ep.title,
                transcript_text=transcript_text,
                source_type=source_type,
                source_url=source_url,
                is_generated=is_generated,
                model=model_used,
            )
            created += 1
            created_episode_ids.append(episode_id)
            print(f"  Saved transcript for episode_id={episode_id} ({source_type})", flush=True)

        print("", flush=True)
        print("Done.", flush=True)
        print(f"Saved/updated transcripts: {created}", flush=True)
        print(f"Skipped existing: {skipped}", flush=True)
        print(f"Failed/missing audio: {len(failed_episode_ids)}", flush=True)
        print(f"Local transcript cache: {cache_dir}", flush=True)

        if summary_out:
            summary_out.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "generated_at_utc": datetime.utcnow().isoformat() + "Z",
                "feed_url": feed_url,
                "limit": limit,
                "since_date": since_date.isoformat() if since_date else None,
                "max_new": max_new,
                "model": model,
                "force": force,
                "dry_run": dry_run,
                "saved_or_updated_count": created,
                "skipped_existing_count": skipped,
                "failed_count": len(failed_episode_ids),
                "filtered_count": filtered,
                "created_episode_ids": created_episode_ids,
                "skipped_episode_ids": skipped_episode_ids,
                "failed_episode_ids": failed_episode_ids,
            }
            summary_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"Summary JSON: {summary_out}", flush=True)
    finally:
        conn.close()


def load_environment() -> None:
    """Load env from common project locations."""
    repo_root = Path(__file__).resolve().parents[3]
    pipeline_root = Path(__file__).resolve().parents[2]

    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(pipeline_root / ".env.local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill AI Daily Brief transcripts")
    parser.add_argument("--limit", type=int, default=25, help="Episodes to process (default: 25)")
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL, help="Podcast RSS feed URL")
    parser.add_argument("--model", default=DEFAULT_STT_MODEL, help="OpenAI transcription model")
    parser.add_argument(
        "--since-date",
        type=str,
        default="",
        help="Only process episodes on/after YYYY-MM-DD (publish_date based)",
    )
    parser.add_argument(
        "--max-new",
        type=int,
        default=0,
        help="Stop after creating/updating this many transcripts (0 = no cap)",
    )
    parser.add_argument(
        "--summary-out",
        type=str,
        default="",
        help="Optional JSON path to write run summary (created IDs, skipped IDs, etc.)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing transcripts")
    parser.add_argument("--dry-run", action="store_true", help="No database/file writes")
    return parser.parse_args()


if __name__ == "__main__":
    load_environment()
    args = parse_args()
    since_date_value: Optional[date] = None
    if args.since_date.strip():
        try:
            since_date_value = date.fromisoformat(args.since_date.strip())
        except ValueError as exc:
            print(f"Error: invalid --since-date '{args.since_date}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)
    summary_out_path: Optional[Path] = None
    if args.summary_out.strip():
        summary_out_path = Path(args.summary_out).expanduser().resolve()
    try:
        run(
            feed_url=args.feed_url,
            limit=args.limit,
            model=args.model,
            since_date=since_date_value,
            max_new=args.max_new,
            summary_out=summary_out_path,
            force=args.force,
            dry_run=args.dry_run,
        )
    except requests.exceptions.RequestException as exc:
        print(f"Network error while fetching feed/audio/transcript: {exc}", file=sys.stderr)
        print("Check internet connection and feed URL, then retry.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Castro podcast clips → audio highlights on the episode's Notion transcript page.

Kevin clips podcast moments in Castro; the exports (.MOV: audio + static artwork)
carry full embedded metadata — show, exact episode title, clip date. For episodes
already in the DB this pipeline:

  1. identifies the episode from ffprobe tags (no transcription needed to match)
  2. extracts audio-only m4a (ffmpeg; ~5% of the .MOV size)
  3. transcribes the CLIP with Whisper (existing OPENAI_API_KEY — only needed to
     find the in/out points; the full transcript already lives in Neon)
  4. locates the span in the episode transcript + the Notion paragraph block
     containing the in-point
  5. uploads the audio to Notion (file-upload API — Notion is the audio's
     permanent home; the .MOV originals stay Kevin's to archive) and inserts a
     highlight callout right after the page intro: player + quote + an anchor
     link that jumps to the spot in the transcript

LOCAL-ONLY (clips live on Kevin's machine) and idempotent: a manifest maps
processed clip ids → created blocks; duplicate exports ("… 2.MOV") dedupe by
Castro clip id. Clips from shows not in the DB are reported, not processed —
they're the one-off podcast-ingest backlog.

    ./venv/bin/python highlight_clips.py --dry-run     # match + bucket report
    ./venv/bin/python highlight_clips.py --limit 1     # one clip end-to-end
    ./venv/bin/python highlight_clips.py               # everything new
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, get_logger, load_environment  # noqa: E402
from pipeline.sync_notion import NOTION_API, notion_request  # noqa: E402

DEFAULT_CLIPS_DIR = Path.home() / "Downloads" / "Podcast Clips"
CACHE_DIR = Path(__file__).resolve().parent / "_cache" / "podcast-clips"
MANIFEST_PATH = CACHE_DIR / "manifest.json"

# Castro's `publisher` tag → our show slug. Substring match, lowercase.
PUBLISHER_TO_SLUG = {
    "ai daily brief": "ai-daily-brief",
    "ai breakdown": "ai-daily-brief",   # the show's former name still appears in old clips
    "hard fork": "hard-fork",
}

MATCH_TITLE_MIN_RATIO = 0.85   # fuzzy episode-title match threshold
SPAN_MIN_RATIO = 0.60          # min similarity to trust a located in/out point
QUOTE_CAP = 240                # chars per side of the highlight quote

log = get_logger("pipeline.highlight_clips")


# ── clip discovery + metadata ────────────────────────────────────────────────

def castro_clip_id(filename: str) -> Optional[str]:
    match = re.match(r"castro-clip-(\d+)", filename)
    return match.group(1) if match else None


def discover_clips(clips_dir: Path) -> dict[str, Path]:
    """Map castro id -> file path; duplicate exports ('… 2.MOV') collapse to one."""
    clips: dict[str, Path] = {}
    for path in sorted(clips_dir.iterdir()):
        if path.suffix.lower() != ".mov":
            continue
        cid = castro_clip_id(path.name)
        if cid:
            clips.setdefault(cid, path)
    return clips


def probe_tags(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    fmt = json.loads(result.stdout)["format"]
    tags = fmt.get("tags", {})
    return {
        "publisher": tags.get("com.apple.quicktime.publisher", "") or tags.get("original_source", ""),
        "episode_title": tags.get("com.apple.quicktime.displayname", ""),
        "clipped_at": (tags.get("com.apple.quicktime.creationdate", "") or "")[:10],
        "duration_s": int(float(fmt.get("duration", 0))),
    }


def match_slug(publisher: str) -> Optional[str]:
    low = (publisher or "").lower()
    for needle, slug in PUBLISHER_TO_SLUG.items():
        if needle in low:
            return slug
    return None


# ── episode matching ─────────────────────────────────────────────────────────

def find_episode(conn, slug: str, episode_title: str) -> Optional[dict]:
    """Exact (case-insensitive) title match first, then best fuzzy >= threshold."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ep.id AS episode_id, ep.title, et.notion_transcript_page_id AS page_id,
                   et.transcript_text
            FROM episodes ep
            JOIN shows s ON s.id = ep.show_id
            LEFT JOIN episode_transcripts et ON et.episode_id = ep.id
            WHERE s.slug = %s
            """,
            (slug,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    want = episode_title.strip().lower()
    for row in rows:
        if (row["title"] or "").strip().lower() == want:
            return row
    best, best_ratio = None, 0.0
    for row in rows:
        ratio = difflib.SequenceMatcher(None, want, (row["title"] or "").lower()).ratio()
        if ratio > best_ratio:
            best, best_ratio = row, ratio
    return best if best_ratio >= MATCH_TITLE_MIN_RATIO else None


# ── audio + transcription ────────────────────────────────────────────────────

def extract_audio(src: Path, dst: Path) -> Path:
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-c:a", "copy", str(dst)],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # Some exports carry non-AAC audio — re-encode instead of stream-copy.
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-c:a", "aac", str(dst)],
                       check=True, capture_output=True)
    return dst


def transcribe(path: Path, api_key: str) -> str:
    with open(path, "rb") as fh:
        resp = httpx.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, fh, "audio/mp4")},
            data={"model": "whisper-1", "language": "en"},
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()["text"]


# ── locating the span ────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def locate_span(clip_text: str, transcript: str) -> Optional[dict]:
    """Find where the clip sits in the full transcript via its head/tail phrases.

    Whisper's clip transcription and the show's transcript differ in punctuation
    and small word choices, so this is fuzzy: longest-common-block matching of the
    first/last ~12 words. Returns None when even the head can't be trusted —
    better no anchor than a wrong one.
    """
    t_norm = normalize(transcript)
    words = normalize(clip_text).split()
    if len(words) < 8:
        return None
    head = " ".join(words[:12])
    tail = " ".join(words[-12:])

    def best_pos(needle: str, hay: str, lo: int = 0) -> Optional[int]:
        seg = hay[lo:]
        m = difflib.SequenceMatcher(None, seg, needle, autojunk=False).find_longest_match(
            0, len(seg), 0, len(needle))
        if m.size < int(len(needle) * SPAN_MIN_RATIO):
            return None
        return lo + m.a - m.b  # back out to the needle's would-be start

    in_pos = best_pos(head, t_norm)
    if in_pos is None:
        return None
    out_pos = best_pos(tail, t_norm, lo=max(in_pos, 0))
    return {
        "in_norm_pos": max(in_pos, 0),
        "head": head,
        "tail": tail if out_pos is not None else None,
    }


def quote_from_span(clip_text: str) -> str:
    """The highlight quote comes from the clip's own transcription (clean, exact)."""
    text = clip_text.strip()
    if len(text) <= QUOTE_CAP * 2:
        return text
    return f"{text[:QUOTE_CAP].rsplit(' ', 1)[0]} […] {text[-QUOTE_CAP:].split(' ', 1)[-1]}"


# ── Notion: page blocks, upload, highlight insert ────────────────────────────

def page_blocks(token: str, page_id: str) -> list[dict]:
    blocks, cursor = [], None
    while True:
        url = f"{NOTION_API}/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        result = notion_request("GET", url, token, None)
        for b in result.get("results", []):
            rich = b.get(b.get("type"), {}).get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            blocks.append({"id": b["id"], "type": b["type"], "text": text})
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return blocks


def existing_highlight(blocks: list[dict], cid: str) -> bool:
    """Adopt-don't-duplicate: the castro id lives in each callout's header, so the
    page itself records what's been inserted. Closes the crash window between
    insert_highlight and save_manifest — a re-run adopts instead of duplicating.
    \\b after the id: a bare substring check would falsely adopt prefix ids
    (castro 123 inside castro 12345)."""
    pattern = re.compile(rf"castro {re.escape(cid)}\b")
    return any(b["type"] == "callout" and pattern.search(b["text"]) for b in blocks)


def find_anchor_block(blocks: list[dict], head: str) -> Optional[str]:
    """The paragraph block containing the clip's opening words. Substring on the
    first 8 normalized words; fuzzy best-block fallback."""
    probe = " ".join(head.split()[:8])
    for b in blocks:
        if b["type"] == "paragraph" and probe in normalize(b["text"]):
            return b["id"]
    best, best_ratio = None, 0.0
    for b in blocks:
        if b["type"] != "paragraph" or not b["text"]:
            continue
        ratio = difflib.SequenceMatcher(None, probe, normalize(b["text"]), autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = b["id"], ratio
    return best if best_ratio >= 0.10 else None  # ratio vs a ~1900-char block is naturally tiny


def anchor_url(page_id: str, block_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}#{block_id.replace('-', '')}"


def upload_audio(token: str, path: Path) -> str:
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    created = httpx.post(
        f"{NOTION_API}/file_uploads",
        headers={**headers, "Content-Type": "application/json"},
        json={"mode": "single_part", "filename": path.name, "content_type": "audio/mp4"},
        timeout=30,
    )
    created.raise_for_status()
    fid = created.json()["id"]
    with open(path, "rb") as fh:
        sent = httpx.post(f"{NOTION_API}/file_uploads/{fid}/send", headers=headers,
                          files={"file": (path.name, fh, "audio/mp4")}, timeout=300)
    sent.raise_for_status()
    if sent.json().get("status") != "uploaded":
        raise RuntimeError(f"upload not in 'uploaded' state for {path.name}")
    return fid


def fmt_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_highlight(cid: str, tags: dict, file_upload_id: str, quote: str,
                    jump_url: Optional[str]) -> dict:
    header = [
        {"type": "text", "text": {"content": "Kevin's clip"},
         "annotations": {"bold": True}},
        {"type": "text", "text": {"content":
            f" · {fmt_duration(tags['duration_s'])} · clipped {tags['clipped_at'] or '?'}"}},
        {"type": "text", "text": {"content": f" · castro {cid}"},
         "annotations": {"color": "gray"}},
    ]
    children: list[dict] = [
        {"object": "block", "type": "audio",
         "audio": {"type": "file_upload", "file_upload": {"id": file_upload_id}}},
        {"object": "block", "type": "quote",
         "quote": {"rich_text": [{"type": "text", "text": {"content": quote[:1900]}}]}},
    ]
    if jump_url:
        children.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{
                "type": "text",
                "text": {"content": "↪ Jump to this spot in the transcript", "link": {"url": jump_url}},
            }]},
        })
    return {"object": "block", "type": "callout",
            "callout": {"icon": {"type": "emoji", "emoji": "\U0001F399"},
                        "color": "gray_background", "rich_text": header, "children": children}}


def insert_highlight(token: str, page_id: str, after_block_id: str, callout: dict,
                     clips_count: int | None = None) -> None:
    notion_request("PATCH", f"{NOTION_API}/blocks/{page_id}/children", token,
                   {"children": [callout], "after": after_block_id})
    if clips_count is not None:
        # The Clips column is the at-a-glance "this episode has my moments" marker.
        notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token,
                       {"properties": {"Clips": {"number": clips_count}}})


def count_clip_callouts(blocks: list[dict]) -> int:
    return sum(1 for b in blocks if b["type"] == "callout" and "castro " in b["text"])


def set_clips_count(token: str, page_id: str, count: int) -> None:
    """Also called on ADOPT: a crash between callout-insert and this property PATCH
    would otherwise leave the count stale forever (the adopt path skips insert)."""
    notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token,
                   {"properties": {"Clips": {"number": count}}})


# ── manifest ─────────────────────────────────────────────────────────────────

def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def save_manifest(manifest: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Castro clips → Notion transcript highlights")
    p.add_argument("--clips-dir", default=str(DEFAULT_CLIPS_DIR))
    p.add_argument("--limit", type=int, default=0, help="Max clips to process this run")
    p.add_argument("--dry-run", action="store_true", help="Match + bucket report only")
    args = p.parse_args()

    load_environment()
    token = os.getenv("NOTION_TOKEN")
    api_key = os.getenv("OPENAI_API_KEY")
    if not (token and api_key):
        raise SystemExit("NOTION_TOKEN and OPENAI_API_KEY are required")

    clips_dir = Path(os.path.expanduser(args.clips_dir))
    if not clips_dir.is_dir():
        raise SystemExit(f"Clips folder not found: {clips_dir} — this pipeline is local-only.")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    clips = discover_clips(clips_dir)
    log.info("found %d unique clip(s) (%d already processed)", len(clips),
             sum(1 for c in clips if c in manifest))

    buckets = {"done_before": 0, "processed": 0, "unmatched_show": [], "unmatched_episode": [],
               "no_notion_page": [], "failed": []}
    processed_this_run = 0
    conn = get_db_connection()
    try:
        for cid, path in clips.items():
            if cid in manifest:
                buckets["done_before"] += 1
                continue
            if args.limit and processed_this_run >= args.limit:
                break
            try:
                tags = probe_tags(path)
                slug = match_slug(tags["publisher"])
                label = f"{tags['episode_title'][:50]!r} ({tags['publisher'][:30]})"
                if not slug:
                    buckets["unmatched_show"].append(label)
                    continue
                ep = find_episode(conn, slug, tags["episode_title"])
                if not ep:
                    buckets["unmatched_episode"].append(label)
                    continue
                if not ep.get("page_id"):
                    buckets["no_notion_page"].append(label)
                    continue
                if args.dry_run:
                    print(f"DRY-RUN would process: {label} -> ep {ep['episode_id']}")
                    buckets["processed"] += 1
                    processed_this_run += 1
                    continue

                # Blocks first: the adopt-check must run BEFORE the expensive steps
                # (Whisper, upload) so a crashed prior run costs nothing to heal.
                blocks = page_blocks(token, ep["page_id"])
                if not blocks:
                    raise RuntimeError("page has no blocks")
                if existing_highlight(blocks, cid):
                    set_clips_count(token, ep["page_id"], count_clip_callouts(blocks))
                    manifest[cid] = {"episode_id": ep["episode_id"], "page_id": ep["page_id"],
                                     "title": tags["episode_title"], "adopted": True}
                    save_manifest(manifest)
                    buckets["processed"] += 1
                    processed_this_run += 1
                    log.info("adopted existing highlight for castro %s (ep %s)", cid, ep["episode_id"])
                    continue
                audio = extract_audio(path, CACHE_DIR / f"castro-{cid}.m4a")
                clip_text = transcribe(audio, api_key)
                span = locate_span(clip_text, ep["transcript_text"] or "")
                jump = None
                if span:
                    anchor = find_anchor_block(blocks, span["head"])
                    if anchor:
                        jump = anchor_url(ep["page_id"], anchor)
                fid = upload_audio(token, audio)
                callout = build_highlight(cid, tags, fid, quote_from_span(clip_text), jump)
                insert_highlight(token, ep["page_id"], blocks[0]["id"], callout,
                                 clips_count=count_clip_callouts(blocks) + 1)

                manifest[cid] = {"episode_id": ep["episode_id"], "page_id": ep["page_id"],
                                 "title": tags["episode_title"], "anchored": bool(jump)}
                save_manifest(manifest)  # after EVERY clip — a crash loses nothing
                buckets["processed"] += 1
                processed_this_run += 1
                log.info("highlighted ep %s %s (anchor=%s)", ep["episode_id"], label, bool(jump))
            except Exception as exc:  # noqa: BLE001 — one bad clip must not strand the rest
                buckets["failed"].append(f"{path.name}: {exc}")
                log.error("FAILED %s: %s", path.name, exc)
    finally:
        conn.close()

    print(f"\nprocessed {buckets['processed']}, already done {buckets['done_before']}, "
          f"failed {len(buckets['failed'])}")
    for key in ("unmatched_show", "unmatched_episode", "no_notion_page", "failed"):
        if buckets[key]:
            print(f"\n{key} ({len(buckets[key])}):")
            for item in buckets[key]:
                print(f"  - {item}")
    if buckets["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

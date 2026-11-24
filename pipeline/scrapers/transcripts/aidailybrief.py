"""The AI Daily Brief's own transcript for an episode.

The show posts one markdown transcript per episode at
https://aidailybrief.ai/e/<YYYY-MM-DD>/transcript.md ("feed it to your own agent"),
keyed by the episode's date in US Eastern time. 39 of 39 episodes in the 45 days before
2026-11-23 had one. Its first line is "# <title> — Transcript (<date>)", and
"[HH:MM:SS]" markers start some paragraphs.

The lookup is by DATE, so the page can belong to a different episode: a date that
slips a day finds yesterday's, and a promo item can share a day with the main episode.
The title on the page's first line must match the feed's (base.MIN_TITLE_SIMILARITY),
otherwise the answer is NotYet and a later run asks again.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# pipeline/ on the path, as every module under scrapers/ does, so this imports the same
# way from the orchestrator, a CLI run and the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from feeds import USER_AGENT, FeedItem  # noqa: E402
from scrapers.transcripts.base import (  # noqa: E402
    MIN_TITLE_SIMILARITY,
    Failed,
    NotYet,
    Result,
    Transcript,
    join_paragraphs,
    sanity_check,
    title_similarity,
)

BASE_URL = "https://aidailybrief.ai/e/{day}/transcript.md"
SOURCE_TYPE = "publisher_transcript"
MODEL = "aidailybrief_md"
EASTERN = ZoneInfo("America/New_York")
TIMEOUT_SECONDS = 30

_HEADER_RE = re.compile(r"^#\s+(?P<title>.+?)\s+[—-]+\s+Transcript\s*\((?P<day>\d{4}-\d{2}-\d{2})\)\s*$")
_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]\s*")


def candidate_days(item: FeedItem) -> list[date]:
    """Eastern date first (the site's key), then the UTC date, then the Eastern day
    before and after. Distinct, in that order. The title check decides which is right."""
    if not item.published_at:
        return []
    eastern = item.published_at.astimezone(EASTERN).date()
    days: list[date] = []
    for d in (eastern, item.published_at.date(), eastern - timedelta(days=1), eastern + timedelta(days=1)):
        if d not in days:
            days.append(d)
    return days


def parse_markdown(markdown: str) -> tuple[Optional[str], str]:
    """(title from the header line or None, the body as plain paragraphs)."""
    lines = markdown.strip().splitlines()
    title = None
    if lines:
        m = _HEADER_RE.match(lines[0].strip())
        if m:
            title = m.group("title").strip()
            lines = lines[1:]
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        line = _TIMESTAMP_RE.sub("", line).strip()
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return title, join_paragraphs(paragraphs)


def fetch(item: FeedItem, session=None) -> Result:
    import requests

    getter = session or requests
    days = candidate_days(item)
    if not days:
        return Failed("the feed item has no parseable date, and this source is keyed by date")
    tried: list[str] = []
    for day in days:
        url = BASE_URL.format(day=day.isoformat())
        try:
            resp = getter.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            return NotYet(f"{url}: {exc.__class__.__name__}")
        if resp.status_code == 404:
            tried.append(f"{day} (none)")
            continue
        if resp.status_code != 200:
            return NotYet(f"{url}: HTTP {resp.status_code}")
        page_title, body = parse_markdown(resp.text)
        similarity = title_similarity(page_title, item.title)
        if similarity < MIN_TITLE_SIMILARITY:
            tried.append(f"{day} (another episode, title match {similarity})")
            continue
        problem = sanity_check(body, item.duration_seconds)
        if problem:
            return NotYet(f"{url}: {problem}")
        return Transcript(text=body, source_type=SOURCE_TYPE, model=MODEL, is_generated=True, source_url=url)
    return NotYet("no transcript page for this episode yet; tried " + ", ".join(tried))

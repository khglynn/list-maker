"""The AI Daily Brief's own transcript for an episode.

The show posts one markdown transcript per episode at
https://aidailybrief.ai/e/<YYYY-MM-DD>/transcript.md ("feed it to your own agent"),
keyed by the episode's date in US Eastern time. 39 of 39 episodes in the 45 days before
2026-11-23 had one. Its first line is "# <title> — Transcript (<date>)", and
"[HH:MM:SS]" markers start some paragraphs.

The lookup is by DATE, so a page can belong to another episode, and two review rounds of
PR #84 kept finding new ways a title rule let one through: yesterday's "Part 1" for
today's "Part 2" when today's page is late, a promo sharing a day with the main episode,
two episodes with one title. Titles are the wrong fingerprint (the site retitles pages,
and token matching scores a subset title 100). So the mechanism is now:
  - Only the episode's own Eastern day is asked. 39 of 39 episodes in the 45 days to
    2026-11-23 had their page there, and a late page is NotYet, retried next run, never a
    neighbour's page.
  - The page is accepted when its last "[HH:MM:SS]" marker lands where the episode's
    audio ends AND its title is not a stranger's (same_episode, below, has the bands,
    the floors and the measurements). The one promo
    that shared a day (6 minutes) reads 430% of its own length against its 27-minute
    neighbour's page.
  - A page with no markers, or an item with no duration, falls back to an exact title
    match (after normalising case and punctuation).
The page's own header date must also equal the day in its url.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# pipeline/ on the path, as every module under scrapers/ does, so this imports the same
# way from the orchestrator, a CLI run and the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from feeds import USER_AGENT, FeedItem  # noqa: E402
from scrapers.transcripts.base import (  # noqa: E402
    Failed,
    NotYet,
    Result,
    Transcript,
    join_paragraphs,
    normalize_title,
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


def eastern_day(item: FeedItem) -> Optional[date]:
    """The day the site keys the episode's page by."""
    return item.published_at.astimezone(EASTERN).date() if item.published_at else None


def last_marker_seconds(markdown: str) -> Optional[int]:
    """Where the page's last "[HH:MM:SS]" marker falls, in seconds; None without markers."""
    marks = [int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)  # [HH:MM:SS] or [MM:SS]
             for a, b, c in re.findall(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]", markdown)]
    return max(marks) if marks else None


def parse_markdown(markdown: str) -> tuple[Optional[str], Optional[str], str]:
    """(title from the header line, the header's date, the body as plain paragraphs).
    Title and date are None when the first line is not the expected header."""
    lines = markdown.strip().splitlines()
    title = page_day = None
    if lines:
        m = _HEADER_RE.match(lines[0].strip())
        if m:
            title, page_day = m.group("title").strip(), m.group("day")
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
    return title, page_day, join_paragraphs(paragraphs)


# The last marker sits a little before the end of the audio (outro, closing ad), never
# after it. Measured 2026-11-24 on the 62 newest episodes with a page: 96 to 100% for 53,
# down to 83% for a few with a long close.
STRONG_MARKER_SHARE = (0.95, 1.02)
LOOSE_MARKER_SHARE = (0.80, 1.02)
# Titles alone can't tell episodes apart (the site retitles: 62 for 2026-10-07's page
# against its feed title), and lengths alone can't either (two episodes on one day, one
# page ending early, could land on the other's length). Together they can. Between
# DIFFERENT episodes' titles, measured over 19,900 pairs of the newest 200 (2026-11-24):
# median 42, 99th percentile 60, and the one real same-day pair (2026-01-08) scores 32.
TITLE_FLOOR_STRONG = 45  # with a close length match
TITLE_FLOOR_LOOSE = 60   # with a looser one


def same_episode(markdown: str, page_title: Optional[str], item: FeedItem) -> tuple[bool, str]:
    """(is this page the item's episode, why not). See the module docstring.

    Known trade (review of PR #84, round 3): a retitled page that also ends early (under
    95% of the audio) with a title scoring under 60 is refused and the episode is
    extracted from show notes. Neither has been seen together; refusing is the safe side,
    since a wrong transcript would be stored for good.
    """
    last = last_marker_seconds(markdown)
    similarity = title_similarity(page_title, item.title) if page_title else 0
    if last is not None and item.duration_seconds:
        share = last / item.duration_seconds
        if STRONG_MARKER_SHARE[0] <= share <= STRONG_MARKER_SHARE[1] and similarity >= TITLE_FLOOR_STRONG:
            return True, ""
        if LOOSE_MARKER_SHARE[0] <= share <= LOOSE_MARKER_SHARE[1] and similarity >= TITLE_FLOOR_LOOSE:
            return True, ""
        return False, (f"the page runs to {last // 60} min against a {item.duration_seconds // 60}-min episode, "
                       f"title match {similarity} (another episode's page?)")
    if page_title and normalize_title(page_title) == normalize_title(item.title):
        return True, ""
    return False, "no time markers to match on, and the title differs"


def fetch(item: FeedItem, session=None) -> Result:
    import requests

    getter = session or requests
    day = eastern_day(item)
    if day is None:
        return Failed("the feed item has no parseable date, and this source is keyed by date")
    url = BASE_URL.format(day=day.isoformat())
    try:
        resp = getter.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        return NotYet(f"{url}: {exc.__class__.__name__}")
    if resp.status_code == 404:
        return NotYet(f"{url}: no transcript page yet")
    if resp.status_code == 429:
        return NotYet(f"{url}: HTTP 429", rate_limited=True)
    if resp.status_code != 200:
        return NotYet(f"{url}: HTTP {resp.status_code}")
    page_title, page_day, body = parse_markdown(resp.text)
    if page_day is not None and page_day != day.isoformat():
        return NotYet(f"{url}: the page is headed {page_day}")
    ok, why = same_episode(resp.text, page_title, item)
    if not ok:
        return NotYet(f"{url}: {why}")
    problem = sanity_check(body, item.duration_seconds)
    if problem:
        return NotYet(f"{url}: {problem}")
    return Transcript(text=body, source_type=SOURCE_TYPE, model=MODEL, is_generated=True, source_url=url)

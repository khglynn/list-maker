"""The AI Daily Brief's own transcript for an episode.

The show posts one markdown transcript per episode at
https://aidailybrief.ai/e/<YYYY-MM-DD>/transcript.md ("feed it to your own agent"),
keyed by the episode's date in US Eastern time. 39 of 39 episodes in the 45 days before
2026-11-23 had one. Its first line is "# <title> — Transcript (<date>)", and
"[HH:MM:SS]" markers start some paragraphs.

The lookup is by DATE, so the page can belong to a different episode. Three ways that
happens, each seen in the live feed (2026-11-24 review of PR #84):
  - A new episode's page is not up yet, and the day before holds YESTERDAY's episode,
    often with a near-identical title ("... Part 1" / "... Part 2"; token-set matching
    scores a title whose words are a subset of another's as 100).
  - A promo item shares its date with the main episode.
  - The site retitles the page ("A System for Testing New AI Models" in the feed is
    "The Best Way to Test New AI Models" on its 2026-10-07 page).
So the rule depends on which day the page is for:
  - The episode's OWN Eastern day: when no other feed item shares that day, the page is
    this episode's whatever its title says (the retitle case). When others do, the page
    must match this item's title better than any of theirs, at MIN_TITLE_SIMILARITY or
    above, with the same numbers in both titles.
  - Any OTHER day (the UTC day when it differs, the day before or after): the page's title
    must equal the feed's once normalised. Anything else is NotYet, and a later run asks
    again.
The page's own header date must also equal the day in its url.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Sequence
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


def _numbers(title: Optional[str]) -> list[str]:
    return sorted(re.findall(r"\d+", normalize_title(title)))


def _accepts(page_title: Optional[str], item: FeedItem, own_day: bool, same_day_titles: Sequence[str]) -> tuple[bool, str]:
    """(is this page the item's episode, why not)."""
    if not own_day:
        if page_title and normalize_title(page_title) == normalize_title(item.title):
            return True, ""
        return False, "another episode's page (a different day needs an exact title)"
    others = [t for t in same_day_titles if normalize_title(t) != normalize_title(item.title)]
    if not others:
        return True, ""  # the only episode that day: the page is ours, even retitled
    mine = title_similarity(page_title, item.title)
    best_other = max(title_similarity(page_title, t) for t in others)
    if mine >= MIN_TITLE_SIMILARITY and mine > best_other and _numbers(page_title) == _numbers(item.title):
        return True, ""
    return False, f"another episode's page (title match {mine}, another item that day {best_other})"


def fetch(item: FeedItem, session=None, same_day_titles: Sequence[str] = ()) -> Result:
    """`same_day_titles`: the titles of the OTHER feed items on this item's Eastern day.
    The caller holds the whole feed; without them the reader cannot tell a retitled page
    from a promo's."""
    import requests

    getter = session or requests
    days = candidate_days(item)
    if not days:
        return Failed("the feed item has no parseable date, and this source is keyed by date")
    tried: list[str] = []
    for index, day in enumerate(days):
        url = BASE_URL.format(day=day.isoformat())
        try:
            resp = getter.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            return NotYet(f"{url}: {exc.__class__.__name__}")
        if resp.status_code == 404:
            tried.append(f"{day} (none)")
            continue
        if resp.status_code == 429:
            return NotYet(f"{url}: HTTP 429", rate_limited=True)
        if resp.status_code != 200:
            return NotYet(f"{url}: HTTP {resp.status_code}")
        page_title, page_day, body = parse_markdown(resp.text)
        if page_day is not None and page_day != day.isoformat():
            tried.append(f"{day} (page says {page_day})")
            continue
        ok, why = _accepts(page_title, item, own_day=index == 0, same_day_titles=same_day_titles)
        if not ok:
            tried.append(f"{day} ({why})")
            continue
        problem = sanity_check(body, item.duration_seconds)
        if problem:
            return NotYet(f"{url}: {problem}")
        return Transcript(text=body, source_type=SOURCE_TYPE, model=MODEL, is_generated=True, source_url=url)
    return NotYet("no transcript page for this episode yet; tried " + ", ".join(tried))

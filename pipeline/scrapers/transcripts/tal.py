"""This American Life's own transcript for an episode, from thisamericanlife.org.

Every TAL episode back to #1 has a transcript page at
https://www.thisamericanlife.org/<number>/transcript (checked 2026-11-24 on #1, #250,
#436, #500, #878, #880, #894, #902). The number comes from the feed item's own link
(https://www.thisamericanlife.org/902/the-missing-minutes), and the page states it
("Episode #902"), so like NPR's story id there is no risk of reading another
episode's page: the reader refuses a page whose number differs. A rerun ("436: The
Psychopath Test", re-released 2026-11-23) gets the original's transcript; its new
intro, if any, is missing, which is the right trade for a song-credits show.

Items with no number in their link ("An Update from Ira", links to /lifepartners or
the home page) are announcements without a transcript: NotYet, never Failed.

The page marks each spoken passage as <p begin="HH:MM:SS.ss">, grouped under speaker
<h4>s and act <h3>s. Normalised to the shape extraction was tuned on: plain
paragraphs, no speaker names, no act titles, no bracketed sounds ("[LAUGHS]"). TAL
says its transcripts are speech recognition corrected by people, so is_generated is
False.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from feeds import USER_AGENT, FeedItem  # noqa: E402
from scrapers.transcripts.base import (  # noqa: E402
    Failed,
    NotYet,
    Result,
    Transcript,
    join_paragraphs,
    sanity_check,
)

TRANSCRIPT_URL = "https://www.thisamericanlife.org/{number}/transcript"
SOURCE_TYPE = "publisher_transcript"
MODEL = "tal_transcript_page"
TIMEOUT_SECONDS = 30

_NUMBER_IN_LINK = re.compile(r"thisamericanlife\.org/(\d+)(?:/|$)")
_NUMBER_IN_TITLE = re.compile(r"^(\d+):")
_PAGE_NUMBER = re.compile(r"Episode #(\d+)")
_PASSAGE = re.compile(r'<p begin="[^"]*"[^>]*>(.*?)</p>', re.S)
_BRACKETED = re.compile(r"\[[^\]\n]{1,200}\]")
_TAG = re.compile(r"<[^>]+>")


def episode_number(item: FeedItem) -> Optional[int]:
    """The episode's number: from its own link first, else a "902: ..." title."""
    for pattern, text in ((_NUMBER_IN_LINK, item.link), (_NUMBER_IN_TITLE, item.title)):
        m = pattern.search(text or "")
        if m:
            return int(m.group(1))
    return None


def parse_transcript_page(page_html: str) -> tuple[Optional[int], str]:
    """(the episode number the page states, its transcript as plain paragraphs)."""
    m = _PAGE_NUMBER.search(page_html)
    stated = int(m.group(1)) if m else None
    start = page_html.find('<div class="content">')
    end = page_html.find('<div id="torey">', start)
    body = page_html[start:end if end > start else None] if start >= 0 else ""
    kept = []
    for raw in _PASSAGE.findall(body):
        text = html.unescape(_TAG.sub("", raw))
        text = re.sub(r"\s+", " ", _BRACKETED.sub(" ", text)).strip()
        if text:
            kept.append(text)
    return stated, join_paragraphs(kept)


def fetch(item: FeedItem, session=None) -> Result:
    import requests

    number = episode_number(item)
    if number is None:
        return NotYet(f"no episode number in the item's link ({item.link!r}); no transcript expected")
    url = TRANSCRIPT_URL.format(number=number)
    getter = session or requests
    try:
        resp = getter.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        return NotYet(f"{url}: {exc.__class__.__name__}")
    if resp.status_code == 404:
        return NotYet(f"{url}: no transcript page yet")
    if resp.status_code == 429:
        return NotYet(f"{url}: HTTP 429", rate_limited=True)
    if resp.status_code in (401, 403) or resp.status_code >= 500:
        # Blocked or broken, not "not posted yet": a whole backfill answering this way
        # must say so, not finish green with nothing added.
        return Failed(f"{url}: HTTP {resp.status_code}")
    if resp.status_code != 200:
        return NotYet(f"{url}: HTTP {resp.status_code}")
    stated, body = parse_transcript_page(resp.text)
    if stated != number:
        return NotYet(f"{url}: the page is episode {stated}, not {number}")
    if not body:
        return NotYet(f"{url}: the page has no transcript text yet")
    problem = sanity_check(body, item.duration_seconds)
    if problem:
        return NotYet(f"{url}: {problem}")
    return Transcript(text=body, source_type=SOURCE_TYPE, model=MODEL, is_generated=False, source_url=url)

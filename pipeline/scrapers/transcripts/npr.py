"""NPR's own transcript for an episode (Pop Culture Happy Hour, Machine Gods).

Every NPR podcast item's <link> is a story url carrying the story id
(https://www.npr.org/2026/11/20/nx-s1-6047626/...), and the same id opens
https://www.npr.org/transcripts/<id>. 12 of 12 recent PCHH episodes and the three
Machine Gods episodes checked on 2026-11-23 had a full, human-edited transcript there.

The id comes from the item itself, so unlike AI Daily's date lookup there is no risk of
reading another episode's page, and no title check: NPR's page titles are rewritten
headlines ("Anthropic's record IPO, and what Kevin got wrong ..." for the feed's
"Anthropic's Record IPO + What Kevin Got Wrong ..."), which a title check would
reject for no reason. The length check still applies.

The page marks its transcript with class "transcript storytext"; turns are <p>
elements (not closed in NPR's markup) that open with a speaker label, "THOMPSON:" on
PCHH and "Roose:" on Machine Gods. The text ends where NPR's copyright line starts.

Normalised to the shape extraction was tuned on: plain paragraphs, no speaker labels
(Taddy's text carried none, and a host's name on every turn would read as a mention),
no all-caps stage directions such as "(SOUNDBITE OF MUSIC)".
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
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

TRANSCRIPT_URL = "https://www.npr.org/transcripts/{story_id}"
SOURCE_TYPE = "publisher_transcript"
MODEL = "npr_transcript_page"
TIMEOUT_SECONDS = 30

# nx-s1-6047626 (current ids) or a long all-digit id (older stories).
_STORY_ID_RE = re.compile(r"/(nx-s\d+-\d+|\d{9,})(?:/|$)")
# A turn's label: one to four capitalised words, optionally ", BYLINE", then a colon.
# "THOMPSON:", "CANDICE LIM:", "Roose:", "Kevin Roose:", "SAM SANDERS, BYLINE:".
_LABEL_RE = re.compile(r"^(?:[A-Z][\w.'’-]*)(?: [A-Z][\w.'’-]*){0,3}(?:, BYLINE)?:\s+")
# All-caps parentheticals only: "(SOUNDBITE OF MUSIC)", "(LAUGHTER)". Mixed-case
# parentheses are speech and stay.
_STAGE_RE = re.compile(r"\((?=[^)]*[A-Z]{3})[A-Z0-9 ,.'’\"&/:-]+\)")
_END_MARKERS = ("Copyright ©", "Copyright (c)")
_SKIP_PREFIXES = ("Accuracy and availability of NPR transcripts may vary",)


def story_id(link: Optional[str]) -> Optional[str]:
    if not link:
        return None
    m = _STORY_ID_RE.search(link)
    return m.group(1) if m else None


class _TranscriptParser(HTMLParser):
    """Collect the text of each <p> inside the element whose class includes both
    "transcript" and "storytext". NPR leaves <p> unclosed, so a new <p> (or the end of
    the transcript element) ends the previous paragraph."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0  # >0 while inside the transcript element
        self.paragraphs: list[str] = []
        self._current: Optional[list[str]] = None
        self.done = False

    def _flush(self) -> None:
        if self._current is not None:
            self.paragraphs.append("".join(self._current))
        self._current = None

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if self.depth == 0:
            classes = (dict(attrs).get("class") or "").split()
            if "transcript" in classes and "storytext" in classes:
                self.depth = 1
            return
        if tag in ("div", "section", "article", "ul", "ol", "aside"):
            self.depth += 1
        if tag == "p":
            self._flush()
            self._current = []
        elif tag == "br" and self._current is not None:
            self._current.append(" ")

    def handle_endtag(self, tag):
        if self.done or self.depth == 0:
            return
        if tag == "p":
            self._flush()
        elif tag in ("div", "section", "article", "ul", "ol", "aside"):
            self.depth -= 1
            if self.depth == 0:
                self._flush()
                self.done = True

    def handle_data(self, data):
        if self.depth and self._current is not None:
            self._current.append(data)


def parse_transcript_page(page_html: str) -> str:
    """The transcript's plain text, normalised. Empty when the page has none."""
    parser = _TranscriptParser()
    parser.feed(page_html)
    parser.close()
    parser._flush()
    kept: list[str] = []
    for raw in parser.paragraphs:
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            continue
        if text.startswith(_END_MARKERS):
            break
        if text.startswith(_SKIP_PREFIXES):
            continue
        text = _LABEL_RE.sub("", text)
        text = _STAGE_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            kept.append(text)
    return join_paragraphs(kept)


def fetch(item: FeedItem, session=None) -> Result:
    import requests

    sid = story_id(item.link)
    if not sid:
        return Failed(f"no NPR story id in the item's link ({item.link!r})")
    url = TRANSCRIPT_URL.format(story_id=sid)
    getter = session or requests
    try:
        resp = getter.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as exc:
        return NotYet(f"{url}: {exc.__class__.__name__}")
    if resp.status_code == 404:
        return NotYet(f"{url}: no transcript page yet")
    if resp.status_code != 200:
        return NotYet(f"{url}: HTTP {resp.status_code}")
    body = parse_transcript_page(resp.text)
    if not body:
        return NotYet(f"{url}: the page has no transcript text yet")
    problem = sanity_check(body, item.duration_seconds)
    if problem:
        return NotYet(f"{url}: {problem}")
    return Transcript(text=body, source_type=SOURCE_TYPE, model=MODEL, is_generated=False, source_url=url)

"""Intake sources: turn a publisher's feed or index page into Candidates.

Two publishers, two shapes. OpenAI has an official RSS feed (verified 2026-09-01,
~1,100 items, ~4 posts a day). Anthropic has no feed at all — `/news` and
`/engineering` are HTML indexes, scraped to markdown through Firecrawl and parsed
here. Parsing is deliberately separate from fetching so the parsers are tested on
frozen fixtures (tests/fixtures/intake/) and a layout change on a publisher's site
fails a test, not a Monday run.

Everything here is deterministic. The model only sees what survives these parsers
and the pre-checks in judge.py — "scripts own schema-stable inputs; the LLM earns
its keep at irreducible ambiguity" (docs/principles.md).
"""

from __future__ import annotations

import email.utils
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import httpx

OPENAI_RSS_URL = "https://openai.com/news/rss.xml"
ANTHROPIC_INDEXES = {
    # slug -> index page. Both are one scrape each per weekly run. /news lists the
    # ten newest posts plus a featured block; /engineering is the practitioner
    # blog Kevin actually saves from (the Claude Code auto-mode post lived there).
    "anthropic-news": "https://www.anthropic.com/news",
    "anthropic-engineering": "https://www.anthropic.com/engineering",
}
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
USER_AGENT = "list-maker/intake (+https://github.com/khglynn/list-maker)"


@dataclass
class Candidate:
    """One post a source surfaced. `discovered_via` is the provenance that travels
    with the row into intake_candidates (feed item / index page / episode ids)."""

    source: str  # openai-rss | anthropic-news | anthropic-engineering | podcast-cited
    title: str
    url: str
    published_on: Optional[date]
    category: list[str] = field(default_factory=list)
    blurb: str = ""
    discovered_via: dict = field(default_factory=dict)


# ── OpenAI: RSS ──────────────────────────────────────────────────────────────

def parse_openai_rss(xml_text: str | bytes, since: Optional[date] = None) -> list[Candidate]:
    """Items published on/after `since` (all items when None), newest first.

    A feed item with no parseable pubDate is kept with published_on=None rather than
    dropped — an honest gap the judge can see; silently losing a post is the failure
    mode this whole arc exists to remove.
    """
    root = ET.fromstring(xml_text)
    out: list[Candidate] = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        if not link or not title:
            continue
        published = _parse_rfc2822_date(item.findtext("pubDate"))
        if since is not None and published is not None and published < since:
            continue
        out.append(Candidate(
            source="openai-rss",
            title=title,
            url=link,
            published_on=published,
            category=[c.text.strip() for c in item.findall("category") if c.text],
            blurb=_strip_tags(item.findtext("description") or "")[:500],
            discovered_via={"feed": OPENAI_RSS_URL, "guid": (item.findtext("guid") or "").strip()},
        ))
    out.sort(key=lambda c: c.published_on or date.min, reverse=True)
    return out


def fetch_openai_rss(since: Optional[date] = None, timeout: float = 30.0) -> list[Candidate]:
    resp = httpx.get(OPENAI_RSS_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return parse_openai_rss(resp.content, since)


# ── Anthropic: HTML index → Firecrawl markdown → Candidates ─────────────────

# One markdown link whose target is on anthropic.com. The bracket text carries some
# mix of a date ("Sep 1, 2026"), a category glued to it ("AnnouncementsSep 1, 2026"
# or "Sep 1, 2026Announcements"), a bold title, and a blurb — the exact mix depends
# on whether the entry is the hero, a featured card, or a list row. We merge every
# bracket that points at the same URL, so the shape differences stop mattering.
_LINK_RE = re.compile(r"\[(?P<text>[^\]]*?)\]\((?P<url>https://www\.anthropic\.com/[^)\s]+)\)", re.S)
_DATE_RE = re.compile(r"(?P<cat>[A-Z][A-Za-z ]*?)?(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4})(?P<cat2>[A-Z][A-Za-z ]*)?")
_BOLD_RE = re.compile(r"\*\*(?P<title>[^*]+?)\*\*")
_NOISE_PATHS = ("/press-kit", "/news$", "/engineering$", "/careers", "/company", "/legal", "/rss", "/customers$")


def parse_anthropic_index(markdown: str, source: str = "anthropic-news",
                          since: Optional[date] = None) -> list[Candidate]:
    """Entries on an Anthropic index page (hero + featured cards + the list), newest
    first. Undated entries are dropped: on these pages every post row carries a date,
    so a dateless link is navigation, not a post."""
    merged: dict[str, dict] = {}
    for m in _LINK_RE.finditer(markdown):
        url = m["url"].rstrip("/")
        if any(re.search(p, url) for p in _NOISE_PATHS):
            continue
        text = m["text"].replace("\\\\", "\n").replace("\\", "")
        entry = merged.setdefault(url, {"title": "", "date": None, "category": [], "blurb": ""})
        bold = _BOLD_RE.search(text)
        if bold:
            entry["title"] = entry["title"] or bold["title"].strip()
        dm = _DATE_RE.search(text)
        if dm:
            entry["date"] = entry["date"] or _parse_month_date(dm["date"])
            cat = (dm["cat"] or dm["cat2"] or "").strip()
            if cat and cat not in entry["category"]:
                entry["category"].append(cat)
            remainder = _DATE_RE.sub("", text, count=1)
            lines = [ln.strip(" *") for ln in remainder.splitlines() if ln.strip(" *")]
            if not entry["title"] and lines:
                entry["title"] = lines[0]  # list rows: the one line after the date IS the title
            elif bold and len(lines) > 1:
                entry["blurb"] = entry["blurb"] or " ".join(lines[1:])[:500]
            elif not bold and lines and entry["title"] and lines[0] != entry["title"]:
                entry["blurb"] = entry["blurb"] or " ".join(lines)[:500]
        elif not bold and text.strip() and not entry["title"]:
            entry["title"] = text.strip()
    out = [
        Candidate(source=source, title=e["title"], url=url, published_on=e["date"],
                  category=e["category"], blurb=e["blurb"],
                  discovered_via={"index": ANTHROPIC_INDEXES.get(source, source)})
        for url, e in merged.items() if e["date"] and e["title"]
    ]
    if since is not None:
        out = [c for c in out if c.published_on >= since]
    out.sort(key=lambda c: c.published_on, reverse=True)
    return out


def fetch_anthropic_index(source: str, firecrawl_api_key: str, since: Optional[date] = None,
                          timeout: float = 90.0) -> list[Candidate]:
    resp = httpx.post(
        FIRECRAWL_SCRAPE_URL,
        json={"url": ANTHROPIC_INDEXES[source], "formats": ["markdown"], "onlyMainContent": True},
        headers={"Authorization": f"Bearer {firecrawl_api_key}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    markdown = (resp.json().get("data") or {}).get("markdown") or ""
    return parse_anthropic_index(markdown, source, since)


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_rfc2822_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw.strip()).date()
    except (TypeError, ValueError):
        return None


def _parse_month_date(raw: str) -> Optional[date]:
    try:
        return datetime.strptime(raw, "%b %d, %Y").date()
    except ValueError:
        return None


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()

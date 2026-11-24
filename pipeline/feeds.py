"""The one RSS reader: every podcast feed this repo reads goes through here.

Two kinds of caller share it, and that is the point of having one. The importers WRITE
an episode's identity into `episodes.url` (scrapers/rss/import_feed.py, and the
Culture Gabfest importer); the feed check READS a feed and asks "do we hold this
episode?" by rebuilding that same identity (feed_check.rss_recent_episodes). If the two
ever parsed a date or picked an identity differently, the check would report episodes
we hold as missing, or the importer would insert episodes we already have. Both have
happened here before, which is why there is no second parser.

Read-only and DB-free: nothing in this module touches Neon.

Two rules every caller inherits:

1. DATES ARE UTC. An episode's `publish_date` is the UTC calendar day of its pubDate.
   That is what Taddy stored for every row it wrote (its datePublished is unix seconds),
   so it is what the title+date match has to compare against. This American Life dates
   its feed in US Eastern ("Sun, 22 Nov 2026 20:00:00 -0500"), which is already Monday in
   UTC, and Neon holds that episode as Monday. Keeping the feed's own offset instead is
   how TAL episodes 886 and 887 came to exist twice (BACKLOG).
2. IDENTITY IS THE FEED'S <guid>. It is unique and stable per episode, and every
   podcast feed we read carries one. The fallbacks below exist so a malformed item
   still gets a deterministic key, never so a feed can do without guids.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import defusedxml.ElementTree as ET  # hardened against XXE / billion-laughs

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_ITUNES = f"{{{ITUNES_NS}}}"

# A plain, honest User-Agent. Some publishers refuse a bare python-requests agent;
# none we read needs a browser's. The repo url lets a publisher see what this is.
USER_AGENT = "list-maker/1.0 (+https://github.com/khglynn/list-maker)"
FEED_TIMEOUT_SECONDS = 60

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class FeedItem:
    """One <item> from a podcast feed, as every reader in this repo sees it."""

    title: str
    guid: Optional[str]
    link: Optional[str]
    enclosure_url: Optional[str]
    pubdate_raw: Optional[str]
    # Timezone-aware and in UTC; None when the feed's pubDate cannot be parsed.
    published_at: Optional[datetime]
    description_html: str
    duration_seconds: Optional[int] = None
    episode_number: Optional[int] = None
    image_url: Optional[str] = None
    # <itunes:episodeType>: "full", "bonus" or "trailer"; None when the feed omits it.
    episode_type: Optional[str] = None

    @property
    def is_trailer(self) -> bool:
        """A show's trailer is an ad for the show, not an episode: no transcript, nothing
        to extract. The importer skips it and the feed check doesn't expect it."""
        return (self.episode_type or "").strip().lower() == "trailer"

    @property
    def publish_date(self) -> Optional[date]:
        """The UTC calendar day (rule 1 in the module docstring)."""
        return self.published_at.date() if self.published_at else None

    @property
    def description_text(self) -> str:
        return clean_html(self.description_html)


@dataclass(frozen=True)
class ParsedFeed:
    items: list[FeedItem]
    # The channel's <itunes:new-feed-url>, when a publisher has moved the show. Several
    # feeds name THEMSELVES here (TAL, SOP, Hard Fork), so callers compare it to the url
    # they fetched before treating it as a move.
    new_feed_url: Optional[str] = None
    channel_title: str = ""


@dataclass(frozen=True)
class FetchedFeed:
    requested_url: str
    final_url: str
    content: bytes
    status_code: int
    # Redirects are followed, and said out loud: a feed that moved without telling us is
    # the one change that would otherwise only surface as "the show went quiet".
    redirects: list[str] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        return self.final_url.rstrip("/") != self.requested_url.rstrip("/")


def clean_html(raw: Optional[str]) -> str:
    """Strip tags, unescape entities, collapse whitespace into plain text.

    Tags become spaces so ``</p><p>`` keeps a word boundary, then the space that leaves
    before punctuation is dropped (``<i>Backrooms</i>.`` would read ``Backrooms .``).
    """
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def parse_pubdate_utc(raw: Optional[str]) -> Optional[datetime]:
    """An RFC 822 pubDate as an aware UTC datetime, or None.

    A pubDate with no offset at all is read as UTC, which is what RFC 822 says "-0000"
    means and what the feeds we read that lack one intend.
    """
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_duration(raw: Optional[str]) -> Optional[int]:
    """<itunes:duration> in seconds. Feeds write it as "1470", "24:30" or "00:24:30"."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        if ":" in raw:
            seconds = 0
            for part in raw.split(":"):
                seconds = seconds * 60 + int(float(part))
            return seconds or None
        return int(float(raw)) or None
    except ValueError:
        return None


def _int_or_none(raw: Optional[str]) -> Optional[int]:
    try:
        return int(raw.strip()) if raw and raw.strip() else None
    except ValueError:
        return None


def parse_feed(xml_bytes: bytes) -> ParsedFeed:
    """Every <item> in an RSS feed, in feed order (newest first for every feed we read).

    Raises on XML that cannot be parsed or is not RSS. The feed check turns that into
    "unverified" and the importers into a failed step; neither should mistake a broken
    feed for an empty one.
    """
    root = ET.fromstring(xml_bytes)
    if root.tag.lower() != "rss":
        raise ValueError(f"not an RSS document (root element {root.tag!r})")
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS document has no <channel>")

    items: list[FeedItem] = []
    for it in channel.findall("item"):
        guid_el = it.find("guid")
        enclosure_el = it.find("enclosure")
        image_el = it.find(f"{_ITUNES}image")
        pubdate_raw = it.findtext("pubDate")
        items.append(
            FeedItem(
                title=(it.findtext("title") or "").strip(),
                guid=(guid_el.text or "").strip() or None if guid_el is not None and guid_el.text else None,
                link=(it.findtext("link") or "").strip() or None,
                enclosure_url=(enclosure_el.get("url") or None) if enclosure_el is not None else None,
                pubdate_raw=pubdate_raw,
                published_at=parse_pubdate_utc(pubdate_raw),
                description_html=it.findtext("description") or "",
                duration_seconds=parse_duration(it.findtext(f"{_ITUNES}duration")),
                episode_number=_int_or_none(it.findtext(f"{_ITUNES}episode")),
                image_url=(image_el.get("href") or None) if image_el is not None else None,
                episode_type=(it.findtext(f"{_ITUNES}episodeType") or "").strip() or None,
            )
        )
    return ParsedFeed(
        items=items,
        new_feed_url=(channel.findtext(f"{_ITUNES}new-feed-url") or "").strip() or None,
        channel_title=(channel.findtext("title") or "").strip(),
    )


def episode_identity(item: FeedItem, synthetic_prefix: str = "rss") -> str:
    """The exact string an importer writes to `episodes.url` for this item.

    The guid, else the audio url, else the link, else a synthetic key. <link> comes last
    on purpose: some feeds give every episode the same show page (Hard Fork's is
    nytimes.com/column/hard-fork for each one), which would collapse them all onto one
    row through the UNIQUE url constraint.

    `synthetic_prefix` keeps the Culture Gabfest importer's historic "gabfest:" key for
    the malformed-item case; Neon held none of those on 2026-11-23.
    """
    return (
        item.guid
        or item.enclosure_url
        or item.link
        or f"{synthetic_prefix}:{item.title or 'unknown'}:{item.pubdate_raw or ''}"
    )


def fetch_feed(url: str, timeout: int = FEED_TIMEOUT_SECONDS, session=None) -> FetchedFeed:
    """GET a feed, following redirects. Raises on a network error or an HTTP error status.

    `session` is anything with requests' `get`; tests pass a fake.
    """
    import requests

    getter = session or requests
    resp = getter.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    resp.raise_for_status()  # never parse a 404/500 error page as a feed
    history = [getattr(h, "url", "") for h in (getattr(resp, "history", None) or [])]
    return FetchedFeed(
        requested_url=url,
        final_url=getattr(resp, "url", None) or url,
        content=resp.content,
        status_code=getattr(resp, "status_code", 200),
        redirects=[h for h in history if h],
    )

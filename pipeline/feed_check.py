#!/usr/bin/env python3
"""Independent SECOND SOURCE for "are we caught up?" — what makes the pulse's green
EARNED instead of merely trusted.

The freshness check on its own only knows "days since OUR latest episode," which can't
tell "the show is on break" (fine) from "our import silently broke 3 weeks ago" (bad).
This asks each show's REAL feed what the latest episode is, so the caller can compare:
if the feed is ahead of us, we're behind — a silent failure no DB-only check can see.

Coverage: Taddy indexes 5 of the 6 shows (incl. TAL/SOP, which we transcribe elsewhere);
Culture Gabfest comes from its Megaphone RSS. Read-only, no DB.

The contract is deliberately strict so the check can't lie by omission:
  - returns a NON-EMPTY list of recent publish dates (newest first, all <= today) when it
    got a trustworthy answer;
  - returns None for EVERY "couldn't really check" case — unreachable, HTTP error,
    GraphQL-200-with-errors, malformed, or empty — and the caller must surface None as
    "unverified", never as green. A green we didn't earn is the bug we're killing.

Two shapes, same contract. The `*_dates` functions answer "what dates does the feed
show?"; the `*_episodes` functions answer "WHICH EPISODES does the feed show?", pairing
each one's identity (the exact string the importer writes to `episodes.url`) with its
date and title. Dates alone can only ever be compared against MAX(publish_date), which
is blind to a hole in the middle of a series and fooled by a re-dated episode; identity
is immune to both. Shows whose rows are written by a scraper the feed knows nothing
about (SOP) have no comparable identity — see ShowConfig.episode_identity — so the
date-only functions stay, and stay used.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import NamedTuple, Optional

import defusedxml.ElementTree as ET  # hardened against XXE / billion-laughs
import requests

# pipeline/ on the path so `show_config` imports whether this module is loaded flat
# (`from feed_check import ...`, how data_health runs) or as `pipeline.feed_check`
# (how the tests import it). Mirrors data_health.py's own guard.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from show_config import taddy_episode_url  # noqa: E402

TADDY_API_URL = "https://api.taddy.org"
TIMEOUT = 20


class FeedEpisode(NamedTuple):
    """One episode as the show's real feed reports it.

    `identity` is the exact value the importer would write to `episodes.url` for this
    episode, so "do we hold it?" is a set membership test rather than a date comparison.
    `title` rides along because a row written before the show's importer changed hands
    can hold the same episode under an older url — the caller falls back to the
    importer's own title+date dedup rule for those (data_health._feed_episode_is_held).
    """

    identity: str
    publish_date: date
    title: str


def _ts_to_date(ts: object) -> Optional[date]:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()  # Taddy datePublished = unix seconds (UTC)
    except (TypeError, ValueError, OSError):
        return None


def _rss_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:  # normalize to UTC so a late-night -0500 episode lands on the right day
        dt = dt.astimezone(timezone.utc)
    return dt.date()


def _taddy_latest_episodes(series_uuid: str, limit: int) -> Optional[list[dict]]:
    """Raw `getLatestPodcastEpisodes` rows, or None for every "couldn't verify" case.

    One request shared by both readers below (dates only, and full identity) so they can
    never disagree about what the feed said, or about what counts as unverifiable.
    """
    user_id = os.getenv("TADDY_USER_ID")
    api_key = os.getenv("TADDY_API_KEY")
    if not user_id or not api_key:
        return None
    # Taddy requires `uuid` in this query; `name` is what lets a caller fall back to the
    # importer's title+date dedup rule for episodes stored under an older url scheme.
    query = (
        f'query {{ getLatestPodcastEpisodes(uuids:["{series_uuid}"], page:1, '
        f"limitPerPage:{limit}) {{ uuid datePublished name }} }}"
    )
    try:
        resp = requests.post(
            TADDY_API_URL,
            headers={"Content-Type": "application/json", "X-USER-ID": user_id, "X-API-KEY": api_key},
            json={"query": query},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 — any failure = "couldn't verify", not a crash
        return None
    # A GraphQL API can return HTTP 200 WITH an "errors" field — don't read that as empty.
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    eps = (payload.get("data") or {}).get("getLatestPodcastEpisodes")
    if not isinstance(eps, list):
        return None
    return eps


def taddy_recent_dates(series_uuid: str, limit: int = 15) -> Optional[list[date]]:
    eps = _taddy_latest_episodes(series_uuid, limit)
    if eps is None:
        return None
    dates = [d for d in (_ts_to_date(e.get("datePublished")) for e in eps) if d]
    return sorted(dates, reverse=True)


def taddy_recent_episodes(series_uuid: str, limit: int = 15) -> Optional[list[FeedEpisode]]:
    """Like taddy_recent_dates, but keeps each episode's IDENTITY — the exact url the
    Taddy importer writes to episodes.url — alongside its date and title.

    The uuid was already in this query's response and thrown away before 2026-09-03;
    that is why identity comparison costs no extra API call. An episode with no uuid or
    no parseable date is dropped, exactly as the date-only reader drops an unparseable
    one — a feed row we cannot identify must not become a phantom "missing episode".
    """
    eps = _taddy_latest_episodes(series_uuid, limit)
    if eps is None:
        return None
    episodes = [
        FeedEpisode(taddy_episode_url(uuid), d, (e.get("name") or "").strip())
        for e in eps
        if (uuid := e.get("uuid")) and (d := _ts_to_date(e.get("datePublished")))
    ]
    return sorted(episodes, key=lambda ep: ep.publish_date, reverse=True)


def rss_recent_dates(feed_url: str, title_prefix: str = "", limit: int = 15) -> Optional[list[date]]:
    try:
        resp = requests.get(feed_url, timeout=TIMEOUT, headers={"User-Agent": "list-maker-health"})
        resp.raise_for_status()  # don't try to parse a 404/500 HTML error page as a feed
        root = ET.fromstring(resp.content)
        if root.tag.lower() != "rss":
            return None
        channel = root.find("channel")
        if channel is None:
            return None
        dates: list[date] = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            if title_prefix and not title.startswith(title_prefix):
                continue
            d = _rss_date(item.findtext("pubDate"))
            if d:
                dates.append(d)
    except Exception:  # noqa: BLE001
        return None
    return sorted(dates, reverse=True)[:limit]


def rss_recent_episodes(
    feed_url: str, title_prefix: str = "", limit: int = 15
) -> Optional[list[FeedEpisode]]:
    """Like rss_recent_dates, but pairs each item's identity with its date and title.

    The identity comes from import_gabfest.episode_url — the importer's own guid >
    enclosure > link > synthetic chain, REUSED rather than re-implemented, so the
    reader and the writer of episodes.url cannot drift. The import is function-level
    on purpose: import_gabfest pulls in `common`, and this module's contract is
    "read-only, no DB" for the four callers that never touch the RSS path.
    """
    from scrapers.gabfest.import_gabfest import episode_url, parse_feed

    try:
        resp = requests.get(feed_url, timeout=TIMEOUT, headers={"User-Agent": "list-maker-health"})
        resp.raise_for_status()  # don't try to parse a 404/500 HTML error page as a feed
        items = parse_feed(resp.content)
    except Exception:  # noqa: BLE001
        return None
    episodes: list[FeedEpisode] = []
    for item in items:
        title = (item.get("title") or "").strip()
        if title_prefix and not title.startswith(title_prefix):
            continue
        d = item.get("publish_date")  # parse_feed already ran parse_pubdate
        if d:
            episodes.append(FeedEpisode(episode_url(item), d, title))
    return sorted(episodes, key=lambda ep: ep.publish_date, reverse=True)[:limit]


def feed_recent_dates(cfg, limit: int = 15) -> Optional[list[date]]:
    """Recent episode publish dates from the show's REAL feed, newest first, all <= today.

    None means we could NOT get a trustworthy answer (unreachable / error / malformed /
    empty) — the caller must show "unverified", not green. Future-dated (pre-release)
    episodes are filtered out so they don't trigger a false "BEHIND".
    """
    if getattr(cfg, "taddy_uuid", None):
        dates = taddy_recent_dates(cfg.taddy_uuid, limit)
    elif (url := getattr(cfg, "fallback_website_url", None)) and "megaphone" in url:
        dates = rss_recent_dates(url, title_prefix="Culture Gabfest", limit=limit)
    else:
        return None
    if dates is None:
        return None
    today = datetime.now(timezone.utc).date()
    # Sort here, not just in the sources: "newest first" is this function's contract and
    # every caller reads feed[0] as the latest — don't let it depend on who fed us.
    dates = sorted((d for d in dates if d <= today), reverse=True)
    return dates or None  # empty (genuinely, or after dropping future dates) -> unverified


def feed_recent_episodes(cfg, limit: int = 15) -> Optional[list[FeedEpisode]]:
    """Recent episodes from the show's REAL feed, each carrying the identity we would
    hold it under. Newest first, all <= today. Same None contract as feed_recent_dates.

    It branches on cfg.episode_identity — NOT on cfg.taddy_uuid — because the question
    here is "can the feed's ids be compared to the ids we store for this show?", and for
    SOP the answer is no even though it has a taddy_uuid: Taddy is its second source but
    its website scraper writes its urls. A show with no declared identity returns None,
    which the caller reads as "no identity second source for this show", not as
    "unreachable"; data_health picks the path from the same field, so the two Nones are
    never confused.
    """
    identity = getattr(cfg, "episode_identity", None)
    if identity == "taddy_uuid" and getattr(cfg, "taddy_uuid", None):
        episodes = taddy_recent_episodes(cfg.taddy_uuid, limit)
    elif identity == "rss_guid" and (url := getattr(cfg, "fallback_website_url", None)):
        episodes = rss_recent_episodes(url, title_prefix="Culture Gabfest", limit=limit)
    else:
        return None
    if episodes is None:
        return None
    today = datetime.now(timezone.utc).date()
    episodes = sorted(
        (ep for ep in episodes if ep.publish_date <= today),
        key=lambda ep: ep.publish_date,
        reverse=True,
    )
    return episodes or None

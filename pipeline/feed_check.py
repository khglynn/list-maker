#!/usr/bin/env python3
"""Independent SECOND SOURCE for "are we caught up?" — what makes the pulse's green
EARNED instead of merely trusted.

The freshness check on its own only knows "days since OUR latest episode," which can't
tell "the show is on break" (fine) from "our import silently broke 3 weeks ago" (bad).
This asks each show's REAL feed what the latest episode is, so the caller can compare:
if the feed is ahead of us, we're behind — a silent failure no DB-only check can see.

Coverage: Taddy indexes 5 of the 6 shows (incl. TAL/SOP, which we transcribe elsewhere);
Culture Gabfest comes from its Megaphone RSS. Read-only, no DB. Returns recent publish
dates newest-first, or None when the feed can't be reached — and "None" must surface as
"unverified", never as green. Lying by omission is the thing we're trying to kill.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import defusedxml.ElementTree as ET  # hardened against XXE / billion-laughs
import requests

TADDY_API_URL = "https://api.taddy.org"
TIMEOUT = 20


def _ts_to_date(ts: object) -> Optional[date]:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()  # Taddy datePublished = unix seconds
    except (TypeError, ValueError, OSError):
        return None


def _rss_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        return None


def taddy_recent_dates(series_uuid: str, limit: int = 15) -> Optional[list[date]]:
    user_id = os.getenv("TADDY_USER_ID")
    api_key = os.getenv("TADDY_API_KEY")
    if not user_id or not api_key:
        return None
    # Taddy requires `uuid` in this query even though we only need the date.
    query = (
        f'query {{ getLatestPodcastEpisodes(uuids:["{series_uuid}"], page:1, '
        f"limitPerPage:{limit}) {{ uuid datePublished }} }}"
    )
    try:
        resp = requests.post(
            TADDY_API_URL,
            headers={"Content-Type": "application/json", "X-USER-ID": user_id, "X-API-KEY": api_key},
            json={"query": query},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        eps = ((resp.json() or {}).get("data") or {}).get("getLatestPodcastEpisodes") or []
    except Exception:  # noqa: BLE001 — any failure = "couldn't verify", not a crash
        return None
    dates = [d for d in (_ts_to_date(e.get("datePublished")) for e in eps) if d]
    return sorted(dates, reverse=True)


def rss_recent_dates(feed_url: str, title_prefix: str = "", limit: int = 15) -> Optional[list[date]]:
    try:
        xml = requests.get(feed_url, timeout=TIMEOUT, headers={"User-Agent": "list-maker-health"}).content
        channel = ET.fromstring(xml).find("channel")
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


def feed_recent_dates(cfg, limit: int = 15) -> Optional[list[date]]:
    """Recent episode publish dates from the show's REAL feed, newest first.

    None means the feed couldn't be reached — the caller must show "unverified", not
    green. Taddy for shows with a taddy_uuid; Megaphone RSS (Culture Gabfest) otherwise.
    """
    if getattr(cfg, "taddy_uuid", None):
        return taddy_recent_dates(cfg.taddy_uuid, limit)
    url = getattr(cfg, "fallback_website_url", None)
    if url and "megaphone" in url:
        return rss_recent_dates(url, title_prefix="Culture Gabfest", limit=limit)
    return None

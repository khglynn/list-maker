#!/usr/bin/env python3
"""Independent SECOND SOURCE for "are we caught up?" — what makes the pulse's green
EARNED instead of merely trusted.

The freshness check on its own only knows "days since OUR latest episode," which can't
tell "the show is on break" (fine) from "our import silently broke 3 weeks ago" (bad).
This asks each show's REAL feed what its latest episodes are, so the caller can
compare: if the feed is ahead of us, we're behind — a silent failure no DB-only check
can see. Every podcast's own RSS feed (show_config.feed_url) since the move off Taddy
(2026-12). Read-only, no DB.

The contract is deliberately strict so the check can't lie by omission:
  - returns a NON-EMPTY list (newest first, all <= today) when it got a trustworthy
    answer;
  - returns None for EVERY "couldn't really check" case — unreachable, HTTP error, not
    RSS, malformed, or empty — and the caller must surface None as "unverified", never
    as green. A green we didn't earn is the bug we're killing.

Two shapes, same contract. `feed_recent_dates` answers "what dates does the feed show?";
`feed_recent_episodes` answers "WHICH EPISODES does the feed show?", pairing each one's
identity (the exact string the importer writes to `episodes.url`) with its guid, date
and title. Dates alone can only ever be compared against MAX(publish_date), which is
blind to a hole in the middle of a series and fooled by a re-dated episode; identity is
immune to both. SOP's rows are written by a scraper the feed knows nothing about (see
ShowConfig.episode_identity), so its check stays on dates.

Parsing, dates and identity all come from pipeline/feeds.py, the module the importers
write from, so the reader and the writer of episodes.url cannot drift. Trailers are
skipped on both sides (feeds.FeedItem.is_trailer).
"""

from __future__ import annotations


import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import requests  # noqa: F401 — the network goes through feeds.fetch_feed; tests patch feed_check.requests.get

# pipeline/ on the path so `show_config` imports whether this module is loaded flat
# (`from feed_check import ...`, how data_health runs) or as `pipeline.feed_check`
# (how the tests import it). Mirrors data_health.py's own guard.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feeds  # noqa: E402  (the one RSS parser)

TIMEOUT = 20


class FeedEpisode(NamedTuple):
    """One episode as the show's real feed reports it.

    `identity` is the exact value the importer would write to `episodes.url`; `guid`,
    `title` and `publish_date` are what the importer's own matcher uses for rows held
    under an older url (import_feed.HeldIndex.match_keys, which data_health reuses).
    """

    identity: str
    publish_date: date
    title: str
    guid: Optional[str] = None


def _rss_items(feed_url: str, title_prefix: Optional[str]) -> Optional[list]:
    """The feed's items (feeds.parse_feed), filtered by title prefix; None for every
    "couldn't really check" case (unreachable, HTTP error, not RSS, malformed)."""
    try:
        items = feeds.parse_feed(feeds.fetch_feed(feed_url, timeout=TIMEOUT).content).items
    except Exception:  # noqa: BLE001 — any failure = "couldn't verify", not a crash
        return None
    return [it for it in items if not it.is_trailer and (not title_prefix or it.title.startswith(title_prefix))]


def rss_recent_dates(feed_url: str, title_prefix: str = "", limit: int = 15) -> Optional[list[date]]:
    items = _rss_items(feed_url, title_prefix)
    if items is None:
        return None
    return sorted((it.publish_date for it in items if it.publish_date), reverse=True)[:limit]


def rss_recent_episodes(
    feed_url: str, title_prefix: Optional[str] = None, limit: int = 15, synthetic_prefix: str = "rss"
) -> Optional[list[FeedEpisode]]:
    """Like rss_recent_dates, but pairs each item's identity with its date and title.

    Parsing, dates and identity all come from pipeline/feeds.py, the module the
    importers write `episodes.url` and `publish_date` from, so the reader and the writer
    cannot drift. Dates are the UTC day on both sides (until 2026-11 the Gabfest
    importer kept the feed's local date and this reader had to copy it; Slate's feed is
    all GMT, so the move changed no stored date).

    `title_prefix` None or empty means no filter, as everywhere else; a mixed feed
    (Culture Gabfest's) passes its show_config.feed_title_prefix.
    """
    items = _rss_items(feed_url, title_prefix or "")
    if items is None:
        return None
    episodes = [
        FeedEpisode(feeds.episode_identity(it, synthetic_prefix=synthetic_prefix), it.publish_date, it.title, it.guid)
        for it in items
        if it.publish_date
    ]
    return sorted(episodes, key=lambda ep: ep.publish_date, reverse=True)[:limit]


def feed_recent_dates(cfg, limit: int = 15) -> Optional[list[date]]:
    """Recent episode publish dates from the show's REAL feed, newest first, all <= today.

    None means we could NOT get a trustworthy answer (unreachable / error / malformed /
    empty) — the caller must show "unverified", not green. Future-dated (pre-release)
    episodes are filtered out so they don't trigger a false "BEHIND".
    """
    url = getattr(cfg, "feed_url", None)
    if not url:
        return None
    dates = rss_recent_dates(url, title_prefix=getattr(cfg, "feed_title_prefix", None) or "", limit=limit)
    if dates is None:
        return None
    today = datetime.now(timezone.utc).date()
    since = getattr(cfg, "import_since", None) or date.min
    # Sort here, not just in the sources: "newest first" is this function's contract and
    # every caller reads feed[0] as the latest — don't let it depend on who fed us.
    dates = sorted((d for d in dates if since <= d <= today), reverse=True)
    return dates or None  # empty (genuinely, or after dropping future dates) -> unverified


def feed_recent_episodes(cfg, limit: int = 15) -> Optional[list[FeedEpisode]]:
    """Recent episodes from the show's REAL feed, each carrying the identity we would
    hold it under. Newest first, all <= today. Same None contract as feed_recent_dates.

    It branches on cfg.episode_identity because the question is "can the feed's ids be
    compared to the ids we store for this show?", and for SOP the answer is no even
    though it has a feed: its website scraper writes its urls.

    Callers must decide WHICH path to take from cfg.episode_identity themselves (as
    data_health does) rather than from this returning None: a show that declares a scheme
    but lacks the config it needs also lands in the `else` and would be reported as
    "unreachable", which is a lie. tests/test_show_config.py keeps the two aligned.
    """
    identity = getattr(cfg, "episode_identity", None)
    if identity == "rss_guid" and (url := getattr(cfg, "feed_url", None)):
        synthetic = "gabfest" if getattr(cfg, "importer", None) == "gabfest_rss" else "rss"
        episodes = rss_recent_episodes(url, title_prefix=getattr(cfg, "feed_title_prefix", None),
                                       limit=limit, synthetic_prefix=synthetic)
    else:
        return None
    if episodes is None:
        return None
    today = datetime.now(timezone.utc).date()
    # A new show is not expected to hold what came before its start date
    # (show_config.import_since); its back catalogue is a --backfill decision.
    since = getattr(cfg, "import_since", None) or date.min
    episodes = sorted(
        (ep for ep in episodes if since <= ep.publish_date <= today),
        key=lambda ep: ep.publish_date,
        reverse=True,
    )
    return episodes or None

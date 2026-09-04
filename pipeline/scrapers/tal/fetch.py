#!/usr/bin/env python3
"""
TAL Episode Fetcher - Dumb Fetch, Smart Parse Strategy

This script ONLY fetches raw HTML/markdown from TAL episode URLs.
It does NOT parse or interpret the content - that's for Claude to do.

Two facts about TAL that this module exists to keep straight (see the incident note
on get_episodes_missing_songs):

  * `episodes.url` for TAL is an IDENTITY, not a link. The Taddy importer writes
    https://api.taddy.org/podcast-episode/<uuid> there and the Phase 4 feed check
    compares against it, so it must not be rewritten. The READABLE page is derived
    here, at scrape time, and never stored.
  * `episodes.scraped_at` means "Taddy saw this episode", not "we read its page for
    songs". Nothing under scrapers/tal/ has ever written that column.

Usage:
    python fetch.py                  # Fetch every episode still missing songs
    python fetch.py --limit 50       # Fetch up to 50 episodes
    python fetch.py --since 2025-01-01   # Move the date floor for a deliberate backfill
    python fetch.py --dry-run        # Show what would be fetched, and from which URL

Output:
    JSON files in fetched/tal/{db_id}.json containing:
    - db_id: Database row ID (NOT the TAL episode number)
    - url: The episode page URL that was actually fetched
    - markdown: Full page content
    - metadata: All metadata from Firecrawl
    - fetched_at: Timestamp
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

# pipeline/ on the path so `show_config` imports whether this runs as a script from its
# own directory or as pipeline.scrapers.tal.fetch — the same bootstrap the Taddy importer
# and the Gabfest importer use. The TAL url helpers live in show_config beside the Taddy
# one so the identity url and the page url stay visibly different things.
# Guarded: unguarded, every import of this module appended another copy, and pytest
# collection imports it alongside scrape.py's own insert.
_PIPELINE_DIR = str(Path(__file__).resolve().parents[2])
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
from show_config import (  # noqa: E402
    SHOWS,
    is_tal_episode_page_url,
    tal_episode_page_url,
)

# =============================================================================
# Configuration
# =============================================================================

MAX_CONCURRENT = 5  # Firecrawl hobby tier limit
FIRECRAWL_TIMEOUT = 30  # seconds per request
OUTPUT_DIR = Path(__file__).parent / "fetched" / "tal"

TAL_SHOW_ID = 2

# Don't re-read the archive. 213 TAL rows hold zero songs and 189 of them are genuine —
# old episodes whose pages carry no music credits at all (counted read-only 2026-09-04).
# Without a floor, "has no songs yet" would queue every one of them on every run, forever.
#
# 2026-01-01 is where the damage starts, not where the Taddy discovery does. The Phase 5
# plan proposed 2026-06-01 on the belief that the August discovery change was the whole
# cause; the DB says otherwise. TAL rows published in 2026 that hold no songs: 24. In
# 2025: 4. In 2024: 2. Every one of those 24 pages was checked live on 2026-09-04 and 22
# of them still list song credits today (886 "Blackout" -> "Range Mesi" by ONEDAM; 887 ->
# "Only One and Only" by Gillian Welch). A 2026-06-01 floor would leave 13 of them dark
# permanently. The cost of the wider floor is one-time: 24 Firecrawl calls on the first
# run instead of 11, then ~1-3 a week in steady state.
DEFAULT_SONG_SCRAPE_FLOOR = date(2026, 1, 1)

# =============================================================================
# Database
# =============================================================================

def get_db_connection():
    """Connect to Neon database (delegates to common.get_db_connection)."""
    # One implementation for the scheduled path — pipeline/common.py carries the connect
    # timeout, keepalives, and bounded retry. This module's private copy had none, and it
    # sits on pipeline.yml's Mon/Wed/Fri chain (rewired 2026-09-01 after the 08-31 41-minute
    # hang). Lazy import so this file still runs as a script from its own directory.
    pipeline_dir = str(Path(__file__).resolve().parents[2])
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    from common import get_db_connection as shared_connection

    return shared_connection()


def get_episodes_missing_songs(
    limit: Optional[int] = None,
    published_since: Optional[date] = None,
) -> list[dict]:
    """Episodes whose page we still need to read for songs — newest first.

    Was `get_unscraped_episodes`, keyed on `scraped_at IS NULL`. That predicate was
    answering a different question than the one being asked, and between 2026-01 and
    2026-09 it silently answered it wrong for every TAL episode.

    `scraped_at` is written by the Taddy importer only, on both of its branches: the
    INSERT that creates a row (import_transcripts.py:397) and the title+date dedup UPDATE
    that touches a row this scraper had never read (import_transcripts.py:364). Nothing
    under scrapers/tal/ has ever written it. So once TAL discovery started running the
    Taddy importer (2026-08-02), every TAL row was stamped the instant it existed and the
    queue was permanently empty: 0 rows matched `scraped_at IS NULL` on 2026-09-04, and
    the Monday cron reported success every week on the strength of finding no work.

    The question this queue actually asks is "have we read this episode's page for songs
    yet", and in today's schema the honest answer is "has it got songs" — a fact about
    the data, not about a timestamp some other writer owns. It is also self-healing: a
    row leaves the queue by acquiring songs, so a failed fetch is simply retried next run
    instead of being marked done by a side effect.

    The date floor is what keeps that from meaning "re-read the whole archive" — see
    DEFAULT_SONG_SCRAPE_FLOOR. Newest first so a --limit run drains the freshest gap.

    Known edge, stated rather than papered over: `publish_date >= %s` also excludes a row
    with a NULL publish_date, which would therefore never be queued. Zero TAL rows are in
    that state (checked 2026-09-04) and the Taddy importer always supplies a date, so
    this is a latent gap rather than a live one — but it is a real one, and a COALESCE to
    some fake date here would hide it instead of leaving it visible.
    """
    if published_since is None:
        published_since = DEFAULT_SONG_SCRAPE_FLOOR
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT id, url, title, publish_date
                FROM episodes e
                WHERE e.show_id = %s
                  AND NOT EXISTS (SELECT 1 FROM songs s WHERE s.episode_id = e.id)
                  AND e.publish_date >= %s
                ORDER BY e.publish_date DESC, e.id DESC
            """
            params: list[Any] = [TAL_SHOW_ID, published_since]
            if limit is not None:
                # `is not None`, not truthiness: --limit 0 meant "no limit" and fetched
                # everything, which is the opposite of what anyone typing 0 wants. A
                # negative value would have reached Postgres as LIMIT -1 and errored.
                if limit < 0:
                    raise ValueError(f"limit must be >= 0, got {limit}")
                sql += " LIMIT %s"
                params.append(limit)
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_already_fetched() -> set[int]:
    """Episode IDs that already have a JSON file in the local cache.

    A CONVENIENCE, NOT THE QUEUE. This directory is git-ignored and does not exist on a
    CI runner, so it is always empty there — a cache that is empty in the one environment
    that matters cannot be a record of what has been read. Worse, when it is NOT empty it
    used to be authoritative: a JSON left behind by a bad fetch (say, of an api.taddy.org
    url that has no song credits on it) would exclude that episode from every later run
    on that machine, permanently.

    The DB predicate in get_episodes_missing_songs is the queue now. This is only
    reported, never subtracted. Kept because reading the file is how you debug a parse.
    """
    if not OUTPUT_DIR.exists():
        return set()

    fetched = set()
    for f in OUTPUT_DIR.glob("*.json"):
        try:
            episode_id = int(f.stem)
            fetched.add(episode_id)
        except ValueError:
            pass
    return fetched


# =============================================================================
# Which page to read — TAL's site, never the Taddy identity url
# =============================================================================

def _title_key(title: Optional[str]) -> str:
    """Match key for title -> feed link. Straightens curly quotes (the DB and the feed
    disagree on them episode to episode) and folds case/whitespace."""
    if not title:
        return ""
    straight = (
        title.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    return " ".join(straight.split()).casefold()


def page_links_from_feed_items(items: Iterable[dict]) -> dict[str, str]:
    """Map normalised episode title -> canonical page url, from parsed RSS items.

    The feed's <link> is the authority on where an episode lives, because TAL's own url
    scheme is not derivable: /885/bless-this-mess is a 404 while /bless-this-mess is the
    real page (verified live 2026-09-04).

    A link shared by MORE THAN ONE feed item is dropped, not kept. A url that several
    episodes point at is a show-level or marketing page, not an episode page — the same
    failure this repo already documents for Hard Fork, whose Taddy websiteUrl is one
    generic url for every episode (see show_config.taddy_episode_url and
    import_transcripts.episode_url_key). TAL's live feed has two such: the bare site root
    (also caught by is_tal_episode_page_url) and /lifepartners, the Supercast
    subscription pitch it hands out for promo items. Belt and braces on purpose — the
    denylist catches the ones we have seen, this catches the shape.

    Pure so it can be tested against a frozen feed; the fetch is fetch_feed_page_links.
    """
    candidates: dict[str, str] = {}
    link_users: dict[str, int] = {}
    for item in items:
        title = _title_key(item.get("title"))
        link = (item.get("link") or "").strip()
        if not (title and is_tal_episode_page_url(link)):
            continue
        if title not in candidates:
            candidates[title] = link
            link_users[link] = link_users.get(link, 0) + 1
    return {t: link for t, link in candidates.items() if link_users[link] == 1}


def fetch_feed_page_links(feed_url: Optional[str] = None) -> dict[str, str]:
    """Read TAL's RSS and return title -> page url. Empty dict if the feed is unreachable.

    Empty rather than raising: a feed outage should degrade to the slug fallback, not
    take out the Monday music run. The caller logs how many it resolved and how.

    Reuses the Gabfest importer's parse_feed (defusedxml, already hermetically tested by
    tests/test_import_gabfest.py) — the same reuse feed_check.rss_recent_episodes makes,
    for the same reason: one parser for this project's RSS, not three.
    """
    if feed_url is None:
        feed_url = SHOWS["tal"].fallback_website_url

    # Deliberately OUTSIDE the try: a broken import is a bug in this repo, not a feed
    # outage, and swallowing it would silently disable the authoritative url source for
    # good while every run still reported success.
    import requests

    from scrapers.gabfest.import_gabfest import parse_feed

    try:
        resp = requests.get(
            feed_url, timeout=30, headers={"User-Agent": "list-maker-tal-scrape"}
        )
        resp.raise_for_status()  # don't hand a 404 error page to an XML parser
    except Exception as exc:  # noqa: BLE001
        print(f"  TAL feed unreachable ({exc}); falling back to title slugs")
        return {}

    # The PARSE is its own try with its own message. Folding it into the network one
    # reported a parse_feed regression as "feed unavailable" — a misdiagnosis, and exactly
    # the quiet fallback the split above is meant to prevent.
    try:
        return page_links_from_feed_items(parse_feed(resp.content))
    except Exception as exc:  # noqa: BLE001
        print(f"  TAL feed did not PARSE ({exc}) — this is our bug, not an outage; "
              "falling back to title slugs")
        return {}


def resolve_page_url(row: dict, feed_links: Optional[dict[str, str]] = None) -> Optional[str]:
    """The thisamericanlife.org page to Firecrawl for this row, or None if unknowable.

    Three sources, most trustworthy first:

      1. The row's own url, when it already IS a TAL page url. Rows the website scraper
         discovered carry the real page, including the unnumbered ones a slug could never
         reach (/blackjack, /bless-this-mess).
      2. The RSS <link> for a matching title — authoritative, but a rolling 15-item window
         (measured 2026-09-04), so it covers roughly the last four months only, and only
         for items whose link is episode-specific (page_links_from_feed_items drops the
         promo links TAL shares across items).
      3. tal_episode_page_url(title), the derived slug. Best effort; see its docstring.

    None is a real answer, not a failure to try. Row 7422 ("Ira (Reluctantly) Gives a
    Graduation Speech") has no episode page at all — the feed points it at /lifepartners,
    a subscription pitch — so the caller reports it as unresolved rather than paying for a
    fetch that can only come back empty.

    Never returns an api.taddy.org url. That is the second half of the 2026-08 bug: even
    when a Taddy-discovered row WAS queued, the fetch pointed Firecrawl at the identity
    url, which has no "## Song:" sections on it, so the parse found nothing and the run
    still reported success.
    """
    if is_tal_episode_page_url(row.get("url")):
        return row["url"]
    from_feed = (feed_links or {}).get(_title_key(row.get("title")))
    if is_tal_episode_page_url(from_feed):
        return from_feed
    derived = tal_episode_page_url(row.get("title"))
    return derived if is_tal_episode_page_url(derived) else None


def resolve_page_urls(
    episodes: Iterable[dict], feed_links: Optional[dict[str, str]] = None
) -> tuple[list[dict], list[dict]]:
    """Split episodes into (resolved, unresolved), stamping `page_url` on the resolved.

    Unresolved episodes are RETURNED, not dropped silently — the caller has to say out
    loud that it could not find a page for them. "Nothing to do" and "couldn't check" are
    different outcomes and this pipeline has already paid once for conflating them.
    """
    resolved: list[dict] = []
    unresolved: list[dict] = []
    for row in episodes:
        page_url = resolve_page_url(row, feed_links)
        if page_url:
            resolved.append({**row, "page_url": page_url})
        else:
            unresolved.append(row)
    return resolved, unresolved


# =============================================================================
# Firecrawl
# =============================================================================

async def fetch_episode(
    client: httpx.AsyncClient,
    episode_id: int,
    url: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Fetch a single episode via Firecrawl API."""
    async with semaphore:
        try:
            response = await client.post(
                "https://api.firecrawl.dev/v1/scrape",
                json={
                    "url": url,
                    "formats": ["markdown"],
                },
                timeout=FIRECRAWL_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            return {
                "db_id": episode_id,  # Database row ID, NOT the TAL episode number
                "url": url,
                "success": True,
                "markdown": data.get("data", {}).get("markdown", ""),
                "metadata": data.get("data", {}).get("metadata", {}),
                "fetched_at": datetime.now().isoformat(),
            }
        except httpx.TimeoutException:
            return {
                "db_id": episode_id,
                "url": url,
                "success": False,
                "error": "Timeout",
                "fetched_at": datetime.now().isoformat(),
            }
        except httpx.HTTPStatusError as e:
            return {
                "db_id": episode_id,
                "url": url,
                "success": False,
                "error": f"HTTP {e.response.status_code}",
                "fetched_at": datetime.now().isoformat(),
            }
        except Exception as e:
            return {
                "db_id": episode_id,
                "url": url,
                "success": False,
                "error": str(e),
                "fetched_at": datetime.now().isoformat(),
            }


def save_result(result: dict):
    """Save fetch result to JSON file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = OUTPUT_DIR / f"{result['db_id']}.json"
    with open(filepath, "w") as f:
        json.dump(result, f, indent=2)


# =============================================================================
# Main
# =============================================================================

def plan_fetch(
    limit: Optional[int] = None,
    published_since: Optional[date] = None,
) -> tuple[list[dict], list[dict]]:
    """Everything that decides WHAT gets fetched and FROM WHERE, with no fetching.

    Returned as (resolved, unresolved) so both main() and scrapers/tal/scrape.py work
    from one queue and one url map — the previous split, where scrape.py queried and then
    fetch.main() queried again, meant the preview and the fetch could disagree.
    """
    episodes = get_episodes_missing_songs(limit, published_since)
    return resolve_page_urls(episodes, fetch_feed_page_links() if episodes else {})


async def main(
    limit: int = None,
    dry_run: bool = False,
    episodes: Optional[list[dict]] = None,
    published_since: Optional[date] = None,
):
    """Fetch the page of every TAL episode still missing songs.

    `episodes` accepts an already-resolved queue (each row carrying `page_url`) so the
    orchestrator does not re-query and risk a different answer than the one it printed.

    Returns {"success": n, "errors": n} — ATTEMPTS ARE NOT RESULTS. The caller used to
    report len(queue) as "episodes scraped", so a run where Firecrawl 402'd on all 24
    still printed a confident number next to "Songs found: 0". That is the same
    reported-success-for-doing-nothing shape this whole module exists to remove, and it
    does not get to come back one layer up.
    """
    if episodes is None:
        episodes, unresolved = plan_fetch(limit, published_since)
        print(f"Found {len(episodes) + len(unresolved)} episodes missing songs in database")
        for ep in unresolved:
            # Loud, per episode: a real gap no later step can recover.
            print(f"  NO PAGE URL for {ep['id']}: {ep.get('title')!r} — skipped, not fetched")

    queued_ids = {ep["id"] for ep in episodes}
    cached = get_already_fetched() & queued_ids
    if cached:
        # Reported, not subtracted — see get_already_fetched. The DB is the queue.
        # Intersected with the queue so "of these" is true: the cache can hold thousands
        # of ids that are not in today's queue at all.
        print(f"  ({len(cached)} of these have a local JSON from a previous run)")

    if not episodes:
        print("Nothing to fetch!")
        return {"success": 0, "errors": 0}

    print(f"Will fetch {len(episodes)} episodes")

    if dry_run:
        print("\nDry run - would fetch:")
        for ep in episodes[:10]:
            print(f"  {ep['id']}: {ep['page_url']}")
        if len(episodes) > 10:
            print(f"  ... and {len(episodes) - 10} more")
        return {"success": 0, "errors": 0}

    # Fetch with concurrency limit
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        print("Error: FIRECRAWL_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    success_count = 0
    error_count = 0

    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        # Process in batches for progress reporting
        batch_size = 20
        for i in range(0, len(episodes), batch_size):
            batch = episodes[i:i + batch_size]

            tasks = [
                # page_url, never ep["url"] — that one is the Taddy identity for every
                # episode discovery has found since 2026-08.
                fetch_episode(client, ep["id"], ep["page_url"], semaphore)
                for ep in batch
            ]

            results = await asyncio.gather(*tasks)

            for result in results:
                save_result(result)
                if result["success"]:
                    success_count += 1
                else:
                    error_count += 1
                    print(f"  Error: {result['db_id']} - {result.get('error', 'Unknown')}")

            print(f"Progress: {i + len(batch)}/{len(episodes)} ({success_count} ok, {error_count} errors)")

    print(f"\nDone! Fetched {success_count} episodes, {error_count} errors")
    print(f"JSON files saved to: {OUTPUT_DIR}")
    return {"success": success_count, "errors": error_count}


if __name__ == "__main__":
    # One env loader, the shared one. The hand-rolled pair this replaced pointed
    # load_dotenv at pipeline/scrapers/.env.local — a path that has never existed — so a
    # standalone run got no DATABASE_URL unless the shell already had one.
    from common import load_environment

    load_environment()

    parser = argparse.ArgumentParser(description="Fetch TAL episodes via Firecrawl")
    parser.add_argument("--limit", type=int, help="Max episodes to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fetched")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Only consider episodes published on/after this date "
            f"(default {DEFAULT_SONG_SCRAPE_FLOOR}). Lower it for a deliberate backfill — "
            "it is a flag rather than the default so re-reading the archive is a choice."
        ),
    )
    args = parser.parse_args()

    asyncio.run(main(limit=args.limit, dry_run=args.dry_run, published_since=args.since))

"""Podcast-cited candidates that already carry a URL.

The third intake source, and the oldest: when the extractor pulls a `blog_post`
mention (or any mention on a registered blog domain) that came with a `source_url`,
the podcast has already handed us the link — nothing to resolve, nothing to guess.
This query is what produced the 45 rows sitting in the Notion queue today; it moved
here from `build_pull_queue.py` when the checkbox model was retired, unchanged in
what it selects and changed only in what it returns (Candidates, not queue rows).

Its sibling, `links.py`, handles the harder half: report/paper/survey mentions with
NO url, resolved by search. Both emit `source="podcast-cited"` Candidates and both
dedupe on the canonical URL, so a report the notes linked AND the search found lands
as one row.

Already-ingested URLs are excluded in SQL rather than surfaced as `duplicate`
pre-checks: the podcasts cite posts we already hold constantly, and a Notion log row
per re-citation would bury the candidates that need a decision. The count of what
was excluded is logged, so "nothing new" is still distinguishable from "didn't look"
(docs/principles.md). The duplicate pre-check stays as the safety net for feed
sources, where a post really can arrive after Kevin saved it by hand.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

from pipeline.common import get_logger
from pipeline.scrapers.blog.import_blog import canonicalize_url, is_probable_post_url
from pipeline.scrapers.intake.sources import Candidate
from pipeline.show_config import SHOWS

log = get_logger("pipeline.intake.mentions")


def registered_blog_domains() -> set[str]:
    """Blog domains we already carry as shows — derived from show_config so there is
    no second list to drift out of date."""
    domains = set()
    for cfg in SHOWS.values():
        if cfg.medium == "blog" and cfg.fallback_website_url:
            host = urlsplit(cfg.fallback_website_url).netloc.lower().removeprefix("www.")
            if host:
                domains.add(host)
    return domains


def discover_cited_candidates(conn, since_days: Optional[int] = None) -> list[Candidate]:
    """URLs the podcasts pointed at, newest-cited first, minus what we already hold.

    `since_days=None` scans the whole history on purpose: the first run of the judged
    intake is meant to re-judge the June backlog the checkbox never cleared. Later
    runs re-see those rows, find them already in `intake_candidates`, and do nothing.
    """
    domains = sorted(registered_blog_domains())
    domain_pattern = "|".join(re.escape(d) for d in domains) or "^$"
    window_clause = "AND ep.publish_date >= CURRENT_DATE - %s" if since_days else ""
    params: list = [domain_pattern]
    if since_days:
        params.append(since_days)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.source_url,
                   COUNT(DISTINCT m.episode_id) AS cited_in_episodes,
                   MAX(ep.publish_date)::date AS last_cited,
                   (ARRAY_AGG(m.context_snippet ORDER BY ep.publish_date DESC NULLS LAST))[1] AS why,
                   (ARRAY_AGG(m.canonical_name ORDER BY ep.publish_date DESC NULLS LAST))[1] AS cited_as,
                   ARRAY_AGG(DISTINCT s.name) AS shows
            FROM ai_mentions m
            JOIN episodes ep ON ep.id = m.episode_id
            JOIN shows s ON s.id = ep.show_id
            WHERE m.source_url IS NOT NULL AND m.source_url <> ''
              AND (
                    m.source_url ~* ('^https?://(www\\.)?(' || %s || ')/')
                 OR m.mention_type = 'blog_post'
              )
              {window_clause}
            GROUP BY m.source_url
            ORDER BY MAX(ep.publish_date) DESC NULLS LAST
            """,
            tuple(params),
        )
        rows = [dict(r) for r in cur.fetchall()]

    # Collapse http/https/utm variants of the same post before anything else looks at
    # them — episodes.url is canonical, so an uncanonical key would miss the dedup.
    by_canonical: dict[str, dict] = {}
    not_a_post = 0
    for row in rows:
        url = canonicalize_url(row["source_url"])
        if not is_probable_post_url(url):
            not_a_post += 1  # domain roots and section indexes are not pullable posts
            continue
        merged = by_canonical.setdefault(url, {**row, "url": url, "cited_in_episodes": 0})
        merged["cited_in_episodes"] += int(row["cited_in_episodes"] or 0)
        merged["shows"] = sorted({*(merged.get("shows") or []), *(row["shows"] or [])})

    if not by_canonical:
        log.info("podcast-cited: %d cited URL(s), none of them post-shaped", len(rows))
        return []

    from pipeline.scrapers.intake.store import already_ingested_urls  # local: avoids a cycle
    already = already_ingested_urls(conn, list(by_canonical))
    fresh = [c for u, c in by_canonical.items() if u not in already]
    log.info(
        "podcast-cited: %d cited URL(s) → %d not post-shaped, %d distinct posts, "
        "%d already ingested, %d candidate(s)",
        len(rows), not_a_post, len(by_canonical), len(already), len(fresh),
    )
    return [_as_candidate(row) for row in fresh]


def _as_candidate(row: dict) -> Candidate:
    """Title is left empty on purpose: the scrape knows the post's real name, and this
    row only knows what the host called it. `cited_as` rides in the provenance."""
    return Candidate(
        source="podcast-cited",
        title="",
        url=row["url"],
        published_on=None,  # the CITE date is not the POST date; don't pretend otherwise
        category=[],
        blurb=str(row.get("why") or "")[:500],
        discovered_via={
            "cited_as": row.get("cited_as"),
            "shows": row.get("shows") or [],
            "cited_in_episodes": int(row.get("cited_in_episodes") or 0),
            "last_cited": str(row["last_cited"]) if row.get("last_cited") else None,
        },
    )

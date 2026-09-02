"""Link resolution: turn a podcast's "the Ramp AI Index says…" into a URL an agent can open.

Why this exists: of 103 report/paper/survey mentions the tech shows made in the 120
days to 2026-09-02, three carried a URL. The extractor is told never to invent one,
and the show notes are no help — AI Daily's notes hold sponsor links and the host's
own promos and nothing else (measured: 248 links in 30 days, none to a cited
report). So the URL has to be found, and a web search on the cited name finds the
primary source most of the time (probe of 14 real mentions, 2026-09-02: 8 primary
sources ranked first — openai.com/signals, ramp.com/data/ai-index, arxiv, the
authors' own sites). The rest are generic names ("BCG paper", "Meter investigation")
that no search can pin, and those stay candidates instead of guesses.

The resolved URL is written back to the mention (source_url, link_status
auto_verified, link_confidence, every search hit in link_candidates), and the
mention becomes a `podcast-cited` intake Candidate — the judge decides whether the
page is worth saving. Recall over precision here: the judge is the filter and a
Firecrawl scrape is cheap; a wrong resolve costs one scrape, a missed one costs the
report Kevin needed.

Reuses discover_links.search_firecrawl / apply_mention_updates (the 2026-03 link
hunter that never ran on a schedule); its scoring could never auto-verify a report
(max 0.60 against a 0.90 threshold), which is why the scoring lives here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional
from urllib.parse import urlsplit

from pipeline.scrapers.intake.sources import Candidate

REPORT_TYPES = ("report", "paper", "survey", "blog_post")
AUTO_RESOLVE_SCORE = 0.6
AUTO_RESOLVE_TITLE_RATIO = 0.6
# Words that describe the kind of thing, not which thing. A name with fewer than two
# tokens left after removing these is too generic to resolve ("BCG paper").
GENERIC_TOKENS = {"paper", "report", "study", "survey", "investigation", "essay", "post", "blog",
                  "article", "framework", "index", "act", "the", "a", "an", "of", "on", "and", "for", "new"}
# Places that talk ABOUT a report rather than being it. Kept as candidates, never the
# auto-resolved link. Social posts are the exception (a social_post mention wants x.com).
SECONDARY_DOMAINS = {"linkedin.com": 0.3, "reddit.com": 0.3, "youtube.com": 0.3, "m.youtube.com": 0.3,
                     "facebook.com": 0.3, "x.com": 0.3, "twitter.com": 0.3, "pod.wave.co": 0.3,
                     "substack.com": 0.15, "medium.com": 0.15, "wikipedia.org": 0.1}
PRIMARY_HOST_HINTS = ("arxiv.org", "doi.org", "ssrn.com", "openreview.net")


@dataclass
class Resolution:
    mention_id: int
    url: Optional[str]           # None = nothing safe to auto-resolve
    confidence: float
    candidates: list[dict]       # every search hit with its score, for link_candidates
    query: str


def tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) > 1]


def content_tokens(name: str) -> set[str]:
    return {t for t in tokens(name) if t not in GENERIC_TOKENS}


def domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def org_hints(mention: dict) -> list[str]:
    """Capitalized / all-caps words in the context that aren't in the name — the org
    that published the thing ("KPMG argues…", "shared by RAMP's economist")."""
    name_tokens = set(tokens(mention.get("canonical_name", "")))
    hints: list[str] = []
    for word in re.findall(r"\b[A-Z][A-Za-z0-9]{1,}\b", mention.get("context_snippet") or mention.get("ctx") or ""):
        low = word.lower()
        if low in name_tokens or low in GENERIC_TOKENS or low in {"the", "in", "this", "we", "it", "and", "i"}:
            continue
        if low not in hints:
            hints.append(low)
    return hints[:3]


def build_query(mention: dict) -> str:
    name = mention["canonical_name"].strip()
    hints = org_hints(mention)
    # A short/generic name needs the org to mean anything; a specific title stands alone.
    if len(content_tokens(name)) < 2 and hints:
        return f'"{name}" {hints[0]}'
    return f'"{name}"'


def score(mention: dict, url: str, title: str) -> tuple[float, float, bool]:
    """(score, title_ratio, primary). title_ratio = share of the name's content tokens
    found in the result's title or URL; primary = the hit earned a primary-source bonus
    (the org's own domain, or a paper host); score adds that bonus and a secondary penalty."""
    name_tokens = content_tokens(mention["canonical_name"]) or set(tokens(mention["canonical_name"]))
    hit_tokens = set(tokens(title)) | set(tokens(url))
    ratio = len(name_tokens & hit_tokens) / max(1, len(name_tokens))
    s = 0.6 * ratio
    host = domain(url)
    orgs = [t for t in (set(tokens(mention["canonical_name"])) | set(org_hints(mention))) if len(t) >= 3]
    # The org's own site is the primary source: ramp.com for "Ramp AI Index". Equality
    # on the host's first label, not a substring — "white" inside whitecube.ai is not the
    # White House (a real miss from the 2026-09-02 probe).
    primary = False
    if host.split(".")[0] in orgs:
        s += 0.3
        primary = True
    if mention.get("mention_type") == "paper" and any(host.endswith(h) for h in PRIMARY_HOST_HINTS):
        s += 0.2
        primary = True
    for sec, penalty in SECONDARY_DOMAINS.items():
        if host == sec or host.endswith("." + sec):
            if not (mention.get("mention_type") == "social_post" and sec in ("x.com", "twitter.com")):
                s -= penalty
            break
    return max(0.0, min(1.0, round(s, 3))), round(ratio, 3), primary


def resolve_one(mention: dict, results: list[dict], query: str) -> Resolution:
    scored = []
    for rank, r in enumerate(results):
        url = (r.get("url") or "").strip()
        if not url:
            continue
        s, ratio, primary = score(mention, url, r.get("title") or "")
        scored.append({"url": url, "title": (r.get("title") or "")[:200], "score": s,
                       "title_ratio": ratio, "primary": primary, "rank": rank})
    scored.sort(key=lambda x: -x["score"])  # stable: search rank breaks ties
    generic = len(content_tokens(mention["canonical_name"])) < 2
    best = scored[0] if scored else None
    # A full-title match alone is trusted only when the search engine ranked it first:
    # for an exact-title query the primary source comes first (darioamodei.com for
    # "Machines of Loving Grace"); a full-title match ranked third is commentary
    # (happyrock.cloud's "deep dive" on the White House framework). A primary-source
    # bonus (the org's own domain, arxiv) is trusted at any rank.
    trusted = bool(best) and (best["primary"] or best["rank"] == 0)
    if best and not generic and trusted and best["score"] >= AUTO_RESOLVE_SCORE and best["title_ratio"] >= AUTO_RESOLVE_TITLE_RATIO:
        return Resolution(mention["mention_id"], best["url"], best["score"], scored, query)
    return Resolution(mention["mention_id"], None, best["score"] if best else 0.0, scored, query)


def as_candidate(mention: dict, resolution: Resolution) -> Candidate:
    published = mention.get("publish_date")
    if isinstance(published, str):
        published = date.fromisoformat(published)
    return Candidate(
        source="podcast-cited",
        title=mention["canonical_name"],
        url=resolution.url,
        published_on=published,
        category=[mention["mention_type"]],
        blurb=(mention.get("context_snippet") or mention.get("ctx") or "")[:500],
        discovered_via={"mention_id": mention["mention_id"], "episode_id": mention.get("episode_id"),
                        "show": mention.get("show"), "link_confidence": resolution.confidence},
    )


# ── the DB side ─────────────────────────────────────────────────────────────

def unresolved_mentions(conn, since_days: int = 14, show_ids: tuple[int, ...] = (3, 48), limit: int = 40) -> list[dict]:
    """Report-like editorial mentions from the tech shows with no URL yet, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id AS mention_id, m.episode_id, m.mention_type, m.canonical_name,
                   m.context_snippet, ep.publish_date, s.name AS show
            FROM ai_mentions m
            JOIN episodes ep ON ep.id = m.episode_id
            JOIN shows s ON s.id = ep.show_id
            WHERE ep.show_id = ANY(%s)
              AND m.mention_type = ANY(%s)
              AND m.is_editorial = TRUE
              AND (m.source_url IS NULL OR BTRIM(m.source_url) = '')
              AND (m.link_candidates = '[]'::jsonb OR m.link_candidates IS NULL)
              AND ep.publish_date >= CURRENT_DATE - %s
            ORDER BY ep.publish_date DESC, m.id
            LIMIT %s
            """,
            (list(show_ids), list(REPORT_TYPES), since_days, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def resolve_mentions(mentions: list[dict], search: Callable[[str, int], list[dict]],
                     max_results: int = 5) -> list[Resolution]:
    out: list[Resolution] = []
    for m in mentions:
        q = build_query(m)
        try:
            hits = search(q, max_results)
        except Exception as exc:  # noqa: BLE001 — one failed search must not sink the batch
            hits = []
            q = f"{q}  [search failed: {str(exc)[:80]}]"
        out.append(resolve_one(m, hits, q))
    return out


def write_back(conn, resolutions: list[Resolution]) -> int:
    """Record every search's candidates; promote the auto-resolved URL. Returns promoted count."""
    from pipeline.scrapers.ai_daily.discover_links import apply_mention_updates
    promoted = 0
    for r in resolutions:
        apply_mention_updates(
            conn, mention_id=r.mention_id, candidates=r.candidates,
            promoted_url=r.url, promoted_platform=None,
            promoted_score=r.confidence if r.url else None,
        )
        promoted += bool(r.url)
    conn.commit()
    return promoted


def podcast_cited_candidates(conn, *, since_days: int = 14, limit: int = 40, dry_run: bool = False,
                             search: Optional[Callable[[str, int], list[dict]]] = None) -> tuple[list[Candidate], list[Resolution]]:
    """The hook run_intake calls: resolve this window's report mentions and hand back the
    ones that now have a URL as Candidates. dry_run skips the write-back."""
    if search is None:
        from pipeline.scrapers.ai_daily.discover_links import search_firecrawl
        search = search_firecrawl
    mentions = unresolved_mentions(conn, since_days=since_days, limit=limit)
    resolutions = resolve_mentions(mentions, search)
    if not dry_run:
        write_back(conn, resolutions)
    by_id = {m["mention_id"]: m for m in mentions}
    cands = [as_candidate(by_id[r.mention_id], r) for r in resolutions if r.url]
    return cands, resolutions

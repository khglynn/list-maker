#!/usr/bin/env python3
"""
Lightweight link discovery for unresolved AI Daily mentions.

Targets:
- account
- report
- survey
- paper
- blog_post
- social_post

Writes:
- ai_reference_link_candidates (all candidates)
- ai_entity_mentions.source_url (only high-confidence auto_verified)
- ai_episode_reference_links (promoted high-confidence links)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from dotenv import load_dotenv


REQUEST_TIMEOUT = 25
TARGET_TYPES = ("account", "report", "survey", "paper", "blog_post", "social_post")
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"


def load_environment(repo_root: Path) -> None:
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def get_db_connection():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError("Missing dependency: psycopg2-binary") from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def extract_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def decode_ddg_redirect(url: str) -> str:
    # DuckDuckGo often wraps real URLs in /l/?uddg=...
    try:
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            q = parse_qs(parsed.query)
            uddg = q.get("uddg", [None])[0]
            if uddg:
                return unquote(uddg)
    except Exception:
        pass
    return url


def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    resp = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": query},
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    body = resp.text

    results: list[dict] = []
    # Parse result anchors from html endpoint.
    # Example: <a rel="nofollow" class="result__a" href="...">Title</a>
    for m in re.finditer(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw_url = html.unescape(m.group(1))
        url = decode_ddg_redirect(raw_url)
        title = strip_tags(m.group(2))
        if not url:
            continue
        results.append({"url": url, "title": title})
        if len(results) >= max_results:
            break
    return results


def search_firecrawl(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        return []

    resp = requests.post(
        FIRECRAWL_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"query": query, "limit": max_results},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("data") or []

    out: list[dict] = []
    for item in items:
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not url:
            continue
        out.append({"url": url, "title": title})
        if len(out) >= max_results:
            break
    return out


def search_web(query: str, max_results: int = 5) -> tuple[str, list[dict]]:
    # Firecrawl is primary because DuckDuckGo often times out in this environment.
    try:
        firecrawl_results = search_firecrawl(query, max_results=max_results)
        if firecrawl_results:
            return "firecrawl", firecrawl_results
    except Exception:
        pass

    try:
        ddg_results = search_duckduckgo(query, max_results=max_results)
        if ddg_results:
            return "duckduckgo", ddg_results
    except Exception:
        pass

    return "none", []


def build_queries(mention: dict) -> list[str]:
    mention_type = mention["mention_type"]
    canonical = mention["canonical_name"]
    mention_text = mention["mention_text"] or canonical
    platform = (mention["platform"] or "").lower()
    episode_title = mention["episode_title"]

    queries: list[str] = []
    if mention_type == "account":
        # Strong platform-specific query first.
        if platform in ("x", "twitter"):
            queries.append(f'site:x.com "{canonical}"')
            queries.append(f'site:twitter.com "{canonical}"')
        else:
            queries.append(f'"{canonical}" AI')
            queries.append(f'site:x.com "{canonical}"')
    elif mention_type in ("survey", "report", "paper", "blog_post"):
        queries.append(f'"{canonical}"')
        queries.append(f'"{canonical}" "{episode_title}"')
        queries.append(f'"{canonical}" AI')
    elif mention_type == "social_post":
        if platform in ("x", "twitter"):
            queries.append(f'site:x.com "{mention_text}"')
            queries.append(f'site:x.com "{canonical}"')
        else:
            queries.append(f'"{mention_text}"')
            queries.append(f'"{canonical}"')
    else:
        queries.append(f'"{canonical}"')

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen = set()
    for q in queries:
        if q not in seen:
            deduped.append(q)
            seen.add(q)
    return deduped


def score_candidate(mention: dict, candidate_url: str, candidate_title: str) -> float:
    mention_type = mention["mention_type"]
    canonical = mention["canonical_name"]
    platform = (mention["platform"] or "").lower()
    domain = extract_domain(candidate_url)

    score = 0.0
    canonical_tokens = set(normalize_tokens(canonical))
    title_tokens = set(normalize_tokens(candidate_title))
    url_tokens = set(normalize_tokens(candidate_url))
    overlap = len(canonical_tokens.intersection(title_tokens.union(url_tokens)))
    token_ratio = overlap / max(1, len(canonical_tokens))

    score += 0.45 * token_ratio

    if mention_type == "account":
        if domain in ("x.com", "twitter.com"):
            score += 0.35
        if platform in ("x", "twitter") and domain in ("x.com", "twitter.com"):
            score += 0.1
    elif mention_type in ("survey", "report", "paper", "blog_post"):
        if any(domain.endswith(d) for d in ("aidailybrief.ai", "openai.com", "anthropic.com", "arxiv.org", "wikipedia.org")):
            score += 0.15
    elif mention_type == "social_post":
        if domain in ("x.com", "twitter.com"):
            score += 0.25

    # Penalize obviously low-value pages.
    if any(x in candidate_url.lower() for x in ("/search?", "/status", "/explore")):
        score -= 0.2

    return max(0.0, min(1.0, score))


def mention_platform_from_url(url: str) -> Optional[str]:
    d = extract_domain(url)
    if d in ("x.com", "twitter.com"):
        return "x"
    if d.endswith("arxiv.org"):
        return "arxiv"
    if d.endswith("github.com"):
        return "github"
    return None


def candidate_exists(conn, mention_id: int, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM ai_reference_link_candidates
            WHERE mention_id = %s AND candidate_url = %s
            LIMIT 1;
            """,
            (mention_id, url),
        )
        return cur.fetchone() is not None


def insert_candidate(
    conn,
    *,
    mention_id: int,
    entity_id: Optional[int],
    url: str,
    score: float,
    verification_status: str,
    evidence: dict,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_reference_link_candidates (
              mention_id, entity_id, candidate_url, discovery_method,
              match_confidence, verification_status, evidence, created_at
            )
            VALUES (%s, %s, %s, 'duckduckgo', %s, %s, %s::jsonb, NOW());
            """,
            (mention_id, entity_id, url, score, verification_status, json.dumps(evidence)),
        )


def promote_candidate(
    conn,
    *,
    mention: dict,
    url: str,
    platform: Optional[str],
    score: float,
) -> None:
    mention_id = mention["mention_id"]
    run_id = mention["run_id"]
    episode_id = mention["episode_id"]
    entity_id = mention["entity_id"]

    with conn.cursor() as cur:
        # Update mention source_url only if empty.
        cur.execute(
            """
            UPDATE ai_entity_mentions
            SET source_url = %s,
                platform = COALESCE(platform, %s)
            WHERE id = %s
              AND (source_url IS NULL OR BTRIM(source_url) = '');
            """,
            (url, platform, mention_id),
        )

        # Insert into episode reference links only once per episode/entity/url.
        cur.execute(
            """
            SELECT 1
            FROM ai_episode_reference_links
            WHERE episode_id = %s
              AND source_kind = 'link_discovery'
              AND url = %s
              AND COALESCE(linked_entity_id, 0) = COALESCE(%s, 0)
            LIMIT 1;
            """,
            (episode_id, url, entity_id),
        )
        if cur.fetchone() is None:
            cur.execute(
                """
                INSERT INTO ai_episode_reference_links (
                  episode_id, run_id, url, platform, linked_entity_id,
                  source_kind, verification_status, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'link_discovery', 'auto_verified', NOW());
                """,
                (episode_id, run_id, url, platform, entity_id),
            )


def fetch_mentions_for_link_hunt(conn, run_ids: list[int], limit: int) -> list[dict]:
    conditions = [
        "m.is_editorial = TRUE",
        "m.mention_type = ANY(%s)",
        "(m.source_url IS NULL OR BTRIM(m.source_url) = '')",
    ]
    params: list = [list(TARGET_TYPES)]
    if run_ids:
        conditions.append("m.run_id = ANY(%s)")
        params.append(run_ids)

    sql = f"""
        SELECT m.id AS mention_id,
               m.run_id,
               m.episode_id,
               m.entity_id,
               m.mention_type,
               m.mention_text,
               m.platform,
               m.context_snippet,
               COALESCE(e2.canonical_name, m.mention_text) AS canonical_name,
               e.title AS episode_title
        FROM ai_entity_mentions m
        JOIN episodes e ON e.id = m.episode_id
        LEFT JOIN ai_entities e2 ON e2.id = m.entity_id
        WHERE {' AND '.join(conditions)}
        ORDER BY m.run_id, m.id
        LIMIT %s;
    """
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discover candidate links for AI mentions")
    p.add_argument("--run-ids", type=str, default="4,3", help="Comma-separated run IDs to process")
    p.add_argument("--limit", type=int, default=200, help="Max mentions to process")
    p.add_argument("--max-candidates-per-mention", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def parse_run_ids(value: str) -> list[int]:
    out: list[int] = []
    for x in value.split(","):
        x = x.strip()
        if not x:
            continue
        try:
            out.append(int(x))
        except ValueError:
            pass
    return out


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)
    conn = get_db_connection()

    inserted_candidates = 0
    promoted_links = 0
    processed = 0
    run_ids = parse_run_ids(args.run_ids)
    print(f"run_ids={run_ids}, limit={args.limit}, dry_run={args.dry_run}")
    try:
        mentions = fetch_mentions_for_link_hunt(conn, run_ids=run_ids, limit=args.limit)
        print(f"mentions_to_process={len(mentions)}")
        for mention in mentions:
            processed += 1
            queries = build_queries(mention)
            kept = 0
            best_auto = None
            best_score = -1.0

            for query in queries:
                backend, results = search_web(query, max_results=5)
                if not results:
                    continue

                for rank, item in enumerate(results, start=1):
                    url = item["url"]
                    title = item["title"]
                    score = score_candidate(mention, url, title)
                    status = "auto_verified" if score >= 0.86 else "unverified"
                    evidence = {
                        "search_backend": backend,
                        "query": query,
                        "title": title,
                        "rank": rank,
                        "mention_type": mention["mention_type"],
                        "canonical_name": mention["canonical_name"],
                    }
                    if candidate_exists(conn, mention["mention_id"], url):
                        continue

                    if not args.dry_run:
                        insert_candidate(
                            conn,
                            mention_id=mention["mention_id"],
                            entity_id=mention["entity_id"],
                            url=url,
                            score=score,
                            verification_status=status,
                            evidence=evidence,
                        )
                    inserted_candidates += 1
                    kept += 1

                    if score > best_score:
                        best_score = score
                        best_auto = (url, mention_platform_from_url(url), score)

                    if kept >= args.max_candidates_per_mention:
                        break
                if kept >= args.max_candidates_per_mention:
                    break

            # Promote only strongest hit.
            if best_auto and best_auto[2] >= 0.90:
                url, platform, score = best_auto
                if not args.dry_run:
                    promote_candidate(
                        conn,
                        mention=mention,
                        url=url,
                        platform=platform,
                        score=score,
                    )
                promoted_links += 1

            if processed % 25 == 0:
                print(
                    f"processed={processed}/{len(mentions)} candidates={inserted_candidates} promoted={promoted_links}",
                    flush=True,
                )

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        print(
            f"done processed={processed} candidates_inserted={inserted_candidates} "
            f"promoted_links={promoted_links} dry_run={args.dry_run}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

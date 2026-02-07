#!/usr/bin/env python3
"""
Link discovery for unresolved AI Daily mentions in lean schema.

Writes directly to ai_mentions:
- link_candidates (JSON array)
- source_url / link_status / link_confidence (for strong matches)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests
from dotenv import load_dotenv


REQUEST_TIMEOUT = 25
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"
TARGET_TYPES = ("account", "report", "survey", "paper", "blog_post", "social_post")
ACCOUNT_STOPWORDS = {"the", "and", "for", "with", "from", "ai", "official"}


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
    try:
        data = search_firecrawl(query, max_results=max_results)
        if data:
            return "firecrawl", data
    except Exception:
        pass
    try:
        data = search_duckduckgo(query, max_results=max_results)
        if data:
            return "duckduckgo", data
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
        if any(
            domain.endswith(d)
            for d in ("aidailybrief.ai", "openai.com", "anthropic.com", "arxiv.org", "wikipedia.org")
        ):
            score += 0.15
    elif mention_type == "social_post":
        if domain in ("x.com", "twitter.com"):
            score += 0.25

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


def extract_handle_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
    except Exception:
        return ""
    if not parts:
        return ""
    return parts[0].lstrip("@").lower()


def is_acronym(text: str) -> bool:
    clean = re.sub(r"[^A-Za-z]", "", text)
    return len(clean) >= 3 and clean.isupper()


def is_safe_auto_match(
    mention: dict,
    candidate_url: str,
    candidate_title: str,
    score: float,
    threshold: float,
) -> bool:
    if score < threshold:
        return False

    mention_type = mention["mention_type"]
    canonical = mention["canonical_name"]
    domain = extract_domain(candidate_url)
    title_tokens = set(normalize_tokens(candidate_title))
    canonical_tokens = [t for t in normalize_tokens(canonical) if t not in ACCOUNT_STOPWORDS]

    if mention_type == "account":
        if domain not in ("x.com", "twitter.com"):
            return False
        handle = extract_handle_from_url(candidate_url)
        if not handle:
            return False

        if len(canonical_tokens) >= 2:
            long_tokens = [t for t in canonical_tokens if len(t) >= 3]
            if not long_tokens:
                return False
            in_handle = sum(1 for t in long_tokens if t in handle)
            in_title = sum(1 for t in long_tokens if t in title_tokens)
            return in_handle >= 1 and in_title >= 1

        # Single-token account names are ambiguous. Only allow acronym-style exact-ish matches.
        single = canonical_tokens[0] if canonical_tokens else ""
        if not single:
            return False
        if is_acronym(canonical) and len(single) >= 3:
            return single in handle and single in title_tokens
        return False

    if mention_type == "social_post":
        if domain in ("x.com", "twitter.com"):
            return "/status/" in candidate_url
        return score >= (threshold + 0.03)

    return True


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
               m.canonical_name,
               m.platform,
               m.context_snippet,
               e.title AS episode_title
        FROM ai_mentions m
        JOIN episodes e ON e.id = m.episode_id
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
    p.add_argument("--run-ids", type=str, default="", help="Comma-separated run IDs to process")
    p.add_argument("--limit", type=int, default=300, help="Max mentions to process")
    p.add_argument("--max-candidates-per-mention", type=int, default=3)
    p.add_argument("--auto-threshold", type=float, default=0.90)
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


def apply_mention_updates(
    conn,
    *,
    mention_id: int,
    candidates: list[dict],
    promoted_url: Optional[str],
    promoted_platform: Optional[str],
    promoted_score: Optional[float],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_mentions
            SET link_candidates = %s::jsonb,
                source_url = CASE
                  WHEN %s IS NOT NULL THEN %s
                  ELSE source_url
                END,
                platform = CASE
                  WHEN %s IS NOT NULL AND (platform IS NULL OR BTRIM(platform) = '') THEN %s
                  ELSE platform
                END,
                link_status = CASE
                  WHEN %s IS NOT NULL THEN 'auto_verified'
                  ELSE link_status
                END,
                link_confidence = CASE
                  WHEN %s IS NOT NULL THEN %s
                  ELSE link_confidence
                END,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (
                json.dumps(candidates),
                promoted_url,
                promoted_url,
                promoted_platform,
                promoted_platform,
                promoted_url,
                promoted_url,
                promoted_score,
                mention_id,
            ),
        )


def update_entity_primary_url(conn, entity_id: Optional[int], url: str) -> None:
    if not entity_id:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_entities
            SET primary_url = COALESCE(primary_url, %s),
                updated_at = NOW()
            WHERE id = %s;
            """,
            (url, int(entity_id)),
        )


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
            candidates_by_url: dict[str, dict] = {}
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
                    candidate = {
                        "url": url,
                        "title": title,
                        "query": query,
                        "rank": rank,
                        "backend": backend,
                        "score": round(score, 4),
                    }

                    existing = candidates_by_url.get(url)
                    if existing is None or score > float(existing["score"]):
                        candidates_by_url[url] = candidate
                    inserted_candidates += 1

                    if score > best_score:
                        best_score = score
                        best_auto = (url, mention_platform_from_url(url), score)

                    if len(candidates_by_url) >= args.max_candidates_per_mention:
                        break
                if len(candidates_by_url) >= args.max_candidates_per_mention:
                    break

            candidates = sorted(
                candidates_by_url.values(),
                key=lambda c: (-float(c["score"]), c["url"]),
            )
            promoted_url = None
            promoted_platform = None
            promoted_score = None
            if best_auto:
                auto_url, auto_platform, auto_score = best_auto
                # Find title for best candidate so we can apply stricter safety checks.
                best_title = ""
                for c in candidates:
                    if c["url"] == auto_url:
                        best_title = c.get("title", "")
                        break
                if is_safe_auto_match(
                    mention,
                    candidate_url=auto_url,
                    candidate_title=best_title,
                    score=auto_score,
                    threshold=args.auto_threshold,
                ):
                    promoted_url, promoted_platform, promoted_score = best_auto
                    promoted_links += 1

            if not args.dry_run:
                apply_mention_updates(
                    conn,
                    mention_id=int(mention["mention_id"]),
                    candidates=candidates,
                    promoted_url=promoted_url,
                    promoted_platform=promoted_platform,
                    promoted_score=promoted_score,
                )
                if promoted_url:
                    update_entity_primary_url(conn, mention.get("entity_id"), promoted_url)
                conn.commit()

            if processed % 25 == 0:
                print(
                    f"processed={processed}/{len(mentions)} candidates_seen={inserted_candidates} "
                    f"auto_promoted={promoted_links}",
                    flush=True,
                )

        if args.dry_run:
            conn.rollback()
        print(
            f"done processed={processed} candidates_seen={inserted_candidates} "
            f"auto_promoted={promoted_links} dry_run={args.dry_run}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

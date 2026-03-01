#!/usr/bin/env python3
"""
Import podcast transcripts from Taddy into Neon.

Default test run imports 5 new transcripts per show for:
- ai-daily-brief
- pchh
- sop

Required env vars:
- DATABASE_URL (or NEON_DATABASE_URL)
- TADDY_USER_ID
- TADDY_API_KEY
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

TADDY_API_URL = "https://api.taddy.org"
REQUEST_TIMEOUT = 60
MAX_TADDY_RETRIES = 5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class ShowConfig:
    slug: str
    name: str
    series_uuid: str
    fallback_website_url: Optional[str] = None


SHOWS: dict[str, ShowConfig] = {
    "ai-daily-brief": ShowConfig(
        slug="ai-daily-brief",
        name="The AI Daily Brief: Artificial Intelligence News and Analysis",
        series_uuid="60fabbea-f51e-4c8b-82b4-1cbd57fe8c02",
        fallback_website_url="https://www.aidailybrief.ai/",
    ),
    "pchh": ShowConfig(
        slug="pchh",
        name="Pop Culture Happy Hour",
        series_uuid="81b2a312-6976-4d22-bc54-4e3991fee332",
        fallback_website_url="https://www.npr.org/podcasts/510282/pop-culture-happy-hour",
    ),
    "sop": ShowConfig(
        slug="sop",
        name="Switched on Pop",
        series_uuid="97ed51a4-460e-4dc8-8db5-30df96ad59bc",
        fallback_website_url="https://switchedonpop.com",
    ),
}

RAW_CONTENT_SHOW_SLUGS = {"ai-daily-brief", "pchh"}


def get_db_connection():
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: psycopg2-binary. "
            "Install deps with `cd pipeline && pip install -r requirements.txt`."
        ) from exc

    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL (or NEON_DATABASE_URL) is required")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            ALTER TABLE episodes
              ADD COLUMN IF NOT EXISTS description_body TEXT,
              ADD COLUMN IF NOT EXISTS episode_number INTEGER,
              ADD COLUMN IF NOT EXISTS audio_url TEXT,
              ADD COLUMN IF NOT EXISTS image_url TEXT;
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS episode_transcripts (
              id SERIAL PRIMARY KEY,
              episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
              source_type VARCHAR(64) NOT NULL,
              source_url TEXT,
              transcript_text TEXT NOT NULL,
              is_generated BOOLEAN NOT NULL DEFAULT FALSE,
              model VARCHAR(128),
              created_at TIMESTAMP DEFAULT NOW(),
              updated_at TIMESTAMP DEFAULT NOW(),
              UNIQUE (episode_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_transcripts_source_type
              ON episode_transcripts(source_type);
            """
        )
    conn.commit()


def taddy_query(query: str, user_id: str, api_key: str) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-USER-ID": user_id,
        "X-API-KEY": api_key,
    }
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_TADDY_RETRIES + 1):
        try:
            resp = requests.post(
                TADDY_API_URL,
                headers=headers,
                json={"query": query},
                timeout=REQUEST_TIMEOUT,
            )
            status_code = resp.status_code
            if status_code in RETRYABLE_STATUS_CODES and attempt < MAX_TADDY_RETRIES:
                backoff_seconds = min(2 ** (attempt - 1), 8)
                print(
                    f"  ~ taddy transient HTTP {status_code}; retry "
                    f"{attempt}/{MAX_TADDY_RETRIES} in {backoff_seconds}s"
                )
                time.sleep(backoff_seconds)
                continue

            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                raise RuntimeError(f"Taddy GraphQL error: {payload['errors']}")
            return payload.get("data") or {}
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt >= MAX_TADDY_RETRIES:
                break
            backoff_seconds = min(2 ** (attempt - 1), 8)
            print(
                f"  ~ taddy request error ({exc.__class__.__name__}); retry "
                f"{attempt}/{MAX_TADDY_RETRIES} in {backoff_seconds}s"
            )
            time.sleep(backoff_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Taddy query failed without a raised exception")


def get_series(series_uuid: str, user_id: str, api_key: str) -> Optional[dict[str, Any]]:
    query = f"""
    query {{
      getPodcastSeries(uuid: "{series_uuid}") {{
        uuid
        name
        websiteUrl
        rssUrl
        totalEpisodesCount
        taddyTranscribeStatus
      }}
    }}
    """
    data = taddy_query(query, user_id=user_id, api_key=api_key)
    return data.get("getPodcastSeries")


def get_transcript_credits_remaining(user_id: str, api_key: str) -> int:
    query = """
    query {
      getTranscriptCreditsRemaining
    }
    """
    data = taddy_query(query, user_id=user_id, api_key=api_key)
    return int(data.get("getTranscriptCreditsRemaining") or 0)


def get_latest_episodes(
    series_uuid: str,
    page: int,
    limit_per_page: int,
    user_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    query = f"""
    query {{
      getLatestPodcastEpisodes(uuids:["{series_uuid}"], page:{page}, limitPerPage:{limit_per_page}) {{
        uuid
        guid
        datePublished
        name
        description
        duration
        imageUrl
        audioUrl
        websiteUrl
        episodeNumber
        seasonNumber
        transcriptUrls
        taddyTranscribeStatus
      }}
    }}
    """
    data = taddy_query(query, user_id=user_id, api_key=api_key)
    return data.get("getLatestPodcastEpisodes") or []


def get_episode_transcript(
    episode_uuid: str,
    user_id: str,
    api_key: str,
) -> list[dict[str, Any]]:
    query = f"""
    query {{
      getEpisodeTranscript(
        uuid:"{episode_uuid}",
        useOnDemandCreditsIfNeeded:true,
        style:PARAGRAPH
      ) {{
        id
        text
        speaker
        startTimecode
        endTimecode
      }}
    }}
    """
    data = taddy_query(query, user_id=user_id, api_key=api_key)
    return data.get("getEpisodeTranscript") or []


def epoch_to_date(epoch_seconds: Optional[int]) -> Optional[date]:
    if epoch_seconds is None:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).date()
    except Exception:
        return None


def upsert_show(
    conn,
    slug: str,
    name: str,
    website_url: Optional[str],
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shows (name, slug, website_url, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (slug) DO UPDATE
              SET name = EXCLUDED.name,
                  website_url = COALESCE(EXCLUDED.website_url, shows.website_url)
            RETURNING id;
            """,
            (name, slug, website_url),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def get_show_id_by_slug(conn, slug: str) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM shows WHERE slug = %s LIMIT 1;", (slug,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
    return None


def upsert_episode(
    conn,
    show_id: int,
    show_slug: str,
    series_uuid: str,
    episode: dict[str, Any],
) -> int:
    episode_url = (
        episode.get("websiteUrl")
        or episode.get("audioUrl")
        or episode.get("guid")
        or f"https://api.taddy.org/podcast-episode/{episode.get('uuid')}"
    )
    publish_date = epoch_to_date(episode.get("datePublished"))
    raw_payload = None
    if show_slug in RAW_CONTENT_SHOW_SLUGS:
        raw_payload = json.dumps(
            {
                "provider": "taddy",
                "imported_at": datetime.utcnow().isoformat() + "Z",
                "series_uuid": series_uuid,
                "episode_uuid": episode.get("uuid"),
                "guid": episode.get("guid"),
                "name": episode.get("name"),
                "description": episode.get("description"),
                "transcribe_status": episode.get("taddyTranscribeStatus"),
                "transcript_urls": episode.get("transcriptUrls") or [],
                "episode_raw": episode,
            }
        )
    with conn.cursor() as cur:
        # Prefer reusing an existing row for same show/title/date to avoid duplicate
        # episodes when URLs differ across sources.
        existing_id = None
        if publish_date:
            cur.execute(
                """
                SELECT id
                FROM episodes
                WHERE show_id = %s
                  AND lower(title) = lower(%s)
                  AND publish_date = %s
                ORDER BY id
                LIMIT 1;
                """,
                (show_id, (episode.get("name") or "").strip() or "Untitled Episode", publish_date),
            )
            row = cur.fetchone()
            if row:
                existing_id = int(row["id"])

        if existing_id is not None:
            cur.execute(
                """
                UPDATE episodes
                SET description_body = COALESCE(%s, description_body),
                    episode_number = COALESCE(%s, episode_number),
                    audio_url = COALESCE(%s, audio_url),
                    image_url = COALESCE(%s, image_url),
                    raw_content = COALESCE(%s, raw_content),
                    scraped_at = NOW()
                WHERE id = %s
                RETURNING id;
                """,
                (
                    episode.get("description"),
                    episode.get("episodeNumber"),
                    episode.get("audioUrl"),
                    episode.get("imageUrl"),
                    raw_payload,
                    existing_id,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row["id"])

        cur.execute(
            """
            INSERT INTO episodes (
              show_id, title, url, publish_date, description_body, episode_number,
              audio_url, image_url, raw_content, scraped_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (url) DO UPDATE
              SET show_id = EXCLUDED.show_id,
                  title = EXCLUDED.title,
                  publish_date = COALESCE(EXCLUDED.publish_date, episodes.publish_date),
                  description_body = COALESCE(EXCLUDED.description_body, episodes.description_body),
                  episode_number = COALESCE(EXCLUDED.episode_number, episodes.episode_number),
                  audio_url = COALESCE(EXCLUDED.audio_url, episodes.audio_url),
                  image_url = COALESCE(EXCLUDED.image_url, episodes.image_url),
                  raw_content = COALESCE(EXCLUDED.raw_content, episodes.raw_content),
                  scraped_at = NOW()
            RETURNING id;
            """,
            (
                show_id,
                (episode.get("name") or "").strip() or "Untitled Episode",
                episode_url,
                publish_date,
                episode.get("description"),
                episode.get("episodeNumber"),
                episode.get("audioUrl"),
                episode.get("imageUrl"),
                raw_payload,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"])


def find_existing_episode_id(
    conn,
    show_id: int,
    episode: dict[str, Any],
) -> Optional[int]:
    episode_url = (
        episode.get("websiteUrl")
        or episode.get("audioUrl")
        or episode.get("guid")
        or f"https://api.taddy.org/podcast-episode/{episode.get('uuid')}"
    )
    publish_date = epoch_to_date(episode.get("datePublished"))
    title = (episode.get("name") or "").strip() or "Untitled Episode"

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM episodes WHERE url = %s LIMIT 1;", (episode_url,))
        row = cur.fetchone()
        if row:
            return int(row["id"])

        if publish_date:
            cur.execute(
                """
                SELECT id
                FROM episodes
                WHERE show_id = %s
                  AND lower(title) = lower(%s)
                  AND publish_date = %s
                ORDER BY id
                LIMIT 1;
                """,
                (show_id, title, publish_date),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"])
    return None


def transcript_exists(conn, episode_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM episode_transcripts WHERE episode_id = %s LIMIT 1;",
            (episode_id,),
        )
        return cur.fetchone() is not None


def upsert_episode_transcript(
    conn,
    episode_id: int,
    transcript_text: str,
    source_url: Optional[str],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episode_transcripts (
              episode_id, source_type, source_url, transcript_text, is_generated, model, created_at, updated_at
            )
            VALUES (%s, 'taddy_transcript', %s, %s, TRUE, 'taddy_paragraph', NOW(), NOW())
            ON CONFLICT (episode_id) DO UPDATE
              SET source_type = EXCLUDED.source_type,
                  source_url = EXCLUDED.source_url,
                  transcript_text = EXCLUDED.transcript_text,
                  is_generated = EXCLUDED.is_generated,
                  model = EXCLUDED.model,
                  updated_at = NOW();
            """,
            (episode_id, source_url, transcript_text),
        )
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import transcripts from Taddy")
    parser.add_argument(
        "--shows",
        default="ai-daily-brief,pchh,sop",
        help="Comma-separated show slugs from built-in config",
    )
    parser.add_argument(
        "--per-show-limit",
        type=int,
        default=5,
        help="Number of NEW transcripts to import per show",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="Taddy page size (max 50)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Safety cap on pages scanned per show",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be imported without writing transcripts",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing transcript rows for matched episodes",
    )
    parser.add_argument(
        "--max-credit-spend",
        type=int,
        default=0,
        help="Stop run after this many transcript credits are spent (0 disables).",
    )
    parser.add_argument(
        "--check-credits-every",
        type=int,
        default=10,
        help="Check remaining credits every N transcript attempts.",
    )
    parser.add_argument(
        "--max-failures-per-show",
        type=int,
        default=0,
        help="Stop current show after this many missing transcripts (0 disables).",
    )
    parser.add_argument(
        "--max-consecutive-failures",
        type=int,
        default=0,
        help="Stop current show after this many transcript misses in a row (0 disables).",
    )
    parser.add_argument(
        "--min-transcript-chars",
        type=int,
        default=2000,
        help="Warn when transcript text is shorter than this.",
    )
    parser.add_argument(
        "--reject-short-transcripts",
        action="store_true",
        help="Skip saving transcripts shorter than --min-transcript-chars.",
    )
    parser.add_argument(
        "--allow-processing-status",
        action="store_true",
        help="Allow importing episodes where taddyTranscribeStatus is PROCESSING.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    user_id = (os.getenv("TADDY_USER_ID") or "").strip()
    api_key = (os.getenv("TADDY_API_KEY") or "").strip()
    if not user_id or not api_key:
        raise RuntimeError("TADDY_USER_ID and TADDY_API_KEY are required")

    requested = [s.strip() for s in args.shows.split(",") if s.strip()]
    unknown = [s for s in requested if s not in SHOWS]
    if unknown:
        raise RuntimeError(f"Unknown show slugs: {unknown}. Known: {sorted(SHOWS.keys())}")

    conn = get_db_connection()
    try:
        ensure_schema(conn)
        starting_credits = get_transcript_credits_remaining(user_id=user_id, api_key=api_key)
        print(f"Starting transcript credits: {starting_credits}")

        transcript_attempts = 0
        stop_all = False

        for slug in requested:
            if stop_all:
                break
            cfg = SHOWS[slug]
            series = get_series(cfg.series_uuid, user_id=user_id, api_key=api_key)
            if not series:
                print(f"[{slug}] Could not load series {cfg.series_uuid}; skipping")
                continue

            show_name = (series.get("name") or cfg.name).strip()
            show_website = series.get("websiteUrl") or cfg.fallback_website_url
            if args.dry_run:
                show_id = get_show_id_by_slug(conn, cfg.slug)
                if show_id is None:
                    show_id = -1
            else:
                show_id = upsert_show(conn, slug=cfg.slug, name=show_name, website_url=show_website)
            print(
                f"\n[{slug}] show_id={show_id} series_uuid={cfg.series_uuid} "
                f"episodes={series.get('totalEpisodesCount')} status={series.get('taddyTranscribeStatus')}"
            )

            imported = 0
            skipped_existing = 0
            failed_transcript = 0
            scanned = 0
            short_transcript_count = 0
            pending_transcript_count = 0
            consecutive_failures = 0

            for page in range(1, args.max_pages + 1):
                if imported >= args.per_show_limit:
                    break

                episodes = get_latest_episodes(
                    series_uuid=cfg.series_uuid,
                    page=page,
                    limit_per_page=args.page_size,
                    user_id=user_id,
                    api_key=api_key,
                )
                if not episodes:
                    break

                for ep in episodes:
                    if imported >= args.per_show_limit:
                        break
                    scanned += 1

                    if args.dry_run:
                        episode_id = find_existing_episode_id(conn, show_id=show_id, episode=ep)
                    else:
                        episode_id = upsert_episode(
                            conn,
                            show_id=show_id,
                            show_slug=cfg.slug,
                            series_uuid=cfg.series_uuid,
                            episode=ep,
                        )

                    exists = transcript_exists(conn, episode_id) if episode_id is not None else False
                    if exists and not args.force:
                        skipped_existing += 1
                        continue

                    if args.dry_run:
                        imported += 1
                        print(f"  ~ would import {imported}/{args.per_show_limit}: title={ep.get('name')}")
                        continue

                    status = str(ep.get("taddyTranscribeStatus") or "").upper().strip()
                    if status == "PROCESSING" and not args.allow_processing_status:
                        pending_transcript_count += 1
                        print(
                            f"  ~ pending transcript (PROCESSING), skip for now: "
                            f"episode_id={episode_id} title={ep.get('name')}"
                        )
                        continue

                    transcript_attempts += 1
                    transcript_items = get_episode_transcript(
                        episode_uuid=ep["uuid"],
                        user_id=user_id,
                        api_key=api_key,
                    )
                    if args.check_credits_every > 0 and transcript_attempts % args.check_credits_every == 0:
                        current_credits = get_transcript_credits_remaining(
                            user_id=user_id,
                            api_key=api_key,
                        )
                        spent = starting_credits - current_credits
                        print(
                            f"  credits check: remaining={current_credits} spent={spent} "
                            f"attempts={transcript_attempts}"
                        )
                        if args.max_credit_spend > 0 and spent >= args.max_credit_spend:
                            print(
                                f"  stopping run: max credit spend reached "
                                f"({spent} >= {args.max_credit_spend})"
                            )
                            stop_all = True
                            break

                    if not transcript_items:
                        failed_transcript += 1
                        consecutive_failures += 1
                        print(f"  - no transcript: episode_id={episode_id} title={ep.get('name')}")
                        if args.max_failures_per_show > 0 and failed_transcript >= args.max_failures_per_show:
                            print(
                                f"  stopping show: failures reached limit "
                                f"({failed_transcript} >= {args.max_failures_per_show})"
                            )
                            break
                        if (
                            args.max_consecutive_failures > 0
                            and consecutive_failures >= args.max_consecutive_failures
                        ):
                            print(
                                f"  stopping show: consecutive failures reached limit "
                                f"({consecutive_failures} >= {args.max_consecutive_failures})"
                            )
                            break
                        continue

                    transcript_text = "\n\n".join(
                        [i.get("text", "").strip() for i in transcript_items if (i.get("text") or "").strip()]
                    ).strip()
                    if not transcript_text:
                        failed_transcript += 1
                        consecutive_failures += 1
                        print(f"  - empty transcript text: episode_id={episode_id} title={ep.get('name')}")
                        if args.max_failures_per_show > 0 and failed_transcript >= args.max_failures_per_show:
                            print(
                                f"  stopping show: failures reached limit "
                                f"({failed_transcript} >= {args.max_failures_per_show})"
                            )
                            break
                        if (
                            args.max_consecutive_failures > 0
                            and consecutive_failures >= args.max_consecutive_failures
                        ):
                            print(
                                f"  stopping show: consecutive failures reached limit "
                                f"({consecutive_failures} >= {args.max_consecutive_failures})"
                            )
                            break
                        continue

                    if len(transcript_text) < args.min_transcript_chars:
                        short_transcript_count += 1
                        print(
                            f"  ! short transcript ({len(transcript_text)} chars): "
                            f"episode_id={episode_id} title={ep.get('name')}"
                        )
                        if args.reject_short_transcripts:
                            continue

                    source_url = None
                    transcript_urls = ep.get("transcriptUrls") or []
                    if transcript_urls:
                        source_url = transcript_urls[0]
                    elif ep.get("audioUrl"):
                        source_url = ep.get("audioUrl")
                    else:
                        source_url = f"https://api.taddy.org/podcast-episode/{ep.get('uuid')}"

                    upsert_episode_transcript(
                        conn,
                        episode_id=episode_id,
                        transcript_text=transcript_text,
                        source_url=source_url,
                    )
                    consecutive_failures = 0

                    imported += 1
                    print(
                        f"  + imported {imported}/{args.per_show_limit}: "
                        f"episode_id={episode_id} title={ep.get('name')}"
                    )

                if stop_all:
                    break
                if args.max_failures_per_show > 0 and failed_transcript >= args.max_failures_per_show:
                    break

            print(
                f"[{slug}] done: imported={imported}, skipped_existing={skipped_existing}, "
                f"failed={failed_transcript}, pending={pending_transcript_count}, "
                f"short={short_transcript_count}, scanned={scanned}"
            )
        ending_credits = get_transcript_credits_remaining(user_id=user_id, api_key=api_key)
        print(f"Ending transcript credits: {ending_credits} (spent {starting_credits - ending_credits})")
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()

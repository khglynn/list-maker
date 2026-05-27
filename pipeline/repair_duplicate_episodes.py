#!/usr/bin/env python3
"""Merge duplicate episode rows by show/title/publish_date.

Dry-run by default. Use --execute to move child records to a canonical episode
and delete the redundant duplicate rows.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass
class EpisodeChoice:
    episode_id: int
    score: int
    reasons: list[str]


def load_environment() -> None:
    repo_root = Path(__file__).resolve().parents[1]
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


def is_human_url(slug: str, url: str | None) -> bool:
    if not url:
        return False
    if slug == "sop":
        return "switchedonpop.com" in url
    if slug == "tal":
        return "thisamericanlife.org" in url and "podtrac.com" not in url
    return not url.endswith(".mp3")


def score_episode(row: dict[str, Any]) -> EpisodeChoice:
    score = 0
    reasons: list[str] = []

    if is_human_url(row["slug"], row.get("url")):
        score += 100
        reasons.append("human URL")
    if row.get("has_raw_content"):
        score += 30
        reasons.append("raw content")
    if int(row.get("song_count") or 0) > 0:
        score += 20
        reasons.append(f"{row['song_count']} songs")
    if int(row.get("transcript_count") or 0) > 0:
        score += 10
        reasons.append(f"{row['transcript_count']} transcript")
    if row.get("description_body"):
        score += 3
        reasons.append("description")
    if row.get("audio_url"):
        score += 2
        reasons.append("audio URL")
    if row.get("image_url"):
        score += 1
        reasons.append("image URL")

    # Prefer the simpler historical URL when two TAL rows are otherwise equal.
    url = row.get("url") or ""
    if row["slug"] == "tal" and row.get("episode_number") and str(row["episode_number"]) in url:
        score += 1
        reasons.append("episode-number URL")
    if row["slug"] == "tal" and reissue_suffix(row):
        score -= 1
        reasons.append("reissue suffix")

    return EpisodeChoice(int(row["id"]), score, reasons)


def reissue_suffix(row: dict[str, Any]) -> bool:
    url = row.get("url") or ""
    title = (row.get("title") or "").lower()
    if not row.get("publish_date"):
        return False
    year = str(row["publish_date"])[:4]
    return url.rstrip("/").endswith(year) or f"({year})" in title


def choose_canonical(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], EpisodeChoice]:
    choices = {row["id"]: score_episode(row) for row in rows}
    canonical = sorted(
        rows,
        key=lambda row: (-choices[row["id"]].score, int(row["id"])),
    )[0]
    donors = [row for row in rows if row["id"] != canonical["id"]]
    return canonical, donors, choices[canonical["id"]]


def duplicate_groups(conn) -> list[list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH dupes AS (
              SELECT
                s.slug,
                LOWER(BTRIM(e.title)) AS title_key,
                e.publish_date,
                ARRAY_AGG(e.id ORDER BY e.id) AS ids
              FROM episodes e
              JOIN shows s ON s.id = e.show_id
              WHERE e.title IS NOT NULL
                AND BTRIM(e.title) <> ''
                AND e.publish_date IS NOT NULL
              GROUP BY s.slug, LOWER(BTRIM(e.title)), e.publish_date
              HAVING COUNT(*) > 1
            )
            SELECT
              d.slug,
              d.title_key,
              d.publish_date,
              e.id,
              e.title,
              e.url,
              e.description_body,
              e.episode_number,
              e.audio_url,
              e.image_url,
              e.raw_content IS NOT NULL AS has_raw_content,
              e.has_songs_discussed,
              COUNT(DISTINCT so.id) AS song_count,
              COUNT(DISTINCT et.id) AS transcript_count,
              COUNT(DISTINCT m.id) AS mention_count
            FROM dupes d
            JOIN episodes e ON e.id = ANY(d.ids)
            LEFT JOIN songs so ON so.episode_id = e.id
            LEFT JOIN episode_transcripts et ON et.episode_id = e.id
            LEFT JOIN ai_mentions m ON m.episode_id = e.id
            GROUP BY d.slug, d.title_key, d.publish_date, e.id
            ORDER BY d.slug, d.publish_date DESC, d.title_key, e.id;
            """
        )
        rows = [dict(row) for row in cur.fetchall()]

    groups: dict[tuple[str, str, Any], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["slug"], row["title_key"], row["publish_date"])
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def merge_donor(conn, canonical_id: int, donor_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE episodes c
            SET description_body = COALESCE(c.description_body, d.description_body),
                episode_number = COALESCE(c.episode_number, d.episode_number),
                audio_url = COALESCE(c.audio_url, d.audio_url),
                image_url = COALESCE(c.image_url, d.image_url),
                raw_content = COALESCE(c.raw_content, d.raw_content),
                has_songs_discussed = CASE
                  WHEN c.has_songs_discussed IS TRUE OR d.has_songs_discussed IS TRUE THEN TRUE
                  WHEN c.has_songs_discussed IS FALSE OR d.has_songs_discussed IS FALSE THEN FALSE
                  ELSE NULL
                END,
                scraped_at = GREATEST(c.scraped_at, d.scraped_at)
            FROM episodes d
            WHERE c.id = %s
              AND d.id = %s;
            """,
            (canonical_id, donor_id),
        )

        cur.execute(
            """
            DELETE FROM songs donor_song
            USING songs canonical_song
            WHERE donor_song.episode_id = %s
              AND canonical_song.episode_id = %s
              AND LOWER(BTRIM(donor_song.title)) = LOWER(BTRIM(canonical_song.title))
              AND LOWER(BTRIM(COALESCE(donor_song.artist, ''))) =
                  LOWER(BTRIM(COALESCE(canonical_song.artist, '')));
            """,
            (donor_id, canonical_id),
        )
        cur.execute("UPDATE songs SET episode_id = %s WHERE episode_id = %s;", (canonical_id, donor_id))

        cur.execute(
            "SELECT id FROM episode_transcripts WHERE episode_id = %s LIMIT 1;",
            (canonical_id,),
        )
        canonical_transcript = cur.fetchone()
        cur.execute(
            "SELECT id FROM episode_transcripts WHERE episode_id = %s LIMIT 1;",
            (donor_id,),
        )
        donor_transcript = cur.fetchone()
        if donor_transcript and not canonical_transcript:
            cur.execute(
                "UPDATE episode_transcripts SET episode_id = %s, updated_at = NOW() WHERE episode_id = %s;",
                (canonical_id, donor_id),
            )
        elif donor_transcript and canonical_transcript:
            cur.execute("DELETE FROM episode_transcripts WHERE episode_id = %s;", (donor_id,))

        cur.execute("UPDATE ai_mentions SET episode_id = %s WHERE episode_id = %s;", (canonical_id, donor_id))
        cur.execute("DELETE FROM episodes WHERE id = %s;", (donor_id,))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge duplicate episode rows.")
    parser.add_argument("--execute", action="store_true", help="Write duplicate merges to Neon.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()
    conn = get_db_connection()
    groups_merged = 0
    donors_merged = 0
    try:
        groups = duplicate_groups(conn)
        print(f"Duplicate groups: {len(groups)}")
        for group in groups:
            canonical, donors, choice = choose_canonical(group)
            groups_merged += 1
            donors_merged += len(donors)
            print(
                f"\n{canonical['slug']} {canonical['publish_date']} `{canonical['title']}`"
            )
            print(
                f"  canonical id={canonical['id']} score={choice.score} "
                f"({', '.join(choice.reasons)})"
            )
            for donor in donors:
                print(
                    f"  merge donor id={donor['id']} songs={donor['song_count']} "
                    f"transcripts={donor['transcript_count']} url={donor['url']}"
                )
                if args.execute:
                    merge_donor(conn, int(canonical["id"]), int(donor["id"]))
        if args.execute:
            conn.commit()
    finally:
        conn.close()

    print(f"\nGroups reviewed: {groups_merged}")
    print(f"Donor rows {'merged' if args.execute else 'to merge'}: {donors_merged}")
    if not args.execute:
        print("Dry run only. Re-run with --execute to merge duplicate rows.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)

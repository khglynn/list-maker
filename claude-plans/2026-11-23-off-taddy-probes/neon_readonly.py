"""Read-only ground truth from Neon for the off-Taddy plan (2026-11-23).

Session is forced read-only (default_transaction_read_only=on). Prints counts only,
never connection strings or transcript text beyond short samples.
Run with list-maker's venv python; env files are read, values never printed.
"""
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

repo = Path(__file__).resolve().parents[2]
env = {**dotenv_values(repo / ".env.local"), **dotenv_values(repo / "pipeline" / ".env.local")}
dsn = env.get("DATABASE_URL") or env.get("NEON_DATABASE_URL")
conn = psycopg2.connect(dsn, options="-c default_transaction_read_only=on", connect_timeout=20,
                        cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()


def q(title, sql, params=None):
    cur.execute(sql, params or [])
    rows = cur.fetchall()
    print(f"\n## {title}")
    for r in rows:
        print("  " + json.dumps({k: (str(v) if v is not None and not isinstance(v, (int, float, bool)) else v) for k, v in r.items()}))
    return rows


SHOWS = ["ai-daily-brief", "hard-fork", "pchh", "tal", "sop"]

q("episodes, transcripts and url scheme per show", """
SELECT s.slug,
       COUNT(e.id) AS episodes,
       COUNT(et.id) AS transcripts,
       COUNT(e.id) FILTER (WHERE e.url LIKE 'https://api.taddy.org/podcast-episode/%%') AS taddy_url_rows,
       COUNT(e.id) FILTER (WHERE e.raw_content LIKE '{%%' AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL) AS raw_has_guid,
       MIN(e.publish_date) AS first_ep, MAX(e.publish_date) AS last_ep
FROM shows s LEFT JOIN episodes e ON e.show_id = s.id
LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug = ANY(%s) GROUP BY s.slug ORDER BY s.slug""", [SHOWS])

q("transcript source_type per show", """
SELECT s.slug, et.source_type, et.model, COUNT(*) AS n, MIN(e.publish_date) AS first, MAX(e.publish_date) AS last
FROM episode_transcripts et JOIN episodes e ON e.id = et.episode_id JOIN shows s ON s.id = e.show_id
WHERE s.slug = ANY(%s) GROUP BY 1,2,3 ORDER BY 1,2""", [SHOWS])

q("transcripts created per month (last 4 months), Taddy-sourced", """
SELECT to_char(date_trunc('month', et.created_at), 'YYYY-MM') AS month, s.slug, COUNT(*) AS n
FROM episode_transcripts et JOIN episodes e ON e.id = et.episode_id JOIN shows s ON s.id = e.show_id
WHERE et.source_type = 'taddy_transcript' AND et.created_at >= date_trunc('month', now()) - interval '3 months'
GROUP BY 1,2 ORDER BY 1,2""")

q("episodes published per month (last 4 months)", """
SELECT to_char(date_trunc('month', e.publish_date), 'YYYY-MM') AS month, s.slug, COUNT(*) AS n,
       COUNT(et.id) AS with_transcript
FROM episodes e JOIN shows s ON s.id = e.show_id LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug = ANY(%s) AND e.publish_date >= date_trunc('month', now()) - interval '3 months'
GROUP BY 1,2 ORDER BY 1,2""", [SHOWS])

q("Hard Fork: episodes WITHOUT a transcript", """
SELECT e.id, e.publish_date, left(e.title, 70) AS title, (e.audio_url IS NOT NULL) AS has_audio
FROM episodes e JOIN shows s ON s.id = e.show_id LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug = 'hard-fork' AND et.id IS NULL ORDER BY e.publish_date DESC LIMIT 40""")

q("Hard Fork: last 12 episodes we hold", """
SELECT e.id, e.publish_date, left(e.title, 60) AS title, (et.id IS NOT NULL) AS has_transcript,
       left(e.url, 45) AS url_prefix
FROM episodes e JOIN shows s ON s.id = e.show_id LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug = 'hard-fork' ORDER BY e.publish_date DESC LIMIT 12""")

q("Episodes without transcript, last 120 days, transcript shows", """
SELECT s.slug, COUNT(*) AS n, MIN(e.publish_date) AS oldest, MAX(e.publish_date) AS newest
FROM episodes e JOIN shows s ON s.id = e.show_id LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug IN ('ai-daily-brief','hard-fork','pchh') AND et.id IS NULL
  AND e.publish_date >= now() - interval '120 days'
GROUP BY 1 ORDER BY 1""")

q("Entity-show rows WITHOUT a guid in raw_content (identity backfill gap)", """
SELECT s.slug, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE e.url LIKE 'https://api.taddy.org/%%') AS of_which_taddy_url,
       MIN(e.publish_date) AS oldest, MAX(e.publish_date) AS newest
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug IN ('ai-daily-brief','hard-fork','pchh')
  AND NOT (e.raw_content LIKE '{%%' AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL)
GROUP BY 1 ORDER BY 1""")

q("Duplicate guids within a show (would break a guid identity)", """
SELECT s.slug, e.raw_content::jsonb ->> 'guid' AS guid, COUNT(*) AS n
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug IN ('ai-daily-brief','hard-fork','pchh') AND e.raw_content LIKE '{%%'
  AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL
GROUP BY 1,2 HAVING COUNT(*) > 1 ORDER BY 3 DESC LIMIT 10""")

q("TAL: url schemes and transcripts", """
SELECT CASE WHEN e.url LIKE 'https://api.taddy.org/%%' THEN 'taddy' WHEN e.url LIKE 'https://www.thisamericanlife.org/%%' THEN 'tal-site' ELSE 'other' END AS scheme,
       COUNT(*) AS n, COUNT(et.id) AS with_transcript, MIN(e.publish_date) AS oldest, MAX(e.publish_date) AS newest
FROM episodes e JOIN shows s ON s.id = e.show_id LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug = 'tal' GROUP BY 1 ORDER BY 1""")

q("TAL: newest 18 rows", """
SELECT e.id, e.publish_date, left(e.title, 50) AS title, left(e.url, 40) AS url_prefix, (et.id IS NOT NULL) AS has_tx
FROM episodes e JOIN shows s ON s.id = e.show_id LEFT JOIN episode_transcripts et ON et.episode_id = e.id
WHERE s.slug = 'tal' ORDER BY e.publish_date DESC, e.id DESC LIMIT 18""")

q("AI Daily newest 6 rows: url + raw_content keys + description sample", """
SELECT e.id, e.publish_date, left(e.title, 50) AS title, left(e.url, 60) AS url,
       (SELECT array_agg(k) FROM jsonb_object_keys(e.raw_content::jsonb) k) AS raw_keys,
       e.raw_content::jsonb ->> 'guid' AS guid
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug = 'ai-daily-brief' AND e.raw_content LIKE '{%%' ORDER BY e.publish_date DESC LIMIT 6""")

q("Notion transcript pages per show", """
SELECT s.slug, COUNT(*) FILTER (WHERE et.notion_transcript_page_id IS NOT NULL) AS notion_pages
FROM episode_transcripts et JOIN episodes e ON e.id = et.episode_id JOIN shows s ON s.id = e.show_id
WHERE s.slug = ANY(%s) GROUP BY 1 ORDER BY 1""", [SHOWS])

q("saved-episodes transcripts by source", """
SELECT et.source_type, COUNT(*) AS n FROM episode_transcripts et JOIN episodes e ON e.id = et.episode_id
JOIN shows s ON s.id = e.show_id WHERE s.slug = 'saved-episodes' GROUP BY 1 ORDER BY 1""")

conn.close()

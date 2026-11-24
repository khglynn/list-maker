"""Phase 0 baseline before the move off Taddy (read-only; 2026-11-23).

Counts per podcast show, so the cut-over can be checked against a fixed before-picture:
episodes, transcripts and their total characters, transcript sources, the url schemes
held in episodes.url, and how many rows carry a feed guid in raw_content. The guids
themselves stay in Neon; a sha256 over the sorted (episode_id, guid) pairs pins them.
Run from the repo root: pipeline/venv/bin/python claude-plans/2026-11-23-off-taddy-probes/baseline_counts.py
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out" / "baseline-2026-11-23.json"
SHOWS = ["ai-daily-brief", "hard-fork", "pchh", "tal", "sop", "culture-gabfest"]

env = {**dotenv_values(REPO / ".env.local"), **dotenv_values(REPO / "pipeline" / ".env.local")}
conn = psycopg2.connect(env.get("DATABASE_URL") or env.get("NEON_DATABASE_URL"),
                        options="-c default_transaction_read_only=on", connect_timeout=20,
                        cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()
result = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "shows": {}}
for slug in SHOWS:
    cur.execute("""
        SELECT COUNT(e.id) AS episodes,
               COUNT(et.id) AS transcripts,
               COALESCE(SUM(length(et.transcript_text)), 0) AS transcript_chars,
               MIN(e.publish_date)::text AS first_date, MAX(e.publish_date)::text AS last_date,
               COUNT(*) FILTER (WHERE e.url LIKE 'https://api.taddy.org/podcast-episode/%%') AS url_taddy,
               COUNT(*) FILTER (WHERE e.url LIKE 'https://podcasters.spotify.com/%%') AS url_spotify,
               COUNT(*) FILTER (WHERE e.url LIKE 'https://www.npr.org/%%') AS url_npr,
               COUNT(*) FILTER (WHERE e.url LIKE 'https://www.thisamericanlife.org/%%') AS url_tal_site,
               COUNT(*) FILTER (WHERE e.url NOT LIKE 'http%%') AS url_not_http,
               COUNT(*) FILTER (WHERE e.raw_content LIKE '{%%' AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL) AS raw_has_guid
        FROM episodes e JOIN shows s ON s.id = e.show_id
        LEFT JOIN episode_transcripts et ON et.episode_id = e.id
        WHERE s.slug = %s""", (slug,))
    row = dict(cur.fetchone())
    cur.execute("""
        SELECT et.source_type, et.model, COUNT(*) AS n FROM episode_transcripts et
        JOIN episodes e ON e.id = et.episode_id JOIN shows s ON s.id = e.show_id
        WHERE s.slug = %s GROUP BY 1, 2 ORDER BY 3 DESC""", (slug,))
    row["transcript_sources"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT e.id, e.raw_content::jsonb ->> 'guid' AS guid FROM episodes e JOIN shows s ON s.id = e.show_id
        WHERE s.slug = %s AND e.raw_content LIKE '{%%' AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL
        ORDER BY e.id""", (slug,))
    pairs = "\n".join(f"{r['id']}\t{r['guid']}" for r in cur.fetchall())
    row["guid_pairs_sha256"] = hashlib.sha256(pairs.encode()).hexdigest()
    result["shows"][slug] = row
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, indent=2, default=str) + "\n")
for slug, r in result["shows"].items():
    print(slug, {k: v for k, v in r.items() if k not in ("transcript_sources", "guid_pairs_sha256")})

"""Read-only: what identity do non-Taddy rows carry, and would url := guid collide?"""
import json
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import dotenv_values

repo = Path(__file__).resolve().parents[2]
env = {**dotenv_values(repo / ".env.local"), **dotenv_values(repo / "pipeline" / ".env.local")}
conn = psycopg2.connect(env.get("DATABASE_URL") or env.get("NEON_DATABASE_URL"),
                        options="-c default_transaction_read_only=on", connect_timeout=20,
                        cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()


def q(title, sql, params=None):
    cur.execute(sql, params or [])
    print(f"\n## {title}")
    for r in cur.fetchall():
        print("  " + json.dumps({k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)) for k, v in r.items()}))


q("url scheme buckets for entity shows", """
SELECT s.slug,
  CASE WHEN e.url LIKE 'https://api.taddy.org/%%' THEN 'taddy'
       WHEN e.url = e.raw_content::jsonb ->> 'guid' THEN 'url_is_guid'
       WHEN e.url LIKE 'http%%' THEN 'other_http:' || split_part(split_part(e.url, '://', 2), '/', 1)
       ELSE 'other' END AS scheme,
  COUNT(*) AS n, MIN(e.publish_date) AS oldest, MAX(e.publish_date) AS newest
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug IN ('ai-daily-brief','hard-fork','pchh')
GROUP BY 1,2 ORDER BY 1,3 DESC""")

q("would url := guid collide with ANY existing url (any show)?", """
SELECT s.slug, COUNT(*) AS collisions
FROM episodes e JOIN shows s ON s.id = e.show_id
JOIN episodes other ON other.url = (e.raw_content::jsonb ->> 'guid') AND other.id <> e.id
WHERE s.slug IN ('ai-daily-brief','hard-fork','pchh') AND e.raw_content LIKE '{%%'
GROUP BY 1""")

q("does Taddy's guid equal the live RSS guid? newest 3 per show", """
SELECT s.slug, e.publish_date, left(e.title, 40) AS title, e.raw_content::jsonb ->> 'guid' AS guid
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug IN ('hard-fork','pchh') AND e.raw_content LIKE '{%%'
ORDER BY e.publish_date DESC LIMIT 6""")

q("TAL Taddy-url rows: dates vs feed (to map guids)", """
SELECT e.id, e.publish_date, left(e.title, 45) AS title
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug = 'tal' AND e.url LIKE 'https://api.taddy.org/%%' ORDER BY e.publish_date DESC""")

q("episode_transcripts columns", """
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'episode_transcripts' ORDER BY ordinal_position""")

q("episodes columns", """
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'episodes' ORDER BY ordinal_position""")
conn.close()

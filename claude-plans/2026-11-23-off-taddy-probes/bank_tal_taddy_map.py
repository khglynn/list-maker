"""Phase 0 of the move off Taddy: bank the one thing only Taddy holds (2026-11-23).

28 This American Life rows in Neon carry a Taddy episode url as their identity
(https://api.taddy.org/podcast-episode/<uuid>) and no saved Taddy payload, so nothing
in Neon says which feed <guid> each one is. One getLatestPodcastEpisodes call maps
the episodes still in the feed; Taddy's "latest" list mirrors the feed's 16 items, so the
rows that have rolled off it get one getPodcastEpisode(uuid) call each. None of these
spend a transcript credit. Run while the key still works. The map is public data
(episode ids and titles from a public feed), so it is committed as the input to the
one-time id move.

Read-only against Neon (default_transaction_read_only=on). Writes one JSON file.
Run from the repo root:  pipeline/venv/bin/python claude-plans/2026-11-23-off-taddy-probes/bank_tal_taddy_map.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
import requests
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out" / "tal-taddy-uuid-to-guid.json"
TAL_SERIES_UUID = "d682a935-ad2d-46ee-a0ac-139198b83bcc"
PREFIX = "https://api.taddy.org/podcast-episode/"

env = {**dotenv_values(REPO / ".env.local"), **dotenv_values(REPO / "pipeline" / ".env.local")}
query = (
    f'query {{ getLatestPodcastEpisodes(uuids:["{TAL_SERIES_UUID}"], page:1, limitPerPage:50) '
    "{ uuid guid datePublished name } }"
)
resp = requests.post(
    "https://api.taddy.org",
    headers={"Content-Type": "application/json", "X-USER-ID": env["TADDY_USER_ID"], "X-API-KEY": env["TADDY_API_KEY"]},
    json={"query": query},
    timeout=60,
)
resp.raise_for_status()
payload = resp.json()
if payload.get("errors"):
    sys.exit(f"Taddy error: {payload['errors']}")
taddy = {e["uuid"]: e for e in payload["data"]["getLatestPodcastEpisodes"]}

conn = psycopg2.connect(env.get("DATABASE_URL") or env.get("NEON_DATABASE_URL"),
                        options="-c default_transaction_read_only=on", connect_timeout=20,
                        cursor_factory=psycopg2.extras.RealDictCursor)
with conn.cursor() as cur:
    cur.execute(
        "SELECT e.id, e.url, e.title, e.publish_date FROM episodes e JOIN shows s ON s.id = e.show_id "
        "WHERE s.slug = 'tal' AND e.url LIKE %s ORDER BY e.publish_date",
        (PREFIX + "%",),
    )
    rows = cur.fetchall()

def lookup_one(uuid):
    q = f'query {{ getPodcastEpisode(uuid:"{uuid}") {{ uuid guid datePublished name }} }}'
    r = requests.post("https://api.taddy.org", json={"query": q}, timeout=60, headers={
        "Content-Type": "application/json", "X-USER-ID": env["TADDY_USER_ID"], "X-API-KEY": env["TADDY_API_KEY"]})
    r.raise_for_status()
    body = r.json()
    return None if body.get("errors") else (body.get("data") or {}).get("getPodcastEpisode")


mapped, unmapped = [], []
looked_up_one_by_one = 0
for r in rows:
    uuid = r["url"][len(PREFIX):]
    t = taddy.get(uuid)
    if t is None:
        t = lookup_one(uuid)
        looked_up_one_by_one += 1
    entry = {"episode_id": r["id"], "taddy_uuid": uuid, "title": r["title"], "publish_date": str(r["publish_date"])}
    if t and t.get("guid"):
        entry["guid"] = t["guid"]
        entry["taddy_title"] = t.get("name")
        entry["taddy_date_utc"] = str(datetime.fromtimestamp(int(t["datePublished"]), tz=timezone.utc).date())
        mapped.append(entry)
    else:
        unmapped.append(entry)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "banked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "source": "Taddy getLatestPodcastEpisodes (TAL, page 1) + getPodcastEpisode for rows off the feed",
    "taddy_items_returned": len(taddy),
    "looked_up_one_by_one": looked_up_one_by_one,
    "neon_rows_with_taddy_url": len(rows),
    "mapped": mapped,
    "unmapped": unmapped,
}, indent=2, ensure_ascii=False) + "\n")
print(f"Taddy returned {len(taddy)} TAL items; Neon has {len(rows)} TAL rows on a Taddy url; "
      f"mapped {len(mapped)}, unmapped {len(unmapped)} -> {OUT.relative_to(REPO)}")

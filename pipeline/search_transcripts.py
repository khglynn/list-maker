#!/usr/bin/env python3
"""Full-text search over episode transcripts (Workstream 3a).

The power-search half of "transcripts searchable BOTH." Postgres FTS over the corpus
where it already lives — the Willison baseline from the AI-memory primer: a real DB +
an index beats a bespoke memory system, and a smart reader can drive it. The Notion
Transcripts DB (sync_transcripts_notion.py) is the human / Notion-AI half.

Backed by the generated search_vector column + GIN index from sql/005_transcript_fts.

    ./venv/bin/python search_transcripts.py "agent infrastructure"
    ./venv/bin/python search_transcripts.py "token efficiency" --show ai-daily-brief --limit 5
    ./venv/bin/python search_transcripts.py '"context window" OR "context rot"' --json

The query uses Postgres websearch_to_tsquery: quotes for phrases, OR for alternation,
a leading - to exclude a term.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, load_environment  # noqa: E402

# Rank + filter + LIMIT in the CTE first, then run the expensive ts_headline only on the
# final rows (otherwise Postgres would headline every matching transcript before LIMIT).
SEARCH_SQL = """
    WITH hits AS (
        SELECT et.episode_id, et.transcript_text,
               ts_rank(et.search_vector, websearch_to_tsquery('english', %(q)s)) AS rank
        FROM episode_transcripts et
        JOIN episodes ep ON ep.id = et.episode_id
        JOIN shows s ON s.id = ep.show_id
        WHERE et.search_vector @@ websearch_to_tsquery('english', %(q)s)
          AND (%(show)s = '' OR s.slug = %(show)s)
        ORDER BY rank DESC
        LIMIT %(limit)s
    )
    SELECT s.slug AS show, ep.id AS episode_id, ep.title, ep.publish_date, h.rank,
           ts_headline('english', h.transcript_text, websearch_to_tsquery('english', %(q)s),
                       'StartSel=[, StopSel=], MaxFragments=2, MinWords=6, MaxWords=22, '
                       'FragmentDelimiter= … ') AS snippet
    FROM hits h
    JOIN episodes ep ON ep.id = h.episode_id
    JOIN shows s ON s.id = ep.show_id
    ORDER BY h.rank DESC, ep.publish_date DESC NULLS LAST
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Full-text search over episode transcripts")
    p.add_argument("query", help="websearch query (quotes=phrase, OR=alternation, -word=exclude)")
    p.add_argument("--show", default="", help="Filter to a show slug (e.g. ai-daily-brief, hard-fork)")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    load_environment()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(SEARCH_SQL, {"q": args.query, "show": args.show, "limit": args.limit})
            rows = cur.fetchall()
    finally:
        conn.close()

    if args.json:
        out = [
            {
                "show": r["show"],
                "episode_id": r["episode_id"],
                "title": r["title"],
                "publish_date": str(r["publish_date"]) if r["publish_date"] else None,
                "rank": round(float(r["rank"]), 5),
                "snippet": r["snippet"],
            }
            for r in rows
        ]
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    if not rows:
        print(f"No transcripts match: {args.query}")
        return
    print(f"{len(rows)} result(s) for: {args.query}\n")
    for r in rows:
        date = r["publish_date"] or "?"
        print(f"[{r['show']}] {r['title']}  ({date})  rank={float(r['rank']):.4f}  ep={r['episode_id']}")
        print(f"   … {r['snippet']} …\n")


if __name__ == "__main__":
    main()

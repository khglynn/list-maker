#!/usr/bin/env python3
"""Run one numbered migration from scrapers/ai_daily/sql/ against Neon, and say what changed.

Production data changes in this repo are Kevin's paste, not an agent's (the files carry
their reasoning and their own preconditions). This is the paste's engine, so every run
looks the same: the file runs exactly as written, in its own BEGIN ... COMMIT, its
NOTICEs are printed, and a per-show count of episode-id schemes is shown before and
after so the result can be read at a glance. Nothing here is specific to one file.

    cd ~/DevKev/personal/list-maker/pipeline
    ./venv/bin/python apply_sql.py scrapers/ai_daily/sql/014_episode_ids_become_feed_guids.sql

A file that raises (a failed precondition) rolls back whole and changes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import get_db_connection, load_environment  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent / "scrapers" / "ai_daily" / "sql"

ID_SCHEMES = """
SELECT s.slug,
       COUNT(*) AS episodes,
       COUNT(*) FILTER (WHERE e.url LIKE 'https://api.taddy.org/%%') AS taddy_ids,
       COUNT(*) FILTER (WHERE e.url NOT LIKE 'http%%') AS feed_guids,
       COUNT(*) FILTER (WHERE e.url LIKE 'http%%' AND e.url NOT LIKE 'https://api.taddy.org/%%') AS web_urls
FROM episodes e JOIN shows s ON s.id = e.show_id
WHERE s.slug IN ('ai-daily-brief', 'pchh', 'hard-fork', 'machine-gods', 'tal', 'sop')
GROUP BY s.slug ORDER BY s.slug
"""


def _print_schemes(cur, label: str) -> None:
    cur.execute(ID_SCHEMES)
    print(f"\n{label}:")
    print(f"  {'show':16} {'episodes':>8} {'taddy ids':>10} {'feed guids':>11} {'web urls':>9}")
    for r in cur.fetchall():
        print(f"  {r['slug']:16} {r['episodes']:>8} {r['taddy_ids']:>10} {r['feed_guids']:>11} {r['web_urls']:>9}")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = Path(argv[0]).resolve()
    if path.parent != SQL_DIR or not path.name[:3].isdigit():
        print(f"refusing: {path} is not a numbered file in {SQL_DIR}", file=sys.stderr)
        return 2
    load_environment()
    conn = get_db_connection()
    conn.autocommit = True  # the file carries its own BEGIN ... COMMIT
    try:
        with conn.cursor() as cur:
            _print_schemes(cur, "Before")
            try:
                cur.execute(path.read_text(encoding="utf-8"))
            except Exception as exc:  # the file's own precondition said no
                cur.execute("ROLLBACK")
                print(f"\nNOT APPLIED ({exc.__class__.__name__}): {(str(exc).splitlines() or [''])[0]}")
                print("Nothing changed.")
                return 1
            for notice in conn.notices:
                print(f"  {notice.strip()}")
            _print_schemes(cur, "After")
            print(f"\nApplied {path.name}.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

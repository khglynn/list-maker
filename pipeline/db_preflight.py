#!/usr/bin/env python3
"""DB preflight — stop a CI job in about a minute, not 41, when Neon can't be reached.

On 2026-08-31 one GitHub runner VM lost its egress path to the Neon pooler for the
life of the job: a SYN blackhole on all three IPv4 addresses, while two sibling jobs on
other VMs connected to the same pooler at the same minute (Neon was up). Every step then
rediscovered the hole on its own and paid ~7 minutes each — a 41-minute run to report one
fact, and a Slack line that said only "FAILED, view logs".

This runs FIRST in each workflow: one bounded connect attempt. On failure it posts the
diagnostics the log had and the alert didn't — host, resolved addresses, runner — and
exits non-zero so the job ends here. Downstream steps marked `if: always()` gate on
this step's outcome, so nothing re-pays the wait.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import DB_CONNECT_TIMEOUT_SECONDS, get_db_connection, load_environment, post_slack  # noqa: E402


def resolved_addresses(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 5432, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return [f"DNS failed: {exc}"]
    return sorted({info[4][0] for info in infos})


def check(db_url: str) -> tuple[bool, str]:
    """One connect attempt + SELECT 1. Returns (ok, message)."""
    host = urlsplit(db_url).hostname or "?"
    try:
        conn = get_db_connection(attempts=1)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — any failure here is the finding
        first_line = (str(exc).strip().splitlines() or ["?"])[0][:300]
        runner = os.getenv("RUNNER_NAME") or socket.gethostname()
        workflow = os.getenv("GITHUB_WORKFLOW") or "list-maker"
        return False, (
            f":rotating_light: *list-maker: DB preflight failed* — *{workflow}* stopped before "
            f"doing any work.\n"
            f"host `{host}` · addresses `{', '.join(resolved_addresses(host))}` · "
            f"runner `{runner}` · {DB_CONNECT_TIMEOUT_SECONDS}s per address\n"
            f"`{first_line}`\n"
            f"_If sibling jobs this minute succeeded, it is this runner's network path, not Neon "
            f"— the next scheduled run self-heals (imports are idempotent)._"
        )
    return True, f"DB preflight ok — {host} reachable"


def main() -> None:
    load_environment()
    db_url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or ""
    ok, message = check(db_url)
    if ok:
        print(message)
        return
    print(message, file=sys.stderr)
    post_slack(message)
    sys.exit(1)


if __name__ == "__main__":
    main()

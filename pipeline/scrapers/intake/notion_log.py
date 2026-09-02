"""The Notion intake log — the human surface for a pipeline that no longer waits on a human.

Kevin's decision, 2026-09-02: Notion stays where he reads, but nothing blocks on a
checkbox. So the old "Blog Pull Queue" database is repurposed in place — same
database id, same 45 rows, same URLs — into **Blog Intake**: every candidate the
weekly run judged, with the verdict, the confidence, the one-line reason, which two
models said it, and whether they disagreed. The only box left is **Pull anyway**,
the override door: tick it and the next run ingests that row and records
`override_by = kevin`. Ticking nothing is a valid week.

Three rules, because this database holds a year of Kevin's own marks:

* `ensure_schema` is **additive only**. It adds properties and select options,
  renames `Pull` → `Pull anyway`, and rewrites the title and description prose that
  now describe a checkbox workflow that no longer exists. It never deletes a
  property, an option, or a row — the legacy `candidate`/`pulled`/`pdf-report`
  statuses stay until no row uses them, and the retired `Why` column keeps whatever
  the old queue wrote on the 45 historical rows. Retired means never written again,
  not removed: `Reason` (the judge's one-line answer) is its successor.
* `upsert_row` **adopts** an existing page before creating one. The 45 rows predate
  `intake_candidates`, so their Neon rows carry no `notion_page_id`; matching on URL
  first is what keeps the repurposing from doubling the database.
* Every write is idempotent. Re-running the weekly job re-PATCHes the same pages.

Rate limiting, retries and the 2000-char property caps come from `sync_notion`'s
`notion_request`, which is the one place this repo talks to Notion.
"""

from __future__ import annotations

from typing import Optional

from pipeline.common import get_logger
from pipeline.sync_notion import NOTION_API, notion_request

# Same database as the old build_pull_queue.QUEUE_DB_ID — repurposed, not replaced.
INTAKE_DB_ID = "37c0501e-f950-8139-b52b-e5d5f7d71f53"
INTAKE_URL = "https://www.notion.so/37c0501ef9508139b52be5d5f7d71f53"
INTAKE_TITLE = "Blog Intake"
OVERRIDE_PROP = "Pull anyway"
LEGACY_OVERRIDE_PROP = "Pull"
INTAKE_DESCRIPTION = (
    "Every blog post, article and cited report the weekly intake looked at, with the "
    "judge's verdict and why. Nothing here waits on you — tick \"Pull anyway\" on a "
    "row the judge skipped and the next run ingests it. Verdict/Reason/Judge come "
    "from two cheap models reading docs/intake-rubric.md; Disputed means they "
    "disagreed and the recall-first rule saved it anyway."
)

# Properties this log needs. Anything already present is left exactly as it is —
# including its type, so a property Kevin re-typed by hand is never clobbered.
REQUIRED_PROPERTIES: dict[str, dict] = {
    "URL": {"url": {}},
    # Options deliberately left empty: Notion creates a select option on first write,
    # so a new source slug (podcast-linked, added 2026-09-02) needs no schema change.
    # Pinning a closed list here would silently drop rows carrying an unlisted source.
    "Source": {"select": {}},          # openai-rss | anthropic-* | podcast-cited | podcast-linked
    "Published": {"date": {}},
    "Words": {"number": {}},
    "Links Out": {"number": {}},
    "Verdict": {"select": {"options": [
        {"name": "save", "color": "green"},
        {"name": "skip", "color": "gray"},
    ]}},
    "Confidence": {"number": {"format": "percent"}},
    "Reason": {"rich_text": {}},
    # Which rubric rule fired, and (for a save) the later use it serves. A select on
    # both because they are closed vocabularies — Kevin can group the log by "what
    # kind of yes was this", which a free-text reason can never be grouped by.
    "Rule": {"select": {}},
    "Job": {"select": {}},
    "Judge": {"rich_text": {}},        # "judge model | checker model"
    "Disputed": {"checkbox": {}},
    "Precheck": {"rich_text": {}},     # duplicate | thin (117 words) | pdf | dead — a script decided
    OVERRIDE_PROP: {"checkbox": {}},
    "Found Via": {"rich_text": {}},
    "Last Cited": {"date": {}},
}
# Deliberately NOT here: "Why". The old queue filled it with a mention's context
# snippet; nothing in the judged intake writes it, and `Reason` — the judge's own
# one-line answer — is what replaced it. Declaring it would create a permanently
# empty column on a fresh database while claiming the log needs it. It stays on the
# 45 legacy rows (ensure_schema removes nothing) frozen at whatever the old pipeline
# last wrote; retired in place, 2026-09-02.
RETIRED_PROPERTIES = ("Why",)

# Status options the new lifecycle needs. Merged into whatever the database already
# has; the legacy candidate/pulled/pdf-report options are never removed (removing an
# option would blank it on every historical row that carries it).
STATUS_OPTIONS = [
    {"name": "discovered", "color": "default"},
    {"name": "judged", "color": "blue"},
    {"name": "saved", "color": "green"},
    {"name": "skipped", "color": "gray"},
    {"name": "held", "color": "orange"},
    {"name": "failed", "color": "red"},
]

log = get_logger("pipeline.intake.notion_log")


# ── schema ──────────────────────────────────────────────────────────────────

def fetch_schema(token: str, db_id: str) -> dict:
    return notion_request("GET", f"{NOTION_API}/databases/{db_id}", token)


def plan_schema_changes(schema: dict) -> tuple[dict, list[str]]:
    """(PATCH body, human-readable change list) to bring a database up to the log's shape.

    Pure so the plan can be printed by --dry-run and asserted in tests without a
    network call — the part that can quietly destroy a year of Kevin's rows is
    exactly the part that should be inspectable before it runs.
    """
    existing = schema.get("properties") or {}
    props: dict = {}
    changes: list[str] = []

    # A property Kevin already has is left alone, whatever its type.
    for name, spec in REQUIRED_PROPERTIES.items():
        if name in existing:
            continue
        if name == OVERRIDE_PROP and LEGACY_OVERRIDE_PROP in existing:
            continue  # handled by the rename below, which keeps the historical ticks
        props[name] = spec
        changes.append(f"+ property {name} ({next(iter(spec))})")

    if OVERRIDE_PROP not in existing and LEGACY_OVERRIDE_PROP in existing:
        # Rename rather than add: two checkboxes side by side is a trap, and a rename
        # keeps every tick Kevin ever made instead of stranding them on a dead column.
        props[LEGACY_OVERRIDE_PROP] = {"name": OVERRIDE_PROP}
        changes.append(f"~ property {LEGACY_OVERRIDE_PROP} → {OVERRIDE_PROP}")

    status = existing.get("Status")
    if status is None:
        props["Status"] = {"select": {"options": STATUS_OPTIONS}}
        changes.append("+ property Status (select)")
    elif status.get("type") == "select":
        have = {o["name"] for o in status["select"].get("options", [])}
        missing = [o for o in STATUS_OPTIONS if o["name"] not in have]
        if missing:
            # Notion replaces the option list wholesale, so send existing + missing.
            props["Status"] = {"select": {"options": status["select"]["options"] + missing}}
            changes.append("+ Status options: " + ", ".join(o["name"] for o in missing))

    body: dict = {}
    if props:
        body["properties"] = props
    if _plain_text(schema.get("title")) != INTAKE_TITLE:
        body["title"] = [{"type": "text", "text": {"content": INTAKE_TITLE}}]
        changes.append(f"~ title → {INTAKE_TITLE}")
    if _plain_text(schema.get("description")) != INTAKE_DESCRIPTION:
        # The old description told Kevin to check boxes. A stale instruction on the
        # surface he reads is worse than none (docs/principles.md, legibility).
        body["description"] = [{"type": "text", "text": {"content": INTAKE_DESCRIPTION}}]
        changes.append("~ description → the judged-intake wording")
    return body, changes


def ensure_schema(token: str, db_id: str = INTAKE_DB_ID, *, dry_run: bool = False) -> list[str]:
    """Add what's missing, rename Pull, refresh the prose. Returns the changes made.

    Idempotent: the second call returns []. Never removes anything.
    """
    body, changes = plan_schema_changes(fetch_schema(token, db_id))
    if not body:
        return []
    if dry_run:
        return changes
    notion_request("PATCH", f"{NOTION_API}/databases/{db_id}", token, body)
    log.info("intake log schema updated: %s", "; ".join(changes))
    return changes


# ── rows ────────────────────────────────────────────────────────────────────

def build_properties(row: dict) -> dict:
    """A candidate row (the Neon shape) → Notion properties.

    Only fields that HAVE a value are sent. An unscraped row shows an empty Words
    cell, not a zero, and an unjudged one shows no Verdict — the log has to be able
    to say "we haven't got there yet" (docs/principles.md: NULL over a fake default).
    """
    title = (row.get("title") or "").strip() or row["url"]
    props: dict = {
        "Name": {"title": [{"text": {"content": title[:2000]}}]},
        "URL": {"url": row["url"][:2000]},
        "Status": {"select": {"name": row.get("status") or "discovered"}},
    }
    if row.get("source"):
        props["Source"] = {"select": {"name": str(row["source"])[:100]}}
    if row.get("published_on"):
        props["Published"] = {"date": {"start": str(row["published_on"])}}
    if row.get("words") is not None:
        props["Words"] = {"number": int(row["words"])}
    if row.get("links_out") is not None:
        props["Links Out"] = {"number": int(row["links_out"])}
    # A judged row that a later pre-check overturned has these cleared in Neon, and an
    # already-mirrored page would otherwise keep displaying the old verdict forever —
    # a PATCH only touches the properties it sends. So when a pre-check owns the row,
    # send the judge cells as explicit empties rather than omitting them.
    overturned = bool(row.get("precheck"))
    if row.get("verdict"):
        props["Verdict"] = {"select": {"name": row["verdict"]}}
    elif overturned:
        props["Verdict"] = {"select": None}
    if row.get("confidence") is not None:
        props["Confidence"] = {"number": float(row["confidence"])}
    elif overturned:
        props["Confidence"] = {"number": None}
    if row.get("reason"):
        props["Reason"] = {"rich_text": [{"text": {"content": str(row["reason"])[:1900]}}]}
    elif overturned:
        props["Reason"] = {"rich_text": []}
    if row.get("rule"):
        props["Rule"] = {"select": {"name": str(row["rule"])[:100]}}
    elif overturned:
        props["Rule"] = {"select": None}
    if row.get("job"):
        props["Job"] = {"select": {"name": str(row["job"])[:100]}}
    elif overturned:
        props["Job"] = {"select": None}
    if row.get("judge_model"):
        judge = row["judge_model"]
        if row.get("checker_model"):
            judge = f"{judge} | {row['checker_model']}"
        props["Judge"] = {"rich_text": [{"text": {"content": judge[:1900]}}]}
    elif overturned:
        props["Judge"] = {"rich_text": []}
    props["Disputed"] = {"checkbox": bool(row.get("disputed"))}
    precheck = _precheck_text(row)
    if precheck:
        props["Precheck"] = {"rich_text": [{"text": {"content": precheck[:1900]}}]}

    found_via = found_via_text(row)
    if found_via:
        props["Found Via"] = {"rich_text": [{"text": {"content": found_via[:1900]}}]}
    via = row.get("discovered_via") or {}
    if via.get("last_cited"):
        props["Last Cited"] = {"date": {"start": str(via["last_cited"])}}
    return props


def _precheck_text(row: dict) -> str:
    """"thin (117 words)" for Kevin, assembled from the columns that hold each fact.

    Neon stores the bare token so the weekly counts can group on it; the specifics
    live in `words` and `failed_reason`. This is the one place they're joined back
    together, and only for display.
    """
    token = row.get("precheck")
    if not token:
        return ""
    if token == "thin" and row.get("words") is not None:
        return f"thin ({row['words']} words)"
    if token == "dead" and row.get("failed_reason"):
        return f"dead ({str(row['failed_reason'])[:200]})"
    return str(token)


def found_via_text(row: dict) -> str:
    """One readable line of provenance — which feed, or which shows cited it.

    Also the `found_via` the judge is shown: a post an episode cited is a different
    thing from a post a feed listed, and the rubric is allowed to care.
    """
    via = row.get("discovered_via") or {}
    parts: list[str] = []
    shows = via.get("shows") or []
    if shows:
        cited = via.get("cited_in_episodes")
        parts.append(f"{', '.join(map(str, shows))}" + (f" — {cited} episode(s)" if cited else ""))
    if via.get("cited_as"):
        parts.append(f"cited as “{via['cited_as']}”")
    if via.get("feed"):
        parts.append(str(via["feed"]))
    if via.get("index"):
        parts.append(str(via["index"]))
    if via.get("link_confidence") is not None:
        parts.append(f"link resolved at {float(via['link_confidence']):.2f}")
    also = via.get("also_sources") or []
    if also:
        parts.append("also seen via " + ", ".join(map(str, also)))
    return " · ".join(parts)


def existing_page_ids(token: str, db_id: str = INTAKE_DB_ID) -> dict[str, str]:
    """URL → page id for every row in the log, in one paginated read.

    This is the adoption map: rows created before `intake_candidates` existed have no
    id stored in Neon, and creating a second page for a URL already in the log is the
    one way this migration could visibly damage Kevin's database.
    """
    pages: dict[str, str] = {}
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
        for page in result.get("results", []):
            url = (page.get("properties", {}).get("URL") or {}).get("url")
            if url:
                pages.setdefault(url, page["id"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return pages


def upsert_row(token: str, db_id: str, row: dict,
               known_pages: Optional[dict[str, str]] = None) -> str:
    """Create or update the log row for one candidate. Returns the page id.

    Resolution order: the page id Neon already stored → a page with the same URL
    (adoption) → create. `known_pages` is the map from `existing_page_ids`, so the
    adoption lookup costs no extra request per row.
    """
    props = build_properties(row)
    page_id = row.get("notion_page_id") or (known_pages or {}).get(row["url"])
    if page_id:
        notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token, {"properties": props})
        return page_id
    created = notion_request("POST", f"{NOTION_API}/pages", token,
                             {"parent": {"database_id": db_id}, "properties": props})
    return created["id"]


def override_rows(token: str, db_id: str = INTAKE_DB_ID) -> list[dict]:
    """Rows Kevin ticked "Pull anyway" that aren't ingested yet.

    Excludes `saved` rather than filtering to one status: a tick on a `skipped`,
    `judged`, `held` or `failed` row all mean the same thing — he wants it — and a
    filter that only looked at one of those would silently ignore the others.
    """
    out: list[dict] = []
    cursor = None
    where = {"and": [
        {"property": OVERRIDE_PROP, "checkbox": {"equals": True}},
        {"property": "Status", "select": {"does_not_equal": "saved"}},
    ]}
    while True:
        body: dict = {"page_size": 100, "filter": where}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"{NOTION_API}/databases/{db_id}/query", token, body)
        for page in result.get("results", []):
            url = (page.get("properties", {}).get("URL") or {}).get("url")
            if url:
                out.append({"page_id": page["id"], "url": url})
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return out


def set_status(token: str, page_id: str, status: str) -> None:
    notion_request("PATCH", f"{NOTION_API}/pages/{page_id}", token,
                   {"properties": {"Status": {"select": {"name": status}}}})


def _plain_text(rich: Optional[list]) -> str:
    return "".join(part.get("plain_text", "") for part in (rich or []))

"""The Neon side of the intake: reading and writing `intake_candidates`.

One row per canonical URL, carrying its own provenance — where it came from, what
the scrape measured, which models judged it under which rubric version, and what
happened next. That is the whole point of the table: a bad Monday should be
reconstructible from one SELECT, without a join and without reading a log
(docs/principles.md, "Data with provenance").

Two rules this module exists to enforce:

* **Nothing here invents a value.** A row that hasn't been scraped has NULL words,
  not 0. A pre-check skip leaves `verdict` NULL — a script decided, no model spoke.
* **Re-running is free.** `upsert_candidates` keeps the first discovery,
  `needs_judging` hands back only rows without a verdict for the CURRENT rubric,
  and every writer is a plain UPDATE. A weekly job that can't be run twice by hand
  is a job nobody dares re-run when it half-fails.

The table is Kevin's paste (DDL is guard-blocked for agents): `require_table` turns
a missing table into one clear instruction instead of a psycopg2 traceback forty
lines into a run.

Status vocabulary (matches the CHECK in sql/010; the two ambiguous cases are
resolved here and nowhere else):
    discovered  surfaced by a source, not yet scraped or judged
    judged      verdict = save, nothing ingested — shadow mode's "would save",
                and the set the Notion override door acts on
    skipped     the judge said skip, OR a pre-check did (`precheck` says which)
    held        needs a human-only step. `pdf` is the only reason today, so
                `record_precheck` is the only writer and there is no `mark_held`
    saved       ingested; ingested_at set, and episode_id set EXCEPT for a local
                PDF (precheck='pdf'), which saves as a file in the Obsidian
                research folder and has no episodes row at all. A consumer
                joining on episode_id should filter `precheck IS DISTINCT FROM
                'pdf'` rather than assume the column is non-NULL
    failed      ingest attempted and failed; failed_reason says why
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from pipeline.scrapers.blog.import_blog import canonicalize_url
from pipeline.scrapers.intake.judge import Decision, Precheck
from pipeline.scrapers.intake.sources import Candidate

TABLE = "intake_candidates"
MIGRATION_PATH = "pipeline/scrapers/ai_daily/sql/010_intake_candidates.sql"

STATUS_DISCOVERED = "discovered"
STATUS_JUDGED = "judged"
STATUS_SKIPPED = "skipped"
STATUS_HELD = "held"
STATUS_SAVED = "saved"
STATUS_FAILED = "failed"

# Statuses a rubric change may re-open. `saved` is done, `held` and any pre-check
# outcome are structural facts about the URL (already ingested, dead, a PDF) that a
# new rubric cannot change — re-judging those would burn Firecrawl credits and
# model calls every week to reach the same answer. `failed` is not here either, but
# for a different reason: it is re-opened by its own narrow branch in needs_judging,
# only when the crash happened BEFORE a verdict existed.
REJUDGEABLE_STATUSES = (STATUS_JUDGED, STATUS_SKIPPED)

MISSING_TABLE_HINT = (
    f"{TABLE} does not exist in this database.\n"
    f"It is created by {MIGRATION_PATH}, which Kevin runs (DDL is blocked for agents).\n"
    "Paste to run it, using the repo's own schema runner:\n"
    "  cd ~/DevKev/personal/list-maker \\\n"
    "    && pipeline/venv/bin/python pipeline/scrapers/ai_daily/init_entity_schema.py \\\n"
    f"       --schema-file {MIGRATION_PATH}\n"
    "(or paste the file into the Neon SQL editor). Then re-run this command."
)

# Every column a caller reads. Spelled out rather than SELECT * so a schema change
# shows up as a failing test here instead of a KeyError in the Notion mirror.
SELECT_COLUMNS = (
    "id, url, source, title, published_on, category, discovered_at, discovered_via, "
    "words, links_out, text_sha256, scraped_at, "
    "verdict, confidence, reason, rule, job, "
    "judge_model, checker_model, checker_verdict, "
    "disputed, prompt_version, judged_at, "
    "status, precheck, episode_id, ingested_at, failed_reason, override_by, "
    "notion_page_id, notion_synced_at"
)


# ── the table has to be there ───────────────────────────────────────────────

def table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS reg", (f"public.{TABLE}",))
        row = cur.fetchone()
    return bool(row and row["reg"])


def require_table(conn) -> None:
    """Fail with the paste instruction, not a traceback, when the DDL hasn't run."""
    if not table_exists(conn):
        raise SystemExit(MISSING_TABLE_HINT)


# ── discovery → rows ────────────────────────────────────────────────────────

_UPSERT_SQL = f"""
INSERT INTO {TABLE} (url, source, title, published_on, category, discovered_via, status)
VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, '{STATUS_DISCOVERED}')
ON CONFLICT (url) DO UPDATE SET
    -- The FIRST discovery wins on every field it already filled: which source found
    -- a post first is provenance, not a detail to overwrite on the next run. A later
    -- discovery only fills gaps (a feed item that arrived without a date, say).
    title        = COALESCE({TABLE}.title, EXCLUDED.title),
    published_on = COALESCE({TABLE}.published_on, EXCLUDED.published_on),
    category     = CASE WHEN {TABLE}.category = '[]'::jsonb
                        THEN EXCLUDED.category ELSE {TABLE}.category END,
    -- Merge, existing keys winning, and record that a second source saw it too:
    -- "the OpenAI feed carried it AND an episode cited it" is a real signal, and
    -- losing it would make the row look like whichever source ran first.
    discovered_via = (EXCLUDED.discovered_via || {TABLE}.discovered_via)
        || CASE
             WHEN {TABLE}.source <> EXCLUDED.source
              AND NOT (COALESCE({TABLE}.discovered_via->'also_sources', '[]'::jsonb)
                       @> to_jsonb(EXCLUDED.source))
             THEN jsonb_build_object('also_sources',
                    COALESCE({TABLE}.discovered_via->'also_sources', '[]'::jsonb)
                    || to_jsonb(EXCLUDED.source))
             ELSE '{{}}'::jsonb
           END,
    updated_at = now()
RETURNING id, (xmax = 0) AS created
"""


def upsert_candidates(conn, candidates: Iterable[Candidate]) -> tuple[int, int]:
    """Insert new candidates, leave known ones alone. Returns (new, existing).

    URLs are canonicalized here as well as by the caller: `canonicalize_url` is
    idempotent, and doing it at the write boundary means no code path can create a
    second row for the http:// or utm-tagged twin of a URL already in the table.
    """
    new = existing = 0
    with conn.cursor() as cur:
        for cand in candidates:
            if not cand.url:
                continue  # an unresolved citation is not a candidate (links.py keeps the mention)
            cur.execute(_UPSERT_SQL, (
                canonicalize_url(cand.url),
                cand.source,
                (cand.title or "").strip() or None,
                cand.published_on,
                json.dumps(list(cand.category or [])),
                json.dumps(cand.discovered_via or {}, default=str),
            ))
            row = cur.fetchone()
            new += bool(row["created"])
            existing += not row["created"]
    conn.commit()
    return new, existing


def last_seen_published(conn, source: str) -> Optional[date]:
    """Newest `published_on` already stored for a source — the feed's `since` cursor.

    Deliberately the newest POST date, not the last run time: if the weekly job
    misses three weeks, this still asks the feed for everything since the last post
    we actually hold, so a skipped run catches up instead of leaving a hole.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT MAX(published_on) AS latest FROM {TABLE} WHERE source = %s",
            (source,),
        )
        row = cur.fetchone()
    return row["latest"] if row else None


# ── the work list ───────────────────────────────────────────────────────────

def pending(conn, status: str, limit: Optional[int] = None) -> list[dict]:
    """Every row at one status, newest post first."""
    sql = (f"SELECT {SELECT_COLUMNS} FROM {TABLE} WHERE status = %s "
           "ORDER BY published_on DESC NULLS LAST, discovered_at DESC")
    params: list = [status]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def needs_judging(conn, prompt_version: Optional[str], limit: Optional[int] = None) -> list[dict]:
    """Rows with no verdict under the CURRENT rubric version.

    Three groups: anything still `discovered`; anything a MODEL judged under a
    different rubric version (a rubric edit is a new `prompt_version`, and re-judging
    under it is the point of versioning it); and anything that CRASHED before it got a
    verdict.

    Rows a pre-check decided are excluded — `precheck IS NULL` — because "already
    ingested" and "dead link" don't change when the rubric does.

    The crash branch is narrow on purpose. `failed` is overloaded: `mark_failed` sets
    it both when judging blew up (no verdict — an OpenRouter blip, a timeout, and
    judge.judge_once raises rather than retrying) and when a row that HAS a verdict
    failed to ingest. Only the first deserves another go; re-judging the second would
    ask the models the same question every week forever. `verdict IS NULL` is what
    separates them. Without this branch a transient API blip was permanent data loss:
    the row left the work list for good, and the only recovery was Kevin ticking "Pull
    anyway", which force-ingests it without ever getting the verdict that is the whole
    point of the intake.
    """
    sql = f"""
        SELECT {SELECT_COLUMNS} FROM {TABLE}
        WHERE status = %s
           OR (status = ANY(%s) AND precheck IS NULL
               AND prompt_version IS DISTINCT FROM %s)
           OR (status = %s AND verdict IS NULL AND precheck IS NULL)
        ORDER BY published_on DESC NULLS LAST, discovered_at DESC
    """
    params: list = [STATUS_DISCOVERED, list(REJUDGEABLE_STATUSES), prompt_version,
                    STATUS_FAILED]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def needs_mirroring(conn, limit=None) -> list[dict]:
    """Rows the Notion log has never seen, or has seen in an older state.

    Without this a mirror failure is permanent: the judging loop only visits rows
    that still need a verdict, so a row whose Notion write failed keeps its verdict
    in Neon and never appears on the surface Kevin reads. The run would report the
    failure once and then be quiet about it forever. Catching up at the start of each
    run turns a Notion outage into a delay instead of a hole.
    """
    sql = f"""
        SELECT {SELECT_COLUMNS} FROM {TABLE}
        WHERE status <> %s
          AND (notion_page_id IS NULL OR notion_synced_at IS NULL
               OR notion_synced_at < updated_at)
        ORDER BY updated_at DESC
    """
    params: list = [STATUS_DISCOVERED]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def get_by_id(conn, candidate_id: int) -> Optional[dict]:
    """One row, re-read. The Notion mirror uses this rather than the dict it started
    with, so the log shows what the database says and not what this process believes."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {SELECT_COLUMNS} FROM {TABLE} WHERE id = %s", (candidate_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def get_by_urls(conn, urls: Sequence[str]) -> dict[str, dict]:
    """Rows for these canonical URLs, keyed by URL (the Notion override path's lookup)."""
    if not urls:
        return {}
    with conn.cursor() as cur:
        cur.execute(f"SELECT {SELECT_COLUMNS} FROM {TABLE} WHERE url = ANY(%s)", (list(urls),))
        return {r["url"]: dict(r) for r in cur.fetchall()}


def already_ingested_urls(conn, urls: Sequence[str]) -> set[str]:
    """Which of these URLs are already episodes — the `duplicate` pre-check, in one query.

    `episodes.url` is the canonical dedup key (import_blog.canonicalize_url), so the
    comparison is exact; callers pass canonical URLs.
    """
    if not urls:
        return set()
    with conn.cursor() as cur:
        cur.execute("SELECT url FROM episodes WHERE url = ANY(%s)", (list(urls),))
        return {r["url"] for r in cur.fetchall()}


# ── writers, one per stage ──────────────────────────────────────────────────

def record_scrape(conn, candidate_id: int, *, words: Optional[int], links_out: Optional[int],
                  text_sha256: Optional[str], title: Optional[str] = None,
                  published_on: Optional[date] = None) -> None:
    """What the scrape measured. NULLs stay NULL — a failed scrape is not zero words.

    The scraped title and date only FILL missing ones (podcast-cited rows arrive with
    neither); a feed's own title and pubDate are authoritative and shouldn't be
    replaced by whatever Firecrawl reads off a JS shell.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {TABLE}
                SET words = %s, links_out = %s, text_sha256 = %s,
                    title = COALESCE(title, NULLIF(%s, '')),
                    published_on = COALESCE(published_on, %s),
                    scraped_at = now(), updated_at = now()
                WHERE id = %s""",
            (words, links_out, text_sha256, (title or "").strip(), published_on, candidate_id),
        )
    conn.commit()


def record_precheck(conn, candidate_id: int, precheck: Precheck,
                    detail: Optional[str] = None) -> None:
    """A pre-check decided; no model was asked, so `verdict` stays NULL on purpose.

    `precheck` holds the bare token (duplicate | thin | pdf | dead | academy |
    people-news | stale) so the weekly line can GROUP BY it; the specifics live in
    columns that already exist — the word count in `words`, a dead link's error in
    `failed_reason`. One fact, one column: a "thin (117 words)" token would split the
    grouping into one bucket per post.

    The judge columns are cleared in the same statement, mirroring what
    `record_decision` does with `precheck = NULL`. A row CAN arrive here already
    judged: a rubric edit re-opens it (that is what prompt_version is for), and this
    pass may then find it stale, already ingested, 404, or newly paywalled. Leaving
    the old verdict behind would freeze a self-contradicting row forever — `precheck`
    saying a script decided while `verdict`/`judge_model` still show a model's answer
    under a `prompt_version` it no longer earned — and, because a non-NULL precheck is
    excluded from re-judging by design, it could never be corrected. Misleading
    provenance is precisely what this table exists to prevent.
    """
    if precheck.skip_reason is None:
        raise ValueError("record_precheck called on a candidate that passed its pre-checks")
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {TABLE}
                SET precheck = %s, status = %s, failed_reason = %s,
                    verdict = NULL, confidence = NULL, reason = NULL,
                    rule = NULL, job = NULL,
                    judge_model = NULL, checker_model = NULL, checker_verdict = NULL,
                    disputed = FALSE, prompt_version = NULL, judged_at = NULL,
                    updated_at = now()
                WHERE id = %s""",
            (precheck.skip_reason, precheck.status,
             str(detail)[:500] if detail else None, candidate_id),
        )
    conn.commit()


def record_decision(conn, candidate_id: int, decision: Decision, *,
                    status: Optional[str] = None) -> str:
    """Store the full verdict — both models, the rubric version, when, why.

    `rule` and `job` are the rubric's own provenance: WHICH rule fired (S1…K9, R-*,
    X-*) and, for a save, the later use it serves. Stored beside the reason because a
    one-line reason ages into prose, while a rule id stays checkable against the
    rubric version that produced it.

    Default status follows the verdict: `skip` → skipped, `save` → judged ("a verdict
    exists, nothing was ingested"). run_intake passes an explicit status once
    auto-ingest is on and a save becomes `saved`/`failed`.
    """
    status = status or (STATUS_SKIPPED if decision.verdict == "skip" else STATUS_JUDGED)
    checker = decision.checker
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {TABLE} SET
                    verdict = %s, confidence = %s, reason = %s, rule = %s, job = %s,
                    judge_model = %s, checker_model = %s, checker_verdict = %s,
                    disputed = %s, prompt_version = %s, judged_at = now(),
                    status = %s, precheck = NULL, failed_reason = NULL,
                    updated_at = now()
                WHERE id = %s""",
            (decision.verdict, decision.confidence, decision.reason,
             decision.rule, decision.job,
             decision.judge.model, checker.model if checker else None,
             checker.verdict if checker else None,
             decision.disputed, decision.prompt_version, status, candidate_id),
        )
    conn.commit()
    return status


def mark_saved(conn, candidate_id: int, episode_id: Optional[int], *,
               override_by: Optional[str] = None) -> None:
    """Ingested. `episode_id` is Optional deliberately, not defensively.

    A PDF override is the one save with nothing to point at: save_item downloads it
    into the Obsidian research folder and never writes an episodes row (Kevin's rule —
    reports live as files). The row still reconstructs from one SELECT, because
    precheck='pdf' survives this write and says why episode_id is NULL.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {TABLE}
                SET status = %s, episode_id = %s, ingested_at = now(),
                    failed_reason = NULL, override_by = COALESCE(%s, override_by),
                    updated_at = now()
                WHERE id = %s""",
            (STATUS_SAVED, episode_id, override_by, candidate_id),
        )
    conn.commit()


def mark_failed(conn, candidate_id: int, reason: str, *, override_by: Optional[str] = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {TABLE}
                SET status = %s, failed_reason = %s,
                    override_by = COALESCE(%s, override_by), updated_at = now()
                WHERE id = %s""",
            (STATUS_FAILED, str(reason)[:500], override_by, candidate_id),
        )
    conn.commit()


def record_notion_page(conn, candidate_id: int, page_id: str) -> None:
    """The log now holds this row's current state.

    Deliberately does NOT touch `updated_at`: that column means "the row's content
    last changed", and `needs_mirroring` compares it against `notion_synced_at` to
    decide what the log is behind on. Bumping it here would make every mirror look
    like a fresh change and re-push the same row on every run forever.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TABLE} SET notion_page_id = %s, notion_synced_at = now() "
            "WHERE id = %s",
            (page_id, candidate_id),
        )
    conn.commit()


# ── what to say out loud ────────────────────────────────────────────────────

def weekly_counts(conn, since: datetime) -> dict:
    """The numbers the weekly Slack line reports.

    `since` is normally the run's own start time, so the line describes THIS run
    rather than a rolling window — the pulse passes a wider one. Every count is a
    real query, never a running tally kept in Python: a step that crashed halfway
    must not be able to report a number the table disagrees with.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT
                  COUNT(*) FILTER (WHERE discovered_at >= %(s)s)                        AS discovered,
                  COUNT(*) FILTER (WHERE judged_at >= %(s)s)                            AS judged,
                  COUNT(*) FILTER (WHERE judged_at >= %(s)s AND verdict = 'save')       AS would_save,
                  COUNT(*) FILTER (WHERE judged_at >= %(s)s AND verdict = 'skip')       AS judge_skipped,
                  COUNT(*) FILTER (WHERE judged_at >= %(s)s AND disputed)               AS disputed,
                  COUNT(*) FILTER (WHERE updated_at >= %(s)s AND precheck IS NOT NULL
                                     AND status = %(skipped)s)                          AS precheck_skipped,
                  COUNT(*) FILTER (WHERE updated_at >= %(s)s AND status = %(held)s)     AS held,
                  COUNT(*) FILTER (WHERE updated_at >= %(s)s AND status = %(failed)s)   AS failed,
                  COUNT(*) FILTER (WHERE ingested_at >= %(s)s)                          AS saved,
                  COUNT(*) FILTER (WHERE ingested_at >= %(s)s AND override_by IS NOT NULL) AS overrides,
                  COUNT(*) FILTER (WHERE status = %(judged)s)                           AS would_save_backlog
                FROM {TABLE}""",
            {"s": since, "skipped": STATUS_SKIPPED, "held": STATUS_HELD,
             "failed": STATUS_FAILED, "judged": STATUS_JUDGED},
        )
        counts = dict(cur.fetchone())
        # Why each pre-check fired, so "skipped 31" is never a number without a cause.
        # Restricted to `skipped`: a PDF is also a pre-check outcome but it is reported
        # under `held`, and counting it in both places would make the line not add up.
        cur.execute(
            f"""SELECT precheck, COUNT(*) AS n FROM {TABLE}
                WHERE updated_at >= %s AND precheck IS NOT NULL AND status = %s
                GROUP BY precheck ORDER BY n DESC""",
            (since, STATUS_SKIPPED),
        )
        counts["precheck_reasons"] = {r["precheck"]: r["n"] for r in cur.fetchall()}
    return counts


def titles(conn, since: datetime, status: str, limit: int = 5) -> list[str]:
    """Titles for the Slack line — a count with no names tells you nothing to act on."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT COALESCE(NULLIF(title, ''), url) AS label FROM {TABLE}
                WHERE status = %s AND updated_at >= %s
                ORDER BY confidence DESC NULLS LAST, updated_at DESC LIMIT %s""",
            (status, since, limit),
        )
        return [r["label"] for r in cur.fetchall()]

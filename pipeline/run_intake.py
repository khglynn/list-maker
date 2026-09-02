#!/usr/bin/env python3
"""The weekly curated intake: discover → pre-check → scrape → judge → log.

Replaces `build_pull_queue.py`, which discovered candidates and then waited for Kevin
to tick a box. Eleven consecutive weekly runs (2026-06-21 → 08-31) found nothing new,
said nothing, and left 31 candidates un-triaged — including the one post ("How people
are using ChatGPT") the whole idea existed to catch. The fix isn't a better nudge, it
is to stop asking: two cheap models read the post against `docs/intake-rubric.md` and
answer save or skip, every candidate lands in the Notion log with the verdict and the
reason, and the only human input left is the "Pull anyway" override.

    ./venv/bin/python run_intake.py --dry-run           # discovery + free pre-checks, no writes
    ./venv/bin/python run_intake.py                     # the weekly run (Mondays, blogs.yml)
    ./venv/bin/python run_intake.py --sources podcast-cited   # daily-able: resolve + judge citations
    ./venv/bin/python run_intake.py --overrides-only    # just ingest the rows Kevin ticked
    ./venv/bin/python run_intake.py --ensure-log-schema # reshape the Notion DB into the log

SHADOW MODE (this PR): `AUTO_INGEST = False`. Verdicts are recorded and mirrored to
Notion, and a `save` lands at status `judged` — "we would have saved this" — but
nothing is ingested except rows Kevin explicitly ticked. PR 3 flips the constant once
`evals/intake` clears its floor (recall ≥ 0.9 on save, precision ≥ 0.7) and one shadow
week reads right. Nothing else in this file changes when it flips.

Failure states are visible by construction: a missing table, a missing rubric, or any
failed candidate exits non-zero (so blogs.yml's failure notify fires), and the Slack
line posts on every run — including a week where nothing happened. Silence must never
be the designed outcome of a job whose job is to tell you something.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import get_db_connection, get_logger, load_environment, post_slack  # noqa: E402
from pipeline.scrapers.blog.import_blog import (  # noqa: E402
    canonicalize_url,
    parse_publish_date,
    pick_title,
    scrape_post,
)
from pipeline.scrapers.intake import judge, mentions, notion_log, sources, store  # noqa: E402
from pipeline.scrapers.intake.sources import Candidate  # noqa: E402

# ── the switch ──────────────────────────────────────────────────────────────
# Shadow mode. PR 3 sets this True after the eval floor clears (evals/intake:
# recall ≥ 0.9 on `save`, precision ≥ 0.7) and one week of shadow verdicts reads
# right to Kevin. While False, a `save` verdict is recorded at status `judged` and
# the weekly line says "would save". Overrides still ingest either way, because
# those are Kevin's own decisions and were never the judge's to make.
AUTO_INGEST = False

# One cap, applied where the money is: each judged candidate costs one Firecrawl
# scrape plus two model calls (~$0.002). Discovery itself is free now — it writes
# rows, it doesn't scrape — so surplus candidates sit at `discovered` and drain over
# following runs instead of being dropped. The overflow is logged and Slacked.
MAX_CANDIDATES_PER_RUN = 60
# How far back to ask a feed we hold no history for yet.
DEFAULT_FEED_LOOKBACK_DAYS = 14
# The link resolver's window: a report cited on Monday should be judged this week.
PODCAST_CITED_SINCE_DAYS = 14
LINK_RESOLVE_LIMIT = 40
# Rows to re-push to Notion per run when the log is behind. Bounded so a long
# outage drains over several runs instead of arriving as one huge burst.
MAX_MIRROR_CATCHUP = 100

SOURCE_CHOICES = ("all", "feeds", "podcast-cited")

log = get_logger("pipeline.run_intake")


# ── discovery ───────────────────────────────────────────────────────────────

def feed_since(conn, source: str, table_ok: bool, lookback_days: int) -> date:
    """Ask a feed for everything since the newest post we already hold from it.

    Deliberately keyed on the newest stored POST date rather than the last run time:
    if the weekly job misses three weeks, this still asks for the whole gap, so a
    skipped run catches up instead of leaving a hole nobody notices.
    """
    latest = store.last_seen_published(conn, source) if table_ok else None
    if latest:
        # One day of overlap: a post published the same day as our newest can appear
        # after that run read the feed, and re-seeing a known URL costs nothing.
        return latest - timedelta(days=1)
    return date.today() - timedelta(days=lookback_days)


def discover(conn, *, groups: str, table_ok: bool, firecrawl_key: Optional[str],
             lookback_days: int, resolve_links: bool, dry_run: bool,
             extra_candidates: Optional[Iterable[Candidate]] = None,
             ) -> tuple[list[Candidate], list[str]]:
    """Every source's candidates, plus a per-source line for the log and the dry run.

    A source that fails is reported and the run continues: one publisher changing its
    HTML must not cost us the other three sources' week. The note says so out loud, so
    "0 from Anthropic" is never mistaken for "Anthropic published nothing".

    `extra_candidates` is the hook for candidates produced elsewhere — anything that
    can be expressed as a `Candidate` joins the same pre-check → judge → log path.
    """
    found: list[Candidate] = []
    notes: list[str] = []
    want_feeds = groups in ("all", "feeds")
    want_cited = groups in ("all", "podcast-cited")

    if want_feeds:
        since = feed_since(conn, "openai-rss", table_ok, lookback_days)
        try:
            items = sources.fetch_openai_rss(since)
            found.extend(items)
            notes.append(f"openai-rss: {len(items)} since {since}")
        except Exception as exc:  # noqa: BLE001 — one source down is not the run down
            notes.append(f"openai-rss: FAILED ({exc})")
            log.error("openai-rss discovery failed: %s", exc)

        for slug in ("anthropic-news", "anthropic-engineering"):
            since = feed_since(conn, slug, table_ok, lookback_days)
            if not firecrawl_key:
                notes.append(f"{slug}: skipped (no FIRECRAWL_API_KEY)")
                continue
            try:
                items = sources.fetch_anthropic_index(slug, firecrawl_key, since)
                found.extend(items)
                notes.append(f"{slug}: {len(items)} since {since}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{slug}: FAILED ({exc})")
                log.error("%s discovery failed: %s", slug, exc)

    if want_cited:
        try:
            cited = mentions.discover_cited_candidates(conn)
            found.extend(cited)
            notes.append(f"podcast-cited (url in the mention): {len(cited)}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"podcast-cited (url in the mention): FAILED ({exc})")
            log.error("cited-mention discovery failed: %s", exc)

        if resolve_links:
            try:
                from pipeline.scrapers.intake import links

                resolved, resolutions = links.podcast_cited_candidates(
                    conn, since_days=PODCAST_CITED_SINCE_DAYS,
                    limit=LINK_RESOLVE_LIMIT, dry_run=dry_run,
                )
                found.extend(resolved)
                notes.append(
                    f"podcast-cited (resolved by search): {len(resolved)} of "
                    f"{len(resolutions)} tried, {len(resolutions) - len(resolved)} "
                    "too generic to pin"
                )
            except Exception as exc:  # noqa: BLE001
                notes.append(f"podcast-cited (resolved by search): FAILED ({exc})")
                log.error("link resolution failed: %s", exc)
        else:
            # Off by default in --dry-run: one Firecrawl search per unresolved mention.
            notes.append("podcast-cited (resolved by search): skipped (pass --resolve-links)")

    extra = list(extra_candidates or [])
    if extra:
        found.extend(extra)
        notes.append(f"extra_candidates (caller-supplied): {len(extra)}")
    return found, notes


def dedupe(candidates: Iterable[Candidate]) -> tuple[list[Candidate], int]:
    """Collapse to one Candidate per canonical URL, first source winning.

    Returns (candidates, collapsed). The same post reaching us from two sources is
    normal — OpenAI's feed and an AI Daily citation both carry the big releases — and
    `upsert_candidates` records the second source in `discovered_via` rather than
    letting whichever ran first look like the only one.
    """
    seen: dict[str, Candidate] = {}
    collapsed = 0
    for cand in candidates:
        if not cand.url:
            continue  # an unresolved citation is not a candidate; links.py keeps the mention
        cand.url = canonicalize_url(cand.url)
        if cand.url in seen:
            collapsed += 1
            continue
        seen[cand.url] = cand
    return list(seen.values()), collapsed


# ── one candidate, start to finish ──────────────────────────────────────────

def scrape_measurements(url: str, firecrawl_key: str) -> tuple[dict, Optional[str]]:
    """({text, words, links_out, text_sha256, title, published_on}, scrape_error).

    Links Out counts http(s) occurrences in the markdown — the same measure the pull
    queue ranked on since June, kept so numbers from the two eras mean the same thing.
    """
    try:
        scraped = scrape_post(url, firecrawl_key)
    except Exception as exc:  # noqa: BLE001 — a dead link is a verdict, not a crash
        return {}, str(exc)[:200]
    text = (scraped.get("markdown") or "").strip()
    meta = scraped.get("metadata") or {}
    return {
        "text": text,
        "words": len(text.split()),
        "links_out": len(re.findall(r"https?://", text)),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "title": pick_title(meta, url),
        "published_on": parse_publish_date(meta, url),
    }, None


def free_precheck(row: dict, *, already_ingested: bool) -> judge.Precheck:
    """Every pre-check that needs no network, on what discovery already knows.

    Shared by the run and by --dry-run so the preview cannot claim a different set of
    skips than the run will make. Feed candidates arrive with a title, a category and
    a date, so `academy`, `people-news` and `stale` can fire here — before a Firecrawl
    credit is spent. Podcast-cited candidates arrive with none of those and fall
    through to the post-scrape pass.
    """
    return judge.precheck(
        row["url"], already_ingested=already_ingested, words=None, scrape_error=None,
        title=(row.get("title") or ""), category=list(row.get("category") or []),
        published_on=row.get("published_on"), source=row.get("source") or "",
    )


def process_candidate(conn, row: dict, *, already_ingested: bool, firecrawl_key: str,
                      openrouter_key: str, rubric_path: Path) -> str:
    """Pre-check → scrape → judge → record. Returns the status the row ended at.

    The order is the cost story: the structural checks that need no network (already
    an episode, a PDF) run before Firecrawl, and the scrape runs before the models, so
    a duplicate costs one set lookup and a dead link costs one scrape.
    """
    pre = free_precheck(row, already_ingested=already_ingested)
    if pre.skip_reason:
        store.record_precheck(conn, row["id"], pre)
        return pre.status

    measured, scrape_error = scrape_measurements(row["url"], firecrawl_key)
    if not scrape_error:
        store.record_scrape(
            conn, row["id"], words=measured["words"], links_out=measured["links_out"],
            text_sha256=measured["text_sha256"], title=measured["title"],
            published_on=measured["published_on"],
        )

    # Again with what the scrape learned: a podcast-cited row arrives with no title
    # and no date, so its people-news and category skips can only fire on this pass.
    pre = judge.precheck(
        row["url"], already_ingested=False,
        words=measured.get("words"), scrape_error=scrape_error,
        title=(measured.get("title") or row.get("title") or ""),
        category=list(row.get("category") or []),
        published_on=measured.get("published_on") or row.get("published_on"),
        source=row["source"],
    )
    if pre.skip_reason:
        store.record_precheck(conn, row["id"], pre, detail=scrape_error)
        return pre.status

    decision = judge.judge_candidate(
        title=(measured.get("title") or row.get("title") or row["url"]),
        source=row["source"],
        published_on=str(measured.get("published_on") or row.get("published_on") or ""),
        category=list(row.get("category") or []),
        words=measured["words"],
        links_out=measured["links_out"],
        found_via=notion_log.found_via_text(row) or row["source"],
        text=measured["text"],
        api_key=openrouter_key,
        rubric_path=rubric_path,
    )
    return store.record_decision(conn, row["id"], decision)


# ── the ingest half (overrides now; every `save` once AUTO_INGEST flips) ─────

def episode_id_for_url(conn, url: str) -> Optional[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM episodes WHERE url = %s", (url,))
        row = cur.fetchone()
    return row["id"] if row else None


def ingest_one(conn, row: dict, save_fn: Callable[[str, Optional[str]], bool], *,
               override_by: Optional[str] = None) -> bool:
    """Ingest one candidate through save_item.save_url and record what happened.

    SystemExit is caught alongside Exception because `save_url` raises it for
    operational refusals (no Firecrawl key, no local Obsidian folder for a PDF).
    Letting it escape would abort the run mid-list and strand every remaining row —
    the exact failure the old queue's ingest loop was written to avoid.
    """
    try:
        ok = bool(save_fn(row["url"], None))
        error = None if ok else "save_url reported failure"
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        ok, error = False, str(exc)[:300]
    if ok:
        store.mark_saved(conn, row["id"], episode_id_for_url(conn, row["url"]),
                         override_by=override_by)
    else:
        log.error("ingest FAILED %s: %s", row["url"], error)
        store.mark_failed(conn, row["id"], error or "unknown", override_by=override_by)
    return ok


def process_overrides(conn, token: str, db_id: str,
                      save_fn: Optional[Callable[[str, Optional[str]], bool]] = None,
                      ) -> tuple[int, int, list[str]]:
    """Ingest every row Kevin ticked "Pull anyway". Returns (ok, failed, unknown urls).

    A ticked URL with no `intake_candidates` row means a row was added to Notion by
    hand. It is reported rather than ingested: this path records provenance into Neon,
    and a save with nowhere to record it is a value nothing can trace later. It does
    NOT fail the run — an untickable row would then fail every run until Kevin noticed.
    """
    if save_fn is None:
        from pipeline.save_item import save_url  # late import: avoids a module cycle
        save_fn = save_url
    ticked = notion_log.override_rows(token, db_id)
    if not ticked:
        return 0, 0, []
    # The ticked rows carry their own page ids, which is exactly the adoption map
    # `_mirror` needs — without it a legacy row with no stored id gets a SECOND page.
    pages = {canonicalize_url(r["url"]): r["page_id"] for r in ticked}
    rows = store.get_by_urls(conn, list(pages))
    ok = failed = 0
    for url, row in rows.items():
        if ingest_one(conn, row, save_fn, override_by="kevin"):
            ok += 1
        else:
            failed += 1
        _mirror(conn, token, db_id, row["id"], pages)
    return ok, failed, [url for url in pages if url not in rows]


# ── the Notion mirror ───────────────────────────────────────────────────────

def _mirror(conn, token: str, db_id: str, candidate_id: int,
            known_pages: Optional[dict[str, str]] = None) -> bool:
    """Push one candidate's CURRENT Neon row to the log.

    Re-reads the row rather than reusing the dict this process started with: the log
    has to show what the database says, not what the code believes it wrote. A Notion
    failure is logged and counted, never raised — Neon is the source of truth and the
    mirror is allowed to lag by a run.
    """
    row = store.get_by_id(conn, candidate_id)
    if row is None:
        log.error("notion mirror: candidate %s vanished from %s", candidate_id, store.TABLE)
        return False
    try:
        page_id = notion_log.upsert_row(token, db_id, row, known_pages)
    except Exception as exc:  # noqa: BLE001
        log.error("notion mirror FAILED for %s: %s", row["url"], exc)
        return False
    # Unconditionally, even when the page id is unchanged: this is what marks the row
    # as caught up. Recording only on a NEW page id left every row whose content
    # changed after its first mirror (an override ingest, say) permanently behind, so
    # `needs_mirroring` re-pushed it on every run for the rest of its life.
    store.record_notion_page(conn, candidate_id, page_id)
    return True


# ── what gets said ──────────────────────────────────────────────────────────

def weekly_line(counts: dict, would_save: list[str], held: list[str], *,
                auto_ingest: bool, backlog: int = 0, unknown_overrides: int = 0,
                sources_label: str = "all") -> str:
    """The one line every run posts — a dry week included.

    The titles matter more than the counts: "would save 9" tells Kevin nothing he can
    act on, and nine names tell him whether the judge has his taste yet. Every number
    comes from `weekly_counts`, i.e. from the table, not from a tally kept in Python —
    a run that crashed halfway must not be able to report a number Neon disagrees with.
    """
    verb = "saved" if auto_ingest else "would save"
    mode = "" if auto_ingest else " _(shadow mode — nothing auto-ingests yet)_"
    parts = [f"judged {counts.get('judged', 0)}", f"{verb} {counts.get('would_save', 0)}"]

    skipped = counts.get("judge_skipped", 0) + counts.get("precheck_skipped", 0)
    reasons = counts.get("precheck_reasons") or {}
    detail = ", ".join(f"{n} {why}" for why, n in reasons.items())
    parts.append(f"skipped {skipped}" + (f" ({detail})" if detail else ""))

    if counts.get("disputed"):
        parts.append(f"{counts['disputed']} disputed")
    if counts.get("held"):
        names = ", ".join(held[:3])
        parts.append(f"held {counts['held']}" + (f" ({names})" if names else ""))
    if counts.get("failed"):
        parts.append(f"failed {counts['failed']}")
    if counts.get("overrides"):
        parts.append(f"{counts['overrides']} ingested by your override")
    if unknown_overrides:
        parts.append(f"{unknown_overrides} ticked row(s) not in {store.TABLE}")
    if backlog:
        parts.append(f"{backlog} waiting for the next run")

    line = f":inbox_tray: *list-maker intake*{mode} [{sources_label}]: " + " · ".join(parts)
    if would_save:
        shown = would_save[:5]
        total = counts.get("would_save", len(shown))
        more = f" (+{total - len(shown)} more)" if total > len(shown) else ""
        line += f"\n*{verb.capitalize()}:* " + " · ".join(shown) + more
    return line + f"\n<{notion_log.INTAKE_URL}|open the intake log>"


def print_dry_run(notes: list[str], candidates: list[Candidate], collapsed: int,
                  known: dict[str, dict], already: set[str], cap: int,
                  table_ok: bool, rubric_version: Optional[str]) -> None:
    """The plan, printed. This is how the run is verified against production before
    anything writes: real feeds, real Neon reads, no scrapes and no model calls."""
    print("\nINTAKE DRY RUN — no Neon writes, no Notion writes, no model calls, no per-post scrapes")
    print("  (discovery itself is live: the OpenAI feed is read and Anthropic's two index")
    print("   pages are scraped once each — two Firecrawl credits, not one per candidate)")
    print(f"  run at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    if not table_ok:
        print(f"  ! {store.TABLE} does not exist yet — every candidate reads as new.")
        print(f"    A real run stops here until {store.MIGRATION_PATH} has been run.")
    if rubric_version is None:
        print(f"  ! {judge.RUBRIC_PATH} is missing — a real run stops here.")
    else:
        print(f"  rubric version {rubric_version}")

    print("\n  discovery")
    for note in notes:
        print(f"    {note}")
    print(f"\n  {len(candidates)} distinct URL(s) after canonicalization "
          f"({collapsed} collapsed as the same post seen twice)")
    print(f"  already rows in {store.TABLE}: {len(known)}")

    # The same free_precheck the run uses, so the preview cannot claim a different
    # set of skips than the run will make.
    skipped: dict[str, list[Candidate]] = {}
    to_judge: list[Candidate] = []
    for cand in candidates:
        pre = free_precheck({"url": cand.url, "title": cand.title, "category": cand.category,
                             "published_on": cand.published_on, "source": cand.source},
                            already_ingested=cand.url in already)
        if pre.skip_reason:
            skipped.setdefault(f"{pre.skip_reason} ({pre.status})", []).append(cand)
        else:
            to_judge.append(cand)

    print("\n  pre-checks that need no scrape")
    if not skipped:
        print("    none fired — every candidate goes to the scrape")
    for reason in sorted(skipped):
        group = skipped[reason]
        print(f"    {reason}: {len(group)}")
        for cand in group[:5]:
            print(f"      - {(cand.title or cand.url)[:76]}")

    # Same order the real run judges in (store.needs_judging): newest post first, and
    # candidates with no date — podcast citations — last. Ordering the preview any
    # other way would show a different sixty than the cap will actually take.
    to_judge.sort(key=lambda c: (c.published_on is not None, c.published_on or date.min),
                  reverse=True)
    over = max(0, len(to_judge) - cap)
    print(f"\n  would scrape + judge: {min(len(to_judge), cap)}"
          + (f" ({over} wait for the next run)" if over else ""))
    for cand in to_judge[:10]:
        print(f"    - [{cand.source}] {(cand.title or cand.url)[:78]}")
    if len(to_judge) > 10:
        print(f"    … {len(to_judge) - 10} more")
    print("\n  thin and dead can only be known after the scrape, and a podcast-cited row\n  has no title or date until then, so its skips cannot be previewed either.\n")


# ── the run ─────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Curated intake: discover, judge, log")
    p.add_argument("--dry-run", action="store_true",
                   help="Discovery + the free pre-checks, printed. No writes, no model calls.")
    p.add_argument("--sources", choices=SOURCE_CHOICES, default="all",
                   help="Which sources to run (podcast-cited is daily-able on its own)")
    p.add_argument("--resolve-links", dest="resolve_links", action="store_true", default=None,
                   help="Resolve url-less report citations by search (default on, off in --dry-run)")
    p.add_argument("--no-resolve-links", dest="resolve_links", action="store_false",
                   help="Skip link resolution (it costs one Firecrawl search per mention)")
    p.add_argument("--overrides-only", action="store_true",
                   help="Skip discovery and judging; just ingest the rows ticked Pull anyway")
    p.add_argument("--ensure-log-schema", action="store_true",
                   help="Reshape the Notion database into the intake log, then exit (idempotent)")
    p.add_argument("--limit", type=int, default=MAX_CANDIDATES_PER_RUN,
                   help=f"Candidates to scrape + judge this run (default {MAX_CANDIDATES_PER_RUN})")
    p.add_argument("--lookback-days", type=int, default=DEFAULT_FEED_LOOKBACK_DAYS,
                   help="How far back to ask a feed we hold no history for")
    return p.parse_args(argv)


def require_secrets(args: argparse.Namespace) -> tuple[str, Optional[str], Optional[str]]:
    """Every secret this mode needs, checked up front.

    Thirty rows in is the wrong place to discover a missing key: the run dies with
    half the week judged and the Slack line can't say which half.
    """
    token = os.getenv("NOTION_TOKEN")
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    needed: list[tuple[str, Optional[str]]] = []
    if not args.dry_run:
        needed.append(("NOTION_TOKEN", token))
    # Every mode that POSTS needs a webhook, and an unset one is a silent no-op on the
    # happy path (common.post_slack logs and returns False by design) — so an
    # unattended run would judge the whole week before anything noticed nobody was
    # told. Demanded up front only when headless, mirroring common.ensure_spotify_token:
    # CI is where the Slack line IS the deliverable, while a terminal run is a human
    # watching the output who does not need a webhook to read it. Either way the
    # post-run return check below still fails the run if the post doesn't land.
    # --ensure-log-schema posts nothing, so it is exempt along with --dry-run.
    if not (args.dry_run or args.ensure_log_schema) and not sys.stdin.isatty():
        needed.append(("SLACK_WEBHOOK_URL", os.getenv("SLACK_WEBHOOK_URL")))
    # Only the mode that actually scrapes and judges needs the scrape and judge keys.
    # blogs.yml runs --ensure-log-schema as its own step with NOTION_TOKEN alone, and
    # --overrides-only ingests through save_item, which brings its own.
    if not (args.dry_run or args.overrides_only or args.ensure_log_schema):
        needed += [("FIRECRAWL_API_KEY", firecrawl_key), ("OPENROUTER_API_KEY", openrouter_key)]
    missing = [name for name, value in needed if not value]
    if missing:
        raise SystemExit(f"missing required secret(s): {', '.join(missing)}")
    return token or "", firecrawl_key, openrouter_key


def ensure_log_schema(token: str, *, dry_run: bool = False) -> list[str]:
    """Reshape the Notion database into the intake log. Idempotent; additive only.

    Called explicitly (by blogs.yml, or by hand) rather than from the run, so a weekly
    judging pass never restructures Kevin's database as a side effect.
    """
    changes = notion_log.ensure_schema(token, notion_log.INTAKE_DB_ID, dry_run=dry_run)
    prefix = "would change" if dry_run else "changed"
    print(f"intake log schema: {prefix} " + ("; ".join(changes) if changes else "nothing (current)"))
    return changes




def run(args: argparse.Namespace, conn, token: str, firecrawl_key: Optional[str],
        openrouter_key: Optional[str]) -> int:
    """One intake run. Returns the number of failures.

    Split from `main` so every path — overrides-only included — funnels through the
    same exit-code decision. An early `return` inside a try/finally is exactly how a
    run that failed ends up reporting success.
    """
    started = datetime.now(timezone.utc)
    failures = 0

    table_ok = store.table_exists(conn)
    if not table_ok and not args.dry_run:
        raise SystemExit(store.MISSING_TABLE_HINT)

    rubric_version: Optional[str] = None
    try:
        _, rubric_version = judge.load_rubric()
    except FileNotFoundError:
        if not args.dry_run and not args.overrides_only:
            raise SystemExit(
                f"the rubric is missing ({judge.RUBRIC_PATH}). It is what the judge reads "
                "and what prompt_version hashes; nothing can be judged without it."
            )

    if args.overrides_only:
        ok, failed, unknown = process_overrides(conn, token, notion_log.INTAKE_DB_ID)
        log.info("overrides: %d ingested, %d failed, %d not in %s",
                 ok, failed, len(unknown), store.TABLE)
        posted = post_slack(weekly_line(store.weekly_counts(conn, started), [], [],
                                        auto_ingest=AUTO_INGEST,
                                        unknown_overrides=len(unknown),
                                        sources_label="overrides only"))
        return failed + (0 if posted else 1)

    resolve_links = (not args.dry_run) if args.resolve_links is None else args.resolve_links
    found, notes = discover(
        conn, groups=args.sources, table_ok=table_ok, firecrawl_key=firecrawl_key,
        lookback_days=args.lookback_days, resolve_links=resolve_links, dry_run=args.dry_run,
    )
    candidates, collapsed = dedupe(found)
    for note in notes:
        log.info("discovery — %s", note)

    if args.dry_run:
        urls = [c.url for c in candidates]
        print_dry_run(
            notes, candidates, collapsed,
            store.get_by_urls(conn, urls) if table_ok else {},
            store.already_ingested_urls(conn, urls),
            args.limit, table_ok, rubric_version,
        )
        return 0

    new, existing = store.upsert_candidates(conn, candidates)
    log.info("upsert: %d new, %d already known (%d collapsed as duplicates in this run)",
             new, existing, collapsed)

    work = store.needs_judging(conn, rubric_version)
    backlog = max(0, len(work) - args.limit)
    if backlog:
        log.warning("judging %d of %d candidate(s); %d wait for the next run",
                    args.limit, len(work), backlog)
    work = work[:args.limit]

    ingested = store.already_ingested_urls(conn, [r["url"] for r in work])
    known_pages = notion_log.existing_page_ids(token, notion_log.INTAKE_DB_ID)

    # Catch the log up first. A row judged last week whose Notion write failed is
    # never revisited by the judging loop — its verdict is settled — so without this
    # one Notion outage would keep it off Kevin's surface permanently.
    in_work = {w["id"] for w in work}
    stale = [s for s in store.needs_mirroring(conn, limit=MAX_MIRROR_CATCHUP)
             if s["id"] not in in_work]
    if stale:
        log.info("mirroring %d row(s) the log is missing or behind on", len(stale))
    for row in stale:
        if not _mirror(conn, token, notion_log.INTAKE_DB_ID, row["id"], known_pages):
            failures += 1
    for row in work:
        try:
            status = process_candidate(
                conn, row, already_ingested=row["url"] in ingested,
                firecrawl_key=firecrawl_key, openrouter_key=openrouter_key,
                rubric_path=judge.RUBRIC_PATH,
            )
            if AUTO_INGEST and status == store.STATUS_JUDGED:
                from pipeline.save_item import save_url  # late import: avoids a module cycle

                ingest_one(conn, store.get_by_id(conn, row["id"]) or row, save_url)
        except SystemExit:
            raise  # an operational refusal (a missing key) stops the run, loudly
        except Exception as exc:  # noqa: BLE001 — isolate one bad post, keep the week
            failures += 1
            log.error("candidate FAILED %s: %s", row["url"], exc)
            store.mark_failed(conn, row["id"], str(exc))
        if not _mirror(conn, token, notion_log.INTAKE_DB_ID, row["id"], known_pages):
            failures += 1

    _, override_failed, unknown = process_overrides(conn, token, notion_log.INTAKE_DB_ID)
    if unknown:
        log.warning("%d ticked row(s) have no %s row: %s", len(unknown), store.TABLE,
                    ", ".join(unknown[:5]))
    failures += override_failed

    # The weekly line IS this job's deliverable — Neon and Notion are where the data
    # lives, but the Slack post is the only thing that reaches Kevin unprompted. A post
    # that didn't land is a week he never heard about, so it fails the run and
    # blogs.yml's notify fires. Same call the pulse already makes for its heartbeat.
    if not post_slack(weekly_line(
        store.weekly_counts(conn, started),
        store.titles(conn, started, store.STATUS_JUDGED),
        store.titles(conn, started, store.STATUS_HELD),
        auto_ingest=AUTO_INGEST, backlog=backlog, unknown_overrides=len(unknown),
        sources_label=args.sources,
    )):
        log.error("the weekly intake line could not be posted to Slack — "
                  "is SLACK_WEBHOOK_URL set and valid?")
        failures += 1
    return failures


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    load_environment()
    token, firecrawl_key, openrouter_key = require_secrets(args)

    if args.ensure_log_schema:
        ensure_log_schema(token, dry_run=args.dry_run)
        return

    conn = get_db_connection()
    try:
        failures = run(args, conn, token, firecrawl_key, openrouter_key)
    finally:
        conn.close()
    if failures:
        raise SystemExit(f"{failures} candidate(s) failed this run — see the log above")


if __name__ == "__main__":
    main()

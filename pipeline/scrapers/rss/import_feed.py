#!/usr/bin/env python3
"""Find each show's new episodes in its own RSS feed, and fetch their publisher transcripts.

This replaces the Taddy importer (scrapers/taddy/import_transcripts.py). Until the
cut-over (claude-plans/2026-11-23-off-taddy-plan.md, phase 3) nothing scheduled calls
it to write: the daily run reads it in shadow (pipeline/shadow_feeds.py), which uses
the same `plan_import` below and writes nothing.

One run, per show:
  1. Download the feed once (feeds.fetch_feed) and parse it (feeds.parse_feed).
  2. Load what Neon already holds for the show, once.
  3. Plan (plan_import, pure, no I/O): every feed item is either already held, new
     (to insert), held but still waiting for its transcript, or outside the window.
  4. Apply: insert the new rows; fetch and store transcripts for new and waiting rows.

Rows it already holds get NO write. The Taddy importer re-stamped scraped_at and
rewrote raw_content on every row it re-saw, every day (about 1,500 writes for nothing),
and that re-stamping is what emptied TAL's song queue in 2026
(scrapers/tal/fetch.get_episodes_missing_songs).

Rules that each cost this repo a bug before (the plan, section 2):
  - Dates are the UTC day (feeds.py, rule 1). TAL episodes 886 and 887 exist twice
    because a feed's local date missed Taddy's UTC date in the title+date match.
  - Match before inserting, in this order: stored url = the guid; the guid inside
    raw_content (rows written by Taddy, before the one-time id move); then the same
    show + title + UTC date; then the same title one day either side, which is logged
    because it means a date convention slipped somewhere.
  - scraped_at is set on insert, never on a row already held
    (sync_playlist.get_latest_episode reads it for the playlist description).
  - raw_content keeps the shape the entity shows' readers expect: `description` is the
    feed's HTML, because scrapers/ai_daily/sponsors.roster_from_raw_content parses the
    "Brought to you by:" block out of it.
  - A transcript is only ever ADDED (ON CONFLICT DO NOTHING): a row that already has
    one, from Taddy or anywhere else, is never overwritten, and its id (which
    ai_mentions.transcript_id points at) never changes.

PUBLIC REPO: logs carry titles, ids, dates and counts. Never transcript text.

Usage (from pipeline/):
    python scrapers/rss/import_feed.py --shows ai-daily-brief,pchh --dry-run
    python scrapers/rss/import_feed.py --shows machine-gods --backfill
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from feeds import FeedItem, ParsedFeed, episode_identity, fetch_feed, parse_feed  # noqa: E402
from scrapers.transcripts import reader_for  # noqa: E402
from scrapers.transcripts.base import Failed, NotYet, Transcript, normalize_title  # noqa: E402

# A feed item we do not hold is imported when it is at most this old. Older ones are
# counted, not imported: a show's back catalogue arriving because a feed was re-ordered
# or a host re-published old items would be a surprise, and --backfill is the door for
# doing it on purpose.
NEW_EPISODE_WINDOW_DAYS = 30
# A held episode still waiting for its transcript is asked about for this long. AI
# Daily's page has appeared within a day so far, and NPR's within three; past two weeks
# the extraction has long since fallen back to show notes (TRANSCRIPT_GRACE_DAYS = 7).
TRANSCRIPT_RETRY_DAYS = 14
# Between transcript page requests, to be a polite reader of someone else's site.
TRANSCRIPT_FETCH_PAUSE_SECONDS = 0.5
DETERMINISTIC_EXIT_CODE = 2  # run_new_episodes.DETERMINISTIC_EXIT_CODE: don't retry
TITLE_MAX_CHARS = 500


@dataclass(frozen=True)
class HeldEpisode:
    id: int
    url: Optional[str]
    raw_guid: Optional[str]
    title: str
    publish_date: Optional[date]
    has_transcript: bool


class HeldIndex:
    """What Neon holds for one show, indexed the four ways `match` looks."""

    def __init__(self, rows: Iterable[HeldEpisode]):
        self.rows = list(rows)
        self.by_url: dict[str, HeldEpisode] = {}
        self.by_guid: dict[str, HeldEpisode] = {}
        self.by_title_date: dict[tuple[str, date], HeldEpisode] = {}
        # Lowest id wins every tie, as upsert_episode's ORDER BY id did, so an existing
        # duplicate pair keeps resolving to the same row.
        for row in sorted(self.rows, key=lambda r: r.id):
            if row.url:
                self.by_url.setdefault(row.url, row)
            if row.raw_guid:
                self.by_guid.setdefault(row.raw_guid, row)
            if row.publish_date:
                self.by_title_date.setdefault((normalize_title(row.title or "Untitled Episode"), row.publish_date), row)

    def match(self, item: FeedItem, identity: str) -> tuple[Optional[HeldEpisode], Optional[str]]:
        """(the held row, how it matched) or (None, None)."""
        return self.match_keys(identity, item.guid, item.title, item.publish_date)

    def match_keys(
        self, identity: str, guid: Optional[str], title: Optional[str], day: Optional[date]
    ) -> tuple[Optional[HeldEpisode], Optional[str]]:
        """The match itself, on plain values, so the feed check (data_health) asks exactly
        the importer's question: if the importer would call an episode held, the check
        must not call it missing, or nothing could ever clear the alarm.

        A title match onto a row that already carries a DIFFERENT feed guid comes back as
        "guid_conflict": two items with one title (a daily "roundup", a re-upload under a
        new guid) must not share a row, and which one is right is a person's call.
        """
        if identity in self.by_url:
            return self.by_url[identity], "url"
        if guid and guid in self.by_guid:
            return self.by_guid[guid], "raw_guid"
        if day:
            key = normalize_title(title or "Untitled Episode")
            for shifted, how in ((day, "title_date"), (day - timedelta(days=1), "title_date_shifted"),
                                 (day + timedelta(days=1), "title_date_shifted")):
                row = self.by_title_date.get((key, shifted))
                if row is not None:
                    known = known_guid(row)
                    if guid and known and known != guid:
                        return row, "guid_conflict"
                    return row, how
        return None, None


def known_guid(row: HeldEpisode) -> Optional[str]:
    """The feed guid a held row is already tied to: the one Taddy saved, or its url when
    the url IS a guid (this importer's rows, Gabfest's, and every row after the id move).
    A web address is a legacy page url, not a guid."""
    if row.raw_guid:
        return row.raw_guid
    if row.url and not row.url.startswith(("http://", "https://")):
        return row.url
    return None


@dataclass(frozen=True)
class Action:
    """What the importer would do with one feed item.

    kind:
      insert      not held, inside the window: a new row (and a transcript, if the
                  show has a source)
      transcript  held, inside the retry window, still without a transcript
      held        held; nothing to do
      old         not held, older than the window (counted; --backfill imports it)
      undated     the item's pubDate does not parse; skipped and counted
      trailer     the show's trailer (<itunes:episodeType>trailer): an ad for the show,
                  never imported
      future      dated in the future (a scheduled pre-release); skipped until it isn't
      conflict    its title matches a held row tied to a different guid; nothing written,
                  reported for a person to settle
      duplicate   a second new item with the same title and day as one already planned
                  in this run (a re-upload under a new guid); not inserted, reported
    """

    kind: str
    item: FeedItem
    identity: str
    row: Optional[HeldEpisode] = None
    how: Optional[str] = None


def plan_import(
    items: Iterable[FeedItem],
    held: HeldIndex,
    *,
    now: datetime,
    wants_transcript: bool,
    title_prefix: Optional[str] = None,
    backfill: bool = False,
    import_since: Optional[date] = None,
    new_window_days: int = NEW_EPISODE_WINDOW_DAYS,
    transcript_retry_days: int = TRANSCRIPT_RETRY_DAYS,
) -> list[Action]:
    """Decide, without any I/O, what to do with every item of a show's feed.

    `import_since` (show_config) keeps a new show's first run from importing a month of
    back catalogue: items before it are "old" unless this is a --backfill.
    """
    today = now.astimezone(timezone.utc).date()
    actions: list[Action] = []
    seen: set[str] = set()
    planned_inserts: set[tuple[str, date]] = set()
    for item in items:
        if title_prefix and not item.title.startswith(title_prefix):
            continue
        identity = episode_identity(item)
        if identity in seen:
            # A feed that lists the same guid twice gets one row, not a conflict.
            continue
        seen.add(identity)
        if item.is_trailer:
            actions.append(Action("trailer", item, identity))
            continue
        if not item.published_at:
            row, how = held.match(item, identity)  # url and guid still work without a date
            actions.append(Action("held" if row is not None else "undated", item, identity, row, how))
            continue
        if item.published_at > now + timedelta(hours=1):
            actions.append(Action("future", item, identity))
            continue
        row, how = held.match(item, identity)
        day = item.publish_date
        if how == "guid_conflict":
            actions.append(Action("conflict", item, identity, row, how))
        elif row is not None:
            waiting = (
                wants_transcript
                and not row.has_transcript
                and day is not None
                # --backfill also repairs older rows whose transcript fetch once failed
                and (backfill or day >= today - timedelta(days=transcript_retry_days))
            )
            actions.append(Action("transcript" if waiting else "held", item, identity, row, how))
        elif backfill or (day >= today - timedelta(days=new_window_days) and (import_since is None or day >= import_since)):
            key = (normalize_title(item.title or "Untitled Episode"), day)
            if key in planned_inserts:
                actions.append(Action("duplicate", item, identity))
            else:
                planned_inserts.add(key)
                actions.append(Action("insert", item, identity))
        else:
            actions.append(Action("old", item, identity))
    return actions


# ── Neon ──────────────────────────────────────────────────────────────────────


def load_held(conn, show_id: int) -> HeldIndex:
    """Every row of the show, with the feed guid Taddy saved in raw_content where present.

    The guid is read server-side and only for raw_content that is JSON (Taddy, Gabfest
    and this importer write JSON; the music shows' scrapers write markdown there).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.url, e.title, e.publish_date,
                   CASE WHEN e.raw_content LIKE '{%%' THEN e.raw_content::jsonb ->> 'guid' END AS raw_guid,
                   EXISTS (SELECT 1 FROM episode_transcripts et WHERE et.episode_id = e.id) AS has_transcript
            FROM episodes e
            WHERE e.show_id = %s
            """,
            (show_id,),
        )
        rows = cur.fetchall()
    return HeldIndex(
        HeldEpisode(
            id=int(r["id"]),
            url=r["url"],
            raw_guid=r["raw_guid"],
            title=r["title"] or "",
            publish_date=r["publish_date"],
            has_transcript=bool(r["has_transcript"]),
        )
        for r in rows
    )


def raw_content_for(item: FeedItem, feed_url: str, imported_at: datetime) -> str:
    return json.dumps(
        {
            "provider": "rss",
            "feed_url": feed_url,
            "imported_at": imported_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "guid": item.guid,
            "name": item.title,
            # The feed's HTML, as Taddy gave it: sponsors.roster_from_raw_content reads
            # the "Brought to you by:" block out of it.
            "description": item.description_html,
            "link": item.link,
            "enclosure_url": item.enclosure_url,
            "duration_seconds": item.duration_seconds,
        }
    )


def insert_episode(conn, *, show_id: int, item: FeedItem, identity: str, raw_content: Optional[str]) -> Optional[int]:
    """Insert one new row; its id, or None when another row already holds that url.

    ON CONFLICT DO NOTHING rather than DO UPDATE: `match` already looked for this url in
    the show, so a conflict means a DIFFERENT show holds it. Overwriting that row would
    move an episode between shows; refusing and saying so is the safe side.
    """
    title = (item.title or "").strip()[:TITLE_MAX_CHARS] or "Untitled Episode"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episodes (
              show_id, title, url, publish_date, description_body, episode_number,
              audio_url, image_url, raw_content, scraped_at, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (url) DO NOTHING
            RETURNING id;
            """,
            (
                show_id,
                title,
                identity,
                item.publish_date,
                item.description_html or None,
                item.episode_number,
                item.enclosure_url,
                item.image_url,
                raw_content,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["id"]) if row else None


def add_transcript(conn, episode_id: int, transcript: Transcript) -> bool:
    """Store a transcript for an episode that has none. False if one appeared meanwhile."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO episode_transcripts (
              episode_id, source_type, source_url, transcript_text, is_generated, model, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (episode_id) DO NOTHING
            RETURNING id;
            """,
            (
                episode_id,
                transcript.source_type,
                transcript.source_url,
                transcript.text,
                transcript.is_generated,
                transcript.model,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return row is not None


# ── One show ──────────────────────────────────────────────────────────────────


@dataclass
class ShowReport:
    slug: str
    feed_items: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    matched_by: dict[str, int] = field(default_factory=dict)
    inserted: list[str] = field(default_factory=list)
    transcripts_added: int = 0
    transcripts_pending: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # anything a human should look at
    notes: list[str] = field(default_factory=list)


def _label(item: FeedItem) -> str:
    return f"{item.publish_date} {item.title[:70]!r}"


def run_show(
    cfg,
    conn,
    *,
    now: datetime,
    dry_run: bool,
    backfill: bool,
    limit: Optional[int] = None,
    session=None,
    pause_seconds: float = TRANSCRIPT_FETCH_PAUSE_SECONDS,
) -> ShowReport:
    report = ShowReport(cfg.slug)
    fetched = fetch_feed(cfg.feed_url, session=session)
    if fetched.moved:
        report.notes.append(f"feed redirected: {cfg.feed_url} -> {fetched.final_url} (update show_config.feed_url)")
    feed: ParsedFeed = parse_feed(fetched.content)
    if feed.new_feed_url and feed.new_feed_url.rstrip("/") not in (
        cfg.feed_url.rstrip("/"),
        fetched.final_url.rstrip("/"),
    ):
        report.notes.append(f"feed says it moved to {feed.new_feed_url} (itunes:new-feed-url)")
    held = load_held(conn, cfg.show_id)
    actions = plan_import(
        feed.items,
        held,
        now=now,
        wants_transcript=bool(cfg.transcript_source),
        title_prefix=cfg.feed_title_prefix,
        backfill=backfill,
        import_since=getattr(cfg, "import_since", None),
    )
    report.feed_items = len(actions)
    for a in actions:
        report.counts[a.kind] = report.counts.get(a.kind, 0) + 1
        if a.how:
            report.matched_by[a.how] = report.matched_by.get(a.how, 0) + 1
        if a.how == "title_date_shifted":
            report.notes.append(
                f"matched across a one-day date shift (row {a.row.id}, held {a.row.publish_date}, feed {a.item.publish_date}): {a.item.title[:70]!r}"
            )
        elif a.kind == "conflict":
            report.problems.append(
                f"title matches row {a.row.id}, which is tied to another guid ({known_guid(a.row)!r}, feed has {a.item.guid!r}); "
                f"nothing written: {_label(a.item)}"
            )
        elif a.kind == "duplicate":
            report.problems.append(f"two new feed items share a title and day; only the first was planned: {_label(a.item)}")
        elif a.kind == "undated":
            report.problems.append(f"feed item with an unreadable date {a.item.pubdate_raw!r}: {a.item.title[:70]!r}")
    if not any(a.item.publish_date for a in actions):
        # "Empty means unverified" (feed_check's rule): a feed that parses but lists
        # nothing usable is a broken feed, never a quiet day.
        report.problems.append(f"the feed lists no dated items ({len(feed.items)} raw items); treating it as unreadable")

    todo = [a for a in actions if a.kind in ("insert", "transcript")]
    # Oldest first, so a --limit'ed backfill fills the gap from its start.
    todo.sort(key=lambda a: a.item.published_at)
    if limit is not None:
        todo = todo[:limit]
    reader = reader_for(cfg.transcript_source) if cfg.transcript_source else None
    rate_limited = False

    for a in todo:
        if dry_run:
            print(f"  [dry-run] would {a.kind}: {_label(a.item)}")
            continue
        episode_id = a.row.id if a.row else None
        if a.kind == "insert":
            raw = raw_content_for(a.item, cfg.feed_url, now) if cfg.store_raw_content else None
            episode_id = insert_episode(conn, show_id=cfg.show_id, item=a.item, identity=a.identity, raw_content=raw)
            if episode_id is None:
                report.problems.append(f"another show already holds this episode's id {a.identity!r}: {_label(a.item)}")
                continue
            report.inserted.append(_label(a.item))
            print(f"  + episode {episode_id}: {_label(a.item)}")
        if reader is None or rate_limited:
            continue
        result = reader.fetch(a.item, session=session)
        if pause_seconds:
            time.sleep(pause_seconds)
        if isinstance(result, Transcript):
            if add_transcript(conn, episode_id, result):
                report.transcripts_added += 1
                print(f"  + transcript for episode {episode_id} ({result.words} words, {result.model}): {_label(a.item)}")
        elif isinstance(result, NotYet):
            report.transcripts_pending.append(f"{_label(a.item)}: {result.reason}")
            if result.rate_limited:
                rate_limited = True
                report.notes.append(f"{cfg.transcript_source} asked us to slow down (HTTP 429); no more transcript requests this run")
        elif isinstance(result, Failed):
            report.problems.append(f"transcript source cannot read {_label(a.item)}: {result.reason}")
    return report


def print_report(r: ShowReport) -> None:
    counts = ", ".join(f"{k}={v}" for k, v in sorted(r.counts.items()))
    print(f"[{r.slug}] feed items={r.feed_items} ({counts}); inserted={len(r.inserted)}, "
          f"transcripts added={r.transcripts_added}, waiting={len(r.transcripts_pending)}")
    for line in r.transcripts_pending:
        print(f"  ~ transcript not yet: {line}")
    for line in r.notes:
        print(f"  ! {line}")
    for line in r.problems:
        print(f"  PROBLEM: {line}")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import new episodes from each show's RSS feed")
    p.add_argument("--shows", required=True, help="Comma-separated show slugs (show_config)")
    p.add_argument("--dry-run", action="store_true", help="Plan only: read the feed and Neon, write nothing")
    p.add_argument("--backfill", action="store_true",
                   help=f"Import every feed item not held, not just the last {NEW_EPISODE_WINDOW_DAYS} days")
    p.add_argument("--limit", type=int, default=None, help="At most this many inserts + transcript fetches per show")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    from common import get_db_connection, get_logger, load_environment
    from show_config import SHOWS

    args = parse_args(argv)
    load_environment()
    log = get_logger("import_feed")
    slugs = [s.strip() for s in args.shows.split(",") if s.strip()]
    unknown = [s for s in slugs if s not in SHOWS]
    no_feed = [s for s in slugs if s in SHOWS and not SHOWS[s].feed_url]
    if unknown or no_feed or not slugs:
        print(f"Unknown show(s) {unknown}, or no feed_url for {no_feed}. Known: {sorted(SHOWS)}", file=sys.stderr)
        sys.exit(DETERMINISTIC_EXIT_CODE)

    now = datetime.now(timezone.utc)
    conn = get_db_connection()
    failed_shows: list[str] = []
    try:
        for slug in slugs:
            cfg = SHOWS[slug]
            try:
                report = run_show(cfg, conn, now=now, dry_run=args.dry_run, backfill=args.backfill, limit=args.limit)
            except Exception as exc:  # one show's broken feed must not stop the others
                conn.rollback()
                # First line only, capped: a database error can echo the values it was
                # given, and this log is public.
                brief = (str(exc).splitlines() or [""])[0][:200]
                log.error("show=%s import failed: %s: %s", slug, exc.__class__.__name__, brief)
                print(f"[{slug}] FAILED: {exc.__class__.__name__}: {brief}")
                failed_shows.append(slug)
                continue
            print_report(report)
            if report.problems:
                failed_shows.append(slug)
    finally:
        conn.close()
    if failed_shows:
        print(f"Import problems for: {', '.join(failed_shows)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

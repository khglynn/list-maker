#!/usr/bin/env python3
"""Shadow run of the new feed path beside Taddy: compare, write nothing.

Phase 2 of the move off Taddy (claude-plans/2026-11-23-off-taddy-plan.md). The daily
entities run calls this right after the Taddy import, so Neon holds what Taddy found
today and this can ask, per show:

  1. Discovery. Run the new importer's own plan (scrapers/rss/import_feed.plan_import)
     against what Neon holds. Anything it would INSERT is an episode Taddy did not
     import; anything Taddy imported in the last two weeks that the feed lacks is the
     other direction. Matched rows whose stored date differs from the feed's UTC date
     are counted too, because that is the duplicate-row trap.
  2. Transcripts. For the last week's episodes, fetch the publisher's transcript with
     the reader the cut-over will use. Report found / not yet, hours since publishing,
     words per minute and the title match. Where Taddy's text for the same episode is
     in Neon, compare the two: word overlap, and the share of the entities already
     extracted from that episode whose names appear in each text. That share is what
     matters for extraction, and it is deterministic and free (no model call).
  3. Machine Gods, which has no rows yet: how many episodes its feed lists and whether
     each one's transcript can be read.

Writes NOTHING. The database session is set read-only before the first query, so a
bug here cannot write even by accident. Output is counts, ids, titles, ratios and
timings only (the repo and its logs are public; transcript text never leaves memory):
a Markdown table to $GITHUB_STEP_SUMMARY when set, and one `SHADOW_RESULT {json}`
line on stdout for collecting the run-by-run numbers.

A failure here must never fail the daily run while Taddy is still the writer: the
workflow step is continue-on-error, and this script reports a show it could not check
rather than raising.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scrapers" / "rss"))
from thefuzz import fuzz  # noqa: E402

from feeds import FeedItem, fetch_feed, parse_feed  # noqa: E402
from import_feed import HeldIndex, load_held, plan_import  # noqa: E402
from scrapers.transcripts import reader_for  # noqa: E402
from scrapers.transcripts.base import Transcript, words_per_minute  # noqa: E402
from show_config import SHOWS, ShowConfig  # noqa: E402

# Shows the shadow compares. Hard Fork is out: it stops being collected at the cut-over
# (Kevin, 2026-11-23: "hard fork has basically become machine gods"). Gabfest is out:
# it ended, and its importer is not changing.
SHADOW_SLUGS = ("ai-daily-brief", "pchh", "tal", "sop")

# Machine Gods joins at the cut-over, when it gets its `shows` row and a real show_id.
# Until then it is shadow-only and lives here, so SHOWS (which data_health checks
# against Neon) does not name a show Neon doesn't have yet.
MACHINE_GODS = ShowConfig(
    slug="machine-gods",
    name="Machine Gods",
    show_id=0,  # no row yet; the shadow treats it as holding nothing
    content_types=["entities"],
    feed_url="https://feeds.npr.org/g-s1-143588/podcast.xml",
    itunes_id=6812430636,
    transcript_source="npr",
    require_transcript=True,
    store_raw_content=True,
)

TRANSCRIPT_LOOKBACK_DAYS = 7
MAX_TRANSCRIPT_FETCHES_PER_SHOW = 12
REVERSE_LOOKBACK_DAYS = 14
FETCH_PAUSE_SECONDS = 0.5
FUZZY_NAME_MATCH = 90
_AD_RE = re.compile(r"brought to you by|sponsored by|today'?s sponsor|this episode is sponsored")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'’.+-]*[a-z0-9+]|[a-z0-9]")


@dataclass
class TranscriptCheck:
    episode_id: Optional[int]
    publish_date: str
    title: str
    status: str  # found | not_yet | failed
    hours_since_publish: float
    reason: str = ""
    words: Optional[int] = None
    wpm: Optional[float] = None
    source_url: Optional[str] = None
    taddy_words: Optional[int] = None
    word_overlap: Optional[float] = None  # share of Taddy's distinct words found in the new text
    entities: Optional[int] = None  # entities already extracted from this episode
    entities_in_taddy: Optional[int] = None  # of those, named in Taddy's text
    recall_exact: Optional[float] = None  # share of those the new text names exactly
    recall_fuzzy: Optional[float] = None  # ...or with a near spelling
    ad_phrases_taddy: Optional[int] = None
    ad_phrases_new: Optional[int] = None
    seconds: Optional[float] = None


@dataclass
class ShowShadow:
    slug: str
    ok: bool = True
    error: str = ""
    feed_items: int = 0
    counts: dict = field(default_factory=dict)
    matched_by: dict = field(default_factory=dict)
    would_insert: list = field(default_factory=list)
    date_mismatches: list = field(default_factory=list)
    held_not_in_feed: list = field(default_factory=list)
    feed_latest: Optional[str] = None
    held_latest: Optional[str] = None
    transcripts: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def word_overlap(reference: str, candidate: str) -> Optional[float]:
    ref = set(_words(reference))
    if not ref:
        return None
    return len(ref & set(_words(candidate))) / len(ref)


def _contains(text_cf: str, phrase: str) -> bool:
    phrase = phrase.casefold().strip()
    if len(phrase) < 2:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text_cf) is not None


def _fuzzy_contains(text_cf: str, phrase: str) -> bool:
    """A near-spelling of the phrase in the text ("Google Anti-Gravity" for
    "Google Antigravity"). Only for names long enough that a near miss means something."""
    phrase = phrase.casefold().strip()
    if len(phrase) < 6:
        return False
    return fuzz.partial_ratio(phrase, text_cf) >= FUZZY_NAME_MATCH


def entity_agreement(surface_forms: list[set[str]], reference: str, candidate: str) -> dict:
    """How many of an episode's extracted entities the new text still names.

    The denominator is the entities whose names appear in the REFERENCE (Taddy's) text:
    the extractor often normalises a name ("KPMG AI Amplifiers Study" for what was said),
    and such an entity is in neither text, so counting it would measure the extractor,
    not the transcript. Exact is a whole-word match; fuzzy also accepts a near spelling,
    which is mostly the publisher spelling a name right that speech recognition got
    wrong ("Being Heumann", not "Being Human"; checked 2026-11-24).
    """
    ref_cf, cand_cf = reference.casefold(), candidate.casefold()
    in_ref = [forms for forms in surface_forms if any(_contains(ref_cf, f) for f in forms)]
    exact = [forms for forms in in_ref if any(_contains(cand_cf, f) for f in forms)]
    fuzzy = [forms for forms in in_ref if forms in exact or any(_fuzzy_contains(cand_cf, f) for f in forms)]
    n = len(in_ref)
    return {
        "entities": len(surface_forms),
        "entities_in_taddy": n,
        "recall_exact": round(len(exact) / n, 3) if n else None,
        "recall_fuzzy": round(len(fuzzy) / n, 3) if n else None,
    }


def ad_phrases(text: str) -> int:
    """Sponsor-read markers. Speech-recognised transcripts carry the ads; a publisher's
    own transcript may not, which changes what the sponsor detector can see."""
    return len(_AD_RE.findall(text.casefold()))


def _entity_forms(conn, episode_id: int) -> list[set[str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.entity_id,
                   array_agg(DISTINCT m.mention_text) FILTER (WHERE m.mention_text IS NOT NULL) AS mention_texts,
                   max(e.canonical_name) AS canonical_name,
                   (array_agg(e.aliases))[1] AS aliases
            FROM ai_mentions m
            JOIN ai_entities e ON e.id = m.entity_id
            WHERE m.episode_id = %s AND m.transcript_id IS NOT NULL
            GROUP BY m.entity_id
            """,
            (episode_id,),
        )
        rows = cur.fetchall()
    forms: list[set[str]] = []
    for r in rows:
        names = {n for n in (r["mention_texts"] or []) if n}
        if r["canonical_name"]:
            names.add(r["canonical_name"])
        aliases = r["aliases"]
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except ValueError:
                aliases = []
        names.update(a for a in (aliases or []) if isinstance(a, str))
        if names:
            forms.append(names)
    return forms


def _taddy_text(conn, episode_id: int) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT transcript_text FROM episode_transcripts WHERE episode_id = %s AND source_type = 'taddy_transcript'",
            (episode_id,),
        )
        row = cur.fetchone()
    return row["transcript_text"] if row else None


def check_transcript(conn, cfg: ShowConfig, item: FeedItem, episode_id: Optional[int], now: datetime, session=None) -> TranscriptCheck:
    reader = reader_for(cfg.transcript_source)
    hours = round((now - item.published_at).total_seconds() / 3600, 1)
    started = time.monotonic()
    result = reader.fetch(item, session=session)
    elapsed = round(time.monotonic() - started, 2)
    check = TranscriptCheck(
        episode_id=episode_id,
        publish_date=str(item.publish_date),
        title=item.title[:80],
        status="found" if isinstance(result, Transcript) else type(result).__name__.lower().replace("notyet", "not_yet"),
        hours_since_publish=hours,
        seconds=elapsed,
    )
    if not isinstance(result, Transcript):
        check.reason = result.reason[:200]
        return check
    check.words = result.words
    wpm = words_per_minute(result.words, item.duration_seconds)
    check.wpm = round(wpm) if wpm else None
    check.source_url = result.source_url  # for AI Daily, the date page whose title matched
    if episode_id and conn is not None:
        taddy = _taddy_text(conn, episode_id)
        if taddy:
            check.taddy_words = len(taddy.split())
            overlap = word_overlap(taddy, result.text)
            check.word_overlap = round(overlap, 3) if overlap is not None else None
            check.ad_phrases_taddy, check.ad_phrases_new = ad_phrases(taddy), ad_phrases(result.text)
            forms = _entity_forms(conn, episode_id)
            if forms:
                for key, value in entity_agreement(forms, taddy, result.text).items():
                    setattr(check, key, value)
    return check


def shadow_show(conn, cfg: ShowConfig, now: datetime, session=None, pause: float = FETCH_PAUSE_SECONDS) -> ShowShadow:
    out = ShowShadow(cfg.slug)
    try:
        fetched = fetch_feed(cfg.feed_url, session=session)
        if fetched.moved:
            out.notes.append(f"feed redirected to {fetched.final_url}")
        feed = parse_feed(fetched.content)
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        out.ok, out.error = False, f"feed: {exc.__class__.__name__}: {exc}"[:300]
        return out

    held = load_held(conn, cfg.show_id) if cfg.show_id else HeldIndex([])
    actions = plan_import(
        feed.items, held, now=now, wants_transcript=bool(cfg.transcript_source), title_prefix=cfg.feed_title_prefix
    )
    out.feed_items = len(actions)
    dated = [a for a in actions if a.item.publish_date and a.kind != "future"]
    if dated:
        out.feed_latest = str(max(a.item.publish_date for a in dated))
    held_dates = [r.publish_date for r in held.rows if r.publish_date]
    if held_dates:
        out.held_latest = str(max(held_dates))
    for a in actions:
        out.counts[a.kind] = out.counts.get(a.kind, 0) + 1
        if a.how:
            out.matched_by[a.how] = out.matched_by.get(a.how, 0) + 1
        if a.kind == "insert" and cfg.show_id:
            age_h = round((now - a.item.published_at).total_seconds() / 3600, 1)
            out.would_insert.append(f"{a.item.publish_date} ({age_h}h old) {a.item.title[:70]!r}")
        if a.row is not None and a.row.publish_date and a.row.publish_date != a.item.publish_date:
            out.date_mismatches.append(
                f"row {a.row.id}: held {a.row.publish_date}, feed {a.item.publish_date} ({a.how}) {a.item.title[:60]!r}"
            )

    # The other direction: recent rows Taddy wrote (they carry its guid) that the feed
    # does not list. Hard Fork's feed is known to drop episodes; any show doing it would
    # mean the feed alone would have missed them.
    feed_guids = {a.item.guid for a in actions if a.item.guid}
    feed_ids = {a.identity for a in actions}
    since = (now - timedelta(days=REVERSE_LOOKBACK_DAYS)).date()
    for r in held.rows:
        if r.publish_date and r.publish_date >= since and r.raw_guid:
            if r.raw_guid not in feed_guids and r.url not in feed_ids:
                out.held_not_in_feed.append(f"row {r.id} {r.publish_date} {r.title[:60]!r}")

    if cfg.transcript_source:
        lookback = (now - timedelta(days=TRANSCRIPT_LOOKBACK_DAYS)).date()
        recent = [a for a in actions if a.item.publish_date and a.kind != "future"
                  and (a.item.publish_date >= lookback or not cfg.show_id)]
        recent.sort(key=lambda a: a.item.published_at, reverse=True)
        for a in recent[:MAX_TRANSCRIPT_FETCHES_PER_SHOW]:
            try:
                out.transcripts.append(asdict(check_transcript(conn, cfg, a.item, a.row.id if a.row else None, now, session)))
            except Exception as exc:  # noqa: BLE001
                out.transcripts.append({"publish_date": str(a.item.publish_date), "title": a.item.title[:80],
                                        "status": "error", "reason": f"{exc.__class__.__name__}: {exc}"[:200]})
            if pause:
                time.sleep(pause)
    return out


def _pct(x: Optional[float]) -> str:
    return "" if x is None else f"{x * 100:.0f}%"


def render_markdown(results: list[ShowShadow], now: datetime) -> str:
    lines = [f"## Shadow: the feed path beside Taddy ({now:%Y-%m-%d %H:%M} UTC)", "",
             "Read-only. Discovery is the new importer's plan against what Neon holds after today's Taddy import.", "",
             "| Show | Feed items | Held (how) | Would insert | Held, not in feed | Date mismatches | Feed latest / held latest |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.ok:
            lines.append(f"| {r.slug} | could not check: {r.error} | | | | | |")
            continue
        how = ", ".join(f"{k} {v}" for k, v in sorted(r.matched_by.items()))
        lines.append(f"| {r.slug} | {r.feed_items} | {r.counts.get('held', 0) + r.counts.get('transcript', 0)} ({how}) | "
                     f"{len(r.would_insert)} | {len(r.held_not_in_feed)} | {len(r.date_mismatches)} | {r.feed_latest} / {r.held_latest} |")
    for r in results:
        for label, rows in (("would insert", r.would_insert), ("held, not in feed", r.held_not_in_feed),
                            ("date mismatch", r.date_mismatches), ("note", r.notes)):
            for row in rows[:10]:
                lines.append(f"- {r.slug} {label}: {row}")
    lines += ["", "| Show | Date | Status | Hours since | Words | WPM | Taddy words | Word overlap | Entities (in Taddy's text) | Still named: exact / near spelling | Ad markers Taddy / new | Title |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        for t in r.transcripts:
            lines.append(
                f"| {r.slug} | {t.get('publish_date')} | {t.get('status')} | {t.get('hours_since_publish', '')} | "
                f"{t.get('words') or ''} | {t.get('wpm') or ''} | {t.get('taddy_words') or ''} | {_pct(t.get('word_overlap'))} | "
                f"{t.get('entities') or ''} ({t.get('entities_in_taddy') or ''}) | {_pct(t.get('recall_exact'))} / {_pct(t.get('recall_fuzzy'))} | "
                f"{'' if t.get('ad_phrases_taddy') is None else t.get('ad_phrases_taddy')} / {'' if t.get('ad_phrases_new') is None else t.get('ad_phrases_new')} | {str(t.get('title'))[:50]} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    from common import get_db_connection, load_environment

    load_environment()
    now = datetime.now(timezone.utc)
    conn = get_db_connection()
    results: list[ShowShadow] = []
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.commit()
        for cfg in [SHOWS[s] for s in SHADOW_SLUGS] + [MACHINE_GODS]:
            try:
                results.append(shadow_show(conn, cfg, now))
            except Exception as exc:  # noqa: BLE001 — one show never stops the rest
                conn.rollback()
                results.append(ShowShadow(cfg.slug, ok=False, error=f"{exc.__class__.__name__}: {exc}"[:300]))
    finally:
        conn.close()

    markdown = render_markdown(results, now)
    print(markdown)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown)
    print("SHADOW_RESULT " + json.dumps({"at": now.isoformat(timespec="seconds"), "shows": [asdict(r) for r in results]},
                                        ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

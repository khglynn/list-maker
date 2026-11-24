"""The feed importer that replaces Taddy (pipeline/scrapers/rss/import_feed.py).

Hermetic: a fake connection records every statement, a fake session serves made-up
feeds and pages. What these pin is each rule in the importer's docstring, because
each one is a bug this repo already had once.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "scrapers" / "rss"))
import import_feed as imp  # noqa: E402
from feeds import FeedItem  # noqa: E402

NOW = datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc)


def _item(title, guid, days_ago=0.0, hour=12, link=None, duration=600, description="<p>Notes</p>") -> FeedItem:
    published = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0)
    return FeedItem(title=title, guid=guid, link=link, enclosure_url=f"https://cdn.example/{guid}.mp3",
                    pubdate_raw=None, published_at=published, description_html=description,
                    duration_seconds=duration, episode_number=None, image_url=None)


def _held(id, title, day, url=None, raw_guid=None, has_transcript=True):
    return imp.HeldEpisode(id=id, url=url, raw_guid=raw_guid, title=title, publish_date=day, has_transcript=has_transcript)


# ── Matching ──────────────────────────────────────────────────────────────────


def test_match_order_is_url_then_saved_guid_then_title_and_utc_day() -> None:
    item = _item("Ep One", "g-1", days_ago=1)
    day = item.publish_date
    by_url = _held(1, "Other", date(2020, 1, 1), url="g-1")
    by_guid = _held(2, "Other", date(2020, 1, 1), url="https://api.taddy.org/podcast-episode/x", raw_guid="g-1")
    by_title = _held(3, "ep one", day, url="https://old.example/ep-one")
    assert imp.HeldIndex([by_title, by_guid, by_url]).match(item, "g-1") == (by_url, "url")
    assert imp.HeldIndex([by_title, by_guid]).match(item, "g-1") == (by_guid, "raw_guid")
    assert imp.HeldIndex([by_title]).match(item, "g-1") == (by_title, "title_date")


def test_a_title_one_day_off_still_matches_and_says_how() -> None:
    """The guard on the trap that doubled TAL 886 and 887: a row held under the other
    convention's date must not become a second row."""
    item = _item("902: The Missing Minutes", "g-902", days_ago=1)
    held = _held(9, "902: The Missing Minutes", item.publish_date - timedelta(days=1), url="https://www.thisamericanlife.org/902")
    assert imp.HeldIndex([held]).match(item, "g-902") == (held, "title_date_shifted")


def test_titles_match_through_curly_quotes_and_case() -> None:
    item = _item("Jensen Huang’s Fall", "g", days_ago=0)
    held = _held(4, "jensen huang's fall", item.publish_date, url="https://old.example/x")
    assert imp.HeldIndex([held]).match(item, "g")[1] == "title_date"


def test_a_different_title_on_the_same_day_is_a_different_episode() -> None:
    item = _item("Main Episode", "g-main")
    promo = _held(5, "Promo: Listen to Our Other Show", item.publish_date, url="g-promo")
    assert imp.HeldIndex([promo]).match(item, "g-main") == (None, None)


def test_ties_resolve_to_the_lowest_id_like_upsert_episode_did() -> None:
    item = _item("Dup", "g")
    a, b = _held(20, "Dup", item.publish_date, url="u20"), _held(10, "Dup", item.publish_date, url="u10")
    assert imp.HeldIndex([a, b]).match(item, "g")[0].id == 10


# ── Planning ──────────────────────────────────────────────────────────────────


def _kinds(actions):
    return {a.item.title: a.kind for a in actions}


def test_plan_sorts_every_item_into_exactly_one_kind() -> None:
    items = [
        _item("New", "g-new", days_ago=1),
        _item("Held", "g-held", days_ago=2),
        _item("Waiting", "g-wait", days_ago=3),
        _item("Waited too long", "g-long", days_ago=20),
        _item("Ancient", "g-old", days_ago=400),
        FeedItem("Undated", "g-undated", None, None, "garbage", None, ""),
        _item("Scheduled", "g-future", days_ago=-3),
    ]
    held = imp.HeldIndex([
        _held(1, "x", date(2026, 1, 1), url="g-held"),
        _held(2, "x", date(2026, 1, 1), url="g-wait", has_transcript=False),
        _held(3, "x", date(2026, 1, 1), url="g-long", has_transcript=False),
    ])
    kinds = _kinds(imp.plan_import(items, held, now=NOW, wants_transcript=True))
    assert kinds == {
        "New": "insert", "Held": "held", "Waiting": "transcript", "Waited too long": "held",
        "Ancient": "old", "Undated": "undated", "Scheduled": "future",
    }


def test_backfill_imports_the_back_catalogue_on_purpose() -> None:
    kinds = _kinds(imp.plan_import([_item("Ancient", "g", days_ago=400)], imp.HeldIndex([]), now=NOW,
                                   wants_transcript=False, backfill=True))
    assert kinds == {"Ancient": "insert"}


def test_a_show_without_a_transcript_source_never_waits_for_one() -> None:
    held = imp.HeldIndex([_held(1, "x", date(2026, 1, 1), url="g", has_transcript=False)])
    assert _kinds(imp.plan_import([_item("TAL ep", "g")], held, now=NOW, wants_transcript=False)) == {"TAL ep": "held"}


def test_a_mixed_feed_is_filtered_to_its_show_and_a_repeated_guid_is_one_episode() -> None:
    items = [_item("Culture Gabfest - A", "g-a"), _item("ICYMI - B", "g-b"), _item("Culture Gabfest - A", "g-a")]
    actions = imp.plan_import(items, imp.HeldIndex([]), now=NOW, wants_transcript=False, title_prefix="Culture Gabfest")
    assert [a.item.title for a in actions] == ["Culture Gabfest - A"]


# ── Applying (a fake Neon) ────────────────────────────────────────────────────


class FakeCursor:
    def __init__(self, conn):
        self.conn, self._result = conn, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.statements.append((" ".join(sql.split()), params))
        if "FROM episodes e" in sql and "raw_guid" in sql:
            self._result = self.conn.held_rows
        elif sql.lstrip().startswith("INSERT INTO episodes"):
            url = params[2]
            if url in self.conn.taken_urls:
                self._result = []
            else:
                self.conn.next_id += 1
                self.conn.taken_urls.add(url)
                self._result = [{"id": self.conn.next_id}]
        elif sql.lstrip().startswith("INSERT INTO episode_transcripts"):
            eid = params[0]
            if eid in self.conn.has_transcript:
                self._result = []
            else:
                self.conn.has_transcript.add(eid)
                self._result = [{"id": 900 + eid}]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConn:
    def __init__(self, held_rows=(), taken_urls=(), has_transcript=()):
        self.held_rows, self.taken_urls = list(held_rows), set(taken_urls)
        self.has_transcript, self.statements, self.next_id, self.commits = set(has_transcript), [], 100, 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def writes(self):
        return [s for s, _ in self.statements if s.startswith("INSERT") or s.startswith("UPDATE")]


def _feed_xml(items: list[tuple[str, str, str, str]]) -> bytes:
    """(title, guid, rfc822 date, link) -> a tiny RSS document."""
    body = "".join(
        f"<item><title>{t}</title><guid>{g}</guid><pubDate>{d}</pubDate><link>{l}</link>"
        f"<enclosure url='https://cdn.example/{g}.mp3'/><itunes:duration>600</itunes:duration>"
        f"<description>&lt;p&gt;Brought to you by: &lt;a href='https://s.example'&gt;S&lt;/a&gt;&lt;/p&gt;</description></item>"
        for t, g, d, l in items
    )
    return (f'<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><title>Show</title>{body}'
            f"</channel></rss>").encode()


class FakeSession:
    def __init__(self, feed: bytes, pages: dict[str, str]):
        self.feed, self.pages, self.asked = feed, pages, []

    def get(self, url, **kwargs):
        self.asked.append(url)
        if url == "https://feeds.example/show.xml":
            return SimpleNamespace(url=url, history=[], content=self.feed, status_code=200, raise_for_status=lambda: None)
        text = self.pages.get(url)
        return SimpleNamespace(status_code=200 if text else 404, text=text or "")


def _npr_page(words: int) -> str:
    return '<div class="transcript storytext"><p>HOST: ' + " ".join(["word"] * words) + "<p>Copyright © NPR</div>"


def _cfg(**over):
    base = dict(slug="example", show_id=11, feed_url="https://feeds.example/show.xml", feed_title_prefix=None,
                transcript_source="npr", store_raw_content=True)
    base.update(over)
    return SimpleNamespace(**base)


def test_a_new_episode_is_inserted_with_its_transcript_and_raw_content_shape() -> None:
    link = "https://www.npr.org/2026/11/24/nx-s1-1111111/slug"
    feed = _feed_xml([("New Ep", "g-new", "Tue, 24 Nov 2026 08:00:00 +0000", link)])
    session = FakeSession(feed, {"https://www.npr.org/transcripts/nx-s1-1111111": _npr_page(1900)})
    conn = FakeConn()
    report = imp.run_show(_cfg(), conn, now=NOW, dry_run=False, backfill=False, session=session, pause_seconds=0)

    assert report.inserted and report.transcripts_added == 1 and not report.problems
    insert = next(p for s, p in conn.statements if s.startswith("INSERT INTO episodes"))
    show_id, title, url, publish_date = insert[:4]
    assert (show_id, title, url, publish_date) == (11, "New Ep", "g-new", date(2026, 11, 24))
    assert "scraped_at" in next(s for s, _ in conn.statements if s.startswith("INSERT INTO episodes"))
    raw = json.loads(insert[8])
    assert raw["provider"] == "rss" and raw["guid"] == "g-new" and raw["link"] == link
    # The HTML survives: sponsors.roster_from_raw_content parses the roster out of it.
    assert "Brought to you by:" in raw["description"] and "<a href=" in raw["description"]
    transcript = next(p for s, p in conn.statements if s.startswith("INSERT INTO episode_transcripts"))
    assert transcript[1] == "publisher_transcript" and transcript[5] == "npr_transcript_page"
    assert "ON CONFLICT (episode_id) DO NOTHING" in next(s for s, _ in conn.statements if "episode_transcripts" in s and s.startswith("INSERT"))


def test_held_episodes_get_no_write_at_all() -> None:
    """The Taddy importer rewrote ~1,500 held rows a day; re-stamping scraped_at on held
    TAL rows is what emptied the song queue."""
    feed = _feed_xml([("Held Ep", "g-held", "Tue, 24 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-2/x")])
    held = [{"id": 7, "url": "https://api.taddy.org/podcast-episode/u", "title": "Held Ep",
             "publish_date": date(2026, 11, 24), "raw_guid": "g-held", "has_transcript": True}]
    conn = FakeConn(held_rows=held)
    report = imp.run_show(_cfg(), conn, now=NOW, dry_run=False, backfill=False, session=FakeSession(feed, {}), pause_seconds=0)
    assert conn.writes() == [] and report.counts == {"held": 1} and report.matched_by == {"raw_guid": 1}


def test_a_held_episode_waiting_for_its_transcript_gets_only_the_transcript() -> None:
    link = "https://www.npr.org/2026/11/23/nx-s1-3333333/x"
    feed = _feed_xml([("Waiting Ep", "g-w", "Mon, 23 Nov 2026 08:00:00 +0000", link)])
    held = [{"id": 8, "url": "g-w", "title": "Waiting Ep", "publish_date": date(2026, 11, 23),
             "raw_guid": None, "has_transcript": False}]
    conn = FakeConn(held_rows=held)
    session = FakeSession(feed, {"https://www.npr.org/transcripts/nx-s1-3333333": _npr_page(1900)})
    report = imp.run_show(_cfg(), conn, now=NOW, dry_run=False, backfill=False, session=session, pause_seconds=0)
    assert [s.split(" (")[0] for s in conn.writes()] == ["INSERT INTO episode_transcripts"]
    assert report.transcripts_added == 1 and report.inserted == []


def test_a_transcript_not_up_yet_is_reported_and_the_row_still_lands() -> None:
    feed = _feed_xml([("Fresh", "g-f", "Tue, 24 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-4/x")])
    conn = FakeConn()
    report = imp.run_show(_cfg(), conn, now=NOW, dry_run=False, backfill=False, session=FakeSession(feed, {}), pause_seconds=0)
    assert len(report.inserted) == 1 and report.transcripts_added == 0
    assert len(report.transcripts_pending) == 1 and not report.problems


def test_a_url_another_show_holds_is_refused_and_reported() -> None:
    feed = _feed_xml([("Clash", "g-taken", "Tue, 24 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-5/x")])
    conn = FakeConn(taken_urls={"g-taken"})
    report = imp.run_show(_cfg(), conn, now=NOW, dry_run=False, backfill=False, session=FakeSession(feed, {}), pause_seconds=0)
    assert report.inserted == [] and report.problems and "another show" in report.problems[0]
    assert not any("episode_transcripts" in s for s in conn.writes())


def test_a_dry_run_writes_nothing_and_fetches_no_transcripts() -> None:
    feed = _feed_xml([("New", "g-n", "Tue, 24 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-6/x")])
    session = FakeSession(feed, {})
    conn = FakeConn()
    imp.run_show(_cfg(), conn, now=NOW, dry_run=True, backfill=False, session=session, pause_seconds=0)
    assert conn.writes() == [] and session.asked == ["https://feeds.example/show.xml"]


def test_raw_content_is_only_stored_for_shows_that_keep_it() -> None:
    feed = _feed_xml([("TAL ep", "37237 at https://www.thisamericanlife.org", "Sun, 22 Nov 2026 20:00:00 -0500",
                       "https://www.thisamericanlife.org/436/x")])
    conn = FakeConn()
    imp.run_show(_cfg(transcript_source=None, store_raw_content=False), conn, now=NOW, dry_run=False,
                 backfill=False, session=FakeSession(feed, {}), pause_seconds=0)
    insert = next(p for s, p in conn.statements if s.startswith("INSERT INTO episodes"))
    assert insert[8] is None
    assert insert[3] == date(2026, 11, 23)  # the UTC day of a Sunday-evening Eastern episode


def test_the_cli_refuses_an_unknown_show_without_retrying(monkeypatch) -> None:
    import pytest

    monkeypatch.setattr("common.load_environment", lambda *a, **k: None)
    with pytest.raises(SystemExit) as exc:
        imp.main(["--shows", "no-such-show"])
    assert exc.value.code == imp.DETERMINISTIC_EXIT_CODE


def test_a_title_match_onto_a_row_tied_to_another_guid_is_a_conflict_not_a_merge() -> None:
    """Two items can share a title (a daily roundup, a re-upload under a new guid). Folding
    the second onto the first row would attach its transcript to the wrong episode."""
    item = _item("Daily Roundup", "guid-B", days_ago=1)
    held = imp.HeldIndex([_held(1, "Daily Roundup", item.publish_date - timedelta(days=1), url="guid-A",
                                has_transcript=False)])
    assert held.match(item, "guid-B")[1] == "guid_conflict"
    assert _kinds(imp.plan_import([item], held, now=NOW, wants_transcript=True)) == {"Daily Roundup": "conflict"}


def test_two_new_items_with_one_title_and_day_insert_once() -> None:
    items = [_item("Re-uploaded", "g-1", days_ago=1), _item("Re-uploaded", "g-2", days_ago=1)]
    kinds = [a.kind for a in imp.plan_import(items, imp.HeldIndex([]), now=NOW, wants_transcript=False)]
    assert kinds == ["insert", "duplicate"]


def test_an_undated_item_still_matches_by_guid() -> None:
    item = FeedItem("Undated but held", "g-held", None, None, "garbage", None, "")
    held = imp.HeldIndex([_held(1, "x", date(2026, 1, 1), url="g-held")])
    assert _kinds(imp.plan_import([item], held, now=NOW, wants_transcript=False)) == {"Undated but held": "held"}


def test_backfill_also_retries_old_rows_still_missing_a_transcript() -> None:
    item = _item("Old, no transcript", "g", days_ago=40)
    held = imp.HeldIndex([_held(1, "x", date(2026, 1, 1), url="g", has_transcript=False)])
    assert _kinds(imp.plan_import([item], held, now=NOW, wants_transcript=True)) == {"Old, no transcript": "held"}
    assert _kinds(imp.plan_import([item], held, now=NOW, wants_transcript=True, backfill=True)) == {"Old, no transcript": "transcript"}


def test_a_feed_with_no_dated_items_is_a_problem_not_a_quiet_day() -> None:
    feed = b"<rss><channel><title>Show</title></channel></rss>"
    report = imp.run_show(_cfg(), FakeConn(), now=NOW, dry_run=False, backfill=False,
                          session=FakeSession(feed, {}), pause_seconds=0)
    assert report.problems and "no dated items" in report.problems[0]


def test_conflicts_duplicates_and_undated_items_are_problems() -> None:
    feed = _feed_xml([
        ("Same", "g-1", "Tue, 24 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-7/x"),
        ("Same", "g-2", "Tue, 24 Nov 2026 09:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-8/x"),
        ("No date", "g-3", "garbage", "https://www.npr.org/2026/11/24/nx-s1-9/x"),
    ])
    report = imp.run_show(_cfg(), FakeConn(), now=NOW, dry_run=False, backfill=False,
                          session=FakeSession(feed, {}), pause_seconds=0)
    assert len(report.inserted) == 1
    assert any("share a title and day" in p for p in report.problems)
    assert any("unreadable date" in p for p in report.problems)


def test_a_rate_limited_source_is_not_asked_again_this_run() -> None:
    feed = _feed_xml([
        ("One", "g-1", "Tue, 24 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/24/nx-s1-11/x"),
        ("Two", "g-2", "Mon, 23 Nov 2026 08:00:00 +0000", "https://www.npr.org/2026/11/23/nx-s1-12/x"),
    ])

    class Limited(FakeSession):
        def get(self, url, **kwargs):
            if "transcripts" in url:
                self.asked.append(url)
                return SimpleNamespace(status_code=429, text="")
            return super().get(url, **kwargs)

    session = Limited(feed, {})
    report = imp.run_show(_cfg(), FakeConn(), now=NOW, dry_run=False, backfill=False, session=session, pause_seconds=0)
    assert len([u for u in session.asked if "transcripts" in u]) == 1
    assert any("429" in n for n in report.notes) and len(report.inserted) == 2


def test_a_shows_start_date_collects_everything_since_it_however_old() -> None:
    """Machine Gods: its whole run arrives on the first import, past the 30-day window,
    and nothing before its start date does."""
    items = [_item("Ep 11", "g11", days_ago=4), _item("Ep 1", "g1", days_ago=34), _item("Before", "g0", days_ago=80)]
    actions = imp.plan_import(items, imp.HeldIndex([]), now=NOW, wants_transcript=False,
                              import_since=(NOW - timedelta(days=60)).date())
    assert [(a.kind, a.item.title) for a in actions] == [("insert", "Ep 11"), ("insert", "Ep 1"), ("old", "Before")]
    # Without a start date the 30-day window applies as before.
    plain = imp.plan_import(items, imp.HeldIndex([]), now=NOW, wants_transcript=False)
    assert [a.kind for a in plain] == ["insert", "old", "old"]

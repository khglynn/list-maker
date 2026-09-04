"""Unit tests for the pulse digest logic.

The pulse's core decision is "are we caught up to the real feed?" (second source), and
its job is to never show green when we're behind or can't verify. That logic is pinned
here. Pure formatting + comparison, no DB / no network.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from pipeline.data_health import CheckResult, HeldEpisodes
from pipeline.feed_check import FeedEpisode
from pipeline.pulse_report import build_digest, show_status

TOTALS = {"entities": 100, "mentions": 200, "notion_transcripts": 50}


def _show(slug: str = "ai-daily-brief", latest: str = "2026-06-06", days: int = 1,
          recent: int = 2, feed_dates: object = "caught_up") -> dict:
    """A show on the DATE comparison — `cfg=None` declares no episode identity, which
    is the path SOP takes (its scraper writes the urls; Taddy is only a second source)
    and the path any DB show row with no config takes. The identity comparison every
    other show uses has its own fixture below."""
    ldate = date.fromisoformat(latest) if latest else None
    fd = feed_dates
    if fd == "caught_up":  # feed agrees with our latest
        fd = [ldate] if ldate else []
    return {"slug": slug, "latest": ldate, "days_since": days, "recent": recent,
            "episodes": 10, "feed_dates": fd, "cfg": None}


def _ep(identity: str, day: str, title: str = "An Episode") -> FeedEpisode:
    return FeedEpisode(identity, date.fromisoformat(day), title)


def _identity_show(feed: list[FeedEpisode] | None, held_urls: set[str],
                   latest: str = "2026-06-06", grace: int = 2,
                   held_title_dates: set | None = None) -> dict:
    """A show on the IDENTITY comparison — the shape five of six podcasts have, and
    the one the pulse could not make before 2026-09-03.

    `feed_dates` is deliberately populated with the same feed's dates even though the
    identity path must never read it. That is what makes these tests discriminate: if
    the pulse is ever rerouted back to the date comparison, the mid-series and re-date
    cases below flip their verdicts instead of failing on a missing key.
    """
    return {
        "slug": "ai-daily-brief",
        "latest": date.fromisoformat(latest),
        "days_since": 1,
        "recent": 2,
        "episodes": 10,
        "cfg": SimpleNamespace(
            episode_identity="taddy_uuid", feed_grace_days=grace, medium="podcast"
        ),
        "feed_episodes": feed,
        "held": HeldEpisodes(
            urls=set(held_urls), title_dates=set(held_title_dates or set())
        ),
        "feed_dates": [ep.publish_date for ep in feed] if feed else None,
    }


def test_caught_up_is_ok() -> None:
    status, state = show_status(_show())
    assert "caught up" in status and state == "ok"


def test_behind_the_feed_flags_and_counts() -> None:
    # Feed has two episodes newer than ours -> BEHIND 2.
    s = _show(latest="2026-06-06", feed_dates=[date(2026, 6, 8), date(2026, 6, 7), date(2026, 6, 6)])
    status, state = show_status(s)
    assert "BEHIND 2" in status and state == "behind"


def test_feed_unverified_is_its_own_state() -> None:
    # Couldn't reach the feed -> "unverified", NOT green, NOT behind.
    status, state = show_status(_show(feed_dates=None))
    assert "unverified" in status and state == "unverified"


def test_quiet_show_caught_up_is_explained() -> None:
    # Caught up but old (25d > TAL's 21d threshold, feed also old) -> ok, but says "quiet".
    s = _show(slug="tal", latest="2026-05-13", days=25, feed_dates=[date(2026, 5, 13)])
    status, state = show_status(s)
    assert "caught up" in status and "quiet" in status and state == "ok"


def test_digest_has_hub_link_and_green_headline() -> None:
    digest = build_digest([_show()], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "Pod Lists hub" in digest
    assert "caught up to every feed" in digest


def test_digest_behind_flips_headline() -> None:
    behind = _show(feed_dates=[date(2026, 6, 8), date(2026, 6, 6)])  # behind 1
    digest = build_digest([behind], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "need attention" in digest


def test_digest_unverified_is_not_claimed_as_every_feed() -> None:
    # An unverified feed must NOT produce "caught up to every feed" — that would be a lie.
    digest = build_digest([_show(feed_dates=None)], TOTALS, [CheckResult("x", "pass", "ok", [])])
    assert "caught up to every feed" not in digest
    assert "unverified" in digest


def test_digest_warning_only_stays_green_but_counted() -> None:
    checks = [CheckResult("possible_entity_alias_splits", "warn", "25 splits", [])]
    digest = build_digest([_show()], TOTALS, checks)
    assert "caught up to every feed" in digest
    assert "1 warning" in digest
    assert "Needs attention" not in digest


# ---- curated sources, the grace window, and the intake line (2026-09-02) ----

class _Cfg:
    def __init__(self, medium: str = "podcast", grace: int = 2) -> None:
        self.medium = medium
        self.feed_grace_days = grace
        self.notion_database_id = None
        self.spotify_playlist_id = None


def test_curated_source_is_curated_not_unverified() -> None:
    # A blog source has no feed. "Unverified" is for feeds we tried to read and couldn't.
    s = _show(slug="openai-blog", latest="2026-06-10", feed_dates=None)
    s["cfg"] = _Cfg("blog")
    status, state = show_status(s)
    assert state == "curated"
    assert "unverified" not in status and "2026-06-10" in status


def test_digest_does_not_count_curated_as_unverified() -> None:
    s = _show(slug="openai-blog", latest="2026-06-10", feed_dates=None)
    s["cfg"] = _Cfg("blog")
    digest = build_digest([_show(), s], TOTALS, [CheckResult("x", "pass", "ok", [])],
                          intake={"judged": 0, "would_save_backlog": 0})
    assert "unverified" not in digest
    assert "All systems firing" in digest
    assert "Curated sources" in digest and "OpenAI blog" in digest


def test_fresh_episode_inside_the_grace_window_is_caught_up() -> None:
    # Published two days ago, imported daily with a 2-day grace: pending, not behind.
    s = _show(latest="2026-08-29", feed_dates=[date(2026, 8, 31), date(2026, 8, 29)])
    status, state = show_status(s, today=date(2026, 9, 1))
    assert state == "ok" and "pending" in status


def test_stale_missing_episode_is_still_behind(monkeypatch) -> None:
    s = _show(latest="2026-08-29", feed_dates=[date(2026, 8, 31), date(2026, 8, 29)])
    status, state = show_status(s, today=date(2026, 9, 6))
    assert state == "behind" and "BEHIND 1" in status


def test_intake_line_reports_what_the_judge_did() -> None:
    # Changed 2026-09-02: this used to nudge Kevin about unchecked boxes. Nothing waits
    # on a checkbox now, so the pulse reports what the judge DID and what shadow mode
    # has piled up unhandled.
    digest = build_digest([_show()], TOTALS, [], intake={
        "judged": 18, "would_save": 5, "disputed": 2, "would_save_backlog": 9, "held": 1})
    assert "18 judged" in digest and "5 marked save" in digest
    assert "2 disputed" in digest and "9 not yet ingested" in digest


def test_intake_line_keeps_a_stuck_failure_visible() -> None:
    # The weekly Slack line reports only the run that produced a failure. The pulse
    # reads a 15-day window, so a row stuck failing stays visible instead of being
    # mentioned once and then going quiet forever.
    digest = build_digest([_show()], TOTALS, [], intake={
        "judged": 4, "would_save": 1, "would_save_backlog": 1, "failed": 3})
    assert "3 failed" in digest


def test_intake_line_is_quiet_but_present_on_an_empty_period() -> None:
    digest = build_digest([_show()], TOTALS, [], intake={"judged": 0, "would_save_backlog": 0})
    assert "nothing judged this period" in digest


def test_intake_line_is_honest_when_the_table_cannot_be_read() -> None:
    # A missing table or a Neon hiccup must read as "couldn't check", never as zeroes.
    digest = build_digest([_show()], TOTALS, [], intake=None)
    assert "couldn't read" in digest


# --------------------------------------------------- the identity comparison (2026-09-03)
# The digest used to compare MAX(publish_date) against the feed for every show, while
# the daily check had already moved to episode identity. Two answers to one question:
# the pulse could print a BEHIND for a show the daily check called caught up. These pin
# the pulse on the same comparison, including the two cases dates cannot answer.


def test_identity_show_caught_up_is_ok() -> None:
    feed = [_ep("u-3", "2026-06-06"), _ep("u-2", "2026-06-04")]
    s = _identity_show(feed, held_urls={"u-3", "u-2"})
    status, state = show_status(s, today=date(2026, 6, 10))
    assert "caught up" in status and state == "ok"


def test_identity_show_sees_a_hole_in_the_middle_of_a_series() -> None:
    """The whole prize: we hold the NEWEST episode, so MAX(publish_date) says caught up
    forever — but an older one was never imported. A set difference sees it."""
    feed = [_ep("u-3", "2026-06-06"), _ep("u-2", "2026-06-04"), _ep("u-1", "2026-06-02")]
    s = _identity_show(feed, held_urls={"u-3", "u-1"}, latest="2026-06-06")
    status, state = show_status(s, today=date(2026, 6, 10))
    assert "BEHIND 1" in status and state == "behind"
    # And the line still reads the way it always did.
    assert "feed at 2026-06-06, we have 2026-06-06" in status


def test_identity_show_ignores_a_re_dated_episode_we_already_hold() -> None:
    """The TAL false BEHIND (DEVLOG 2026-09-01). Taddy re-dates an episode we hold to a
    date newer than our MAX(publish_date); by date that is a brand-new missing episode,
    by identity it is the row we already have."""
    feed = [_ep("u-2", "2026-06-09"), _ep("u-1", "2026-06-04")]
    s = _identity_show(feed, held_urls={"u-2", "u-1"}, latest="2026-06-06")
    status, state = show_status(s, today=date(2026, 6, 20))
    assert state == "ok"
    assert "BEHIND" not in status


def test_identity_show_missing_episode_inside_grace_is_pending_not_behind() -> None:
    """The grace contract is unchanged by the switch: published but not yet imported,
    inside the show's import window, is still not an alarm."""
    feed = [_ep("u-9", "2026-06-09"), _ep("u-2", "2026-06-04")]
    s = _identity_show(feed, held_urls={"u-2"}, latest="2026-06-04", grace=2)
    status, state = show_status(s, today=date(2026, 6, 10))
    assert state == "ok" and "1 newer pending import" in status


def test_identity_show_unreachable_feed_is_unverified_not_green() -> None:
    s = _identity_show(None, held_urls={"u-1"})
    status, state = show_status(s, today=date(2026, 6, 10))
    assert "unverified" in status and state == "unverified"


def test_the_date_comparison_gets_both_of_those_wrong() -> None:
    """Proof the two cases above are load-bearing, not decoration: the identical show
    data, routed the old way (no episode_identity), produces the two verdicts that made
    the digest untrustworthy — a hole it cannot see, and a BEHIND that isn't real."""
    hole = _identity_show(
        [_ep("u-3", "2026-06-06"), _ep("u-2", "2026-06-04"), _ep("u-1", "2026-06-02")],
        held_urls={"u-3", "u-1"},
        latest="2026-06-06",
    )
    hole["cfg"] = SimpleNamespace(episode_identity=None, feed_grace_days=2, medium="podcast")
    assert show_status(hole, today=date(2026, 6, 10))[1] == "ok"  # blind to the hole

    redated = _identity_show(
        [_ep("u-2", "2026-06-09"), _ep("u-1", "2026-06-04")],
        held_urls={"u-2", "u-1"},
        latest="2026-06-06",
    )
    redated["cfg"] = SimpleNamespace(episode_identity=None, feed_grace_days=2, medium="podcast")
    assert show_status(redated, today=date(2026, 6, 20))[1] == "behind"  # a false alarm


def test_identity_show_matches_a_legacy_url_row_by_title_and_date() -> None:
    """Rows written before a show's importer changed hands are held under an older url
    and match only on title+date — the same fallback the daily check uses. Without it
    TAL reports BEHIND on episodes we demonstrably have."""
    feed = [_ep("u-new", "2026-06-06", "An Old Bonus Episode")]
    s = _identity_show(
        feed,
        held_urls=set(),
        held_title_dates={("an old bonus episode", date(2026, 6, 6))},
        latest="2026-06-06",
    )
    status, state = show_status(s, today=date(2026, 6, 20))
    assert state == "ok" and "BEHIND" not in status

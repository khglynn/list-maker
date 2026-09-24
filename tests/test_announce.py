"""The announce step: one message per change of state, a weekly reminder, and issues
that open and close themselves.

The scenarios replay what #list-maker actually received in September 2026 — two pings
per red day, a failure thread (#64) that merged unrelated streaks and never closed — and
pin the replacement behaviour Kevin asked for on 2026-09-23: say it once, again weekly
while it's still true, and "recovered" on the first green.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from pipeline import announce
from pipeline.alert_guides import CHECK_GUIDES, STEP_GUIDES
from pipeline.announce import (
    REMIND_AFTER_DAYS,
    Alert,
    Finding,
    RunContext,
    announce as run_announce,
    collect_findings,
    decide,
    parse_alert,
    render_slack,
)

RUN = "https://github.com/khglynn/list-maker/actions/runs/1"
SEP22 = date(2026, 9, 22)
ENTITIES = RunContext.build("entities")

AI_DAILY_BEHIND = {
    "name": "import_caught_up_to_feed",
    "status": "fail",
    "summary": "1 show(s) behind their feed (missing episodes).",
    "details": ["ai-daily-brief: BEHIND 1 — feed at 2026-09-19, we have 2026-09-18 (past the 2-day import window)"],
    "failures": ["ai-daily-brief: BEHIND 1 — feed at 2026-09-19, we have 2026-09-18 (past the 2-day import window)"],
}
NOTION_DRIFT = {
    "name": "notion_sync_freshness",
    "status": "fail",
    "summary": "1 Notion sync drift failure(s), 0 warning(s).",
    "details": ["1 entity page(s) have Neon updates waiting >2d that never reached Notion — Codex (7)"],
    "failures": ["1 entity page(s) have Neon updates waiting >2d that never reached Notion — Codex (7)"],
}


def _passing(name: str) -> dict:
    return {"name": name, "status": "pass", "summary": "ok", "details": []}


def _steps(**outcomes: str) -> dict:
    return {sid: {"outcome": o, "conclusion": o, "outputs": {}} for sid, o in outcomes.items()}


GREEN_STEPS = _steps(preflight="success", **{"import": "success"}, notion="success", health="success")
RED_HEALTH_STEPS = _steps(preflight="success", **{"import": "success"}, notion="success", health="failure")


def _entities(health: list[dict], *, steps=None, job="failure", preflight=None) -> dict[str, Finding]:
    return collect_findings(ENTITIES, steps or RED_HEALTH_STEPS, job, health, preflight)


class FakeGitHub:
    """An in-memory issue tracker with the four calls announce.py makes."""

    def __init__(self, issues: list[dict] | None = None, fail_reads: bool = False):
        self.issues = {i["number"]: dict(i) for i in (issues or [])}
        self.comments: list[tuple[int, str]] = []
        self.writes: list[str] = []
        self.fail_reads = fail_reads
        self._next = max(self.issues, default=100) + 1

    def open_issues(self):
        if self.fail_reads:
            raise RuntimeError("HTTP 502")
        return [i for i in self.issues.values() if i.get("state", "open") == "open"]

    def recently_closed(self, since):
        return [i for i in self.issues.values() if i.get("state") == "closed"]

    def create_issue(self, title, body, labels):
        number = self._next
        self._next += 1
        self.issues[number] = {
            "number": number, "title": title, "body": body, "state": "open",
            "labels": [{"name": n} for n in labels],
            "html_url": f"https://github.com/khglynn/list-maker/issues/{number}",
            "created_at": "2026-09-22T20:40:00Z",
        }
        self.writes.append(f"create #{number}")
        return self.issues[number]

    def edit_issue(self, number, **fields):
        self.issues[number].update(fields)
        self.writes.append(f"edit #{number}")

    def comment(self, number, body):
        self.comments.append((number, body))
        self.writes.append(f"comment #{number}")

    def alert(self, key: str) -> Alert | None:
        for issue in self.issues.values():
            a = parse_alert(issue)
            if a and a.key == key and issue.get("state", "open") == "open":
                return a
        return None


class FakeSlack:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.messages: list[str] = []

    def __call__(self, text: str) -> bool:
        self.messages.append(text)
        return self.ok


def _run(gh, slack, findings, today, **kw):
    return run_announce(ENTITIES, findings, gh=gh, post=slack, today=today, run_url=RUN, **kw)


# ── what counts as evaluated ────────────────────────────────────────────────────────────

def test_a_failing_check_is_its_own_finding_and_the_health_step_is_not_blamed():
    findings = _entities([AI_DAILY_BEHIND, _passing("notion_sync_freshness")])
    assert findings["entities:import_caught_up_to_feed"].failing
    assert not findings["entities:notion_sync_freshness"].failing
    assert not findings["entities:step:health"].failing  # it reported; the check failed
    assert not findings["entities:run"].failing  # the failure is explained by a check


def test_a_health_step_that_crashed_before_reporting_is_the_failure():
    findings = _entities(None)
    assert findings["entities:step:health"].failing
    assert not any(k.startswith("entities:import") for k in findings)


def test_steps_that_did_not_run_are_not_evaluated():
    """Neon unreachable: the preflight fails, everything after it is skipped. Nothing
    skipped may count as passing — an open issue for a check must not 'recover' on a
    day the check never ran."""
    steps = _steps(preflight="failure", **{"import": "skipped"}, notion="skipped", health="skipped")
    findings = _entities(None, steps=steps, preflight="host `ep-x` · addresses `1.2.3.4`\n`timeout`")
    assert set(findings) == {"entities:step:preflight", "entities:run"}
    assert not findings["entities:run"].failing  # explained by the preflight
    assert findings["entities:step:preflight"].failing
    assert findings["entities:step:preflight"].details == ["host `ep-x` · addresses `1.2.3.4`", "`timeout`"]


def test_a_red_run_with_no_tracked_failure_is_still_announced():
    """pip install failed: every tracked step was skipped. A red run is never silent."""
    steps = _steps(preflight="skipped", **{"import": "skipped"}, notion="skipped", health="skipped")
    findings = _entities(None, steps=steps)
    assert findings["entities:run"].failing


def test_a_cancelled_run_is_a_failure_not_silence():
    """Review finding C6: a job cut off by its time limit ends "cancelled". Staying quiet
    about that is the shape that hid the May-June 2026 playlist outage."""
    steps = _steps(preflight="success", **{"import": "success"}, notion="cancelled", health="skipped")
    findings = collect_findings(ENTITIES, steps, "cancelled")
    run = findings["entities:run"]
    assert run.failing and "cancelled before it finished" in run.summary
    assert not findings["entities:step:import"].failing  # what did run still counts
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, findings, SEP22)
    assert len(slack.messages) == 1 and "Something outside the main steps failed" in slack.messages[0]


def test_music_keys_are_scoped_to_the_show_that_ran():
    ctx = RunContext.build("music", "2")
    assert ctx.prefix == "music-tal" and ctx.label == "TAL music run"
    findings = collect_findings(ctx, _steps(preflight="success", spotify_cache="success",
                                            pipeline="failure", feed_check="skipped"), "failure")
    assert set(findings) == {"music-tal:step:preflight", "music-tal:step:spotify_cache",
                             "music-tal:step:pipeline", "music-tal:run"}


# ── the decision ────────────────────────────────────────────────────────────────────────

def _alert(key, *, since=SEP22, last_posted=SEP22, number=70, said="failing", green=0,
           closed=False) -> Alert:
    return Alert(number, f"https://x/{number}", key, since, last_posted, said, green, "F", [], 1, closed)


def _kinds(items) -> dict[str, str]:
    return {i.finding.key: i.kind for i in items}


def test_decide_covers_every_transition():
    """The whole state machine on one page: each key's kind for two dates."""
    f = lambda key, failing: Finding(key, key.split(":")[1], CHECK_GUIDES["sponsor_share"], failing)  # noqa: E731
    findings = {k: f(k, failing) for k, failing in [
        ("entities:new", True), ("entities:open", True), ("entities:relapse", True),
        ("entities:green1", False), ("entities:green2", False), ("entities:healed_again", False)]}
    memory = {
        "entities:open": _alert("entities:open"),
        "entities:relapse": _alert("entities:relapse", said="recovered", closed=True),
        "entities:green1": _alert("entities:green1"),
        "entities:green2": _alert("entities:green2", green=1),
        "entities:healed_again": _alert("entities:healed_again", said="recovered", green=1),
        "entities:unreached": _alert("entities:unreached"),
    }
    assert _kinds(decide(ENTITIES, findings, memory, SEP22 + timedelta(days=3))) == {
        "entities:new": "new",                   # first failure: said at once
        "entities:open": "quiet_fail",           # said 3 days ago: quiet
        "entities:relapse": "quiet_fail",        # relapse within a week: reopened quietly
        "entities:green1": "quiet_pass",         # one green day is not yet a recovery
        "entities:green2": "recovered",          # second green day in a row: said
        "entities:healed_again": "quiet_close",  # "recovered" was already the last word
        # entities:unreached: not due yet, so nothing
    }
    week = _kinds(decide(ENTITIES, findings, memory, SEP22 + timedelta(days=REMIND_AFTER_DAYS)))
    assert week["entities:open"] == "remind"
    assert week["entities:relapse"] == "again"
    assert week["entities:unreached"] == "unchecked"  # not evaluated, and due: still said


def test_an_issue_whose_message_never_landed_is_reminded_next_run():
    finding = Finding("entities:a", "a", CHECK_GUIDES["sponsor_share"], True)
    items = decide(ENTITIES, {finding.key: finding}, {finding.key: _alert(finding.key, last_posted=None)},
                   SEP22 + timedelta(days=1))
    assert _kinds(items) == {"entities:a": "remind"}


# ── the whole loop, replayed ────────────────────────────────────────────────────────────

def test_a_new_failure_is_one_message_with_plain_words_and_its_own_issue():
    gh, slack = FakeGitHub(), FakeSlack()
    out = _run(gh, slack, _entities([AI_DAILY_BEHIND, _passing("notion_sync_freshness")]), SEP22)

    assert len(slack.messages) == 1  # was two pings a second apart, every red day
    msg = slack.messages[0]
    guide = CHECK_GUIDES["import_caught_up_to_feed"]
    for needle in ("1 new problem", guide.title, "_Usually:_", guide.usually,
                   "_Check first:_", "ai-daily-brief: BEHIND 1", f"<{RUN}|View this run>",
                   "issues/101|#101"):
        assert needle in msg, needle
    alert = gh.alert("entities:import_caught_up_to_feed")
    assert alert and alert.since == SEP22 and alert.last_posted == SEP22
    assert "A show is behind its podcast feed (since 2026-09-22)" in gh.issues[101]["title"]
    assert out.posted


def test_the_same_failure_the_next_day_is_quiet_and_the_issue_updates_silently():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22)
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22 + timedelta(days=1))

    assert len(slack.messages) == 1
    assert gh.comments == []  # a comment notifies; a body edit doesn't
    assert gh.alert("entities:import_caught_up_to_feed").runs == 2


def test_a_week_later_it_is_said_again_with_how_long_it_has_been_true():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22)
    for day in range(1, REMIND_AFTER_DAYS):
        _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22 + timedelta(days=day))
    assert len(slack.messages) == 1

    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22 + timedelta(days=REMIND_AFTER_DAYS))
    assert len(slack.messages) == 2
    assert "Still failing since Sep 22* (7 days)" in slack.messages[1]
    assert gh.comments and "7 days since 2026-09-22" in gh.comments[-1][1]
    alert = gh.alert("entities:import_caught_up_to_feed")
    assert alert.since == SEP22 and alert.last_posted == SEP22 + timedelta(days=7)

    # ...and then quiet for another week.
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22 + timedelta(days=8))
    assert len(slack.messages) == 2


def _green(*names: str):
    return collect_findings(ENTITIES, GREEN_STEPS, "success", [_passing(n) for n in names])


def test_recovery_is_said_once_it_has_held_and_the_issue_closes():
    """A daily check says "recovered" on its second green day in a row (RECOVERY_HOLD_DAYS),
    not its first: a check hovering at a threshold otherwise posts every day."""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22)
    _run(gh, slack, _green("import_caught_up_to_feed"), SEP22 + timedelta(days=1))
    assert len(slack.messages) == 1 and gh.issues[101]["state"] == "open"  # not held yet

    _run(gh, slack, _green("import_caught_up_to_feed"), SEP22 + timedelta(days=2))
    assert len(slack.messages) == 2
    assert "recovered" in slack.messages[1] and "failing since Sep 22" in slack.messages[1]
    assert gh.issues[101]["state"] == "closed"
    assert any("Recovered on 2026-09-24" in body for _, body in gh.comments)

    # A further green run has nothing to say.
    _run(gh, slack, _green("import_caught_up_to_feed"), SEP22 + timedelta(days=3))
    assert len(slack.messages) == 2


def test_a_weekly_check_recovers_on_its_first_green_run():
    gh, slack = FakeGitHub(), FakeSlack()
    intake = RunContext.build("intake")
    red = collect_findings(intake, _steps(preflight="success", log_schema="success", intake="failure"), "failure")
    run_announce(intake, red, gh=gh, post=slack, today=SEP22, run_url=RUN)
    green = collect_findings(intake, _steps(preflight="success", log_schema="success", intake="success"), "success")
    run_announce(intake, green, gh=gh, post=slack, today=SEP22 + timedelta(days=7), run_url=RUN)
    assert len(slack.messages) == 2 and "recovered" in slack.messages[1]


def test_unrelated_failures_get_separate_issues_and_each_recovers_on_its_own():
    """#64 put the 09-14 Notion race and the 09-22 SOP lag in one thread."""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([NOTION_DRIFT, _passing("import_caught_up_to_feed")]), date(2026, 9, 21))
    _run(gh, slack, _entities([_passing("notion_sync_freshness"), AI_DAILY_BEHIND]), SEP22)

    assert len(slack.messages) == 2 and "1 new problem" in slack.messages[1]
    notion_issue, feed_issue = gh.issues[101], gh.issues[102]
    assert "Notion" in notion_issue["title"] and "podcast feed" in feed_issue["title"]
    assert notion_issue["state"] == "open"  # one green day: not held yet

    _run(gh, slack, _entities([_passing("notion_sync_freshness"), AI_DAILY_BEHIND]),
         SEP22 + timedelta(days=1))
    assert len(slack.messages) == 3 and "Recovered:" in slack.messages[2]
    assert notion_issue["state"] == "closed" and feed_issue["state"] == "open"


def test_a_failure_joining_one_already_reported_is_new_and_names_the_other():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([NOTION_DRIFT]), date(2026, 9, 21))
    _run(gh, slack, _entities([NOTION_DRIFT, AI_DAILY_BEHIND]), SEP22)
    assert len(slack.messages) == 2
    assert "Also still failing, already reported" in slack.messages[1]
    assert "Notion is behind Neon" in slack.messages[1]


def _feed_failing(*slugs: str) -> dict:
    lines = [f"{slug}: BEHIND 1 — feed at 2026-09-19 (past the window)" for slug in slugs]
    return {"name": "import_caught_up_to_feed", "status": "fail",
            "summary": f"{len(slugs)} show(s) behind their feed (missing episodes).",
            "details": lines, "failures": lines}


def test_a_second_show_failing_an_open_check_is_said_in_its_weekly_word():
    """Kevin's rule is per failing CHECK. A second show failing a check that is already
    open is part of that problem: the issue body shows it at once, and the next weekly
    word lists it. (Show-level "now also failing" news was removed 2026-09-24: it was
    one of the features whose interactions kept producing holes.)"""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief")]), SEP22)
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief", "tal")]), SEP22 + timedelta(days=1))
    assert len(slack.messages) == 1
    assert "shows:** ai-daily-brief, tal" in gh.issues[101]["body"]
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief", "tal")]), SEP22 + timedelta(days=7))
    assert len(slack.messages) == 2 and "tal: BEHIND 1" in slack.messages[1]


def test_one_music_show_can_have_two_threads_each_said_at_most_weekly():
    """The accepted price of dropping the entities -> music hand-off (2026-09-24): a real
    SOP feed gap is reported by pipeline.yml after its imports and, a week past SOP's
    window, by the entities backstop too. Two threads, each weekly — never a silenced one."""
    gh, slack = FakeGitHub(), FakeSlack()
    sop_red = collect_findings(SOP, _steps(preflight="success", spotify_cache="success",
                                           pipeline="success", feed_check="failure"), "failure",
                               [_feed_failing("sop")])
    run_announce(SOP, sop_red, gh=gh, post=slack, today=SEP22, run_url=RUN)
    for n in range(8, 16):
        _run(gh, slack, _entities([_feed_failing("sop")]), SEP22 + timedelta(days=n))
    assert len(gh.issues) == 2
    assert len(slack.messages) == 3  # SOP's, the backstop's first (day 8), its weekly word (day 15)


def test_an_unverified_feed_neither_fails_nor_recovers():
    """Reviewer finding: the feed check warns only when a feed was unreachable. Read as
    a pass, a day of Taddy downtime closed an open BEHIND as 'recovered' and reopened it
    the next day as a new problem."""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief")]), SEP22)
    unverified = {"name": "import_caught_up_to_feed", "status": "warn",
                  "summary": "1 show(s) could not be verified against their feed.",
                  "details": ["ai-daily-brief: feed UNVERIFIED — second source unreachable"],
                  "failures": []}
    findings = collect_findings(ENTITIES, GREEN_STEPS, "success", [unverified])
    assert findings["entities:import_caught_up_to_feed"].unknown_subjects == ["ai-daily-brief"]
    _run(gh, slack, findings, SEP22 + timedelta(days=1))
    assert len(slack.messages) == 1 and gh.issues[101]["state"] == "open"


def test_a_different_shows_unreachable_feed_does_not_freeze_a_recovery():
    """Reviewer finding: if one feed stayed UNVERIFIED for weeks, every non-failing day
    would be a warn, and an open alert for ANOTHER show could never close."""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("hard-fork")]), SEP22)
    gabfest_down = {"name": "import_caught_up_to_feed", "status": "warn", "summary": "unverified",
                    "details": ["culture-gabfest: feed UNVERIFIED — second source unreachable",
                                "hard-fork: caught up (2026-09-22)"],
                    "failures": []}
    for day in (1, 2):  # held on the second green day
        _run(gh, slack, collect_findings(ENTITIES, GREEN_STEPS, "success", [gabfest_down]),
             SEP22 + timedelta(days=day))
    assert "recovered" in slack.messages[-1] and gh.issues[101]["state"] == "closed"


SOP = RunContext.build("music", "1")
TAL = RunContext.build("music", "2")


def _music_feed_red(ctx: RunContext, slug: str) -> dict:
    return collect_findings(ctx, _steps(preflight="success", spotify_cache="success",
                                        pipeline="success", feed_check="failure"), "failure",
                            [_feed_failing(slug)])


def _music_step_red(ctx: RunContext) -> dict:
    return collect_findings(ctx, _steps(preflight="success", spotify_cache="success",
                                        pipeline="failure", feed_check="skipped"), "failure")


def test_no_music_alert_ever_mutes_the_songs_check():
    """Review C1/C7/C10, high: the hand-off applied to every per-show check, so any active
    music-tal alert silenced 'tal: NO NEW SONGS' — the check built for the July TAL
    outage, which only the entities run evaluates."""
    songs = {"name": "music_songs_still_arriving", "status": "fail",
             "summary": "1 music show(s) have stopped acquiring songs: tal.",
             "details": ["tal: NO NEW SONGS in 30 days"], "failures": ["tal: NO NEW SONGS in 30 days"]}
    for owner in (_music_step_red(TAL), _music_feed_red(TAL, "tal")):
        gh, slack = FakeGitHub(), FakeSlack()
        run_announce(TAL, owner, gh=gh, post=slack, today=SEP22, run_url=RUN)
        out = _run(gh, slack, _entities([songs]), SEP22 + timedelta(days=3))
        assert len(slack.messages) == 2 and "stopped getting songs" in slack.messages[1]
        assert gh.alert("entities:music_songs_still_arriving")
        assert not any("to the music workflow" in a for a in out.actions)


def test_a_failed_recovery_comment_still_closes_the_issue():
    class NoComments(FakeGitHub):
        def comment(self, number, body):
            raise RuntimeError("HTTP 502")

    gh, slack = NoComments(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22)
    green = collect_findings(ENTITIES, GREEN_STEPS, "success", [_passing("import_caught_up_to_feed")])
    _run(gh, slack, green, SEP22 + timedelta(days=1))
    _run(gh, slack, green, SEP22 + timedelta(days=2))
    assert gh.issues[101]["state"] == "closed"
    assert sum("recovered" in m for m in slack.messages) == 1


def test_two_threads_for_one_key_are_merged_into_the_older():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22)
    twin = dict(gh.issues[101], number=150, html_url="https://x/150")
    gh.issues[150] = twin
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22 + timedelta(days=1))
    assert gh.issues[150]["state"] == "closed" and gh.issues[101]["state"] == "open"
    assert any(n == 150 and "#101 already tracks" in b for n, b in gh.comments)


def test_a_milder_warning_does_end_a_failure():
    """Other checks' warn is a real, milder state (some stand every day), so it ends
    the failure — otherwise an issue could never close."""
    gh, slack = FakeGitHub(), FakeSlack()
    stuck = {"name": "transcript_race_selfheal", "status": "fail", "summary": "not draining",
             "details": ["hard-fork ep 1: 5d pending"], "failures": []}
    draining = dict(stuck, status="warn", summary="2 episode(s) queued")
    _run(gh, slack, _entities([stuck]), SEP22)
    for day in (1, 2):
        _run(gh, slack, collect_findings(ENTITIES, GREEN_STEPS, "success", [draining]),
             SEP22 + timedelta(days=day))
    assert "recovered" in slack.messages[-1] and gh.issues[101]["state"] == "closed"


def test_notes_stay_visible_in_the_step_summary_when_nothing_is_said():
    gh, slack = FakeGitHub(), FakeSlack()
    green = collect_findings(ENTITIES, GREEN_STEPS, "success", [_passing("import_caught_up_to_feed")])
    out = _run(gh, slack, green, SEP22, notes=["Notion sync — incremental update: 2/10 failed (20%)"])
    assert slack.messages == []
    assert "2/10 failed" in announce._summarize(out)


def test_a_music_run_cannot_recover_another_shows_failure():
    gh, slack = FakeGitHub(), FakeSlack()
    sop = RunContext.build("music", "1")
    tal = RunContext.build("music", "2")
    red = collect_findings(sop, _steps(preflight="success", spotify_cache="success",
                                       pipeline="failure", feed_check="skipped"), "failure")
    run_announce(sop, red, gh=gh, post=slack, today=SEP22, run_url=RUN)
    green = collect_findings(tal, _steps(preflight="success", spotify_cache="success",
                                         pipeline="success", feed_check="success"), "success",
                             [_passing("import_caught_up_to_feed")])
    run_announce(tal, green, gh=gh, post=slack, today=SEP22 + timedelta(days=5), run_url=RUN)

    assert len(slack.messages) == 1
    assert gh.issues[101]["state"] == "open"
    assert "SOP music run" in slack.messages[0]


def test_a_message_that_did_not_land_is_retried_the_next_day():
    gh = FakeGitHub()
    _run(gh, FakeSlack(ok=False), _entities([AI_DAILY_BEHIND]), SEP22)
    assert gh.alert("entities:import_caught_up_to_feed").last_posted is None

    slack = FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22 + timedelta(days=1))
    # Review L5: the first message Kevin actually receives reads as a new problem, with
    # its usual cause, not as a reminder of something he never heard.
    assert len(slack.messages) == 1
    msg = slack.messages[0]
    assert "first announced today; failing since Sep 22" in msg and "_Usually:_" in msg
    assert gh.alert("entities:import_caught_up_to_feed").last_posted == SEP22 + timedelta(days=1)


def test_a_dry_run_reads_but_writes_nothing():
    gh, slack = FakeGitHub(), FakeSlack()
    out = _run(gh, slack, _entities([AI_DAILY_BEHIND]), SEP22, dry_run=True)
    assert slack.messages == [] and gh.writes == []
    assert out.message and "1 new problem" in out.message
    assert any(a.startswith("would open issue") for a in out.actions)


def test_the_old_failure_thread_is_retired_on_the_first_run():
    legacy = {"number": 64, "title": "Entity pipeline failure (2026-09-14)", "state": "open",
              "body": "The scheduled entity pipeline run failed.",
              "labels": [{"name": "pipeline-failure"}, {"name": "entities"}],
              "html_url": "https://github.com/khglynn/list-maker/issues/64",
              "created_at": "2026-09-14T21:00:00Z"}
    music_legacy = dict(legacy, number=57, labels=[{"name": "pipeline-failure"}, {"name": "music"}])
    gh, slack = FakeGitHub([legacy, music_legacy]), FakeSlack()
    green = collect_findings(ENTITIES, GREEN_STEPS, "success", [_passing("import_caught_up_to_feed")])
    _run(gh, slack, green, date(2026, 9, 24))

    assert gh.issues[64]["state"] == "closed"
    assert gh.issues[57]["state"] == "open"  # a music thread is the music run's to retire
    body = next(b for n, b in gh.comments if n == 64)
    assert "one issue per failing check" in body and "Everything this run checks is passing" in body
    assert slack.messages == []  # retiring an old thread is not news


def test_when_the_memory_cannot_be_read_failures_are_still_said():
    slack = FakeSlack()
    out = _run(FakeGitHub(fail_reads=True), slack, _entities([AI_DAILY_BEHIND]), SEP22)
    assert len(slack.messages) == 1 and "may repeat tomorrow" in slack.messages[0]
    assert out.posted

    quiet = FakeSlack()
    green = collect_findings(ENTITIES, GREEN_STEPS, "success", [_passing("import_caught_up_to_feed")])
    _run(FakeGitHub(fail_reads=True), quiet, green, SEP22)
    assert quiet.messages == []


def test_a_failed_github_write_does_not_cost_the_message():
    class BrokenWrites(FakeGitHub):
        def create_issue(self, *a, **k):
            raise RuntimeError("HTTP 403")

    slack = FakeSlack()
    out = _run(BrokenWrites(), slack, _entities([AI_DAILY_BEHIND]), SEP22)
    assert len(slack.messages) == 1 and "(issue not created)" in slack.messages[0]
    assert any("FAILED: HTTP 403" in a for a in out.actions)


def test_detail_text_cannot_break_slack_markup():
    finding = Finding("entities:x", "x", CHECK_GUIDES["sponsor_share"], True, "a < b & c > d",
                      ["<!channel> & friends"])
    msg = render_slack(ENTITIES, decide(ENTITIES, {finding.key: finding}, {}, SEP22), SEP22, RUN, {})
    assert "a &lt; b &amp; c &gt; d" in msg and "&lt;!channel&gt; &amp; friends" in msg


# ── every alert has words ───────────────────────────────────────────────────────────────

def test_every_health_check_has_a_plain_words_guide(monkeypatch):
    """A check added to data_health without an entry in alert_guides would reach Slack
    as its bare function name. Run the real check list against an empty database to
    collect every name it can produce."""
    import pipeline.data_health as dh

    monkeypatch.setattr(dh, "_rows", lambda *a, **k: [])
    monkeypatch.setattr(dh, "_one", lambda *a, **k: {})
    monkeypatch.setattr(dh, "feed_recent_dates", lambda cfg, limit=15: None)
    monkeypatch.setattr(dh, "feed_recent_episodes", lambda cfg, limit=15: None)
    names = {r.name for r in dh.run_checks(conn=None, include_feed_check=True)}
    assert names, "no checks collected"
    assert names <= set(CHECK_GUIDES), sorted(names - set(CHECK_GUIDES))


def test_every_tracked_step_has_a_guide():
    for workflow, spec in announce.WORKFLOWS.items():
        for step_id in spec["steps"]:
            assert f"{workflow}:{step_id}" in STEP_GUIDES


# ── the pieces that feed it ─────────────────────────────────────────────────────────────

def test_data_health_writes_results_for_the_announcer_and_posts_nothing(monkeypatch, tmp_path):
    import sys

    import pipeline.data_health as dh
    from pipeline.data_health import CheckResult

    results_path = tmp_path / "alerts" / "health.json"
    monkeypatch.setattr(sys, "argv", ["data_health.py", "--strict", "--results-file", str(results_path)])
    monkeypatch.setattr(dh, "load_environment", lambda: None)
    monkeypatch.setattr(dh, "get_db_connection", lambda: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(dh, "run_checks", lambda conn, **kw: [CheckResult("sponsor_share", "fail", "s", ["d"])])
    monkeypatch.setattr(dh, "check_optional_null_map", lambda conn: CheckResult("optional_null_map", "pass", "", []))
    assert not hasattr(dh, "post_slack"), "data_health must not post to Slack itself"

    with pytest.raises(SystemExit):
        dh.main()
    written = json.loads(results_path.read_text())
    assert [r["name"] for r in written] == ["sponsor_share", "optional_null_map"]


def test_preflight_leaves_diagnostics_for_the_announcer_instead_of_posting(monkeypatch, tmp_path):
    from pipeline import db_preflight

    posted: list[str] = []
    monkeypatch.setenv("ALERT_DETAILS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setattr(db_preflight, "load_environment", lambda: None)
    monkeypatch.setattr(db_preflight, "post_slack", lambda text: posted.append(text) or True)
    monkeypatch.setattr(db_preflight, "check",
                        lambda url: (False, ":rotating_light: *headline*\nhost `h` · runner `r`\n`boom`"))
    with pytest.raises(SystemExit):
        db_preflight.main()
    assert posted == []
    assert (tmp_path / "preflight.txt").read_text() == "host `h` · runner `r`\n`boom`"


def test_alert_note_rides_with_the_announcer_when_there_is_one(monkeypatch, tmp_path):
    from pipeline import common

    posted: list[str] = []
    monkeypatch.setattr(common, "post_slack", lambda text: posted.append(text) or True)
    monkeypatch.setenv("ALERT_DETAILS_DIR", str(tmp_path))
    assert common.alert_note(":warning: Notion sync — 2/10 failed")
    assert (tmp_path / "notes.txt").read_text() == ":warning: Notion sync — 2/10 failed\n"
    assert posted == []

    monkeypatch.delenv("ALERT_DETAILS_DIR")
    common.alert_note("local run")
    assert posted == ["local run"]


def test_main_reads_what_the_steps_left_and_summarizes(monkeypatch, tmp_path, capsys):
    (tmp_path / "health.json").write_text(json.dumps([AI_DAILY_BEHIND]))
    (tmp_path / "notes.txt").write_text("Notion sync — 1/40 failed\n")
    monkeypatch.setenv("ALERT_DETAILS_DIR", str(tmp_path))
    monkeypatch.setenv("STEPS_JSON", json.dumps(RED_HEALTH_STEPS))
    monkeypatch.setenv("JOB_STATUS", "failure")
    monkeypatch.setenv("RUN_URL", RUN)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)  # no memory → the loud fallback
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    slack = FakeSlack()
    monkeypatch.setattr(announce, "post_slack", slack)

    assert announce.main(["--workflow", "entities"]) == 0
    assert len(slack.messages) == 1
    assert "ai-daily-brief: BEHIND 1" in slack.messages[0]
    assert "Notion sync — 1/40 failed" in slack.messages[0]
    assert "## Alerts" in capsys.readouterr().out


def test_a_crash_in_the_announcer_is_loud(monkeypatch):
    monkeypatch.setenv("STEPS_JSON", "{not json")
    monkeypatch.setenv("RUN_URL", RUN)
    slack = FakeSlack()
    monkeypatch.setattr(announce, "post_slack", slack)
    assert announce.main(["--workflow", "intake"]) == 1
    assert "alert step itself crashed" in slack.messages[0]


# ── round 3: flapping, reopening, and the paths that must never go silent ──────────────

def _day_after(n: int) -> date:
    return SEP22 + timedelta(days=n)


def test_a_flapping_check_is_one_problem_said_at_most_weekly():
    """Review C4: red, green, red, green, red on five days gave 5 posts and 3 issues.
    Kevin's rule: start, weekly while unfixed, once on recovery, never daily."""
    gh, slack = FakeGitHub(), FakeSlack()
    red, green = _entities([AI_DAILY_BEHIND]), _green("import_caught_up_to_feed")
    for n in range(7):  # Sep 22-28: F P F P F P F
        _run(gh, slack, red if n % 2 == 0 else green, _day_after(n))
    assert len(slack.messages) == 1  # the start, then quiet
    assert len(gh.issues) == 1 and gh.issues[101]["state"] == "open"

    _run(gh, slack, red, _day_after(7))  # a week after the first post
    assert len(slack.messages) == 2
    assert "on and off: failed 4 of the last 7 runs" in slack.messages[1]  # Sep 23-29: P F P F P F F
    assert len(gh.issues) == 1


def test_a_failure_soon_after_a_recovery_reopens_the_same_issue_quietly():
    gh, slack = FakeGitHub(), FakeSlack()
    red, green = _entities([AI_DAILY_BEHIND]), _green("import_caught_up_to_feed")
    _run(gh, slack, red, _day_after(0))
    _run(gh, slack, green, _day_after(1))
    _run(gh, slack, green, _day_after(2))  # held: "recovered", closed
    assert len(slack.messages) == 2 and gh.issues[101]["state"] == "closed"

    _run(gh, slack, red, _day_after(4))  # failing again two days later
    assert len(slack.messages) == 2  # quiet: same problem, next word is the reminder
    assert gh.issues[101]["state"] == "open" and len(gh.issues) == 1
    assert any(n == 101 and "Failing again on 2026-09-26" in b for n, b in gh.comments)

    _run(gh, slack, red, _day_after(9))  # 7 days after the "recovered" post
    assert len(slack.messages) == 3 and "failing again" in slack.messages[2]
    assert "first failed Sep 22" in slack.messages[2]


def test_an_issue_closed_by_hand_is_not_reopened():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), _day_after(0))
    gh.issues[101]["state"] = "closed"  # Kevin: "I think it's fixed"
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), _day_after(1))
    assert len(gh.issues) == 2 and gh.issues[102]["state"] == "open"
    assert len(slack.messages) == 2 and "1 new problem" in slack.messages[1]


def test_a_recovery_that_was_not_announced_keeps_the_issue_open():
    """Review C12: closing without the message landing meant 'recovered' was never said."""
    gh = FakeGitHub()
    _run(gh, FakeSlack(), _entities([AI_DAILY_BEHIND]), _day_after(0))
    green = _green("import_caught_up_to_feed")
    _run(gh, FakeSlack(), green, _day_after(1))
    _run(gh, FakeSlack(ok=False), green, _day_after(2))
    assert gh.issues[101]["state"] == "open"
    slack = FakeSlack()
    _run(gh, slack, green, _day_after(3))
    assert len(slack.messages) == 1 and "recovered" in slack.messages[0]
    assert gh.issues[101]["state"] == "closed"


def test_a_failed_close_after_recovered_finishes_quietly():
    """Review L3: the message landed but the close didn't; the next run closes without
    saying 'recovered' again."""
    class NoClose(FakeGitHub):
        closes_left_to_fail = 1

        def edit_issue(self, number, **fields):
            if fields.get("state") == "closed" and self.closes_left_to_fail:
                self.closes_left_to_fail -= 1
                raise RuntimeError("HTTP 502")
            super().edit_issue(number, **fields)

    gh, slack = NoClose(), FakeSlack()
    _run(gh, slack, _entities([AI_DAILY_BEHIND]), _day_after(0))
    green = _green("import_caught_up_to_feed")
    _run(gh, slack, green, _day_after(1))
    _run(gh, slack, green, _day_after(2))  # the close fails once and is retried in the same run
    assert gh.issues[101]["state"] == "closed" and "recovered" in slack.messages[-1]
    _run(gh, slack, green, _day_after(3))
    assert len(slack.messages) == 2


def test_an_unreachable_feed_still_gets_its_weekly_reminder():
    """Review C9: an open BEHIND whose feed stopped answering went silent for good."""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("tal")]), _day_after(0))
    down = {"name": "import_caught_up_to_feed", "status": "warn", "summary": "unverified",
            "details": ["tal: feed UNVERIFIED — second source unreachable"], "failures": []}
    for n in range(1, 22):
        _run(gh, slack, collect_findings(ENTITIES, GREEN_STEPS, "success", [down]), _day_after(n))
    assert len(slack.messages) == 4  # day 0, then days 7, 14 and 21
    assert "Couldn't check today: the feed for tal didn't answer" in slack.messages[1]
    assert gh.issues[101]["state"] == "open"


def test_an_unreachable_show_on_a_fail_day_is_not_forgotten():
    """Review C2/L11: tal behind, ai-daily-brief's feed down for a day while tal still
    fails. The next day both behind again must not read as 'now also failing'."""
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief", "tal")]), _day_after(0))
    blip = _feed_failing("tal")
    blip["details"] = blip["details"] + ["ai-daily-brief: feed UNVERIFIED — second source unreachable"]
    _run(gh, slack, _entities([blip]), _day_after(1))
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief", "tal")]), _day_after(2))
    assert len(slack.messages) == 1


def test_a_manual_all_shows_music_run_posts_but_keeps_no_issue():
    """Review C5/L9/L14: no schedule re-checks show_id 'all' or '3', so an issue from
    one of those runs would never be reminded or closed."""
    for show_id in ("all", "3"):
        ctx = RunContext.build("music", show_id)
        assert ctx.stateless
        gh, slack = FakeGitHub(), FakeSlack()
        red = collect_findings(ctx, _steps(preflight="success", spotify_cache="success",
                                           pipeline="failure", feed_check="skipped"), "failure")
        run_announce(ctx, red, gh=gh, post=slack, today=SEP22, run_url=RUN)
        assert gh.issues == {} and len(slack.messages) == 1
        assert "not tracked" in slack.messages[0]
        green = collect_findings(ctx, _steps(preflight="success", spotify_cache="success",
                                             pipeline="success", feed_check="success"), "success")
        run_announce(ctx, green, gh=gh, post=slack, today=SEP22, run_url=RUN)
        assert len(slack.messages) == 1


def test_an_issue_for_a_check_that_no_longer_exists_is_closed():
    """Review C5/F12: a renamed or removed check's issue would stay open forever."""
    gh, slack = FakeGitHub(), FakeSlack()
    old = {"name": "old_check_name", "status": "fail", "summary": "x", "details": [], "failures": []}
    _run(gh, slack, _entities([old]), _day_after(0))
    green = _green("import_caught_up_to_feed")
    _run(gh, slack, green, _day_after(1), reported_checks={"import_caught_up_to_feed"})
    assert gh.issues[101]["state"] == "closed" and gh.issues[101]["state_reason"] == "not_planned"
    assert len(slack.messages) == 1


def test_a_hand_filed_failure_issue_is_never_retired():
    """Review L2: only issues from before the switch are legacy."""
    hand = {"number": 70, "title": "Pipeline question", "state": "open", "body": "Kevin's note",
            "labels": [{"name": "pipeline-failure"}, {"name": "entities"}],
            "html_url": "https://x/70", "created_at": "2026-10-01T12:00:00Z"}
    gh = FakeGitHub([hand])
    _run(gh, FakeSlack(), _green("import_caught_up_to_feed"), date(2026, 10, 2))
    assert gh.issues[70]["state"] == "open" and gh.comments == []


def test_the_no_memory_fallback_says_it_may_be_a_repeat():
    """Review L4: without the memory every failure looks new; the headline says so."""
    slack = FakeSlack()
    _run(FakeGitHub(fail_reads=True), slack, _entities([AI_DAILY_BEHIND]), _day_after(20))
    assert "alert history unavailable, so this may be a repeat" in slack.messages[0]
    assert "new problem" not in slack.messages[0]


def test_an_import_failure_still_posts_the_crash_line(monkeypatch):
    """Review L10: announce.py's own imports are guarded, so a broken show_config still
    produces 'the alert step itself crashed' instead of a traceback nobody sees."""
    slack = FakeSlack()
    monkeypatch.setattr(announce, "_IMPORT_ERROR", "Traceback: SyntaxError in show_config.py")
    monkeypatch.setattr(announce, "post_slack", slack)
    monkeypatch.setenv("RUN_URL", RUN)
    assert announce.main(["--workflow", "entities"]) == 1
    assert "alert step itself crashed" in slack.messages[0]


# ── round 4: Codex's review of round 3, reproduced before fixing ────────────────────────
# Each starts as a strict xfail against the round-3 announcer (7e1be63): it must FAIL
# there, which is the reproduction. The markers come off as the fixes land.

def _intake_ctx():
    return RunContext.build("intake")


def _intake(outcome: str):
    ctx = _intake_ctx()
    job = "success" if outcome == "success" else "failure"
    return ctx, collect_findings(ctx, _steps(preflight="success", log_schema="success", intake=outcome), job)


def test_r4_1_a_thread_is_never_closed_while_one_of_its_shows_is_unverified():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief", "tal")]), _day_after(0))
    tal_owner = collect_findings(TAL, _steps(preflight="success", spotify_cache="success",
                                             pipeline="success", feed_check="failure"), "failure",
                                 [_feed_failing("tal")])
    run_announce(TAL, tal_owner, gh=gh, post=slack, today=_day_after(0), run_url=RUN)
    today = _feed_failing("tal")
    today["details"] = today["details"] + ["ai-daily-brief: feed UNVERIFIED — second source unreachable"]
    _run(gh, slack, _entities([today]), _day_after(1))
    assert gh.issues[101]["state"] == "open"
    before = len(slack.messages)
    _run(gh, slack, _entities([today]), _day_after(7))
    assert len(slack.messages) == before + 1 and "#101" in slack.messages[-1]


def test_r4_2_a_weekly_check_that_relapses_says_so():
    gh, slack = FakeGitHub(), FakeSlack()
    for n, outcome in enumerate(["failure", "success", "failure", "success", "failure"]):
        ctx, findings = _intake(outcome)
        run_announce(ctx, findings, gh=gh, post=slack, today=_day_after(7 * n), run_url=RUN)
    assert len(slack.messages) == 5  # failing, recovered, failing again, recovered, failing again
    assert slack.messages[2].startswith(":rotating_light: *list-maker · weekly curated intake* — failing again")


def test_r4_3_a_cancel_is_said_even_when_another_failure_is_already_open():
    gh, slack = FakeGitHub(), FakeSlack()
    red = collect_findings(ENTITIES, _steps(preflight="success", **{"import": "failure"},
                                            notion="success", health="success"), "failure",
                           [_passing("import_caught_up_to_feed")])
    _run(gh, slack, red, _day_after(0))
    cancelled = collect_findings(ENTITIES, _steps(preflight="success", **{"import": "failure"},
                                                  notion="cancelled", health="skipped"), "cancelled")
    _run(gh, slack, cancelled, _day_after(1))
    assert len(slack.messages) == 2 and "cancelled" in slack.messages[1]


def test_r4_4_an_open_alert_the_run_never_reaches_still_gets_its_weekly_reminder():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([NOTION_DRIFT]), _day_after(0))
    down = collect_findings(ENTITIES, _steps(preflight="failure", **{"import": "skipped"},
                                             notion="skipped", health="skipped"), "failure")
    for n in range(1, 8):
        _run(gh, slack, down, _day_after(n))
    assert any("Notion is behind Neon" in m and "couldn't check" in m.lower() for m in slack.messages[1:])


def test_r4_5_a_fail_pass_pass_cycle_is_not_announced_every_few_days():
    gh, slack = FakeGitHub(), FakeSlack()
    red, green = _entities([AI_DAILY_BEHIND]), _green("import_caught_up_to_feed")
    days_recovered = []
    for n in range(15):
        before = len(slack.messages)
        _run(gh, slack, red if n % 3 == 0 else green, _day_after(n))
        if len(slack.messages) > before and slack.messages[-1].startswith(":white_check_mark:"):
            days_recovered.append(n)
    assert all(b - a >= 7 for a, b in zip(days_recovered, days_recovered[1:])), days_recovered


def test_r4_6_an_unverified_day_does_not_count_toward_a_daily_recovery():
    gh, slack = FakeGitHub(), FakeSlack()
    _run(gh, slack, _entities([_feed_failing("ai-daily-brief")]), _day_after(0))
    unverified = {"name": "import_caught_up_to_feed", "status": "warn", "summary": "unverified",
                  "details": ["ai-daily-brief: feed UNVERIFIED — second source unreachable"], "failures": []}
    _run(gh, slack, collect_findings(ENTITIES, GREEN_STEPS, "success", [unverified]), _day_after(1))
    _run(gh, slack, _green("import_caught_up_to_feed"), _day_after(2))
    assert len(slack.messages) == 1 and gh.issues[101]["state"] == "open"
    _run(gh, slack, _green("import_caught_up_to_feed"), _day_after(3))
    assert "recovered" in slack.messages[-1]


def test_r4_7_a_relapse_after_a_half_saved_recovery_is_not_a_new_problem():
    class FlakyBodies(FakeGitHub):
        fail_body_only = False

        def edit_issue(self, number, **fields):
            if self.fail_body_only and "body" in fields and "state" not in fields:
                raise RuntimeError("HTTP 502")
            super().edit_issue(number, **fields)

    gh, slack = FlakyBodies(), FakeSlack()
    red, green = _entities([AI_DAILY_BEHIND]), _green("import_caught_up_to_feed")
    _run(gh, slack, red, _day_after(0))
    _run(gh, slack, green, _day_after(1))
    gh.fail_body_only = True
    _run(gh, slack, green, _day_after(2))  # "recovered" posted; the marker write fails
    gh.fail_body_only = False
    _run(gh, slack, red, _day_after(3))
    assert len(gh.issues) == 1
    assert not any("new problem" in m for m in slack.messages[2:])


def test_a_problem_never_announced_recovers_quietly():
    """If the first message never landed and it recovers before the next try, 'recovered'
    about a problem Kevin never heard of would only confuse."""
    gh = FakeGitHub()
    _run(gh, FakeSlack(ok=False), _entities([AI_DAILY_BEHIND]), _day_after(0))
    slack = FakeSlack()
    for n in (1, 2):
        _run(gh, slack, _green("import_caught_up_to_feed"), _day_after(n))
    assert slack.messages == [] and gh.issues[101]["state"] == "closed"

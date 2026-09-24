#!/usr/bin/env python3
"""The one voice of list-maker's scheduled workflows: Slack and the failure issues.

WHY THIS EXISTS. Until 2026-09-23 each workflow spoke from several places at once. A red
entities run posted the data-health line, then "entity pipeline FAILED" one second
later, then commented "Failed again" on a failure issue that never closed — every day
the condition held. From June 8 to August 26 the channel carried the same data-health
line almost daily for eleven weeks. A check that talks that much trains its reader to
ignore it, which is worse than no check (Kevin's legibility standard).

THE RULE (Kevin, 2026-09-23): say it when a check starts failing (or the set of failing
checks changes); again every 7 days while it is unfixed; once on recovery; never daily.
It matches fleet-release-watch.yml in khglynn/google_workspace_mcp (once, then weekly).

ONE INVARIANT carries that rule, per key (a data_health check, or a tracked step, in one
workflow — or one music show's runs). A post about a key is one of:
  1. its first failure                  → "N new problem(s)", and an issue of its own
  2. a weekly word, ≥7 days since the   → "still failing since Sep 22 (8 days)", or
     key's last post                      "failing again" (it relapsed after "recovered"),
                                          or "couldn't check today" when this run didn't
                                          evaluate it (a skipped step, a feed that didn't
                                          answer) — an open problem never goes silent
  3. a verified recovery, and only if   → "recovered", and the issue closes
     the last thing said was "failing"
Nothing else posts. So a check that flaps is one problem said at most weekly (plus one
"recovered" per announced failure); a relapse soon after "recovered" reopens the same
issue quietly and is said at the next weekly word.

"Verified" means consecutive passing evaluations: 2 for the daily entities run (one
green day proves little for a check hovering at its threshold), 1 for the weekly and
twice-weekly workflows. A run that didn't evaluate the key doesn't count either way.

WHAT WAS REMOVED ON 2026-09-24, deliberately. Three review rounds kept finding holes
where features met: a hand-off that let the entities run defer a music show's feed alert
to pipeline.yml (it could silence checks), "now also failing for <show>" news inside an
open check (Kevin's rule is per failing check; the weekly word lists the shows), and a
time-based recovery hold. The model above has fewer states and says the same things.
The price: one music show's feed gap can have two threads (pipeline.yml's and the
entities backstop's), each posting at most weekly.

WHY THE ISSUES ARE THE STATE. They already existed as the failure thread, persist with no
infrastructure, and a person can read them. Each carries a hidden JSON marker (key,
first failed, last posted, what was last said, green streak, the last 14 results, the
failing shows). An orphan state branch would have meant `contents: write` on workflows
that hold every pipeline secret. A recovery writes the marker and closes the issue in one
edit, so the two can't disagree. Closing an issue by hand means "I think it's fixed": it
is never reopened, and if the problem is still there the next run opens a fresh one.

Standard library only, so it still runs when the dependency install is what failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Guarded: if an import breaks (a later edit to alert_guides, say), main() still posts
# "the alert step itself crashed" instead of dying with a traceback nobody sees.
try:
    from alert_guides import CHECK_GUIDES, RUN_GUIDE, STEP_GUIDES, Guide, fallback_guide  # noqa: E402
    _IMPORT_ERROR: Optional[str] = None
except Exception:  # noqa: BLE001
    _IMPORT_ERROR = traceback.format_exc()

REMIND_AFTER_DAYS = 7
HISTORY_LEN = 14
MARKER = "list-maker-alert"
_MARKER_RE = re.compile(r"<!--\s*" + MARKER + r"\s+(\{.*?\})\s*-->", re.S)

# Issues from before this announcer (#64) are retired on first contact. Only issues this
# old: a failure issue someone files by hand later must never be auto-closed.
LEGACY_BEFORE = datetime(2026, 9, 24, tzinfo=timezone.utc)

# Per workflow: the step ids this announcer tracks (each workflow file gives these ids),
# which of them writes data_health results, the issue label, and how many consecutive
# passing evaluations make a recovery (the daily run needs two; the others run weekly or
# twice a week, so their first green run is already days after the failure).
WORKFLOWS: dict[str, dict[str, Any]] = {
    "entities": {
        "steps": ("preflight", "import", "notion", "health"),
        "health_step": "health",
        "label": "entities",
        "green_to_recover": 2,
    },
    "music": {
        "steps": ("preflight", "spotify_cache", "pipeline", "feed_check"),
        "health_step": "feed_check",
        "label": "music",
        "green_to_recover": 1,
    },
    "intake": {
        "steps": ("preflight", "log_schema", "intake"),
        "health_step": None,
        "label": "intake",
        "green_to_recover": 1,
    },
}

# pipeline.yml's show_id → the show a music run evaluates. Only the scheduled ones (the
# Worker dispatches 1 on Wed/Fri and 2 on Mondays) are TRACKED: nothing re-checks "all"
# or "3", so an issue opened by one of those manual runs would never be reminded or
# closed. They post, and keep no memory.
MUSIC_SHOWS = {"1": ("sop", "SOP"), "2": ("tal", "TAL"), "3": ("ai-daily-brief", "AI Daily"),
               "all": ("all", "all-shows")}
TRACKED_MUSIC_SHOW_IDS = frozenset({"1", "2"})

# A check whose "warn" can mean "couldn't find out" rather than "a milder problem": the
# feed check warns when a show's feed didn't answer (UNVERIFIED). If a show the open
# issue is failing for is among them, this run didn't evaluate the key. Every other
# check's warn is a real, milder state (some stand every day), so it counts as a pass.
UNVERIFIED_WHEN_WARN = frozenset({"import_caught_up_to_feed"})

_SUBJECT_RE = re.compile(r"^([a-z0-9][a-z0-9-]*):\s")

DETAIL_LINES_IN_SLACK = 3
DETAIL_LINES_IN_ISSUE = 20
DETAIL_CHARS = 400


# ── what this run evaluated ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """One thing this run evaluated, passing or failing."""

    key: str  # "entities:import_caught_up_to_feed", "music-sop:step:pipeline", "intake:run"
    name: str  # the check name or step id, for the reader
    guide: Guide
    failing: bool
    summary: str = ""
    details: list[str] = field(default_factory=list)
    # Which shows (or "general") a per-show check is failing for; empty for steps.
    subjects: list[str] = field(default_factory=list)
    # Shows this check couldn't find out about this run (a feed that didn't answer).
    unknown_subjects: list[str] = field(default_factory=list)


def failure_subjects(failure_lines: list[str]) -> list[str]:
    """The shows a check's failing lines are about ("sop: BEHIND 1 …" → "sop"); a failing
    line with no show prefix counts as "general"."""
    subjects = set()
    for line in failure_lines:
        match = _SUBJECT_RE.match(str(line))
        subjects.add(match.group(1) if match else "general")
    return sorted(subjects)


@dataclass
class RunContext:
    workflow: str
    prefix: str  # the key namespace: the workflow, or "music-<show>"
    label: str  # "daily entities run"
    stateless: bool = False  # a manual scope no schedule re-checks: post, remember nothing

    @classmethod
    def build(cls, workflow: str, show_id: Optional[str] = None) -> "RunContext":
        if workflow == "music":
            sid = str(show_id)
            slug, name = MUSIC_SHOWS.get(sid, (sid, sid))
            return cls(workflow, f"music-{slug}", f"{name} music run",
                       stateless=sid not in TRACKED_MUSIC_SHOW_IDS)
        label = {"entities": "daily entities run", "intake": "weekly curated intake"}[workflow]
        return cls(workflow, workflow, label)


def collect_findings(
    ctx: RunContext,
    steps: dict[str, dict],
    job_status: str,
    health: Optional[list[dict]] = None,
    preflight_detail: Optional[str] = None,
) -> dict[str, Finding]:
    """Everything this run evaluated, keyed for the issue memory.

    A step counts only if it ran (success or failure); skipped or cancelled steps say
    nothing about themselves, so an unreachable database can't "recover" a check that
    never ran. When the health step reported, each data_health check is its own finding
    and the step's own finding only fails if it broke without a failing check to explain
    it. A cancelled run is always a failing `run` finding: a job cut off by its time limit
    ends "cancelled", the shape that hid the May–June 2026 playlist outage.
    """
    spec = WORKFLOWS[ctx.workflow]
    findings: dict[str, Finding] = {}

    def add(name: str, guide: Guide, failing: bool, summary: str = "", details=None,
            subjects=None, unknown=None) -> None:
        key = f"{ctx.prefix}:{name}"
        findings[key] = Finding(key, name, guide, failing, summary, list(details or []),
                                list(subjects or []), list(unknown or []))

    for step_id in spec["steps"]:
        outcome = (steps.get(step_id) or {}).get("outcome")
        if outcome not in ("success", "failure"):
            continue
        guide = STEP_GUIDES[f"{ctx.workflow}:{step_id}"]
        if step_id == spec["health_step"] and health is not None:
            any_check_failed = False
            for result in health:
                name = str(result.get("name"))
                status = result.get("status")
                failing = status == "fail"
                any_check_failed |= failing
                unknown: list[str] = []
                if name in UNVERIFIED_WHEN_WARN:
                    unknown = failure_subjects(
                        [d for d in result.get("details") or [] if "UNVERIFIED" in str(d)]
                    )
                add(name, CHECK_GUIDES.get(name) or fallback_guide(name), failing,
                    str(result.get("summary") or ""), result.get("details") or [],
                    failure_subjects(result.get("failures") or []) if failing else [], unknown)
            add(f"step:{step_id}", guide, outcome == "failure" and not any_check_failed)
            continue
        details = []
        if step_id == "preflight" and outcome == "failure" and preflight_detail:
            details = [line for line in preflight_detail.splitlines() if line.strip()]
        add(f"step:{step_id}", guide, outcome == "failure", details=details)

    # The `run` key: did the run itself end the way a run should? A cancel is always a
    # failure of its own (review R3, 2026-09-24: hidden behind an import failure that was
    # already open, a timeout said nothing for up to a week). A failure no tracked step
    # explains is one too. A failure a tracked step explains is that step's news, so the
    # run itself counts as having ended normally.
    tracked_failure = any(f.failing for f in findings.values())
    if job_status == "cancelled":
        add("run", RUN_GUIDE, True,
            "The run was cancelled before it finished — usually a time limit. If someone "
            "stopped it by hand, the next run clears this.")
    elif job_status == "failure" and not tracked_failure:
        add("run", RUN_GUIDE, True, "The run failed, but in none of the steps this alert tracks.")
    elif job_status in ("success", "failure"):
        add("run", RUN_GUIDE, False)
    return findings


def reported_check_names(ctx: RunContext, steps: dict[str, dict],
                         health: Optional[list[dict]]) -> Optional[set[str]]:
    """The data_health checks this run reported on, or None if the health step didn't
    report. Used to close issues for checks that no longer exist (renamed or removed)."""
    health_step = WORKFLOWS[ctx.workflow]["health_step"]
    if not health_step or health is None:
        return None
    if (steps.get(health_step) or {}).get("outcome") not in ("success", "failure"):
        return None
    return {str(r.get("name")) for r in health}


# ── the memory: issues ──────────────────────────────────────────────────────────────────

@dataclass
class Alert:
    """A key's memory, read from an issue's hidden marker."""

    number: int
    url: str
    key: str
    since: date  # first failure of this thread
    last_posted: Optional[date]  # the last time Slack heard about this key
    said: str  # what Slack was last told: "failing" or "recovered"
    green: int  # consecutive passing evaluations
    history: str  # the last HISTORY_LEN evaluations, oldest first: F(ail) / P(ass)
    subjects: list[str]  # the shows it was failing for at its last failing run
    runs: int  # failing evaluations in this thread
    closed: bool = False

    @property
    def name(self) -> str:
        return self.key.split(":", 1)[1] if ":" in self.key else self.key


def _created(issue: dict) -> Optional[datetime]:
    raw = issue.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_alert(issue: dict) -> Optional[Alert]:
    """The alert state carried by an issue's hidden marker, or None for any other issue."""
    match = _MARKER_RE.search(issue.get("body") or "")
    if not match:
        return None
    try:
        state = json.loads(match.group(1))
        return Alert(
            number=int(issue["number"]),
            url=str(issue.get("html_url") or ""),
            key=str(state["key"]),
            since=date.fromisoformat(state["since"]),
            last_posted=date.fromisoformat(state["last_posted"]) if state.get("last_posted") else None,
            said=str(state.get("said") or "failing"),
            green=int(state.get("green") or 0),
            history=str(state.get("history") or ""),
            subjects=[str(x) for x in state.get("subjects") or []],
            runs=int(state.get("runs") or 1),
            closed=issue.get("state") == "closed",
        )
    except (ValueError, KeyError, TypeError):
        return None


def is_legacy_issue(issue: dict, label: str) -> bool:
    """A failure thread from before this announcer: the pipeline-failure label plus this
    workflow's label, no marker, and created before the switch. #64 is the one still open
    on 2026-09-23."""
    names = {lbl.get("name") for lbl in issue.get("labels") or [] if isinstance(lbl, dict)}
    created = _created(issue)
    return ({"pipeline-failure", label} <= names and parse_alert(issue) is None
            and created is not None and created < LEGACY_BEFORE)


# ── the decision ────────────────────────────────────────────────────────────────────────

# What happens to one key this run. Posting kinds: new, remind, again, unchecked, recovered.
POSTING = ("new", "remind", "again", "unchecked", "recovered")


@dataclass
class Item:
    kind: str  # new | remind | again | unchecked | recovered | quiet_fail | quiet_pass | quiet_close
    finding: Finding  # today's finding (or a stand-in for a key this run didn't reach)
    alert: Optional[Alert] = None
    reason: str = ""  # for "unchecked": why it couldn't be checked


def _due(last_posted: Optional[date], today: date) -> bool:
    return last_posted is None or (today - last_posted).days >= REMIND_AFTER_DAYS


def evaluation(finding: Optional[Finding], alert: Optional[Alert]) -> Optional[str]:
    """This run's result for a key: "F", "P", or None when it wasn't evaluated — no
    finding at all, or a feed check that couldn't see a show the open issue is about."""
    if finding is None:
        return None
    if finding.failing:
        return "F"
    unknown = set(finding.unknown_subjects)
    if unknown and alert is not None and (not alert.subjects or unknown & set(alert.subjects)):
        return None
    return "P"


def _guide_for(ctx: "RunContext", name: str) -> Guide:
    if name == "run":
        return RUN_GUIDE
    if name.startswith("step:"):
        return STEP_GUIDES.get(f"{ctx.workflow}:{name[5:]}") or fallback_guide(name)
    return CHECK_GUIDES.get(name) or fallback_guide(name)


def decide(ctx: "RunContext", findings: dict[str, Finding], memory: dict[str, Alert],
           today: date) -> list[Item]:
    """One item per key that has a finding or a memory. `memory` holds the open issues and
    the issues closed after an announced recovery whose last post is under a week old."""
    need = WORKFLOWS[ctx.workflow]["green_to_recover"]
    items: list[Item] = []
    for key in sorted(set(findings) | set(memory)):
        finding, alert = findings.get(key), memory.get(key)
        result = evaluation(finding, alert)
        if result == "F":
            if alert is None:
                items.append(Item("new", finding))
            elif _due(alert.last_posted, today):
                items.append(Item("again" if alert.said == "recovered" else "remind", finding, alert))
            else:
                items.append(Item("quiet_fail", finding, alert))
        elif result == "P":
            if alert is None or alert.closed:
                continue
            if alert.green + 1 >= need:
                items.append(Item("recovered" if alert.said == "failing" else "quiet_close", finding, alert))
            else:
                items.append(Item("quiet_pass", finding, alert))
        else:  # not evaluated
            if alert is None or alert.closed or alert.said != "failing" or not _due(alert.last_posted, today):
                continue
            if finding is not None:
                shows = sorted(set(finding.unknown_subjects) & set(alert.subjects)) or finding.unknown_subjects
                reason = f"the feed for {', '.join(shows)} didn't answer"
            else:
                finding = Finding(key, alert.name, _guide_for(ctx, alert.name), True)
                reason = "this run didn't get to it (an earlier step failed or was skipped)"
            items.append(Item("unchecked", finding, alert, reason))
    return items


# ── what gets said ──────────────────────────────────────────────────────────────────────

def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


def _days(n: int) -> str:
    return f"{n} day{'s' if n != 1 else ''}"


def _escape(text: str) -> str:
    """Slack treats &, < and > as markup; detail lines come from data, so escape them."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clip(text: str, limit: int = DETAIL_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _detail_block(finding: Finding, limit: int) -> list[str]:
    lines = []
    if finding.summary:
        lines.append(f"> {_escape(_clip(finding.summary))}")
    shown = finding.details[:limit]
    for detail in shown:
        lines.append(f"> • {_escape(_clip(detail))}")
    if len(finding.details) > len(shown):
        lines.append(f"> _+{len(finding.details) - len(shown)} more in the issue_")
    return lines


def _issue_link(number: Optional[int], url: str) -> str:
    return f"<{url}|#{number}>" if number and url else "(issue not created)"


def _flapping(history: str) -> str:
    """ "on and off: failed 3 of the last 7 runs", or "" for a steady failure."""
    recent = history[-7:]
    if "P" not in recent or "F" not in recent:
        return ""
    return f" — on and off: failed {recent.count('F')} of the last {len(recent)} runs"


def render_slack(ctx: "RunContext", items: list[Item], today: date, run_url: str,
                 new_issues: dict[str, tuple[Optional[int], str]], notes: list[str] = (),
                 state_note: str = "", fallback: bool = False) -> str:
    kinds = [i.kind for i in items]
    news = kinds.count("new")
    if fallback:
        head = (f":rotating_light: *list-maker · {ctx.label}* — failing "
                "(alert history unavailable, so this may be a repeat)")
    elif news:
        head = f":rotating_light: *list-maker · {ctx.label}* — {news} new problem{'s' if news > 1 else ''}"
    elif "again" in kinds:
        head = f":rotating_light: *list-maker · {ctx.label}* — failing again"
    elif "remind" in kinds or "unchecked" in kinds:
        head = f":hourglass_flowing_sand: *list-maker · {ctx.label}* — still failing"
    else:
        head = f":white_check_mark: *list-maker · {ctx.label}* — recovered"
    lines = [head]

    def full(item: Item, link: str, note: str = "") -> None:
        f = item.finding
        lines.append("")
        lines.append(f"*{f.guide.title}* (`{f.name}`) · {link}{note}")
        lines.extend(_detail_block(f, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Usually:_ {f.guide.usually}")
        lines.append(f"_Check first:_ {f.guide.check_first}")

    for item in items:
        f, a = item.finding, item.alert
        if item.kind == "new":
            full(item, _issue_link(*new_issues.get(f.key, (None, ""))))
        elif item.kind == "again":
            full(item, _issue_link(a.number, a.url),
                 f" — *failing again* after it was said to have recovered on {_day(a.last_posted)}"
                 f" (first failed {_day(a.since)}){_flapping(a.history + 'F')}")
        elif item.kind == "remind":
            if a.last_posted is None:
                # Its first message never landed: say it the way a new problem is said.
                full(item, _issue_link(a.number, a.url),
                     f" (first announced today; failing since {_day(a.since)})")
                continue
            lines.append("")
            lines.append(
                f"*Still failing since {_day(a.since)}* ({_days((today - a.since).days)})"
                f"{_flapping(a.history + 'F')}: {f.guide.title} (`{f.name}`) · "
                f"{_issue_link(a.number, a.url)}"
            )
            lines.extend(_detail_block(f, DETAIL_LINES_IN_SLACK))
            lines.append(f"_Check first:_ {f.guide.check_first}")
        elif item.kind == "unchecked":
            lines.append("")
            lines.append(
                f"*Still open since {_day(a.since)}* ({_days((today - a.since).days)}): "
                f"{f.guide.title} (`{f.name}`) · {_issue_link(a.number, a.url)}"
            )
            lines.append(f"> Couldn't check today: {item.reason}. Last known: failing.")
        elif item.kind == "recovered":
            lines.append("")
            lines.append(f"*Recovered:* {f.guide.title} — failing since {_day(a.since)} · "
                         f"{_issue_link(a.number, a.url)} (closed)")
    quiet = [i for i in items if i.kind == "quiet_fail"]
    if quiet and any(i.kind in POSTING for i in items):
        lines.append("")
        lines.append("_Also still failing, already reported:_ " + ", ".join(
            f"{i.finding.guide.title} ({_issue_link(i.alert.number, i.alert.url)})" for i in quiet))
    if notes:
        lines.append("")
        lines.append("_Notes from this run:_ " + " · ".join(_escape(_clip(n, 200)) for n in notes))
    if state_note:
        lines.append("")
        lines.append(f"_{state_note}_")
    lines.append("")
    lines.append(f"<{run_url}|View this run>")
    return "\n".join(lines)


def issue_title(ctx: "RunContext", finding: Finding, since: date) -> str:
    return f"{ctx.label[0].upper()}{ctx.label[1:]}: {finding.guide.title} (since {since.isoformat()})"


def next_state(finding: Finding, alert: Optional[Alert], today: date, *, result: Optional[str],
               said: Optional[str] = None, posted: bool = False) -> dict:
    """The marker to write back. `result` is this run's F/P (None: not evaluated)."""
    history = ((alert.history if alert else "") + (result or ""))[-HISTORY_LEN:]
    green = 0 if result == "F" else (alert.green if alert else 0) + (1 if result == "P" else 0)
    last_posted = today if posted else (alert.last_posted if alert else None)
    return {
        "key": finding.key,
        "since": (alert.since if alert else today).isoformat(),
        "last_posted": last_posted.isoformat() if last_posted else None,
        "said": said or (alert.said if alert else "failing"),
        "green": green,
        "history": history,
        "subjects": finding.subjects if result == "F" else (alert.subjects if alert else []),
        "runs": (alert.runs if alert else 0) + (1 if result == "F" else 0),
    }


def issue_body(ctx: "RunContext", finding: Finding, state: dict, today: date, run_url: str) -> str:
    details = "\n".join(f"- {_clip(d, 600)}" for d in finding.details[:DETAIL_LINES_IN_ISSUE])
    if len(finding.details) > DETAIL_LINES_IN_ISSUE:
        details += f"\n- … +{len(finding.details) - DETAIL_LINES_IN_ISSUE} more in the run log"
    shows = f" · **shows:** {', '.join(state['subjects'])}" if state.get("subjects") else ""
    return "\n".join([
        f"<!-- {MARKER} {json.dumps(state, sort_keys=True)} -->",
        f"**What's failing:** {finding.guide.title} (`{finding.name}`), in the {ctx.label}.",
        "",
        f"**Latest ({today.isoformat()}):** {finding.summary or 'the step failed.'}",
        details,
        "",
        f"**Usually means:** {finding.guide.usually}",
        f"**Check first:** {finding.guide.check_first}",
        "",
        f"**First failed:** {state['since']} · **runs failing:** {state['runs']} · "
        f"**recent runs (oldest first, F = failed):** `{state['history'] or '-'}`{shows} · "
        f"**latest run:** {run_url}",
        "",
        "_Kept by `pipeline/announce.py`. Slack hears about this when it starts, at most once "
        f"every {REMIND_AFTER_DAYS} days while it lasts (even on days it can't be checked), "
        "and once when it has recovered — this issue then closes itself, and reopens if it "
        f"fails again within {REMIND_AFTER_DAYS} days. A hidden comment at the top of this "
        "description is the alert's memory (edit view shows it); leave it in place._",
    ])


# ── talking to GitHub and Slack (stdlib only) ──────────────────────────────────────────

class GitHub:
    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        self.token, self.repo, self.api = token, repo, api.rstrip("/")

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.api}/repos/{self.repo}{path}", data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def _paged(self, query: str, pages: int = 10) -> list[dict]:
        issues: list[dict] = []
        for page in range(1, pages + 1):
            batch = self._request("GET", f"/issues?{query}&per_page=100&page={page}") or []
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                break
        return issues

    def open_issues(self) -> list[dict]:
        return self._paged("state=open")

    def recently_closed(self, since: date) -> list[dict]:
        return self._paged(f"state=closed&labels=pipeline-failure&since={since.isoformat()}T00:00:00Z", 3)

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict:
        return self._request("POST", "/issues", {"title": title, "body": body, "labels": labels})

    def edit_issue(self, number: int, **fields: Any) -> None:
        self._request("PATCH", f"/issues/{number}", fields)

    def comment(self, number: int, body: str) -> None:
        self._request("POST", f"/issues/{number}/comments", {"body": body})


def post_slack(text: str, webhook: Optional[str] = None) -> bool:
    """Post to the SLACK_WEBHOOK_URL. Never raises; True only if Slack accepted it."""
    webhook = webhook if webhook is not None else os.getenv("SLACK_WEBHOOK_URL")
    if not webhook:
        print("announce: no SLACK_WEBHOOK_URL; message not posted")
        return False
    req = urllib.request.Request(
        webhook, data=json.dumps({"text": text}).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        print(f"announce: Slack post failed: {exc}", file=sys.stderr)
        return False


# ── doing it ────────────────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """What announce() did, for the step summary and the tests."""

    items: list[Item]
    message: Optional[str]
    posted: bool
    actions: list[str]
    notes: list[str] = field(default_factory=list)


def announce(
    ctx: RunContext,
    findings: dict[str, Finding],
    *,
    gh: Optional[GitHub],
    post: Callable[[str], bool],
    today: date,
    run_url: str,
    dry_run: bool = False,
    notes: list[str] = (),
    reported_checks: Optional[set[str]] = None,
) -> Outcome:
    actions: list[str] = []
    notes = list(notes)
    label = WORKFLOWS[ctx.workflow]["label"]

    def write(desc: str, fn: Callable[[], Any]) -> bool:
        """One GitHub write, retried once; in a dry run, only logged. A failed write is
        reported and skipped — it must not cost the Slack message."""
        actions.append(("would " if dry_run else "") + desc)
        if dry_run:
            return False
        for attempt in (1, 2):
            try:
                fn()
                return True
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    actions.append(f"  FAILED: {exc}")
        return False

    def say(items: list[Item], new_issues=None, state_note: str = "",
            fallback: bool = False) -> tuple[Optional[str], bool]:
        if not any(i.kind in POSTING for i in items):
            return None, False
        message = render_slack(ctx, items, today, run_url, new_issues or {}, notes, state_note, fallback)
        posted = False if dry_run else post(message)
        actions.append(("would post" if dry_run else ("posted" if posted else "Slack post FAILED"))
                       + " one Slack message")
        return message, posted

    # 0. A manual scope no schedule re-checks (pipeline.yml show_id all or 3): say what
    #    failed, remember nothing, since nothing would ever remind or close it.
    if ctx.stateless:
        items = [Item("new", f) for f in findings.values() if f.failing]
        message, posted = say(items, state_note="A manual run for a scope no schedule re-checks, "
                                                 "so it is not tracked: no issue, no reminders.")
        return Outcome(items, message, posted, actions, notes)

    # 1. The memory: open issues for this run's keys, plus issues closed after an announced
    #    recovery whose last post is under a week old (a relapse reopens those). If GitHub
    #    can't be read, say every current failure (loud beats silent) and change nothing.
    memory: dict[str, Alert] = {}
    duplicates: list[tuple[Alert, Alert]] = []
    legacy: list[dict] = []
    try:
        if gh is None:
            raise RuntimeError("no GITHUB_TOKEN/GITHUB_REPOSITORY")
        for issue in sorted(gh.open_issues(), key=lambda i: int(i.get("number") or 0)):
            alert = parse_alert(issue)
            if alert and alert.key.startswith(f"{ctx.prefix}:"):
                if alert.key in memory:  # two threads for one key: keep the older
                    duplicates.append((alert, memory[alert.key]))
                else:
                    memory[alert.key] = alert
            elif alert is None and is_legacy_issue(issue, label):
                legacy.append(issue)
        since = today - timedelta(days=REMIND_AFTER_DAYS + 1)
        for issue in sorted(gh.recently_closed(since), key=lambda i: int(i.get("number") or 0)):
            alert = parse_alert(issue)
            # Only issues this announcer closed after saying "recovered". One closed by hand
            # means "I think it's fixed" and is never reopened.
            if (alert and alert.closed and alert.key.startswith(f"{ctx.prefix}:")
                    and alert.said == "recovered" and alert.key not in memory
                    and alert.last_posted and (today - alert.last_posted).days <= REMIND_AFTER_DAYS):
                memory[alert.key] = alert
    except Exception as exc:  # noqa: BLE001
        items = [Item("new", f) for f in findings.values() if f.failing]
        message, posted = say(items, state_note=f"Couldn't read the alert memory (GitHub issues: "
                                                f"{exc}), so this may repeat tomorrow.", fallback=True)
        actions.append(f"could not read alert state: {exc}")
        return Outcome(items, message, posted, actions, notes)

    # 2. Checks that no longer exist: their issues would otherwise remind forever.
    orphans = []
    if reported_checks is not None:
        for key, alert in list(memory.items()):
            name = alert.name
            if (not alert.closed and not name.startswith("step:") and name != "run"
                    and name not in reported_checks):
                orphans.append(memory.pop(key))

    items = decide(ctx, findings, memory, today)

    # 3. Open an issue for each new failure first, so the message can link it. last_posted
    #    stays empty until the message is known to have landed.
    new_issues: dict[str, tuple[Optional[int], str]] = {}
    for item in items:
        if item.kind != "new":
            continue
        created: dict = {}
        write(f"open issue for {item.finding.key}", lambda f=item.finding, c=created: c.update(
            gh.create_issue(issue_title(ctx, f, today),
                            issue_body(ctx, f, next_state(f, None, today, result="F"), today, run_url),
                            ["pipeline-failure", label]) or {}))
        if created:
            new_issues[item.finding.key] = (created.get("number"), created.get("html_url", ""))

    # 4. At most one Slack message for the whole run.
    message, posted = say(items, new_issues)

    # 5. Bring each issue's memory up to date. Anything meant to be said that didn't land
    #    keeps its old memory, so the next run says it again. Each issue gets ONE edit, so
    #    its marker and its open/closed state can never disagree.
    def edit(item: Item, marker: dict, desc: str, **fields: Any) -> None:
        a = item.alert
        write(desc, lambda: gh.edit_issue(
            a.number, body=issue_body(ctx, item.finding, marker, today, run_url), **fields))

    for item in items:
        f, a, k = item.finding, item.alert, item.kind
        if k == "new":
            number = new_issues.get(f.key, (None, ""))[0]
            if number and posted:
                write(f"mark #{number} as announced", lambda f=f, n=number: gh.edit_issue(
                    n, body=issue_body(ctx, f, next_state(f, None, today, result="F", posted=True),
                                       today, run_url)))
        elif k in ("remind", "again", "quiet_fail"):
            speak = k != "quiet_fail" and posted
            marker = next_state(f, a, today, result="F", posted=speak,
                                said="failing" if speak else None)
            reopen = {"state": "open"} if a.closed else {}
            edit(item, marker, f"{'reopen' if a.closed else 'refresh'} #{a.number} ({k})", **reopen)
            if a.closed:
                write(f"comment on #{a.number} (failing again)", lambda a=a, s=speak: gh.comment(
                    a.number, f"Failing again on {today.isoformat()}, after it was said to have "
                              f"recovered on {a.last_posted.isoformat()}. Treated as the same problem"
                              + (": Slack was told." if s else
                                 "; the next Slack word on it is the weekly one.")
                              + f" Latest run: {run_url}"))
            elif speak:
                write(f"comment on #{a.number}", lambda a=a: gh.comment(
                    a.number, f"Still failing on {today.isoformat()} — {_days((today - a.since).days)} "
                              f"since {a.since.isoformat()}. Slack was reminded. Latest run: {run_url}"))
        elif k == "unchecked":
            if posted:
                edit(item, next_state(f, a, today, result=None, posted=True),
                     f"refresh #{a.number} (reminder: couldn't check)")
                write(f"comment on #{a.number}", lambda a=a, r=item.reason: gh.comment(
                    a.number, f"Still open on {today.isoformat()}; couldn't check today ({r}). "
                              f"Slack was reminded. Latest run: {run_url}"))
        elif k == "quiet_pass":
            edit(item, next_state(f, a, today, result="P"), f"refresh #{a.number} (passing)")
        elif k == "quiet_close":
            edit(item, next_state(f, a, today, result="P"),
                 f"close #{a.number} (recovered again; already said)", state="closed",
                 state_reason="completed")
        elif k == "recovered":
            if not posted:
                # A closed issue can't be announced as recovered later: stay open, retry.
                edit(item, next_state(f, a, today, result="P"),
                     f"keep #{a.number} open until 'recovered' is posted")
                continue
            edit(item, next_state(f, a, today, result="P", said="recovered", posted=True),
                 f"close #{a.number} as recovered", state="closed", state_reason="completed")
            write(f"comment on #{a.number} (recovered)", lambda a=a: gh.comment(
                a.number, f"Recovered on {today.isoformat()}. It had been failing since "
                          f"{a.since.isoformat()}. {run_url}"))

    # 6. Housekeeping, never news.
    for alert in orphans:
        write(f"close #{alert.number}: `{alert.name}` is no longer checked", lambda a=alert: (
            gh.comment(a.number, f"Closing: the check `{a.name}` is no longer part of the health "
                                 f"report (renamed or removed), so nothing will re-check this. {run_url}"),
            gh.edit_issue(a.number, state="closed", state_reason="not_planned"),
        ))
    for dup, keep in duplicates:
        write(f"close #{dup.number} as a duplicate of #{keep.number}", lambda d=dup, k=keep: (
            gh.comment(d.number, f"Closing: #{k.number} already tracks `{d.key}`."),
            gh.edit_issue(d.number, state="closed", state_reason="not_planned"),
        ))
    failing = [i for i in items if i.kind in ("new", "remind", "again", "quiet_fail")]
    now = ("Everything this run checks is passing." if not failing else
           "Failing now, each in its own issue: " + ", ".join(
               f"{i.finding.guide.title} (#{i.alert.number if i.alert else new_issues.get(i.finding.key, (None, ''))[0]})"
               for i in failing) + ".")
    for issue in legacy:
        write(f"comment on legacy #{issue['number']}", lambda i=issue: gh.comment(
            i["number"],
            "Retiring this thread. Failures now get one issue per failing check, opened when "
            "it starts, reminded at most weekly while it lasts, and closed with a 'recovered' "
            f"comment when it recovers (pipeline/announce.py). {now} Run {today.isoformat()}: "
            f"{run_url}"))
        write(f"retire legacy #{issue['number']}", lambda i=issue: gh.edit_issue(
            i["number"], state="closed", state_reason="completed"))
    return Outcome(items, message, posted, actions, notes)


def _read_details(details_dir: Optional[str]) -> tuple[Optional[list[dict]], Optional[str], list[str]]:
    """(health results, preflight diagnostics, notes) left by earlier steps, if any."""
    if not details_dir:
        return None, None, []
    root = Path(details_dir)
    health = None
    health_path = root / "health.json"
    if health_path.exists():
        try:
            loaded = json.loads(health_path.read_text())
            health = loaded if isinstance(loaded, list) else None
        except (ValueError, OSError) as exc:
            print(f"announce: unreadable {health_path}: {exc}", file=sys.stderr)
    preflight = (root / "preflight.txt").read_text() if (root / "preflight.txt").exists() else None
    notes_path = root / "notes.txt"
    notes = [n for n in notes_path.read_text().splitlines() if n.strip()] if notes_path.exists() else []
    return health, preflight, notes


def _summarize(outcome: Outcome) -> str:
    headings = {
        "new": "New", "again": "Failing again (said)", "remind": "Reminded (7+ days)",
        "unchecked": "Reminded, couldn't check today", "recovered": "Recovered",
        "quiet_fail": "Still failing (quiet)", "quiet_pass": "Passing, not yet a verified recovery (quiet)",
        "quiet_close": "Closed quietly (recovery already said)",
    }
    lines = ["## Alerts", ""]
    for kind, heading in headings.items():
        keys = [i.finding.key for i in outcome.items if i.kind == kind]
        if keys:
            lines.append(f"- **{heading}:** " + ", ".join(f"`{k}`" for k in keys))
    if len(lines) == 2:
        lines.append("- Nothing failing, nothing recovered: no message.")
    if outcome.notes:
        # Mid-run warnings (common.alert_note). They ride in the Slack message when there
        # is one; this is where they stay visible when there isn't.
        lines += ["", "Notes from this run:"] + [f"- {n}" for n in outcome.notes]
    lines += ["", "Actions:"] + [f"- {a}" for a in outcome.actions]
    if outcome.message:
        lines += ["", "Message:", "```", outcome.message, "```"]
    return "\n".join(lines)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="The one voice of list-maker's scheduled workflows.")
    parser.add_argument("--workflow", required=True, choices=sorted(WORKFLOWS))
    parser.add_argument("--show-id", help="pipeline.yml's show_id (music runs only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read the issue memory and print what would be said; write nothing.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    run_url = os.getenv("RUN_URL", "")
    label = "list-maker run"
    try:
        if _IMPORT_ERROR:
            raise RuntimeError(f"announce.py could not import its modules:\n{_IMPORT_ERROR}")
        args = parse_args(argv)
        ctx = RunContext.build(args.workflow, args.show_id)
        label = ctx.label
        steps = json.loads(os.getenv("STEPS_JSON") or "{}")
        health, preflight, notes = _read_details(os.getenv("ALERT_DETAILS_DIR"))
        findings = collect_findings(ctx, steps, os.getenv("JOB_STATUS", ""), health, preflight)
        token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
        outcome = announce(
            ctx, findings,
            gh=GitHub(token, repo) if token and repo else None,
            post=post_slack,
            today=datetime.now(timezone.utc).date(),
            run_url=run_url,
            dry_run=args.dry_run,
            notes=notes,
            reported_checks=reported_check_names(ctx, steps, health),
        )
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — a broken alert layer must be loud, never silent
        traceback.print_exc()
        post_slack(f":warning: *list-maker · {label}* — the alert step itself crashed, so "
                   f"this run's result may not have been announced. <{run_url}|View the run>")
        return 1
    summary = _summarize(outcome)
    print(summary)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n" + summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

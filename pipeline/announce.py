#!/usr/bin/env python3
"""The one voice of list-maker's scheduled workflows: Slack and the failure issues.

WHY THIS EXISTS. Until 2026-09-23 each workflow spoke from several places at once. A red
entities run posted the data-health line, then "entity pipeline FAILED" one second
later, then commented "Failed again" on a failure issue that never closed — every day
the condition held. From June 8 to August 26 the channel carried the same data-health
line almost daily for eleven weeks. A check that talks that much trains its reader to
ignore it, which is worse than no check (Kevin's legibility standard).

THE RULE (Kevin, 2026-09-23): say it when it starts or when the set of failing things
changes; again every 7 days while it is still unfixed ("still failing since Sep 22 (8
days)"); once on recovery; never daily. It matches fleet-release-watch.yml in
khglynn/google_workspace_mcp (once, then weekly). In practice:

  - something starts failing        → one Slack message, and an issue of its own
  - a new show fails an open check  → one message ("now also failing for tal")
  - still failing, said <7 days ago → quiet; the issue body is refreshed silently
  - still failing, said ≥7 days ago → a reminder, naming any flapping
                                      ("on and off: failed 3 of the last 7 runs")
  - passes, and has held            → one "recovered" message; the issue closes
  - fails again within 7 days of a  → the SAME issue reopens, quietly: a check that
    "recovered" message               flips back and forth is one problem, and the
                                      weekly reminder is the next thing said about it

"Has held" means the last failing run was at least RECOVERY_HOLD_DAYS (2) days ago: a
daily check needs two green days in a row, and a check that only runs weekly (or Wed and
Fri) recovers on its first green run. Without a hold, a check hovering at its threshold
(AI Daily's 2-day feed window) posted "new problem" and "recovered" on alternate days
and scattered one problem across a new issue every flip.

One Slack message per run at most, covering every change, each item in plain words with
its usual cause, what to check first (alert_guides.py), and links to the issue and run.
Something this run did NOT evaluate (a step skipped because the database was
unreachable, a feed that didn't answer) is never counted as a recovery — but an open
alert that can't be checked still gets its weekly reminder, saying so.

WHY THE ISSUES ARE THE STATE. They already existed as the failure thread, persist with no
infrastructure, and a person can read them. Each carries a hidden JSON marker (key, first
failed, last posted, the shows involved, the last 14 results). An orphan state branch
(fleet-release-watch's store) would have meant `contents: write` on workflows that hold
every pipeline secret. Closing an issue by hand says "I think it's fixed": it is not
reopened, and if the problem is still there the next run opens a fresh one.

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
# Guarded: if either import breaks (a later edit to show_config, say), main() still posts
# "the alert step itself crashed" instead of dying with a traceback nobody sees.
try:
    from alert_guides import CHECK_GUIDES, RUN_GUIDE, STEP_GUIDES, Guide, fallback_guide  # noqa: E402
    from show_config import shows_with_spotify  # noqa: E402 — stdlib-only, like this file
    _IMPORT_ERROR: Optional[str] = None
except Exception:  # noqa: BLE001
    _IMPORT_ERROR = traceback.format_exc()

REMIND_AFTER_DAYS = 7
RECOVERY_HOLD_DAYS = 2
REOPEN_WITHIN_DAYS = 7
HISTORY_LEN = 14
MARKER = "list-maker-alert"
_MARKER_RE = re.compile(r"<!--\s*" + MARKER + r"\s+(\{.*?\})\s*-->", re.S)

# Issues from before this announcer (#64) are retired on first contact. Only issues this
# old: a failure issue someone files by hand later must never be auto-closed.
LEGACY_BEFORE = datetime(2026, 9, 24, tzinfo=timezone.utc)

# Per workflow: the step ids this announcer tracks (each workflow file gives these ids),
# which of them writes data_health results, and the issue label.
WORKFLOWS: dict[str, dict[str, Any]] = {
    "entities": {
        "steps": ("preflight", "import", "notion", "health"),
        "health_step": "health",
        "label": "entities",
    },
    "music": {
        "steps": ("preflight", "spotify_cache", "pipeline", "feed_check"),
        "health_step": "feed_check",
        "label": "music",
    },
    "intake": {
        "steps": ("preflight", "log_schema", "intake"),
        "health_step": None,
        "label": "intake",
    },
}

# pipeline.yml's show_id → the show a music run evaluates. Only the scheduled ones (the
# Worker dispatches 1 on Wed/Fri and 2 on Mondays) are TRACKED: a run can only recover what
# a later run re-checks, and nothing re-checks "all" or "3", so an issue opened by one of
# those manual runs would never be reminded or closed. They post, and keep no memory.
MUSIC_SHOWS = {"1": ("sop", "SOP"), "2": ("tal", "TAL"), "3": ("ai-daily-brief", "AI Daily"),
               "all": ("all", "all-shows")}
TRACKED_MUSIC_SHOW_IDS = frozenset({"1", "2"})

# A check whose "warn" means "couldn't find out" rather than "a milder problem". The feed
# check warns when a show's feed didn't answer (UNVERIFIED). Those shows are unknown this
# run, on a fail day as much as a warn day: never a recovery, never forgotten. Every
# other check's warn is a real, milder state (some stand every day), so it ends a failure.
UNVERIFIED_WHEN_WARN = frozenset({"import_caught_up_to_feed"})

# The only check the daily entities run shares with the music workflow: pipeline.yml runs
# `data_health.py --feed-check-only` and nothing else, so it is the only check the entities
# run may leave to it (the songs, freshness and transcript checks exist only here).
HANDOFF_CHECK = "import_caught_up_to_feed"
# How recently the music workflow must have spoken about a show for the entities run to
# leave that show's feed check to it: its weekly cadence plus the gap between its runs.
OWNER_ACTIVE_DAYS = REMIND_AFTER_DAYS + 3

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
    it. A run that failed or was CANCELLED without any tracked failure is itself a failing
    finding: a job cut off by its time limit ends "cancelled", and that is the shape that
    hid the May–June 2026 playlist outage.
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

    tracked_failure = any(f.failing for f in findings.values())
    if job_status == "success":
        add("run", RUN_GUIDE, False)
    elif job_status == "failure" and not tracked_failure:
        add("run", RUN_GUIDE, True, "The run failed, but in none of the steps this alert tracks.")
    elif job_status == "cancelled" and not tracked_failure:
        add("run", RUN_GUIDE, True,
            "The run was cancelled before it finished — usually a time limit. If someone "
            "stopped it by hand, the next run clears this.")
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
class OpenAlert:
    number: int
    url: str
    key: str
    since: date
    last_posted: Optional[date]
    runs: int
    subjects: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)  # every show this alert has failed for
    history: str = ""  # the last HISTORY_LEN evaluations, oldest first: F(ail) / P(ass)
    last_failed: Optional[date] = None
    recovered_posted: Optional[date] = None  # "recovered" said; closing may still be due
    created: Optional[datetime] = None
    closed: bool = False

    @property
    def name(self) -> str:
        return self.key.split(":", 1)[1] if ":" in self.key else self.key


def _date(value: Any) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _created(issue: dict) -> Optional[datetime]:
    raw = issue.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_alert(issue: dict) -> Optional[OpenAlert]:
    """The alert state carried by an issue's hidden marker, or None for any other issue."""
    match = _MARKER_RE.search(issue.get("body") or "")
    if not match:
        return None
    try:
        state = json.loads(match.group(1))
        subjects = [str(x) for x in state.get("subjects") or []]
        return OpenAlert(
            number=int(issue["number"]),
            url=str(issue.get("html_url") or ""),
            key=str(state["key"]),
            since=date.fromisoformat(state["since"]),
            last_posted=_date(state.get("last_posted")),
            runs=int(state.get("runs") or 1),
            subjects=subjects,
            seen=[str(x) for x in state.get("seen") or subjects],
            history=str(state.get("history") or ""),
            last_failed=_date(state.get("last_failed")),
            recovered_posted=_date(state.get("recovered_posted")),
            created=_created(issue),
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

@dataclass
class Plan:
    new: list[Finding] = field(default_factory=list)
    # A recently recovered issue failing again: (finding, alert, shows it never failed for).
    reopened: list[tuple[Finding, OpenAlert, list[str]]] = field(default_factory=list)
    # Open, and now failing for a show it never failed for: (finding, alert, those shows).
    changed: list[tuple[Finding, OpenAlert, list[str]]] = field(default_factory=list)
    remind: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    ongoing: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    recovering: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    recovered: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    close_quietly: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    unknown_remind: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    fallback: bool = False  # the memory couldn't be read

    @property
    def speaking_reopens(self) -> list[tuple[Finding, OpenAlert, list[str]]]:
        return [r for r in self.reopened if r[2]]

    @property
    def should_post(self) -> bool:
        return bool(self.new or self.changed or self.speaking_reopens or self.remind
                    or self.recovered or self.unknown_remind)


def _due(last_posted: Optional[date], today: date) -> bool:
    return last_posted is None or (today - last_posted).days >= REMIND_AFTER_DAYS


def _held(alert: OpenAlert, today: date) -> bool:
    last_failed = alert.last_failed or alert.last_posted or alert.since
    return (today - last_failed).days >= RECOVERY_HOLD_DAYS


def remembered_subjects(finding: Finding, alert: Optional[OpenAlert]) -> list[str]:
    """The shows to remember as failing: today's, plus any the alert already had that
    couldn't be checked today — so a one-day feed outage can't make a show look new."""
    keep = set(alert.subjects) & set(finding.unknown_subjects) if alert else set()
    return sorted(set(finding.subjects) | keep)


def decide(findings: dict[str, Finding], open_alerts: dict[str, OpenAlert], today: date,
           recent_closed: Optional[dict[str, OpenAlert]] = None) -> Plan:
    plan = Plan()
    recent_closed = recent_closed or {}
    for key in sorted(findings):
        finding = findings[key]
        alert = open_alerts.get(key)
        if finding.failing:
            if alert is None:
                previous = recent_closed.get(key)
                if previous is None:
                    plan.new.append(finding)
                else:
                    added = sorted(set(finding.subjects) - set(previous.seen))
                    plan.reopened.append((finding, previous, added))
                continue
            if alert.recovered_posted:
                # "Recovered" was said but the close didn't land, and it's failing again:
                # the same as a reopen.
                added = sorted(set(finding.subjects) - set(alert.seen))
                plan.reopened.append((finding, alert, added))
                continue
            added = sorted(set(finding.subjects) - set(alert.seen))
            if added:
                plan.changed.append((finding, alert, added))
            elif _due(alert.last_posted, today):
                # last_posted None: the issue exists but its message never landed.
                plan.remind.append((finding, alert))
            else:
                plan.ongoing.append((finding, alert))
        elif alert is not None:
            unknown = set(finding.unknown_subjects)
            if unknown and (not alert.subjects or unknown & set(alert.subjects)):
                # The show it was failing for couldn't be checked today: never a recovery,
                # but the weekly reminder still comes, saying so.
                if _due(alert.last_posted, today):
                    plan.unknown_remind.append((finding, alert))
                continue
            if alert.recovered_posted:
                plan.close_quietly.append((finding, alert))
            elif _held(alert, today):
                plan.recovered.append((finding, alert))
            else:
                plan.recovering.append((finding, alert))
    return plan


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


def render_slack(
    ctx: RunContext,
    plan: Plan,
    today: date,
    run_url: str,
    new_issues: dict[str, tuple[Optional[int], str]],
    notes: list[str] = (),
    state_note: str = "",
) -> str:
    news = len(plan.new) + len(plan.changed) + len(plan.speaking_reopens)
    if plan.fallback:
        head = (f":rotating_light: *list-maker · {ctx.label}* — failing "
                "(alert history unavailable, so this may be a repeat)")
    elif news:
        head = f":rotating_light: *list-maker · {ctx.label}* — {news} new problem{'s' if news > 1 else ''}"
    elif plan.remind or plan.unknown_remind:
        head = f":hourglass_flowing_sand: *list-maker · {ctx.label}* — still failing"
    else:
        head = f":white_check_mark: *list-maker · {ctx.label}* — recovered"
    lines = [head]

    def new_style(finding: Finding, link: str, note: str = "") -> None:
        lines.append("")
        lines.append(f"*{finding.guide.title}* (`{finding.name}`) · {link}{note}")
        lines.extend(_detail_block(finding, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Usually:_ {finding.guide.usually}")
        lines.append(f"_Check first:_ {finding.guide.check_first}")

    for finding in plan.new:
        new_style(finding, _issue_link(*new_issues.get(finding.key, (None, ""))))
    for finding, alert, added in plan.changed + plan.speaking_reopens:
        lines.append("")
        lines.append(
            f"*{finding.guide.title}* (`{finding.name}`) — now also failing for "
            f"{', '.join(added)} · {_issue_link(alert.number, alert.url)} "
            f"(first failed {_day(alert.since)})"
        )
        lines.extend(_detail_block(finding, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Check first:_ {finding.guide.check_first}")
    for finding, alert in plan.remind:
        if alert.last_posted is None:
            # Its first message never landed: say it the way a new problem is said.
            new_style(finding, _issue_link(alert.number, alert.url),
                      f" (first announced today; failing since {_day(alert.since)})")
            continue
        lines.append("")
        lines.append(
            f"*Still failing since {_day(alert.since)}* ({_days((today - alert.since).days)})"
            f"{_flapping(alert.history + 'F')}: {finding.guide.title} (`{finding.name}`) · "
            f"{_issue_link(alert.number, alert.url)}"
        )
        lines.extend(_detail_block(finding, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Check first:_ {finding.guide.check_first}")
    for finding, alert in plan.unknown_remind:
        unknown = ", ".join(sorted(set(finding.unknown_subjects) & set(alert.subjects))
                            or finding.unknown_subjects)
        seen = f"; last seen failing {_day(alert.last_failed)}" if alert.last_failed else ""
        lines.append("")
        lines.append(
            f"*Still open since {_day(alert.since)}* ({_days((today - alert.since).days)}): "
            f"{finding.guide.title} (`{finding.name}`) · {_issue_link(alert.number, alert.url)}"
        )
        lines.append(f"> Couldn't check today: the feed for {unknown} didn't answer{seen}.")
    for finding, alert in plan.recovered:
        lines.append("")
        lines.append(
            f"*Recovered:* {finding.guide.title} — failing since "
            f"{_day(alert.since)} · {_issue_link(alert.number, alert.url)} (closed)"
        )
    quiet = [(f, a) for f, a in plan.ongoing] + [(f, a) for f, a, added in plan.reopened if not added]
    if quiet:
        lines.append("")
        lines.append("_Also still failing, already reported:_ " + ", ".join(
            f"{f.guide.title} ({_issue_link(a.number, a.url)})" for f, a in quiet))
    if notes:
        lines.append("")
        lines.append("_Notes from this run:_ " + " · ".join(_escape(_clip(n, 200)) for n in notes))
    if state_note:
        lines.append("")
        lines.append(f"_{state_note}_")
    lines.append("")
    lines.append(f"<{run_url}|View this run>")
    return "\n".join(lines)


def issue_title(ctx: RunContext, finding: Finding, since: date) -> str:
    return f"{ctx.label[0].upper()}{ctx.label[1:]}: {finding.guide.title} (since {since.isoformat()})"


def next_state(finding: Finding, alert: Optional[OpenAlert], today: date, *,
               result: Optional[str], announced: bool,
               subjects: Optional[list[str]] = None, seen: Optional[list[str]] = None,
               recovered_posted: Optional[date] = None) -> dict:
    """The marker to write back. `result` is this run's F/P (None: not evaluated)."""
    history = (alert.history if alert else "") + (result or "")
    failing = result == "F"
    if subjects is None:
        subjects = remembered_subjects(finding, alert) if failing else (alert.subjects if alert else [])
    if seen is None:
        seen = sorted(set(alert.seen if alert else []) | set(subjects))
    last_posted = today if announced else (alert.last_posted if alert else None)
    last_failed = today if failing else (alert.last_failed if alert else None)
    return {
        "key": finding.key,
        "since": (alert.since if alert else today).isoformat(),
        "last_posted": last_posted.isoformat() if last_posted else None,
        "runs": (alert.runs if alert else 0) + (1 if failing else 0),
        "subjects": subjects,
        "seen": seen,
        "history": history[-HISTORY_LEN:],
        "last_failed": last_failed.isoformat() if last_failed else None,
        "recovered_posted": recovered_posted.isoformat() if recovered_posted else None,
    }


def issue_body(ctx: RunContext, finding: Finding, state: dict, today: date, run_url: str) -> str:
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
        "_Kept by `pipeline/announce.py`. Slack hears about this when it starts, again every "
        f"{REMIND_AFTER_DAYS} days while it lasts, and once when it has recovered and held "
        f"({RECOVERY_HOLD_DAYS} days) — this issue then closes itself, and reopens if it "
        f"fails again within {REOPEN_WITHIN_DAYS} days. A hidden comment at the top of this "
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

    plan: Plan
    message: Optional[str]
    posted: bool
    actions: list[str]
    notes: list[str] = field(default_factory=list)


def _hand_off_feed_backstop(
    ctx: RunContext, findings: dict[str, Finding], other_open: dict[str, OpenAlert],
    today: date, actions: list[str],
) -> tuple[dict[str, Finding], dict[str, OpenAlert]]:
    """Leave a music show's feed-behind to the music workflow while it is reporting it.

    The daily entities run backstops the music shows a week past their feed window. When
    pipeline.yml has an ACTIVE alert for that show's own feed check (announced within
    OWNER_ACTIVE_DAYS, or opened within the last day and not yet announced — the two
    workflows run at the same minute), a second thread about one outage is noise: the
    show is dropped from the entities finding, and a finding left with nothing of its
    own is set aside. Returns (findings, owner alert per fully handed-off key), so the
    caller can close the entities thread with a pointer instead of leaving it silent.

    Deliberately narrow (review, 2026-09-23): only import_caught_up_to_feed, and only to
    the owner's same check. The music workflow runs nothing else; muting songs-still-
    arriving or freshness here would silence the checks built for the July TAL outage.
    """
    if ctx.workflow != "entities":
        return findings, {}
    music = {cfg.slug for cfg in shows_with_spotify()}

    def owner(slug: str) -> Optional[OpenAlert]:
        alert = other_open.get(f"music-{slug}:{HANDOFF_CHECK}")
        if alert is None:
            return None
        if alert.last_posted is not None:
            return alert if (today - alert.last_posted).days <= OWNER_ACTIVE_DAYS else None
        fresh = alert.created is not None and (today - alert.created.date()).days <= 1
        return alert if fresh else None

    key = f"{ctx.prefix}:{HANDOFF_CHECK}"
    finding = findings.get(key)
    if finding is None or not (finding.failing and finding.subjects):
        return findings, {}
    owners = {s: owner(s) for s in finding.subjects if s in music}
    owned = {s: a for s, a in owners.items() if a is not None}
    if not owned:
        return findings, {}
    out = dict(findings)
    remaining = [s for s in finding.subjects if s not in owned]
    actions.append(f"left {', '.join(sorted(owned))} in {key} to the music workflow's open alert")
    if remaining:
        out[key] = Finding(finding.key, finding.name, finding.guide, True, finding.summary,
                           finding.details, remaining, finding.unknown_subjects)
        return out, {}
    del out[key]
    return out, {key: next(iter(owned.values()))}


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
        """One GitHub write, logged; in a dry run, only logged. A failed write is reported
        and skipped — it must not cost the Slack message. True if it happened."""
        actions.append(("would " if dry_run else "") + desc)
        if dry_run:
            return False
        try:
            fn()
            return True
        except Exception as exc:  # noqa: BLE001
            actions.append(f"  FAILED: {exc}")
            return False

    def say(plan: Plan, new_issues=None, state_note: str = "") -> tuple[Optional[str], bool]:
        if not plan.should_post:
            return None, False
        message = render_slack(ctx, plan, today, run_url, new_issues or {}, notes, state_note)
        posted = False if dry_run else post(message)
        actions.append(("would post" if dry_run else ("posted" if posted else "Slack post FAILED"))
                       + " one Slack message")
        return message, posted

    # 0. A manual scope no schedule re-checks (pipeline.yml show_id all or 3): say what
    #    failed, remember nothing, since nothing would ever remind or close it.
    if ctx.stateless:
        plan = Plan(new=[f for f in findings.values() if f.failing])
        message, posted = say(plan, state_note="A manual run for a scope no schedule re-checks, "
                                               "so it is not tracked: no issue, no reminders.")
        return Outcome(plan, message, posted, actions, notes)

    # 1. The memory. If GitHub can't be read, nothing can be deduplicated: say every current
    #    failure (loud beats silent), headlined as possibly a repeat, and change nothing.
    open_alerts: dict[str, OpenAlert] = {}
    other_open: dict[str, OpenAlert] = {}
    recent_closed: dict[str, OpenAlert] = {}
    duplicates: list[tuple[OpenAlert, OpenAlert]] = []
    legacy: list[dict] = []
    try:
        if gh is None:
            raise RuntimeError("no GITHUB_TOKEN/GITHUB_REPOSITORY")
        for issue in sorted(gh.open_issues(), key=lambda i: int(i.get("number") or 0)):
            alert = parse_alert(issue)
            if alert and alert.key.startswith(f"{ctx.prefix}:"):
                if alert.key in open_alerts:  # two threads for one key: keep the older
                    duplicates.append((alert, open_alerts[alert.key]))
                else:
                    open_alerts[alert.key] = alert
            elif alert:
                other_open[alert.key] = alert
            elif is_legacy_issue(issue, label):
                legacy.append(issue)
        window = today - timedelta(days=REOPEN_WITHIN_DAYS + 1)
        for issue in sorted(gh.recently_closed(window), key=lambda i: int(i.get("number") or 0)):
            alert = parse_alert(issue)
            # Only issues this announcer closed after saying "recovered"; one closed by hand
            # means "I think it's fixed" and is never reopened.
            if (alert and alert.key.startswith(f"{ctx.prefix}:") and alert.recovered_posted
                    and (today - alert.recovered_posted).days <= REOPEN_WITHIN_DAYS
                    and alert.key not in open_alerts):
                recent_closed[alert.key] = alert
    except Exception as exc:  # noqa: BLE001
        plan = Plan(new=[f for f in findings.values() if f.failing], fallback=True)
        message, posted = say(plan, state_note=f"Couldn't read the alert memory (GitHub issues: "
                                               f"{exc}), so this may repeat tomorrow.")
        if not plan.should_post:
            actions.append(f"could not read alert state ({exc}); nothing failing, so quiet")
        else:
            actions.append(f"could not read alert state: {exc}")
        return Outcome(plan, message, posted, actions, notes)

    # 2. Checks that no longer exist: their issues would otherwise never close.
    orphans = []
    if reported_checks is not None:
        for key, alert in list(open_alerts.items()):
            name = alert.name
            if not name.startswith("step:") and name != "run" and name not in reported_checks:
                orphans.append(open_alerts.pop(key))

    # 3. The feed backstop's hand-off to the music workflow.
    findings, handed_off = _hand_off_feed_backstop(ctx, findings, other_open, today, actions)
    handoffs = [(open_alerts.pop(k), owner) for k, owner in handed_off.items() if k in open_alerts]

    plan = decide(findings, open_alerts, today, recent_closed)

    # 4. Open an issue for each new failure first, so the message can link it. last_posted
    #    stays empty until the message is known to have landed.
    new_issues: dict[str, tuple[Optional[int], str]] = {}
    for finding in plan.new:
        created: dict = {}
        write(f"open issue for {finding.key}", lambda f=finding, c=created: c.update(gh.create_issue(
            issue_title(ctx, f, today),
            issue_body(ctx, f, next_state(f, None, today, result="F", announced=False), today, run_url),
            ["pipeline-failure", label],
        ) or {}))
        if created:
            new_issues[finding.key] = (created.get("number"), created.get("html_url", ""))

    # 5. At most one Slack message for the whole run.
    message, posted = say(plan, new_issues)

    # 6. Bring each issue's memory up to date. Anything that was meant to be said but
    #    didn't land keeps its old memory, so the next run says it again.
    def body(f: Finding, state: dict) -> str:
        return issue_body(ctx, f, state, today, run_url)

    for finding in plan.new:
        number = new_issues.get(finding.key, (None, ""))[0]
        if number and posted:
            write(f"mark #{number} as announced", lambda f=finding, n=number: gh.edit_issue(
                n, body=body(f, next_state(f, None, today, result="F", announced=True))))
    for finding, alert, added in plan.reopened:
        spoke = bool(added) and posted
        state = next_state(finding, alert, today, result="F", announced=spoke,
                           seen=None if (spoke or not added) else alert.seen)
        write(f"reopen #{alert.number} (failing again)", lambda f=finding, a=alert, s=state: gh.edit_issue(
            a.number, state="open", body=body(f, s)))
        when = alert.recovered_posted or today
        write(f"comment on #{alert.number} (failing again)", lambda a=alert, w=when, sp=spoke: gh.comment(
            a.number,
            f"Failing again on {today.isoformat()}, {_days((today - w).days)} after it was "
            f"announced recovered. Treated as the same problem"
            + (": Slack was told about the new show." if sp else
               f"; the next Slack word on it is the weekly reminder.")
            + f" Latest run: {run_url}"))
    for finding, alert, added in plan.changed:
        state = next_state(finding, alert, today, result="F", announced=posted,
                           subjects=None if posted else alert.subjects,
                           seen=None if posted else alert.seen)
        write(f"refresh #{alert.number} (now also {', '.join(added)})",
              lambda f=finding, a=alert, s=state: gh.edit_issue(a.number, body=body(f, s)))
        if posted:
            write(f"comment on #{alert.number}", lambda a=alert, ad=added: gh.comment(
                a.number, f"Now also failing for {', '.join(ad)} on {today.isoformat()}. "
                          f"Slack was told. Latest run: {run_url}"))
    for finding, alert in plan.remind:
        write(f"refresh #{alert.number} (reminder{' posted' if posted else ' not posted'})",
              lambda f=finding, a=alert: gh.edit_issue(a.number, body=body(
                  f, next_state(f, a, today, result="F", announced=posted))))
        if posted:
            write(f"comment on #{alert.number}", lambda a=alert: gh.comment(
                a.number, f"Still failing on {today.isoformat()} — {_days((today - a.since).days)} "
                          f"since {a.since.isoformat()}. Slack was reminded. Latest run: {run_url}"))
    for finding, alert in plan.ongoing:
        write(f"refresh #{alert.number} quietly", lambda f=finding, a=alert: gh.edit_issue(
            a.number, body=body(f, next_state(f, a, today, result="F", announced=False))))
    for finding, alert in plan.unknown_remind:
        if posted:
            write(f"refresh #{alert.number} (reminder: couldn't check)", lambda f=finding, a=alert:
                  gh.edit_issue(a.number, body=body(f, next_state(f, a, today, result=None, announced=True))))
            write(f"comment on #{alert.number}", lambda a=alert: gh.comment(
                a.number, f"Still open on {today.isoformat()}; couldn't check today (the feed "
                          f"didn't answer). Slack was reminded. Latest run: {run_url}"))
    for finding, alert in plan.recovering:
        write(f"refresh #{alert.number} (passing; recovery not held yet)", lambda f=finding, a=alert:
              gh.edit_issue(a.number, body=body(f, next_state(f, a, today, result="P", announced=False))))
    for finding, alert in plan.recovered:
        if not posted:
            # Say it before closing: a closed issue can't be announced as recovered later.
            write(f"keep #{alert.number} open until 'recovered' is posted", lambda f=finding, a=alert:
                  gh.edit_issue(a.number, body=body(f, next_state(f, a, today, result="P", announced=False))))
            continue
        # Memory first, so a failed close is finished quietly next run (not re-announced).
        write(f"mark #{alert.number} recovered", lambda f=finding, a=alert: gh.edit_issue(
            a.number, body=body(f, next_state(f, a, today, result="P", announced=True,
                                              recovered_posted=today))))
        write(f"comment on #{alert.number} (recovered)", lambda a=alert: gh.comment(
            a.number, f"Recovered on {today.isoformat()}: passing since the last failure on "
                      f"{(a.last_failed or a.since).isoformat()}. It had been failing since "
                      f"{a.since.isoformat()}. {run_url}"))
        write(f"close #{alert.number} as recovered", lambda a=alert: gh.edit_issue(
            a.number, state="closed", state_reason="completed"))
    for finding, alert in plan.close_quietly:
        write(f"close #{alert.number} (recovery already announced)", lambda a=alert: gh.edit_issue(
            a.number, state="closed", state_reason="completed"))

    # 7. Housekeeping, never news.
    for alert in orphans:
        write(f"close #{alert.number}: `{alert.name}` is no longer checked", lambda a=alert: (
            gh.comment(a.number, f"Closing: the check `{a.name}` is no longer part of the health "
                                 f"report (renamed or removed), so nothing will re-check this. {run_url}"),
            gh.edit_issue(a.number, state="closed", state_reason="not_planned"),
        ))
    for alert, owner in handoffs:
        write(f"close #{alert.number}: now tracked in #{owner.number}", lambda a=alert, o=owner: (
            gh.comment(a.number, f"The music workflow is reporting this show's feed now "
                                 f"(#{o.number}); closing this thread so one outage has one thread."),
            gh.edit_issue(a.number, state="closed", state_reason="not_planned"),
        ))
    for dup, keep in duplicates:
        write(f"close #{dup.number} as a duplicate of #{keep.number}", lambda d=dup, k=keep: (
            gh.comment(d.number, f"Closing: #{k.number} already tracks `{d.key}`."),
            gh.edit_issue(d.number, state="closed", state_reason="not_planned"),
        ))
    still = [(f.guide.title, a.number) for f, a in plan.remind + plan.ongoing]
    still += [(f.guide.title, a.number) for f, a, _ in plan.changed + plan.reopened]
    still += [(f.guide.title, new_issues.get(f.key, (None, ""))[0]) for f in plan.new]
    now = ("Everything this run checks is passing." if not still else
           "Failing now, each in its own issue: "
           + ", ".join(f"{t} (#{n})" if n else t for t, n in still) + ".")
    for issue in legacy:
        write(f"comment on legacy #{issue['number']}", lambda i=issue: gh.comment(
            i["number"],
            "Retiring this thread. Failures now get one issue per failing check, opened when "
            "it starts, reminded weekly while it lasts, and closed with a 'recovered' comment "
            f"once it has held (pipeline/announce.py). {now} Run {today.isoformat()}: {run_url}"))
        write(f"retire legacy #{issue['number']}", lambda i=issue: gh.edit_issue(
            i["number"], state="closed", state_reason="completed"))
    return Outcome(plan, message, posted, actions, notes)


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
    plan = outcome.plan
    lines = ["## Alerts", ""]
    for heading, items in (
        ("New", [f.key for f in plan.new]),
        ("Now failing for more shows", [f"{f.key} (+{', '.join(a)})" for f, _, a in plan.changed]),
        ("Failing again (reopened)", [f.key for f, _, _ in plan.reopened]),
        ("Reminded (7+ days)", [f.key for f, _ in plan.remind]),
        ("Reminded, couldn't check today", [f.key for f, _ in plan.unknown_remind]),
        ("Still failing (quiet)", [f.key for f, _ in plan.ongoing]),
        ("Passing, recovery not held yet (quiet)", [f.key for f, _ in plan.recovering]),
        ("Recovered", [f.key for f, _ in plan.recovered]),
    ):
        if items:
            lines.append(f"- **{heading}:** " + ", ".join(f"`{k}`" for k in items))
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

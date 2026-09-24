#!/usr/bin/env python3
"""The one voice of list-maker's scheduled workflows: Slack and the failure issues.

WHY THIS EXISTS. Until 2026-09-23 each workflow spoke from several places at once. A red
entities run posted the data-health line, then "entity pipeline FAILED" one second
later, then commented "Failed again" on a failure issue that never closed — every day
the condition held. From June 8 to August 26 the channel carried the same data-health
line almost daily for eleven weeks. A check that talks that much trains its reader to
ignore it, which is worse than no check (Kevin's legibility standard).

WHAT IT DOES. The last step of entities.yml, pipeline.yml and blogs.yml runs this once,
whatever happened before it. It works out which things this run actually evaluated
(each tracked step, and each data_health check when the health step reported), then
compares them with the open failure issues, which are the memory:

  - something starts failing       → one Slack message, and a new issue for it
  - the set of failures changes    → the same: each new failure is its own issue, and a
                                     new show failing a check already open is news too
  - still failing, last said <7d   → quiet; the issue body is refreshed silently
  - still failing, last said ≥7d   → Slack again: "still failing since Sep 22 (8 days)"
  - passes while its issue is open → Slack "recovered", comment, close the issue

(The weekly reminder is Kevin's rule, 2026-09-23: a real error that posts only once
leaves him unable to tell whether it was ever fixed. It matches fleet-release-watch.yml
in khglynn/google_workspace_mcp, which says a thing once, then weekly.)

One Slack message per run at most, covering every change, each item in plain words
with its usual cause, what to check first (alert_guides.py), and links to the issue and
the run. Something this run did NOT evaluate (a step skipped because the database was
unreachable) is left exactly as it was: a run can only recover what it checked.

WHY THE ISSUES ARE THE STATE. They already existed as the failure thread, they persist
with no infrastructure, a person can read them, and the issue for a failure is exactly
"this is currently failing". Each carries a hidden marker with its key, first-failed
date and last-posted date. An orphan state branch (fleet-release-watch's store) would
have meant `contents: write` on workflows that hold every pipeline secret, for state
the issues already hold. Closing an issue by hand says "I think it's fixed"; if it
isn't, the next run opens a fresh one and says so.

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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert_guides import CHECK_GUIDES, RUN_GUIDE, STEP_GUIDES, Guide, fallback_guide  # noqa: E402
from show_config import shows_with_spotify  # noqa: E402 — stdlib-only, like this file

REMIND_AFTER_DAYS = 7
MARKER = "list-maker-alert"
_MARKER_RE = re.compile(r"<!--\s*" + MARKER + r"\s+(\{.*?\})\s*-->", re.S)

# Per workflow: the step ids this announcer tracks (each workflow file gives these ids),
# which of them writes data_health results, the issue label, and how to name the run.
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

# pipeline.yml's show_id input → the show a music run evaluates. A run can only recover
# the failures of the show it ran, so music keys are scoped per show.
MUSIC_SHOWS = {"1": ("sop", "SOP"), "2": ("tal", "TAL"), "3": ("ai-daily-brief", "AI Daily"),
               "all": ("all", "all-shows")}

# A check whose "warn" means "couldn't find out" rather than "a milder problem". The feed
# check warns only when a show's feed was unreachable (UNVERIFIED); treating that as a
# pass would close an open BEHIND issue with "recovered" on a day Taddy was down, and
# reopen it as a new problem the next. Every other check's warn is a real, milder
# state — some are standing (transcript coverage warns on music shows every day) — so
# for those a warn does end a failure.
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
    """The shows a check's failing lines are about ("sop: BEHIND 1 …" → "sop"); a
    failing line with no show prefix counts as "general". So a second show failing a
    check that is already open is a change worth saying, while a count moving inside
    the same show is not."""
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

    @classmethod
    def build(cls, workflow: str, show_id: Optional[str] = None) -> "RunContext":
        if workflow == "music":
            slug, name = MUSIC_SHOWS.get(str(show_id), (str(show_id), str(show_id)))
            return cls(workflow, f"music-{slug}", f"{name} music run")
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
    nothing, so an unreachable database can't "recover" a check that never ran. When
    the health step reported, each data_health check is its own finding and the step's
    own finding only fails if it broke without a failing check to explain it.
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
                unknown = []
                if status == "warn" and name in UNVERIFIED_WHEN_WARN:
                    # Not a failure; a recovery only for shows it could actually see.
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

    if job_status == "success":
        add("run", RUN_GUIDE, False)
    elif job_status == "failure" and not any(f.failing for f in findings.values()):
        add("run", RUN_GUIDE, True, "The run failed, but in none of the steps this alert tracks.")
    return findings


# ── the memory: open issues ─────────────────────────────────────────────────────────────

@dataclass
class OpenAlert:
    number: int
    url: str
    key: str
    since: date
    last_posted: Optional[date]
    runs: int
    subjects: list[str] = field(default_factory=list)


def parse_alert(issue: dict) -> Optional[OpenAlert]:
    """The alert state carried by an issue's hidden marker, or None for any other issue."""
    match = _MARKER_RE.search(issue.get("body") or "")
    if not match:
        return None
    try:
        state = json.loads(match.group(1))
        return OpenAlert(
            number=int(issue["number"]),
            url=str(issue.get("html_url") or ""),
            key=str(state["key"]),
            since=date.fromisoformat(state["since"]),
            last_posted=date.fromisoformat(state["last_posted"]) if state.get("last_posted") else None,
            runs=int(state.get("runs") or 1),
            subjects=[str(x) for x in state.get("subjects") or []],
        )
    except (ValueError, KeyError, TypeError):
        return None


def is_legacy_issue(issue: dict, label: str) -> bool:
    """A failure issue from before this announcer: the pipeline-failure label plus this
    workflow's label, and no marker. #64 is the one still open on 2026-09-23."""
    names = {lbl.get("name") for lbl in issue.get("labels") or [] if isinstance(lbl, dict)}
    return {"pipeline-failure", label} <= names and parse_alert(issue) is None


# ── the decision ────────────────────────────────────────────────────────────────────────

@dataclass
class Plan:
    new: list[Finding] = field(default_factory=list)
    # Already open, but failing for a show it wasn't: (finding, alert, the added shows).
    changed: list[tuple[Finding, OpenAlert, list[str]]] = field(default_factory=list)
    remind: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    ongoing: list[tuple[Finding, OpenAlert]] = field(default_factory=list)
    recovered: list[tuple[Finding, OpenAlert]] = field(default_factory=list)

    @property
    def should_post(self) -> bool:
        return bool(self.new or self.changed or self.remind or self.recovered)


def decide(findings: dict[str, Finding], open_alerts: dict[str, OpenAlert], today: date) -> Plan:
    plan = Plan()
    for key in sorted(findings):
        finding = findings[key]
        alert = open_alerts.get(key)
        if finding.failing:
            added = sorted(set(finding.subjects) - set(alert.subjects)) if alert else []
            if alert is None:
                plan.new.append(finding)
            elif added:
                plan.changed.append((finding, alert, added))
            elif alert.last_posted is None or (today - alert.last_posted).days >= REMIND_AFTER_DAYS:
                # last_posted None: the issue exists but its Slack message never landed.
                plan.remind.append((finding, alert))
            else:
                plan.ongoing.append((finding, alert))
        elif alert is not None:
            unknown = set(finding.unknown_subjects)
            if unknown and (not alert.subjects or unknown & set(alert.subjects)):
                continue  # the show it was failing for couldn't be checked today: leave it
            plan.recovered.append((finding, alert))
    return plan


# ── what gets said ──────────────────────────────────────────────────────────────────────

def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


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


def render_slack(
    ctx: RunContext,
    plan: Plan,
    today: date,
    run_url: str,
    new_issues: dict[str, tuple[Optional[int], str]],
    notes: list[str] = (),
    state_note: str = "",
) -> str:
    if plan.new or plan.changed:
        n = len(plan.new) + len(plan.changed)
        head = f":rotating_light: *list-maker · {ctx.label}* — {n} new problem{'s' if n > 1 else ''}"
    elif plan.remind:
        head = f":hourglass_flowing_sand: *list-maker · {ctx.label}* — still failing"
    else:
        head = f":white_check_mark: *list-maker · {ctx.label}* — recovered"
    lines = [head]
    for finding in plan.new:
        number, url = new_issues.get(finding.key, (None, ""))
        lines.append("")
        lines.append(f"*{finding.guide.title}* (`{finding.name}`) · {_issue_link(number, url)}")
        lines.extend(_detail_block(finding, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Usually:_ {finding.guide.usually}")
        lines.append(f"_Check first:_ {finding.guide.check_first}")
    for finding, alert, added in plan.changed:
        lines.append("")
        lines.append(
            f"*{finding.guide.title}* (`{finding.name}`) — now also failing for "
            f"{', '.join(added)} · {_issue_link(alert.number, alert.url)} "
            f"(open since {_day(alert.since)})"
        )
        lines.extend(_detail_block(finding, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Check first:_ {finding.guide.check_first}")
    for finding, alert in plan.remind:
        days = (today - alert.since).days
        lines.append("")
        lines.append(
            f"*Still failing since {_day(alert.since)}* ({days} day{'s' if days != 1 else ''}): "
            f"{finding.guide.title} (`{finding.name}`) · {_issue_link(alert.number, alert.url)}"
        )
        lines.extend(_detail_block(finding, DETAIL_LINES_IN_SLACK))
        lines.append(f"_Check first:_ {finding.guide.check_first}")
    for finding, alert in plan.recovered:
        lines.append("")
        lines.append(
            f"*Recovered:* {finding.guide.title} — failing since "
            f"{_day(alert.since)} · {_issue_link(alert.number, alert.url)} (closed)"
        )
    if plan.ongoing:
        quiet = ", ".join(
            f"{f.guide.title} ({_issue_link(a.number, a.url)})" for f, a in plan.ongoing
        )
        lines.append("")
        lines.append(f"_Also still failing, already reported:_ {quiet}")
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


def issue_body(ctx: RunContext, finding: Finding, *, since: date, last_posted: Optional[date],
               runs: int, today: date, run_url: str, subjects: Optional[list[str]] = None) -> str:
    state = {
        "key": finding.key,
        "since": since.isoformat(),
        "last_posted": last_posted.isoformat() if last_posted else None,
        "runs": runs,
        # Remembered only once announced, so a lost message is retried next run.
        "subjects": finding.subjects if subjects is None else subjects,
    }
    details = "\n".join(f"- {_clip(d, 600)}" for d in finding.details[:DETAIL_LINES_IN_ISSUE])
    if len(finding.details) > DETAIL_LINES_IN_ISSUE:
        details += f"\n- … +{len(finding.details) - DETAIL_LINES_IN_ISSUE} more in the run log"
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
        f"**Failing since:** {since.isoformat()} · **runs failing:** {runs} · "
        f"**latest run:** {run_url}",
        "",
        "_Kept by `pipeline/announce.py`. Slack hears about this when it starts, again "
        f"every {REMIND_AFTER_DAYS} days while it lasts, and when it recovers — this issue "
        "then closes itself. A hidden comment at the top of this description is the "
        "alert's memory (edit view shows it); leave it in place._",
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

    def open_issues(self) -> list[dict]:
        issues: list[dict] = []
        for page in range(1, 11):
            batch = self._request("GET", f"/issues?state=open&per_page=100&page={page}") or []
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                break
        return issues

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

# How recently the music workflow must have spoken about a show for the entities run to
# leave that show to it: its weekly reminder cadence plus the gap between its runs.
OWNER_ACTIVE_DAYS = REMIND_AFTER_DAYS + 3


def _leave_owned_shows_to_their_owner(
    ctx: RunContext, findings: dict[str, Finding], other_open: dict[str, OpenAlert],
    actions: list[str], today: date,
) -> dict[str, Finding]:
    """The daily entities run backstops the music shows a week past their window. If the
    music workflow is ACTIVELY reporting that show (an open alert it announced within
    OWNER_ACTIVE_DAYS), a second thread about the same outage is noise: the show is
    dropped from the entities finding, and a finding left with no show of its own is set
    aside as not evaluated (neither new nor a recovery).

    Two limits, both from review (2026-09-23). Only real music shows are deferred —
    pipeline.yml's manual show_id=3 writes `music-ai-daily-brief:` keys that no
    scheduled run re-evaluates, and those must never mute the entities run's own show.
    And only an owner that is still speaking: if pipeline.yml stops being dispatched
    with an alert open (the July 2026 failure), its reminders stop too, and the
    backstop has to take over rather than defer to a thread nobody updates."""
    if ctx.workflow != "entities":
        return findings
    music = {cfg.slug for cfg in shows_with_spotify()}

    def owner_active(slug: str) -> bool:
        return any(
            k.startswith(f"music-{slug}:") and a.last_posted is not None
            and (today - a.last_posted).days <= OWNER_ACTIVE_DAYS
            for k, a in other_open.items()
        )

    out = dict(findings)
    for key, finding in findings.items():
        if not (finding.failing and finding.subjects):
            continue
        owned = {s for s in finding.subjects if s in music and owner_active(s)}
        if not owned:
            continue
        remaining = [s for s in finding.subjects if s not in owned]
        actions.append(f"left {', '.join(sorted(owned))} in {key} to the music workflow's open alert")
        if remaining:
            out[key] = Finding(finding.key, finding.name, finding.guide, True, finding.summary,
                               finding.details, remaining, finding.unknown_subjects)
        else:
            del out[key]
    return out


@dataclass
class Outcome:
    """What announce() did, for the step summary and the tests."""

    plan: Plan
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
) -> Outcome:
    actions: list[str] = []
    label = WORKFLOWS[ctx.workflow]["label"]

    def write(desc: str, fn: Callable[[], Any]) -> Any:
        """One GitHub write, logged; in a dry run, only logged. A failed write is
        reported and skipped — it must not cost the Slack message."""
        actions.append(("would " if dry_run else "") + desc)
        if dry_run:
            return None
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            actions.append(f"  FAILED: {exc}")
            return None

    # 1. The memory. If GitHub can't be read, nothing can be deduplicated: say every
    #    current failure (loud beats silent) and change nothing.
    open_alerts: dict[str, OpenAlert] = {}
    other_open: dict[str, OpenAlert] = {}  # other workflows' alerts, for owner coverage
    duplicates: list[tuple[OpenAlert, OpenAlert]] = []
    legacy: list[dict] = []
    state_note = ""
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
    except Exception as exc:  # noqa: BLE001
        failing = {k: f for k, f in findings.items() if f.failing}
        plan = Plan(new=list(failing.values()))
        if not plan.should_post:
            return Outcome(plan, None, False, [f"could not read alert state ({exc}); nothing failing, so quiet"], list(notes))
        message = render_slack(ctx, plan, today, run_url, {}, notes,
                               state_note=f"Couldn't read the alert memory (GitHub issues: {exc}), "
                                          "so this may repeat tomorrow.")
        posted = False if dry_run else post(message)
        return Outcome(plan, message, posted, [f"could not read alert state: {exc}"], list(notes))

    findings = _leave_owned_shows_to_their_owner(ctx, findings, other_open, actions, today)
    plan = decide(findings, open_alerts, today)

    # 2. Open an issue for each new failure first, so the Slack message can link it.
    #    last_posted stays empty until the message is known to have landed.
    new_issues: dict[str, tuple[Optional[int], str]] = {}
    for finding in plan.new:
        created = write(
            f"open issue for {finding.key}",
            lambda f=finding: gh.create_issue(
                issue_title(ctx, f, today),
                issue_body(ctx, f, since=today, last_posted=None, runs=1, today=today, run_url=run_url),
                ["pipeline-failure", label],
            ),
        )
        if created:
            new_issues[finding.key] = (created.get("number"), created.get("html_url", ""))

    # 3. At most one Slack message for the whole run.
    message = None
    posted = False
    if plan.should_post:
        message = render_slack(ctx, plan, today, run_url, new_issues, notes, state_note)
        posted = False if dry_run else post(message)
        actions.append(("would post" if dry_run else ("posted" if posted else "Slack post FAILED"))
                       + " one Slack message")

    # 4. Bring each issue's memory up to date.
    for finding in plan.new:
        number = new_issues.get(finding.key, (None, ""))[0]
        if number and posted:
            write(f"mark #{number} as announced", lambda f=finding, n=number: gh.edit_issue(
                n, body=issue_body(ctx, f, since=today, last_posted=today, runs=1, today=today,
                                   run_url=run_url)))
    for finding, alert, added in plan.changed:
        write(f"refresh #{alert.number} (now also {', '.join(added)})",
              lambda f=finding, a=alert: gh.edit_issue(a.number, body=issue_body(
                  ctx, f, since=a.since, last_posted=today if posted else a.last_posted,
                  runs=a.runs + 1, today=today, run_url=run_url,
                  subjects=None if posted else a.subjects)))
        if posted:
            write(f"comment on #{alert.number}", lambda a=alert, ad=added: gh.comment(
                a.number, f"Now also failing for {', '.join(ad)} on {today.isoformat()}. "
                          f"Slack was told. Latest run: {run_url}"))
    for finding, alert in plan.remind:
        write(f"refresh #{alert.number} (reminder{' posted' if posted else ' not posted'})",
              lambda f=finding, a=alert: gh.edit_issue(a.number, body=issue_body(
                  ctx, f, since=a.since, last_posted=today if posted else a.last_posted,
                  runs=a.runs + 1, today=today, run_url=run_url)))
        if posted:
            days = (today - alert.since).days
            write(f"comment on #{alert.number}", lambda a=alert, d=days: gh.comment(
                a.number, f"Still failing on {today.isoformat()} — {d} days since "
                          f"{a.since.isoformat()}. Slack was reminded. Latest run: {run_url}"))
    for finding, alert in plan.ongoing:
        write(f"refresh #{alert.number} quietly", lambda f=finding, a=alert: gh.edit_issue(
            a.number, body=issue_body(ctx, f, since=a.since, last_posted=a.last_posted,
                                      runs=a.runs + 1, today=today, run_url=run_url)))
    for finding, alert in plan.recovered:
        # Two writes, not one: a failed comment must not leave the issue open, or the
        # next green run would announce the same recovery again.
        write(f"comment on #{alert.number} (recovered)", lambda a=alert: gh.comment(
            a.number, f"Recovered on {today.isoformat()}: this run passed. "
                      f"It had been failing since {a.since.isoformat()}. {run_url}"))
        write(f"close #{alert.number} as recovered", lambda a=alert: gh.edit_issue(
            a.number, state="closed", state_reason="completed"))
    for dup, keep in duplicates:
        write(f"close #{dup.number} as a duplicate of #{keep.number}", lambda d=dup, k=keep: (
            gh.comment(d.number, f"Closing: #{k.number} already tracks `{d.key}`."),
            gh.edit_issue(d.number, state="closed", state_reason="not_planned"),
        ))

    # 5. Retire the pre-announcer thread(s) through the same path.
    still = [(f.guide.title, a.number) for f, a in plan.remind + plan.ongoing]
    still += [(f.guide.title, a.number) for f, a, _ in plan.changed]
    still += [(f.guide.title, new_issues.get(f.key, (None, ""))[0]) for f in plan.new]
    now = ("Everything this run checks is passing." if not still else
           "Failing now, each in its own issue: "
           + ", ".join(f"{t} (#{n})" if n else t for t, n in still) + ".")
    for issue in legacy:
        write(f"comment on legacy #{issue['number']}", lambda i=issue: gh.comment(
            i["number"],
            "Retiring this thread. Failures now get one issue per failing check, opened when "
            "it starts, commented weekly while it lasts, and closed with a 'recovered' "
            f"comment when it passes (pipeline/announce.py). {now} Run {today.isoformat()}: "
            f"{run_url}"))
        write(f"retire legacy #{issue['number']}", lambda i=issue: gh.edit_issue(
            i["number"], state="closed", state_reason="completed"))
    return Outcome(plan, message, posted, actions, list(notes))


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
        ("Reminded (7+ days)", [f.key for f, _ in plan.remind]),
        ("Still failing (quiet)", [f.key for f, _ in plan.ongoing]),
        ("Recovered", [f.key for f, _ in plan.recovered]),
    ):
        if items:
            lines.append(f"- **{heading}:** " + ", ".join(f"`{k}`" for k in items))
    if not (plan.new or plan.changed or plan.remind or plan.ongoing or plan.recovered):
        lines.append("- Nothing failing, nothing recovered: no message.")
    if outcome.notes:
        # Mid-run warnings (common.alert_note). They ride in the Slack message when
        # there is one; this is where they stay visible when there isn't.
        lines += ["", "Notes from this run:"] + [f"- {n}" for n in outcome.notes]
    lines += ["", "Actions:"] + [f"- {a}" for a in outcome.actions]
    if outcome.message:
        lines += ["", "Message:", "```", outcome.message, "```"]
    return "\n".join(lines)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--workflow", required=True, choices=sorted(WORKFLOWS))
    parser.add_argument("--show-id", help="pipeline.yml's show_id (music runs only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read the issue memory and print what would be said; write nothing.")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    ctx = RunContext.build(args.workflow, args.show_id)
    run_url = os.getenv("RUN_URL", "")
    try:
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
        )
    except Exception:  # noqa: BLE001 — a broken alert layer must be loud, never silent
        traceback.print_exc()
        post_slack(f":warning: *list-maker · {ctx.label}* — the alert step itself crashed, so "
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

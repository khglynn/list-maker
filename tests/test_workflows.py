"""Invariants of the GitHub workflow files that no Python test would otherwise see.

Workflows here run only when the Cloudflare Worker dispatches them, so a mistake in one
surfaces as a red run (or, worse, a silent one) days later. These checks read the YAML
as text: the repo carries no YAML parser, and every invariant below is a line-level fact.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _top_level_concurrency_group(text: str) -> str | None:
    """The `group:` value of the workflow-level `concurrency:` block, or None."""
    match = re.search(r"^concurrency:\s*\n(?:[ \t]+.*\n)*?[ \t]+group:\s*(.+)$", text, re.M)
    return match.group(1).strip() if match else None


def _called_workflows() -> set[str]:
    """Every workflow file some other workflow runs with `uses: ./.github/workflows/…`."""
    called: set[str] = set()
    for path in WORKFLOWS.glob("*.yml"):
        for name in re.findall(r"uses:\s*\./\.github/workflows/([\w.-]+\.ya?ml)", path.read_text()):
            called.add(name)
    return called


def test_pulse_is_still_called_by_entities():
    # The deadlock guard below only matters while pulse runs as a called workflow. If
    # that changes, this test says so instead of guarding a situation that no longer
    # exists.
    assert "pulse.yml" in _called_workflows()


def test_called_workflows_do_not_take_the_callers_concurrency_group():
    """A called workflow must not key its concurrency group on github.workflow.

    Inside a workflow_call, `github.workflow` is the CALLER's name, so the called
    workflow asks for the group its caller already holds. GitHub cancels it as a
    deadlock. That is what killed the 2026-09-15 pulse, silently (DEVLOG 2026-09-23).
    """
    for name in sorted(_called_workflows()):
        group = _top_level_concurrency_group(_read(name))
        if group is None:
            continue
        assert "github.workflow" not in group, (
            f"{name} is run via workflow_call, and its concurrency group {group!r} "
            "resolves to the caller's name — the caller already holds that group, so "
            "GitHub cancels this workflow as a deadlock. Use a literal group."
        )


def test_entities_health_check_judges_only_the_shows_it_imports():
    """A music show's normal wait between imports belongs to pipeline.yml, which checks
    right after each import. Unscoped, the daily entities run failed SOP's ordinary
    Friday-to-Wednesday wait as "entity pipeline FAILED" (2026-09-22)."""
    text = _read("entities.yml")
    assert re.search(r"data_health\.py[^\n]*--music-as-backstop", text)
    # Never the run's own show list (review C3): a narrower manual run would move the
    # shows it skipped onto the backstop window and "recover" their open alerts.
    assert "--feed-owned-shows" not in text


def test_music_workflow_keeps_its_own_strict_feed_check():
    assert re.search(r"data_health\.py --feed-check-only --shows \"\$SLUGS\" --strict", _read("pipeline.yml"))


ANNOUNCED = {"entities.yml": "entities", "pipeline.yml": "music", "blogs.yml": "intake"}


def test_each_scheduled_workflow_speaks_through_the_announce_step_only():
    """One voice per run (pipeline/announce.py). The old per-workflow Slack curl and the
    daily "Failed again" issue comment are what made a red day two pings and a comment."""
    for name, workflow in ANNOUNCED.items():
        text = _read(name)
        step = text[text.index("- name: Announce (Slack + failure issues"):]
        assert re.search(r"^\s+if: always\(\)\s*$", step, re.M), name
        assert f"ARGS=(--workflow {workflow}" in step, name
        assert "python3 pipeline/announce.py" in step, name
        for leftover in ("Notify Slack (failure)", "Create issue on failure", "hooks.slack.com"):
            assert leftover not in text, f"{name} still has {leftover!r}"
        # The only direct post left is the announce step's fallback for a run whose
        # checkout failed (so announce.py isn't on disk).
        assert text.count('-X POST "$SLACK_WEBHOOK_URL"') == 1, name
        assert step.index("if [ ! -f pipeline/announce.py ]") < step.index('-X POST "$SLACK_WEBHOOK_URL"'), name
        assert re.search(r"^  issues: write$", text, re.M), f"{name} needs issues: write"
        assert "ALERT_DETAILS_DIR: ${{ github.workspace }}/.alert-details" in text, name


def test_the_steps_the_announcer_tracks_exist_in_each_workflow():
    """announce.WORKFLOWS names step ids; a renamed or missing id would make that step
    invisible to the alerts (it would never count as run, so never fail or recover)."""
    from pipeline.announce import WORKFLOWS as TRACKED

    for name, workflow in ANNOUNCED.items():
        ids = set(re.findall(r"^\s+id: ([\w-]+)\s*$", _read(name), re.M))
        missing = set(TRACKED[workflow]["steps"]) - ids
        assert not missing, f"{name} lacks step id(s) {sorted(missing)}"


def test_every_health_check_run_hands_its_results_to_the_announcer():
    for name in ("entities.yml", "pipeline.yml"):
        runs = re.findall(r"python data_health\.py(?:[^\n]*\\\n)*[^\n]*", _read(name))
        assert runs, name
        for line in runs:
            assert '--results-file "$ALERT_DETAILS_DIR/health.json"' in line, (name, line)


def test_announce_is_the_last_step_of_its_job():
    """It judges the whole run from the steps before it; a step added after it would be
    invisible to the alerts (never failed, never recovered)."""
    for name in ANNOUNCED:
        text = _read(name)
        after = text[text.index("- name: Announce (Slack + failure issues"):]
        later_steps = re.findall(r"^      - (?:name|uses|run):", after, re.M)
        assert later_steps == [], f"{name}: a step follows Announce"


def test_every_step_has_a_timeout_that_fits_inside_the_job():
    """Review finding C6 (2026-09-23): a job cut off by its own time limit ends
    "cancelled", the shape that hid the May-June 2026 playlist outage. Every step gets
    its own timeout, and their sum stays under the job's, so a hang fails a step (which
    is announced) before the job limit can cancel anything."""
    for name in ANNOUNCED:
        text = _read(name)
        job_limit = int(re.search(r"^    timeout-minutes: (\d+)$", text, re.M).group(1))
        steps = re.split(r"^      - ", text.split("\n    steps:\n", 1)[1], flags=re.M)[1:]
        total = 0
        for step in steps:
            m = re.search(r"^        timeout-minutes: (\d+)$", step, re.M)
            assert m, f"{name}: step without timeout-minutes: {step.splitlines()[0]}"
            total += int(m.group(1))
        assert total < job_limit, f"{name}: steps may take {total} min, job allows {job_limit}"

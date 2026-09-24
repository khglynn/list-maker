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

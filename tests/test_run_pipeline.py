"""TAL episode discovery in the music orchestrator.

TAL had no discovery step: its scraper starts from rows already in `episodes`, so
it could only fill songs for episodes something else had inserted. Nothing did, and
TAL drifted 6 episodes behind its feed while every Monday run reported success.
These tests pin the discovery call, and pin that a discovery failure is LOUD —
the original bug was silence, not a crash.
"""

import subprocess

import pytest

from pipeline import run_pipeline


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_discovery_invokes_the_taddy_importer_for_tal(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Completed(stdout="[tal] done: imported=2")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_pipeline.discover_tal_episodes(dry_run=False)

    assert len(calls) == 1
    cmd = calls[0]
    assert "import_transcripts.py" in " ".join(cmd)
    assert "--shows" in cmd and "tal" in cmd


def test_discovery_is_skipped_on_dry_run(monkeypatch) -> None:
    def fail(*a, **k):  # a dry run must not touch Taddy or the DB
        raise AssertionError("dry run must not shell out")

    monkeypatch.setattr(subprocess, "run", fail)

    assert run_pipeline.discover_tal_episodes(dry_run=True)["dry_run"] is True


def test_discovery_failure_raises_instead_of_passing_silently(monkeypatch) -> None:
    """The whole defect was a green run that discovered nothing. A non-zero exit
    must reach run_pipeline's handler, which marks the run failed and Slacks."""
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Completed(returncode=1, stderr="taddy 500")
    )

    with pytest.raises(RuntimeError, match="TAL episode discovery failed"):
        run_pipeline.discover_tal_episodes(dry_run=False)

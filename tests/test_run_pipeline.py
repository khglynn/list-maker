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


def _patch_counts(monkeypatch, counts: list[int]) -> None:
    """count_tal_episodes is called before and after the import."""
    monkeypatch.setattr(run_pipeline, "count_tal_episodes", lambda: counts.pop(0))


def test_discovery_invokes_the_taddy_importer_for_tal(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Completed(stdout="[tal] done: imported=2")

    monkeypatch.setattr(subprocess, "run", fake_run)
    _patch_counts(monkeypatch, [889, 891])

    result = run_pipeline.discover_tal_episodes(dry_run=False)

    assert len(calls) == 1
    cmd = calls[0]
    assert "import_transcripts.py" in " ".join(cmd)
    assert "--shows" in cmd and "tal" in cmd
    # Bounded: never sweep the whole archive on a routine run.
    assert "--per-show-limit" in cmd
    assert result["discovered"] == 2


def test_discovery_reports_zero_rather_than_staying_quiet(monkeypatch) -> None:
    """A week with no new episodes must still SAY so — 'discovered 0' every week is
    the signal that discovery itself has broken, which is what we missed for 10 weeks."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(stdout=""))
    _patch_counts(monkeypatch, [889, 889])

    assert run_pipeline.discover_tal_episodes(dry_run=False)["discovered"] == 0


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
    _patch_counts(monkeypatch, [889, 889])

    with pytest.raises(RuntimeError, match="TAL episode discovery failed"):
        run_pipeline.discover_tal_episodes(dry_run=False)


# ---------------------------------------------------------------------------
# Partial step failures redden the run without aborting it
#
# A Monday where Firecrawl failed on every page used to exit 0: the scrape returned a
# summary listing the failures, nothing raised, and pipeline.yml's Slack step is
# `if: failure()`. "Found no work" and "could not do the work" reported identically —
# the same shape as the outage this arc exists to close.
# ---------------------------------------------------------------------------

def _music_show(monkeypatch, scrape_result: dict) -> dict:
    """Run the orchestrator for TAL with every step faked. No network, no DB."""
    ran: list[str] = []

    def _scrape(show_id, dry_run, yes):
        ran.append("scrape")
        return scrape_result

    def _match(show_id, dry_run, cache_path):
        ran.append("match")
        return {"high": 1, "medium": 0, "low": 0, "not_found": 0}

    def _sync(show_id, dry_run, cache_path):
        ran.append("sync")
        return {"added": 1}

    monkeypatch.setattr(run_pipeline, "run_scrape", _scrape)
    monkeypatch.setattr(run_pipeline, "run_match", _match)
    monkeypatch.setattr(run_pipeline, "run_sync", _sync)

    summary = run_pipeline.run_pipeline(show_id=2, dry_run=False, yes=True)
    summary["_ran"] = ran
    return summary


def test_a_scrape_with_fetch_failures_fails_the_run(monkeypatch) -> None:
    """Non-zero exit is what makes pipeline.yml's `if: failure()` Slack step fire."""
    summary = _music_show(monkeypatch, {"fetched": 0, "failures": 24, "errors": ["boom"]})

    assert summary["success"] is False
    assert summary["step_failures"] == [{"step": "scrape", "failures": 24}]
    assert "24" in summary["error"]


def test_fetch_failures_do_not_throw_away_the_pages_that_worked(monkeypatch) -> None:
    """Red AND useful. Raising on the first failure would strand every page that did come
    back — unparsed, uninserted, unmatched, unsynced — until someone noticed by hand."""
    summary = _music_show(monkeypatch, {"fetched": 20, "failures": 4, "errors": ["boom"]})

    assert summary["_ran"] == ["scrape", "match", "sync"], "match and sync must still run"
    assert summary["steps"]["sync"] == {"added": 1}
    assert summary["success"] is False


def test_unresolved_only_does_not_redden_the_run(monkeypatch) -> None:
    """Row 7422 has no page url anywhere and never will. Counting it would fail the run
    every single Monday — an alert that can never be cleared is how a real one gets
    ignored. It is already printed and counted as `unresolved`."""
    summary = _music_show(
        monkeypatch,
        {"fetched": 23, "failures": 0, "unresolved": 1, "errors": ["No page URL for 7422"]},
    )

    assert summary["success"] is True
    assert "step_failures" not in summary


def test_a_clean_scrape_stays_green(monkeypatch) -> None:
    summary = _music_show(monkeypatch, {"fetched": 3, "failures": 0, "errors": []})

    assert summary["success"] is True
    assert summary["error"] is None


def test_record_step_failures_is_generic_across_steps() -> None:
    """One key, any step. A sync PR that surfaces dropped playlist batches sets
    `failures` and needs no change in run_pipeline."""
    summary = {"success": True, "error": None}

    run_pipeline.record_step_failures(summary, "sync", {"added": 40, "failures": 60})

    assert summary["success"] is False
    assert summary["step_failures"] == [{"step": "sync", "failures": 60}]
    assert run_pipeline.STEP_FAILURE_KEY == "failures"


def test_record_step_failures_tolerates_a_step_that_returns_nothing() -> None:
    summary = {"success": True, "error": None}

    run_pipeline.record_step_failures(summary, "scrape", None)
    run_pipeline.record_step_failures(summary, "scrape", {})

    assert summary["success"] is True

"""Spotify playlist sync — the failure paths the orchestrator reads.

Deliberately thin: the module's live surface (playlist diff, dedup, the Spotify
client) has no coverage yet — that is Phase 5's job. What is pinned here is the exit
code, because run_new_episodes.run_script branches on it and a silent drift back to
exit 1 would put an unknown --show-id back into the retry loop.
"""

from __future__ import annotations

import pytest

from pipeline import sync_playlist


def test_unknown_show_id_exits_deterministically(monkeypatch) -> None:
    """An unknown --show-id is refused before any Spotify or DB call and fails the
    same way every time, so it exits 2 and run_script does not retry it."""
    monkeypatch.setattr(
        "sys.argv", ["sync_playlist.py", "--show-id", "9999", "--dry-run"]
    )
    with pytest.raises(SystemExit) as exc:
        sync_playlist.main()
    assert exc.value.code == 2


def test_bad_show_id_argument_also_exits_two(monkeypatch) -> None:
    """argparse's own usage exit is already 2, so a mistyped argument lands in the
    no-retry branch without this file doing anything. Pinned so the two conventions
    are known to agree."""
    monkeypatch.setattr(
        "sys.argv", ["sync_playlist.py", "--show-id", "not-a-number"]
    )
    with pytest.raises(SystemExit) as exc:
        sync_playlist.main()
    assert exc.value.code == 2

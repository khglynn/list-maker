"""Blog Pull Queue: the counts and the weekly line that make a dry week visible.

Eleven consecutive weekly runs (2026-06-21 → 08-31) found nothing new and said nothing,
while 31 candidates sat un-triaged. These pin the two things that end that: the count
of what is waiting, and a line that posts every week regardless.
"""

from __future__ import annotations

from pipeline import build_pull_queue as bpq


def _page(checked: bool, created: str) -> dict:
    return {"properties": {"Pull": {"checkbox": checked}}, "created_time": created}


def test_queue_counts_paginates_and_splits_checked(monkeypatch) -> None:
    pages = [
        {"results": [_page(False, "2026-06-11T00:00:00.000Z"), _page(True, "2026-06-14T00:00:00.000Z")],
         "has_more": True, "next_cursor": "c2"},
        {"results": [_page(False, "2026-08-01T00:00:00.000Z")], "has_more": False},
    ]
    seen: list[dict] = []

    def fake_request(method, url, token, body):
        seen.append(body)
        return pages[len(seen) - 1]

    monkeypatch.setattr(bpq, "notion_request", fake_request)
    counts = bpq.queue_counts("tok", "db")
    assert counts["candidates"] == 3 and counts["checked"] == 1
    assert counts["oldest_days"] > 60  # the June rows are the old ones
    assert seen[0]["filter"] == {"property": "Status", "select": {"equals": "candidate"}}
    assert seen[1]["start_cursor"] == "c2"


def test_weekly_line_speaks_on_a_dry_week() -> None:
    line = bpq.weekly_line(0, {"candidates": 31, "checked": 0, "oldest_days": 79})
    assert "0 new candidate(s)" in line
    assert "31 awaiting your checkbox, oldest 79d" in line
    assert bpq.QUEUE_URL in line


def test_weekly_line_reports_checked_rows_and_an_empty_queue() -> None:
    assert "2 checked" in bpq.weekly_line(3, {"candidates": 5, "checked": 2, "oldest_days": 1})
    assert "queue empty" in bpq.weekly_line(0, {"candidates": 0, "checked": 0, "oldest_days": 0})

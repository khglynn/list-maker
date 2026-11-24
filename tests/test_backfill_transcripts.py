"""The archive transcript backfill: rebuilds a feed item from a held row, adds only."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline" / "scrapers" / "rss"))
import backfill_transcripts as bt  # noqa: E402


def test_an_archive_row_is_its_own_page_url() -> None:
    item = bt.item_from_row({"title": "The Psychopath Test", "url": "https://www.thisamericanlife.org/436/the-psychopath-test",
                             "publish_date": date(2011, 5, 13), "raw_content": None})
    assert item.link.endswith("/436/the-psychopath-test") and item.duration_seconds is None


def test_a_feed_or_taddy_row_keeps_its_link_and_length_in_raw_content() -> None:
    feed = bt.item_from_row({"title": "902", "url": "46259 at https://www.thisamericanlife.org", "publish_date": date(2026, 11, 16),
                             "raw_content": json.dumps({"link": "https://www.thisamericanlife.org/902/x", "duration_seconds": 3546})})
    assert feed.link.endswith("/902/x") and feed.duration_seconds == 3546
    taddy = bt.item_from_row({"title": "t", "url": "https://api.taddy.org/podcast-episode/u", "publish_date": date(2026, 1, 1),
                              "raw_content": json.dumps({"websiteUrl": "https://www.npr.org/2026/01/01/nx-s1-1/x", "duration": 1800})})
    assert "nx-s1-1" in taddy.link and taddy.duration_seconds == 1800

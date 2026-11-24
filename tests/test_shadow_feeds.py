"""The read-only shadow run (pipeline/shadow_feeds.py).

The property that matters most is the one a public repo needs: transcript text never
reaches the step summary or the log line, whatever the comparison found.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))
sys.path.insert(0, str(PIPELINE / "scrapers" / "rss"))
import shadow_feeds as sh  # noqa: E402

NOW = datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc)
SECRET_PHRASE = "zebrafinch quartermaster"  # stands in for publisher text


def test_entity_agreement_counts_only_what_the_reference_text_named() -> None:
    forms = [
        {"Claude Code"},                       # in both
        {"Semafor"},                           # Taddy misheard it; the publisher spells it right
        {"KPMG AI Amplifiers Study"},          # the extractor's paraphrase; in neither
        {"Google Antigravity", "Antigravity"},  # near spelling in the new text
    ]
    taddy = "we used claude code and read semafor and google antigravity"
    new = "we used Claude Code and read Semaphore and Google Anti-Gravity"
    result = sh.entity_agreement(forms, taddy, new)
    assert result["entities"] == 4 and result["entities_in_taddy"] == 3
    assert result["recall_exact"] == round(1 / 3, 3)
    assert result["recall_fuzzy"] >= round(2 / 3, 3)


def test_whole_word_matching_does_not_find_a_name_inside_another_word() -> None:
    assert sh._contains("the xai team", "xAI")
    assert not sh._contains("taxation", "xai")


def test_word_overlap_is_the_share_of_the_reference_vocabulary_kept() -> None:
    assert sh.word_overlap("a b c d", "a b c x") == 0.75
    assert sh.word_overlap("", "a") is None


def test_ad_markers_are_counted() -> None:
    assert sh.ad_phrases("Today's episode is brought to you by X. Also sponsored by Y.") == 2


class _Conn:
    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return {"transcript_text": f"taddy said {SECRET_PHRASE} Claude " + "word " * 2000}

    def fetchall(self):
        return [{"entity_id": 1, "mention_texts": ["Claude"], "canonical_name": "Claude", "aliases": ["Claude AI"]}]


def test_nothing_the_publisher_wrote_reaches_the_summary_or_the_log_line(monkeypatch, capsys) -> None:
    from scrapers.transcripts.base import Transcript

    item = SimpleNamespace(
        title="Ep", publish_date=date(2026, 11, 24), duration_seconds=600,
        published_at=datetime(2026, 11, 24, 12, tzinfo=timezone.utc),
    )
    reader = SimpleNamespace(fetch=lambda it, session=None: Transcript(
        text=f"publisher said {SECRET_PHRASE} Claude " + "word " * 2000, source_type="publisher_transcript",
        model="npr_transcript_page", is_generated=False, source_url="https://www.npr.org/transcripts/nx-s1-1"))
    monkeypatch.setattr(sh, "reader_for", lambda source: reader)
    cfg = SimpleNamespace(transcript_source="npr")
    check = sh.check_transcript(_Conn(), cfg, item, 42, NOW)
    assert check.status == "found" and check.recall_exact == 1.0

    result = sh.ShowShadow("pchh", feed_items=1, transcripts=[sh.asdict(check)])
    markdown = sh.render_markdown([result], NOW)
    blob = json.dumps(sh.asdict(result))
    assert SECRET_PHRASE not in markdown and SECRET_PHRASE not in blob
    assert "found" in markdown


def test_a_show_whose_feed_fails_is_reported_not_raised(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("feed down")

    monkeypatch.setattr(sh, "fetch_feed", boom)
    out = sh.shadow_show(None, SimpleNamespace(slug="pchh", feed_url="https://x"), NOW)
    assert not out.ok and "feed down" in out.error
    assert "could not check" in sh.render_markdown([out], NOW)


def test_machine_gods_is_shadow_only_until_its_show_row_exists() -> None:
    from show_config import SHOWS

    assert "machine-gods" not in SHOWS
    assert sh.MACHINE_GODS.transcript_source == "npr" and sh.MACHINE_GODS.show_id == 0

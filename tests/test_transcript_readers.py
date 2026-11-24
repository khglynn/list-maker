"""Publisher transcript readers (pipeline/scrapers/transcripts/).

Every page here is a few made-up lines in the publisher's SHAPE, never its text: the
repo is public. Lengths are padded with a filler word so the sanity check (words per
audio minute) has something to measure.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# The readers import their siblings as `scrapers.transcripts...` (pipeline/ on the
# path), so the tests do too: the result classes must be the same objects.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from feeds import FeedItem  # noqa: E402
from scrapers.transcripts import READERS, reader_for  # noqa: E402
from scrapers.transcripts import aidailybrief, npr  # noqa: E402
from scrapers.transcripts.base import (  # noqa: E402
    Failed,
    NotYet,
    Transcript,
    sanity_check,
    title_similarity,
)


def _item(title="Why Agents Need Memory", published="2026-11-20T18:30:00", duration=600, link=None) -> FeedItem:
    return FeedItem(
        title=title,
        guid="g-1",
        link=link,
        enclosure_url=None,
        pubdate_raw=None,
        published_at=datetime.fromisoformat(published).replace(tzinfo=timezone.utc),
        description_html="",
        duration_seconds=duration,
    )


def _words(n: int) -> str:
    return " ".join(["word"] * n)


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code, self.text = status_code, text


class _Session:
    """Answers by url; records what was asked."""

    def __init__(self, pages: dict[str, _Resp]):
        self.pages, self.asked = pages, []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return self.pages.get(url, _Resp(404))


# ── AI Daily Brief ────────────────────────────────────────────────────────────


def _aidb_page(title: str, day: str, body_words: int = 2000) -> str:
    return f"# {title} — Transcript ({day})\n\n[00:00:00] Opening line here.\n\n{_words(body_words)}\n\n[00:12:00] Later.\n"


def test_aidb_markdown_loses_its_header_and_timestamps() -> None:
    title, body = aidailybrief.parse_markdown(_aidb_page("Why Agents Need Memory", "2026-11-20", 3))
    assert title == "Why Agents Need Memory"
    assert body == "Opening line here.\n\nword word word\n\nLater."
    assert "[00:" not in body


def test_aidb_looks_up_the_eastern_day_first() -> None:
    # 02:00 UTC on 11-21 is still 11-20 in New York.
    days = aidailybrief.candidate_days(_item(published="2026-11-21T02:00:00"))
    assert days[:2] == [date(2026, 11, 20), date(2026, 11, 21)]
    assert len(days) == len(set(days))


def test_aidb_refuses_another_episodes_page_on_the_same_date() -> None:
    """The probe that found this source matched the day before when a date slipped, and a
    promo item shared a date with the main episode. Only the title tells them apart."""
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    session = _Session({url: _Resp(text=_aidb_page("A Completely Different Story", "2026-11-20"))})
    result = aidailybrief.fetch(_item(), session=session)
    assert isinstance(result, NotYet)
    assert "another episode" in result.reason


def test_aidb_finds_the_page_whose_title_matches() -> None:
    page = _aidb_page("Why Agents Need Memory", "2026-11-20")
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    result = aidailybrief.fetch(_item(), session=_Session({url: _Resp(text=page)}))
    assert isinstance(result, Transcript)
    assert result.source_type == "publisher_transcript" and result.model == "aidailybrief_md"
    assert result.source_url == url


def test_aidb_not_published_yet_is_not_yet() -> None:
    session = _Session({})
    result = aidailybrief.fetch(_item(), session=session)
    assert isinstance(result, NotYet)
    assert len(session.asked) == len(aidailybrief.candidate_days(_item()))


def test_aidb_a_page_too_short_for_the_audio_is_not_stored() -> None:
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    page = _aidb_page("Why Agents Need Memory", "2026-11-20", body_words=400)  # 40 wpm for 10 min
    result = aidailybrief.fetch(_item(), session=_Session({url: _Resp(text=page)}))
    assert isinstance(result, NotYet) and "words per audio minute" in result.reason


def test_aidb_an_item_without_a_date_cannot_be_looked_up() -> None:
    item = FeedItem("t", "g", None, None, None, None, "")
    assert isinstance(aidailybrief.fetch(item, session=_Session({})), Failed)


# ── NPR ───────────────────────────────────────────────────────────────────────


def _npr_page(turns: list[str]) -> str:
    paragraphs = "".join(f"<p>{t}\n" for t in turns)  # NPR leaves <p> unclosed
    return (
        "<html><head><title>A rewritten headline : Example Show : NPR</title></head><body>"
        '<div class="nav"><p>Navigation text that is not the transcript</div>'
        '<div class="transcript storytext"><div class="icn-story-transcript-wrap">'
        '<div class="transcript-label">Transcript</div></div>'
        f"{paragraphs}"
        "<p>Copyright © 2026 NPR. All rights reserved.\n"
        "<p>Accuracy and availability of NPR transcripts may vary.\n"
        "</div><p>Footer text</body></html>"
    )


def test_npr_story_id_from_both_link_shapes() -> None:
    assert npr.story_id("https://www.npr.org/2026/11/20/nx-s1-6047626/some-slug") == "nx-s1-6047626"
    assert npr.story_id("https://www.npr.org/2019/03/01/699334212/older-story") == "699334212"
    assert npr.story_id("https://www.nytimes.com/column/hard-fork") is None
    assert npr.story_id(None) is None


def test_npr_page_keeps_the_turns_and_drops_labels_stage_directions_and_boilerplate() -> None:
    text = npr.parse_transcript_page(_npr_page([
        "STEPHEN THOMPSON, BYLINE: Welcome to the show.",
        "LIM: I loved it. (LAUGHTER) Truly.",
        "(SOUNDBITE OF MUSIC)",
        "Roose: A mixed-case label (with an aside in parentheses) stays as speech.",
    ]))
    assert text.split("\n\n") == [
        "Welcome to the show.",
        "I loved it. Truly.",
        "A mixed-case label (with an aside in parentheses) stays as speech.",
    ]
    for gone in ("Navigation", "Copyright", "Accuracy", "Footer", "Transcript", "THOMPSON", "Roose:"):
        assert gone not in text


def test_npr_fetch_reads_the_items_own_story() -> None:
    link = "https://www.npr.org/2026/11/20/nx-s1-1234567/slug"
    url = npr.TRANSCRIPT_URL.format(story_id="nx-s1-1234567")
    page = _npr_page([f"HOST: {_words(1900)}"])
    result = npr.fetch(_item(link=link), session=_Session({url: _Resp(text=page)}))
    assert isinstance(result, Transcript)
    assert (result.model, result.is_generated, result.source_url) == ("npr_transcript_page", False, url)


def test_npr_takes_no_title_check() -> None:
    """NPR rewrites headlines for the page; the id comes from the item itself, so a
    different page title is not a different episode."""
    link = "https://www.npr.org/2026/11/20/nx-s1-1234567/slug"
    url = npr.TRANSCRIPT_URL.format(story_id="nx-s1-1234567")
    page = _npr_page([f"HOST: {_words(1900)}"]).replace("A rewritten headline", "Nothing alike")
    assert isinstance(npr.fetch(_item(title="Feed Title", link=link), session=_Session({url: _Resp(text=page)})), Transcript)


def test_npr_without_a_story_id_fails_rather_than_waiting_forever() -> None:
    assert isinstance(npr.fetch(_item(link="https://example.org/x"), session=_Session({})), Failed)


def test_npr_page_without_transcript_text_is_not_yet() -> None:
    link = "https://www.npr.org/2026/11/20/nx-s1-1234567/slug"
    url = npr.TRANSCRIPT_URL.format(story_id="nx-s1-1234567")
    result = npr.fetch(_item(link=link), session=_Session({url: _Resp(text="<html>No transcript</html>")}))
    assert isinstance(result, NotYet)


# ── Shared guards and the registry ────────────────────────────────────────────


def test_title_similarity_tolerates_punctuation_and_subtitles_but_not_another_episode() -> None:
    assert title_similarity("Anthropic’s Record IPO + What Kevin Got Wrong", "Anthropic's Record IPO + What Kevin Got Wrong") == 100
    assert title_similarity("Why the AI Kids Hate Astra 6.2", "Why the AI Kids Hate Astra 6.2 (Plus: News)") >= 80
    assert title_similarity("When AI Labs Rival Nations", "Too Big to Fire: Jensen Huang Is Back") < 80


def test_sanity_check_bounds() -> None:
    assert sanity_check(_words(2000), 600) is None  # 200 wpm
    assert "words" in sanity_check(_words(100), None)
    assert "words per audio minute" in sanity_check(_words(3000), 600)  # 300 wpm
    assert sanity_check(_words(2000), None) is None  # no duration: only the floor applies


@pytest.mark.parametrize("source", sorted(READERS))
def test_every_registered_source_has_a_reader_with_fetch(source) -> None:
    assert callable(reader_for(source).fetch)

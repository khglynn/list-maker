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
    title, day, body = aidailybrief.parse_markdown(_aidb_page("Why Agents Need Memory", "2026-11-20", 3) + "\n\u200b\n")
    assert (title, day) == ("Why Agents Need Memory", "2026-11-20")
    assert body == "Opening line here.\n\nword word word\n\nLater."
    assert "[00:" not in body


def test_aidb_looks_up_the_eastern_day_first() -> None:
    # 02:00 UTC on 11-21 is still 11-20 in New York.
    days = aidailybrief.candidate_days(_item(published="2026-11-21T02:00:00"))
    assert days[:2] == [date(2026, 11, 20), date(2026, 11, 21)]
    assert len(days) == len(set(days))


def test_aidb_on_a_shared_day_takes_only_the_page_that_is_ours() -> None:
    """A promo item shared its date with the main episode (2026-10-26). With another item
    on the day, the page must match this item's title better than the other's."""
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    session = _Session({url: _Resp(text=_aidb_page("Listen to Our Sister Show", "2026-11-20"))})
    result = aidailybrief.fetch(_item(), session=session, same_day_titles=["Listen to Our Sister Show"])
    assert isinstance(result, NotYet) and "another episode" in result.reason


def test_aidb_a_retitled_page_on_the_episodes_own_day_is_still_its_page() -> None:
    """The feed's "A System for Testing New AI Models" is "The Best Way to Test New AI
    Models" on its own 2026-10-07 page. The only item that day owns that day's page."""
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    session = _Session({url: _Resp(text=_aidb_page("The Best Way to Remember Things", "2026-11-20"))})
    assert isinstance(aidailybrief.fetch(_item(), session=session), Transcript)


def test_aidb_the_day_before_needs_an_exact_title() -> None:
    """Today's page is not up yet, and yesterday's is "Part 1" of today's "Part 2": a
    token-set score calls those 100. A page from another day must match exactly."""
    item = _item(title="Agents, Part 2")
    yesterday = aidailybrief.BASE_URL.format(day="2026-11-19")
    session = _Session({yesterday: _Resp(text=_aidb_page("Agents, Part 1", "2026-11-19"))})
    result = aidailybrief.fetch(item, session=session)
    assert isinstance(result, NotYet) and yesterday in session.asked


def test_aidb_a_page_whose_header_names_another_day_is_refused() -> None:
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    session = _Session({url: _Resp(text=_aidb_page("Why Agents Need Memory", "2026-11-19"))})
    assert isinstance(aidailybrief.fetch(_item(), session=session), NotYet)


def test_aidb_numbers_must_agree_on_a_shared_day() -> None:
    url = aidailybrief.BASE_URL.format(day="2026-11-20")
    page = _aidb_page("Why the AI Kids Hate Astra 6.3", "2026-11-20")
    result = aidailybrief.fetch(_item(title="Why the AI Kids Hate Astra 6.2"), session=_Session({url: _Resp(text=page)}),
                                same_day_titles=["A Bonus Episode"])
    assert isinstance(result, NotYet)


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
    text, generated = npr.parse_transcript_page(_npr_page([
        "STEPHEN THOMPSON, BYLINE: Welcome to the show.",
        "LIM: I loved it. (LAUGHTER) Truly. [LAUGHTER]",
        "(SOUNDBITE OF MUSIC)",
        "[THEME MUSIC]",
        "SPEAKER 2: A numbered label goes too.",
        "Roose: A mixed-case label (with an aside in parentheses) stays as speech. [Laughs]",
    ]))
    assert text.split("\n\n") == [
        "Welcome to the show.",
        "I loved it. Truly.",
        "A numbered label goes too.",
        "A mixed-case label (with an aside in parentheses) stays as speech.",
    ]
    assert generated is False
    for gone in ("Navigation", "Copyright", "Accuracy", "Footer", "Transcript", "THOMPSON", "Roose:", "[", "SPEAKER"):
        assert gone not in text


def test_npr_says_when_the_page_was_generated_by_ai() -> None:
    text, generated = npr.parse_transcript_page(_npr_page(["This transcript was generated by AI.", "HOST: Hello."]))
    assert (text, generated) == ("Hello.", True)


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


def test_npr_without_a_story_id_is_an_item_with_no_transcript_not_a_failure() -> None:
    """NPR posts announcements and trailers without a story. Failed would redden every
    run for the two weeks the importer keeps asking."""
    result = npr.fetch(_item(link="https://example.org/x"), session=_Session({}))
    assert isinstance(result, NotYet) and "no transcript expected" in result.reason


def test_a_rate_limit_is_said_so_the_caller_can_stop_asking() -> None:
    link = "https://www.npr.org/2026/11/20/nx-s1-1234567/slug"
    url = npr.TRANSCRIPT_URL.format(story_id="nx-s1-1234567")
    result = npr.fetch(_item(link=link), session=_Session({url: _Resp(429)}))
    assert isinstance(result, NotYet) and result.rate_limited


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

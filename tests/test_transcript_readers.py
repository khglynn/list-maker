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


def _aidb_page(title: str, day: str, body_words: int = 2000, ends_at: str = "00:09:40") -> str:
    """A page in AI Daily's shape: the header, timestamped paragraphs, the last marker near
    the end of the audio (the default suits _item()'s 10 minutes)."""
    return f"# {title} — Transcript ({day})\n\n[00:00:00] Opening line here.\n\n{_words(body_words)}\n\n[{ends_at}] Later.\n"


def _aidb(pages: dict[str, str]) -> _Session:
    return _Session({aidailybrief.BASE_URL.format(day=d): _Resp(text=t) for d, t in pages.items()})


def test_aidb_markdown_loses_its_header_and_timestamps() -> None:
    page = _aidb_page("Why Agents Need Memory", "2026-11-20", 3) + "\n\u200b\n"
    title, day, body = aidailybrief.parse_markdown(page)
    assert (title, day) == ("Why Agents Need Memory", "2026-11-20")
    assert body == "Opening line here.\n\nword word word\n\nLater."
    assert aidailybrief.last_marker_seconds(page) == 580


def test_aidb_asks_only_for_the_episodes_own_eastern_day() -> None:
    """02:00 UTC on 11-21 is still 11-20 in New York. No neighbouring day is asked: a late
    page is "not yet", never yesterday's page (the Part 1 / Part 2 trap)."""
    session = _aidb({})
    result = aidailybrief.fetch(_item(published="2026-11-21T02:00:00"), session=session)
    assert isinstance(result, NotYet)
    assert session.asked == [aidailybrief.BASE_URL.format(day="2026-11-20")]


def test_aidb_accepts_its_page_even_retitled_when_the_length_matches() -> None:
    """The feed's "A System for Testing New AI Models" is "The Best Way to Test New AI
    Models" on its 2026-10-07 page. The audio length is the fingerprint, not the title."""
    # Scores 62 against the feed title, as the real 2026-10-07 pair does.
    session = _aidb({"2026-11-20": _aidb_page("The Best Way to Remember Agents", "2026-11-20")})
    result = aidailybrief.fetch(_item(), session=session)
    assert isinstance(result, Transcript)
    assert result.source_type == "publisher_transcript" and result.model == "aidailybrief_md"


def test_aidb_refuses_a_page_whose_length_is_another_episodes() -> None:
    """A six-minute promo sharing a day with the 27-minute main episode: the day's page is
    the main episode's, and the promo must not take it, title or no title."""
    promo = _item(title="Why Agents Need Memory", duration=377)
    session = _aidb({"2026-11-20": _aidb_page("Why Agents Need Memory", "2026-11-20", ends_at="00:27:00")})
    result = aidailybrief.fetch(promo, session=session)
    assert isinstance(result, NotYet) and "another episode" in result.reason


def test_aidb_without_markers_needs_the_exact_title() -> None:
    plain = "# Why Agents Need Memory — Transcript (2026-11-20)\n\n" + _words(2000) + "\n"
    assert isinstance(aidailybrief.fetch(_item(), session=_aidb({"2026-11-20": plain})), Transcript)
    other = plain.replace("Why Agents Need Memory", "Agents, Part 1")
    assert isinstance(aidailybrief.fetch(_item(), session=_aidb({"2026-11-20": other})), NotYet)


def test_aidb_a_page_headed_with_another_day_is_refused() -> None:
    session = _aidb({"2026-11-20": _aidb_page("Why Agents Need Memory", "2026-11-19")})
    assert isinstance(aidailybrief.fetch(_item(), session=session), NotYet)


def test_aidb_a_page_too_short_for_the_audio_is_not_stored() -> None:
    page = _aidb_page("Why Agents Need Memory", "2026-11-20", body_words=400)  # 40 wpm for 10 min
    result = aidailybrief.fetch(_item(), session=_aidb({"2026-11-20": page}))
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


def test_aidb_a_looser_length_match_also_needs_the_title() -> None:
    """Some real pages end at 83 to 87% of the audio (a long close). That band alone would
    let two episodes on one day swap pages (24 and 29 minutes, 2026-01-08), so in it the
    title must match too."""
    late_close = _aidb_page("Why Agents Need Memory", "2026-11-20", ends_at="00:08:30")  # 85%
    assert isinstance(aidailybrief.fetch(_item(), session=_aidb({"2026-11-20": late_close})), Transcript)
    other = late_close.replace("Why Agents Need Memory", "How People Are Using AI for Health")
    assert isinstance(aidailybrief.fetch(_item(), session=_aidb({"2026-11-20": other})), NotYet)


def test_aidb_a_same_day_neighbour_whose_page_ends_early_cannot_take_it() -> None:
    """Two episodes on one Eastern day (2026-01-08: 24 and 29 minutes). If the longer
    one's page ended at 83% it would land on the shorter one's length; the titles (32
    apart) are what refuse it."""
    shorter = _item(title="How People Are Using AI for Health", duration=1462)
    page = _aidb_page("Why Everyone Is Obsessed With Claude Code", "2026-11-20", body_words=4800, ends_at="00:24:00")
    assert isinstance(aidailybrief.fetch(shorter, session=_aidb({"2026-11-20": page})), NotYet)


# ── This American Life ────────────────────────────────────────────────────────

from pipeline.scrapers.transcripts import tal  # noqa: E402


def _tal_page(number: int, words: int = 8000) -> str:
    return (f'<header><span>Episode #{number}</span><h1>{number}: A Title</h1></header>'
            f'<div class="content"><div class="act"><h3>Prologue: Prologue</h3><div class="act-inner">'
            f'<div class="host"><h4>Ira Glass</h4><p begin="00:00:11.80" data-timestamp="11.8"> It&#039;s '
            f'<i>This</i> <i>American</i> <i>Life.</i> [MUSIC]</p></div>'
            f'<div class="subject"><h4>A Guest</h4><p begin="00:00:45.64">{_words(words)}</p>'
            f'<p begin="00:57:05.71"></p></div></div></div></div><div id="torey">Thanks Torey</div>')


def _tal_item(link="https://www.thisamericanlife.org/902/the-missing-minutes", title="902: The Missing Minutes"):
    return FeedItem(title, "46259 at https://www.thisamericanlife.org", link, None, None, None, "", duration_seconds=3546)


def test_tal_reads_the_episodes_own_numbered_page() -> None:
    session = _Session({tal.TRANSCRIPT_URL.format(number=902): _Resp(text=_tal_page(902))})
    result = tal.fetch(_tal_item(), session=session)
    assert isinstance(result, Transcript) and result.model == "tal_transcript_page" and not result.is_generated
    first = result.text.split("\n\n")[0]
    assert first == "It's This American Life."  # no speaker, act title or [MUSIC]


def test_tal_refuses_a_page_for_another_episode() -> None:
    session = _Session({tal.TRANSCRIPT_URL.format(number=902): _Resp(text=_tal_page(901))})
    assert isinstance(tal.fetch(_tal_item(), session=session), NotYet)


def test_tal_announcements_without_a_number_expect_no_transcript() -> None:
    result = tal.fetch(_tal_item(link="https://www.thisamericanlife.org/lifepartners", title="An Update from Ira"),
                       session=_Session({}))
    assert isinstance(result, NotYet) and "no transcript expected" in result.reason


def test_tal_finds_the_number_in_an_archive_rows_page_url() -> None:
    item = _tal_item(link="https://www.thisamericanlife.org/436/the-psychopath-test", title="The Psychopath Test")
    assert tal.episode_number(item) == 436

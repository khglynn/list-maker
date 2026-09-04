"""The strings that become songs.

Every song in both the SOP and TAL Spotify playlists entered the system through one of
the five pure parsers pinned here: `pipeline.scrapers.sop.scrape.parse_episode_list` /
`parse_songs_discussed` / `parse_description_body`, and
`pipeline.scrapers.tal.parse.parse_episode` / `clean_quotes`. No DB, no HTTP, no fakes --
text in, dicts out.

Fixtures under tests/fixtures/music/ (five files, real Firecrawl captures from
switchedonpop.com and thisamericanlife.org taken read-only on 2026-09-04, each trimmed
to the section under test with a header comment naming its source and capture date):

  - sop-episode-dash-format.md       -- normal SOP episode, "- Artist -- Title" format
  - sop-episode-quote-format.md      -- SOP episode using the comma+curly-quote fallback
  - sop-episode-no-songs-section.md  -- SOP episode with no Songs Discussed section
  - tal-episode-with-songs.json      -- TAL episode with two "## Song:" sections (both
                                         real song-line formats)
  - tal-episode-404.json             -- TAL 404 (the markdown body is real; the db_id/url
                                         envelope is constructed to look like a plausible
                                         not-yet-published discovery row -- see the file's
                                         own _fixture_note)

A handful of narrower regex behaviours (the Previous/Next nav filter, the "(Album)" and
leading-underscore filters, an isolated footer regex, an unparseable date, a TAL episode
with zero or exactly one song) cannot be organically demonstrated by any single real
capture without either a much larger fixture corpus or hunting for a rare page-shape
combination. Each of those is built as a small inline string instead, and every one says
plainly whether it is real text (trimmed from a second capture, cited inline) or
constructed to match the parser's own regex. See the PR body for the two things this
found: SOP's quote-format branch bakes the leading "- " bullet marker and trailing comma
into the artist string on live production data, and today's page template means SOP's
Previous/Next nav filter and two of parse_description_body's three footer regexes never
actually fire on a real page (the byline link / the first footer regex always intervene
first) -- they are dead code on the current template, still correct in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.scrapers.sop.scrape import (
    parse_description_body,
    parse_episode_list,
    parse_songs_discussed,
)
from pipeline.scrapers.tal.parse import clean_quotes, parse_episode

FIX = Path(__file__).parent / "fixtures" / "music"


# =============================================================================
# sop/scrape.py: parse_songs_discussed
# =============================================================================


def test_dash_format_parses_the_real_songs_discussed_list() -> None:
    """Real capture: switchedonpop.com/episodes/phoebe-bridgers-lost-weekend, the
    "- Artist -- Title" (en-dash) format. One artist name itself contains "and", which
    the pattern must not mistake for a second dash-separated entry."""
    md = (FIX / "sop-episode-dash-format.md").read_text()
    result = parse_songs_discussed(md)
    assert result["has_songs_section"] is True
    assert result["songs"] == [
        {"artist": "Phoebe Bridgers", "title": "The Outside"},
        {"artist": "Phoebe Bridgers", "title": "Lost Boys"},
        {"artist": "Paul Lansky and Hannah MacKay", "title": "Notjustmoreidlechatter"},
        {"artist": "Phoebe Bridgers", "title": "Motion Sickness"},
    ]


def test_quote_format_fallback_fires_on_a_real_episode_with_no_dash_at_all() -> None:
    """Real capture: switchedonpop.com/episodes/.../dolly-parton-ella-langley... . This
    episode's Songs Discussed list uses "Artist, "Title"" with no dash/en-dash character
    anywhere in the section, so the dash pattern matches zero songs and the quote-format
    fallback is what actually produces this episode's songs on live data -- not a
    constructed edge case.

    It also pins a real quirk: because the quote pattern's artist group is only bounded
    by the next quote character, the leading "- " bullet marker and the trailing comma
    end up baked into the stored artist string. Every SOP song matched via this branch
    on live data carries that shape -- worth knowing before "fixing" it, since matching
    (`spotify_match.py`) is fuzzy and may tolerate the noise today."""
    md = (FIX / "sop-episode-quote-format.md").read_text()
    result = parse_songs_discussed(md)
    assert result["has_songs_section"] is True
    assert result["songs"] == [
        {"artist": "- Dolly Parton,", "title": "9 to 5"},
        {"artist": "- Dolly Parton & Kenny Rogers,", "title": "Islands In the Stream"},
        {"artist": "- Ella Langley,", "title": "Choosin’ Texas"},
        {"artist": "- Taylor Swift,", "title": "I Knew You Were Trouble."},
        {"artist": "- Stella Lefty,", "title": "Boston"},
    ]


def test_quote_format_is_a_fallback_not_a_union() -> None:
    """Constructed: a section carrying one dash-parseable line and one quote-parseable
    line. If the dash pattern finds anything at all, the quote branch never runs -- the
    quote-only line is dropped, not merged in."""
    md = (
        "**Songs Discussed**\n\n"
        "- Real Artist – Real Title\n\n"
        "Ignored Artist “Ignored Song”\n"
    )
    result = parse_songs_discussed(md)
    assert result["songs"] == [{"artist": "Real Artist", "title": "Real Title"}]


def test_previous_and_next_nav_entries_are_filtered_from_the_dash_branch() -> None:
    """Constructed, and disclosed as such: on today's live template every real episode
    page has a "[Author Name](...)" byline link immediately after the songs list, and
    parse_songs_discussed's own section regex (`(?=\\n\\n\\[|$)`) stops right there --
    so the trailing Previous/Next nav block is never actually included in songs_text on
    a real page (verified against both real SOP fixtures above). This filter is
    defensive, currently-dead-on-the-template code; it is still pinned here because it
    is real, reachable logic, and a template change could reopen the path any time."""
    md = (
        "**Songs Discussed**\n\n"
        "- Real Artist – Real Title\n\n"
        "- Nav Artist – Previous\n\n"
        "- Nav Artist – Next Episode\n"
    )
    result = parse_songs_discussed(md)
    assert result["songs"] == [{"artist": "Real Artist", "title": "Real Title"}]


def test_album_and_leading_underscore_entries_are_filtered_from_the_quote_branch() -> None:
    """Constructed to match the quote pattern (no dash char anywhere, so the fallback
    fires): a title ending "(Album)" and an artist with a leading underscore are both
    dropped."""
    md = (
        "**Songs Discussed**\n\n"
        "Real Artist “Real Title”\n"
        'Album Artist "Full Album (Album)"\n'
        '_Weird Artist "Weird Song"\n'
    )
    result = parse_songs_discussed(md)
    assert result["songs"] == [{"artist": "Real Artist", "title": "Real Title"}]


def test_no_songs_discussed_section_at_all() -> None:
    """Real capture: switchedonpop.com/episodes/anthems-queen-we-are-the-champions, an
    older template with no Songs Discussed section at all."""
    md = (FIX / "sop-episode-no-songs-section.md").read_text()
    result = parse_songs_discussed(md)
    assert result == {"songs": [], "has_songs_section": False}


# =============================================================================
# sop/scrape.py: parse_episode_list
# =============================================================================

# Real capture, trimmed to two entries: switchedonpop.com/episodes, fetched read-only via
# Firecrawl markdown on 2026-09-04. The doubled "Charlie Harding9/1/26Charlie Harding9/1/26"
# byline text is exactly how the real page renders (image caption + text caption both
# carrying the author+date) -- not a typo introduced here.
SOP_EPISODE_LIST_REAL_EXCERPT = (
    "[![Questionably Country](https://images.example/493.jpg)]"
    "(https://switchedonpop.com/episodes/dolly-parton-ella-langley-choosin-texas-morgan-wallen-stella-lefty)\n\n"
    "Charlie Harding9/1/26Charlie Harding9/1/26\n\n"
    "# [Questionably Country](https://switchedonpop.com/episodes/dolly-parton-ella-langley-choosin-texas-morgan-wallen-stella-lefty)\n\n"
    "For the first time in history, five country songs were at the top of the Billboard Hot 100.\n\n"
    "[Read More](https://switchedonpop.com/episodes/dolly-parton-ella-langley-choosin-texas-morgan-wallen-stella-lefty)\n\n"
    "[![Phoebe Bridgers opens her Moleskine](https://images.example/492.jpg)]"
    "(https://switchedonpop.com/episodes/phoebe-bridgers-lost-weekend)\n\n"
    "Nate Sloan8/25/26Nate Sloan8/25/26\n\n"
    "# [Phoebe Bridgers opens her Moleskine](https://switchedonpop.com/episodes/phoebe-bridgers-lost-weekend)\n\n"
    "Many of us keep a journal, but few of us are able to turn our unvarnished thoughts.\n\n"
    "[Read More](https://switchedonpop.com/episodes/phoebe-bridgers-lost-weekend)\n"
)


def test_episode_list_matches_title_and_url_and_looks_up_the_preceding_date() -> None:
    episodes = parse_episode_list(SOP_EPISODE_LIST_REAL_EXCERPT)
    assert [e["title"] for e in episodes] == [
        "Questionably Country",
        "Phoebe Bridgers opens her Moleskine",
    ]
    assert episodes[0]["url"] == (
        "https://switchedonpop.com/episodes/dolly-parton-ella-langley-choosin-texas-morgan-wallen-stella-lefty"
    )
    import datetime

    assert episodes[0]["publish_date"] == datetime.date(2026, 9, 1)
    assert episodes[1]["publish_date"] == datetime.date(2026, 8, 25)


def test_episode_list_publish_date_is_none_when_no_date_precedes_the_title() -> None:
    """Constructed: no MM/DD/YY pattern anywhere in the 100 characters before the title."""
    md = (
        "Some unrelated preamble text with no date pattern anywhere near this heading, "
        "just words.\n\n"
        "# [Some Episode](https://switchedonpop.com/episodes/some-episode)\n\nBody.\n"
    )
    episodes = parse_episode_list(md)
    assert len(episodes) == 1
    assert episodes[0]["publish_date"] is None


def test_episode_list_publish_date_is_none_when_the_date_does_not_parse() -> None:
    """Constructed: "2/30/26" matches the MM/DD/YY regex shape but Feb 30 does not exist,
    so strptime raises and the ValueError is swallowed to None rather than propagating."""
    md = (
        "Charlie Harding2/30/26\n\n"
        "# [Some Episode](https://switchedonpop.com/episodes/some-episode)\n\nBody.\n"
    )
    episodes = parse_episode_list(md)
    assert len(episodes) == 1
    assert episodes[0]["publish_date"] is None


# =============================================================================
# sop/scrape.py: parse_description_body
# =============================================================================


def test_description_body_strips_title_and_truncates_at_songs_discussed() -> None:
    md = (FIX / "sop-episode-dash-format.md").read_text()
    body = parse_description_body(md)
    assert not body.startswith("#")
    assert "Songs Discussed" not in body
    assert body.startswith("Many of us keep a journal")
    assert body.endswith("Rock Ridge Productions.")


def test_description_body_footer_regex_one_removes_the_whole_nav_and_footer_chain() -> None:
    """Real capture: the ANTHEMS/Queen no-songs fixture. On today's real template the
    Previous/Next nav ("[Previous...") always precedes the Apple-Podcasts/Substack
    footer, so this one regex eats the entire trailing chain in one shot -- the other
    two footer regexes never get anything to match on a real page. Pinned here as
    real, verified behaviour; the other two regexes are proven independently below."""
    md = (FIX / "sop-episode-no-songs-section.md").read_text()
    body = parse_description_body(md)
    assert body.endswith(
        "[Charlie Harding](https://switchedonpop.com/episodes?author=5dc85fbd5cdd8240dbc3e94f)"
    )
    assert "Previous" not in body
    assert "Apple-Podcasts" not in body
    assert "Substack" not in body


def test_description_body_footer_regex_two_fires_without_a_previous_nav() -> None:
    """Constructed: no "[Previous" anywhere, so regex 1 is a no-op; regex 2 must catch
    the Apple-Podcasts footer (and, since it comes first, everything after it too)."""
    md = (
        "# Some Episode\n\nSome description text.\n\n"
        "[![Apple-Podcasts-Footer.png](url)](https://podcasts.apple.com/x)\n\n"
        "Switched On Pop \\| Substack\n\nmore trailing junk\n"
    )
    body = parse_description_body(md)
    assert body == "Some description text."


def test_description_body_footer_regex_three_fires_alone() -> None:
    """Constructed: neither "[Previous" nor "[![Apple-Podcasts" present -- only the
    Substack marker, proving regex 3 works in isolation rather than only ever being
    reached as a no-op after regex 1 or 2 already consumed it."""
    md = (
        "# Some Episode\n\nSome description text.\n\n"
        "Switched On Pop \\| Substack\n\nmore trailing junk that should also disappear\n"
    )
    body = parse_description_body(md)
    assert body == "Some description text."


# =============================================================================
# tal/parse.py: clean_quotes
# =============================================================================


def test_clean_quotes_converts_all_four_curly_code_points() -> None:
    assert clean_quotes(
        "“left double” ‘left single’ done"
    ) == "\"left double\" 'left single' done"


# =============================================================================
# tal/parse.py: parse_episode
# =============================================================================


def test_tal_episode_with_two_song_sections_both_real_formats() -> None:
    """Real capture: thisamericanlife.org/746/this-is-just-some-songs. Two "## Song:"
    sections, one in the bracketed-link format and one plain-text -- both curly-quoted
    in the source, exercising clean_quotes() ahead of each format's own regex."""
    result = parse_episode(FIX / "tal-episode-with-songs.json")
    assert result["is_404"] is False
    assert result["episode_number"] == 746
    assert result["title"] == "This Is Just Some Songs"
    assert result["publish_date"] == "2021-08-18"
    assert result["has_songs"] is True
    assert result["songs"] == [
        {"title": "Music is Easy", "artist": "Josephine Network"},
        {"title": "I Talk in Tunes", "artist": "Chandler Travis"},
    ]


def test_tal_404_detection_short_circuits_before_any_other_field() -> None:
    """Real capture: the markdown body is the actual thisamericanlife.org 404 page."""
    result = parse_episode(FIX / "tal-episode-404.json")
    assert result == {
        "db_id": 9102,
        "is_404": True,
        "url": "https://www.thisamericanlife.org/897/a-slug-that-never-shipped",
    }


def test_tal_episode_with_zero_song_sections(tmp_path: Path) -> None:
    """Real capture, trimmed: thisamericanlife.org/206/somewhere-in-the-arabian-sea, an
    episode with no "## Song:" section at all. Written to a tmp_path file because
    parse_episode reads from a filepath, not a string."""
    markdown = (
        "## [Prologue](https://www.thisamericanlife.org/206/somewhere-in-the-arabian-sea/prologue-11)\n\n"
        "Alex Blumberg talks with sailor Crevon Scott, who stocks vending machines on the Stennis.\n\n"
        "## [Act One](https://www.thisamericanlife.org/206/somewhere-in-the-arabian-sea/act-one-12)\n\n"
        "Aboard the USS Stennis, Ira Glass and Wendy Dorr talk with sailors about life on board.\n"
    )
    payload = {
        "db_id": 9103,
        "url": "https://www.thisamericanlife.org/206/somewhere-in-the-arabian-sea",
        "markdown": markdown,
        "metadata": {
            "og:title": "Somewhere in the Arabian Sea - This American Life",
            "article:published_time": "2002-03-01T00:00:00-05:00",
        },
    }
    fixture_path = tmp_path / "206.json"
    fixture_path.write_text(json.dumps(payload))
    result = parse_episode(fixture_path)
    assert result["is_404"] is False
    assert result["has_songs"] is False
    assert result["songs"] == []


def test_tal_episode_with_exactly_one_song_section(tmp_path: Path) -> None:
    """Real capture, trimmed: thisamericanlife.org/890/maximal-americanness, the
    plain-text song-line format on its own (no bracketed link), curly-quoted in the
    source. Written to a tmp_path file for the same reason as the zero-song case above."""
    markdown = (
        "## [Jose Can You See?](https://www.thisamericanlife.org/890/maximal-americanness/act-five-0)\n\n"
        "Emmanuel Dzotsi investigates a musical phenomenon very particular to the United States: "
        "singers embellishing the end of the national anthem. (9 minutes)\n\n"
        "## Song:\n\n"
        "“We the People” by The Staples Singers\n\n"
        "## Related\n\nIf you enjoyed this episode, you may like these.\n"
    )
    payload = {
        "db_id": 9104,
        "url": "https://www.thisamericanlife.org/890/maximal-americanness",
        "markdown": markdown,
        "metadata": {
            "og:title": "Maximal Americanness - This American Life",
            "article:published_time": "2026-06-26T10:06:51-04:00",
        },
    }
    fixture_path = tmp_path / "890.json"
    fixture_path.write_text(json.dumps(payload))
    result = parse_episode(fixture_path)
    assert result["has_songs"] is True
    assert result["songs"] == [{"title": "We the People", "artist": "The Staples Singers"}]


def test_tal_title_suffix_strip_and_curly_quote_normalisation(tmp_path: Path) -> None:
    """Constructed metadata: none of the real captures above happen to carry a curly
    apostrophe in the title, so this isolates the two title transforms directly --
    the " - This American Life" suffix strip and clean_quotes()."""
    payload = {
        "db_id": 9105,
        "url": "https://www.thisamericanlife.org/999/iras-prologue",
        "markdown": "## [Prologue](url)\n\nNo songs here.\n",
        "metadata": {
            "og:title": "Ira’s Prologue - This American Life",
            "article:published_time": "2026-05-01T00:00:00-04:00",
        },
    }
    fixture_path = tmp_path / "999.json"
    fixture_path.write_text(json.dumps(payload))
    result = parse_episode(fixture_path)
    assert result["title"] == "Ira's Prologue"

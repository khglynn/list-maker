"""The one RSS reader (pipeline/feeds.py).

Fixtures are tiny and made up: the repo is public, and nothing here needs a real
publisher's text to prove what it proves.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pipeline import feeds

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
  <title>Example Show</title>
  <itunes:new-feed-url>https://example.org/feed.xml</itunes:new-feed-url>
  <item>
    <title> 902: An Evening Episode </title>
    <guid isPermaLink="false">46259 at https://example.org</guid>
    <link>https://example.org/902/an-evening-episode</link>
    <enclosure url="https://cdn.example.org/902.mp3" type="audio/mpeg"/>
    <pubDate>Sun, 22 Nov 2026 20:00:00 -0500</pubDate>
    <itunes:duration>01:02:03</itunes:duration>
    <itunes:episode>902</itunes:episode>
    <itunes:image href="https://cdn.example.org/902.jpg"/>
    <description>&lt;p&gt;Brought to you by: &lt;a href="https://sponsor.example"&gt;Sponsor&lt;/a&gt;&lt;/p&gt;</description>
  </item>
  <item>
    <title>No guid here</title>
    <link>https://example.org/show-page</link>
    <enclosure url="https://cdn.example.org/b.mp3" type="audio/mpeg"/>
    <pubDate>Fri, 20 Nov 2026 12:00:00 +0000</pubDate>
    <itunes:duration>1470</itunes:duration>
  </item>
  <item>
    <title>Bare</title>
    <pubDate>not a date</pubDate>
  </item>
</channel></rss>"""


def test_a_late_evening_eastern_episode_is_dated_by_its_utc_day() -> None:
    """TAL dates its feed in US Eastern; Taddy stored the UTC day, and so does every row
    the title+date match compares against. The local day here (11-22) is the one that
    doubled episodes 886 and 887."""
    item = feeds.parse_feed(FEED).items[0]
    assert item.published_at == datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc)
    assert item.publish_date == date(2026, 11, 23)


def test_items_carry_what_the_importer_stores() -> None:
    item = feeds.parse_feed(FEED).items[0]
    assert item.title == "902: An Evening Episode"  # stripped
    assert item.guid == "46259 at https://example.org"
    assert item.enclosure_url == "https://cdn.example.org/902.mp3"
    assert item.duration_seconds == 3723
    assert item.episode_number == 902
    assert item.image_url == "https://cdn.example.org/902.jpg"
    # The HTML survives untouched: the sponsor roster parser reads it.
    assert '<a href="https://sponsor.example">' in item.description_html
    assert item.description_text == "Brought to you by: Sponsor"


def test_identity_is_the_guid_then_audio_then_link_then_synthetic() -> None:
    first, second, third = feeds.parse_feed(FEED).items
    assert feeds.episode_identity(first) == "46259 at https://example.org"
    # The audio url beats the link: a link can be one show page shared by every episode.
    assert feeds.episode_identity(second) == "https://cdn.example.org/b.mp3"
    assert feeds.episode_identity(third) == "rss:Bare:not a date"
    assert feeds.episode_identity(third, synthetic_prefix="gabfest") == "gabfest:Bare:not a date"


def test_an_unparseable_date_is_none_not_a_guess() -> None:
    third = feeds.parse_feed(FEED).items[2]
    assert third.published_at is None and third.publish_date is None


@pytest.mark.parametrize("raw, seconds", [("1470", 1470), ("24:30", 1470), ("00:24:30", 1470), ("", None), ("abc", None)])
def test_durations_in_every_shape_feeds_write(raw, seconds) -> None:
    assert feeds.parse_duration(raw) == seconds


def test_the_channel_names_where_it_says_it_lives() -> None:
    parsed = feeds.parse_feed(FEED)
    assert parsed.new_feed_url == "https://example.org/feed.xml"
    assert parsed.channel_title == "Example Show"


def test_a_document_that_is_not_rss_raises_rather_than_reading_as_empty() -> None:
    """An error page or an Atom document must not look like a feed with no episodes:
    the feed check would call that "caught up"."""
    with pytest.raises(ValueError):
        feeds.parse_feed(b"<html><body>Service unavailable</body></html>")


def test_a_pubdate_without_an_offset_is_read_as_utc() -> None:
    assert feeds.parse_pubdate_utc("Tue, 17 Nov 2026 08:01:00") == datetime(2026, 11, 17, 8, 1, tzinfo=timezone.utc)
    assert feeds.parse_pubdate_utc("Tue, 17 Nov 2026 08:01:00 -0000").tzinfo == timezone.utc


class _Resp:
    def __init__(self, url, history=()):
        self.url, self.history, self.content, self.status_code = url, list(history), FEED, 200

    def raise_for_status(self):
        return None


class _Hop:
    def __init__(self, url):
        self.url = url


def test_a_redirected_feed_says_so() -> None:
    class Session:
        def get(self, url, **kwargs):
            assert kwargs["headers"]["User-Agent"] == feeds.USER_AGENT
            return _Resp("https://new.example.org/feed.xml", history=[_Hop(url)])

    fetched = feeds.fetch_feed("https://old.example.org/feed.xml", session=Session())
    assert fetched.moved
    assert fetched.redirects == ["https://old.example.org/feed.xml"]


def test_a_feed_that_did_not_move_is_not_reported_as_moved() -> None:
    class Session:
        def get(self, url, **kwargs):
            return _Resp(url)

    assert not feeds.fetch_feed("https://example.org/feed.xml", session=Session()).moved

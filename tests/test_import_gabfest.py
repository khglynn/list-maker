"""Culture Gabfest importer (Workstream D) — RSS parse + filter + dedup-key logic.

The Megaphone feed mixes Slate shows, so filtering to "Culture Gabfest" titles is
the load-bearing correctness step; the guid-based url keeps episodes from
collapsing the way the generic-websiteUrl Hard Fork bug did.
"""

from pipeline.scrapers.gabfest.import_gabfest import (
    clean_html,
    episode_url,
    filter_gabfest,
    parse_feed,
    parse_pubdate,
)

SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Slate Culture</title>
  <item>
    <title>ICYMI - Beware The Boy Mom</title>
    <guid isPermaLink="false">icymi-001</guid>
    <pubDate>Sat, 06 Jun 2026 07:00:00 GMT</pubDate>
    <description>&lt;p&gt;An ICYMI episode.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Culture Gabfest - Stuck in the Backrooms Edition</title>
    <guid isPermaLink="false">gabfest-100</guid>
    <link>https://slate.com/podcasts/culture-gabfest/backrooms</link>
    <enclosure url="https://traffic.megaphone.fm/gabfest-100.mp3" type="audio/mpeg"/>
    <pubDate>Wed, 03 Jun 2026 07:10:00 GMT</pubDate>
    <description>&lt;p&gt;Steve, Dana &amp; Julia on &lt;i&gt;Backrooms&lt;/i&gt; (A24) and the book Let's Talk About Love.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Culture Gabfest - Another Edition</title>
    <guid isPermaLink="false">gabfest-101</guid>
    <pubDate>Wed, 27 May 2026 07:00:00 GMT</pubDate>
    <description>Another Gabfest.</description>
  </item>
</channel></rss>"""


def test_parse_feed_extracts_all_items() -> None:
    items = parse_feed(SAMPLE_FEED)
    assert len(items) == 3
    assert items[0]["title"] == "ICYMI - Beware The Boy Mom"
    assert items[1]["guid"] == "gabfest-100"


def test_filter_gabfest_keeps_only_gabfest() -> None:
    gabfest = filter_gabfest(parse_feed(SAMPLE_FEED))
    assert len(gabfest) == 2
    assert all(it["title"].startswith("Culture Gabfest") for it in gabfest)


def test_clean_html_strips_tags_and_unescapes() -> None:
    assert clean_html("<p>Steve &amp; Dana on <i>Backrooms</i>.</p>") == "Steve & Dana on Backrooms."
    assert clean_html("") == ""


def test_parse_pubdate() -> None:
    assert str(parse_pubdate("Wed, 03 Jun 2026 07:10:00 GMT")) == "2026-06-03"
    assert parse_pubdate(None) is None
    assert parse_pubdate("not a date") is None


def test_episode_url_prefers_guid_over_link() -> None:
    by_title = {it["title"]: it for it in parse_feed(SAMPLE_FEED)}
    backrooms = by_title["Culture Gabfest - Stuck in the Backrooms Edition"]
    # Has both guid and a (potentially generic) link → must use the unique guid.
    assert episode_url(backrooms) == "gabfest-100"


def test_episode_url_falls_back_to_enclosure_when_no_guid() -> None:
    item = {"enclosure_url": "https://x.megaphone.fm/ep.mp3", "link": "https://slate.com/show"}
    assert episode_url(item) == "https://x.megaphone.fm/ep.mp3"


def test_episode_url_synthetic_fallback_when_no_ids() -> None:
    # No guid/enclosure/link → a stable synthetic key from title+pubdate (still
    # unique enough to dedup; better than colliding everything onto one row).
    item = {"title": "Culture Gabfest - X", "pubdate_raw": "Wed, 03 Jun 2026 07:10:00 GMT"}
    assert episode_url(item) == "gabfest:Culture Gabfest - X:Wed, 03 Jun 2026 07:10:00 GMT"

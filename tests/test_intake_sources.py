"""Intake sources parse frozen copies of the real feed / index pages.

The fixtures are the actual OpenAI RSS (first six items, 2026-09-02) and the actual
Firecrawl markdown of anthropic.com/news the same day. If a publisher changes layout,
the parser breaks HERE, in CI, not silently on a Monday run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pipeline.scrapers.intake.sources import Candidate, parse_anthropic_index, parse_openai_rss

FIX = Path(__file__).parent / "fixtures" / "intake"


def test_openai_rss_items_carry_title_url_date_category_blurb() -> None:
    items = parse_openai_rss((FIX / "openai-rss-sample.xml").read_text())
    assert len(items) == 6
    first = items[0]
    assert isinstance(first, Candidate) and first.source == "openai-rss"
    assert first.url.startswith("https://openai.com/index/")
    assert first.published_on == date(2026, 9, 1)
    assert first.category and first.blurb and first.discovered_via["guid"]
    assert items == sorted(items, key=lambda c: c.published_on, reverse=True)


def test_openai_rss_since_filters_older_posts_and_keeps_undated_ones() -> None:
    xml = (FIX / "openai-rss-sample.xml").read_text()
    recent = parse_openai_rss(xml, since=date(2026, 9, 1))
    assert {c.published_on for c in recent} == {date(2026, 9, 1)}
    undated = xml.replace("<pubDate>", "<pubDate>garbage ", 1)
    kept = parse_openai_rss(undated, since=date(2030, 1, 1))
    assert len(kept) == 1 and kept[0].published_on is None  # visible gap, not a silent drop


def test_anthropic_index_merges_hero_cards_and_list_rows() -> None:
    md = (FIX / "anthropic-news-index-sample.md").read_text()
    posts = parse_anthropic_index(md)
    by_url = {c.url: c for c in posts}
    # the hero: title in one bracket, date + blurb in the next, a non-/news/ URL
    hero = by_url["https://www.anthropic.com/claude-fable-and-mythos-5-1"]
    assert hero.title == "Introducing Claude Fable 5.1 and Claude Mythos 5.1"
    assert hero.published_on == date(2026, 9, 1) and hero.category == ["Announcements"]
    assert hero.blurb.startswith("Our most advanced models")
    # a featured card: category glued before the date, bold title, blurb after
    card = by_url["https://www.anthropic.com/news/model-hardware-standard-research-preview"]
    assert card.title == "Previewing the Model Hardware Standard" and card.published_on == date(2026, 8, 27)
    # a list row with no category
    row = by_url["https://www.anthropic.com/news/improving-alignment-security-efforts"]
    assert row.title == "Improving our alignment and security efforts" and row.category == []
    assert row.published_on == date(2026, 8, 31)
    # navigation never becomes a post
    assert not any("press-kit" in u or u.endswith("/news") for u in by_url)
    assert posts == sorted(posts, key=lambda c: c.published_on, reverse=True)
    assert len(posts) >= 12


def test_anthropic_index_since_and_source_slug() -> None:
    md = (FIX / "anthropic-news-index-sample.md").read_text()
    recent = parse_anthropic_index(md, source="anthropic-engineering", since=date(2026, 8, 27))
    assert recent and all(c.published_on >= date(2026, 8, 27) for c in recent)
    assert {c.source for c in recent} == {"anthropic-engineering"}
    assert all(c.discovered_via["index"].endswith("/engineering") for c in recent)


def test_anthropic_engineering_cards_wrap_thumbnails_and_the_hero_is_undated() -> None:
    """/engineering: every card wraps an image inside the link text; the featured post
    carries no date. Both were invisible on the /news fixture (found 2026-09-02 by a
    dry run that reported 0 engineering posts when there were 24)."""
    md = (FIX / "anthropic-engineering-index-sample.md").read_text()
    posts = parse_anthropic_index(md, source="anthropic-engineering")
    by_url = {c.url: c for c in posts}
    assert len(posts) >= 20
    card = by_url["https://www.anthropic.com/engineering/claude-code-auto-mode"]
    assert card.title == "How we built Claude Code auto mode: a safer way to skip permissions"
    assert card.published_on == date(2026, 3, 25) and card.category == []
    hero = by_url["https://www.anthropic.com/engineering/how-we-contain-claude"]
    assert hero.title == "How we contain Claude across products" and hero.published_on is None
    assert hero.discovered_via["hero"] is True and hero.blurb.startswith("As agents grow")
    assert posts[-1] is hero  # undated sorts last
    recent = parse_anthropic_index(md, source="anthropic-engineering", since=date(2026, 4, 1))
    assert hero in recent and all(c.published_on is None or c.published_on >= date(2026, 4, 1) for c in recent)
    assert not any("![" in c.title for c in posts)

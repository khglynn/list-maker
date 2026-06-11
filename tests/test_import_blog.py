"""Pure-function tests for the blog importer (no DB, no Firecrawl).

canonicalize_url guards the episodes.url UNIQUE dedup key — variants of the same
post must collapse to one key or we duplicate episodes and double-extract.
"""

from datetime import date

from pipeline.scrapers.blog.import_blog import (
    canonicalize_url,
    parse_publish_date,
    pick_title,
)


def test_canonicalize_forces_https_and_lowercases_host() -> None:
    assert canonicalize_url("http://OpenAI.com/news/post") == "https://openai.com/news/post"


def test_canonicalize_strips_trailing_slash_fragment_and_tracking() -> None:
    url = "https://www.anthropic.com/news/claude-fable/?utm_source=tw&utm_campaign=x&ref=feed#intro"
    assert canonicalize_url(url) == "https://www.anthropic.com/news/claude-fable"


def test_canonicalize_keeps_legitimate_query_params() -> None:
    assert canonicalize_url("https://example.com/post?id=42&utm_medium=email") == (
        "https://example.com/post?id=42"
    )


def test_canonicalize_variants_collapse_to_one_key() -> None:
    variants = [
        "http://openai.com/index/abc/",
        "https://openai.com/index/abc?utm_source=newsletter",
        "https://OPENAI.com/index/abc#section",
    ]
    assert len({canonicalize_url(v) for v in variants}) == 1


def test_parse_publish_date_iso_and_z_suffix() -> None:
    assert parse_publish_date({"article:published_time": "2026-05-26T10:00:00Z"}) == date(2026, 5, 26)
    assert parse_publish_date({"publishedTime": "2026-01-02"}) == date(2026, 1, 2)


def test_parse_publish_date_none_when_absent_or_junk() -> None:
    assert parse_publish_date({}) is None
    assert parse_publish_date({"date": "yesterday"}) is None


def test_pick_title_prefers_og_and_strips_site_suffix() -> None:
    meta = {"ogTitle": "A reality check on the AI jobs hysteria | MIT Technology Review"}
    assert pick_title(meta, "https://x") == "A reality check on the AI jobs hysteria"


def test_pick_title_falls_back_to_url() -> None:
    assert pick_title({}, "https://example.com/post") == "https://example.com/post"


def test_parse_publish_date_from_url_path() -> None:
    assert parse_publish_date({}, "https://www.technologyreview.com/2026/05/26/1137855/x/") == (
        date(2026, 5, 26)
    )
    assert parse_publish_date({}, "https://example.com/2026/99/99/post/") is None


def test_metadata_date_wins_over_url_date() -> None:
    meta = {"article:published_time": "2026-01-15T08:00:00Z"}
    assert parse_publish_date(meta, "https://example.com/2026/05/26/post/") == date(2026, 1, 15)

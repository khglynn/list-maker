"""Podcast-cited candidates: what the mentions query keeps, drops, and says out loud.

This is the discovery that produced the 45 rows in the Notion log today, carried over
from build_pull_queue when the checkbox retired. The behaviour worth pinning is the
part that decides what Kevin never sees: a section index isn't a post, an
already-ingested URL isn't a candidate, and http/utm twins are one row, not two.
"""

from __future__ import annotations

from datetime import date

from pipeline.scrapers.intake import mentions as M


class _Cursor:
    def __init__(self, conn) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql, params=()) -> None:
        self.conn.calls.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.conn.rows.pop(0) if self.conn.rows else []


class _Conn:
    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])
        self.calls: list = []

    def cursor(self):
        return _Cursor(self)


def _row(url: str, **kw) -> dict:
    base = {"source_url": url, "cited_in_episodes": 1, "last_cited": date(2026, 8, 30),
            "why": "the Ramp index says seat growth flattened", "cited_as": "Ramp AI Index",
            "shows": ["The AI Daily Brief"]}
    base.update(kw)
    return base


def test_registered_blog_domains_are_derived_from_show_config() -> None:
    # Derived, so there is no second list to drift out of date with show_config.
    domains = M.registered_blog_domains()
    assert "openai.com" in domains and "anthropic.com" in domains
    assert all(not d.startswith("www.") for d in domains)


def test_section_indexes_and_ingested_urls_never_become_candidates(monkeypatch) -> None:
    conn = _Conn([[
        _row("https://openai.com/index/a-real-post"),
        _row("https://openai.com/news"),                 # a section index, not a post
        _row("https://anthropic.com/news/already-here"),  # already an episode
    ]])
    monkeypatch.setattr("pipeline.scrapers.intake.store.already_ingested_urls",
                        lambda c, urls: {"https://anthropic.com/news/already-here"})
    out = M.discover_cited_candidates(conn)
    assert [c.url for c in out] == ["https://openai.com/index/a-real-post"]
    assert all(c.source == "podcast-cited" for c in out)


def test_http_and_utm_twins_collapse_into_one_candidate(monkeypatch) -> None:
    conn = _Conn([[
        _row("http://www.openai.com/index/a/", cited_in_episodes=2, shows=["Hard Fork"]),
        _row("https://openai.com/index/a?utm_source=x", cited_in_episodes=3),
    ]])
    monkeypatch.setattr("pipeline.scrapers.intake.store.already_ingested_urls",
                        lambda c, urls: set())
    out = M.discover_cited_candidates(conn)
    assert len(out) == 1 and out[0].url == "https://openai.com/index/a"
    via = out[0].discovered_via
    # the citation counts and the shows are summed, not overwritten by whichever row
    # the database happened to return second
    assert via["cited_in_episodes"] == 5
    assert via["shows"] == ["Hard Fork", "The AI Daily Brief"]


def test_a_candidate_carries_provenance_but_not_a_borrowed_date(monkeypatch) -> None:
    conn = _Conn([[_row("https://ramp.com/data/ai-index")]])
    monkeypatch.setattr("pipeline.scrapers.intake.store.already_ingested_urls",
                        lambda c, urls: set())
    cand = M.discover_cited_candidates(conn)[0]
    # The CITE date is not the POST date. Leaving published_on None is the honest gap;
    # the scrape fills it from the page's own metadata.
    assert cand.published_on is None
    assert cand.discovered_via["last_cited"] == "2026-08-30"
    assert cand.discovered_via["cited_as"] == "Ramp AI Index"
    # the title is left for the scrape: this row only knows what the host called it
    assert cand.title == "" and cand.blurb.startswith("the Ramp index")


def test_the_window_is_optional_and_only_then_bounds_the_query(monkeypatch) -> None:
    monkeypatch.setattr("pipeline.scrapers.intake.store.already_ingested_urls",
                        lambda c, urls: set())
    unbounded = _Conn([[]])
    M.discover_cited_candidates(unbounded)
    # No window by default: the first run of the judged intake is meant to re-judge the
    # backlog the checkbox never cleared.
    assert "publish_date >=" not in unbounded.calls[0][0]
    assert len(unbounded.calls[0][1]) == 1

    windowed = _Conn([[]])
    M.discover_cited_candidates(windowed, since_days=14)
    assert "ep.publish_date >= CURRENT_DATE - %s" in windowed.calls[0][0]
    assert windowed.calls[0][1][1] == 14


def test_an_empty_result_is_reported_not_silently_returned(caplog) -> None:
    conn = _Conn([[_row("https://openai.com/news")]])  # only a section index
    with caplog.at_level("INFO"):
        assert M.discover_cited_candidates(conn) == []
    # "nothing to do" must be distinguishable from "didn't check" (docs/principles.md)
    assert "none of them post-shaped" in caplog.text

"""The manual ingest door: `save_item.py --url`.

`save_item.save_url` is the primitive behind both "save this blog post please" and
the weekly curated intake's "Pull anyway" rows, and until now it had no test file.
Everything here is hermetic — the DB connection, Firecrawl's `ingest_url`, the
extraction and Notion steps, `httpx.stream` and `subprocess.run` are all faked at
the import boundary, the idiom `tests/test_run_new_episodes.py` established.

Written for Phase 5 PR 5 (2026-09-04). Tests only: `pipeline/save_item.py` is not
modified by this file, and where the code and the plan disagreed the code won.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from pipeline import save_item
from pipeline.save_item import (
    domain_to_show,
    episode_has_mentions,
    is_pdf,
    resolve_show,
    save_pdf,
    save_url,
    sync_blog_mirror,
    sync_curated,
)
from pipeline.show_config import SHOWS


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeCursor:
    """Records (sql, params) and serves a queue of canned fetchone rows."""

    def __init__(self, rows: list | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._rows = list(rows or [])

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.calls.append((sql, params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class _FakeConn:
    def __init__(self, rows: list | None = None) -> None:
        self.cursor_obj = _FakeCursor(rows)
        self.committed = False
        self.closed = False

    def cursor(self, **_kwargs) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class _Result:
    """The shape subprocess.run returns that this module reads."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _no_db(*_a, **_k):
    raise AssertionError("get_db_connection must not be called on this path")


# ── show resolution ──────────────────────────────────────────────────────────

def test_a_registered_blog_domain_resolves_to_its_own_show() -> None:
    assert resolve_show("https://www.anthropic.com/news/claude-fable-5") == "anthropic-blog"
    assert resolve_show("https://openai.com/index/some-post/") == "openai-blog"


def test_host_case_and_www_do_not_change_the_answer() -> None:
    # resolve_show runs the URL through canonicalize_url first, which lower-cases the
    # host and strips www. — so the three spellings of one domain are one show.
    assert resolve_show("https://WWW.Anthropic.COM/news/x") == "anthropic-blog"
    assert resolve_show("http://anthropic.com/news/x") == "anthropic-blog"


def test_an_unregistered_domain_falls_back_to_the_catch_all() -> None:
    assert resolve_show("https://www.technologyreview.com/2026/09/04/a-post/") == "saved-articles"


def test_the_domain_map_covers_only_blog_shows_that_declare_a_site() -> None:
    """The map is derived from show_config so there is no second list to drift.
    A show is in it only if medium == "blog" AND it has a fallback_website_url:
    saved-articles is the catch-all (no site of its own) and agentic-research is
    medium "research", so neither may appear — and no podcast may either, however
    many of them carry a website URL."""
    expected = {
        urlsplit(cfg.fallback_website_url).netloc.lower().removeprefix("www."): slug
        for slug, cfg in SHOWS.items()
        if cfg.medium == "blog" and cfg.fallback_website_url
    }
    mapping = domain_to_show()

    assert mapping == expected
    assert mapping == {"openai.com": "openai-blog", "anthropic.com": "anthropic-blog"}
    assert "saved-articles" not in mapping.values()
    assert "agentic-research" not in mapping.values()
    assert all(SHOWS[slug].medium == "blog" for slug in mapping.values())


def test_no_two_shows_claim_the_same_host() -> None:
    """Drift guard, the spirit of test_show_config.py: domain_to_show builds a dict,
    so if two blog shows ever resolved to one host the second would silently win and
    every save from that domain would land under the wrong show — with no error."""
    hosts = [
        urlsplit(cfg.fallback_website_url).netloc.lower().removeprefix("www.")
        for cfg in SHOWS.values()
        if cfg.medium == "blog" and cfg.fallback_website_url
    ]

    assert len(hosts) == len(set(hosts)), f"two blog shows share a host: {sorted(hosts)}"
    assert len(domain_to_show()) == len(hosts)


# ── the PDF gate ─────────────────────────────────────────────────────────────

def test_a_pdf_suffix_is_recognised_in_any_case() -> None:
    assert is_pdf("https://arxiv.org/pdf/2509.01234.pdf")
    assert is_pdf("https://example.com/Report.PDF")
    assert is_pdf("https://example.com/report.Pdf")


def test_a_query_string_or_fragment_after_the_suffix_still_counts() -> None:
    assert is_pdf("https://example.com/paper.pdf?download=1&utm_source=x")
    assert is_pdf("https://example.com/paper.pdf#page=4")


def test_a_url_that_merely_contains_pdf_is_not_a_pdf() -> None:
    """This gate decides whether a URL is ingested at all: is_pdf() True means the
    file is downloaded to the Obsidian folder and save_url returns True having
    written no episode row, run no extraction and synced nothing. A false positive
    here is a save that reports success and leaves no trace in Neon."""
    assert not is_pdf("https://example.com/pdf/report")
    assert not is_pdf("https://example.com/pdf-viewer/article")
    assert not is_pdf("https://example.com/a.pdf.html")
    assert not is_pdf("https://pdf.example.com/article")
    assert not is_pdf("https://example.com/article?file=paper.pdf")


# ── the mentions probe ───────────────────────────────────────────────────────

def test_an_episode_with_a_mention_row_reports_true() -> None:
    conn = _FakeConn(rows=[{"?column?": 1}])

    assert episode_has_mentions(conn, 4242) is True
    sql, params = conn.cursor_obj.calls[0]
    assert "FROM ai_mentions" in sql
    assert "WHERE episode_id = %s" in sql
    assert params == (4242,)


def test_an_episode_with_no_mention_rows_reports_false() -> None:
    """Existence is all this asks, so a genuinely mention-free article is
    indistinguishable from one that was never extracted — it will be re-extracted
    on every future re-save. Cheap and harmless today; pinned so a future reader
    recognises it as the design rather than a bug."""
    conn = _FakeConn(rows=[])

    assert episode_has_mentions(conn, 7) is False


# ── the PDF download ─────────────────────────────────────────────────────────

class _FakeStream:
    def __init__(self, chunks: list[bytes], recorder: list) -> None:
        self._chunks = chunks
        self._recorder = recorder

    def __call__(self, method: str, url: str, **kwargs):
        self._recorder.append((method, url, kwargs))
        return self

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield from self._chunks


def test_a_pdf_is_streamed_to_disk_under_the_research_folder(monkeypatch, tmp_path) -> None:
    calls: list = []
    monkeypatch.setenv("RESEARCH_DOCS_DIR", str(tmp_path / "Documents"))
    monkeypatch.setattr(httpx, "stream", _FakeStream([b"%PDF-1.7", b"\ntail"], calls))

    target = save_pdf("https://example.com/reports/state-of-ai.pdf?v=2")

    assert target == tmp_path / "Documents" / "state-of-ai.pdf"
    assert target.read_bytes() == b"%PDF-1.7\ntail"
    assert calls[0][0] == "GET"
    assert calls[0][2]["follow_redirects"] is True


def test_a_pathless_pdf_url_gets_a_default_filename(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCH_DOCS_DIR", str(tmp_path / "Documents"))
    monkeypatch.setattr(httpx, "stream", _FakeStream([b"x"], []))

    assert save_pdf("https://example.com/").name == "document.pdf"


def test_a_missing_research_folder_fails_loudly_and_never_downloads(monkeypatch, tmp_path) -> None:
    """PDF saves are a local-only action (the vault lives on Kevin's Mac). On a machine
    without it — CI — this must raise, not quietly write somewhere else."""
    def _never(*_a, **_k):
        raise AssertionError("no download may start when the folder is missing")

    monkeypatch.setenv("RESEARCH_DOCS_DIR", str(tmp_path / "nope" / "Documents"))
    monkeypatch.setattr(httpx, "stream", _never)

    with pytest.raises(SystemExit):
        save_pdf("https://example.com/paper.pdf")


# ── save_url: the composed flow ──────────────────────────────────────────────

@pytest.fixture()
def wired(monkeypatch):
    """Every collaborator of save_url, faked, with a record of what was called."""
    seen: dict = {"ingest": [], "extract": [], "sync": [], "pdf": [], "conns": []}

    def fake_ingest(conn, slug, url, api_key):
        seen["ingest"].append((slug, url, api_key))
        return 909

    def fake_extract(cfg, episodes, dry_run):
        seen["extract"].append((cfg.slug, [(e.episode_id, e.source) for e in episodes], dry_run))
        return seen.get("extract_ok", True)

    def fake_sync(cfg=None):
        seen["sync"].append(cfg.slug if cfg else None)
        return True

    def fake_conn():
        conn = _FakeConn()
        seen["conns"].append(conn)
        return conn

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(save_item, "get_db_connection", fake_conn)
    monkeypatch.setattr(save_item, "ingest_url", fake_ingest)
    monkeypatch.setattr(save_item, "episode_has_mentions", lambda conn, eid: seen.get("already", False))
    monkeypatch.setattr(save_item, "step_entity_extraction", fake_extract)
    monkeypatch.setattr(save_item, "sync_curated", fake_sync)
    monkeypatch.setattr(save_item, "save_pdf", lambda url: seen["pdf"].append(url))
    return seen


def test_a_pdf_url_never_opens_a_database_connection(monkeypatch, wired) -> None:
    monkeypatch.setattr(save_item, "get_db_connection", _no_db)

    assert save_url("https://example.com/reports/paper.pdf", None) is True
    assert wired["pdf"] == ["https://example.com/reports/paper.pdf"]
    assert wired["ingest"] == [] and wired["extract"] == [] and wired["sync"] == []


def test_a_missing_firecrawl_key_fails_before_the_database(monkeypatch, wired) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(save_item, "get_db_connection", _no_db)

    with pytest.raises(SystemExit):
        save_url("https://www.anthropic.com/news/x", None)


def test_a_saved_url_is_stored_extracted_and_synced(wired) -> None:
    assert save_url("https://www.anthropic.com/news/x", None) is True

    assert wired["ingest"] == [("anthropic-blog", "https://www.anthropic.com/news/x", "fc-test")]
    assert wired["extract"] == [("anthropic-blog", [(909, "transcript")], False)]
    assert wired["sync"] == ["anthropic-blog"]
    assert wired["conns"][0].closed is True


def test_an_explicit_show_slug_overrides_domain_resolution(wired) -> None:
    save_url("https://www.technologyreview.com/a-post", "openai-blog")

    assert wired["ingest"][0][0] == "openai-blog"


def test_skip_extract_stores_the_text_and_stops_there(wired) -> None:
    assert save_url("https://www.anthropic.com/news/x", None, skip_extract=True) is True

    assert wired["ingest"] and wired["extract"] == []
    assert wired["sync"] == ["anthropic-blog"]  # the mirror still runs


def test_re_saving_an_already_extracted_url_skips_extraction_without_erroring(wired) -> None:
    """Re-saving is meant to be safe and idempotent: the second save must not
    re-extract (that would double the mention rows) and must not fail."""
    wired["already"] = True

    assert save_url("https://www.anthropic.com/news/x", None) is True
    assert wired["extract"] == []


def test_sync_false_returns_before_the_notion_pass(wired) -> None:
    """The weekly intake ingests dozens of posts per run and syncs once at the end —
    syncing the whole tech group after each post cost ~40s a post on 2026-09-02."""
    assert save_url("https://www.anthropic.com/news/x", None, sync=False) is True

    assert wired["extract"]  # it still stored and extracted
    assert wired["sync"] == []


def test_a_failed_extraction_still_syncs_and_still_returns_false(wired) -> None:
    """Whatever DID load should reach Notion; the False return is what makes the gap
    visible to the run instead of swallowing it."""
    wired["extract_ok"] = False

    assert save_url("https://www.anthropic.com/news/x", None) is False
    assert wired["sync"] == ["anthropic-blog"]


def test_the_connection_is_closed_even_when_the_ingest_raises(monkeypatch, wired) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("scrape too thin")

    monkeypatch.setattr(save_item, "ingest_url", boom)

    with pytest.raises(RuntimeError):
        save_url("https://www.anthropic.com/news/x", None)
    assert wired["conns"][0].closed is True


# ── the Notion passes ────────────────────────────────────────────────────────

def test_the_blog_mirror_targets_the_blog_posts_database(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(save_item.subprocess, "run",
                        lambda cmd, **kw: calls.append((cmd, kw)) or _Result(0))

    assert sync_blog_mirror() is True
    cmd = calls[0][0]
    assert Path(cmd[1]).name == "sync_transcripts_notion.py"
    assert cmd[2:] == ["--target", "blog-posts"]


def test_a_failing_mirror_subprocess_reports_false(monkeypatch) -> None:
    monkeypatch.setattr(save_item.subprocess, "run", lambda cmd, **kw: _Result(2))

    assert sync_blog_mirror() is False


def test_the_full_text_mirror_runs_even_when_the_entity_sync_fails(monkeypatch) -> None:
    """sync_blog_mirror() is the LEFT operand of the `and`, deliberately: a failing
    entity sync must not short-circuit the full-text mirror, or one broken Notion
    call would silently skip the other database."""
    ran: list = []
    monkeypatch.setattr(save_item, "step_notion_sync", lambda cfg, dry_run: False)
    monkeypatch.setattr(save_item, "sync_blog_mirror", lambda: ran.append("mirror") or True)

    assert sync_curated(SHOWS["anthropic-blog"]) is False
    assert ran == ["mirror"]


def test_sync_curated_defaults_to_the_saved_articles_config(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(save_item, "step_notion_sync", lambda cfg, dry_run: seen.append(cfg.slug) or True)
    monkeypatch.setattr(save_item, "sync_blog_mirror", lambda: True)

    assert sync_curated() is True
    # Any curated show's config addresses the shared Tech DB — saved-articles is the
    # one that always exists, so it is the default entry point.
    assert seen == ["saved-articles"]

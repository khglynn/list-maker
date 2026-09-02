"""The intake orchestrator's decisions, with the network and the database mocked out.

Replaces tests/test_build_pull_queue.py. That file pinned one lesson worth carrying
over: eleven consecutive weekly runs (2026-06-21 → 08-31) found nothing new, said
nothing, and left 31 candidates un-triaged. So the weekly line is tested here for the
same property — it speaks on a dry week — plus the ones the judge added: shadow mode
must be legible in the message, a skip must say why, and a run that failed anything
must not exit 0.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import run_intake as R
from pipeline.scrapers.intake import store
from pipeline.scrapers.intake.sources import Candidate


@pytest.fixture(autouse=True)
def _shadow_unless_marked_live(request, monkeypatch):
    """Every run() test states its mode. AUTO_INGEST is True in production (2026-09-02),
    so a test that never mentions the switch would silently take the ingest path;
    shadow mode is the safe default here, and a test opts into the live path by
    setting R.AUTO_INGEST itself. The catch-up query is inert unless a test stubs it.
    The one test that pins the production value carries the `live` marker."""
    if not request.node.get_closest_marker("live"):
        monkeypatch.setattr(R, "AUTO_INGEST", False)
    monkeypatch.setattr(store, "pending", lambda conn, status, limit=None: [])
    # The end-of-run Notion pass shells out to two sync scripts; a test that wants to
    # count it replaces this stub.
    monkeypatch.setattr(R, "sync_ingested", lambda: True)


def _candidate(url: str, source: str = "openai-rss", title: str = "T") -> Candidate:
    return Candidate(source=source, title=title, url=url, published_on=date(2026, 9, 1))


# ── the switch ──────────────────────────────────────────────────────────────

@pytest.mark.live
def test_auto_ingest_is_on_since_kevin_approved_the_labels() -> None:
    """Flipped 2026-09-02 after the eval floor cleared on Kevin's own labels (recall 0.96,
    precision 0.91) and he read the first shadow run. The constant is the switch back;
    this assertion is what makes a silent flip in either direction a reviewed change."""
    assert R.AUTO_INGEST is True


# ── the weekly line ─────────────────────────────────────────────────────────

def _counts(**kw) -> dict:
    base = {"judged": 0, "would_save": 0, "saved": 0, "judge_skipped": 0,
            "precheck_skipped": 0, "disputed": 0, "held": 0, "failed": 0,
            "overrides": 0, "precheck_reasons": {}, "would_save_backlog": 0}
    base.update(kw)
    return base


def test_weekly_line_speaks_on_a_dry_week() -> None:
    line = R.weekly_line(_counts(), [], [], auto_ingest=False)
    assert "judged 0" in line and "would save 0" in line and "skipped 0" in line
    assert R.notion_log.INTAKE_URL in line


def test_weekly_line_says_shadow_mode_and_names_what_it_would_save() -> None:
    line = R.weekly_line(
        _counts(judged=12, would_save=6, judge_skipped=6),
        ["How people are using ChatGPT", "Claude Fable 5.1", "A", "B", "C", "D"],
        [], auto_ingest=False,
    )
    assert "shadow mode — nothing auto-ingests yet" in line
    assert "would save 6" in line
    assert "How people are using ChatGPT" in line
    assert "(+1 more)" in line  # five names shown, the count says there are six


def test_weekly_line_switches_verb_when_auto_ingest_is_on() -> None:
    line = R.weekly_line(_counts(judged=3, would_save=2, saved=2), ["X"], [], auto_ingest=True)
    assert "saved 2" in line and "would save" not in line and "shadow mode" not in line


def test_weekly_line_counts_actual_ingests_once_auto_ingest_is_on() -> None:
    """A save that then failed to ingest must not be claimed as a save.

    `would_save` counts verdicts and `saved` counts ingests; before this they were the
    same number, so a row that judged save and failed to ingest was reported under
    both "saved" and "failed" on the same line.
    """
    counts = _counts(judged=5, would_save=4, saved=3, failed=1)
    assert "saved 3" in R.weekly_line(counts, [], [], auto_ingest=True)
    # in shadow mode nothing is ingested, so the verdict count is the honest one
    assert "would save 4" in R.weekly_line(counts, [], [], auto_ingest=False)


def test_weekly_line_says_why_things_were_skipped() -> None:
    line = R.weekly_line(
        _counts(judged=4, judge_skipped=2, precheck_skipped=29,
                precheck_reasons={"thin": 18, "duplicate": 9, "dead": 2}),
        [], [], auto_ingest=False)
    # a count with no cause is a number nobody can act on
    assert "skipped 31 (18 thin, 9 duplicate, 2 dead)" in line


def test_weekly_line_names_held_pdfs_and_flags_disputes_and_backlog() -> None:
    line = R.weekly_line(_counts(judged=5, disputed=2, held=2),
                         [], ["ai-index-2026.pdf", "gpt5-system-card.pdf"],
                         auto_ingest=False, backlog=14)
    assert "2 disputed" in line
    assert "held 2 (ai-index-2026.pdf, gpt5-system-card.pdf)" in line
    assert "14 waiting for the next run" in line


def test_weekly_line_surfaces_a_ticked_row_with_no_neon_row() -> None:
    line = R.weekly_line(_counts(), [], [], auto_ingest=False, unknown_overrides=2)
    assert f"2 ticked row(s) not in {store.TABLE}" in line


# ── discovery ───────────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, latest=None) -> None:
        self.latest = latest

    def cursor(self):
        raise AssertionError("these tests never reach SQL")


def test_feed_since_catches_up_from_the_newest_post_we_hold(monkeypatch) -> None:
    monkeypatch.setattr(store, "last_seen_published", lambda conn, src: date(2026, 8, 1))
    # A skipped run must not leave a hole: ask from the last post, not the last run.
    assert R.feed_since(_Conn(), "openai-rss", True, 14) == date(2026, 7, 31)


def test_feed_since_falls_back_to_the_lookback_on_an_empty_table(monkeypatch) -> None:
    monkeypatch.setattr(store, "last_seen_published", lambda conn, src: None)
    assert R.feed_since(_Conn(), "openai-rss", False, 14) == date.today() - timedelta(days=14)


def test_discover_reports_a_failed_source_instead_of_swallowing_it(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("openai.com timed out")

    monkeypatch.setattr(R.sources, "fetch_openai_rss", boom)
    monkeypatch.setattr(R.sources, "fetch_anthropic_index",
                        lambda slug, key, since: [_candidate(f"https://x/{slug}", slug)])
    monkeypatch.setattr(store, "last_seen_published", lambda conn, src: None)
    found, notes = R.discover(_Conn(), groups="feeds", table_ok=False, firecrawl_key="k",
                              lookback_days=14, resolve_links=False, dry_run=False)
    # "0 from OpenAI" must never be mistaken for "OpenAI published nothing"
    assert any("openai-rss: FAILED" in n for n in notes)
    assert len(found) == 2  # the other two sources still ran


def test_discover_honours_the_sources_selector(monkeypatch) -> None:
    monkeypatch.setattr(R.mentions, "discover_cited_candidates",
                        lambda conn: [_candidate("https://x/cited", "podcast-cited")])
    monkeypatch.setattr(R.sources, "fetch_openai_rss",
                        lambda since: pytest.fail("feeds must not run for --sources podcast-cited"))
    found, notes = R.discover(_Conn(), groups="podcast-cited", table_ok=True,
                              firecrawl_key="k", lookback_days=14,
                              resolve_links=False, dry_run=False)
    assert [c.source for c in found] == ["podcast-cited"]
    assert any("skipped (pass --resolve-links)" in n for n in notes)


def test_discover_accepts_caller_supplied_candidates(monkeypatch) -> None:
    monkeypatch.setattr(R.mentions, "discover_cited_candidates", lambda conn: [])
    found, notes = R.discover(_Conn(), groups="podcast-cited", table_ok=True,
                              firecrawl_key=None, lookback_days=14, resolve_links=False,
                              dry_run=False, extra_candidates=[_candidate("https://x/e")])
    assert [c.url for c in found] == ["https://x/e"]
    assert any("extra_candidates" in n for n in notes)


def test_dedupe_collapses_the_same_post_seen_by_two_sources() -> None:
    kept, collapsed = R.dedupe([
        _candidate("https://openai.com/index/a/", "openai-rss"),
        _candidate("http://www.openai.com/index/a?utm_source=x", "podcast-cited"),
        _candidate("", "podcast-cited"),  # an unresolved citation is not a candidate
    ])
    assert collapsed == 1
    assert [c.url for c in kept] == ["https://openai.com/index/a"]
    assert kept[0].source == "openai-rss"  # first source wins; the second is kept in provenance


# ── one candidate through the machine ───────────────────────────────────────

class _Recorder:
    """Stands in for the store: records the calls instead of writing to Neon."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args[1:], kwargs))
            return kwargs.get("_return")
        return record

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def _patch_store(monkeypatch, rec: _Recorder, **overrides):
    for name in ("record_precheck", "record_scrape", "mark_saved", "mark_failed"):
        monkeypatch.setattr(store, name, rec(name))
    monkeypatch.setattr(store, "record_decision",
                        overrides.get("record_decision",
                                      lambda conn, i, d, status=None: "judged"))


def test_a_duplicate_never_reaches_firecrawl_or_a_model(monkeypatch) -> None:
    rec = _Recorder()
    _patch_store(monkeypatch, rec)
    monkeypatch.setattr(R, "scrape_measurements",
                        lambda *a, **k: pytest.fail("a duplicate must not be scraped"))
    status = R.process_candidate(
        object(), {"id": 1, "url": "https://x/a", "source": "openai-rss"},
        already_ingested=True, firecrawl_key="k", openrouter_key="o",
        rubric_path=R.judge.RUBRIC_PATH)
    assert status == "skipped" and rec.names() == ["record_precheck"]
    assert rec.calls[0][1][1].skip_reason == "duplicate"


def test_free_precheck_skips_academy_people_news_and_stale_before_firecrawl() -> None:
    # These three need only what discovery already knows, so they must fire on the
    # free pass — a Firecrawl credit spent to learn a post is an OpenAI Academy course
    # is a credit spent to learn nothing.
    def row(**kw):
        base = {"url": "https://openai.com/index/a", "title": "T", "category": [],
                "published_on": date(2026, 9, 1), "source": "openai-rss"}
        base.update(kw)
        return base

    assert R.free_precheck(row(category=["OpenAI Academy"]),
                           already_ingested=False).skip_reason == "academy"
    assert R.free_precheck(row(published_on=date(2020, 1, 1)),
                           already_ingested=False).skip_reason == "stale"
    # a podcast-cited row has no title or date yet, so nothing fires until the scrape
    assert R.free_precheck({"url": "https://x/a", "title": "", "category": [],
                            "published_on": None, "source": "podcast-cited"},
                           already_ingested=False).skip_reason is None


def test_a_pdf_is_held_before_the_scrape(monkeypatch) -> None:
    rec = _Recorder()
    _patch_store(monkeypatch, rec)
    monkeypatch.setattr(R, "scrape_measurements",
                        lambda *a, **k: pytest.fail("a PDF must not be scraped"))
    status = R.process_candidate(
        object(), {"id": 1, "url": "https://x/report.pdf", "source": "podcast-cited"},
        already_ingested=False, firecrawl_key="k", openrouter_key="o",
        rubric_path=R.judge.RUBRIC_PATH)
    assert status == "held"


def test_the_post_scrape_pass_catches_what_discovery_could_not_know(monkeypatch) -> None:
    # A podcast-cited row has no title until the scrape, so its people-news skip can
    # only fire on the second pass. The scrape is still recorded: "we looked" is a
    # fact worth keeping even when the answer is no.
    rec = _Recorder()
    _patch_store(monkeypatch, rec)
    monkeypatch.setattr(R, "scrape_measurements", lambda url, key: (
        {"text": "words " * 900, "words": 900, "links_out": 3, "text_sha256": "sha",
         "title": "Sam Altman steps down as chairman", "published_on": date(2026, 9, 1)}, None))
    monkeypatch.setattr(R.judge, "judge_candidate",
                        lambda **k: pytest.fail("people news is not worth a model call"))
    status = R.process_candidate(
        object(), {"id": 1, "url": "https://x/a", "source": "podcast-cited",
                   "title": "", "category": [], "published_on": None},
        already_ingested=False, firecrawl_key="k", openrouter_key="o",
        rubric_path=R.judge.RUBRIC_PATH)
    assert status == "skipped"
    assert rec.names() == ["record_scrape", "record_precheck"]
    assert rec.calls[1][1][1].skip_reason == "people-news"


def test_a_thin_scrape_is_recorded_then_skipped_without_a_model(monkeypatch) -> None:
    rec = _Recorder()
    _patch_store(monkeypatch, rec)
    monkeypatch.setattr(R, "scrape_measurements", lambda url, key: (
        {"text": "x " * 20, "words": 20, "links_out": 0, "text_sha256": "sha",
         "title": "Stub", "published_on": None}, None))
    monkeypatch.setattr(R.judge, "judge_candidate",
                        lambda **k: pytest.fail("a 20-word stub is not worth a model call"))
    status = R.process_candidate(
        object(), {"id": 1, "url": "https://x/a", "source": "openai-rss"},
        already_ingested=False, firecrawl_key="k", openrouter_key="o",
        rubric_path=R.judge.RUBRIC_PATH)
    # the measurement is still stored: "we looked, and it was thin" beats no row at all
    assert rec.names() == ["record_scrape", "record_precheck"]
    assert status == "skipped"


def test_a_dead_link_is_skipped_with_the_error_kept(monkeypatch) -> None:
    rec = _Recorder()
    _patch_store(monkeypatch, rec)
    monkeypatch.setattr(R, "scrape_measurements", lambda url, key: ({}, "404 Not Found"))
    status = R.process_candidate(
        object(), {"id": 1, "url": "https://x/a", "source": "openai-rss"},
        already_ingested=False, firecrawl_key="k", openrouter_key="o",
        rubric_path=R.judge.RUBRIC_PATH)
    assert status == "skipped"
    assert rec.names() == ["record_precheck"]  # nothing measured, so nothing recorded
    assert rec.calls[0][2]["detail"] == "404 Not Found"


def test_a_real_post_is_judged_with_what_the_scrape_measured(monkeypatch) -> None:
    seen: dict = {}
    _patch_store(monkeypatch, _Recorder())
    monkeypatch.setattr(R, "scrape_measurements", lambda url, key: (
        {"text": "words " * 900, "words": 900, "links_out": 14, "text_sha256": "sha",
         "title": "How people are using ChatGPT", "published_on": date(2026, 9, 1)}, None))
    monkeypatch.setattr(R.judge, "judge_candidate", lambda **k: seen.update(k) or "D")
    monkeypatch.setattr(store, "record_decision", lambda conn, i, d, status=None: "judged")
    status = R.process_candidate(
        object(), {"id": 1, "url": "https://x/a", "source": "openai-rss",
                   "discovered_via": {"shows": ["The AI Daily Brief"], "cited_in_episodes": 2}},
        already_ingested=False, firecrawl_key="k", openrouter_key="o",
        rubric_path=R.judge.RUBRIC_PATH)
    assert status == "judged"
    assert seen["words"] == 900 and seen["links_out"] == 14
    assert seen["title"] == "How people are using ChatGPT"
    # how it was found is part of what the rubric judges, not just decoration
    assert "The AI Daily Brief" in seen["found_via"]


# ── the override door ───────────────────────────────────────────────────────

def test_ingest_one_catches_system_exit_so_one_bad_row_cannot_end_the_run(monkeypatch) -> None:
    rec = _Recorder()
    _patch_store(monkeypatch, rec)

    def refuses(url, show):
        raise SystemExit("Research folder not found — PDF saves are local-only")

    assert R.ingest_one(object(), {"id": 1, "url": "https://x/r.pdf"}, refuses,
                        override_by="kevin") is False
    assert rec.names() == ["mark_failed"]
    assert "local-only" in rec.calls[0][1][1]
    assert rec.calls[0][2]["override_by"] == "kevin"


def test_process_overrides_ingests_ticked_rows_and_records_who_asked(monkeypatch) -> None:
    monkeypatch.setattr(R.notion_log, "override_rows", lambda token, db: [
        {"page_id": "p1", "url": "https://openai.com/index/a/"},
        {"page_id": "p2", "url": "https://x/not-a-candidate"},
    ])
    monkeypatch.setattr(store, "get_by_urls", lambda conn, urls: {
        "https://openai.com/index/a": {"id": 5, "url": "https://openai.com/index/a"}})
    rec = _Recorder()
    _patch_store(monkeypatch, rec)
    monkeypatch.setattr(store, "episode_id_for_url", lambda *a: 42, raising=False)
    monkeypatch.setattr(R, "episode_id_for_url", lambda conn, url: 42)
    mirrored: list = []
    monkeypatch.setattr(R, "_mirror", lambda *a, **k: mirrored.append(a[-1]) or True)

    ok, failed, unknown = R.process_overrides(object(), "tok", "db", lambda url, show: True)
    assert (ok, failed) == (1, 0)
    # a URL with no Neon row is REPORTED, not ingested: a save with nowhere to record
    # its provenance is a value nothing can trace later
    assert unknown == ["https://x/not-a-candidate"]
    assert rec.calls[0] == ("mark_saved", (5, 42), {"override_by": "kevin"})
    # the ticked rows carry their own page ids — without passing them as the adoption
    # map, a legacy row with no stored id would get a SECOND Notion page
    assert mirrored and isinstance(mirrored[-1], dict)


def test_process_overrides_is_silent_when_nothing_is_ticked(monkeypatch) -> None:
    monkeypatch.setattr(R.notion_log, "override_rows", lambda token, db: [])
    assert R.process_overrides(object(), "tok", "db", lambda u, s: True) == (0, 0, [])


# ── the run's contract with CI ──────────────────────────────────────────────

def test_a_missing_table_stops_a_real_run_with_the_paste_instruction(monkeypatch) -> None:
    monkeypatch.setattr(store, "table_exists", lambda conn: False)
    args = R.parse_args([])
    with pytest.raises(SystemExit) as exc:
        R.run(args, object(), "tok", "fc", "or")
    assert store.MIGRATION_PATH in str(exc.value)


def test_a_missing_table_only_warns_in_a_dry_run(monkeypatch, capsys) -> None:
    monkeypatch.setattr(store, "table_exists", lambda conn: False)
    monkeypatch.setattr(store, "already_ingested_urls", lambda conn, urls: set())
    monkeypatch.setattr(R, "discover", lambda *a, **k: ([_candidate("https://x/a")], ["note"]))
    assert R.run(R.parse_args(["--dry-run"]), object(), "", None, None) == 0
    out = capsys.readouterr().out
    # the dry run is the only way to see the plan BEFORE Kevin runs the DDL, so it has
    # to work without the table — loudly, never silently
    assert "does not exist yet" in out and store.MIGRATION_PATH in out


def test_a_missing_rubric_stops_a_real_run(monkeypatch) -> None:
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(R.judge, "load_rubric", lambda: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(SystemExit) as exc:
        R.run(R.parse_args([]), object(), "tok", "fc", "or")
    assert "rubric is missing" in str(exc.value)


def test_require_secrets_names_every_missing_key_at_once(monkeypatch) -> None:
    for key in ("NOTION_TOKEN", "FIRECRAWL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit) as exc:
        R.require_secrets(R.parse_args([]))
    message = str(exc.value)
    # all three at once: finding them one failed run at a time is three wasted weeks
    assert all(k in message for k in ("NOTION_TOKEN", "FIRECRAWL_API_KEY", "OPENROUTER_API_KEY"))


def test_ensure_log_schema_needs_only_the_notion_token(monkeypatch) -> None:
    """blogs.yml runs the schema step with NOTION_TOKEN and nothing else.

    Caught by reading that step's env against require_secrets, not by a test: the
    schema pass neither scrapes nor judges, and demanding those keys would have
    failed the very first real run at the step before any work happened.
    """
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    for key in ("FIRECRAWL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert R.require_secrets(R.parse_args(["--ensure-log-schema"]))[0] == "tok"


def test_overrides_only_needs_no_scrape_or_judge_keys(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    for key in ("FIRECRAWL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert R.require_secrets(R.parse_args(["--overrides-only"]))[0] == "tok"


def _headless(monkeypatch, headless: bool = True) -> None:
    monkeypatch.setattr(R.sys.stdin, "isatty", lambda: not headless, raising=False)


def test_an_unattended_run_that_posts_requires_a_webhook(monkeypatch) -> None:
    """An unset webhook must fail up front in CI, not after the whole week is judged.

    common.post_slack logs and returns False when SLACK_WEBHOOK_URL is missing — by
    design, "alerting must not break a pipeline run" — so nothing downstream would
    notice. --ensure-log-schema posts nothing and stays exempt, which is what
    blogs.yml's schema step (NOTION_TOKEN only) relies on.
    """
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    _headless(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        R.require_secrets(R.parse_args([]))
    assert "SLACK_WEBHOOK_URL" in str(exc.value)
    with pytest.raises(SystemExit):
        R.require_secrets(R.parse_args(["--overrides-only"]))
    # the schema pass posts nothing, so it still runs on NOTION_TOKEN alone
    assert R.require_secrets(R.parse_args(["--ensure-log-schema"]))[0] == "tok"


def test_a_terminal_run_does_not_demand_a_webhook(monkeypatch) -> None:
    """Kevin has no SLACK_WEBHOOK_URL in ~/.env or .env.local (checked 2026-09-02), and
    the runbook documents three hand-run commands. Refusing to start would break all
    three to guard a post whose failure the run already catches on the way out — the
    same headless/interactive split common.ensure_spotify_token makes."""
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    _headless(monkeypatch, headless=False)
    assert R.require_secrets(R.parse_args([]))[0] == "tok"


def test_a_dry_run_needs_no_secrets(monkeypatch) -> None:
    for key in ("NOTION_TOKEN", "FIRECRAWL_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert R.require_secrets(R.parse_args(["--dry-run"])) == ("", None, None)


def test_dry_run_writes_nothing(monkeypatch, capsys) -> None:
    for name in ("upsert_candidates", "record_decision", "record_scrape", "record_precheck"):
        monkeypatch.setattr(store, name, lambda *a, **k: pytest.fail(f"{name} wrote in a dry run"))
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(store, "get_by_urls", lambda conn, urls: {})
    monkeypatch.setattr(store, "already_ingested_urls", lambda conn, urls: set())
    monkeypatch.setattr(R.judge, "load_rubric", lambda: ("rubric", "v0abc"))
    monkeypatch.setattr(R, "discover", lambda *a, **k: (
        [_candidate("https://x/a"), _candidate("https://x/r.pdf")], ["openai-rss: 2 since …"]))
    monkeypatch.setattr(R, "post_slack", lambda text: pytest.fail("a dry run must not Slack"))

    assert R.run(R.parse_args(["--dry-run"]), object(), "", None, None) == 0
    out = capsys.readouterr().out
    assert "pdf (held): 1" in out
    assert "would scrape + judge: 1" in out
    assert "rubric version v0abc" in out


# ── the log catches up on what a Notion outage dropped ──────────────────────

def test_run_mirrors_rows_the_log_is_behind_on_before_judging(monkeypatch) -> None:
    """A verdict Neon holds but Notion never got must not be stranded forever.

    The judging loop only visits rows that still need a verdict, so a row whose Notion
    write failed last week is never revisited — without this catch-up, one outage
    keeps a judged post off Kevin's surface permanently.
    """
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(R.judge, "load_rubric", lambda: ("rubric", "v0abc"))
    monkeypatch.setattr(R, "discover", lambda *a, **k: ([], []))
    monkeypatch.setattr(store, "upsert_candidates", lambda conn, c: (0, 0))
    monkeypatch.setattr(store, "needs_judging", lambda conn, v: [{"id": 7, "url": "https://x/7"}])
    monkeypatch.setattr(store, "already_ingested_urls", lambda conn, urls: set())
    monkeypatch.setattr(R.notion_log, "existing_page_ids", lambda token, db: {})
    monkeypatch.setattr(store, "needs_mirroring",
                        lambda conn, limit=None: [{"id": 7, "url": "https://x/7"},
                                                  {"id": 9, "url": "https://x/9"}])
    monkeypatch.setattr(R, "process_candidate", lambda *a, **k: "judged")
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (0, 0, []))
    monkeypatch.setattr(store, "weekly_counts", lambda conn, since: _counts())
    monkeypatch.setattr(store, "titles", lambda conn, since, status, **k: [])
    mirrored: list[int] = []
    monkeypatch.setattr(R, "_mirror",
                        lambda conn, token, db, cid, pages=None: mirrored.append(cid) or True)
    monkeypatch.setattr(R, "post_slack", lambda text: True)

    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 0
    # 9 is caught up; 7 is in this run's work list and is mirrored there, once
    assert mirrored == [9, 7]


def test_a_failed_mirror_fails_the_run(monkeypatch) -> None:
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(R.judge, "load_rubric", lambda: ("rubric", "v0abc"))
    monkeypatch.setattr(R, "discover", lambda *a, **k: ([], []))
    monkeypatch.setattr(store, "upsert_candidates", lambda conn, c: (0, 0))
    monkeypatch.setattr(store, "needs_judging", lambda conn, v: [])
    monkeypatch.setattr(store, "already_ingested_urls", lambda conn, urls: set())
    monkeypatch.setattr(R.notion_log, "existing_page_ids", lambda token, db: {})
    monkeypatch.setattr(store, "needs_mirroring", lambda conn, limit=None: [{"id": 9}])
    monkeypatch.setattr(R, "_mirror", lambda *a, **k: False)
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (0, 0, []))
    monkeypatch.setattr(store, "weekly_counts", lambda conn, since: _counts())
    monkeypatch.setattr(store, "titles", lambda conn, since, status, **k: [])
    monkeypatch.setattr(R, "post_slack", lambda text: True)
    # the Slack line still posts — the week is still reported — but the run is red
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 1


def test_mirror_records_the_sync_even_when_the_page_already_existed(monkeypatch) -> None:
    """Otherwise a row whose content changed after its first mirror never catches up.

    An override ingest bumps `updated_at` but reuses the same Notion page, so
    recording the sync only on a NEW page id left `notion_synced_at` behind
    `updated_at` forever — and needs_mirroring re-pushed that row every single run.
    """
    recorded: list = []
    monkeypatch.setattr(store, "get_by_id",
                        lambda conn, cid: {"id": cid, "url": "https://x/a",
                                           "status": "saved", "notion_page_id": "p1"})
    monkeypatch.setattr(R.notion_log, "upsert_row", lambda t, d, row, pages=None: "p1")
    monkeypatch.setattr(store, "record_notion_page",
                        lambda conn, cid, pid: recorded.append((cid, pid)))
    assert R._mirror(object(), "tok", "db", 5) is True
    assert recorded == [(5, "p1")]


# ── the weekly line is the deliverable, so a failed post is a failed run ────

def _stub_a_quiet_run(monkeypatch, posted: bool):
    """A run with no work to do, so only the Slack post can change the outcome."""
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(R.judge, "load_rubric", lambda: ("rubric", "v0abc"))
    monkeypatch.setattr(R, "discover", lambda *a, **k: ([], []))
    monkeypatch.setattr(store, "upsert_candidates", lambda conn, c: (0, 0))
    monkeypatch.setattr(store, "needs_judging", lambda conn, v: [])
    monkeypatch.setattr(store, "needs_mirroring", lambda conn, limit=None: [])
    monkeypatch.setattr(store, "already_ingested_urls", lambda conn, urls: set())
    monkeypatch.setattr(R.notion_log, "existing_page_ids", lambda token, db: {})
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (0, 0, []))
    monkeypatch.setattr(store, "weekly_counts", lambda conn, since: _counts())
    monkeypatch.setattr(store, "titles", lambda conn, since, status, **k: [])
    monkeypatch.setattr(R, "post_slack", lambda text: posted)


def test_a_weekly_line_that_did_not_post_fails_the_run(monkeypatch) -> None:
    """Slack is the only thing that reaches Kevin unprompted.

    A revoked webhook or a Slack outage used to leave the run green with nobody told —
    the exact silence this whole PR exists to kill, and the same hazard pulse_report.py
    already guards against with a comment naming it.
    """
    _stub_a_quiet_run(monkeypatch, posted=False)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 1


def test_a_posted_weekly_line_leaves_a_quiet_run_green(monkeypatch) -> None:
    _stub_a_quiet_run(monkeypatch, posted=True)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 0


def test_overrides_only_also_fails_when_its_line_does_not_post(monkeypatch) -> None:
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(R.judge, "load_rubric", lambda: ("rubric", "v0abc"))
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (0, 0, []))
    monkeypatch.setattr(store, "weekly_counts", lambda conn, since: _counts())
    monkeypatch.setattr(R, "post_slack", lambda text: False)
    assert R.run(R.parse_args(["--overrides-only"]), object(), "tok", "fc", "or") == 1


# ── the switch PR 3 flips ───────────────────────────────────────────────────

def _stub_one_saved_candidate(monkeypatch, statuses: list, ingest_ok: bool = True):
    """One candidate that judges `save`, with every collaborator recorded."""
    monkeypatch.setattr(store, "table_exists", lambda conn: True)
    monkeypatch.setattr(R.judge, "load_rubric", lambda: ("rubric", "v0abc"))
    monkeypatch.setattr(R, "discover", lambda *a, **k: ([], []))
    monkeypatch.setattr(store, "upsert_candidates", lambda conn, c: (0, 0))
    monkeypatch.setattr(store, "needs_judging",
                        lambda conn, v: [{"id": 7, "url": "https://x/7", "source": "openai-rss"}])
    monkeypatch.setattr(store, "needs_mirroring", lambda conn, limit=None: [])
    monkeypatch.setattr(store, "already_ingested_urls", lambda conn, urls: set())
    monkeypatch.setattr(store, "get_by_id", lambda conn, cid: {"id": cid, "url": "https://x/7"})
    monkeypatch.setattr(R.notion_log, "existing_page_ids", lambda token, db: {})
    monkeypatch.setattr(R, "process_candidate", lambda *a, **k: store.STATUS_JUDGED)
    monkeypatch.setattr(R, "ingest_one", lambda *a, **k: ingest_ok)
    monkeypatch.setattr(R, "_mirror", lambda *a, **k: True)
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (0, 0, []))
    monkeypatch.setattr(store, "weekly_counts", lambda conn, since: _counts())
    monkeypatch.setattr(store, "titles",
                        lambda conn, since, status, **k: statuses.append(status) or [])
    monkeypatch.setattr(R, "post_slack", lambda text: True)


def test_the_titles_query_follows_auto_ingest(monkeypatch) -> None:
    """Once auto-ingest is on, no row is left at `judged` when the line is built.

    The save is ingested in the same loop iteration, so asking for STATUS_JUDGED
    would print an empty "Saved:" list on the very week the names matter most. This
    is the wiring the pure weekly_line() tests can't see.
    """
    seen: list = []
    _stub_one_saved_candidate(monkeypatch, seen)
    monkeypatch.setattr(R, "AUTO_INGEST", True)
    R.run(R.parse_args([]), object(), "tok", "fc", "or")
    assert seen[0] == store.STATUS_SAVED

    seen.clear()
    monkeypatch.setattr(R, "AUTO_INGEST", False)
    R.run(R.parse_args([]), object(), "tok", "fc", "or")
    assert seen[0] == store.STATUS_JUDGED


def test_auto_ingest_on_ingests_a_save_and_shadow_mode_does_not(monkeypatch) -> None:
    calls: list = []
    _stub_one_saved_candidate(monkeypatch, [])
    monkeypatch.setattr(R, "ingest_one", lambda *a, **k: calls.append(a[1]["id"]) or True)

    monkeypatch.setattr(R, "AUTO_INGEST", False)
    R.run(R.parse_args([]), object(), "tok", "fc", "or")
    assert calls == [], "shadow mode must record the verdict and ingest nothing"

    monkeypatch.setattr(R, "AUTO_INGEST", True)
    R.run(R.parse_args([]), object(), "tok", "fc", "or")
    assert calls == [7]


def test_a_failed_auto_ingest_fails_the_run(monkeypatch) -> None:
    # ingest_one swallows the failure by design so one bad row can't end the week —
    # discarding its bool left the run green and blogs.yml's notify silent.
    _stub_one_saved_candidate(monkeypatch, [], ingest_ok=False)
    monkeypatch.setattr(R, "AUTO_INGEST", True)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 1


# ── main() turns a failure count into the exit code CI reads ────────────────

def test_main_exits_non_zero_so_the_workflow_notify_fires(monkeypatch) -> None:
    """blogs.yml's "Notify Slack (failure)" step is gated on `if: failure()`.

    That alert exists only because main() translates run()'s count into a SystemExit,
    which is two branch-free lines nothing else covers.
    """
    monkeypatch.setattr(R, "load_environment", lambda: None)
    monkeypatch.setattr(R, "require_secrets", lambda args: ("tok", "fc", "or"))
    monkeypatch.setattr(R, "get_db_connection", lambda: _closable())
    monkeypatch.setattr(R, "run", lambda *a, **k: 3)
    with pytest.raises(SystemExit) as exc:
        R.main([])
    assert "3 candidate(s) failed" in str(exc.value)

    monkeypatch.setattr(R, "run", lambda *a, **k: 0)
    R.main([])  # a clean run returns normally, so the step exits 0


def test_auto_ingest_catches_up_saves_judged_in_shadow_mode(monkeypatch) -> None:
    """The shadow runs left rows at `judged` + `save`; with the switch on, a run must
    ingest them, bounded, not only the rows it judged itself."""
    calls: list = []
    _stub_one_saved_candidate(monkeypatch, [])
    monkeypatch.setattr(R, "ingest_one", lambda *a, **k: calls.append(a[1]["id"]) or True)
    backlog = [{"id": 101, "url": "https://x/a", "verdict": "save"},
               {"id": 102, "url": "https://x/b", "verdict": "save"},
               {"id": 103, "url": "https://x/c", "verdict": "skip"}]  # a skip is never ingested
    monkeypatch.setattr(R.store, "pending",
                        lambda conn, status, limit=None: backlog if status == R.store.STATUS_JUDGED else [])
    monkeypatch.setattr(R, "AUTO_INGEST", True)
    monkeypatch.setattr(R, "MAX_INGEST_CATCHUP", 1)
    lines: list = []
    monkeypatch.setattr(R, "post_slack", lambda text: lines.append(text) or True)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 0
    assert calls == [7, 101], "the day's own save first, then the backlog within the cap"
    assert "1 judged save(s) still waiting to ingest" in lines[-1]

    monkeypatch.setattr(R, "AUTO_INGEST", False)
    calls.clear()
    R.run(R.parse_args([]), object(), "tok", "fc", "or")
    assert calls == [], "shadow mode never touches the backlog"


def test_notion_is_synced_once_per_run_after_ingesting(monkeypatch) -> None:
    """Per-post syncing cost ~40 s a post on the first live run (2026-09-02); the run
    ingests with sync=False and makes one Notion pass at the end — and none when
    nothing was ingested. A failed pass fails the run: the rows are stored, but
    Kevin's surface is behind."""
    syncs: list = []
    _stub_one_saved_candidate(monkeypatch, [])
    monkeypatch.setattr(R, "sync_ingested", lambda: syncs.append(1) or True)

    monkeypatch.setattr(R, "AUTO_INGEST", False)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 0
    assert syncs == [], "nothing ingested, nothing to sync"

    monkeypatch.setattr(R, "AUTO_INGEST", True)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 0
    assert syncs == [1], "one pass for the run, not one per post"

    monkeypatch.setattr(R, "sync_ingested", lambda: False)
    assert R.run(R.parse_args([]), object(), "tok", "fc", "or") == 1

    # --overrides-only ingests too, so it syncs too — once, and only if something landed
    syncs.clear()
    monkeypatch.setattr(R, "sync_ingested", lambda: syncs.append(1) or True)
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (2, 0, []))
    assert R.run(R.parse_args(["--overrides-only"]), object(), "tok", None, None) == 0
    assert syncs == [1]
    monkeypatch.setattr(R, "process_overrides", lambda *a, **k: (0, 0, []))
    assert R.run(R.parse_args(["--overrides-only"]), object(), "tok", None, None) == 0
    assert syncs == [1]


def test_save_for_intake_defers_the_sync(monkeypatch) -> None:
    import pipeline.save_item as SI
    seen: dict = {}
    monkeypatch.setattr(SI, "save_url", lambda url, show, skip_extract=False, sync=True: seen.update(sync=sync) or True)
    assert R.save_for_intake("https://x/a", None) is True
    assert seen == {"sync": False}


def test_main_closes_the_connection_even_when_the_run_raises(monkeypatch) -> None:
    conn = _closable()
    monkeypatch.setattr(R, "load_environment", lambda: None)
    monkeypatch.setattr(R, "require_secrets", lambda args: ("tok", "fc", "or"))
    monkeypatch.setattr(R, "get_db_connection", lambda: conn)
    monkeypatch.setattr(R, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        R.main([])
    assert conn.closed, "a Neon connection must not leak when the run blows up"


class _closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

"""The intake log's Notion side: additive schema changes, adoption, honest empty cells.

This database holds 45 rows and a year of Kevin's own marks, and the code that
reshapes it runs unattended on a Monday. So the destructive shapes are what these
pin: the schema plan never removes a property or a select option, the rename keeps
the historical ticks instead of stranding them, and a row that already exists is
adopted rather than duplicated.
"""

from __future__ import annotations

from pipeline.scrapers.intake import notion_log as N

# The live schema on 2026-09-02, before any of this ran: nine properties, five Status
# options, a "Pull" checkbox, and a title/description that describe the checkbox era.
LIVE_SCHEMA = {
    "title": [{"plain_text": "Blog Pull Queue"}],
    "description": [{"plain_text": "Candidate posts discovered from the mentions DB. Check Pull…"}],
    "properties": {
        "Name": {"type": "title"},
        "URL": {"type": "url"},
        "Source": {"type": "select", "select": {"options": [{"name": "openai.com"}]}},
        "Found Via": {"type": "rich_text"},
        "Last Cited": {"type": "date"},
        "Words": {"type": "number"},
        "Links Out": {"type": "number"},
        "Why": {"type": "rich_text"},
        "Pull": {"type": "checkbox"},
        "Status": {"type": "select", "select": {"options": [
            {"name": "candidate"}, {"name": "pulled"}, {"name": "pdf-report"},
            {"name": "failed"}, {"name": "skipped"},
        ]}},
    },
}


def _fake_notion(monkeypatch, responses):
    """Replace notion_request; record every call as (method, url, body)."""
    calls: list[tuple] = []

    def fake(method, url, token, body=None):
        calls.append((method, url, body))
        return responses.pop(0) if responses else {}

    monkeypatch.setattr(N, "notion_request", fake)
    return calls


# ── schema ──────────────────────────────────────────────────────────────────

def test_schema_plan_adds_what_is_missing_and_removes_nothing() -> None:
    body, changes = N.plan_schema_changes(LIVE_SCHEMA)
    props = body["properties"]
    for added in ("Published", "Verdict", "Confidence", "Reason", "Rule", "Job",
                  "Judge", "Disputed", "Precheck"):
        assert added in props, f"{added} should be added"
    # Everything the database already had is left out of the patch entirely — the
    # only way to be sure a type or an option Kevin set by hand is not clobbered.
    for kept in ("Name", "URL", "Source", "Found Via", "Last Cited", "Words",
                 "Links Out", "Why"):
        assert kept not in props
    assert not any(spec is None for spec in props.values())  # None is Notion's DELETE
    assert body["title"][0]["text"]["content"] == N.INTAKE_TITLE
    assert "description" in body  # the old prose told Kevin to tick boxes


def test_schema_plan_renames_pull_instead_of_adding_a_second_checkbox() -> None:
    body, changes = N.plan_schema_changes(LIVE_SCHEMA)
    # Two checkboxes side by side is a trap, and a rename keeps every historical tick.
    assert body["properties"]["Pull"] == {"name": N.OVERRIDE_PROP}
    assert N.OVERRIDE_PROP not in body["properties"]
    assert any("Pull → Pull anyway" in c for c in changes)


def test_schema_plan_merges_status_options_and_keeps_the_legacy_ones() -> None:
    body, _ = N.plan_schema_changes(LIVE_SCHEMA)
    names = [o["name"] for o in body["properties"]["Status"]["select"]["options"]]
    # removing an option would blank it on every historical row that carries it
    for legacy in ("candidate", "pulled", "pdf-report"):
        assert legacy in names
    for new in ("discovered", "judged", "saved", "held"):
        assert new in names


def test_schema_plan_is_empty_once_the_database_is_current() -> None:
    body, _ = N.plan_schema_changes(LIVE_SCHEMA)
    current = {
        "title": [{"plain_text": N.INTAKE_TITLE}],
        "description": [{"plain_text": N.INTAKE_DESCRIPTION}],
        "properties": {**LIVE_SCHEMA["properties"], **{
            name: {"type": next(iter(spec))} for name, spec in N.REQUIRED_PROPERTIES.items()},
            "Status": {"type": "select", "select": {"options": N.STATUS_OPTIONS}}},
    }
    del current["properties"]["Pull"]  # renamed on the first pass
    body2, changes2 = N.plan_schema_changes(current)
    assert (body2, changes2) == ({}, [])  # idempotent: the second run does nothing


def test_ensure_schema_dry_run_reports_without_patching(monkeypatch) -> None:
    calls = _fake_notion(monkeypatch, [LIVE_SCHEMA])
    changes = N.ensure_schema("tok", "db", dry_run=True)
    assert changes and [c[0] for c in calls] == ["GET"]  # nothing was written


# ── rows ────────────────────────────────────────────────────────────────────

def _row(**kw) -> dict:
    base = {
        "id": 1, "url": "https://openai.com/index/how-people-are-using-chatgpt",
        "title": "How people are using ChatGPT", "source": "openai-rss",
        "published_on": "2026-09-01", "words": 3200, "links_out": 14,
        "verdict": "save", "confidence": 0.82, "reason": "first-party usage figures",
        "rule": "S1", "job": "deck",
        "judge_model": "google/gemini-3.7-flash", "checker_model": "openai/gpt-5.6-luna",
        "disputed": True, "status": "judged", "precheck": None, "failed_reason": None,
        "discovered_via": {"feed": "https://openai.com/news/rss.xml"},
        "notion_page_id": None,
    }
    base.update(kw)
    return base


def test_build_properties_carries_the_verdict_and_both_models() -> None:
    props = N.build_properties(_row())
    assert props["Verdict"]["select"]["name"] == "save"
    assert props["Confidence"]["number"] == 0.82
    assert props["Disputed"]["checkbox"] is True
    # "which model said this" is provenance the row has to carry on its face
    assert props["Judge"]["rich_text"][0]["text"]["content"] == \
        "google/gemini-3.7-flash | openai/gpt-5.6-luna"
    assert props["Reason"]["rich_text"][0]["text"]["content"] == "first-party usage figures"
    # which rule fired and what the save is FOR — groupable in a way a reason isn't
    assert props["Rule"]["select"]["name"] == "S1"
    assert props["Job"]["select"]["name"] == "deck"


def test_build_properties_leaves_unknown_cells_empty_rather_than_zero() -> None:
    props = N.build_properties(_row(words=None, links_out=None, verdict=None,
                                    confidence=None, reason=None, judge_model=None,
                                    rule=None, job=None, title="", status="discovered"))
    for absent in ("Words", "Links Out", "Verdict", "Confidence", "Reason", "Judge",
                   "Rule", "Job"):
        assert absent not in props
    assert props["Name"]["title"][0]["text"]["content"].startswith("https://")  # url as the label
    assert props["Status"]["select"]["name"] == "discovered"


def test_build_properties_spells_out_why_a_pre_check_fired() -> None:
    thin = N.build_properties(_row(precheck="thin", words=117, verdict=None, status="skipped"))
    assert thin["Precheck"]["rich_text"][0]["text"]["content"] == "thin (117 words)"
    dead = N.build_properties(_row(precheck="dead", words=None, failed_reason="404",
                                   verdict=None, status="skipped"))
    assert dead["Precheck"]["rich_text"][0]["text"]["content"] == "dead (404)"


def test_found_via_reads_as_provenance_for_a_cited_post() -> None:
    text = N.found_via_text({"discovered_via": {
        "shows": ["The AI Daily Brief"], "cited_in_episodes": 2,
        "cited_as": "Ramp AI Index", "also_sources": ["openai-rss"]}})
    assert "The AI Daily Brief — 2 episode(s)" in text
    assert "Ramp AI Index" in text and "also seen via openai-rss" in text


def test_build_properties_truncates_instead_of_failing_on_long_text() -> None:
    props = N.build_properties(_row(title="t" * 2500, reason="r" * 2500))
    assert len(props["Name"]["title"][0]["text"]["content"]) == 2000
    assert len(props["Reason"]["rich_text"][0]["text"]["content"]) == 1900


# ── adoption: never create a second page for a URL already in the log ───────

def test_upsert_adopts_a_legacy_page_by_url(monkeypatch) -> None:
    calls = _fake_notion(monkeypatch, [{}])
    row = _row(notion_page_id=None)
    page_id = N.upsert_row("tok", "db", row, {row["url"]: "legacy-page"})
    assert page_id == "legacy-page"
    assert calls[0][0] == "PATCH" and calls[0][1].endswith("/pages/legacy-page")


def test_upsert_prefers_the_id_neon_already_stored(monkeypatch) -> None:
    calls = _fake_notion(monkeypatch, [{}])
    assert N.upsert_row("tok", "db", _row(notion_page_id="stored"), {}) == "stored"
    assert calls[0][1].endswith("/pages/stored")


def test_upsert_creates_only_when_the_url_is_genuinely_new(monkeypatch) -> None:
    calls = _fake_notion(monkeypatch, [{"id": "new-page"}])
    assert N.upsert_row("tok", "db", _row(), {}) == "new-page"
    assert calls[0][0] == "POST" and calls[0][2]["parent"] == {"database_id": "db"}


def test_existing_page_ids_builds_the_adoption_map(monkeypatch) -> None:
    calls = _fake_notion(monkeypatch, [
        {"results": [{"id": "p1", "properties": {"URL": {"url": "https://a"}}},
                     {"id": "dup", "properties": {"URL": {"url": "https://a"}}}],
         "has_more": True, "next_cursor": "c2"},
        {"results": [{"id": "p2", "properties": {"URL": {"url": "https://b"}}}],
         "has_more": False},
    ])
    assert N.existing_page_ids("tok", "db") == {"https://a": "p1", "https://b": "p2"}
    assert calls[1][2]["start_cursor"] == "c2"  # one read, however many pages


# ── the override door ───────────────────────────────────────────────────────

def test_override_rows_filter_on_the_tick_and_exclude_only_saved(monkeypatch) -> None:
    pages = [
        {"results": [{"id": "p1", "properties": {"URL": {"url": "https://a"}}}],
         "has_more": True, "next_cursor": "c2"},
        {"results": [{"id": "p2", "properties": {"URL": {"url": "https://b"}}}],
         "has_more": False},
    ]
    calls = _fake_notion(monkeypatch, list(pages))
    rows = N.override_rows("tok", "db")
    assert rows == [{"page_id": "p1", "url": "https://a"},
                    {"page_id": "p2", "url": "https://b"}]
    where = calls[0][2]["filter"]["and"]
    assert {"property": N.OVERRIDE_PROP, "checkbox": {"equals": True}} in where
    # a tick on a skipped, held or failed row all mean "I want it" — only saved is done
    assert {"property": "Status", "select": {"does_not_equal": "saved"}} in where
    assert calls[1][2]["start_cursor"] == "c2"


def test_override_rows_ignore_a_row_with_no_url(monkeypatch) -> None:
    _fake_notion(monkeypatch, [{"results": [{"id": "p1", "properties": {}}], "has_more": False}])
    assert N.override_rows("tok", "db") == []

from datetime import date, datetime, timedelta

from pipeline.sync_notion import (
    alert_on_failure_rate,
    build_notion_properties,
    compute_diff,
    mark_sync_failed,
    save_notion_page_id,
)


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple = ()

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.sql = sql
        self.params = params


class _Conn:
    def __init__(self) -> None:
        self.cur = _Cursor()
        self.committed = False

    def cursor(self) -> _Cursor:
        return self.cur

    def commit(self) -> None:
        self.committed = True


def entity(**overrides):
    base = {
        "entity_id": 1,
        "canonical_name": "Example Tool",
        "entity_type": "software_product",
        "mention_count": 3,
        "episode_count": 2,
        "first_date": date(2026, 1, 1),
        "last_date": date(2026, 1, 5),
        "latest_context": "A concise context snippet.",
        "primary_url": "https://example.com",
        "notion_page_id": "page-1",
        "updated_at": datetime(2026, 1, 5, 12, 0, 0),
        "notion_synced_at": datetime(2026, 1, 6, 12, 0, 0),
    }
    base.update(overrides)
    return base


def test_build_notion_properties_maps_core_fields() -> None:
    props = build_notion_properties(entity())

    assert props["Name"]["title"][0]["text"]["content"] == "Example Tool"
    assert props["Type"]["select"]["name"] == "software_product"
    assert props["Mentions"]["number"] == 3
    assert props["Items"]["number"] == 2
    assert props["First Mentioned"]["date"]["start"] == "2026-01-01"
    assert props["Last Mentioned"]["date"]["start"] == "2026-01-05"
    assert props["URL"]["url"] == "https://example.com"


def test_build_notion_properties_truncates_long_text_fields() -> None:
    long_name = "n" * 2100
    long_context = "c" * 2100
    long_url = "https://example.com/" + ("u" * 2100)

    props = build_notion_properties(
        entity(canonical_name=long_name, latest_context=long_context, primary_url=long_url)
    )

    assert len(props["Name"]["title"][0]["text"]["content"]) == 2000
    assert len(props["Context"]["rich_text"][0]["text"]["content"]) == 2000
    assert len(props["URL"]["url"]) == 2000


def test_build_notion_properties_adds_sources_multiselect() -> None:
    # Option A: shared DB → a "Sources" tag listing which sources mention the entity
    # (podcasts, blogs, research runs — renamed from "Shows" in the 2026-06-11 UX pass).
    props = build_notion_properties(entity(show_names=["The AI Daily Brief", "OpenAI Blog"]))
    assert props["Sources"] == {
        "multi_select": [{"name": "The AI Daily Brief"}, {"name": "OpenAI Blog"}]
    }


def test_build_notion_properties_omits_sources_when_absent() -> None:
    assert "Sources" not in build_notion_properties(entity())  # no empty tag


def test_compute_diff_creates_missing_pages() -> None:
    to_create, to_update = compute_diff([entity(notion_page_id=None)])

    assert len(to_create) == 1
    assert to_update == []


def test_compute_diff_updates_when_never_synced() -> None:
    to_create, to_update = compute_diff([entity(notion_synced_at=None)])

    assert to_create == []
    assert len(to_update) == 1


def test_compute_diff_updates_when_entity_changed_after_sync() -> None:
    synced = datetime(2026, 1, 5, 12, 0, 0)
    changed = synced + timedelta(seconds=1)

    to_create, to_update = compute_diff(
        [entity(updated_at=changed, notion_synced_at=synced)]
    )

    assert to_create == []
    assert len(to_update) == 1


def test_compute_diff_updates_when_latest_mention_is_after_sync_date() -> None:
    to_create, to_update = compute_diff(
        [
            entity(
                last_date=date(2026, 1, 7),
                notion_synced_at=datetime(2026, 1, 6, 23, 59, 0),
            )
        ]
    )

    assert to_create == []
    assert len(to_update) == 1


def test_compute_diff_leaves_current_pages_alone() -> None:
    to_create, to_update = compute_diff([entity()])

    assert to_create == []
    assert to_update == []


def test_save_notion_page_id_marks_synced() -> None:
    conn = _Conn()
    save_notion_page_id(conn, 7, "page-x")
    assert "notion_sync_status = 'synced'" in conn.cur.sql
    assert conn.cur.params == ("page-x", 7)
    assert conn.committed is True


def test_mark_sync_failed_records_status_and_truncates_error() -> None:
    conn = _Conn()
    mark_sync_failed(conn, 7, "boom" * 200)  # 800 chars
    assert "notion_sync_status = 'failed'" in conn.cur.sql
    assert conn.cur.params[0] == ("boom" * 200)[:500]
    assert conn.cur.params[1] == 7
    assert conn.committed is True


def test_alert_on_failure_rate_alerts_above_threshold(monkeypatch) -> None:
    import pipeline.sync_notion as sn

    posted: list = []
    monkeypatch.setattr(sn, "post_slack", lambda text: posted.append(text))

    sn.alert_on_failure_rate("incremental create", succeeded=8, failed=2)  # 20%
    assert posted, "should alert above 10%"

    posted.clear()
    sn.alert_on_failure_rate("incremental create", succeeded=99, failed=1)  # 1%
    assert not posted, "should not alert at/below 10%"

    posted.clear()
    sn.alert_on_failure_rate("incremental create", succeeded=9, failed=1)  # exactly 10%
    assert not posted, "10% is not strictly > 10%"


def test_alert_on_failure_rate_silent_when_no_failures(monkeypatch) -> None:
    import pipeline.sync_notion as sn

    posted: list = []
    monkeypatch.setattr(sn, "post_slack", lambda text: posted.append(text))
    sn.alert_on_failure_rate("incremental create", succeeded=10, failed=0)
    assert not posted


def test_fetch_entity_rollup_curated_qualifier() -> None:
    """The HAVING must qualify entities by group threshold OR any curated-show
    mention — and an empty curated list must degrade to pure old behavior."""
    from pipeline.sync_notion import fetch_entity_rollup

    class _Cursor:
        sql = ""
        params = None

        def execute(self, sql, params=None):
            _Cursor.sql, _Cursor.params = sql, params

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

    from pipeline.sync_notion import MAX_AD_MENTIONS_COUNTED as CAP

    fetch_entity_rollup(_Conn(), [3, 48, 62], {}, 2, [62])
    # The threshold is tested against the CAPPED count, not the raw one: an entity must
    # not qualify on 40 ad mentions that the rollup will then only count 5 of.
    assert "LEAST(COUNT(*) FILTER (WHERE m.sponsor_source IS NOT NULL), %s) >= %s" in _Cursor.sql
    assert "FILTER (WHERE ep.show_id = ANY(%s)) >= 1" in _Cursor.sql
    assert _Cursor.params == (CAP, [3, 48, 62], CAP, 2, [62])

    fetch_entity_rollup(_Conn(), [3, 48], {}, 2)
    assert _Cursor.params == (CAP, [3, 48], CAP, 2, [])


# --- ads as data: the weight cap and the sponsor properties -----------------------


def test_ad_mentions_are_capped_in_the_rollup_sql() -> None:
    """Editorial counts in full; ads count at most MAX_AD_MENTIONS_COUNTED.

    73 of Blitzy's 77 mentions are ad reads (retag_sponsor_mentions.py --dry-run, 2026-09-02). Uncapped it outranks
    every tool the hosts actually discussed, which is exactly what Kevin asked to stop.
    """
    from pipeline.sync_notion import MAX_AD_MENTIONS_COUNTED, fetch_entity_rollup

    class _Cursor:
        sql = ""
        params = None

        def execute(self, sql, params=None):
            _Cursor.sql, _Cursor.params = sql, params

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

    fetch_entity_rollup(_Conn(), [3, 48], {}, 2)
    sql = " ".join(_Cursor.sql.split())
    assert (
        "COUNT(*) FILTER (WHERE m.sponsor_source IS NULL) "
        "+ LEAST(COUNT(*) FILTER (WHERE m.sponsor_source IS NOT NULL), %s) AS mention_count"
    ) in sql
    assert "COUNT(*) FILTER (WHERE m.sponsor_source IS NOT NULL) AS ad_mention_count" in sql
    assert "COUNT(*) FILTER (WHERE m.sponsor_source IS NULL) AS editorial_mention_count" in sql
    assert MAX_AD_MENTIONS_COUNTED == 5


def test_rollup_prefers_an_editorial_snippet_for_the_visible_context() -> None:
    """Showing ad copy as the entity's Context makes a sponsor read look like Kevin's
    own note about the product."""
    from pipeline.sync_notion import fetch_entity_rollup

    class _Cursor:
        sql = ""

        def execute(self, sql, params=None):
            _Cursor.sql = sql

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

    fetch_entity_rollup(_Conn(), [3], {}, 2)
    sql = " ".join(_Cursor.sql.split())
    assert "ORDER BY (m.sponsor_source IS NOT NULL), ep.publish_date DESC NULLS LAST" in sql


def test_sponsor_properties_are_built_from_the_uncapped_ad_count() -> None:
    props = build_notion_properties(entity(ad_mention_count=76, editorial_mention_count=1,
                                           mention_count=6))
    assert props["Sponsor"]["checkbox"] is True
    # Uncapped, so a reader can see how much of Mentions was withheld.
    assert props["Ad mentions"]["number"] == 76
    assert props["Mentions"]["number"] == 6


def test_an_entity_with_no_ads_is_not_flagged_a_sponsor() -> None:
    props = build_notion_properties(entity())
    assert props["Sponsor"]["checkbox"] is False
    assert props["Ad mentions"]["number"] == 0
    assert "First seen as ad" not in props


def test_first_seen_as_ad_reaches_notion_when_recorded() -> None:
    props = build_notion_properties(
        entity(ad_mention_count=3, attributes={"first_seen_as_ad": "2026-07-06"})
    )
    assert props["First seen as ad"]["date"]["start"] == "2026-07-06"


def test_ensure_database_properties_adds_only_what_is_missing(monkeypatch) -> None:
    """Additive only: a hand-configured property of the same name is left alone, and a
    second run adds nothing."""
    import pipeline.sync_notion as sn

    calls = []

    def fake_request(method, url, token, body=None):
        calls.append((method, url, body))
        if method == "GET":
            return {"properties": {"Name": {}, "Mentions": {}, "Sponsor": {}}}
        return {}

    monkeypatch.setattr(sn, "notion_request", fake_request)
    added = sn.ensure_database_properties("tok", "db-1")

    assert added == ["Ad mentions", "First seen as ad"]
    patch = [c for c in calls if c[0] == "PATCH"][0]
    assert set(patch[2]["properties"]) == {"Ad mentions", "First seen as ad"}
    assert "Sponsor" not in patch[2]["properties"]  # already there, untouched


def test_ensure_database_properties_is_idempotent(monkeypatch) -> None:
    import pipeline.sync_notion as sn

    calls = []

    def fake_request(method, url, token, body=None):
        calls.append(method)
        return {"properties": dict.fromkeys(sn.REQUIRED_DATABASE_PROPERTIES, {})}

    monkeypatch.setattr(sn, "notion_request", fake_request)
    assert sn.ensure_database_properties("tok", "db-1") == []
    assert "PATCH" not in calls


def test_ensure_database_properties_writes_nothing_in_dry_run(monkeypatch) -> None:
    import pipeline.sync_notion as sn

    calls = []

    def fake_request(method, url, token, body=None):
        calls.append(method)
        return {"properties": {}}

    monkeypatch.setattr(sn, "notion_request", fake_request)
    added = sn.ensure_database_properties("tok", "db-1", dry_run=True)
    assert added == sorted(sn.REQUIRED_DATABASE_PROPERTIES)
    assert calls == ["GET"]

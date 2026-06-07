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
    assert props["Episodes"]["number"] == 2
    assert props["First Mentioned"]["date"]["start"] == "2026-01-01"
    assert props["Last Mentioned"]["date"]["start"] == "2026-01-05"
    assert props["userDefined:URL"]["url"] == "https://example.com"


def test_build_notion_properties_truncates_long_text_fields() -> None:
    long_name = "n" * 2100
    long_context = "c" * 2100
    long_url = "https://example.com/" + ("u" * 2100)

    props = build_notion_properties(
        entity(canonical_name=long_name, latest_context=long_context, primary_url=long_url)
    )

    assert len(props["Name"]["title"][0]["text"]["content"]) == 2000
    assert len(props["Context"]["rich_text"][0]["text"]["content"]) == 2000
    assert len(props["userDefined:URL"]["url"]) == 2000


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

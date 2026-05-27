from datetime import date, datetime, timedelta

from pipeline.sync_notion import build_notion_properties, compute_diff


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

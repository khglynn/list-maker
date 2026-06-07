from pipeline.show_config import SHOWS
from pipeline.scrapers.taddy.import_transcripts import SHOWS as TADDY_SHOWS


def test_taddy_show_configs_stay_in_sync() -> None:
    configured_taddy_slugs = {
        slug for slug, cfg in SHOWS.items() if cfg.taddy_uuid is not None
    }

    assert configured_taddy_slugs == set(TADDY_SHOWS)
    # Single source of truth: the importer derives its registry from show_config,
    # so the uuids are guaranteed identical (no separate hardcoded list to drift).
    for slug in configured_taddy_slugs:
        assert SHOWS[slug].taddy_uuid == TADDY_SHOWS[slug].taddy_uuid


def test_show_ids_are_unique_positive_integers() -> None:
    show_ids = [cfg.show_id for cfg in SHOWS.values()]

    assert all(isinstance(show_id, int) and show_id > 0 for show_id in show_ids)
    assert len(show_ids) == len(set(show_ids))


def test_only_configured_notion_shows_have_database_ids() -> None:
    notion_slugs = {slug for slug, cfg in SHOWS.items() if cfg.notion_database_id}

    assert notion_slugs == {"ai-daily-brief", "hard-fork"}

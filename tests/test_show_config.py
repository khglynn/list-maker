from pipeline.show_config import SHOWS, curated_show_slugs
from pipeline.scrapers.taddy.import_transcripts import SHOWS as TADDY_SHOWS

TECH_DB = "982dafa0ad374d618e25207e67860e33"
MEDIA_DB = "3780501ef95081a783ebf8a32fa94657"


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
    by_db: dict[str, set[str]] = {}
    for slug, cfg in SHOWS.items():
        if cfg.notion_database_id:
            by_db.setdefault(cfg.notion_database_id, set()).add(slug)

    # Group membership is load-bearing: sync_notion rolls up entities ACROSS every
    # show sharing a DB id, so an accidental addition here changes shared counts.
    assert by_db == {
        TECH_DB: {
            "ai-daily-brief", "hard-fork",
            "openai-blog", "anthropic-blog", "saved-articles", "agentic-research",
        },
        MEDIA_DB: {"pchh", "culture-gabfest"},
    }


def test_curated_shows_have_no_scheduled_import_path() -> None:
    """Curated sources must never enter the scheduled orchestrator's import:
    no Taddy uuid, no importer. (Ingestion is save_item/the pull queue only.)"""
    for slug in curated_show_slugs():
        cfg = SHOWS[slug]
        assert cfg.taddy_uuid is None, f"{slug}: curated shows must not have a taddy_uuid"
        assert cfg.importer is None, f"{slug}: curated shows must not have a scheduled importer"


def test_curated_set_matches_expected() -> None:
    assert curated_show_slugs() == {
        "openai-blog", "anthropic-blog", "saved-articles", "agentic-research"
    }


def test_scheduled_non_taddy_importers_are_known() -> None:
    """step_import routes on cfg.importer — an unknown value would silently no-op."""
    known = {None, "gabfest_rss"}
    for slug, cfg in SHOWS.items():
        assert cfg.importer in known, f"{slug}: unknown importer {cfg.importer!r}"

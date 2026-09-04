from datetime import date

from pipeline.show_config import SHOWS, curated_show_slugs, ended_show_slugs
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
        "openai-blog", "anthropic-blog", "saved-articles", "agentic-research",
        "saved-episodes",
    }


def test_ended_shows_are_dated_and_expected() -> None:
    """An ended show still imports (a revival would be picked up) — it only stops
    counting as 'stale'. Keep the set explicit so retiring a show is a deliberate act."""
    assert ended_show_slugs() == {"culture-gabfest"}
    assert SHOWS["culture-gabfest"].ended_on == date(2026, 7, 1)


def test_ended_on_is_a_date_not_a_string() -> None:
    """A stringly-typed date would compare wrong and format wrong in the health report."""
    for slug, cfg in SHOWS.items():
        if cfg.ended_on is not None:
            assert isinstance(cfg.ended_on, date), f"{slug}: ended_on must be a datetime.date"


def test_scheduled_non_taddy_importers_are_known() -> None:
    """step_import routes on cfg.importer — an unknown value would silently no-op."""
    known = {None, "gabfest_rss"}
    for slug, cfg in SHOWS.items():
        assert cfg.importer in known, f"{slug}: unknown importer {cfg.importer!r}"


def test_feed_grace_matches_each_show_import_cadence() -> None:
    """The feed check's tolerance must cover a show's real publish→import gap, or the
    daily alarm fires on every fresh episode. That was August 2026: SOP publishes
    Tuesdays and imports Wed/Fri, so "1 show behind" hit Slack most days for nothing."""
    for slug, cfg in SHOWS.items():
        if cfg.medium != "podcast":
            continue  # curated sources have no feed; the check skips them entirely
        assert isinstance(cfg.feed_grace_days, int) and cfg.feed_grace_days >= 1, slug
    assert SHOWS["sop"].feed_grace_days >= 4  # Tue publish; Wed + Fri imports both get a turn
    assert SHOWS["tal"].feed_grace_days >= 2  # Mon publish; Mon import at the same minute as the check
    for slug in ("ai-daily-brief", "hard-fork", "pchh"):
        # Daily-imported shows: a grace longer than this would hide a real multi-day gap.
        assert SHOWS[slug].feed_grace_days <= 3, slug


def test_episode_identity_names_the_writer_of_each_show_url() -> None:
    """episode_identity says which identity a show's episodes.url carries, and the feed
    check compares ids only where it is set. Getting it wrong is not a soft failure: a
    show wrongly marked "taddy_uuid" reports its whole feed missing every day.

    Pinned against what actually writes each row (verified against live Neon 2026-09-03):
      - the four Taddy-imported shows, plus TAL, whose discovery step runs that same
        importer (run_pipeline.discover_tal_episodes)
      - culture-gabfest, written by import_gabfest with the Megaphone <guid>
      - SOP: NOT identity-comparable. scrapers/sop/scrape.py writes
        switchedonpop.com/episodes/... urls; only 2 of its 716 rows carry a Taddy url,
        and 13 of the 15 episodes in its Taddy feed match nothing we hold.
    """
    identity_by_slug = {slug: cfg.episode_identity for slug, cfg in SHOWS.items()}

    assert identity_by_slug == {
        "sop": None,
        "tal": "taddy_uuid",
        "ai-daily-brief": "taddy_uuid",
        "pchh": "taddy_uuid",
        "hard-fork": "taddy_uuid",
        "culture-gabfest": "rss_guid",
        "openai-blog": None,
        "anthropic-blog": None,
        "saved-articles": None,
        "agentic-research": None,
        "saved-episodes": None,
    }


def test_episode_identity_values_are_backed_by_a_real_source() -> None:
    """Each declared scheme needs the config the reader actually uses, or
    feed_recent_episodes silently returns None and the show goes unverified forever."""
    for slug, cfg in SHOWS.items():
        if cfg.episode_identity == "taddy_uuid":
            assert cfg.taddy_uuid, f"{slug}: declares taddy identity with no taddy_uuid"
        elif cfg.episode_identity == "rss_guid":
            assert "megaphone" in (cfg.fallback_website_url or ""), f"{slug}: no RSS feed url"
        else:
            assert cfg.episode_identity is None, f"{slug}: unknown scheme {cfg.episode_identity!r}"


def test_curated_sources_have_no_episode_identity() -> None:
    """Curated sources have no feed at all, so there is nothing to compare ids against —
    the feed check skips them before either reader is asked."""
    for slug in curated_show_slugs():
        assert SHOWS[slug].episode_identity is None, f"{slug}: curated shows have no feed"

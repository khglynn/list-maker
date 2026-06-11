"""Centralized show configuration for the list-maker pipeline.

Single source of truth for every show. Other modules (the Taddy importer, the
orchestrator, the Notion/Spotify sync) import from here — nothing about a show is
duplicated elsewhere. tests/test_show_config.py guards against drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ShowConfig:
    slug: str
    name: str
    show_id: int
    content_types: list[str]  # "music", "entities", "mixed"

    # What kind of source this is. Podcasts flow through the scheduled orchestrator;
    # "blog" and "research" sources are CURATED — ingested via save_item/the pull queue,
    # never the daily import — so data_health skips their feed/staleness checks.
    medium: str = "podcast"  # "podcast" | "blog" | "research"

    # Scheduled importer for non-Taddy shows ("gabfest_rss"). None + no taddy_uuid =
    # nothing to import on a scheduled run (curated sources). Taddy shows are keyed
    # off taddy_uuid itself — the uuid IS the importer config.
    importer: Optional[str] = None

    # Taddy
    taddy_uuid: Optional[str] = None
    fallback_website_url: Optional[str] = None
    store_raw_content: bool = False  # persist raw Taddy episode JSON (entity shows)

    # Spotify
    spotify_playlist_id: Optional[str] = None
    spotify_playlist_name: Optional[str] = None

    # Notion
    notion_database_id: Optional[str] = None
    notion_min_mentions: int = 2  # min mentions to surface in Notion (media shows use 1)

    # Extraction
    extraction_type: Optional[str] = None  # "entity_extraction", "song_extraction"


SHOWS: dict[str, ShowConfig] = {
    "sop": ShowConfig(
        slug="sop",
        name="Switched On Pop",
        show_id=1,
        content_types=["music"],
        taddy_uuid="97ed51a4-460e-4dc8-8db5-30df96ad59bc",
        fallback_website_url="https://switchedonpop.com",
        spotify_playlist_id="0cEVeX4pdHf5RJOiTRzgxX",
        spotify_playlist_name="Switched On Pop - All Songs Ever Discussed",
        extraction_type="song_extraction",
    ),
    "tal": ShowConfig(
        slug="tal",
        name="This American Life",
        show_id=2,
        content_types=["music"],
        taddy_uuid="d682a935-ad2d-46ee-a0ac-139198b83bcc",
        fallback_website_url="https://www.thisamericanlife.org/podcast/rss.xml",
        spotify_playlist_id="3d7fjfrTTKvrl7VHv5JzIz",
        spotify_playlist_name="This American Life: Full Music Archive",
        extraction_type="song_extraction",
    ),
    "ai-daily-brief": ShowConfig(
        slug="ai-daily-brief",
        name="The AI Daily Brief",
        show_id=3,
        content_types=["entities"],
        taddy_uuid="60fabbea-f51e-4c8b-82b4-1cbd57fe8c02",
        fallback_website_url="https://www.aidailybrief.ai/",
        store_raw_content=True,
        notion_database_id="982dafa0ad374d618e25207e67860e33",
        extraction_type="entity_extraction",
    ),
    "pchh": ShowConfig(
        slug="pchh",
        name="Pop Culture Happy Hour",
        show_id=11,
        content_types=["mixed"],
        taddy_uuid="81b2a312-6976-4d22-bc54-4e3991fee332",
        fallback_website_url="https://www.npr.org/podcasts/510282/pop-culture-happy-hour",
        store_raw_content=True,
        notion_database_id="3780501ef95081a783ebf8a32fa94657",  # shared Media DB (Option A)
        notion_min_mentions=1,  # media: a single recommendation is meaningful
        extraction_type="media_extraction",
    ),
    "hard-fork": ShowConfig(
        slug="hard-fork",
        name="Hard Fork",
        show_id=48,
        content_types=["entities"],
        taddy_uuid="ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0",
        fallback_website_url="https://www.nytimes.com/column/hard-fork",
        store_raw_content=True,
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB (Option A) — same as AI Daily
        extraction_type="entity_extraction",
    ),
    "culture-gabfest": ShowConfig(
        slug="culture-gabfest",
        name="Culture Gabfest",
        show_id=54,
        content_types=["media"],
        importer="gabfest_rss",
        taddy_uuid=None,  # Taddy won't transcribe Gabfest (iHeart rights) — Megaphone RSS show-notes instead
        fallback_website_url="https://feeds.megaphone.fm/slatesculturegabfest",
        store_raw_content=True,
        notion_database_id="3780501ef95081a783ebf8a32fa94657",  # shared Media DB (Option A)
        notion_min_mentions=1,  # media: a single recommendation is meaningful
        extraction_type="media_extraction",
    ),
    # ── Curated sources (medium != "podcast") ───────────────────────────────
    # Ingested item-by-item via save_item.py / the Blog Pull Queue — NOT by the
    # scheduled orchestrator. Pull signal: outbound-link density (posts citing many
    # resources improve the mentions DB most). Their entities surface in Notion at
    # 1 mention via fetch_entity_rollup's curated-show qualifier — notion_min_mentions
    # here only gates podcast noise, so the default 2 is correct.
    "openai-blog": ShowConfig(
        slug="openai-blog",
        name="OpenAI Blog",
        show_id=60,
        content_types=["entities"],
        medium="blog",
        fallback_website_url="https://openai.com/news/",
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB (Option A)
        extraction_type="entity_extraction",
    ),
    "anthropic-blog": ShowConfig(
        slug="anthropic-blog",
        name="Anthropic Blog",
        show_id=61,
        content_types=["entities"],
        medium="blog",
        fallback_website_url="https://www.anthropic.com/news",
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB (Option A)
        extraction_type="entity_extraction",
    ),
    "saved-articles": ShowConfig(
        slug="saved-articles",
        name="Saved Articles",
        show_id=62,
        content_types=["entities"],
        medium="blog",  # catch-all for one-off publications (e.g. MIT Tech Review)
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB (Option A)
        extraction_type="entity_extraction",
    ),
    "agentic-research": ShowConfig(
        slug="agentic-research",
        name="Agentic Research",
        show_id=63,
        content_types=["entities"],
        medium="research",  # local Obsidian research-run docs; ingested locally only
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB (Option A)
        extraction_type="entity_extraction",
    ),
    "saved-episodes": ShowConfig(
        slug="saved-episodes",
        name="Saved Episodes",
        show_id=64,
        content_types=["entities"],
        medium="episode",  # one-off podcast episodes from shows we don't carry (clips, note links)
        # No extraction + no entity DB on purpose: these span Science Vs / Pivot /
        # Las Culturistas etc. — tech-profile mentions from them would pollute the
        # shared Tech DB. The value is the highlight page, not the mentions.
        extraction_type=None,
    ),
}


# Shows whose full transcripts mirror into the Notion "Transcripts" DB
# (sync_transcripts_notion.py, daily via entities.yml). Single source for the
# sync default AND data_health's notion-freshness check — keep them in lockstep.
TRANSCRIPT_NOTION_SHOWS: tuple[str, ...] = ("ai-daily-brief", "hard-fork", "saved-episodes")

# Shows whose full texts mirror into the Notion "Blog Posts" DB (same engine,
# --target blog-posts). agentic-research is deliberately absent: those docs'
# canonical home is the Obsidian vault — only their MENTIONS go to Notion.
BLOG_NOTION_SHOWS: tuple[str, ...] = ("openai-blog", "anthropic-blog", "saved-articles")


def get_show(slug: str) -> ShowConfig:
    """Get show config by slug. Raises KeyError if not found."""
    if slug not in SHOWS:
        raise KeyError(f"Unknown show slug: {slug}. Known: {sorted(SHOWS.keys())}")
    return SHOWS[slug]


def shows_with_notion() -> list[ShowConfig]:
    """Return shows that have a Notion database configured."""
    return [s for s in SHOWS.values() if s.notion_database_id]


def shows_with_spotify() -> list[ShowConfig]:
    """Return shows that have a Spotify playlist configured."""
    return [s for s in SHOWS.values() if s.spotify_playlist_id]


def curated_show_slugs() -> set[str]:
    """Slugs of curated (non-podcast) sources — blog/research shows with no feed and
    no publishing cadence. data_health skips staleness + feed checks for these; a
    'stale' curated show just means Kevin hasn't pulled anything lately, by design."""
    return {slug for slug, cfg in SHOWS.items() if cfg.medium != "podcast"}

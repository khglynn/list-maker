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

    # Taddy
    taddy_uuid: Optional[str] = None
    fallback_website_url: Optional[str] = None
    store_raw_content: bool = False  # persist raw Taddy episode JSON (entity shows)

    # Spotify
    spotify_playlist_id: Optional[str] = None
    spotify_playlist_name: Optional[str] = None

    # Notion
    notion_database_id: Optional[str] = None

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
        taddy_uuid=None,  # Taddy won't transcribe Gabfest (iHeart rights) — Megaphone RSS show-notes instead
        fallback_website_url="https://feeds.megaphone.fm/slatesculturegabfest",
        store_raw_content=True,
        notion_database_id="3780501ef95081a783ebf8a32fa94657",  # shared Media DB (Option A)
        extraction_type="media_extraction",
    ),
}


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

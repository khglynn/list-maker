"""Centralized show configuration for the list-maker pipeline.

Single source of truth for every show. Other modules (the Taddy importer, the
orchestrator, the Notion/Spotify sync) import from here — nothing about a show is
duplicated elsewhere. tests/test_show_config.py guards against drift.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
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

    # The scheduled importer: "rss" (scrapers/rss/import_feed.py, every show's own feed)
    # or "gabfest_rss" (the Culture Gabfest importer, kept because it is show-notes only
    # and the show has ended). None = nothing to import on a scheduled run: curated
    # sources, SOP (its website scraper runs in pipeline.yml) and shows no longer collected.
    importer: Optional[str] = None

    # The day we stopped collecting a show that still publishes (Hard Fork, 2026-12: Kevin
    # chose Machine Gods, its hosts' new show, over it). Like ended_on, its staleness check
    # stops; unlike ended_on, so does its feed check, because the feed keeps moving and
    # nothing will ever import from it again. Its rows stay.
    collected_until: Optional[date] = None

    # A new show's first scheduled run imports nothing published before this day. Without
    # it the 30-day window would import a month of back catalogue on day one, which is a
    # backfill, and a backfill is a decision (scrapers/rss/import_feed.py --backfill).
    import_since: Optional[date] = None

    # Date of the show's final episode, for shows that have concluded. A finished show
    # can never be "fresh" again, so data_health skips its STALENESS check — otherwise
    # it fails every single run forever, which is how a Slack alert becomes noise you
    # learn to ignore. The feed check deliberately keeps running: it costs nothing extra
    # and is how we'd find out if the show ever came back.
    ended_on: Optional[date] = None

    # How long a feed episode may be absent from our DB before that is a real gap.
    # The feed check (data_health.check_import_caught_up, also the pulse) asks each
    # show's real feed for its latest episodes and compares to what we hold. Without a
    # grace window it fires the moment an episode publishes — hours or days before the
    # import that would normally fetch it. The music shows import only on Mon/Wed/Fri
    # (the Worker's dispatchesFor), so through August 2026 the channel got a "1 show
    # behind" alarm nearly every day while nothing was wrong — exactly the noise that
    # trains a reader to ignore the one real alert.
    # THE RULE: the longest normal wait between publish and the next scheduled import
    # that would catch it, plus a day (Taddy's transcript lag for the daily shows,
    # measured 2026-09-01 at p90 = 1 day; the website lagging the feed for SOP). Size it
    # from the days the show ACTUALLY publishes, measured in Neon — not from what it
    # used to do (SOP's 4 was sized for a Tuesday-only show and false-alarmed 09-22).
    # tests/test_show_config.py derives each music show's window from this rule. A
    # missing episode OLDER than this still fails loudly — TAL's 10-week gap in 2026-07
    # would have tripped it on day three.
    feed_grace_days: int = 2

    # WHICH IDENTITY THIS SHOW'S `episodes.url` CARRIES — i.e. whether the feed check can
    # compare the FEED's episode ids against the ids we hold, or must fall back to
    # comparing dates. It is a fact about WHO WRITES THE ROW:
    #   "rss_guid" — a feed importer writes the row, so url is the item's <guid>
    #       (feeds.episode_identity). Every podcast but SOP since the move off Taddy
    #       (2026-12); sql/014 moved the rows Taddy had written onto their guids.
    #   None        — a scraper writes urls the feed knows nothing about. SOP is the one
    #       podcast in this bucket: its rows come from switchedonpop.com/episodes/... via
    #       scrapers/sop/scrape.py, and its feed is only the second source. Measured
    #       2026-09-03: an identity comparison there would report a 13-episode BEHIND every
    #       day. Curated sources are None too: no feed at all (see curated_show_slugs).
    episode_identity: Optional[str] = None

    # The show's own public RSS feed: where new episodes are found (scrapers/rss/
    # import_feed.py) and what the feed check compares against. Found through Apple's
    # free lookup (https://itunes.apple.com/lookup?id=<itunes_id> returns feedUrl), so if
    # a feed ever moves, re-finding it is one lookup with the id kept here. Checked live
    # 2026-11-23 for every show below.
    feed_url: Optional[str] = None
    itunes_id: Optional[int] = None
    # Keep only items whose title starts with this, for a feed that carries several
    # shows (Slate's feed mixes Culture Gabfest with its other podcasts).
    feed_title_prefix: Optional[str] = None

    # Where this show's full transcripts come from: a key of scrapers.transcripts.READERS
    # ("aidailybrief", "npr"), or None for a show we do not transcribe (TAL and SOP get
    # their songs from their websites; Gabfest is show notes only).
    transcript_source: Optional[str] = None
    # Whether extraction should wait for that transcript before falling back to the show
    # notes (run_new_episodes.find_unextracted_episodes, up to TRANSCRIPT_GRACE_DAYS).
    # Until 2026-11 this was inferred from "has a taddy_uuid"; it is a fact about the
    # show, so it is stated here instead.
    require_transcript: bool = False

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


# The cut-over from Taddy (claude-plans/2026-11-23-off-taddy-plan.md, phase 3).
HARD_FORK_LAST_COLLECTED = date(2026, 12, 1)
# Machine Gods' back catalogue (from 2026-10-21) is Kevin's call; until he says yes, its
# first run starts at the cut-over and the backfill is `import_feed.py --backfill`.
MACHINE_GODS_IMPORT_SINCE = date(2026, 12, 1)


SHOWS: dict[str, ShowConfig] = {
    "sop": ShowConfig(
        slug="sop",
        name="Switched On Pop",
        show_id=1,
        content_types=["music"],
        # Imported Wed + Fri. Publishes mostly Tuesdays, but not only: since 2026-03-01
        # Neon holds Tue 29, Fri 8, Wed 4, Mon 3, Thu 2. The longest normal wait is a
        # Friday episode the Friday scrape can't see yet (the website lists it later
        # than the feed — 2026-09-18 appeared there as 09-21), which next imports
        # Wednesday: 5 days, plus a day = 6. The old 4 assumed Tuesday-only publishing;
        # it failed that 09-18 episode on 09-22 and the Wednesday import fetched it.
        feed_grace_days=6,
        # Deliberately no episode_identity: scrapers/sop/scrape.py writes
        # switchedonpop.com/episodes/... urls, so Taddy uuids match almost nothing we
        # hold. The feed check compares DATES for SOP (see ShowConfig.episode_identity).
        taddy_uuid="97ed51a4-460e-4dc8-8db5-30df96ad59bc",
        feed_url="https://feeds.megaphone.fm/switchedonpop",
        itunes_id=934552872,
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
        # Publishes Sun/Mon (since 2026-03-01: Mon 16, Sun 9) and imports Mondays; every
        # Monday episode since 08-03 was imported the same day. Longest normal wait is
        # Sunday → Monday, 1 day, plus a day = 2. The one Friday bonus in that window
        # would need 4 by the rule, but pipeline.yml judges TAL right after the Monday
        # import that fetches it, so a bonus can't false-alarm there; widening for it
        # would only slow the real alarm.
        feed_grace_days=2,
        # run_pipeline.discover_tal_episodes runs the feed importer, so new TAL rows carry
        # the feed's <guid> ("46259 at https://www.thisamericanlife.org") — unlike SOP. The
        # 28 rows Taddy wrote moved to their guids in sql/014; older rows keep their page
        # urls and match by title + date.
        importer="rss",
        episode_identity="rss_guid",
        taddy_uuid="d682a935-ad2d-46ee-a0ac-139198b83bcc",
        feed_url="https://www.thisamericanlife.org/podcast/rss.xml",
        itunes_id=201671138,
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
        importer="rss",
        episode_identity="rss_guid",
        taddy_uuid="60fabbea-f51e-4c8b-82b4-1cbd57fe8c02",
        feed_url="https://anchor.fm/s/f7cac464/podcast/rss",
        itunes_id=1680633614,
        transcript_source="aidailybrief",
        require_transcript=True,
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
        importer="rss",
        episode_identity="rss_guid",
        taddy_uuid="81b2a312-6976-4d22-bc54-4e3991fee332",
        feed_url="https://feeds.npr.org/510282/podcast.xml",
        itunes_id=278974813,
        transcript_source="npr",
        require_transcript=True,
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
        episode_identity="rss_guid",
        # No longer collected: Kevin, 2026-11-23, "hard fork has basically become machine
        # gods". Its 226 rows and transcripts stay; nothing imports it.
        collected_until=HARD_FORK_LAST_COLLECTED,
        taddy_uuid="ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0",
        feed_url="https://feeds.simplecast.com/6HKOhNgS",
        itunes_id=1528594034,
        fallback_website_url="https://www.nytimes.com/column/hard-fork",
        store_raw_content=True,
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB (Option A) — same as AI Daily
        extraction_type="entity_extraction",
    ),
    "machine-gods": ShowConfig(
        slug="machine-gods",
        name="Machine Gods",
        show_id=65,  # created by sql/014 with this explicit id
        content_types=["entities"],
        # Kevin Roose and Casey Newton's twice-weekly NPR show (from 2026-10), which took
        # Hard Fork's place here. Full NPR transcripts, read like PCHH's.
        importer="rss",
        episode_identity="rss_guid",
        feed_url="https://feeds.npr.org/g-s1-143588/podcast.xml",
        itunes_id=6812430636,
        transcript_source="npr",
        require_transcript=True,
        import_since=MACHINE_GODS_IMPORT_SINCE,
        fallback_website_url="https://www.npr.org/podcasts/g-s1-143588/machine-gods",
        store_raw_content=True,
        notion_database_id="982dafa0ad374d618e25207e67860e33",  # shared Tech DB, as Hard Fork was
        extraction_type="entity_extraction",
    ),
    "culture-gabfest": ShowConfig(
        slug="culture-gabfest",
        name="Culture Gabfest",
        show_id=54,
        content_types=["media"],
        importer="gabfest_rss",
        episode_identity="rss_guid",
        taddy_uuid=None,  # Taddy won't transcribe Gabfest (iHeart rights) — Megaphone RSS show-notes instead
        fallback_website_url="https://feeds.megaphone.fm/slatesculturegabfest",
        # Slate moved its feed to Acast; the Megaphone url above redirects there, and the
        # channel's itunes:new-feed-url names this one (checked 2026-11-23).
        feed_url="https://feeds.acast.com/public/shows/6969638b5f7f66b0e4468dad",
        feed_title_prefix="Culture Gabfest",
        # Slate ended the show: final episode 2026-07-01, "So Long, and Thanks for All
        # the Granola Edition". https://slate.com/podcasts/culture-gabfest/2026/07/the-last-episode-of-the-slate-culture-gabfest-ever
        ended_on=date(2026, 7, 1),
        store_raw_content=True,
        notion_database_id="3780501ef95081a783ebf8a32fa94657",  # shared Media DB (Option A)
        notion_min_mentions=1,  # media: a single recommendation is meaningful
        extraction_type="media_extraction",
    ),
    # ── Curated sources (medium != "podcast") ───────────────────────────────
    # Ingested item-by-item via save_item.py or the weekly curated intake
    # (run_intake.py) — NOT by the scheduled episode orchestrator. Their entities
    # surface in Notion at
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
TRANSCRIPT_NOTION_SHOWS: tuple[str, ...] = ("ai-daily-brief", "hard-fork", "machine-gods", "saved-episodes")

# Shows whose full texts mirror into the Notion "Blog Posts" DB (same engine,
# --target blog-posts). agentic-research is deliberately absent: those docs'
# canonical home is the Obsidian vault — only their MENTIONS go to Notion.
BLOG_NOTION_SHOWS: tuple[str, ...] = ("openai-blog", "anthropic-blog", "saved-articles")


# The one place this format string lives. The Taddy importer writes it into
# `episodes.url` (scrapers/taddy/import_transcripts.episode_url_key, which delegates
# here) and the feed check rebuilds it from the feed's uuid to ask "do we hold this
# episode?" — two readers of the same identity, so it must not exist twice.
TADDY_EPISODE_URL_PREFIX = "https://api.taddy.org/podcast-episode/"


def taddy_episode_url(uuid: str) -> str:
    """The exact `episodes.url` the Taddy importer writes for this episode uuid.

    Identity, not a link: the uuid is unique per episode where a show's websiteUrl may
    not be (Hard Fork returns one show-level url for every episode), and it is stable
    when Taddy re-dates an episode — the upsert is ON CONFLICT (url) with
    COALESCE on publish_date, so a re-date moves the date and never the identity.
    tests/test_feed_check.py pins this against the importer so they cannot drift.
    """
    return f"{TADDY_EPISODE_URL_PREFIX}{uuid}"


# TAL's own website, where the song credits live. The Taddy url above is TAL's IDENTITY
# (episodes.url, what the feed check compares); this is the READABLE PAGE, derived at
# scrape time and never stored. Keeping the two apart is the whole point: writing the
# page url into episodes.url would break the Phase 4 identity check.
TAL_SITE_ROOT = "https://www.thisamericanlife.org"

_TAL_TITLE_NUMBER = re.compile(r"^\s*(\d+)\s*:\s*(.+)$")
# Apostrophes vanish rather than becoming separators — TAL writes "I Couldn't Help but
# Notice" as /894/i-couldnt-help-but-notice, not .../i-couldn-t-....
_APOSTROPHES = dict.fromkeys(map(ord, "'‘’ʼ"), None)
_NON_SLUG = re.compile(r"[^a-z0-9]+")

# TAL pages that are NOT episodes, by first path segment. OBSERVED, not derived, and
# therefore not exhaustive — checked 2026-09-04.
#
# Nothing in a TAL url distinguishes an episode from a marketing page: /blackjack IS
# episode 466, and /lifepartners is the Supercast subscription pitch — it 307s off-site
# to thisamericanlife.supercast.com and carries no song credits. Both are bare slugs, so
# a shape rule cannot separate them. A short denylist with a date on it is the honest
# mechanism; a regex pretending to derive the answer would not be.
#
# /lifepartners earns its place twice over: TAL's RSS uses it as the <link> for promo
# items ("An Update from Ira", "Ira (Reluctantly) Gives a Graduation Speech"), and it is
# already sitting in episodes.url on row 3022. Getting one of these wrong costs a wasted
# Firecrawl call once — but the episode can never gain songs, so it also becomes a
# permanent queue resident, and that is the expensive half.
_TAL_NON_EPISODE_PATHS = frozenset({
    "lifepartners",
    "about",
    "archive",
    "staff",
    "contact",
    "donate",
    "shop",
    "newsletter",
    "search",
})


def tal_episode_page_url(title: Optional[str]) -> Optional[str]:
    """Best-effort thisamericanlife.org page URL derived from an episode title.

    "896: I Know What You Need" -> https://www.thisamericanlife.org/896/i-know-what-you-need

    LAST RESORT, and deliberately so. The authoritative page url is the RSS item's
    <link> (scrapers/tal/fetch.fetch_feed_page_links); this exists only for an episode
    that has rolled off the feed's 15-item window — measured at exactly 15 on
    2026-09-04, so a backlog longer than about four months outruns it.

    Returns None when the title carries no leading episode number, because there is then
    nothing to guess from. Live example: row 7422, "Ira (Reluctantly) Gives a Graduation
    Speech". Its feed <link> is /lifepartners, which is NOT an episode page — it is the
    Supercast subscription pitch (see _TAL_NON_EPISODE_PATHS), so that row has no readable
    page anywhere and the honest answer for it is None all the way down.

    A derived url can also simply be wrong: TAL's own numbering is not uniform. Verified
    2026-09-04 — /885/bless-this-mess is a 404 while the canonical page is
    /bless-this-mess, and the number-only /896 is a 404 too. That is survivable by
    construction: a miss costs one Firecrawl call and scrapers/tal/parse.parse_episode
    drops it on its is_404 branch. It never writes a wrong song.
    """
    if not title:
        return None
    match = _TAL_TITLE_NUMBER.match(title)
    if not match:
        return None
    number, rest = match.group(1), match.group(2)
    # NFKD first so an accented title slugs the same whether the source string arrived
    # composed or decomposed — "Café" as NFC vs NFD otherwise yields /caf-society vs
    # /cafe-society, i.e. the same title producing two different urls run to run.
    rest = unicodedata.normalize("NFKD", rest)
    slug = _NON_SLUG.sub("-", rest.translate(_APOSTROPHES).lower()).strip("-")
    if not slug:
        return None
    return f"{TAL_SITE_ROOT}/{number}/{slug}"


def is_tal_episode_page_url(url: Optional[str]) -> bool:
    """Is this a TAL url worth spending a Firecrawl call on to look for songs?

    Rejects the four ways the scrape has sent, or could send, Firecrawl somewhere useless
    or unsafe (all verified live 2026-09-04):

      1. An api.taddy.org identity url — the 2026-08 regression this module's caller
         fixes. No song credits on it, ever.
      2. The bare site root, which is what TAL's RSS carries as the <link> for its
         untitled bonus items ("A Big Announcement" -> https://www.thisamericanlife.org).
      3. A known non-episode page (_TAL_NON_EPISODE_PATHS) — notably /lifepartners, the
         subscription pitch that TAL's feed hands out as the <link> for promo items.
      4. A lookalike host. The host match is on a DOMAIN BOUNDARY, not a string prefix: a
         bare startswith would accept https://www.thisamericanlife.org.example.com/x.
         Nothing hostile is expected in TAL's own feed, but this function's whole job is
         deciding what is safe to fetch, so it should not be the weak link.

    WHAT IT CANNOT DO, stated plainly so the name does not over-promise: prove that a
    given TAL page IS an episode. /blackjack and /lifepartners are the same shape and only
    one is an episode, so the answer lives in the denylist above, which is observed rather
    than derived. Something novel and non-episodic in TAL's feed would get through, cost
    one Firecrawl call, and then sit in the queue — see fetch.get_episodes_missing_songs.
    """
    if not url:
        return False
    if url.startswith(TADDY_EPISODE_URL_PREFIX):
        return False
    for root in (TAL_SITE_ROOT, TAL_SITE_ROOT.replace("://www.", "://")):
        if url == root or url.startswith(root + "/"):
            path = url[len(root):].strip("/")
            if not path:
                return False
            first_segment = path.split("/")[0].split("?")[0].split("#")[0].lower()
            return first_segment not in _TAL_NON_EPISODE_PATHS
    return False


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


def ended_show_slugs() -> set[str]:
    """Slugs of shows that have published their final episode (see ShowConfig.ended_on).

    data_health skips STALENESS for these — a concluded show is permanently stale by
    definition, and a check that can never pass again is noise, not signal."""
    return {slug for slug, cfg in SHOWS.items() if cfg.ended_on is not None}


def not_collected_show_slugs() -> set[str]:
    """Slugs of shows we stopped importing while they keep publishing (collected_until).
    data_health skips their staleness and feed checks; their rows stay."""
    return {slug for slug, cfg in SHOWS.items() if cfg.collected_until is not None}


def curated_show_slugs() -> set[str]:
    """Slugs of curated (non-podcast) sources — blog/research shows with no feed and
    no publishing cadence. data_health skips staleness + feed checks for these; a
    'stale' curated show just means Kevin hasn't pulled anything lately, by design."""
    return {slug for slug, cfg in SHOWS.items() if cfg.medium != "podcast"}

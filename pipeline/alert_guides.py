"""What each list-maker alert means, in plain words: the text announce.py puts in Slack.

One entry per thing that can fail: every data_health check, and every workflow step
announce.py tracks. Each says what failed, why it usually happens, and what to look at
first, so the person reading the alert can act on it without opening the code. The
reasoning behind each check lives in its docstring in data_health.py; this is the
reader's summary of it. tests/test_announce.py fails if a check has no entry here.

Standard library only, like announce.py: the alert step must still work in a run whose
dependency install is the thing that failed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Guide:
    title: str        # what failed, as a person would say it
    usually: str      # the most common cause, from the check's history
    check_first: str  # the first place to look


# ── data_health checks (keyed by CheckResult.name) ─────────────────────────────────────
CHECK_GUIDES: dict[str, Guide] = {
    "show_config_matches_neon": Guide(
        "The show list in code and in Neon disagree",
        "a show was added or renamed in pipeline/show_config.py without its Neon row, or the reverse.",
        "the slug and id it names against the `shows` table.",
    ),
    "episode_identity_required_fields": Guide(
        "Episodes are missing a title, link, date or show",
        "an importer wrote a partial row (a feed item with no date, a scrape that lost the title).",
        "the sample rows it lists, then the importer that wrote them.",
    ),
    "duplicate_episodes_by_show_title_date": Guide(
        "The same episode is stored twice",
        "an importer's duplicate check missed, usually after a link format or date changed.",
        "the ids it lists; pipeline/repair_duplicate_episodes.py merges them.",
    ),
    "transcript_coverage_by_show": Guide(
        "Episodes are missing their transcripts",
        "Taddy hasn't published the transcript yet (normally within a day) or the transcript import failed.",
        "whether Taddy has transcripts for the episodes it names; if it does, the import step's log.",
    ),
    "episode_freshness_by_show": Guide(
        "A show has gone too long without a new episode",
        "its import stopped (a feed change, an expired login, the workflow not running), or the show is on break.",
        "the show's own feed: if it has recent episodes we don't, the import is broken.",
    ),
    "music_songs_still_arriving": Guide(
        "A music show has stopped getting songs",
        "the website scrape changed shape and finds nothing while episodes keep arriving (TAL, Jan–Sep 2026).",
        "the newest episode's page on the show's website against what the scraper reads.",
    ),
    "notion_sync_freshness": Guide(
        "Notion is behind Neon",
        "the Notion sync is failing or not reaching some rows (a revoked token, an API change, a deleted page).",
        "the 'Sync transcripts to Notion' step, and the sync log lines for the entities it names.",
    ),
    "ai_daily_extraction_integrity": Guide(
        "AI extraction left gaps",
        "an extraction batch failed or its filters removed everything; orphans mean a transcript was deleted.",
        "the episodes and runs it names (table `ai_runs`), and the run's dead-letter artifact.",
    ),
    "ai_run_completeness": Guide(
        "An extraction batch loaded fewer mentions than it produced",
        "the load crashed partway, or mentions were deleted afterwards.",
        "the named run's batch folder (dead-letter artifact); re-running that batch reloads it.",
    ),
    "ai_run_stuck_loading": Guide(
        "An extraction batch is stuck halfway through loading",
        "the run died during a load (a timeout or a lost runner).",
        "the named run; the next daily run normally re-extracts its episodes.",
    ),
    "transcript_race_selfheal": Guide(
        "Episodes extracted before their transcript arrived aren't being redone",
        "the self-heal step in the daily import is failing or not running.",
        "the self-heal lines in the import step's log.",
    ),
    "ai_mention_required_fields": Guide(
        "Mentions have blank or out-of-range fields",
        "an extraction prompt or parser change let through empty names or bad confidence values.",
        "the mention ids it lists and the batch that wrote them.",
    ),
    "sponsor_share": Guide(
        "A show's recent mentions are all ad reads",
        "the sponsor detector is over-claiming, or the filters removed every editorial mention.",
        "that show's latest extraction batch output.",
    ),
    "possible_entity_alias_splits": Guide(
        "Some tools may be stored under two names",
        "two spellings of one tool (\"ChatGPT\" / \"Chat GPT\").",
        "the ids it lists; alias normalization merges them.",
    ),
    "import_caught_up_to_feed": Guide(
        "A show is behind its podcast feed",
        "the import that fetches the episode hasn't had its turn yet, the source (website or Taddy) lags "
        "the feed, or the importer stopped finding episodes.",
        "the show's next scheduled import; if the line says 'backstop', whether that workflow is still "
        "being dispatched at all.",
    ),
}


# ── workflow steps (keyed by "<workflow>:<step id>") ────────────────────────────────────
_PREFLIGHT = Guide(
    "The run couldn't reach the database",
    "this runner's network path to Neon — other jobs the same minute usually connect fine, and the next "
    "run heals it.",
    "the host and addresses in the alert; if it repeats, Neon's status page.",
)

STEP_GUIDES: dict[str, Guide] = {
    "entities:preflight": _PREFLIGHT,
    "entities:import": Guide(
        "The daily import and extraction step failed",
        "a Taddy or OpenAI error, a timeout on a large batch, or a code error.",
        "the end of the step's log; the extraction batch is kept as the run's dead-letter artifact.",
    ),
    "entities:notion": Guide(
        "The Notion transcript sync failed",
        "Notion API errors or a revoked token.",
        "the FAILED lines in the step's log.",
    ),
    "entities:health": Guide(
        "The data health check crashed before it could report",
        "a query error or a feed reader exception.",
        "the traceback in the step's log.",
    ),
    "music:preflight": _PREFLIGHT,
    "music:spotify_cache": Guide(
        "The Spotify login couldn't be restored",
        "the SPOTIFY_CACHE_JSON secret is missing or malformed.",
        "the secret in the repo's Actions settings.",
    ),
    "music:pipeline": Guide(
        "The music import failed",
        "the Spotify login expired (re-auth locally and update SPOTIFY_CACHE_JSON), the website scrape "
        "broke, or Firecrawl errored.",
        "the first error line in the step's log.",
    ),
    "music:feed_check": Guide(
        "The feed check crashed before it could report",
        "a Taddy error or a query error.",
        "the traceback in the step's log.",
    ),
    "intake:preflight": _PREFLIGHT,
    "intake:log_schema": Guide(
        "The Notion intake log couldn't be prepared",
        "a Notion API error or a revoked token.",
        "the step's log.",
    ),
    "intake:intake": Guide(
        "The weekly curated intake failed",
        "a candidate that couldn't be judged or saved, the weekly Slack line not posting, or the "
        "database connection dropping during the run.",
        "the last lines of the step's log: it says how many candidates failed and why.",
    ),
}

# The job failed but none of the tracked steps did — setup, install, or a step nobody
# mapped. Named rather than dropped, so a red run can never be a silent one.
RUN_GUIDE = Guide(
    "Something outside the main steps failed",
    "a GitHub or PyPI hiccup while setting up the runner or installing dependencies.",
    "the first red step in the run.",
)


def fallback_guide(name: str) -> Guide:
    """For a check added to data_health without an entry above (the test should catch it
    first). Better a plain message than no message."""
    return Guide(name, "see this check's docstring in pipeline/data_health.py.", "the details in the alert.")

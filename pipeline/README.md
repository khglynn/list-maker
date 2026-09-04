# list-maker Pipeline

*Last updated: 2026-05-22*

## Directory Structure

```
pipeline/
├── run_new_episodes.py    # Orchestrator for AI Daily (import → extract → sync)
├── run_pipeline.py        # Orchestrator for music shows (scrape → match → sync)
├── common.py              # Shared DB connection + env loading
├── show_config.py         # Centralized ShowConfig for all shows
├── spotify_match.py       # Match songs to Spotify (all shows)
├── sync_playlist.py       # Sync matched songs to playlists
├── sync_notion.py         # Sync entities to Notion (AI Daily)
├── data_health.py         # Read-only Neon data quality report
├── repair_duplicate_episodes.py # Dry-run-first duplicate episode merger
├── requirements.txt       # Python dependencies
├── venv/                  # Virtual environment (gitignored)
│
├── scrapers/              # Show-specific scraping code
│   ├── sop/               # Switched On Pop
│   │   ├── scrape.py          # Scrape new episodes from website
│   │   └── download_episode_art.py
│   │
│   ├── tal/               # This American Life
│   │   ├── scrape.py          # Unified scrape pipeline (fetch→parse→fill)
│   │   ├── fetch.py           # Fetch episode URLs
│   │   ├── parse.py           # Parse episode pages
│   │   ├── fill_songs.py      # Fill in song data
│   │   ├── repair_metadata.py # Dry-run-first official metadata repair
│   │   └── download_episode_art.py
│   │
│   ├── ai_daily/          # AI Daily Brief entity extraction
│   │   ├── transcripts.py        # Fetch transcripts (RSS + OpenAI STT)
│   │   ├── extract_entities.py   # LLM entity extraction (OpenAI)
│   │   ├── init_entity_schema.py # Create/reset Neon schema
│   │   ├── load_entity_batch.py  # Load batch artifacts into Neon
│   │   ├── normalize_aliases.py  # Merge duplicate entities
│   │   ├── discover_links.py     # Find URLs for entities (Firecrawl)
│   │   ├── report_summary.py     # Quality summary report
│   │   └── run_guarded_backfill.py    # Quality-gated batch runner
│   │
│   └── taddy/             # Taddy API transcript importer
│       └── import_transcripts.py  # Multi-show transcript import
│
└── _cache/                # Scraped episode data (gitignored)
    ├── tal/               # TAL episode JSONs
    └── ai_daily/          # AI Daily transcripts
```

Note: Mosaic artwork generation is in `marketing/` (separate from pipeline).

---

## Data Quality + Tests

Use tests and health checks together:

- Tests protect pipeline behavior before code changes ship.
- Health checks inspect the live Neon data for gaps, duplicates, and suspicious drift.

Run the local test suite:

```bash
pipeline/venv/bin/python -m pytest
```

Run the live read-only health report:

```bash
pipeline/venv/bin/python pipeline/data_health.py
```

Run it in strict mode for automation:

```bash
pipeline/venv/bin/python pipeline/data_health.py --strict
```

Current health policy:

| Area | Hard failure | Allowed / explained |
|------|--------------|---------------------|
| Shows | Configured slug/id mismatch | None |
| Episodes | Missing show, title, URL, publish date | Optional episode fields may be null |
| Duplicates | Same show/title/date more than once | None |
| Transcripts | AI Daily/PCHH must be complete; SOP/TAL latest transcript must stay fresh | Historic SOP/TAL transcript coverage can be partial |
| AI Daily | Transcript without mentions, mention without transcript, completed run with zero mentions, a completed run holding fewer mentions than its CSV declared, a batch load abandoned in `loading` | Entity alias split candidates are warning-level until curated; a batch load in flight is a pass |
| Music songs | A music show (any show with a Spotify playlist) that has published 3+ episodes over 21+ days without acquiring a single song — the show still runs, still exits 0, and produces nothing | One songless episode is normal (TAL reruns archive episodes with no music credits); the archive is out of scope entirely (only episodes newer than the last song-bearing one count); 2 episodes / 14 days is a warning; a show on break never trips it (no new episodes, no streak) and an ended show is skipped |

Dry-run-first repair commands:

```bash
pipeline/venv/bin/python pipeline/scrapers/tal/repair_metadata.py
pipeline/venv/bin/python pipeline/scrapers/tal/repair_metadata.py --execute

pipeline/venv/bin/python pipeline/repair_duplicate_episodes.py
pipeline/venv/bin/python pipeline/repair_duplicate_episodes.py --execute
```

As of 2026-05-22, the episode-level health report has zero hard failures. The remaining warning is AI Daily entity alias splits, which should be handled by a curated merge pass rather than a broad automatic rewrite.

---

## Orchestrators

### run_pipeline.py (Music: SOP/TAL)

Runs the full music pipeline for any show in one command.

```bash
# Local usage (interactive)
cd pipeline
python run_pipeline.py --show-id 1              # SOP
python run_pipeline.py --show-id 2              # TAL
python run_pipeline.py --show-id 1 --dry-run    # Preview only

# CI usage (non-interactive, JSON output)
python run_pipeline.py --show-id 1 --yes --json --cache-path ../.spotify_cache/.cache

# Run all shows
python run_pipeline.py --show-id all --yes --json
```

What it does for music shows:
1. **Scrape** — discover new episodes, fetch pages, parse songs, insert to DB
2. **Match** — search unmatched songs on Spotify, score confidence
3. **Sync** — add matched tracks to playlist, update description

| Flag | Purpose |
|------|---------|
| `--show-id` | Show ID (1=SOP, 2=TAL, 3=AI Daily) or `all` |
| `--dry-run` | Preview only, no database or API writes |
| `--yes` | Skip confirmation prompts (required for CI) |
| `--cache-path` | Custom Spotify OAuth cache location |
| `--json` | Output structured JSON summary |

### run_new_episodes.py (AI Daily)

Chains Taddy import → transcript-race self-heal → entity extraction → alias
normalization → Notion sync → Spotify sync.

```bash
cd pipeline
./venv/bin/python3 run_new_episodes.py --shows ai-daily-brief
./venv/bin/python3 run_new_episodes.py --all
```

#### The transcript race, and how it heals itself

Taddy publishes a transcript roughly a day after the episode. A run that fires in
between used to extract the show-notes blurb instead, and because the selection query
skips any episode that already has mentions, nothing ever went back for the real text —
episodes 5133 and 7261 ended up with mentions like "The AI Daily Brief Newsletter" and
stayed that way. Three pieces keep that closed:

- **Prevention.** Transcript-based (Taddy) shows wait for the transcript rather than
  falling back to notes.
- **A bound on the wait.** After `TRANSCRIPT_GRACE_DAYS` (7) the episode is extracted
  from its notes anyway, announced in the run output. Waiting forever would trade a
  wrong-source extraction for a missing one.
- **Recovery.** Each run re-extracts episodes whose mentions carry no transcript_id
  though the episode now has a transcript, capped at `SELF_HEAL_MAX_EPISODES_PER_RUN`
  (3) and reported in the run summary. It re-extracts by ORIGINAL batch name, because
  `delete_existing_run` keys on (show_id, batch_name) — healing one episode of a mixed
  batch under a new name would delete its healthy siblings and never replace them.

Provenance is recorded when the text is read (`extraction_provenance.json`, passed to
`load_entity_batch --provenance-json`), not looked up at load time. Extraction of a
batch takes minutes, and a transcript landing inside that window would otherwise be
stamped onto mentions mined from show notes — provenance nobody could later tell was
fabricated, and an episode the recovery loop would never revisit.

`data_health.check_transcript_race_selfheal` is the backstop: it warns while the queue
is draining and fails once an episode has sat unhealed for more than 3 days.

#### Exit codes: which failures get retried

`run_script` retries a failed step twice with 5s/10s backoff, because the steps are
idempotent and most failures are weather — a 429, a 5xx, a dropped connection, a
timeout. One exit code opts out:

| Exit | Meaning | run_script |
|---|---|---|
| 0 | success | done |
| 1 | something failed, possibly transiently | retry twice, then FAIL |
| **2** | **deterministic — this will fail identically next time** | **FAIL immediately** |

Exit 2 is not an invented number: argparse already exits 2 on a bad invocation, so
"your inputs are wrong" was half this convention before it was written down. A step
uses it for preconditions only — a missing credential, an unknown show slug, an input
file that isn't there — never for anything a network or a database could have caused.
The producers today (grep `sys.exit(2)`):

- `scrapers/taddy/import_transcripts.py` — missing `TADDY_USER_ID`/`TADDY_API_KEY`, unknown show slug
- `scrapers/ai_daily/extract_entities.py` — missing `OPENAI_API_KEY`, no episodes selected, any missing input file
- `scrapers/ai_daily/load_entity_batch.py` — missing `batch_manifest.json` / `mentions.csv`, unknown show slug
- `sync_notion.py` — show has no `notion_database_id`, missing `NOTION_TOKEN`
- `sync_playlist.py` — unknown `--show-id`

Note the shape: several of these are raised inline rather than through a shared
`except SomeError` handler, because `RuntimeError` is *also* how the Taddy importer and
the extractor report OpenAI/GraphQL HTTP failures — which are exactly the retryable
case. Catching by type there would stop retrying real API blips.

**The loader is the sharpest version of that rule.** A database error that rolls the
batch back MUST stay exit 1, because the retry is what clears the abandoned `'loading'`
row and re-runs the batch whole — that retry is the entire point of the transactional
load. So `load_entity_batch` maps only `FileNotFoundError` by type (both raises happen
before the connection is opened) and exits inline for the unknown slug; its
`RuntimeError` from `finalize_run_completed` stays retryable, and a malformed CSV that
only fails once rows are being inserted stays exit 1 too, because at that point it
cannot be told apart from a genuine database error without a new validation step.

**A partial playlist sync is exit 1** (2026-09-04, Kevin's call). `sync_playlist.py`
used to add 150 of 250 tracks, print `Done!` and exit 0. It now reports one `failures`
per dropped batch — plus one if the playlist *read* was truncated, in which case it
refuses to add anything at all, because the diff is the only dedup there is and a
partial read makes it re-add tracks Spotify already holds. One, not two: a dropped batch
is a Spotify blip and the next run adds what this one missed, so it *should* be retried.
Both orchestrators see it — `run_pipeline` reads the `failures` key in-process through
`record_step_failures`, and `run_new_episodes` shells out and reads the exit code.

---

## GitHub Actions Automation

The pipeline runs automatically via `.github/workflows/pipeline.yml`.

### Schedule

| Show | When | Why |
|------|------|-----|
| SOP | Wed + Fri, 10 AM UTC | SOP publishes Tue/Thu |
| TAL | Monday, 10 AM UTC | TAL publishes Sunday |
| AI Daily | (not yet scheduled) | Entity extraction needs more work |

### Manual Trigger

Go to **Actions** → **Pipeline - Update Playlists** → **Run workflow** → pick show + dry-run toggle.

### Secrets Required

| Secret | Source | Purpose |
|--------|--------|---------|
| `SPOTIFY_CLIENT_ID` | spotify-bulk-actions-mcp/.env | Spotify app |
| `SPOTIFY_CLIENT_SECRET` | spotify-bulk-actions-mcp/.env | Spotify app |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8080/callback` | Spotify OAuth |
| `SPOTIFY_CACHE_JSON` | .spotify_cache/.cache contents | Refresh token |
| `NEON_DATABASE_URL` | list-maker/.env.local | Database |
| `FIRECRAWL_API_KEY` | list-maker/.env.local | Web scraping |
| `SLACK_WEBHOOK_URL` | Slack app setup (optional) | Notifications |

### Refreshing Spotify Token

If the pipeline fails with an auth error:
1. Run locally: `python spotify_match.py --show-id 1 --limit 1` (triggers OAuth flow)
2. Copy the updated `.spotify_cache/.cache` file contents
3. Update the `SPOTIFY_CACHE_JSON` GitHub secret with the new JSON

---

## Individual Scripts

| Script | Purpose | Run |
|--------|---------|-----|
| `spotify_match.py` | Match songs to Spotify | `python spotify_match.py --show-id 1` |
| `sync_playlist.py` | Sync matched songs to playlist | `python sync_playlist.py --show-id 1` |
| `scrapers/sop/scrape.py` | Scrape new SOP episodes | `python scrapers/sop/scrape.py --execute` |
| `scrapers/tal/scrape.py` | Scrape new TAL episodes | `python scrapers/tal/scrape.py --execute` |

## Setup (Local)

```bash
cd ~/DevKev/personal/list-maker/pipeline
source venv/bin/activate
```

## Environment Variables

Loaded automatically from:
1. `~/DevKev/personal/spotify-bulk-actions-mcp/.env` — Spotify credentials
2. `../.env.local` — DATABASE_URL, FIRECRAWL_API_KEY

In CI, these come from GitHub secrets instead.

---

## AI Daily Brief Commands

For full transcripts (last 25 episodes):
```bash
cd ~/DevKev/personal/list-maker/pipeline/scrapers/ai_daily
python3 transcripts.py --limit 25
```

For entity extraction:
```bash
python3 extract_entities.py --limit 5 --offset 0
```

Guarded scale workflow (preflight + automatic quality gates):
```bash
cd ~/DevKev/personal/list-maker
pipeline/venv/bin/python pipeline/scrapers/ai_daily/run_guarded_backfill.py \
  --since-date 2025-08-08 \
  --preflight-new 10 \
  --run-full \
  --chunk-size 20
```

Taddy multi-show import:
```bash
pipeline/venv/bin/python pipeline/scrapers/taddy/import_transcripts.py \
  --shows ai-daily-brief,pchh,sop,tal \
  --per-show-limit 2000 \
  --max-pages 20
```

---

## Show Configuration

Defined in `sync_playlist.py` → `SHOWS` dict:

| ID | Name | Playlist ID |
|----|------|-------------|
| 1 | Switched On Pop - All Songs Ever Discussed | `0cEVeX4pdHf5RJOiTRzgxX` |
| 2 | This American Life: Full Music Archive | `3d7fjfrTTKvrl7VHv5JzIz` |

## Logs

Match progress logged to `match_progress.log` (gitignored)

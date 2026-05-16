# pod-lists Pipeline

*Last updated: 2026-03-13*

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
│   │   ├── process_batch.py   # Batch processing
│   │   ├── fix_404s.py        # Fix broken URLs
│   │   ├── scrape_missing.py  # Scrape missing episodes
│   │   ├── scoring_match.py   # Match scoring tracks
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
│   │   ├── run_guarded_backfill.py    # Quality-gated batch runner
│   │   └── run_mentions_until_done.py # Parallel orchestrator
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

Chains Taddy import → entity extraction → alias normalization → Notion sync.

```bash
cd pipeline
./venv/bin/python3 run_new_episodes.py --shows ai-daily-brief
./venv/bin/python3 run_new_episodes.py --all
```

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
cd /Users/KevinHG/DevKev/personal/list-maker/pipeline
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
cd /Users/KevinHG/DevKev/personal/list-maker/pipeline/scrapers/ai_daily
python3 transcripts.py --limit 25
```

For entity extraction:
```bash
python3 extract_entities.py --limit 5 --offset 0
```

Guarded scale workflow (preflight + automatic quality gates):
```bash
cd /Users/KevinHG/DevKev/personal/list-maker
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

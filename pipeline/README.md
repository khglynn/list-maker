# pod-lists Pipeline

*Last updated: 2026-03-06*

## Directory Structure

```
pipeline/
├── spotify_match.py       # Match songs to Spotify (all shows)
├── sync_playlist.py       # Sync matched songs to playlists
├── requirements.txt       # Python dependencies
├── venv/                  # Virtual environment (gitignored)
│
├── scrapers/              # Show-specific scraping code
│   ├── sop/               # Switched On Pop
│   │   └── download_episode_art.py
│   │
│   ├── tal/               # This American Life
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
    ├── tal/               # 885 TAL episode JSONs
    └── ai_daily/          # AI Daily transcripts
```

Note: Mosaic artwork generation is in `marketing/` (separate from pipeline).

## Quick Reference

| Script | Purpose | Run |
|--------|---------|-----|
| `spotify_match.py` | Match songs to Spotify | `python spotify_match.py --show-id 1` |
| `sync_playlist.py` | Sync matched songs to playlist | `python sync_playlist.py --show-id 1` |

## Setup

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline
source venv/bin/activate
```

## Weekly Update Process (SOP)

Run these steps when new episodes are published:

### Step 1: Scrape New Episodes

Use Claude to scrape new episodes from switchedonpop.com:
- Query unscraped episodes: `SELECT id, url FROM episodes WHERE scraped_at IS NULL`
- Scrape using Firecrawl
- Extract songs to `songs` table

### Step 2: Match New Songs

```bash
python spotify_match.py --show-id 1
```

This finds unmatched songs and searches Spotify. Results:
- HIGH (90%+): Auto-approved
- MEDIUM (70-89%): Auto-approved
- LOW (<70%): Needs review
- NOT_FOUND: Needs fuzzy search or mark unavailable

### Step 3: Review LOW/NOT_FOUND (if any)

Query songs needing review:
```sql
SELECT id, title, artist, spotify_match_confidence
FROM songs s JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 1 AND spotify_match_confidence IN ('LOW', 'NOT_FOUND')
```

Use Claude + Spotify MCP to fuzzy search and fix.

### Step 4: Sync to Playlist

```bash
python sync_playlist.py --show-id 1
```

Adds new tracks, skips duplicates.

### Step 5: Update Playlist Description

After sync, update the description with latest episode:
```
Last updated [DATE] with "[EPISODE TITLE]" (Ep [NUMBER])
```

Use `mcp__spotify__update_playlist` or Spotify app.

---

## AI Daily Brief Transcript Backfill

For full transcripts (last 25 episodes), use:

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily
python3 transcripts.py --limit 25 --dry-run
python3 transcripts.py --limit 25
```

What it does:
- Pulls recent episodes from RSS
- Uses official transcript URL when present
- Otherwise generates transcript from audio with OpenAI STT
- Saves transcript to Neon (`episode_transcripts`) and local cache (`pipeline/_cache/ai_daily/transcripts/`)

For entity extraction schema testing (5-episode batches), use:

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily
python3 extract_entities.py --limit 5 --offset 0
```

This creates review artifacts under:
- `codex-notes/ai-daily-entity-extraction/<batch-name>/`

To make the AI Daily schema visible in Neon Database Studio:

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily
python3 init_entity_schema.py --reset
python3 load_entity_batch.py --batch-dir /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/ai-daily-entity-extraction/batch-01-focused-mini
python3 normalize_aliases.py
python3 discover_links.py --run-ids 1 --limit 300
python3 report_summary.py --run-id 1 --top 25
```

Guarded scale workflow (preflight + automatic quality gates):

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python pipeline/scrapers/ai_daily/run_guarded_backfill.py \
  --since-date 2025-08-08 \
  --preflight-new 10 \
  --run-full \
  --chunk-size 20
```

Taddy multi-show import (AI Daily + PCHH + SOP):

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python pipeline/scrapers/taddy/import_transcripts.py \
  --shows ai-daily-brief,pchh,sop \
  --per-show-limit 2000 \
  --max-pages 40 \
  --max-credit-spend 1701 \
  --check-credits-every 10 \
  --max-failures-per-show 40 \
  --min-transcript-chars 5000 \
  --reject-short-transcripts
```

Taddy dry-run (read-only, no transcript credits consumed):

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python pipeline/scrapers/taddy/import_transcripts.py \
  --shows ai-daily-brief,pchh,sop \
  --per-show-limit 25 \
  --dry-run
```

---

## Show Configuration

Defined in `sync_playlist.py` → `SHOWS` dict:

| ID | Name | Playlist ID |
|----|------|-------------|
| 1 | Switched On Pop - All Songs Ever Discussed | `0cEVeX4pdHf5RJOiTRzgxX` |
| 2 | This American Life: Full Music Archive | `3d7fjfrTTKvrl7VHv5JzIz` |

**To add a new show:** Add entry to `SHOWS` dict with `name`, `playlist_id`, and `acronym`.

**Description template** (in `DESCRIPTION_TEMPLATE`):
> [X] songs across [X] [ACRONYM] episodes. Last updated [MM/YY]. Support: buymeacoffee.com/kevinhg. Requests: hi@kevinhg.com.

## Environment Variables

Scripts load from two `.env` files:
1. `~/DevKev/personal/spotify-bulk-actions-mcp/.env` - Spotify credentials
2. `../env.local` - DATABASE_URL, FIRECRAWL_API_KEY

## Logs

Match progress logged to `match_progress.log` (gitignored)

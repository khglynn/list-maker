# list-maker - Agent Instructions

*Inherits from ~/DevKev/CLAUDE.md*
*Last updated: 2026-06-06*

## ⚑ Active work + resuming after compaction

A **durable, self-healing rebuild** of this pipeline is underway (all 6 shows auto-processing → Spotify/Notion; self-healing; Slack-notifying; tested). If you're resuming — **especially after a compaction** — read these first, in order, before touching anything:
1. `claude-plans/2026-06-06-durable-pipeline-resume.md` — way-of-working + failure-modes + grounding (load-bearing; don't skim).
2. `NOW.md` — live state + the exact next step.
3. `claude-plans/2026-06-06-durable-pipeline-rebuild.md` — the full plan (acceptance criteria, workstreams A–E).

Post-compaction sessions drift toward doing the minimum — that's the #1 failure mode here. The global CLAUDE.md "Default to deep, durable work" + the resume doc are the antidote.

## About This Project

Automated pipeline that extracts recommendations from podcasts and routes them to the right platforms.

**Source of truth:** Neon (Postgres) - all data lives here first, then syncs to other platforms.

**Destinations:**
- **Music** → Neon → Spotify playlists (one per show)
- **Movies/TV** → Neon → Notion + Trakt
- **Books** → Neon → Notion
- **Apps/Products** → Neon → Notion

**Data strategy:** Avoid transcription when possible. Many podcasts list songs/recommendations on their websites (FREE). Use cascading logic: website → free transcripts → transcript API → Whisper (last resort).

## Communication Default

Kevin prefers a "help me mode" by default:
- Use plain language first, minimal jargon.
- Be prescriptive: clear step-by-step actions with expected outcomes.
- Explain why each step matters in one short sentence.
- Keep asks small (1-2 actions at a time), then continue.
- Prefer "I handled X, now please do Y" over long implementation explanations.

## Key Abbreviations

| Abbreviation | Full Name | Data Source |
|--------------|-----------|-------------|
| SOP | Switched On Pop | Website show notes |
| TAL | This American Life | Website song credits |
| AI Daily | AI Daily Brief | Taddy transcripts → LLM extraction |
| PCHH | Pop Culture Happy Hour | Taddy transcripts (pipeline not built yet) |

## Tech Stack

- **Database:** Neon (Postgres) - source of truth
- **Framework:** Next.js (TypeScript)
- **Hosting:** Vercel
- **APIs:** Spotify (via MCP), Notion, Firecrawl (web scraping)
- **Transcripts:** Taddy API (multi-show transcript import)
- **Extraction:** OpenAI gpt-4.1-mini (AI Daily entity extraction from transcripts)

## Spotify MCP

We have a custom Spotify MCP built for this exact use case!

**Location:** `~/DevKev/personal/spotify-bulk-actions-mcp/`
**Repo:** https://github.com/khglynn/spotify-bulk-actions-mcp

**Key tools:**
- `batch_search_tracks` - Search songs with confidence scoring (HIGH/MEDIUM/LOW)
- `import_and_create_playlist` - CSV → playlist workflow
- `create_playlist_from_search_results` - Create from batch search
- `add_reviewed_tracks` - Add human-reviewed uncertain matches

**Settings:** Configured in `~/.claude/settings.local.json`

## Always-Allowed (project-specific)

*(Will add paths as we build)*

## Folder Structure

```
list-maker/
├── pipeline/                # Extraction and matching (Python)
│   ├── common.py            # Shared DB connection + env loading
│   ├── show_config.py       # Centralized ShowConfig for all shows
│   ├── run_new_episodes.py  # Orchestrator: import → extract → sync (AI Daily)
│   ├── run_pipeline.py      # Orchestrator: scrape → match → sync (SOP/TAL)
│   ├── sync_notion.py       # Neon → Notion sync (create/update/archive)
│   ├── sync_playlist.py     # Neon → Spotify playlist sync
│   ├── spotify_match.py     # Match songs to Spotify
│   ├── scrapers/            # Show-specific scrapers
│   │   ├── sop/             # Switched On Pop (website scraper)
│   │   ├── tal/             # This American Life (website scraper)
│   │   ├── ai_daily/        # AI Daily Brief (transcript entity extraction)
│   │   └── taddy/           # Taddy API transcript importer (multi-show)
│   └── _cache/              # Cached episode data + transcripts (gitignored)
│
├── .github/workflows/       # GitHub Actions automation
│   └── pipeline.yml         # Scheduled + manual pipeline runs
│
├── saved-transcripts/        # Saved episode transcripts + summaries
│
├── codex-notes/             # AI Daily extraction batch artifacts
│
├── marketing/               # Playlist artwork (mosaic generator)
│   ├── sop/                 # SOP tiles, targets, outputs
│   └── tal/                 # TAL tiles, targets, outputs
│
├── web/                     # Next.js app (future automation UI)
│   └── src/                 # Run npm commands from inside web/
│
└── claude-plans/            # Session plans and prompts
```

**Note:** All `npm` commands must be run from inside `web/` (e.g., `cd web && npm run dev`).

## Current Status (2026-06-07 — all 6 shows live)

**2026-06-07 update:** All 6 shows auto-process end-to-end. Pipeline hardened (Workstream A). **Option A shared Notion DBs:** entities are global (deduped across shows, tagged with a "Shows" multi-select), so AI Daily + Hard Fork share one **Tech DB** (`982dafa0…`) and PCHH + Culture Gabfest share one **Media DB** (`3780501e…94657`). A media extraction profile was added (one extractor, two profiles — tech vs media). Counts below verified 2026-06-07.

| Show | Type | Episodes | Items | Destination / status |
|------|------|----------|-------|----------------------|
| SOP | Music | — | 4,864 songs, 4,244 matched (87%) | Spotify playlist (auth fixed; revives on next pipeline.yml run) |
| TAL | Music | — | 1,087 songs, 875 matched (80%) | Spotify playlist |
| AI Daily | Tech | 997 | 5,563 entities, 11,065 mentions | shared Tech DB; caught up to 2026-06-06 |
| Hard Fork | Tech | 198 | 1,267 entities, 2,057 mentions | shared Tech DB |
| PCHH | Media | 52* | 365 media entities | shared Media DB |
| Culture Gabfest | Media | 17* | 122 media entities | shared Media DB (extracts from show-notes — no transcripts) |

*PCHH/Gabfest = scoped-recent backfill so far; the full archive (357 + 871 eps) is a ~11h/$7.50 run deferred to Kevin's call.

## Automation

The pipeline runs automatically via GitHub Actions (`.github/workflows/pipeline.yml`).

**Schedule:** SOP on Wed+Fri, TAL on Monday (all at 10 AM UTC).

**Manual trigger:** Actions tab → "Pipeline - Update Playlists" → Run workflow.

**Secrets (7 total):** `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`, `SPOTIFY_CACHE_JSON`, `NEON_DATABASE_URL`, `FIRECRAWL_API_KEY`, `SLACK_WEBHOOK_URL`

**If Spotify auth fails:** Re-auth locally (`python spotify_match.py --show-id 1 --limit 1`), then update `SPOTIFY_CACHE_JSON` secret with new `.spotify_cache/.cache` contents.

See `pipeline/README.md` for full orchestrator docs.

## AI Daily Pipeline

Extracts app/tool/platform mentions from transcripts using LLM extraction.

**Neon schema:** 3 tables — `ai_runs`, `ai_entities`, `ai_mentions` (plus `notion_page_id` / `notion_synced_at` on entities)
**Extraction model:** gpt-4.1-mini via OpenAI API
**Transcripts:** 915 episodes imported via Taddy API (originally RSS + Firecrawl, migrated to Taddy)
**Extraction status:** 773/914 episodes extracted (85%). ~141 old episodes (pre-Dec 2025) are intentionally skipped — they failed quality gates on lighter episodes and aren't worth re-processing. The orchestrator's `recent_only` filter (90 days) excludes them automatically. New episodes are extracted automatically.
**Notion destination:** Connected. Database "AI Daily Brief — Tools & Mentions" (DB ID: `982dafa0ad374d618e25207e67860e33`, MCP data source: `a72f8f82-1ca0-4973-9dc2-3757aa729c6e`). Sync via `pipeline/sync_notion.py`.
**Orchestrator:** `pipeline/run_new_episodes.py` — chains Taddy import → entity extraction → alias normalization → Notion sync → Spotify sync. Run with `--shows ai-daily-brief` or `--all`.

**Required env vars** (in `.env.local`):
- `DATABASE_URL` — Neon connection string
- `OPENAI_API_KEY` — for entity extraction
- `NOTION_TOKEN` — for Notion sync
- `TADDY_USER_ID` / `TADDY_API_KEY` — for Taddy transcript import

See `pipeline/scrapers/ai_daily/README.md` for full pipeline docs.

### Running Pipeline Scripts

The pipeline uses a Python venv and `.env.local` files that don't use `export`. Scripts like `import_transcripts.py` read env vars via `os.getenv()` but don't call `load_environment()` from `common.py`, so you must export vars manually.

**Working command pattern:**
```bash
cd /Users/KevinHG/DevKev/personal/list-maker && set -a && source .env.local && source pipeline/.env.local && set +a && cd pipeline && ./venv/bin/python3 <script>
```

- `set -a` / `set +a` — exports all sourced vars to child processes
- Must use `./venv/bin/python3` — system python3 doesn't have deps (psycopg2, etc.)
- The orchestrator (`run_new_episodes.py`) uses `common.py`'s `load_environment()` so it handles env loading itself, but still needs the venv

## Project-Specific Notes

- **SOP and TAL automated** - Both shows backfilled, playlists updated automatically via GitHub Actions
- **SOP matching partially improved** - NOT_FOUND dropped from 534 → 357 since Dec 2025. 17 more tagged UNAVAILABLE (not on Spotify).
- **Scrape before transcribe** - SOP and TAL have song data on their websites
- **Mosaic artwork done** - See `marketing/` for playlist cover generators
- **Taddy scraper supports multiple shows** - AI Daily, PCHH, SOP all configured

## Relevant Docs & Links

- **Plan file:** `claude-plans/2025-12-12-initial-plan.md`
- **Context doc:** `claude-plans/2025-12-12-project-context.md` (summary of original research chats)
- **Original research chats:**
  - `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - CSV Playlist Creation Guide_67e70c24.md`
  - `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - Workflow and transcript strategy_68eaedfe.md`

## Playwright Instance

Use `playwright-generic` for this project. No project-specific Playwright MCP set up yet.

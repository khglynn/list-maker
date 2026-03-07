# pod-lists - Agent Instructions

*Inherits from ~/DevKev/CLAUDE.md*
*Last updated: 2026-03-06*

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
pod-lists/
├── pipeline/              # Extraction and matching (Python)
│   ├── spotify_match.py   # Match songs to Spotify
│   ├── sync_playlist.py   # Sync to playlists
│   ├── scrapers/          # Show-specific scrapers
│   │   ├── sop/           # Switched On Pop (website scraper)
│   │   ├── tal/           # This American Life (website scraper)
│   │   ├── ai_daily/      # AI Daily Brief (transcript entity extraction)
│   │   └── taddy/         # Taddy API transcript importer (multi-show)
│   └── _cache/            # Cached episode data + transcripts (gitignored)
│
├── codex-notes/           # AI Daily extraction batch artifacts
│
├── marketing/             # Playlist artwork (mosaic generator)
│   ├── sop/               # SOP tiles, targets, outputs
│   └── tal/               # TAL tiles, targets, outputs
│
├── web/                   # Next.js app (future automation UI)
│   └── src/               # Run npm commands from inside web/
│
└── claude-plans/          # Session plans and prompts
```

**Note:** All `npm` commands must be run from inside `web/` (e.g., `cd web && npm run dev`).

## Current Status (Mar 2026)

| Show | Type | Episodes | Items | Status |
|------|------|----------|-------|--------|
| SOP | Music | 664 | 4,417 songs, 4,043 matched (92%) | Live playlist, 357 NOT_FOUND |
| TAL | Music | 882 | 1,094 songs, 880 matched (80%) | Live playlist, 214 NOT_FOUND |
| AI Daily | Apps/Tools | 888 | 734 ep extracted (83%), 7,982 mentions | Backfill mostly done |
| PCHH | Mixed | 300 | 298 transcripts imported | Taddy configured, pipeline not built |

## AI Daily Pipeline

Extracts app/tool/platform mentions from transcripts using LLM extraction.

**Neon schema:** 3 tables — `ai_runs`, `ai_entities`, `ai_mentions`
**Extraction model:** gpt-4.1-mini via OpenAI API
**Transcripts:** 888 episodes imported (RSS + Firecrawl initially, Taddy API added later)
**Backfill status:** 734/888 episodes processed (83%). 154 remaining. Quality gate (`mentions_per_episode >= 5`) was causing failures on lighter episodes — may need threshold adjustment to finish.
**Next destination:** Notion (not yet connected)

See `pipeline/scrapers/ai_daily/README.md` for full pipeline docs.

## Project-Specific Notes

- **SOP and TAL playlists active** - Both shows backfilled, playlists live
- **SOP matching partially improved** - NOT_FOUND dropped from 534 → 357 since Dec 2025. More fixes possible.
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

# Completed Work

Work that's done. Newest at top.

---

## March 2026

### Taddy Multi-Show Transcript Importer
**Completed:** Mar 1, 2026

Built `pipeline/scrapers/taddy/import_transcripts.py` — imports transcripts from Taddy API for multiple shows (AI Daily, PCHH, SOP). Handles retries, credit management, short transcript rejection.

### Project Rename: list-maker -> pod-lists
**Completed:** Mar 1, 2026

Renamed project and repo. Updated CLAUDE.md (README and pipeline README updated Mar 6).

---

## February 2026

### AI Daily Entity Extraction Pipeline
**Completed:** Feb 5-11, 2026

Full pipeline for extracting app/tool/platform mentions from AI Daily Brief transcripts:
- Lean 3-table Neon schema (`ai_runs`, `ai_entities`, `ai_mentions`)
- LLM extraction via OpenAI gpt-4.1-mini with locked 12-type taxonomy
- Quality-gated backfill runner with configurable thresholds
- Parallel orchestrator (`run_mentions_until_done.py`)
- Alias normalization, link discovery, QA summary scripts
- Validated on 25-episode batch, then scaled to 230 episodes

**Status:** 734/888 episodes extracted (83%), 7,982 mentions across 4,292 entities. 154 episodes remaining — quality gate may need threshold tuning to finish.

**Scripts:** `pipeline/scrapers/ai_daily/`

### AI Daily Transcript Backfill
**Completed:** Feb 2026

- AI Daily Brief episodes imported to Neon via RSS + Firecrawl + OpenAI STT
- Transcripts stored in `episode_transcripts` table and local cache
- Taddy API added later (Mar 2026) as a cheaper bulk transcript source

---

## January 2026

### Folder Reorganization
**Completed:** Jan 25, 2026

Restructured project folders for clarity:
- `scripts/` → `pipeline/` (describes purpose)
- `scripts/sop/`, `scripts/tal/` → `pipeline/scrapers/sop/`, `pipeline/scrapers/tal/` (grouped as scrapers)
- `scripts/fetched/` → `pipeline/_cache/` (underscore = internal)
- `scripts/album-cover-mosaic/` → `marketing/` (separate from pipeline)

### Mosaic Artwork Complete
**Completed:** Jan 25, 2026

Created mosaic artwork for both SOP and TAL Spotify playlists:
- **SOP:** Album art mosaic + episode art mosaic
- **TAL:** Episode art mosaic with tinted variants
- See `marketing/CLAUDE.md` for settings and outputs

### TAL Backfill Complete
**Completed:** Jan 2026

- ✅ 882 episodes scraped from thisamericanlife.org
- ✅ 1,094 songs extracted with episode credits
- ✅ 880 tracks matched to Spotify (80%)
- ✅ Synced to playlist: [TAL Songs](https://open.spotify.com/playlist/3d7fjfrTTKvrl7VHv5JzIz)
- 214 NOT_FOUND songs remaining (need manual review)

**Scripts:** `pipeline/scrapers/tal/`

---

## December 2025

### SOP Backfill Complete
**Completed:** Dec 21, 2025

- ✅ **462 episodes** initially scraped from switchedonpop.com (now 664 in Neon)
- ✅ **4,544 songs** initially extracted (now 4,417 after dedup, 4,043 matched)
- ✅ Playlist live with matched tracks
- ✅ Neon database with shows, episodes, songs tables
- ✅ Built `pipeline/spotify_match.py` for matching
- ✅ Built `pipeline/sync_playlist.py` for syncing
- ✅ Reviewed all LOW matches (200 songs processed)
- ⚠️ NOT_FOUND analyzed (534 songs) but fixes not executed — see `claude-plans/2025-12-21-song-review-progress.md`
- ✅ Playlist: [Every Song on Switched On Pop](https://open.spotify.com/playlist/0cEVeX4pdHf5RJOiTRzgxX)

**Match results:**
| Confidence | Count | % |
|------------|-------|---|
| HIGH | 3,251 | 71.5% |
| MEDIUM | 566 | 12.5% |
| MANUAL | 333 | 7.3% |
| NOT_FOUND | 376 | 8.3% |
| UNAVAILABLE | 18 | 0.4% |

**Files:**
- `src/lib/db.ts` - Neon client + queries
- `pipeline/spotify_match.py` - Match songs to Spotify
- `pipeline/sync_playlist.py` - Sync to playlist
- `claude-plans/prompts/sop/` - Scraping prompts

---

### Session Handoff Doc Created
**Completed:** Dec 12, 2025

Created `claude-plans/2025-12-12-session-handoff.md` for continuity between sessions.

---

## December 2025

### Spotify Bulk Actions MCP - Published
**Completed:** Dec 12, 2025
**Plan:** `claude-plans/2025-12-12-spotify-mcp-publish.md`

Moved Kevin's existing Spotify MCP to a public repo, updated it, and published to package registries. This tool powers the music → Spotify pipeline.

- ✅ Moved from festival-navigator to standalone repo
- ✅ Batch playlist creation with confidence scoring (HIGH/MEDIUM/LOW)
- ✅ Library exports (tracks, artists, albums)
- ✅ Human-in-the-loop CSV review workflow
- ✅ Published to PyPI: [spotify-bulk-actions-mcp](https://pypi.org/project/spotify-bulk-actions-mcp/)
- ✅ Listed on mcp.so
- ✅ Published to official MCP Registry (`io.github.khglynn/spotify-bulk-actions-mcp`)
- ✅ PR submitted to awesome-mcp-servers

**Repo:** [github.com/khglynn/spotify-bulk-actions-mcp](https://github.com/khglynn/spotify-bulk-actions-mcp)

---

### Project Planning & Setup
**Completed:** Dec 12, 2025
**Plan:** `claude-plans/2025-12-12-initial-plan.md`

- ✅ Created CLAUDE.md for project instructions
- ✅ Created project stack file at `~/DevKev/helper/project-stacks/pod-lists.md`
- ✅ Archived initial plan to `claude-plans/2025-12-12-initial-plan.md`
- ✅ Created context doc summarizing original research chats
- ✅ Decided on Vercel App (Next.js + Neon) over n8n
- ✅ Initialized Next.js project structure

---

### Original Research
**Completed:** Oct-Nov 2025 (before this repo)
**Docs:** `claude-plans/2025-12-12-project-context.md`

Two long chats with ChatGPT exploring:
- ✅ Scraping show notes vs transcription costs
- ✅ Destination platforms (Spotify, Notion, Trakt)
- ✅ Workflow orchestration options (n8n, Vercel, etc.)
- ✅ Show-specific extraction strategies

# list-maker Project Plan

*Created: 2025-12-12*
*Archived from: ~/.claude/plans/buzzing-seeking-meadow.md*

## Project Overview

**What it does:** An automated pipeline that extracts recommendations from podcast transcripts and routes them to the right platforms:
- **Music** → Spotify playlists (one per show)
- **Movies/TV** → Notion + Trakt (for cross-device watchlist)
- **Books** → Notion (with manual audiobook availability checking)
- **Apps/Products** → Notion

**Target podcasts:**
- **Switched On Pop** - all songs discussed (always positive)
- **Pop Culture Happy Hour** - music/TV/movies (positive sentiment, often at episode end)
- **AI Daily** - apps/platforms (positive sentiment only)

**Two phases:**
1. **Batch backfill** - ~500 hours of historical episodes
2. **Weekly runs** - new episodes as they drop

---

## Key Context from Previous Research

### Source Chats (read for full details)
- `/Users/KevinHG/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - CSV Playlist Creation Guide_67e70c24.md` - Initial exploration with ChatGPT about scraping podcast show notes
- `/Users/KevinHG/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - Workflow and transcript strategy_68eaedfe.md` - Deep dive on architecture with GPT, recommended n8n

### Previous Conclusions (from those chats)
| Component | Recommended | Cost/Notes |
|-----------|-------------|------------|
| Workflow orchestration | n8n | Self-host or cloud, visual + code |
| Transcription | OpenAI Whisper | ~$0.006/min = ~$180 for 500 hours |
| Content extraction | GPT-4/Claude | Sentiment filtering in prompt |
| Central database | Notion | Single mega-DB with Category property |
| Music destination | Spotify API | Direct playlist management |
| Movie/TV watchlist | Trakt API | Cross-device sync, open API |
| Books | Notion only | Manual audiobook availability |

### Updated Approach: Avoid Transcription When Possible

**Cascading data source logic (cheapest first):**
1. **Show notes/website** - Many podcasts list songs/recommendations on their site (FREE)
2. **Free transcripts** - Some podcasts provide transcripts in RSS or on website (FREE)
3. **Podcast transcript API** - Services like Taddy, Podscribe (cheaper than DIY)
4. **Self-transcription (Whisper)** - Last resort if nothing else works

**Shows with existing data:**
| Show | Abbreviation | Data Source | Notes |
|------|--------------|-------------|-------|
| Switched On Pop | SOP | Website show notes | Lists songs discussed per episode |
| This American Life | TAL | Website song credits | Song credits on each episode page |
| Pop Culture Happy Hour | PCHH | TBD | Check for transcripts |
| AI Daily | - | TBD | Check for transcripts |

---

## Approach Recommendation: Vercel App vs n8n

Kevin's intuition that a **Vercel-hosted app might be simpler** is worth exploring:

### Why Vercel App (Next.js) Could Be Better

| Factor | n8n | Vercel App |
|--------|-----|------------|
| **Learning curve** | New platform to learn | Kevin already uses Vercel |
| **Iteration speed** | GUI editing + JSON uploads | Claude Code edits directly |
| **Complex logic** | Function nodes (still code) | Native TypeScript, full flexibility |
| **Hosting** | Self-host or cloud ($) | Already have Vercel |
| **Cron/scheduling** | Built-in triggers | Vercel Cron or external trigger |
| **Debugging** | n8n execution logs | Console logs, familiar debugging |
| **Visual representation** | Yes (workflow canvas) | No (but code is readable) |

**Recommended: Vercel App (Next.js + Neon)**

Reasons:
1. You're already comfortable with Vercel from other projects
2. Claude Code can build and iterate on the logic directly - no platform middleman
3. Complex show-specific rules (sentiment, episode-end detection) are cleaner in code
4. Neon for database means proper SQL when needed, syncs to Notion
5. One less platform to maintain

---

## Implementation Plan

### Phase 1: Project Setup
- [x] Create `CLAUDE.md` for project-specific instructions
- [x] Create project stack file at `~/DevKev/tools/helper/project-stacks/list-maker.md`
- [x] Copy this plan to `claude-plans/2025-12-12-initial-plan.md` (archive for future sessions)
- [ ] Create OG context doc at `claude-plans/2025-12-12-project-context.md` (summary of those long chats)
- [ ] Initialize Next.js project with TypeScript
- [ ] Set up Neon database + schema
- [ ] Set up Notion integration (API key, target databases)
- [ ] Configure environment variables

### Phase 2: Core Pipeline (Switched On Pop)
Build the full pipeline for SOP - **no transcription needed** (website lists songs):
- [ ] Scrape SOP website for episode list + show notes
- [ ] Parse show notes to extract songs + artists (LLM or regex)
- [ ] Metadata enrichment (Spotify search to get track IDs)
- [ ] Write to Neon database
- [ ] Sync to Notion
- [ ] Add to Spotify playlist

### Phase 3: Backfill (SOP)
**Much cheaper now** - just scraping show notes, not transcribing:
- [ ] Scrape all historical SOP episodes from website
- [ ] Parse show notes in batches
- [ ] Match songs to Spotify (rate limit friendly)
- [ ] Create master "Switched On Pop" Spotify playlist
- [ ] Verify accuracy, tune parsing as needed

### Phase 4: Weekly Automation
- [ ] Vercel Cron job to check for new episodes
- [ ] Incremental processing (only new episodes)
- [ ] Notification when new songs added

### Phase 5: Expand to Other Shows (Later)
- [ ] TAL (This American Life) - website has song credits, similar approach to SOP
- [ ] PCHH (music/TV/movies, sentiment filtering) - check for free transcripts first
- [ ] AI Daily (apps/products) - check for free transcripts first
- [ ] Trakt integration for movies/TV
- [ ] Human review UI for low-confidence items
- [ ] Build cascading transcript logic for shows that need it

---

## Decisions Made (2025-12-12)

| Decision | Choice | Reasoning |
|----------|--------|-----------|
| Platform | **Vercel App (Next.js)** | Familiar, Claude Code iterates directly |
| Database | **Neon → sync to Notion** | Proper SQL for queries, Notion for UX |
| First show | **Switched On Pop** | Simplest - no sentiment filtering |
| Sequence | **Backfill first** | Capture historical value, tune prompts |

---

## Files to Create

```
/Users/KevinHG/DevKev/personal/list-maker/
├── CLAUDE.md                    # Project instructions
├── claude-plans/
│   └── 2025-12-12-project-context.md  # OG context doc (summary of those chats)
├── README.md                    # Basic readme
├── package.json
├── src/
│   ├── app/
│   │   └── api/                 # API routes
│   ├── lib/
│   │   ├── transcription.ts     # Whisper integration
│   │   ├── extraction.ts        # LLM extraction
│   │   ├── spotify.ts           # Spotify API
│   │   ├── notion.ts            # Notion API
│   │   └── shows/               # Show-specific extraction rules
│   └── types/
└── ...
```

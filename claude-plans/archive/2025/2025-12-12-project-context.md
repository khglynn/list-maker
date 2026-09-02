# list-maker - Original Project Context

*Created: 2025-12-12*
*Summary of original research chats so future sessions don't need to read the 600+ line originals*

## What This Document Is

Kevin explored this project idea with ChatGPT across two long conversations in late 2025. This is the condensed version with key decisions and reasoning preserved. If you need the full details, see the original chats linked below.

## Original Source Chats

1. **CSV Playlist Creation Guide** (with ChatGPT, ~March 2025)
   - Path: `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - CSV Playlist Creation Guide_67e70c24.md`
   - Focus: How to scrape Switched On Pop show notes to create Spotify playlists
   - Explored: Soundiiz, CSV imports, browser scraping extensions, n8n

2. **Workflow and Transcript Strategy** (with ChatGPT, ~October 2025)
   - Path: `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - Workflow and transcript strategy_68eaedfe.md`
   - Focus: Full architecture for multi-podcast recommendation extraction
   - Explored: n8n vs other workflow builders, transcription costs, destination platforms

---

## The Core Idea

Kevin listens to several podcasts that recommend music, movies, TV shows, books, and apps. He wants to automatically capture these recommendations and route them to the right places:

| Content Type | Destination |
|--------------|-------------|
| Music | Spotify playlists (one per show) |
| Movies | Notion + Letterboxd/Trakt |
| TV Shows | Notion + Trakt + one-click-play app |
| Books | Notion (with audiobook availability) |
| Apps/Products | Notion |

---

## Target Podcasts

| Show | What to Extract | Notes |
|------|-----------------|-------|
| **Switched On Pop** (SOP) | Songs + artists | Always positive sentiment - it's a music analysis show |
| **Pop Culture Happy Hour** (PCHH) | Music, TV, movies | Each host shares "what's making me happy" at episode end |
| **AI Daily** | Apps, platforms, tools | Tech podcast, only positive mentions |
| **This American Life** (TAL) | Songs | Song credits listed on website |

---

## Key Decisions from Original Research

### Platform: n8n → Changed to Vercel App

Original recommendation was n8n (visual workflow builder). Changed because:
- Kevin already uses Vercel
- Claude Code can iterate directly on code
- Complex logic (sentiment analysis, show-specific rules) is cleaner in TypeScript
- One less platform to learn/maintain

### Transcription: Cascading Logic

Original plan was Whisper for everything (~$180 for 500 hours). Optimized:
1. **Website scraping** (FREE) - SOP and TAL have song lists on their sites
2. **Free transcripts** - Some podcasts include transcripts
3. **Transcript API** - Taddy, Podscribe (cheaper than DIY)
4. **Whisper** - Last resort

### Database: Neon (Postgres)

Central source of truth. All data goes to Neon first, then syncs to:
- Notion (for human browsing/filtering)
- Spotify (for music playlists)
- Trakt (for movie/TV watchlists)

### Extraction: LLM-Based

Use Claude/GPT to:
- Parse show notes for songs/recommendations
- Apply sentiment filtering (only positive mentions)
- Handle show-specific formats (PCHH recs at end, etc.)

---

## Show-Specific Extraction Notes

### Switched On Pop (SOP)
- **Data source:** Website show notes list songs discussed
- **Sentiment:** Not needed - show is about celebrating music
- **Format:** "Songs Discussed" section in each episode page
- **Album rule:** If album mentioned without songs, get top 4 tracks from Spotify

### Pop Culture Happy Hour (PCHH)
- **Data source:** Needs transcripts (check if NPR provides)
- **Sentiment:** Required - only extract positive recommendations
- **Format:** "What's Making Me Happy" segment at episode end
- **Categories:** Music, TV, movies (variable per episode)

### AI Daily
- **Data source:** Needs transcripts
- **Sentiment:** Required - only apps/tools they endorse
- **Categories:** Apps, platforms, AI tools

### This American Life (TAL)
- **Data source:** Website has song credits per episode
- **Sentiment:** Not needed - it's just a credits list
- **Format:** Structured song credits section

---

## Technical Details from Research

### Spotify Integration
- Use Spotify Web API directly (no middleware needed)
- Create playlists per show
- Search API to match song+artist → track ID
- Rate limit friendly batch processing for backfill

### Notion Integration
- One mega-database with Category property (vs separate DBs)
- Properties: Title, Type, Source Podcast, Date, Quote, Sentiment
- Could add images (album art, movie posters)

### Trakt (for Movies/TV)
- Open API with watchlist endpoints
- Cross-device sync
- Better than Letterboxd (which has private API)
- Can integrate with JustWatch/Reelgood for "where to watch"

### Cost Estimates (from original research)
- Whisper transcription: ~$0.006/min (~$180 for 500 hours)
- But with website scraping for SOP/TAL: Most content is FREE
- Weekly ongoing: Minimal (few episodes, small content volume)

---

## What Was Decided NOT to Do

- **No n8n** - Vercel app is simpler for Kevin's workflow
- **No human transcription** - Too expensive ($37,500 for 500 hours at Rev rates)
- **No Goodreads** - Kevin doesn't like the UX
- **No Bubble/Zapier** - n8n was preferred, then Vercel app won over that

---

## Open Questions (to revisit later)

1. **PCHH transcripts** - Does NPR provide free transcripts? Check their RSS
2. **TV one-click-play** - Likewise is buggy. Test Reelgood on Fire Stick?
3. **Book audiobook availability** - No good aggregator API found. Manual for now
4. **Human-in-the-loop** - How much review friction is acceptable?

---

## For Future Claude Sessions

Read this doc first. Only read the original 600+ line chats if you need:
- Specific pricing details or API documentation excerpts
- The full reasoning behind a particular decision
- Context on a tool/platform that was evaluated but rejected

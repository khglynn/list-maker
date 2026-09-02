# list-maker Session Handoff

*Last updated: 2025-12-12*

## What's Done

### Phase 1: SOP Pipeline (COMPLETE)

| Component | Status | Notes |
|-----------|--------|-------|
| Neon database | ✓ | Project: `summer-grass-52363332` |
| Schema (shows, episodes, songs) | ✓ | SOP show inserted as id=1 |
| SOP scraper | ✓ | Handles both quote formats |
| API endpoint `/api/scrape` | ✓ | POST to scrape, GET to test parser |
| Date parsing | ✓ | Just fixed - looks BEFORE title now |

### Current Database State

- **3 episodes** scraped (Amy Allen, Sombr, Marc Rebillet)
- **15 songs** extracted
- Dates need re-scrape to populate (just fixed the parser)

### Files Created

```
src/lib/db.ts           - Neon client + queries
src/lib/scraper/sop.ts  - SOP website scraper
src/app/api/scrape/route.ts - API endpoint
.env.local              - DATABASE_URL + FIRECRAWL_API_KEY
```

## What's Next

### Immediate (this session)

1. **Re-scrape to get dates** - Clear episodes, run scraper again
2. **Test Spotify MCP** - Just added via `claude mcp add spotify-bulk-actions-mcp`
3. **Search songs on Spotify** - Use `batch_search_tracks`
4. **Create playlist** - "Switched On Pop" playlist with matched tracks

### Later

- Backfill more episodes (increase limit)
- Weekly automation (Vercel cron)
- Add more podcasts (TAL, PCHH)

## Quick Commands

```bash
# Start dev server
PORT=3001 npm run dev

# Scrape 3 episodes
curl -X POST http://localhost:3001/api/scrape -H "Content-Type: application/json" -d '{"limit": 3}'

# Check database
# Go to: https://console.neon.tech/app/projects/summer-grass-52363332
```

## Neon Connection

```
Project ID: summer-grass-52363332
Database: neondb
URL: https://console.neon.tech/app/projects/summer-grass-52363332
```

## Spotify MCP

Added to project config. Should be available after Claude Code restart.

Key tools:
- `batch_search_tracks` - Search songs with confidence scoring
- `create_playlist_from_search_results` - Create playlist from matches

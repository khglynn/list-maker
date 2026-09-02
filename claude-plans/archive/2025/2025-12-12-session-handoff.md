# pod-lists Session Handoff (archived)

*Last updated: 2025-12-12 (session 3)*

## What's Done

### Phase 1: SOP Pipeline (COMPLETE)

| Component | Status | Notes |
|-----------|--------|-------|
| Neon database | ✓ | Project: `summer-grass-52363332` |
| Schema (shows, episodes, songs) | ✓ | SOP show inserted as id=1 |
| SOP scraper | ✓ | Handles both quote formats |
| API endpoint `/api/scrape` | ✓ | POST to scrape, GET to test parser |
| Date parsing | ✓ | Fixed - parses dates correctly now |
| Re-scraped with dates | ✓ | Used Firecrawl MCP directly (faster than API) |
| Spotify MCP config | ✓ | Fixed bad config in ~/.claude.json |

### Current Database State

- **3 episodes** scraped:
  - Amy Allen (2025-12-12) - 11 songs
  - Sombr (2025-12-10) - 5 songs
  - Marc Rebillet (2025-12-01) - 0 songs (live performance, no featured tracks)
- **16 songs** total with dates populated
- Episode IDs: 12, 13, 14

### Files Created

```
src/lib/db.ts           - Neon client + queries
src/lib/scraper/sop.ts  - SOP website scraper
src/app/api/scrape/route.ts - API endpoint
.env.local              - DATABASE_URL + FIRECRAWL_API_KEY
```

## What's Next

### Immediate (next session)

1. **Search songs on Spotify** - Use `batch_search_tracks` with the 16 songs
2. **Create playlist** - "Switched On Pop" playlist with matched tracks
3. **Review confidence scores** - Handle LOW confidence matches

### Later (backfill phase)

- Architecture for ~500 episodes (pagination, batch processing)
- Weekly automation (Vercel cron)
- Add more podcasts (TAL, PCHH)

## Quick Commands

```bash
# Start dev server
PORT=3001 npm run dev

# Scrape episodes (note: API times out, use Firecrawl MCP directly instead)
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

**IMPORTANT:** MCPs must be added via `claude mcp add`, NOT by editing settings files!

```bash
# How it was added (already done):
claude mcp add --transport stdio spotify -- /Users/KevinHG/DevKev/personal/spotify-bulk-actions-mcp/venv/bin/python /Users/KevinHG/DevKev/personal/spotify-bulk-actions-mcp/spotify_bulk_actions_mcp/server.py
```

Source: `~/DevKev/personal/spotify-bulk-actions-mcp/`

Key tools:
- `batch_search_tracks` - Search songs with confidence scoring (HIGH/MEDIUM/LOW)
- `create_playlist_from_search_results` - Create playlist from matches
- `add_reviewed_tracks` - Add human-reviewed uncertain matches

## Playlist Details (decided this session)

- **Name:** Every Song on Switched On Pop
- **Description:** Every song discussed on Switched On Pop, automatically updated. Made by Kevin Glynn — buymeacoffee.com/kevinhg

## Notes

- API endpoint was timing out for scraping - used Firecrawl MCP directly instead (much faster)
- settings.local.json is NOT where MCPs go - that was a config mistake from earlier sessions

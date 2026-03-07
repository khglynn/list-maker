# pod-lists

Automated pipeline that extracts recommendations from podcasts and routes them to the right platforms.

## What It Does

Scrapes podcast websites and transcripts for recommendations (songs, apps, tools), matches them to destination platforms, and syncs to playlists and databases.

**Active Playlists:**
- [Every Song on Switched On Pop](https://open.spotify.com/playlist/0cEVeX4pdHf5RJOiTRzgxX) - 3,501 tracks
- [This American Life: Full Music Archive](https://open.spotify.com/playlist/3d7fjfrTTKvrl7VHv5JzIz) - 880 tracks

## Status

| Show | Type | Episodes | Items | Status |
|------|------|----------|-------|--------|
| Switched On Pop | Music | 462 | 4,544 songs (91% matched) | Live playlist |
| This American Life | Music | 882 | 1,094 songs (80% matched) | Live playlist |
| AI Daily Brief | Apps/Tools | 888 | 230 episodes extracted | Backfill stalled (quality gate) |
| PCHH | Mixed | - | - | Future |

See [ROADMAP.md](ROADMAP.md) for what's next.

## Structure

```
pipeline/              # Extraction and matching (Python)
  scrapers/sop/        # Switched On Pop website scraper
  scrapers/tal/        # This American Life website scraper
  scrapers/ai_daily/   # AI Daily transcript entity extraction
  scrapers/taddy/      # Taddy API transcript importer
marketing/             # Playlist cover art (mosaic generator)
web/                   # Next.js app (future automation UI)
claude-plans/          # Session plans and prompts
codex-notes/           # AI Daily extraction batch artifacts
```

## Running the Pipeline

```bash
cd pipeline
source venv/bin/activate

# Match unmatched songs to Spotify
python spotify_match.py --show-id 1  # SOP
python spotify_match.py --show-id 2  # TAL

# Sync matched songs to playlist
python sync_playlist.py --show-id 1

# AI Daily entity extraction (see pipeline/scrapers/ai_daily/README.md)
```

See [pipeline/README.md](pipeline/README.md) for full documentation.

## Tech Stack

- **Database:** Neon (Postgres) - source of truth
- **Matching:** Custom [Spotify MCP](https://github.com/khglynn/spotify-bulk-actions-mcp)
- **Scraping:** Firecrawl + Claude
- **Transcripts:** Taddy API
- **Extraction:** OpenAI (gpt-4.1-mini for entity extraction)

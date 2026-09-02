# Spotify Bulk Actions MCP - Publishing Plan

*Created: 2025-12-12*
*Mini-plan for publishing Kevin's Spotify MCP to a public repo*

## The MCP

**Current location:** `~/DevKev/personal/festival-navigator/spotify-mcp/`
**New repo name:** `spotify-bulk-actions-mcp`
**New location (after publish):** Will update MCP path in `~/.claude/settings.local.json`

## What It Does (18 tools)

### Library Analysis
- `get_followed_artists` - Get all artists you follow
- `get_saved_tracks` - Get all liked songs (handles 10k+)
- `get_library_artists` - Artists ranked by saved song count
- `get_albums_by_song_count` - Albums with N+ saved songs (vinyl shopping!)
- `export_library_summary` - Complete library export

### Search
- `search_track` - Single track search
- `search_track_fuzzy` - Broader search when exact fails
- `batch_search_tracks` - Bulk search with confidence scores (HIGH/MEDIUM/LOW)
- `get_track_preview_url` - 30-second preview URL

### Playlists
- `create_playlist` - Create new playlist
- `add_tracks_to_playlist` - Add tracks to existing playlist
- `import_and_create_playlist` - Full CSV → playlist workflow
- `create_playlist_from_search_results` - Create from batch search
- `add_reviewed_tracks` - Add human-reviewed corrections

### Utilities
- `parse_song_list_csv` - Validate song CSV
- `export_review_csv` - Export uncertain matches for review

## Key Differentiators

1. **Confidence scoring** - HIGH/MEDIUM/LOW on song matches
2. **Human-in-the-loop** - Exports uncertain matches to CSV for review
3. **Bulk operations** - Handles 500+ songs efficiently
4. **Library exports** - Complete library data extraction
5. **Vinyl shopping** - Find albums where you have 6+ saved songs

## Publishing Checklist

- [ ] Create GitHub repo `spotify-bulk-actions-mcp`
- [ ] Update README with:
  - [ ] Better description highlighting differentiators
  - [ ] Buy Me a Coffee link: https://buymeacoffee.com/kevinhg
  - [ ] Installation instructions
  - [ ] Example workflows
- [ ] Push code to repo
- [ ] Add GitHub topics: `mcp`, `spotify`, `claude`, `playlist`, `bulk-operations`
- [ ] Update `~/.claude/settings.local.json` with new path
- [ ] Submit to MCP aggregators:
  - [ ] mcp.so (popular aggregator)
  - [ ] Awesome MCP lists on GitHub
  - [ ] Anthropic community resources (if applicable)

## Links to Include

- **Buy Me a Coffee:** https://buymeacoffee.com/kevinhg
- **Kevin's GitHub:** (will be repo owner)

## Notes for Playlist Descriptions

When creating Spotify playlists with this MCP, include in description:
> Created with Spotify Bulk Actions MCP
> Support the creator: https://buymeacoffee.com/kevinhg

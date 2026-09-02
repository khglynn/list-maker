# Plan: Reorganize Prompts + Build Spotify Matching Script

*Created 2025-12-20, Updated 2025-12-20 (ultrathink review)*

## Quick Summary

**What we're building:** A Python script (`scripts/spotify_match.py`) to replace manual Claude Code Spotify matching. Reuses the same confidence algorithm from our Spotify MCP.

**Why:** 1,150 songs remaining + future shows. Script is faster, cheaper, and resumable.

**Key decisions:**
- Script for matching/backfill → LLM for review/judgment
- Shared song prompts (`_prefix`) → Per-show episode prompts
- Reuse MCP's OAuth tokens (no new auth setup)
- Resume via DB state (no separate state file)

**Est. runtime:** ~1,150 songs × 0.3s = ~6 minutes for remaining SOP songs

---

## Goal

1. Separate tasks into SCRIPT vs LLM in our documentation
2. Build a Python script for Spotify matching (remaining ~1,150 songs + future shows)
3. Update prompt files and roadmap to reflect this new approach
4. Restructure prompt files: shared (songs) vs show-specific (episodes)

---

## Task Classification (Final)

| Task | DB Table | Show-Specific? | Type | Reason |
|------|----------|----------------|------|--------|
| **URL discovery/setup** | `episodes` | YES | SCRIPT | Different site patterns per show |
| **Episode scraping** | `episodes` | YES | LLM | Different formats, parsing rules |
| **Description extraction** | `episodes` | YES | LLM | Different structures per show |
| **Song matching** | `songs` | NO | SCRIPT | Unified table, pure API + SQL |
| **Metadata backfill** | `songs` | NO | SCRIPT | Unified table, API + SQL |
| **Playlist sync** | `songs` | YES | SCRIPT | Different playlist per show |
| **Review LOW/NOT_FOUND** | `songs` | NO | LLM | Fuzzy search, decisions |
| **Quality checks** | `songs` | NO | LLM | Compare data, flag issues |

---

## Spotify API Rate Limits

- **Mode:** Development (lower limits)
- **Behavior:** Rolling 30-second window, 429 + Retry-After header
- **Mitigation:** 0.2-0.5s delay between batches, respect Retry-After, log progress for resume

---

## Implementation Steps

### Step 1: Create `_task-types.md` reference doc

**File:** `claude-plans/prompts/_task-types.md`

Contents:
- Script vs LLM task definitions
- When to use each
- Rate limit notes
- Links to specific prompts

### Step 2: Build Python matching script

**File:** `scripts/spotify_match.py`

#### Dependencies (`scripts/requirements.txt`)
```
spotipy>=2.23.0
psycopg2-binary>=2.9.0
python-dotenv>=1.0.0
thefuzz>=0.22.0
python-Levenshtein>=0.23.0  # 10x faster fuzzy matching
```

#### Environment Variables

**Approach:** Share the MCP's existing `.env` file (no duplication)

The script will:
1. Load Spotify credentials from `~/DevKev/personal/spotify-bulk-actions-mcp/.env`
2. Only need one new env var for Neon (can add to MCP's .env or use a separate file)

```python
# In script
load_dotenv(os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.env"))
# or add NEON_DATABASE_URL to that file
```

**Add to MCP's .env:**
```
NEON_DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require
```

**Why this approach:** One source of truth for credentials. The MCP's auth tokens are already working.

#### Confidence Scoring Algorithm (from MCP)
```python
def calculate_match_confidence(query_title, query_artist, result_title, result_artists):
    """
    Uses thefuzz.fuzz.ratio for string similarity.

    title_score = fuzz.ratio(query_title.lower(), result_title.lower()) / 100
    artist_score = max(fuzz.ratio(query_artist.lower(), artist.lower()) for artist in result_artists) / 100

    confidence = (title_score * 0.55) + (artist_score * 0.45)

    Returns float 0.0-1.0
    """
```

| Level | Threshold | DB Value |
|-------|-----------|----------|
| HIGH | ≥0.90 | `'HIGH'` |
| MEDIUM | 0.70-0.89 | `'MEDIUM'` |
| LOW | <0.70 | `'LOW'` |
| NOT_FOUND | No results | `'NOT_FOUND'` |

#### CLI Interface
```bash
# Basic usage
python scripts/spotify_match.py --show-id 1 --limit 100

# Dry run (no DB writes)
python scripts/spotify_match.py --show-id 1 --limit 10 --dry-run

# All shows (no filter)
python scripts/spotify_match.py --limit 500

# Full run, all unmatched
python scripts/spotify_match.py --show-id 1
```

#### Script Structure
```python
#!/usr/bin/env python3
"""Spotify song matching script for list-maker project."""

import argparse, os, sys, time
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import execute_values
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from thefuzz import fuzz
from dotenv import load_dotenv

# Constants
BATCH_SIZE = 50  # Songs per DB query
API_DELAY = 0.3  # Seconds between Spotify API calls
MAX_RETRIES = 3  # For rate limit handling

def main():
    args = parse_args()
    load_dotenv()

    # Initialize clients
    sp = get_spotify_client()
    conn = get_db_connection()

    # Main loop
    processed = 0
    while True:
        songs = fetch_unmatched_songs(conn, args.show_id, BATCH_SIZE)
        if not songs:
            break

        results = match_songs_batch(sp, songs)

        if not args.dry_run:
            save_results(conn, results)

        processed += len(songs)
        print(f"Processed {processed} songs...")

        if args.limit and processed >= args.limit:
            break

    print_summary(processed, results)
    conn.close()

def fetch_unmatched_songs(conn, show_id: Optional[int], limit: int) -> List[Dict]:
    """Query songs with spotify_track_id IS NULL."""
    query = """
        SELECT s.id, s.title, s.artist
        FROM songs s
        JOIN episodes e ON s.episode_id = e.id
        WHERE s.spotify_track_id IS NULL
          AND s.spotify_match_confidence IS NULL
    """
    if show_id:
        query += f" AND e.show_id = {show_id}"
    query += f" ORDER BY s.id LIMIT {limit}"

    with conn.cursor() as cur:
        cur.execute(query)
        return [{"id": r[0], "title": r[1], "artist": r[2]} for r in cur.fetchall()]

def match_songs_batch(sp, songs: List[Dict]) -> Dict[str, List]:
    """Search Spotify for each song, return categorized results."""
    results = {"high": [], "medium": [], "low": [], "not_found": []}

    for song in songs:
        try:
            match = search_and_score(sp, song["title"], song["artist"])
            if match:
                match["song_id"] = song["id"]
                category = get_confidence_category(match["confidence"])
                results[category].append(match)
            else:
                results["not_found"].append(song["id"])
        except Exception as e:
            print(f"Error matching {song['title']}: {e}", file=sys.stderr)
            results["not_found"].append(song["id"])

        time.sleep(API_DELAY)

    return results

def save_results(conn, results: Dict[str, List]):
    """Bulk UPDATE songs table with match data."""
    # Build VALUES list for matched songs
    # Use execute_values for efficient bulk update
    # Mark NOT_FOUND songs with just confidence level
```

#### Error Handling & Safety

**Failure behavior (visible, not silent):**
```
ERROR: Song 1234 "Bad Title" - Spotify returned 404
  → Marked as NOT_FOUND, continuing...

ERROR: Rate limited (429) - waiting 32 seconds...
  → Retrying...

CRITICAL: Database connection lost
  → Saving progress log, exiting safely
```

| Error | Response | Recovery |
|-------|----------|----------|
| 429 Rate Limit | Wait for Retry-After + 1s | Auto-retry up to 3x |
| Network Error | Log error message | Retry 3x, then mark NOT_FOUND |
| DB Error | Stop immediately | Uncommitted batch lost (max 50 songs), but no data corruption |
| Invalid Response | Log and continue | Mark as NOT_FOUND |
| Bad credentials | Fail fast at startup | Fix .env, re-run |

**Database safety (non-destructive writes only):**
- Script only does UPDATE, never DELETE or INSERT on songs table
- Each UPDATE only touches columns: `spotify_track_id`, `spotify_match_confidence`, `album`, `spotify_web_url`, `spotify_popularity`, `spotify_title`, `spotify_artist`
- Original data (`title`, `artist`, `episode_id`) is NEVER modified
- If something goes wrong, we can reset by running:
  ```sql
  -- Reset a batch (example: songs 2000-2050)
  UPDATE songs SET
    spotify_track_id = NULL,
    spotify_match_confidence = NULL,
    album = NULL,
    spotify_web_url = NULL,
    spotify_popularity = NULL,
    spotify_title = NULL,
    spotify_artist = NULL
  WHERE id BETWEEN 2000 AND 2050;
  ```

**Pre-run safety check:**
Before the full run, script outputs:
```
Found 1,147 unmatched songs (show_id=1)
Ready to process in batches of 50 (23 batches)
Press Enter to continue or Ctrl+C to abort...
```

**Progress log file:**
Script writes `scripts/match_progress.log`:
```
2025-12-20 14:30:01 | Batch 1 | Songs 1901-1950 | HIGH:38 MED:8 LOW:2 NF:2
2025-12-20 14:30:18 | Batch 2 | Songs 1951-2000 | HIGH:41 MED:5 LOW:3 NF:1
```
If it crashes, you can see exactly where it stopped.

#### Resume Capability
**Built-in:** Database tracks progress via `spotify_track_id IS NULL AND spotify_match_confidence IS NULL`
- No separate state file needed
- Safe to Ctrl+C and restart anytime
- Each batch commits before next query

#### Output
```
Processed 50 songs...
  HIGH: 38, MEDIUM: 7, LOW: 3, NOT_FOUND: 2
Processed 100 songs...
  HIGH: 42, MEDIUM: 5, LOW: 1, NOT_FOUND: 2

=== COMPLETE ===
Total processed: 100
HIGH: 80 (80%)
MEDIUM: 12 (12%)
LOW: 4 (4%)
NOT_FOUND: 4 (4%)
```

### Step 3: Update `sop/spotify-matching.md`

Add sections:
- Note that batch matching is now scripted
- Keep manual review workflow (LLM task)
- Add "Review LOW/NOT_FOUND" section for Claude Code

### Step 4: Update `ROADMAP.md`

- Phase 2: Note that matching is scripted
- Add rate limit consideration
- Add LLM review task after scripted matching
- Add to Future Ideas: **Public database export** - Export to SQLite or build API for public access (Neon is for dev, not public sharing)

### Step 5: Restructure prompt files

- Move `sop/spotify-matching.md` → `_spotify-matching.md` (shared)
- Delete `sop/match-songs.md` and `tal/match-songs.md` (redundant)
- Create `_spotify-review.md` for LLM review workflow
- Keep episode prompts per-show (different parsing rules)

---

## New Prompt File Structure

```
claude-plans/prompts/
├── _task-types.md              # Reference: script vs LLM tasks
├── _spotify-matching.md        # SHARED: song matching (all shows)
├── _spotify-review.md          # SHARED: LLM review of LOW/NOT_FOUND
├── _playlist-sync.md           # SHARED: add to playlist (pass show_id)
├── sop/
│   ├── scrape-episodes.md      # SOP-specific episode parsing
│   ├── setup-url-tracking.md   # SOP URL discovery
│   └── backfill-descriptions.md # SOP description cleanup
└── tal/
    ├── scrape-episodes.md      # TAL-specific episode parsing
    ├── setup-url-tracking.md   # TAL URL discovery
    └── backfill-issues.md      # TAL cleanup
```

**Key insight:** Song-related prompts are shared (prefixed with `_`), episode prompts stay per-show.

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `scripts/spotify_match.py` | CREATE - main matching script |
| `scripts/requirements.txt` | CREATE - dependencies |
| `scripts/.env.example` | CREATE - env template |
| `claude-plans/prompts/_task-types.md` | CREATE - reference doc |
| `claude-plans/prompts/_spotify-matching.md` | MOVE from sop/, make shared |
| `claude-plans/prompts/_spotify-review.md` | CREATE - LLM review workflow |
| `sop/match-songs.md` | DELETE - redundant |
| `tal/match-songs.md` | DELETE - replaced by shared |
| `ROADMAP.md` | UPDATE - reflect script approach |

---

## Current State (for context after compaction)

- **Neon Project:** `summer-grass-52363332`
- **SOP Show ID:** 1
- **Progress:** 1,853 / 3,000 songs matched (62%)
- **Remaining:** ~1,150 songs to match
- **Playlist:** https://open.spotify.com/playlist/0cEVeX4pdHf5RJOiTRzgxX

**Database schema (songs table):**
```sql
id, episode_id, title, artist,
spotify_track_id, spotify_match_confidence,
album, spotify_web_url, spotify_popularity,
spotify_title, spotify_artist, added_to_playlist
```

**Confidence levels:** HIGH, MEDIUM, LOW, NOT_FOUND, MANUAL

---

## TAL Scrape Dependency

**Can we proceed while TAL scrape is running?** YES

- TAL scrape writes to `episodes` and `songs` tables
- Our script reads from `songs` table (different rows - unmatched songs)
- Prompt file changes don't affect running scrapes
- No conflicts expected

The script will automatically pick up TAL songs as they're added (queries `WHERE spotify_track_id IS NULL`).

---

## Potential Gotchas & Mitigations

| Issue | Risk | Mitigation |
|-------|------|------------|
| **Spotify OAuth expiry** | Token expires mid-run | spotipy auto-refreshes; use `.cache` file location |
| **Neon cold starts** | First query slow (~2s) | Not a problem for batch ops |
| **Unicode in titles** | Matching fails | thefuzz handles unicode well; test with "Björk" |
| **Quotes in titles** | SQL injection risk | Use parameterized queries, never string concat |
| **Very long titles** | Spotify search truncates | Search uses first ~100 chars; OK for most cases |
| **"ft." in artist** | Lower match scores | Accept MEDIUM confidence; script handles automatically |
| **Rate limit bursts** | 429 errors | 0.3s delay + Retry-After handling |
| **MCP vs Script auth** | Separate token caches | Point to same `.spotify_cache/` directory |

### Auth Token Sharing

The script can reuse the MCP's OAuth tokens:
```python
# Point to existing MCP cache
SPOTIFY_CACHE_PATH = os.path.expanduser("~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache")
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    cache_path=SPOTIFY_CACHE_PATH,
    ...
))
```

If tokens expire, the user can re-run `python setup_auth.py` in the MCP directory.

---

## Testing Strategy

### 1. Dry Run Test (10 songs)
```bash
python scripts/spotify_match.py --show-id 1 --limit 10 --dry-run
```
Verify: Output shows matches, no DB changes.

### 2. Small Live Test (10 songs)
```bash
python scripts/spotify_match.py --show-id 1 --limit 10
```
Verify: Check database for 10 updated songs.

### 3. Verify Resume
- Run with `--limit 50`
- Ctrl+C after ~25 songs
- Run again with `--limit 50`
- Should process remaining ~25, not restart

### 4. Rate Limit Test
- Reduce delay to 0.05s temporarily
- Monitor for 429 handling
- Verify it backs off and retries

---

## Order of Execution

1. Create `scripts/` folder structure + requirements
2. Build `spotify_match.py` with core functionality
3. Test on small batch (10 songs, dry-run)
4. Test live on 10 songs
5. **Create Neon branch "pre-script-backup"** (safety checkpoint)
6. Run full matching for remaining ~1,150 SOP songs
7. If successful, delete backup branch
8. Restructure prompt files (move/delete/create)
9. Update ROADMAP.md

---

## Post-Script Tasks (LLM)

After script completes matching, these remain as LLM tasks:
1. **Review LOW matches** - Use fuzzy search, decide keep/reject
2. **Research NOT_FOUND** - May be covers, live versions, or unavailable
3. **Playlist sync** - Add HIGH+MEDIUM to Spotify playlist
4. **Metadata backfill** - For songs matched before we added those columns

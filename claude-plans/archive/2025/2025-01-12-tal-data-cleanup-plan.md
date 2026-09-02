# TAL Song Data Cleanup - Plan

*Created: 2025-01-12*
*Updated: 2026-01-12 (ready for 404 fix phase)*

---

## Current State (Final - Session 2 Complete)

### Playlist: LIVE
- **Playlist ID:** `3d7fjfrTTKvrl7VHv5JzIz`
- **Name:** "This American Life: Full Music Archive"
- **Description:** "778 songs across 882 episodes"
- **Tracks synced:** 805

### Database State
| Metric | Count |
|--------|-------|
| Total TAL episodes | 882 |
| JSON files scraped | 882 (100%) |
| **404s (unfixable)** | **1** (episode 481) |
| Total songs in DB | 1,094 |
| Songs with Spotify match | 880 (80%) |

### Spotify Match Breakdown
| Confidence | Count |
|------------|-------|
| HIGH | 771 |
| MEDIUM | 74 |
| LOW | 35 |
| NOT_FOUND | 214 |

---

## Completed This Session

1. Cleaned song titles (stripped quotes, fixed formatting)
2. Ran fill script - added missing songs from parsed JSON
3. Spotify matching - matched 761 songs initially
4. Fuzzy search - found 26 additional matches
5. Updated playlist metadata
6. **Synced 787 tracks to playlist - LIVE**

### Session 2 (2026-01-12 continued)

7. Created `tal_fix_404s.py` script for automated 404 fixing
8. **Fixed 88 of 89 404 episodes** (1 failed: episode 481)
   - Most had wrong URL slugs - auto-corrected via search
   - Many were just temporary 404s during original scrape
9. Discovered **~435 missing JSON files** - only 447 JSONs for 882 episodes
   - JSON files cover db_ids 553-1338 but with gaps
   - Episode 877 (db_id 748) was never scraped!
10. Manually scraped episode 877 - found song: **"Tubthumping" by Chumbawumba**
11. Ran `tal_fill_songs.py` - **inserted 112 new songs**
12. Ran Spotify matching - **81 new matches** (69 HIGH, 7 MEDIUM, 5 LOW)
13. **Synced 70 new tracks to playlist**

### Session 2 Continued: Full Episode Scrape

14. Created `tal_scrape_missing.py` script
15. **Scraped all 435 missing episodes** (100% success rate!)
16. Ran `tal_fill_songs.py` - **35 more songs added**, 18 dupes removed
17. Ran Spotify matching - **12 more matches** (9 HIGH, 3 MEDIUM)
18. **Synced 5 more tracks to playlist**

### Final Stats After Session 2

| Metric | Start | After 404 Fix | After Full Scrape | Total Change |
|--------|-------|---------------|-------------------|--------------|
| JSON files | 447 | 447 | **882** | +435 |
| Total songs | 967 | 1,077 | **1,094** | +127 |
| Matched | 787 | 868 | **880** | +93 |
| HIGH | 693 | 762 | **771** | +78 |
| MEDIUM | 64 | 71 | **74** | +10 |
| LOW | 30 | 35 | **35** | +5 |
| NOT_FOUND | 180 | 209 | **214** | +34 |
| Playlist tracks | 730 | 773 | **805** | +75 |

---

## Next Phase: Fix 404s and Missing Songs

### Problem Identified
User spot-checked 404 episodes and found:

1. **Many 404s are fixable** - Pages exist now, or URL slugs were wrong
   - Example: `358/the-edge-of-the-edge` should be `358/social-engineering`
   - Some pages that 404'd during original scrape now work

2. **Some "no songs" episodes have songs** - Parser missed them
   - Example: Episode 877 "The Making Of" has songs on page but not in DB

### Tested Approaches

| Approach | Result |
|----------|--------|
| Bare episode URL (`/358`) | 404 - no redirect |
| Correct slug (`/358/social-engineering`) | Works |
| **Firecrawl search** (`site:thisamericanlife.org 358`) | **Finds correct URL** |
| RSS feed (`/podcast/rss.xml`) | Only recent ~10 weeks |

### Fix Strategy

#### Phase 1: Fix 89 404 Episodes

For each 404 episode in DB:

1. **Extract episode number from URL** (e.g., `358` from `/358/the-edge-of-the-edge`)
2. **Search TAL for correct URL:**
   ```
   mcp__firecrawl__firecrawl_search
   query: "site:thisamericanlife.org {episode_number}"
   limit: 1
   ```
3. **Scrape the correct URL:**
   ```
   mcp__firecrawl__firecrawl_scrape
   url: {correct_url}
   formats: ["markdown"]
   ```
4. **Update DB with correct URL and scraped content**
5. **Parse songs from new content**

#### Phase 2: Re-check "No Songs" Episodes

Some episodes have page data but no songs parsed. Re-parse these:
- Episode 877 "The Making Of" (confirmed has songs)
- Other episodes in the "no songs" list

Run parser on existing content or re-scrape if needed.

#### Phase 3: Spotify Match & Sync

1. Run `spotify_match.py` on new songs
2. Add matched tracks to playlist

### Important: Skip Recent Episodes

**Do NOT scrape episodes after Dec 12, 2025 (episode 877).**
These will be used to test the cron job we're building later.

Recent episodes to skip:
- 878 "New Lore Drop" (Jan 9, 2026)
- Any others after 877

---

## Key IDs and Resources

| Resource | Value |
|----------|-------|
| TAL show_id | 2 |
| TAL playlist | `3d7fjfrTTKvrl7VHv5JzIz` |
| SOP playlist (reference) | `0cEVeX4pdHf5RJOiTRzgxX` |
| Neon project | `summer-grass-52363332` |
| Fetched JSON dir | `scripts/fetched/tal/` |
| Latest episode in DB | 877 (Dec 12, 2025) |

---

## 404 Episodes to Fix

Query to get all 404 episodes:
```sql
SELECT id, url
FROM episodes
WHERE show_id = 2 AND title IS NULL
ORDER BY id;
```

Sample 404s (89 total):
- `/31/when-you-talk-about-music`
- `/310/habeas-schmabeas`
- `/317/unconditional-love`
- `/358/the-edge-of-the-edge` → should be `/358/social-engineering`
- `/510/fiasco`

---

## "No Songs" Episodes to Re-check

Query to get episodes with data but no songs:
```sql
SELECT e.id, e.episode_number, e.title, e.url
FROM episodes e
LEFT JOIN songs s ON e.id = s.episode_id
WHERE e.show_id = 2 AND e.title IS NOT NULL AND s.id IS NULL
ORDER BY e.episode_number DESC;
```

Sample (172 total, but many legitimately have no songs):
- 877 "The Making Of" - **confirmed has songs, needs re-parse**
- 872 "Winners"
- 868 "The Hand That Rocks The Gavel"

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `tal_parse.py` | Parse episode JSON, extract songs |
| `tal_fill_songs.py` | Fill missing songs to DB, cleanup quotes |
| `tal_fix_404s.py` | **NEW** - Fix 404 episodes (search → scrape → save) |
| `spotify_match.py` | Match songs to Spotify |
| `sync_playlist.py` | Sync matched songs to Spotify playlist |

---

## New Discovery: Missing JSON Files

**Problem:** Only 447 JSON files exist for 882 episodes (~435 missing)

The original scrape only created JSONs for a subset of episodes. Many episodes with data in the DB don't have corresponding JSON files - so songs can't be parsed from them.

**Investigation needed:**
- Which episodes have DB data but no JSON?
- Are these from an older scrape that saved directly to DB?
- Do we need to re-scrape them all?

**Query to find missing:**
```sql
-- Get all TAL episode db_ids
SELECT id FROM episodes WHERE show_id = 2 ORDER BY id;
-- Compare against: ls scripts/fetched/tal/*.json | xargs basename | sed 's/.json//'
```

---

## After This Phase

Once 404s are fixed and songs parsed:
1. Update playlist description with new counts
2. Sync new tracks to playlist
3. Build cron job for weekly episode checking (using 878+ as test cases)

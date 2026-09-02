# Song Review Progress

*Created 2025-12-21*

## Current Status

**SOP Playlist:** `0cEVeX4pdHf5RJOiTRzgxX`
**Neon Project:** `summer-grass-52363332`
**Show ID:** 1

### Match Confidence Breakdown (as of Dec 21, 2025)

| Confidence | Count | Notes |
|------------|-------|-------|
| HIGH | 3,251 | Auto-matched, >= 90% confidence |
| MEDIUM | 566 | Auto-matched, 70-89% confidence |
| MANUAL | 193 | Reviewed and approved this session |
| NOT_FOUND | 534 | Need processing |
| LOW | 0 | All processed |

**Total:** 4,544 songs

---

## What We Completed This Session

1. **Processed all 200 LOW matches** by category:
   - `feat_format` (97) - Songs with "(feat. X)" or "(with X)" → batch approved
   - `null_metadata` (34) - Had track ID but missing title/artist → verified correct, approved
   - `remaster` (4) - Songs with "Remaster" label → approved
   - `other` (65) - Mixed bag, reviewed individually

2. **Batch approved 181 correct matches** → set to MANUAL

3. **Reset 19 wrong matches** to NOT_FOUND, then fuzzy searched:
   - Fixed 12 of 19 successfully
   - Remaining 7 are genuinely unfindable (wrong source data)

---

## NOT_FOUND Analysis (534 songs)

### Category 1: "ft./feat." Format Mismatch (~130 songs, 24%)
**FIXABLE - Priority 1**

The source data has featured artists like "Drake ft. Rihanna" but Spotify uses "Drake" with "(feat. Rihanna)" in the title.

**Query to find them:**
```sql
SELECT s.id, s.title, s.artist
FROM songs s
JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 1
  AND s.spotify_match_confidence = 'NOT_FOUND'
  AND (s.artist LIKE '%ft.%' OR s.artist LIKE '%ft %'
       OR s.artist LIKE '%feat.%' OR s.artist LIKE '%feat %')
ORDER BY s.id
```

**Fix approach:**
1. Extract primary artist (everything before "ft." or "feat.")
2. Fuzzy search with just primary artist + title
3. Batch update matches

### Category 2: Major Artists That Should Work (~80 songs, 15%)
**FIXABLE - Priority 2**

| Artist | Count | Likely Issue |
|--------|-------|--------------|
| Sylvan Esso | 11 | Album/single version differences |
| Lady Gaga | 5 | Deep cuts or remixes |
| Coldplay | 4 | Live versions or collaborations |
| Taylor Swift | 2 | Vault tracks or features |
| Lil Nas X | 3 | Remixes or unreleased |
| Leon Bridges and Khruangbin | 3 | Collaboration formatting |
| Charli XCX | 3 | Various issues |

**Fix:** Manual fuzzy search, should find most.

### Category 3: Obscure/Niche Artists (~100 songs, 19%)
**LOW PRIORITY - May not exist on Spotify**

- Gideon and Hubcap (3) - Podcast-specific music
- Unknown (7) - No artist attribution
- DJ Screw (5) - Houston mixtape legend, limited streaming
- Charlie Harding - Podcast host original music
- World music/ethnomusicology recordings

### Category 4: Classical with Bad Metadata (~30 songs, 6%)
**MEDIUM PRIORITY - Needs manual research**

- "Laureate Dominum" by Mozart (probably "Laudate Dominum")
- Various John Williams film scores
- Opera arias and symphony movements without BWV/K numbers

### Category 5: Removed/Unavailable Artists (~10 songs, 2%)
**Mark as UNAVAILABLE**

- Gary Glitter (2) - Removed from Spotify due to conviction
- Regional restrictions

```sql
UPDATE songs SET spotify_match_confidence = 'UNAVAILABLE'
WHERE artist LIKE '%Gary Glitter%'
```

### Category 6: NPR/Podcast-Specific (~15 songs, 3%)
**Mark as UNAVAILABLE**

- BJ Leiderman (4) - NPR theme music composer
- Original podcast compositions

### Category 7: Duplicates (~50+ songs)
Same song appears multiple times with slightly different formatting.

---

## Next Steps (Priority Order)

### Step 1: Batch Fix "ft./feat." Format (~130 songs)

```python
# Pseudocode for fix
for song in songs_with_ft_feat:
    primary_artist = extract_before_ft(song.artist)
    results = spotify.search_fuzzy(song.title, primary_artist)
    if results and results[0].confidence > 0.8:
        update_song(song.id, results[0])
```

### Step 2: Manual Search Major Artists (~80 songs)

Query each artist group:
```sql
SELECT id, title, artist FROM songs
WHERE spotify_match_confidence = 'NOT_FOUND'
  AND artist LIKE '%Sylvan Esso%'
```

Then use `mcp__spotify__search_track_fuzzy` for each.

### Step 3: Mark Unavailable (~25 songs)

```sql
-- Gary Glitter
UPDATE songs SET spotify_match_confidence = 'UNAVAILABLE'
WHERE artist LIKE '%Gary Glitter%' AND spotify_match_confidence = 'NOT_FOUND';

-- BJ Leiderman (NPR themes)
UPDATE songs SET spotify_match_confidence = 'UNAVAILABLE'
WHERE artist LIKE '%BJ Leiderman%' AND spotify_match_confidence = 'NOT_FOUND';
```

### Step 4: Sync to Spotify Playlist

After fixing what we can:
```sql
-- Get all matched songs for playlist sync
SELECT DISTINCT spotify_track_id
FROM songs s
JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 1
  AND spotify_track_id IS NOT NULL
  AND spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')
```

Then use `mcp__spotify__add_tracks_to_playlist` with playlist ID `0cEVeX4pdHf5RJOiTRzgxX`.

---

## Key SQL Queries

### Check current status
```sql
SELECT spotify_match_confidence, COUNT(*) as count
FROM songs s
JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 1
GROUP BY spotify_match_confidence
ORDER BY count DESC
```

### View NOT_FOUND by artist frequency
```sql
SELECT artist, COUNT(*) as count
FROM songs s
JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 1 AND s.spotify_match_confidence = 'NOT_FOUND'
GROUP BY artist
ORDER BY count DESC
LIMIT 30
```

### Sample NOT_FOUND songs
```sql
SELECT s.id, s.title, s.artist
FROM songs s
JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 1 AND s.spotify_match_confidence = 'NOT_FOUND'
ORDER BY s.id
LIMIT 50
```

---

## Files Reference

- **Matching script:** `scripts/spotify_match.py`
- **Review prompt:** `claude-plans/prompts/_spotify-review.md`
- **Matching prompt:** `claude-plans/prompts/_spotify-matching.md`
- **Roadmap:** `ROADMAP.md`

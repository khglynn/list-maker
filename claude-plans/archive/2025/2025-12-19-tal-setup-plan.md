# This American Life (TAL) Playlist Plan
*Created: 2025-12-19*

## Executive Summary

TAL can run **in parallel** with SOP, using the **same repo** and **same Neon project**. The existing architecture is show-agnostic by design.

---

## Key Decisions

| Question | Answer | Why |
|----------|--------|-----|
| Run in parallel with SOP? | **Yes** | Different show_ids, no conflicts |
| Separate repo? | **No** | Architecture already show-agnostic |
| Separate Neon project? | **No** | Same schema, just add show row |
| Save full episode content? | **Yes** | Already loading it, no extra cost |
| Fetch TAL transcripts? | **No** | YAGNI - only need episode pages for songs |

---

## Prompt Folder Structure

```
claude-plans/prompts/
├── sop/
│   ├── setup-url-tracking.md
│   ├── scrape-episodes.md       ← Updated to save raw_content
│   ├── match-songs.md
│   └── backfill-descriptions.md ← NEW: fill in missing descriptions
├── tal/
│   ├── setup-url-tracking.md    ← Different URL discovery
│   ├── scrape-episodes.md       ← TAL-specific parsing
│   └── match-songs.md
```

---

## Data Strategy

**Save everything during scrape (no extra context cost):**
- `raw_content` - full markdown from Firecrawl
- `description_body` - parsed content before songs section
- `has_songs_discussed` - boolean for filtering later

**Why:** We're already loading the page into context. Saving to DB is just a bigger UPDATE statement.

**SOP backfill needed:** 449 episodes have NULL raw_content (batch prompt oversight). Run backfill after current scrape completes.

---

## SOP vs TAL Comparison

| Aspect | SOP | TAL |
|--------|-----|-----|
| **Episodes** | ~453 | ~876 (eps 1-876) |
| **Songs per ep** | 5-15 typical | 1-3 typical |
| **Total songs** | ~2,800+ | ~1,500 estimated |
| **URL structure** | `/episodes/slug` | `/NUMBER/slug` |
| **Song format** | "Songs Discussed" section | `## Song:` headers in act descriptions |
| **Discovery** | Map `/episodes` page | Archive by year or iterate 1-876 |

---

## Key TAL Differences

### 1. URL Structure
TAL URLs require both episode number AND slug:
- `https://www.thisamericanlife.org/1/new-beginnings` ✅
- `https://www.thisamericanlife.org/876/bigger-than-me` ✅
- `https://www.thisamericanlife.org/500` ❌ (404)

**Discovery options:**
- A) Scrape archive by year (1995-2025, 31 pages)
- B) Map the entire site and filter for `/NUMBER/` pattern
- C) Iterate 1-876 and get redirects (inefficient)

**Recommended:** Option A - scrape each year's archive page

### 2. Song Format
TAL uses a consistent `## Song:` markdown header format:
```markdown
## Song:

["Destination Moon" by Dinah Washington](https://itunes.apple.com/...)

## Song:

"(Uh-Oh) Get Out of the Car" by The Treniers
```

**Parsing regex:** Much simpler than SOP!
- Look for `## Song:` headers
- Extract song title and artist from next line
- Format varies: `["Title" by Artist](link)` or `"Title" by Artist`

### 3. Fewer Songs, More Episodes
- 876 episodes vs 453
- But ~2 songs/ep average vs ~6/ep for SOP
- Net: similar total workload

---

## Implementation Plan

### Phase 1: Database Setup (5 min)
```sql
-- Add TAL show record
INSERT INTO shows (name, slug, website_url)
VALUES ('This American Life', 'tal', 'https://thisamericanlife.org');
```

### Phase 2: URL Discovery
Scrape archive pages by year to get all episode URLs:
- 31 years (1995-2025)
- Extract URLs matching pattern `/\d+/[a-z-]+`
- Insert into episodes table with `scraped_at = NULL`

### Phase 3: Create TAL Prompts
Copy SOP prompts, adapt for TAL:
- `prompts/tal/setup-url-tracking.md`
- `prompts/tal/scrape-episodes.md`
- `prompts/tal/match-songs.md`

### Phase 4: Episode Scraping (Batches)
Same workflow as SOP:
- Query 20 unscraped episodes
- Scrape in parallel (5 at a time)
- Parse songs using TAL-specific regex
- Update database

### Phase 5: Spotify Matching
Identical to SOP - same `batch_search_tracks` workflow.

---

## Recommended Workflow

Since TAL is simpler (consistent format, fewer songs/ep), I suggest:

1. **Finish SOP first** - you're 58% done with scraping (262/453), song matching just started (36/2856)
2. **Then TAL** - cleaner to focus on one at a time
3. **OR run in parallel** - if you want variety, they won't conflict

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/lib/scraper/tal.ts` | Create - TAL-specific parsing |
| `claude-plans/prompts/tal/setup-url-tracking.md` | Create |
| `claude-plans/prompts/tal/scrape-episodes.md` | Create |
| `claude-plans/prompts/tal/match-songs.md` | Create |

No changes needed to:
- `src/lib/db.ts` (already show-agnostic)
- Database schema (already supports multiple shows)
- API routes (query by show_id)

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| TAL rate limits us | Low | 500ms delay between scrapes |
| Some episodes have no songs | Medium | Set `has_songs_discussed = false` |
| Song format varies across 30 years | Medium | Handle both link and plain text formats |
| Missing episodes (404s) | Low | Some ep numbers may be skipped; log and continue |

---

## Decisions Made

- **Parallel execution:** Yes - run TAL in a new Claude instance alongside SOP
- **Backfill scope:** Full archive (episodes 1-876, all 30 years)
- **Spotify playlist:** Will create new "This American Life - All Songs" playlist

---

## Execution Checklist

### This Session - Setup

- [ ] Create prompt folder structure (`prompts/sop/`, `prompts/tal/`)
- [ ] Move existing SOP prompts to `prompts/sop/`
- [ ] Update SOP scrape prompt to save raw_content
- [ ] Create SOP backfill prompt
- [ ] Create TAL prompts
- [ ] Insert TAL show record into database
- [ ] Create Spotify playlist for TAL

### TAL Session (Parallel)

- [ ] Run TAL setup-url-tracking to discover all episode URLs
- [ ] Batch scrape episodes (20/batch)
- [ ] Batch match songs to Spotify (150/batch)

### SOP Backfill (After current scrape completes)

- [ ] Run backfill-descriptions prompt to fill missing raw_content

---

## Prompt Files to Create

### prompts/sop/scrape-episodes.md (UPDATED)

```markdown
# Batch: Scrape Episodes (20/batch)

*Use after running setup-url-tracking.md*

## Context
- Neon Project: `summer-grass-52363332`
- Show ID: 1 (SOP)
- Episodes table has URLs with `scraped_at = NULL` for unscraped

## Task

1. **Query 20 unscraped episodes:**
   ```sql
   SELECT id, url FROM episodes WHERE show_id = 1 AND scraped_at IS NULL LIMIT 20
   ```

2. **Scrape in parallel** (5 at a time):
   ```
   mcp__firecrawl__firecrawl_scrape with formats: ["markdown"]
   ```

3. **Parse each episode:**
   - Episode number: Look for "EPISODE XXX" in markdown
   - Title: First H1 heading
   - Date: Look for date near top (format varies)
   - Songs: Extract from "Songs Discussed" section (format: "Artist – Title")
   - Description body: Everything between title and "Songs Discussed" section

4. **Update episode record** (NOW INCLUDES raw_content and description_body):
   ```sql
   UPDATE episodes SET
     title = 'Episode Title',
     publish_date = 'YYYY-MM-DD',
     episode_number = XXX,
     has_songs_discussed = true/false,
     raw_content = 'FULL MARKDOWN HERE',
     description_body = 'BODY TEXT BEFORE SONGS SECTION',
     scraped_at = NOW()
   WHERE id = X
   ```

5. **Insert songs** (escape apostrophes with ''):
   ```sql
   INSERT INTO songs (episode_id, title, artist) VALUES (X, 'Song Title', 'Artist Name')
   ```

6. **Report and pause:**
   ```
   Batch complete. Scraped 20 episodes, added X songs.
   Total: Y episodes scraped, Z songs.
   Remaining unscraped: W episodes.
   Ready for compact.
   ```

## Notes
- Some episodes have no "Songs Discussed" section - set has_songs_discussed = false
- Escape apostrophes: `Don't` becomes `Don''t`
- For raw_content: escape single quotes and use dollar-quoting if needed: `$body$content$body$`
- If scrape fails (404), mark as scraped with has_songs_discussed = false
```

---

### prompts/sop/backfill-descriptions.md (NEW)

```markdown
# Backfill: Episode Descriptions

*Run AFTER completing the main scrape pass*

## Context
- Neon Project: `summer-grass-52363332`
- Show ID: 1 (SOP)
- ~449 episodes have NULL raw_content from earlier scraping

## When to Run
Run this AFTER:
1. All episodes are scraped (scraped_at IS NOT NULL for all)
2. Songs are extracted and matched to Spotify
3. You have time for a cleanup pass

This is lower priority than TAL or song matching.

## Task

1. **Query episodes missing raw_content:**
   ```sql
   SELECT id, url, title FROM episodes
   WHERE show_id = 1
     AND scraped_at IS NOT NULL
     AND raw_content IS NULL
   LIMIT 20
   ```

2. **Re-scrape each episode** (5 at a time):
   ```
   mcp__firecrawl__firecrawl_scrape with formats: ["markdown"]
   ```

3. **Parse description body:**
   - Everything between the title and "Songs Discussed" section
   - If no "Songs Discussed", use everything until footer/navigation

4. **Update episode record:**
   ```sql
   UPDATE episodes SET
     raw_content = $body$FULL MARKDOWN$body$,
     description_body = $body$PARSED BODY TEXT$body$
   WHERE id = X
   ```

5. **Report progress:**
   ```
   Backfill batch complete. Updated 20 episodes.
   Remaining without raw_content: X episodes.
   ```

## Notes
- This does NOT re-extract songs (already done)
- Use PostgreSQL dollar-quoting for content with apostrophes
- Lower priority than new scraping - run when you have spare cycles
```

---

### prompts/tal/setup-url-tracking.md (NEW)

```markdown
# Setup: TAL URL Discovery

*Run this ONCE before starting TAL batch scraping*

## Context
- Neon Project: `summer-grass-52363332`
- Show ID: 2 (TAL) - create if doesn't exist
- TAL has ~876 episodes (1995-present)

## Task

1. **Ensure TAL show exists:**
   ```sql
   INSERT INTO shows (name, slug, website_url)
   VALUES ('This American Life', 'tal', 'https://thisamericanlife.org')
   ON CONFLICT (slug) DO NOTHING;

   SELECT id FROM shows WHERE slug = 'tal';
   ```

2. **Scrape archive pages by year** (1995-2025):
   For each year, scrape:
   ```
   https://www.thisamericanlife.org/archive?year=YYYY
   ```

   Extract episode URLs matching pattern: `/\d+/[a-z0-9-]+`

3. **Insert episode URLs:**
   ```sql
   INSERT INTO episodes (show_id, url, scraped_at)
   VALUES (2, 'https://www.thisamericanlife.org/876/bigger-than-me', NULL)
   ON CONFLICT (url) DO NOTHING;
   ```

4. **Report:**
   ```
   Setup complete. Inserted X episode URLs for TAL.
   Ready for batch scraping with scrape-episodes.md
   ```

## Notes
- TAL URLs require both number AND slug (e.g., /876/bigger-than-me)
- Some episode numbers may be skipped (not all numbers 1-876 exist)
- Archive pages show ~50 episodes each, paginated
```

---

### prompts/tal/scrape-episodes.md (NEW)

```markdown
# Batch: Scrape TAL Episodes (20/batch)

*Use after running setup-url-tracking.md*

## Context
- Neon Project: `summer-grass-52363332`
- Show ID: 2 (TAL)
- Episodes table has URLs with `scraped_at = NULL` for unscraped

## Task

1. **Query 20 unscraped episodes:**
   ```sql
   SELECT id, url FROM episodes WHERE show_id = 2 AND scraped_at IS NULL LIMIT 20
   ```

2. **Scrape in parallel** (5 at a time):
   ```
   mcp__firecrawl__firecrawl_scrape with formats: ["markdown"]
   ```

3. **Parse each episode:**
   - Episode number: Extract from URL (e.g., /876/title → 876)
   - Title: From og:title or first H1 (format: "Title - This American Life")
   - Date: From `article:published_time` metadata
   - Songs: Look for `## Song:` headers followed by song info

4. **Parse TAL song format:**
   Songs appear as:
   ```markdown
   ## Song:

   ["Song Title" by Artist Name](https://itunes.apple.com/...)
   ```
   OR:
   ```markdown
   ## Song:

   "Song Title" by Artist Name
   ```

   Extract: title (in quotes), artist (after "by")

5. **Update episode record:**
   ```sql
   UPDATE episodes SET
     title = 'Episode Title',
     publish_date = 'YYYY-MM-DD',
     episode_number = XXX,
     has_songs_discussed = true/false,
     raw_content = $body$FULL MARKDOWN$body$,
     description_body = $body$MAIN CONTENT$body$,
     scraped_at = NOW()
   WHERE id = X
   ```

6. **Insert songs:**
   ```sql
   INSERT INTO songs (episode_id, title, artist) VALUES (X, 'Song Title', 'Artist Name')
   ```

7. **Report and pause:**
   ```
   Batch complete. Scraped 20 episodes, added X songs.
   Remaining unscraped: Y episodes.
   Ready for compact.
   ```

## Notes
- TAL episodes typically have 1-3 songs (fewer than SOP)
- Some episodes have no songs - set has_songs_discussed = false
- Use PostgreSQL dollar-quoting for content with apostrophes
- Escape apostrophes in song titles: `Don't` becomes `Don''t`
```

---

### prompts/tal/match-songs.md (NEW)

```markdown
# Batch: Match TAL Songs to Spotify (150/batch)

## Context
- Neon Project: `summer-grass-52363332`
- Show ID: 2 (TAL)
- Spotify Playlist: [TO BE CREATED]

## Task

1. **Query unmatched songs:**
   ```sql
   SELECT s.id, s.title, s.artist, e.title as episode_title
   FROM songs s
   JOIN episodes e ON s.episode_id = e.id
   WHERE e.show_id = 2 AND s.spotify_track_id IS NULL
   LIMIT 150
   ```

2. **Batch search Spotify:**
   ```
   mcp__spotify__batch_search_tracks with songs array and delay_seconds: 0.2
   ```

3. **Process results by confidence:**
   - HIGH (90%+): Add to playlist automatically
   - MEDIUM (70-89%): Log for review
   - LOW (<70%): Skip
   - NOT_FOUND: Skip

4. **Add HIGH matches to playlist:**
   ```
   mcp__spotify__add_tracks_to_playlist
   ```

5. **Update database:**
   ```sql
   UPDATE songs SET
     spotify_track_id = 'spotify:track:xxx',
     spotify_match_confidence = 'HIGH',
     added_to_playlist = true
   WHERE id = X
   ```

6. **Report:**
   ```
   Batch complete.
   - HIGH matches added: X
   - MEDIUM (needs review): Y
   - LOW/NOT_FOUND skipped: Z
   Remaining unmatched: W songs.
   ```
```

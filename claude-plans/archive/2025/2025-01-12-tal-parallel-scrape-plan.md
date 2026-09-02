# TAL Parallel Scrape Plan: Dumb Fetch, Smart Parse

*Created: 2025-01-12*

## The Problem

We have 438 unscraped TAL episodes. The previous batch approach (10 episodes at a time via Claude prompts) was slow and manual. We wanted to parallelize but scripts can be brittle - parsing logic has edge cases that fail silently.

## The Solution: Split Fetch from Parse

**Key insight:** The brittleness risk is in **parsing**, not fetching. So we separate them:

| Step | Who | Why |
|------|-----|-----|
| Fetch | Script (Kevin runs) | Fast, parallel, no judgment needed |
| Parse | Agent (Claude) | Edge cases, ambiguity, can adapt |
| Validate | Agent (Claude) | Find anomalies, fix issues |
| Match | Script (Kevin runs) | Already built, works well |
| Review matches | Agent (Claude) | LOW/NOT_FOUND need judgment |

## Phase 1: Dumb Script Fetches

`scripts/tal_fetch.py` does ONE thing: fetch URLs and save raw responses to JSON files.

```python
# Intentionally simple - nothing to break
for url in unscraped_urls:
    response = firecrawl.scrape(url)
    save_to_json(f"fetched/tal/{episode_id}.json", response)
```

- No parsing logic = nothing to break
- 5 concurrent (Firecrawl hobby tier limit)
- ~2 sec/page = 438 episodes in ~3 minutes
- If it fails, you have partial results and can resume

**Output:** JSON files in `scripts/fetched/tal/`

## Phase 2: Agent Parses

Claude reads the JSON files and does the smart work:
- Extract title, date, episode_number from metadata
- Find and parse songs (handling all 3 formats)
- Clean description_body (strip markdown)
- Handle edge cases with judgment
- Write to Neon database
- Flag anything weird

Can process 100+ at a time since no network calls - just reading local files.

### Song Parsing Formats

**Format 1 - With link:**
```markdown
## Song:
["Destination Moon" by Dinah Washington](https://itunes.apple.com/...)
```
→ title: "Destination Moon", artist: "Dinah Washington"

**Format 2 - Plain text:**
```markdown
## Song:
"(Uh-Oh) Get Out of the Car" by The Treniers
```

**Format 3 - With "performed by":**
```markdown
## Song:
"Who By Fire" by Leonard Cohen, performed by House of Love
```
→ Use original artist (Leonard Cohen) for Spotify matching

## Phase 3: Agent Validates

Query for anomalies:
```sql
-- Missing data
SELECT * FROM episodes WHERE show_id = 2 AND scraped_at IS NOT NULL AND title IS NULL;

-- Suspiciously short content
SELECT * FROM episodes WHERE show_id = 2 AND LENGTH(raw_content) < 500;

-- Episodes that should have songs but don't
SELECT * FROM episodes WHERE show_id = 2 AND has_songs_discussed = false
  AND raw_content LIKE '%## Song:%';
```

Fix anything that slipped through.

## Phase 4: Spotify Matching (Separate)

Keep this separate - different kind of validation.

```bash
python scripts/spotify_match.py --show-id 2
```

Then agent reviews LOW/NOT_FOUND matches.

## Phase 5: Sync to Playlist

```bash
python scripts/sync_playlist.py --show-id 2
```

---

## The Complete Flow

```
Kevin: python scripts/tal_fetch.py
       → Creates 438 JSON files in scripts/fetched/tal/

Claude: Read JSON files in batches of 100
        → Parse, write to DB
        → Report any weirdness

Claude: Validate with SQL queries
        → Fix any issues

Kevin: python scripts/spotify_match.py --show-id 2
       → Matches songs, logs confidence

Claude: Review LOW/NOT_FOUND matches
        → Fix or mark unavailable

Kevin: python scripts/sync_playlist.py --show-id 2
        → Adds to Spotify playlist
```

---

## Why This Works

1. **Speed** - Script parallelizes fetching (5 concurrent)
2. **Quality** - Agent handles parsing edge cases
3. **Resilience** - JSON files persist if interrupted
4. **Separation** - Each step is verifiable before moving on

---

## Files

- `scripts/tal_fetch.py` - Dumb fetcher
- `scripts/fetched/tal/` - Raw JSON responses (gitignored)
- `scripts/spotify_match.py` - Already exists
- `scripts/sync_playlist.py` - Already exists

---

## Firecrawl Limits (Hobby Tier)

- 3,000 credits/month
- 5 concurrent requests
- 438 episodes = 438 credits (plenty of headroom)

---

## Current State (as of 2025-01-12)

| Metric | Count |
|--------|-------|
| Episode URLs discovered | 882 |
| Already scraped | 444 |
| Remaining to scrape | 438 |
| Songs extracted so far | 529 |
| Songs matched to Spotify | 0 |
| TAL playlist tracks | 0 |

---

## Phase 2 Update: Fetch Complete, Parse Ready

**Fetched:** All 438 episodes fetched to `scripts/fetched/tal/`
- 357 valid pages
- 308 with songs (86%)
- 81 are 404s (pages removed from TAL site)

### Parsing Method (Tested & Working)

Python parsing logic at `/tmp/test_parse.py` extracts:
- `db_id` - database row ID
- `episode_number` - from URL pattern `/(\d+)/`
- `title` - from `og:title` metadata, suffix removed
- `publish_date` - from `article:published_time`
- `songs[]` - from `## Song:` markdown sections
- `is_404` - detected by "could not be found" in markdown

**Song formats handled:**
1. `["Title" by Artist (note)](url)` - linked format
2. `"Title" by Artist` - plain text format
3. `"Title" by Original, performed by Other` - uses original artist

### Subagent Strategy for Parsing

Split 438 files into batches, each agent handles ~100 files:

**Agent 1:** Files 901-1000 (first 100)
**Agent 2:** Files 1001-1100 (next 100)
**Agent 3:** Files 1101-1200 (next 100)
**Agent 4:** Files 1201-1338 (remaining ~138)

Each agent:
1. Reads JSON files in its range
2. Parses using the established method
3. Writes to Neon via `run_sql_transaction`:
   - UPDATE episodes (title, episode_number, publish_date, has_songs_discussed, scraped_at)
   - INSERT songs (episode_id, title, artist)
4. Reports results (success count, error count, any anomalies)

**Coordination:** Database prevents conflicts - each agent writes to different episode IDs.

### Post-Parse Steps

1. **Validation queries** - check for missing data, anomalies
2. **Spotify matching** - `python scripts/spotify_match.py --show-id 2`
3. **Review LOW/NOT_FOUND** - agent reviews uncertain matches
4. **Sync playlist** - `python scripts/sync_playlist.py --show-id 2`

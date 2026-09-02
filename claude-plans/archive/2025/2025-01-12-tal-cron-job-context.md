# TAL Cron Job - Context & Lessons Learned

*Created: 2026-01-12*
*For: Session building automated weekly episode checking*

---

## Goal

Build a cron job that:
1. Checks for new TAL episodes (878+)
2. Scrapes episode page → saves JSON
3. Parses songs → inserts to Neon
4. Matches songs to Spotify
5. Syncs new tracks to playlist

---

## Current State

| Resource | Value |
|----------|-------|
| TAL show_id | 2 |
| Neon project | `summer-grass-52363332` |
| Playlist ID | `3d7fjfrTTKvrl7VHv5JzIz` |
| JSON dir | `scripts/fetched/tal/` |
| Latest episode in DB | 877 (Dec 12, 2025) |
| Episodes reserved for testing | 878+ |

**Database counts:**
- 882 episodes total
- 1,094 songs
- 880 matched to Spotify (80%)
- 214 NOT_FOUND

---

## TAL Website Patterns

### Episode URL Structure
```
https://www.thisamericanlife.org/{episode_number}/{slug}
```

Examples:
- `/877/the-making-of`
- `/358/social-engineering`
- `/510/fiasco`

**Important:** The slug can change! Episode 358 was originally scraped as `/358/the-edge-of-the-edge` but the correct URL is `/358/social-engineering`. Always verify URLs work before relying on them.

### Finding New Episodes

**Option 1: Archive page**
```
https://www.thisamericanlife.org/archive
```
Lists episodes with episode numbers - can scrape to find new ones.

**Option 2: RSS feed** (limited)
```
https://www.thisamericanlife.org/podcast/rss.xml
```
Only includes recent ~10 weeks, not useful for comprehensive checking.

**Option 3: Direct URL check**
Try `/{episode_number}` - TAL doesn't auto-redirect, so you need the correct slug. If unsure, search:
```
Firecrawl search: "site:thisamericanlife.org {episode_number}"
```

### Song Location in Episode Pages

Songs appear in the markdown as:
```markdown
## Song:

"Song Title" by Artist Name
```

Or:
```markdown
## Song:

"Song Title"
```

The parser looks for `## Song:` headers and extracts the next non-empty line.

---

## Existing Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `tal_scrape_missing.py` | Scrape episodes without JSON files | `python3 tal_scrape_missing.py --execute --skip-after 877` |
| `tal_fix_404s.py` | Fix 404 episodes (search for correct URL) | `python3 tal_fix_404s.py --execute` |
| `tal_fill_songs.py` | Parse JSONs → insert songs to DB | `python3 tal_fill_songs.py --execute` |
| `tal_parse.py` | Core parsing logic (imported by fill script) | Library, not CLI |
| `spotify_match.py` | Match songs to Spotify tracks | `echo "" | python3 spotify_match.py --show-id 2` |
| `sync_playlist.py` | Sync matched songs to playlist | `python3 sync_playlist.py --show-id 2` |

**Note:** `spotify_match.py` has an interactive prompt - pipe empty string to auto-continue: `echo "" | python3 spotify_match.py`

---

## Firecrawl API Patterns

### Scraping
```python
api_url = "https://api.firecrawl.dev/v1/scrape"
payload = {
    "url": url,
    "formats": ["markdown"],
    "onlyMainContent": True
}
response = requests.post(api_url, headers=headers, json=payload, timeout=60)
data = response.json()
if data.get("success"):
    markdown = data["data"]["markdown"]
    metadata = data["data"]["metadata"]
    status_code = metadata.get("statusCode", 200)
```

### Searching
```python
api_url = "https://api.firecrawl.dev/v1/search"
payload = {
    "query": f"site:thisamericanlife.org {episode_number}",
    "limit": 10
}
# Results are non-deterministic - same query may return different order
# Always check multiple results, not just first one
```

---

## Database Schema (relevant tables)

### episodes
```sql
id SERIAL PRIMARY KEY
show_id INTEGER REFERENCES shows(id)
episode_number INTEGER
title TEXT
url TEXT
air_date DATE
has_songs_discussed BOOLEAN
-- NULL title = 404 (page doesn't exist)
```

### songs
```sql
id SERIAL PRIMARY KEY
episode_id INTEGER REFERENCES episodes(id)
title TEXT
artist TEXT
spotify_track_id TEXT
spotify_confidence TEXT -- 'HIGH', 'MEDIUM', 'LOW', NULL
```

### Key queries
```sql
-- Get episodes without JSON files
SELECT id, episode_number, url FROM episodes
WHERE show_id = 2 AND title IS NOT NULL
ORDER BY episode_number DESC;

-- Get unmatched songs
SELECT s.title, s.artist FROM songs s
JOIN episodes e ON s.episode_id = e.id
WHERE e.show_id = 2 AND s.spotify_track_id IS NULL;

-- Get latest episode
SELECT MAX(episode_number) FROM episodes WHERE show_id = 2;
```

---

## Gotchas & Lessons Learned

### 1. URL Slugs Change
Some episode URLs were wrong in our original scrape. The 404 fixer uses Firecrawl search to find correct URLs. New scraper should verify URLs work.

### 2. Firecrawl Search is Non-Deterministic
The same search query can return results in different orders. Don't rely on first result being the best match. Check all results for the correct episode number.

### 3. Some Episodes Don't Have Songs
363 episodes have 0 songs - this is normal. Not every TAL episode has music credits.

### 4. Episode 481 Doesn't Exist
This episode legitimately 404s - page doesn't exist on TAL site. Don't retry it.

### 5. Quote Handling in Song Titles
Songs in TAL pages are wrapped in quotes: `"Song Title"`. The parser strips these. Watch for:
- Straight quotes: `"`
- Curly quotes: `"` `"`
- Double-wrapped: `""Song""`

The `clean_quotes` function in `tal_parse.py` handles this:
```python
def clean_quotes(text: str) -> str:
    text = re.sub(r'^[\"""\u201c\u201d]+', '', text)
    text = re.sub(r'[\"""\u201c\u201d]+$', '', text)
    return text.strip()
```

### 6. Spotify Matching Confidence
- **HIGH**: Exact or near-exact match
- **MEDIUM**: Likely correct but verify
- **LOW**: Uncertain, may need manual review
- **NOT_FOUND**: No match found

80% match rate is typical. Many NOT_FOUND are obscure/unreleased songs.

### 7. JSON File Naming
JSON files are named by **database ID** (not episode number):
```
scripts/fetched/tal/{db_id}.json
```
e.g., `748.json` for episode 877 (db_id 748)

### 8. Rate Limiting
Firecrawl API needs 0.3s delay between requests to avoid rate limits.

---

## Cron Job Design Suggestions

### Option A: Simple Sequential
```
1. Check TAL archive for new episode numbers
2. For each new episode:
   a. Scrape page → save JSON
   b. Parse songs → insert to DB
   c. Match to Spotify
   d. Sync to playlist
3. Update playlist description
```

### Option B: Split Pipeline
```
Job 1 (hourly): Check for new episodes, save JSONs
Job 2 (daily): Parse all unprocessed JSONs → DB
Job 3 (daily): Match unmatched songs to Spotify
Job 4 (daily): Sync playlist
```

### Recommended: Option A
TAL only publishes ~1 episode/week, so simple sequential is fine. No need to over-engineer.

### Testing Strategy
Episodes 878+ are reserved for testing. Don't scrape these until cron job is ready to test.

---

## Files to Reference

- `scripts/tal_scrape_missing.py` - Full scraping implementation
- `scripts/tal_fix_404s.py` - URL correction via search
- `scripts/tal_fill_songs.py` - JSON parsing → DB insert
- `scripts/tal_parse.py` - Core parsing functions
- `claude-plans/2025-01-12-tal-data-cleanup-plan.md` - Full session history

---

## Environment Setup

Required env vars in `.env.local`:
```
DATABASE_URL=postgresql://...@...neon.tech/...
FIRECRAWL_API_KEY=fc-...
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=...
```

Required packages:
```
psycopg2-binary
python-dotenv
requests
```

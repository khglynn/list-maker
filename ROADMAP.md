# Roadmap

*Last updated: 2026-05-16*

What's next, in order. When done, move to `COMPLETED.md`.

---

## 1. AI Daily -> Notion

Push extracted entities to a browsable Notion database. Initial and incremental sync are live; all 978 transcripted episodes have mentions and 1,067 eligible entities are synced.

**What:**
- [x] Design Notion database schema (Name, Type, Mentions, Dates, URL, Context)
- [x] Create/configure Notion database
- [x] Build sync script (Neon -> Notion)
- [x] Run initial sync
- [ ] Iterate on schema with Kevin after browsing the live Notion database

---

## 2. Automation

Make scrapes run without manual intervention.

**What:**
- [ ] Choose platform (GitHub Actions recommended)
- [ ] Weekly: Taddy transcript import for Taddy-backed shows (`ai-daily-brief,pchh,sop,tal`)
- [ ] Weekly: AI Daily entity extraction on new episodes
- [ ] Weekly: Notion sync (upsert)
- [ ] Weekly: Spotify playlist sync for SOP/TAL
- [ ] Failure alerting (email or Notion)

---

## 3. Spotify Credentials + SOP/TAL Matching Cleanup

Playlist verification is blocked locally until Spotify credentials are restored. After that, matching cleanup is the next music quality pass.

**What:**
- [ ] Restore local Spotify env (`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`) or document where the active token lives.
- [ ] Fix "feat./ft." format mismatches (~130 SOP songs)
- [ ] Fuzzy search major artists (~80 SOP songs)
- [ ] Mark unavailable songs (~25 songs)
- [ ] Re-sync playlists
- [ ] Same for TAL's 214 NOT_FOUND

---

## 4. PCHH

Mixed content — music, movies, TV, books. More complex extraction.

**What:**
- [ ] Design extraction prompt for "What's Making Me Happy" segment
- [ ] Multi-category extraction (music -> Spotify, movies/TV -> Notion + Trakt)
- [ ] Backfill from 356 transcripts + add to automation

---

## 5. TAL Transcript Scope

TAL's official Taddy source is configured and current for the rolling feed, but the full 883-episode archive source is not transcribing.

**What:**
- [ ] Decide whether TAL needs historical transcripts or only website song scraping.
- [ ] If historical transcripts matter, find a source other than the non-transcribing Taddy archive feed.
- [ ] Keep website song scraping separate from transcript import.

---

## 6. Trakt Integration (future)

Cross-device watchlist sync for movie/TV recommendations from PCHH.

---

## Future Ideas (Unprioritized)

- **Enhanced SOP song extraction** - Extract songs from episode body text, not just "Songs Discussed" section. Handle album mentions → pull top tracks from Spotify.
- **Public database export** - SQLite or read-only API
- **Human review UI** - Quick approve/reject for low-confidence matches
- **Spotify metadata enrichment** - Release year, genre (requires extra API calls per track)
- **Book audiobook availability** - No good aggregator API found yet
- **One-click-play for TV** - Reelgood integration (Likewise was buggy)
- **Public dashboard** - Stats on playlists, most-discussed songs/tools

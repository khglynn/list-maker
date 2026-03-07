# Roadmap

*Last updated: 2026-03-06*

What's next, in order. When done, move to `COMPLETED.md`.

---

## 1. Finish AI Daily Backfill

154 episodes remaining (734/888 done). Quality gate may need tuning for the last stretch.

**What:**
- [ ] Check if quality gate threshold needs lowering (5 → 3 mentions/ep)
- [ ] Clear any lock files from previous stall
- [ ] Resume `run_mentions_until_done.py`
- [ ] Run alias normalization + link discovery on all data
- [ ] Verify quality with `report_summary.py`

---

## 2. AI Daily -> Notion

Push extracted entities to a browsable Notion database.

**What:**
- [ ] Design Notion database schema (Name, Type, Mentions, Dates, URL, Context)
- [ ] Create database via Notion MCP
- [ ] Build sync script (Neon -> Notion)
- [ ] Run initial sync, iterate on schema with Kevin

---

## 3. Automation

Make scrapes run without manual intervention.

**What:**
- [ ] Choose platform (GitHub Actions recommended)
- [ ] Weekly: Taddy transcript import for all shows
- [ ] Weekly: AI Daily entity extraction on new episodes
- [ ] Weekly: Notion sync (upsert)
- [ ] Weekly: Spotify playlist sync for SOP/TAL
- [ ] Failure alerting (email or Notion)

---

## 4. SOP/TAL Matching Cleanup (optional)

Playlists work at 80-91%. Nice-to-have improvements.

**What:**
- [ ] Fix "feat./ft." format mismatches (~130 SOP songs)
- [ ] Fuzzy search major artists (~80 SOP songs)
- [ ] Mark unavailable songs (~25 songs)
- [ ] Re-sync playlists
- [ ] Same for TAL's 214 NOT_FOUND

---

## 5. PCHH (future)

Mixed content — music, movies, TV, books. More complex extraction.

**What:**
- [ ] Design extraction prompt for "What's Making Me Happy" segment
- [ ] Multi-category extraction (music -> Spotify, movies/TV -> Notion + Trakt)
- [ ] Backfill + add to automation

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

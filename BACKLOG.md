# BACKLOG — list-maker

*Created 2026-09-01, replacing ROADMAP.md (last updated 2026-05-16) and COMPLETED.md (retired; its milestones are one DEVLOG archive entry). Live state is `NOW.md`; the current plan is `claude-plans/2026-09-01-ground-it-cleanup-plan.md`. This file is the parking lot: ideas and known gaps that are real but not being worked. Add a date when you park something; delete it when it ships or is decided against.*

## Decided, waiting on a build (see the plan's "Next arc")
- **Curated intake v2** — automated blog/article intake with an inexpensive classifier deciding "worth saving?" from Kevin's own saves; no human checkbox (Kevin, 2026-09-01).
- **Ads as data** — keep sponsor-read mentions, tag them as ads, cap their weight (≈5 mentions max), log first-seen products (Kevin, 2026-09-01).
- **Dead-man's switch** — reuse the `fleet-watchdog` Worker pattern (personal/self-hosted-mcps/watchdog): list-maker's Worker exposes its last-fire time; the watchdog Slacks when it goes stale.
- **Feed check by episode identity** (Taddy uuid set-difference), a run-id watchdog in the Worker, a transactional batch load, `NULL` not `0.5` for missing confidence — plan Phase 4.
- **Tests on the Spotify write path** (`spotify_match.py`, `sync_playlist.py`) — plan Phase 5.
- **Hash-pinned lockfile** (pip-compile) — plan Phase 2.

## Music quality (from ROADMAP §3, still true)
- SOP: "feat./ft." format mismatches (~130), fuzzy search for major artists (~80), mark unavailable (~25), then re-sync. TAL: the same pass on its NOT_FOUND set.
- `songs` uniqueness constraint: three true duplicate pairs exist (trailing-space titles, identical Spotify ids); delete the three extras, then apply `pipeline/scrapers/ai_daily/sql/008_songs_unique_per_episode.sql`.

## Test gaps (known, dated)
- **SQL statements are only ever run by mocks in `tests/`** (parked 2026-09-02, found while reviewing PR #31): a statement Postgres would reject — a misspelled column, a CHECK violation, a bad placeholder — passes the suite in every module that mocks the cursor (`sync_notion`, `load_entity_batch`, `store`, …). Cheapest fix: a CI step that parses every SQL string with `pglast` and cross-checks column names against the `sql/` migrations; the intake PR did that by hand once. Not a Neon round-trip — CI has no database on purpose.

- **A backfill's Notion sync doesn't fit one step** (parked 2026-09-02): `step_notion_sync` runs under `run_script`'s 600 s timeout, sized for a daily delta of a few entities. The PCHH back-catalogue (2,073 new mentions) needed ~25 minutes; locally it timed out three times (each attempt resumed — the sync is NULL-marker gated — and 987 entities landed in 25 minutes), and the same day's CI run hit the job's 50-minute limit doing the same work. Fix when the next backfill comes: a `--limit` on the sync so one attempt is one bounded chunk, or a longer timeout for `--backfill` runs only. The daily path is unaffected.

## Open questions
- **PCHH + Culture Gabfest full-archive backfill** (~11h, ~$7.50) — run once, or write it off. Deferred since 2026-06-07.
- **TAL historical transcripts** — Taddy's archive feed doesn't transcribe; website song scraping doesn't need them. Decide whether transcripts matter for TAL at all.
- **Provenance columns on `episodes`/`shows`** (which importer wrote this row) — deferred 2026-09-01; every check routes through `show_config.py` today.

## Ideas (unprioritized, from ROADMAP)
- Trakt integration for movie/TV recommendations (cross-device watchlist).
- Enhanced SOP song extraction from episode body text; album mentions → top tracks.
- Public database export (SQLite or read-only API); public dashboard (most-discussed songs/tools).
- Human review UI for low-confidence matches; Spotify metadata enrichment (year, genre).
- Book audiobook availability; one-click-play for TV.

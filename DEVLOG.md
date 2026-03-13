# Dev Log

Chronological session journal. Most recent at top. Never delete entries.

---

## 2026-03-13 — Full Catch-Up: Merge, Pipeline Runs, Automation

**What happened:**
- Merged Spotify refactors from this Mac with AI Daily work from other machines
  - `spotify_match.py` and `sync_playlist.py` refactored to be callable from orchestrators
  - New `run_pipeline.py` orchestrator for music shows (scrape→match→sync)
  - New Python scrapers for SOP and TAL (ported from TypeScript / unified wrappers)
  - GitHub Actions workflow for scheduled runs (SOP Wed+Fri, TAL Mon)
- AI Daily catch-up: 3 new episodes (Mar 10-13), 55 mentions extracted, 47 Notion pages updated
- SOP catch-up: 11 episodes scraped, 211 songs found, 199 matched (81% HIGH), 166 tracks added to Spotify playlist
- TAL: already current, no new episodes
- Fixed `fill_songs.py` import (`tal_parse` → `parse` — old rename not updated)
- GitHub Actions: workflow pushed, 6/7 secrets configured (missing SLACK_WEBHOOK_URL)
- Installed missing `openai` package in pipeline venv

**Key numbers:**
- SOP playlist: 3,542 songs across 675 episodes
- TAL playlist: 778 songs across 882 episodes
- AI Daily: 918 episodes imported, 777 extracted, 8,460 mentions, 853+ in Notion

**Next:** Verify GH Actions dry-run, PCHH pipeline, SOP/TAL NOT_FOUND cleanup (369 + 214 songs)

---

## 2026-03-06 — Roadmap Review + Docs Cleanup

**What happened:**
- Full project audit — reviewed all code, plans, and docs for accuracy
- Discovered AI Daily backfill stalled since Feb 11 (quality gate too strict)
- Discovered SOP/TAL matching improvements were planned but never executed
- Updated all project docs: README, pipeline/README, CLAUDE.md, COMPLETED.md, ROADMAP.md
- Created this DEVLOG
- Established 6-phase roadmap (see ROADMAP.md)

**Key findings:**
- Docs were significantly stale — CLAUDE.md didn't mention AI Daily at all
- READMEs still said "list-maker"
- ROADMAP described transcript integration as future work (it's built)
- Neon has way more data than docs reflected: SOP 664 ep (not 462), AI Daily 734 ep extracted (not 230), PCHH 300 ep imported
- SOP matching was partially improved (NOT_FOUND 534 → 357) — earlier claim it was "never executed" was wrong
- Codex branch `codex/ai-daily-brief-kickoff` did the bulk of AI Daily work, was fast-forward merged to main

**Next:** Finish AI Daily backfill (154 episodes remaining), then Notion sync.

---

## 2026-03-01 — Taddy Scraper Added

**What happened:**
- Built Taddy API transcript importer supporting AI Daily, PCHH, SOP
- 888 AI Daily transcripts imported
- Project renamed from list-maker to pod-lists

---

## 2026-02-11 — AI Daily Backfill Stalled

**What happened:**
- Running parallel backfill with `run_mentions_until_done.py`
- Quality gate failures: `mentions_per_episode_too_low` on lighter episodes
- 230 episodes successfully processed, 658 remaining
- Last 7 attempts all failed quality checks

---

## 2026-02-05 — AI Daily Lean Schema + Extraction Pipeline

**What happened:**
- Simplified AI Daily to 3-table Neon schema (ai_runs, ai_entities, ai_mentions)
- Built full extraction pipeline with quality gates
- Validated on 25-episode batch (11.6 mentions/ep average)
- Added guarded backfill runner, alias normalization, link discovery

---

## 2026-01-25 — Folder Reorg + Mosaic Art

**What happened:**
- Restructured project: scripts/ -> pipeline/, scrapers/ subdirectories
- Created mosaic artwork for SOP and TAL playlist covers
- TAL backfill completed: 882 episodes, 1,094 songs, 880 matched

---

## 2025-12-21 — SOP Song Review + Matching Analysis

**What happened:**
- Processed all LOW confidence matches (200 songs)
- Detailed analysis of 534 NOT_FOUND songs by category
- Created improvement plan (feat. format fixes, major artist search)
- Plan documented but not executed — pivoted to AI Daily

---

## 2025-12-12 — Project Kickoff

**What happened:**
- Created Neon database, schema, SOP scraper
- First 3 episodes scraped, 16 songs extracted
- Spotify MCP configured
- Session handoff doc created

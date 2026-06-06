# NOW — list-maker

**Last updated:** 2026-06-06

## Active: durable, self-healing rebuild (plan approved 2026-06-06)
Goal — all 6 shows auto-processing on a durable schedule → music (SOP, TAL) to Spotify; tech (AI Daily, Hardfork) + media (PCHH, Culture Gabfest) to Notion; self-healing; Slack-notifying; tested; best-practices.
- Full spec: `claude-plans/2026-06-06-durable-pipeline-rebuild.md`
- Way-of-working + grounding (read after any compaction): `claude-plans/2026-06-06-durable-pipeline-resume.md`

## Next step (exact)
**Workstream A — hardening** (scheduler-agnostic durability core):
- **A1 ✅ DONE** — single-source show registry: importer derives `SHOWS`/`RAW_CONTENT_SHOW_SLUGS` from `show_config.py` (added `fallback_website_url` + `store_raw_content`); `cfg.series_uuid`→`cfg.taddy_uuid`; drift-guard test tightened. pytest 24/24; Codex SAFE; parity verified.
- **A2 → NEXT:** `ON CONFLICT` on `insert_mention` (`load_entity_batch.py`) — idempotent writes (no duplicate mentions on retry).
- A3 parameterize 90-day filter + `--backfill` flag · A4 per-entity Notion sync state · A5 structured logging · A6 orchestrator-level retry · A7 staleness alert → Slack · A8 tests on extraction + sync · A9 docs (ARCHITECTURE.md + status refresh).
Then **C** (Hardfork) → **B** (Cloudflare-Cron durable trigger + Slack) → **D** (media: PCHH + Culture Gabfest) → **E** (verify all shows).

Per-task rhythm: investigate (verify reality) → implement (TDD/DB-test where logic or data changes) → run the net AND read it → doc-sweep → secret-scan the staged diff → commit (scope-prefixed, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`) → bank NOW/DEVLOG → **Codex + triple-check at the boundary.**

## Just set up (2026-06-06)
- Plan approved + archived; resume/grounding doc written; global CLAUDE.md gained "Default to deep, durable work"; ralph-loop uninstalled (`/loop` = native paradigm); Codex stop-time review gate enabled for this repo.
- Compaction-survival: project CLAUDE.md pointer in place; PreCompact + SessionStart-compact hooks drafted for `.claude/settings.json` (pending Kevin's OK — self-modification needs explicit approval).

## Pre-flight Taddy gate findings (2026-06-06) — affects WS-C / WS-D
- **Hardfork = "Hard Fork"** (NYT, two words). Taddy uuid `ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0`, 199 eps, **TRANSCRIBING** → ✅ Taddy method works. Register under the exact name "Hard Fork" + this uuid.
- **Culture Gabfest** on Taddy: uuid `7732402c-ed24-4b3c-b344-3570eedd8020`, 871 eps, **NOT_TRANSCRIBING** → ⚠️ no transcripts via Taddy as-is. WS-D decision needed: request Taddy transcription (credits) OR alternate source (show's own transcripts / Whisper). Verify before building Gabfest.
- **Standing practice:** pre-flight verify every new show on Taddy (exists? transcribing?) BEFORE building — catches exactly this. (Save as reusable once proven.)

## Carry-over (pre-rebuild — fold into the workstreams, don't lose)
- AI Daily ~17 days stale (newest ep 2026-05-18; last run 2026-05-20) → fixed by the daily trigger (WS-B) + a catch-up run.
- SOP/TAL Spotify `added_to_playlist` oddly low (SOP 35 / TAL 0) — verify `sync_playlist.py` actually pushes vs. silently failing (WS-E).
- TAL historical transcripts: official Taddy source only exposes the current rolling feed (archive not transcribing).

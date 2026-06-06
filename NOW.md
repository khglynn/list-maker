# NOW — list-maker

**Last updated:** 2026-06-06

## Active: durable, self-healing rebuild (plan approved 2026-06-06)
Goal — all 6 shows auto-processing on a durable schedule → music (SOP, TAL) to Spotify; tech (AI Daily, Hardfork) + media (PCHH, Culture Gabfest) to Notion; self-healing; Slack-notifying; tested; best-practices.
- Full spec: `claude-plans/2026-06-06-durable-pipeline-rebuild.md`
- Way-of-working + grounding (read after any compaction): `claude-plans/2026-06-06-durable-pipeline-resume.md`
- **Push-hold LIFTED (2026-06-06):** no Vercel link in repo → pushing to `khglynn/list-maker` main is safe. Push each commit going forward (no more local-only).
- **Compaction method VALIDATED (2026-06-06):** a real auto-compaction fired mid-A7; the `SessionStart:compact` hook re-grounded the new instance (resume doc + NOW.md) with **zero lost state** — WIP intact, pytest green. The survival kit works. *(Closeout TODO: document in DEVLOG + save the methodology.)*
- **ENV NOTE:** a "learning" output style is active (it wants Claude to ask Kevin to write code) — conflicts with the autonomous-away loop; continued autonomously per instruction-priority. Kevin: `/output-style default` if unintended.

## Next step (exact)
**Workstream A — hardening** (scheduler-agnostic durability core):
- **A1 ✅ DONE** — single-source show registry: importer derives `SHOWS`/`RAW_CONTENT_SHOW_SLUGS` from `show_config.py` (added `fallback_website_url` + `store_raw_content`); `cfg.series_uuid`→`cfg.taddy_uuid`; drift-guard test tightened. pytest 24/24; Codex SAFE; parity verified.
- **A2 ✅ DONE** — idempotent batch load via `delete_existing_run(show_id, batch_name)` before `insert_run` (re-load replaces; self-heals partials). Scoped psycopg2 DELETE (not the MCP, so unaffected by the destructive-op guard). pytest 25/25; Codex SAFE. *Deferred: full single-transaction atomicity — low value vs blast-radius, and the orchestrator is already episode-idempotent via `find_unextracted_episodes`.*
- **A3 ✅ DONE** — `RECENT_EPISODE_WINDOW_DAYS` const + `make_interval(days => %s)` (no hardcoded literal) + `--backfill` flag threaded through `process_show`/`main` (→ `recent_only=False` + Taddy cap 500). pytest 28/28; Codex SAFE.
- **A4 ⏸ KEVIN-BLOCKED (schema migration):** per-entity Notion sync state needs `ALTER TABLE ai_entities ADD COLUMN notion_sync_status / notion_sync_error / notion_sync_attempt_at`. Schema changes are deferred to Kevin per the autonomous-run rule (will summarize at A-complete). Skipped → A5.
- **A5 ✅ DONE** — `get_logger()` foundation in `common.py` (idempotent, `LOG_LEVEL` env, refreshed after env-load) + orchestrator per-show/per-run timing (structured, timestamped). pytest 31/31; Codex SAFE. *A5b (incremental, deferred): convert the per-step `print()`s in import/extract/sync/load to `log` calls — a 5-file sweep, low-risk but churny; do when convenient.*
- **A6 ✅ DONE** — `run_script` retries transient subprocess failures (`MAX_STEP_RETRIES=2`, exponential backoff 5s/10s) — safe because steps are idempotent (A2 + upserts); catches `TimeoutExpired` (retryable); logs retries/recovery/final-failure via `log`. pytest 34/34; Codex SAFE (timeout gap fixed). *A6b (deferred): cross-show aggregated failed-steps summary (per-failure `log.error` already gives greppable observability).*
- **A7 ✅ DONE** — `check_episode_freshness` in `data_health.py`: per-show `STALENESS_MAX_DAYS` (ai-daily 3, pchh 7, hard-fork/gabfest 10, sop 14, tal 21; default 14), flags shows past their freshness threshold; registered in `run_checks`. Closes the silent-staleness hole (AI Daily's 17d drift). pytest 36/36; Codex SAFE. *Slack-send of the report is downstream (workflow + `SLACK_WEBHOOK_URL`) — deferred to B.*
- **A8 → NEXT:** tests on critical paths — golden-master for `extract_entities` (fixture transcript → stable JSON) + data-contract tests (no NULL `canonical_name`/`entity_id`; valid `entity_type`; confidence∈[0,1]).
- **A9:** docs (ARCHITECTURE.md data-flow + CLAUDE.md single-source-of-truth + status refresh) — also fold in the compaction-method writeup + A4/A5b/A6b deferral notes.
Then **C** (Hardfork) → **B** (Cloudflare-Cron durable trigger + Slack) → **D** (media: PCHH + Culture Gabfest) → **E** (verify all shows).

Per-task rhythm: investigate (verify reality) → implement (TDD/DB-test where logic or data changes) → run the net AND read it → doc-sweep → secret-scan the staged diff → commit (scope-prefixed, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`) → bank NOW/DEVLOG → **Codex + triple-check at the boundary.**

## Just set up (2026-06-06)
- Plan approved + archived; resume/grounding doc written; global CLAUDE.md gained "Default to deep, durable work"; ralph-loop uninstalled (`/loop` = native paradigm); Codex stop-time review gate enabled for this repo.
- Compaction-survival: project CLAUDE.md pointer + PreCompact + SessionStart-compact hooks LIVE in `.claude/settings.json` (committed `10219ff`); destructive-DB-op PreToolUse guard live too. **Validated 2026-06-06** by a real compaction (see Active section).

## Pre-flight Taddy gate findings (2026-06-06) — affects WS-C / WS-D
- **Hardfork = "Hard Fork"** (NYT, two words). Taddy uuid `ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0`, 199 eps, **TRANSCRIBING** → ✅ Taddy method works. Register under the exact name "Hard Fork" + this uuid.
- **Culture Gabfest** on Taddy: uuid `7732402c-ed24-4b3c-b344-3570eedd8020`, 871 eps, **NOT_TRANSCRIBING** → ⚠️ no transcripts via Taddy as-is. WS-D decision needed: request Taddy transcription (credits) OR alternate source (show's own transcripts / Whisper). Verify before building Gabfest.
- **Standing practice:** pre-flight verify every new show on Taddy (exists? transcribing?) BEFORE building — catches exactly this. (Save as reusable once proven.)

## Carry-over (pre-rebuild — fold into the workstreams, don't lose)
- AI Daily ~17 days stale (newest ep 2026-05-18; last run 2026-05-20) → fixed by the daily trigger (WS-B) + a catch-up run.
- SOP/TAL Spotify `added_to_playlist` oddly low (SOP 35 / TAL 0) — verify `sync_playlist.py` actually pushes vs. silently failing (WS-E).
- TAL historical transcripts: official Taddy source only exposes the current rolling feed (archive not transcribing).

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
- **A4 ✅ APPROVED (2026-06-07) — do it:** per-entity Notion sync state — `ALTER TABLE ai_entities ADD COLUMN IF NOT EXISTS notion_sync_status / notion_sync_error / notion_sync_attempt_at` via a **psycopg2 migration** (NOT the MCP — guard-blocked) + record status in `sync_notion.py` + alert if >10% of a run's entities fail. Kevin OK'd the columns.
- **A5 ✅ DONE** — `get_logger()` foundation in `common.py` (idempotent, `LOG_LEVEL` env, refreshed after env-load) + orchestrator per-show/per-run timing (structured, timestamped). pytest 31/31; Codex SAFE. *A5b (incremental, deferred): convert the per-step `print()`s in import/extract/sync/load to `log` calls — a 5-file sweep, low-risk but churny; do when convenient.*
- **A6 ✅ DONE** — `run_script` retries transient subprocess failures (`MAX_STEP_RETRIES=2`, exponential backoff 5s/10s) — safe because steps are idempotent (A2 + upserts); catches `TimeoutExpired` (retryable); logs retries/recovery/final-failure via `log`. pytest 34/34; Codex SAFE (timeout gap fixed). *A6b (deferred): cross-show aggregated failed-steps summary (per-failure `log.error` already gives greppable observability).*
- **A7 ✅ DONE** — `check_episode_freshness` in `data_health.py`: per-show `STALENESS_MAX_DAYS` (ai-daily 3, pchh 7, hard-fork/gabfest 10, sop 14, tal 21; default 14), flags shows past their freshness threshold; registered in `run_checks`. Closes the silent-staleness hole (AI Daily's 17d drift). pytest 36/36; Codex SAFE. *Slack-send of the report is downstream (workflow + `SLACK_WEBHOOK_URL`) — deferred to B.*
- **A8 ✅ DONE** — extraction-contract tests (`test_extract_entities.py`: `sanitize_mention`/`sanitize_fact`/`parse_json_object` — confidence∈[0,1], required core fields, unknown-type→other+review, low-conf→review) + DRY'd the duplicated entity_type validation into `normalize_entity_type` (used in `insert_mention` + `main`) with its own test. pytest 45/45; Codex SAFE. *(No OpenAI-mock golden-master needed — the post-LLM sanitizers ARE the testable contract seam.)*
- **A9 ✅ DONE** — `ARCHITECTURE.md` (data flow, schema, code map, hardening table, scheduler plan) + DEVLOG entry + CLAUDE.md status pointer. pytest 45/45.

### ✅ WORKSTREAM A COMPLETE (2026-06-06)
A1–A9 done. Pipeline is idempotent, self-healing, structured-logged, staleness-aware, tested (45), single-sourced, documented — and survived a live compaction.

## 🚀 RUN TO COMPLETION (2026-06-07) — loop widened, Kevin away
**Access resolved:** GH secrets set (OPENAI/NOTION/TADDY/SLACK ✅); Slack webhook wired + tested (posts to #list-maker ✅); wrangler logged into trimm ✅; A4 columns approved ✅; Gabfest path solved (Megaphone RSS show-notes carry the endorsements → scrape, NO transcription).

**Sequence (loop drives, gate every step):**
1. **A4** — Notion sync-state columns (psycopg2 migration, `IF NOT EXISTS`) + record status in `sync_notion.py` + >10%-fail alert.
2. **C — Hard Fork** ("Hard Fork", uuid `ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0`): `show_config.py` entry + Neon `shows` row (MCP INSERT ok — only DELETE/ALTER are guard-blocked) + create Notion DB (clone AI Daily schema via the `NOTION_TOKEN` integration) + Taddy import + `--backfill` (~$2-3). Also catch AI Daily up (19d stale).
3. **D — media:** PCHH media-extraction profile + media Notion DB (PCHH transcripts ready ✅); Gabfest = Megaphone RSS (`feeds.megaphone.fm/slatesculturegabfest`) endorsement scraper (no transcription). Media entity-types = a psycopg2 migration. Tiny validation batch + projected-cost note before any big media backfill.
4. **B — trigger:** write `entities.yml` (daily) + the Cloudflare Worker (trimm) code + the `sys.executable` CI fix. **DEPLOY = Kevin** (`wrangler deploy` w/ trimm + a fine-grained GH PAT as a CF secret).
5. **E — verify:** all shows end-to-end; the SOP/TAL `added_to_playlist` anomaly.

**Still needs Kevin (loop stops + PushNotifies):** the Cloudflare worker DEPLOY (trimm account_id + GH PAT); possibly sharing the Notion integration with new DBs. Everything else is solo.

Per-task rhythm: investigate (verify reality) → implement (TDD/DB-test where logic or data changes) → run the net AND read it → doc-sweep → secret-scan the staged diff → commit (scope-prefixed, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`) → bank NOW/DEVLOG → **Codex + triple-check at the boundary.**

## Just set up (2026-06-06)
- Plan approved + archived; resume/grounding doc written; global CLAUDE.md gained "Default to deep, durable work"; ralph-loop uninstalled (`/loop` = native paradigm); Codex stop-time review gate enabled for this repo.
- Compaction-survival: project CLAUDE.md pointer + PreCompact + SessionStart-compact hooks LIVE in `.claude/settings.json` (committed `10219ff`); destructive-DB-op PreToolUse guard live too. **Validated 2026-06-06** by a real compaction (see Active section).

## Pre-flight Taddy gate findings (2026-06-06) — affects WS-C / WS-D
- **Hardfork = "Hard Fork"** (NYT, two words). Taddy uuid `ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0`, 199 eps, **TRANSCRIBING** → ✅ Taddy method works. Register under the exact name "Hard Fork" + this uuid.
- **Culture Gabfest** (uuid `7732402c-ed24-4b3c-b344-3570eedd8020`) **NOT_TRANSCRIBING** on Taddy (iHeart-distributed; Taddy lacks the rights — not audience size). **SOLVED 2026-06-07:** the endorsements/recs are published in the episode show-notes (Megaphone feed `feeds.megaphone.fm/slatesculturegabfest`) → scrape the RSS descriptions, **no transcription needed**. D-task: build the RSS endorsement scraper; verify main-episode descriptions list the endorsements completely (one bonus ep confirmed; confirm across main eps).
- **Standing practice:** pre-flight verify every new show on Taddy (exists? transcribing?) BEFORE building — catches exactly this. (Save as reusable once proven.)

## Carry-over (pre-rebuild — fold into the workstreams, don't lose)
- AI Daily ~17 days stale (newest ep 2026-05-18; last run 2026-05-20) → fixed by the daily trigger (WS-B) + a catch-up run.
- SOP/TAL Spotify `added_to_playlist` oddly low (SOP 35 / TAL 0) — verify `sync_playlist.py` actually pushes vs. silently failing (WS-E).
- TAL historical transcripts: official Taddy source only exposes the current rolling feed (archive not transcribing).

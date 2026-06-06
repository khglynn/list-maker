# list-maker: Durable, Self-Healing Podcast Pipeline — Lock It In For Good

**Created:** 2026-06-06 · **Validated by:** live DB/code ground-truthing + 3-agent triple-check + Codex review + 3 deep-dig agents (best-practices audit, media schema from real transcripts, durable-scheduler eval)
**Project:** `~/DevKev/personal/list-maker` (repo: `khglynn/list-maker`, **public**) · Neon project `summer-grass-52363332`
**Working copy:** `~/.claude/plans/cool-cool-then-the-woolly-mist.md` → archive to project `claude-plans/` once we proceed.

---

## North star — "done done done" acceptance criteria

This is a **durable, never-touch-it-again** build, not a couple-fixes run. Done means ALL of:

1. **All 6 shows auto-processing on a durable schedule:** SOP, TAL (music → Spotify), AI Daily, Hardfork (tech → Notion), PCHH, Culture Gabfest (media → Notion). Music playlists + Notion DBs stay current with no manual runs.
2. **Durable trigger** — survives indefinitely (no GitHub 60-day silent-disable).
3. **Self-healing** — transient API failures retry automatically; a missed run is caught up by the next idempotent run; nothing duplicates on re-run.
4. **Notifies (Slack)** — every run posts a summary; failures and staleness alert loudly.
5. **Best practices baked in:** single source of truth for config, idempotent writes, NULL-over-default, provenance, structured logging, data-quality gates, tests on the critical paths (extraction + sync), green `data_health.py`.
6. **Docs true to reality** — CLAUDE.md/NOW/ROADMAP/ARCHITECTURE reflect the built system.

---

## Execution mode (per Kevin, 2026-06-06)

- **Autonomous + decisive.** Decide the best path or resolve via tools/agents/codex; don't pester. Only stop for a TRUE blocker (missing credential not in `.env.local`, external auth failure, irreversible action on something I didn't create, high-stakes fork with no defensible best path).
- **REVIEW GATE at every major step (required):** after each meaningful code/work step, run **`/codex:review`** (independent breakage/correctness pass — via the Codex rescue agent since the slash command is human-only) **AND `/triple-check`**; fix everything found; then commit and continue. Verify Codex CLI ready at start (`/codex:setup`); else fall back to the `code-reviewer` agent + triple-check.
- **Run under `/loop` (self-paced)** — the "continue until done" mechanism (ralph-loop; no `/goal` skill exists). Each loop iteration: pick the next unfinished task → implement → codex+triple-check → commit → update the checklist → repeat until ALL acceptance criteria pass. (No fixed interval; self-pace.)
- **Safety discipline holds:** owned repo → commit+push proactively; no `rm` (Finder trash); never commit secrets (`grep` before `add`); don't echo env/payloads in logs; `codex-notes/` artifacts stay gitignored; don't touch the mental-health branch.
- **First execution step:** save this directive (autonomy + review gate + durable-build intent + the 3 reference docs) to project memory as a feedback/project memory; archive this plan to `claude-plans/`.

---

## Architecture decisions

**A1 — Durable scheduler: harden-in-place + Cloudflare-Cron trigger (recommended); Inngest is the elective heavy option.**
"Durable/auto-heal/notify/best-practices" is ~80% pipeline-hardening (scheduler-agnostic) + ~20% trigger. Get the full end-state without a risky rewrite of proven Python:
- **Trigger:** a **Cloudflare Worker Cron** (existing account, $0, ~20 lines) calls GitHub `workflow_dispatch`. Remove the `schedule:` trigger → the 60-day-disable can't apply (it only hits `schedule:` crons). Needs a fine-grained GitHub PAT (actions:write) as a CF secret.
- **Why not Inngest now:** it needs the Python *re-hosted always-on* + a new account, and restructured into Inngest functions — i.e., partly rewriting a working 980-episode pipeline. Your own June-3 legibility research says don't break what works. Inngest's per-step replay is real but marginal for this simple, idempotent, daily, sequential workload (idempotent daily re-runs already self-heal). **Inngest stays documented as the elective "gold-plated" upgrade**, honestly priced (rewrite/host + account) — not deferred-as-in-ignored, just not rushed into breaking proven code.
- Either way, the durability WORK happens now via hardening (A-series below).

**A2 — Single source of truth for show config.** Collapse the dual registry: `import_transcripts.py` imports from `show_config.py`; a test fails the build if they diverge. (Agent 1 GAP A; legibility doc "single-source-of-truth.")

**A3 — Entity store stays unified, media via new types + facts JSONB.** Reuse `ai_entities`/`ai_mentions` (Agent 2 Option A) — add media `entity_type` values + a media `facts` shape (creators[], release_year, platform, caveats, endorsement) rather than new tables. One extraction engine, two profiles (tech vs media prompt).

---

## Workstream A — Pipeline hardening (the durability core; scheduler-agnostic)

From Agent 1's audit (each is a `/loop` task; codex+triple-check each):

- **A1. Merge show registries** → single `show_config.py`; importer consumes it; divergence test. *(GAP A, quick win)*
- **A2. Idempotent writes** → add `ON CONFLICT` to `insert_mention` (`load_entity_batch.py:286`) keyed on (run_id, episode_id, entity_id); wrap batch loads in a transaction. *(GAP B, quick win — prevents duplicate mentions on retry)*
- **A3. Parameterize the 90-day filter** → `RECENT_DAYS` constant + `--backfill`/`recent_only=False` path + explanatory comment citing the skipped-141 policy. *(GAP C + the backfill flag Hardfork/media need)*
- **A4. Per-entity Notion sync state** → add `notion_sync_status`/`notion_sync_error`/`notion_sync_attempt_at` to `ai_entities`; record failures instead of swallowing; post-sync "if >10% failed, alert." *(GAP D)*
- **A5. Structured logging** → Python `logging` (levels, timestamps, JSON) across orchestrator + sync + import + extract; per-stage metrics (counts, timing, OpenAI cost). *(GAP F)*
- **A6. Orchestrator-level retry** → failed batch/stage retries with backoff instead of `continue`-and-forget; end-of-run summary of succeeded/failed/will-retry. *(GAP H; per-API retries already exist)*
- **A7. Staleness alert** → `data_health.py` gains "days since latest episode" per show (ai-daily 3d, hardfork/gabfest 10d, sop/tal 14/21d) → Slack. *(GAP G; closes the silent-stale hole that bit AI Daily)*
- **A8. Tests on the critical paths** → golden-master for `extract_entities` (fixture of real transcripts → stable JSON), data-contract tests (no NULL canonical_name/entity_id; confidence∈[0,1]; valid entity_type), orchestrator smoke test; wire into CI. *(GAP E; "if you liked it, put a test on it")*
- **A9. Docs** → ARCHITECTURE.md (data flow Taddy→Neon→Notion/Spotify), CLAUDE.md single-source-of-truth + status refresh (Notion now 1,070 not 853).

---

## Workstream B — Durable trigger + Slack notifications

- **B1. Cloudflare Worker Cron → `workflow_dispatch`** (per A1). Daily for entity shows; keep music cadence. Remove `schedule:` from the GH workflow(s).
- **B2. Slack everywhere (you want "a Slack whenever"):** run summaries (success), failure alerts, staleness alerts, sync-failure-rate alerts — via the existing `SLACK_WEBHOOK_URL` (CI) + Slack MCP (local). Reuse `pipeline.yml`'s notify pattern in the new `entities.yml`.
- **B3. CI runtime fix (Codex catch):** `run_new_episodes.py:32` hardcodes `pipeline/venv/bin/python`; use `sys.executable` fallback so CI (system Python) works.
- **B4. Pass `OPENAI_API_KEY` + `NOTION_TOKEN` + Taddy creds** in the entity workflow env (secrets from `.env.local`).

---

## Workstream C — Hardfork (tech taxonomy, config-only clone)

1. **Gate:** Taddy lookup for "Hardfork" → `series_uuid` + confirm transcripts. (true blocker if absent)
2. **Notion DB** "Hardfork — Tools & Mentions": **clone the AI Daily DB schema exactly** (inspect data source `a72f8f82-1ca0-4973-9dc2-3757aa729c6e`; URL prop is `userDefined:URL` in code/tests). Share the integration with it (Editor).
3. **Neon `shows` row** — insert explicitly, read back the id (don't assume 4), set `show_config.py` to the real id, verify with `data_health` expected-shows check.
4. **Config** in the (now single) registry + `RAW_CONTENT_SHOW_SLUGS`.
5. **Backfill LOCALLY** (not CI): tiny batch first (validate), then full `--backfill` archive (~$4, gpt-4.1-mini). Standard path, NOT `run_guarded_backfill.py` (AI-Daily-tuned gates).
6. Recent episodes then flow via the daily trigger automatically.

---

## Workstream D — Media build (PCHH + Culture Gabfest)

Per Agent 2's transcript-grounded design. **Schema work across 5 surfaces** (not a flag):
- **D1. DB:** extend `entity_type` CHECK (`001_ai_entity_schema.sql`) + valid-types in `load_entity_batch.py` with media types (`movie, tv_series, book, music_album, music_track, game, podcast_series, theater_production, social_account, artist_profile, visual_media_other`) → migration.
- **D2. Media `facts` shape:** creators[{role,name}], release_year, platform, explicit_endorsement, caveats, comparison_to[], genres[], content_warnings[].
- **D3. Extractor profile:** media prompt (segment-aware: PCHH "What's Making Me Happy," Gabfest "endorsements"; endorsement + creators + platform + caveats), selected by `extraction_type="media_extraction"`.
- **D4. Notion "Media Recommendations" DB:** Type/Platform/Creators/Host/Sentiment/Endorsement Quote/Caveats/Episode/Status/Content Warnings (movies/TV Trakt-ready later). Routing in `sync_notion.py`.
- **D5. Add shows:** Culture Gabfest (Taddy lookup + config + shows row), PCHH (`extraction_type=media_extraction`). Tiny batch → verify → full backfill.
- **D6. Trakt:** deferred (separate account/OAuth) — Notion-only for now; schema is Trakt-ready.

---

## Workstream E — All-shows verification + the SOP/TAL Spotify anomaly
- Confirm SOP/TAL Spotify playlists actually receive tracks (`added_to_playlist` oddly low: SOP 35, TAL 0) — is `sync_playlist.py` pushing or silently failing? Fix or document.
- End-to-end: each show's newest episode → correct destination, traced source→display.

---

## Side tasks
- **Research folder tidy** (`…/2026-05-22-running-things-off-my-laptop/`): add `INDEX.md` (read-order + provenance), rename `zimzAEmW`→`…checkpoint-snapshot.zip`, prompts → `prompts/`. Light touch.
- **Mental-health branch** (`origin/claude/mental-health-podcasts-2DJbo`): leave parked, note in BACKLOG (Short Wave, Science Vs, Radiolab, Speaking of Psychology, Hidden Brain, The Science of Happiness, Unexplainable; all already heard).

---

## Suggested sequence (under `/loop`, codex+triple-check each)
1. Memory + plan archive. 2. WS-A hardening (A1→A9; foundation). 3. WS-C Hardfork (proves the hardened path end-to-end on a real new show). 4. WS-B durable trigger + Slack. 5. WS-D media build. 6. WS-E verify all shows. 7. Side tasks. Loop until every acceptance criterion passes.

## Cost & accounts (durability prioritized; accounts a tiebreaker)
- **New accounts:** **none** for the recommended path (Cloudflare/OpenAI/Notion/Taddy/Slack/GitHub all already in use). Inngest (elective) + Trakt (deferred) are the only account-adders.
- **Cost:** GH Actions free (public); Cloudflare Worker Cron free; OpenAI backfills ~$10–15 one-time (~$0.01/ep) + pennies/day steady — *confirm against usage*.

## Open decision (my rec inside)
**Durable scheduler:** recommended = harden-in-place + Cloudflare-Cron trigger (no rewrite, no new account, hits all criteria). Elective = full Inngest migration (better per-step replay/observability, but rewrite/host + account; risks breaking proven code). Defaulting to the recommended path unless you elect Inngest.

## Validation trail
Ground-truthed live DB + code · 3-agent triple-check (90-day filter, staleness gap, Notion props, media-is-new) · Codex review SAFE-WITH-FIXES (CI venv bug, URL prop, show_id, Phase-2-as-schema) · 3 deep digs (best-practices audit, real-transcript media schema, scheduler eval) — Inngest rec accepted as input but down-weighted for rewrite risk per your own legibility doc.

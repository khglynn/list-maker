# Resume — list-maker: music-pipeline debug, notifications, + the open items
*Written 2026-06-07 (late) by the instance that did the work, for the next instance (compaction OR fresh session). You are MID-BUILD on a durable, self-healing, "never-touch-it-again" rebuild — NOT starting fresh. The #1 failure mode here is doing the minimum / executing the literal next bullet without re-grounding in WHY. This session went deep for good reasons; understand them before you touch anything.*

---

## ▶ HOW TO START — PLAN MODE FIRST, not execute mode (Kevin's explicit framing)

**Do NOT skim this handoff and drop into execution.** This handoff is *signal*, not a spec to tick through. Start in **plan mode**: read the grounding below, then *riff on the plan in your own words* — what's the actual goal, what would YOU do, where do you disagree or see a better path, what does "done done done" mean here, what would let a check lie. Root in your **own** autonomy + quality framing before touching anything; the depth that made this session good came from *re-deriving*, never from stenography. One instance's plan is a starting point for your thinking, not a substitute for it. **Plan → align with Kevin → then execute under the gate.** If you find yourself reaching for the Edit tool before you've re-derived the why, stop.

---

## ▶ WORKING ORDER — follow THIS sequence (NOT the order of the OPEN ITEMS list below)

The open items below are *detail*; this is the order that yields the best results (worked out with Kevin). It separates **Kevin-gated quick-unlocks** (tee up at the start, NEVER block on them) from **solo substantive work**, and uses the pulse as the *confirmation loop* for the music fix.

0. **Plan mode** — read the grounding (below) + re-derive the plan in your own words. Align with Kevin.
1. **Research guides → global CLAUDE.md** (+ the hg-agents skill-ify note). FIRST, because they're *grounding*: putting them in the global CLAUDE.md makes their principles **always-loaded standing context** for the rest of this session and every future one — and the moment right after the plan-mode deep-read is when your understanding is freshest to write a good wrapper. Keep it **TIGHT** (a thoughtful wrapper + reference section, not a rewrite). Cross-repo (`helper/claude-configs/`) — ask Kevin before committing global config.
2. **Tee up Kevin's two parallel tasks** — the **GH PAT** + the **Sentry cron-monitor** — so he does them while you work. Don't block on either.
3. **Music-pipeline debug → catch-up** — the real value; LEAD here while context is fresh. (Runbook + "healthy" acceptance criteria in OPEN ITEMS.)
4. **PAT closeout** — verify a real dispatch, THEN remove the GitHub schedules. The first live pulse then confirms SOP/TAL flipped to ✅ caught-up (the verify-against-reality loop on the music fix).
5. **Sentry → Slack** — when Kevin's Sentry side is ready; group with the Worker work.
6. **Transcripts mentions UX** — a contained, high-delight build.

## ⛔ DO NOT TOUCH without Kevin's explicit OK (this handoff spans many surfaces)
Secrets / credentials; **global CLAUDE.md edits** (affect every project — propose first); **eachie production** (a different deployed app — inspect + ask before modifying/redeploying it); **Notion DB schema** changes; **destructive DB ops** (`DELETE`/`DROP`/`ALTER` — the Neon-MCP guard blocks these; surface, don't route around); any outward push to Spotify/Notion beyond the planned syncs. When unsure whether an action is reversible/outward-facing: ask.

---

## READ FIRST — required, in order. These are the *why*, not background.

**The research guides (universal principles — most of this work descends from them). The first two (laptop + memory) are REQUIRED for any continuation; the legibility + dependency-security guides are required when you touch those areas (refactoring, the Worker, deps):**
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-05-22-running-things-off-my-laptop/2026-05-22-running-things-off-my-laptop--primer_AGENTS MAIN READ ME FILE.md` — durable execution; the five operational contracts; the GitHub-cron 60-day silent-disable; the **five questions** every project answers with one named thing (what starts it / where it runs / where it remembers / where a human approves / how you'll know the output stayed good). *This whole pipeline is an instance of it.*
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-04-23-database history and design/memory-systems-rollup-v2_AGENTS MAIN READ ME FILE.md` — provenance + valid-time; **scripts own deterministic ops, agents earn ambiguity** (list-maker is script-first with one LLM call at extraction); **evals are the only honest gradient**; the Willison/filesystem baseline.
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-06-03-codebase-legibility-and-maintenance/2026-06-03-codebase-legibility-and-maintenance--guide_AGENTS MAIN READ ME FILE.md` — executable-tells-truth / inert-lies-silently / delete-don't-disclaim / tests-are-the-honest-gradient.
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-06-03-dependency-security-hygiene/2026-06-03-dependency-security-hygiene--guide_AGENTS MAIN READ ME FILE.md` — supply-chain/dep hygiene (relevant when touching requirements / the Worker).

**Then:** `NOW.md` (live state), `claude-plans/2026-06-07-session-handoff.md` (the 7 ways-of-working), `claude-plans/2026-06-06-durable-pipeline-resume.md` (failure modes + grounding). The prior resume `claude-plans/2026-06-07-resume-cloudflare-evals-transcripts.md` is ✅ executed.

---

## WAYS OF WORKING — the hard-won insights (load-bearing; this is the heart)

1. **The plan is the FLOOR of ambition, not the letter.** This session's best work came from diverging when reality demanded — the shared-Notion-DB redesign, the COALESCE source path, and (this latest stretch) re-architecting the pulse around a *second source* when "days since our latest" turned out to measure the wrong thing. Read the plan for intent; synthesize with what's TRUE.

2. **VERIFY AGAINST REALITY — the throughline of this whole session, and it kept paying off.** Kevin's instinct: *"make sure these can't lie."* Never declare done from a success message or a DB marker — check the actual destination/source. This session it caught, in order: the Notion ReadTimeout that died mid-sync; "cool archived it" (wasn't); the importer log that lied; **and most spectacularly, the second-source feed check caught SOP behind 1 + TAL behind 2 — a MONTHS-stale music-pipeline failure that "21 days ✅" was hiding.** The green was lying; now it can't. When you build a check, ask: *what would let this show green when it's actually broken?* (Codex found 5 such paths in the second source — all closed.)

3. **The gate is non-negotiable — and concrete.** Before you COMMIT or DEPLOY anything: run the tests (`./pipeline/venv/bin/python -m pytest -q` from repo root, ~120 green), inspect the real destination/output (the actual playlist / Notion page / DB row, not the log), think through the failure modes, THEN run `codex:codex-rescue` + `/triple-check`. It earned its keep relentlessly this session — the transcript dedup-on-resume bug, the pulse-can-succeed-without-posting bug, the 5 second-source lie-paths. Fix everything it finds *inside the active workstream*; park unrelated cross-surface findings as concrete follow-up notes (don't scope-creep into eachie / global config / other shows mid-task).

4. **Surface genuine forks; don't grind through them.** Kevin steered hard this session (Option A, second-source-vs-Spotify, the pulse UX). When a re-issued instruction contradicts a just-found reality, hold the conflicting part and say so — keep everything un-blocked moving.

5. **Autonomy + narrate; bank continuously.** Commit each coherent unit (scope-prefixed, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`), push (owned repo), bank `NOW.md`. Long jobs → background; verify on completion. psycopg2 for migrations (Neon MCP destructive ops are hook-blocked). Deletions/archives need Kevin's clear intent.

6. **Observability must be TRUSTWORTHY + actionable + low-noise.** This session's alert philosophy, settled with Kevin: failures → Slack; a biweekly *pulse* is the positive heartbeat (its absence = trigger down); NO per-run success pings (daily shows = spam); every signal links to its destination + cross-checks a second source so it can't lie. Hold that bar.

7. **DON'T DO THE MINIMUM.** The value was depth — root-causing, redesigning, hardening, mapping to the WHYs. Re-ground (guides + this doc), then continue with that depth.

---

## STATE — shipped this session (committed + gated; don't redo, verify if unsure)

- **Eval harness** (`evals/extraction/`) — deterministic scorers, frozen baseline + hand-verified gold, gated runner, weekly `eval.yml`. KEY FINDING baked in: gpt-4.1-mini extraction has ~40% run-to-run SET churn at temp 0, so the gate uses stable aggregates (yield, type-dist, gold recall, confidence contract), not set identity. `evals/README.md`.
- **Neon FTS** (`search_transcripts.py` + generated tsvector/GIN, sql/005) — power search.
- **Notion Transcripts DB** (`3780501e-f950-81c9-a3e3-eca7f1162c9d`) — all **1,196** tech transcripts (997 AI Daily + 199 Hard Fork; note "199 transcripts" = all 199 Hard Fork episodes — the project CLAUDE.md's "198" was an extraction count, a different number, not a bug to chase). Idempotent sync (`sync_transcripts_notion.py`). *Verified in Notion 997+199.* DB view is sorted newest-first — AI Daily is visibly on top (resolved; no action needed).
- **Cloudflare durable trigger** (`cloudflare-trigger/`, deployed, 6 crons) — DEPLOYED BUT DORMANT until `GH_PAT` is set: the Worker can't dispatch without it, so right now only `pipeline.yml` + `entities.yml` run (via their still-present GitHub `schedule:` blocks); **`eval.yml` + `pulse.yml` are Worker-only and DON'T run yet.** Once the PAT is verified, the Worker takes over and the GitHub schedules come off. (Trigger-failure Slack is wired.)
- **Trustworthy observability** — `pulse_report.py` (biweekly, hub link + second-source per-show + destination links + recent counts), `feed_check.py` (Taddy for 5 shows + Megaphone RSS for Gabfest; returns None=unverified for every can't-check case so it never shows false green), daily second-source alarm in `data_health.py`, the Gabfest false-positive fix, per-run success pings removed.
- ~120 tests, all green.

---

## ⚠️ THE NEXT WORKSTREAM — debug the MUSIC pipeline (SOP/TAL). This is the real one.

The second source caught a **pre-existing, months-stale** music-pipeline failure (NOT this session's regression — we never touched SOP/TAL scraping internals; the new observability just made it visible). Hard evidence from the 2026-06-07 catch-up runs:
- **SOP:** found 1 new episode but scraped **0 songs** from it and added **0** to the playlist. Playlist is behind Neon (~3,727 live vs ~4,244 matched). Last real additions: **Mar 13**.
- **TAL:** scraper found **0 new episodes** though Taddy shows it's behind 2. Last additions: **Jan 13**.
- Descriptions are stale because `update_playlist_description` only runs on a *successful* sync.

**RUNBOOK — facts a fresh instance needs:** Neon project `summer-grass-52363332`. Show IDs: SOP=1, TAL=2. Spotify playlists: SOP `0cEVeX4pdHf5RJOiTRzgxX`, TAL `3d7fjfrTTKvrl7VHv5JzIz`. Taddy uuids (the second source): SOP `97ed51a4-460e-4dc8-8db5-30df96ad59bc`, TAL `d682a935-ad2d-46ee-a0ac-139198b83bcc`. Run Python via the venv: `./pipeline/venv/bin/python3 <script>` from repo root (scripts that use `common.load_environment` handle env; for the others, the env pattern is in the project CLAUDE.md). Music pipeline entry: `pipeline/run_pipeline.py --show-id <id> --yes --cache-path ../.spotify_cache/.cache` (from `pipeline/`). **Re-auth Spotify locally first if the cache is stale** (CLAUDE.md "If Spotify auth fails").

**Reproduce the evidence first (verify reality, don't trust this doc):**
- `./pipeline/venv/bin/python3 pipeline/pulse_report.py --dry-run` → should show SOP `🚨 BEHIND 1`, TAL `🚨 BEHIND 2` (the second source). If they're now ✅, the schedule already caught them up — re-confirm before debugging.
- Run the music pipeline for SOP locally and watch the scrape step report `0 songs` on the new episode; that's the first thread.

**Investigate (each step: run it, read the real output):**
1. **SOP song scraper** (`pipeline/scrapers/sop/`) — why 0 songs from a real episode with a song list? Run it on the latest SOP episode; diff against switchedonpop.com's live markup (likely a markup change).
2. **TAL episode scraper** (`pipeline/scrapers/tal/`) — why 0 new episodes when Taddy shows 2? **DECISION GATE (surface to Kevin):** first *confirm* whether TAL's website/RSS source is actually broken (TAL's RSS 403'd for me — try it); THEN weigh a minimal scraper fix vs migrating TAL's episode source to Taddy (uuid above). Don't change the architecture before confirming the simpler fix won't do.
3. **Playlist sync** (`pipeline/sync_playlist.py`, `get_matched_track_ids` + `add_tracks_to_playlist` + `update_playlist_description`) — the matched-in-Neon vs in-playlist gap (was ~3,727 live vs ~4,244 matched for SOP — re-derive the live count via the Spotify playlist + the Neon matched count; don't trust these stale numbers). Is the dedup wrong, or does it only sync the *new episode's* songs and never reconcile the historical gap? `update_playlist_description` only runs on a successful sync — that's why the descriptions ("Last updated 03/26"/"06/26") are stale.

**"HEALTHY" = done done done for the music pipeline (the acceptance criteria — verify against the DESTINATION, not the run log):**
- `pulse_report.py --dry-run` shows SOP **and** TAL `✅ caught up` (our DB latest == the feed's latest).
- The live Spotify playlists contain every matched track ID from Neon (minus any documented UNAVAILABLE), and the counts reconcile.
- `update_playlist_description` ran: descriptions show current song/episode counts + a today-ish "Last updated".
- `data_health.py`'s `import_caught_up_to_feed` check passes for SOP/TAL.
- You opened the actual playlists in Spotify and saw recent songs — NOT just "the run exited 0".

*(The 2026-06-07 catch-up runs revealed these bugs; they did NOT fully catch up.)*

---

## OPEN ITEMS

- **🔑 GH PAT — CRITICAL UNLOCK.** Until Kevin sets the `GH_PAT` Worker secret, the Worker can't fire, so **`pulse.yml` + `eval.yml` (Worker-only, no GitHub schedule) DON'T RUN AT ALL.** pipeline.yml + entities.yml (+ the daily import-behind alarm) still run via their GitHub schedules. So the pulse Kevin wants is *armed but dormant* until the PAT. Steps: `cloudflare-trigger/README.md`. After: verify a dispatch, then remove the `schedule:` blocks from pipeline.yml + entities.yml.
- **🔔 Sentry → Slack notifications (Kevin keen — build+test FIRST next session). PROJECT IS READY.** Sentry project `list-maker` exists (org `khg-y1`; **DSN is in the Sentry project settings / this session's chat — do NOT commit it to this public repo**). Sentry charges for native Slack; eachie's free workaround is the pattern.
  - **Two parts:** (1) instrument the Worker so errors + cron check-ins reach Sentry; (2) route Sentry alerts to Slack.
  - **(1) Worker instrumentation** — `@sentry/cloudflare` SDK. `npm install @sentry/cloudflare` (this turns `cloudflare-trigger/` from a plain worker.js into a bundled Worker — add `package.json` + `compatibility_flags = ["nodejs_compat"]` in wrangler.toml). Wrap the handler with `Sentry.withSentry(env => ({ dsn }), handler)` for error capture, and wrap each dispatch in `Sentry.withMonitor("list-maker-cron", () => dispatch(...))` for automatic CRON CHECK-INS (in-progress/ok/error). A missed check-in = Sentry knows the trigger is dead. *(Lighter alt if you want to keep the Worker plain JS: skip the SDK and just `fetch` the cron check-in URL `https://o<org>.ingest.sentry.io/api/<project>/cron/<slug>/<key>/?status=ok` on each dispatch.)*
  - **(2) Slack routing — CLEANEST is to REUSE eachie's already-deployed handler.** `~/DevKev/personal/eachie/app/api/webhooks/sentry/route.ts` already HMAC-verifies (`SENTRY_WEBHOOK_SECRET`, `sentry-hook-signature` header) → builds a Block Kit card → posts per-project via `getWebhookForProject(projectSlug)`. Just add `'list-maker': process.env.SLACK_WEBHOOK_LIST_MAKER_ERRORS` to that map (route.ts ~88) + the channel in `~/DevKev/personal/eachie/src/lib/slack.ts`, set the new webhook env var in eachie's deploy, redeploy eachie, and point list-maker's Sentry alert (internal integration webhook) at eachie's `/api/webhooks/sentry` URL. No new Worker route needed; one tested handler serves all projects. (Insight: a missed cron check-in becomes a Sentry *issue*, so the issue-webhook handler covers the dead-trigger alarm too.)
  - **Then:** make a #list-maker-errors Slack incoming webhook, create the cron monitor + an alert rule (webhook action) in Sentry, fire a test error (`setTimeout(() => { throw new Error() })`), verify the card lands in Slack. Source maps optional: `npx @sentry/wizard@latest -i sourcemaps --saas --org khg-y1 --project list-maker`.
- **🔎 Transcripts UX — surface the extracted mentions INSIDE each transcript page (Kevin wants this).** Use case: he's reading a transcript to pull insights, and we've ALREADY extracted the key entities for that episode — so show them. They should appear **up top** and ideally be **linked to where they occur in the body** (a table of contents and/or highlighting — he's open). Implementation needs thought, but the sketch: (1) add a Notion **relation** property "Mentions" on the Transcripts DB → the entity pages in the Tech DB (`982dafa0…`), set at sync time from the chain `ai_mentions(episode_id) → entity_id → ai_entities.notion_page_id`; (2) a top-of-body **"Key mentions"** callout/section listing each entity (name + type) as a **link to its entity page** (where Kevin can see that entity across all episodes); (3) stretch: **bold/highlight** the entity canonical_names where they appear in the transcript body (annotate at block-build time — mind the 1900-char chunk boundaries) and/or anchor a TOC to first-occurrence. Reuses the mention→entity→notion_page_id chain already in Neon + the synced entity pages. Build into `sync_transcripts_notion.py` (idempotent re-sync — adopt-don't-duplicate already handles existing pages; you'll PATCH props + prepend the mentions section). *Verify against reality: open a page after, confirm the links resolve to real entity pages.* (The DB view is now sorted newest-first so AI Daily is visibly present — that earlier confusion is resolved.)
- **📚 Add the 4 research guides to the GLOBAL CLAUDE.mds (Kevin asked; do in clean context).** Canonical sources: `~/DevKev/helper/claude-configs/`. Add a "Standing research references" section (thoughtful wrapper: what each is + when to apply) pointing at the 4 `*_AGENTS MAIN READ ME FILE.md` guides above. They're universal. Then note in `~/DevKev/hg-agents` (a plan/memory) to turn them into proper skills (`/skill-creator`). Commit + push helper; rebuild symlinks if structural (`setup-claude-configs.sh`). *(Deferred from this session deliberately — a global config edit deserves clean context, not a 100k-token tail.)*
- **SOP/TAL catch-up** — blocked on the music-pipeline debug above (the 2026-06-07 catch-up runs revealed the bugs; they did NOT fully catch up).

---

## The bar
Five questions answered with one named thing each; five contracts closed. We're close: the only one not yet solid is "how do you know the output stayed good?" on the MUSIC side — which is exactly the next workstream. Hold the depth + the verify-against-reality discipline. Don't do the minimum.

*Embodies hg-save-it (reasoning over rules) + hg-project-management (useful density, single source of truth, continuity).*

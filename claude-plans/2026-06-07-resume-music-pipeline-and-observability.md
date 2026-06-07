# Resume — list-maker: music-pipeline debug, notifications, + the open items
*Written 2026-06-07 (late) by the instance that did the work, for the next instance (compaction OR fresh session). You are MID-BUILD on a durable, self-healing, "never-touch-it-again" rebuild — NOT starting fresh. The #1 failure mode here is doing the minimum / executing the literal next bullet without re-grounding in WHY. This session went deep for good reasons; understand them before you touch anything.*

---

## READ FIRST — required, in order. These are the *why*, not background.

**The research guides (universal principles — most of this work descends from them). Read the ones relevant to what you're doing:**
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-05-22-running-things-off-my-laptop/2026-05-22-running-things-off-my-laptop--primer_AGENTS MAIN READ ME FILE.md` — durable execution; the five operational contracts; the GitHub-cron 60-day silent-disable; the **five questions** every project answers with one named thing (what starts it / where it runs / where it remembers / where a human approves / how you'll know the output stayed good). *This whole pipeline is an instance of it.*
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-04-23-database history and design/memory-systems-rollup-v2_AGENTS MAIN READ ME FILE.md` — provenance + valid-time; **scripts own deterministic ops, agents earn ambiguity** (list-maker is script-first with one LLM call at extraction); **evals are the only honest gradient**; the Willison/filesystem baseline.
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-06-03-codebase-legibility-and-maintenance/2026-06-03-codebase-legibility-and-maintenance--guide_AGENTS MAIN READ ME FILE.md` — executable-tells-truth / inert-lies-silently / delete-don't-disclaim / tests-are-the-honest-gradient.
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-06-03-dependency-security-hygiene/2026-06-03-dependency-security-hygiene--guide_AGENTS MAIN READ ME FILE.md` — supply-chain/dep hygiene (relevant when touching requirements / the Worker).

**Then:** `NOW.md` (live state), `claude-plans/2026-06-07-session-handoff.md` (the 7 ways-of-working), `claude-plans/2026-06-06-durable-pipeline-resume.md` (failure modes + grounding). The prior resume `claude-plans/2026-06-07-resume-cloudflare-evals-transcripts.md` is ✅ executed.

---

## WAYS OF WORKING — the hard-won insights (load-bearing; this is the heart)

1. **The plan is the FLOOR of ambition, not the letter.** This session's best work came from diverging when reality demanded — the shared-Notion-DB redesign, the COALESCE source path, and (this latest stretch) re-architecting the pulse around a *second source* when "days since our latest" turned out to measure the wrong thing. Read the plan for intent; synthesize with what's TRUE.

2. **VERIFY AGAINST REALITY — the throughline of this whole session, and it kept paying off.** Kevin's instinct: *"make sure these can't lie."* Never declare done from a success message or a DB marker — check the actual destination/source. This session it caught, in order: the Notion ReadTimeout that died mid-sync; "cool archived it" (wasn't); the importer log that lied; **and most spectacularly, the second-source feed check caught SOP behind 1 + TAL behind 2 — a MONTHS-stale music-pipeline failure that "21 days ✅" was hiding.** The green was lying; now it can't. When you build a check, ask: *what would let this show green when it's actually broken?* (Codex found 5 such paths in the second source — all closed.)

3. **The gate is non-negotiable: `codex:codex-rescue` + `/triple-check` at every meaningful step.** It earned its keep relentlessly — the transcript dedup-on-resume bug, the pulse-can-succeed-without-posting bug, the 5 second-source lie-paths. Fix EVERYTHING it finds (critical, important, AND minor).

4. **Surface genuine forks; don't grind through them.** Kevin steered hard this session (Option A, second-source-vs-Spotify, the pulse UX). When a re-issued instruction contradicts a just-found reality, hold the conflicting part and say so — keep everything un-blocked moving.

5. **Autonomy + narrate; bank continuously.** Commit each coherent unit (scope-prefixed, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`), push (owned repo), bank `NOW.md`. Long jobs → background; verify on completion. psycopg2 for migrations (Neon MCP destructive ops are hook-blocked). Deletions/archives need Kevin's clear intent.

6. **Observability must be TRUSTWORTHY + actionable + low-noise.** This session's alert philosophy, settled with Kevin: failures → Slack; a biweekly *pulse* is the positive heartbeat (its absence = trigger down); NO per-run success pings (daily shows = spam); every signal links to its destination + cross-checks a second source so it can't lie. Hold that bar.

7. **DON'T DO THE MINIMUM.** The value was depth — root-causing, redesigning, hardening, mapping to the WHYs. Re-ground (guides + this doc), then continue with that depth.

---

## STATE — shipped this session (committed + gated; don't redo, verify if unsure)

- **Eval harness** (`evals/extraction/`) — deterministic scorers, frozen baseline + hand-verified gold, gated runner, weekly `eval.yml`. KEY FINDING baked in: gpt-4.1-mini extraction has ~40% run-to-run SET churn at temp 0, so the gate uses stable aggregates (yield, type-dist, gold recall, confidence contract), not set identity. `evals/README.md`.
- **Neon FTS** (`search_transcripts.py` + generated tsvector/GIN, sql/005) — power search.
- **Notion Transcripts DB** (`3780501e-f950-81c9-a3e3-eca7f1162c9d`) — all **1,196** tech transcripts (997 AI Daily + 199 Hard Fork), idempotent sync (`sync_transcripts_notion.py`). *Verified in Notion 997+199.* (Default view is sorted oldest-first so 2022 Hard Fork is on top — **TODO: add a grouped/newest-first view** so AI Daily is obviously present.)
- **Cloudflare durable trigger** (`cloudflare-trigger/`, deployed, 6 crons) — drives entities/music/eval/pulse via workflow_dispatch + trigger-failure Slack.
- **Trustworthy observability** — `pulse_report.py` (biweekly, hub link + second-source per-show + destination links + recent counts), `feed_check.py` (Taddy for 5 shows + Megaphone RSS for Gabfest; returns None=unverified for every can't-check case so it never shows false green), daily second-source alarm in `data_health.py`, the Gabfest false-positive fix, per-run success pings removed.
- ~120 tests, all green.

---

## ⚠️ THE NEXT WORKSTREAM — debug the MUSIC pipeline (SOP/TAL). This is the real one.

The second source caught a **pre-existing, months-stale** music-pipeline failure (NOT this session's regression — we never touched SOP/TAL scraping internals; the new observability just made it visible). Hard evidence from the 2026-06-07 catch-up runs:
- **SOP:** found 1 new episode but scraped **0 songs** from it and added **0** to the playlist. Playlist is behind Neon (~3,727 live vs ~4,244 matched). Last real additions: **Mar 13**.
- **TAL:** scraper found **0 new episodes** though Taddy shows it's behind 2. Last additions: **Jan 13**.
- Descriptions are stale because `update_playlist_description` only runs on a *successful* sync.

**Investigate (verify against reality at each step — query Neon, run the scraper, read the live website):**
1. **SOP song scraper** (`pipeline/scrapers/sop/`) — why 0 songs from a real episode? Website markup change? Run it on the new episode and read the output.
2. **TAL episode scraper** (`pipeline/scrapers/tal/`) — why 0 episodes when Taddy has 2 newer? TAL's RSS 403s for bots (I hit it) — is that the cause? Consider Taddy as the episode source for TAL too.
3. **Playlist sync** (`pipeline/sync_playlist.py`) — the ~500-song Neon-vs-Spotify gap: is `get_matched_track_ids` vs existing-tracks dedup wrong, or is it only syncing new-episode songs (never reconciling the historical gap)? `add_tracks_to_playlist` reported "0 added" — why?
4. Once fixed, a full re-sync should fill the playlists + refresh descriptions. The daily second-source alarm + the pulse will confirm SOP/TAL go ✅ caught-up.

(SOP/TAL catch-up runs were triggered 2026-06-07 — they revealed the above; they did NOT fully catch up.)

---

## OPEN ITEMS

- **🔑 GH PAT — CRITICAL UNLOCK.** Until Kevin sets the `GH_PAT` Worker secret, the Worker can't fire, so **`pulse.yml` + `eval.yml` (Worker-only, no GitHub schedule) DON'T RUN AT ALL.** pipeline.yml + entities.yml (+ the daily import-behind alarm) still run via their GitHub schedules. So the pulse Kevin wants is *armed but dormant* until the PAT. Steps: `cloudflare-trigger/README.md`. After: verify a dispatch, then remove the `schedule:` blocks from pipeline.yml + entities.yml.
- **🔔 Sentry → Slack notifications (Kevin keen — build+test FIRST next session). PROJECT IS READY.** Sentry project `list-maker` exists (org `khg-y1`; **DSN is in the Sentry project settings / this session's chat — do NOT commit it to this public repo**). Sentry charges for native Slack; eachie's free workaround is the pattern.
  - **Two parts:** (1) instrument the Worker so errors + cron check-ins reach Sentry; (2) route Sentry alerts to Slack.
  - **(1) Worker instrumentation** — `@sentry/cloudflare` SDK. `npm install @sentry/cloudflare` (this turns `cloudflare-trigger/` from a plain worker.js into a bundled Worker — add `package.json` + `compatibility_flags = ["nodejs_compat"]` in wrangler.toml). Wrap the handler with `Sentry.withSentry(env => ({ dsn }), handler)` for error capture, and wrap each dispatch in `Sentry.withMonitor("list-maker-cron", () => dispatch(...))` for automatic CRON CHECK-INS (in-progress/ok/error). A missed check-in = Sentry knows the trigger is dead. *(Lighter alt if you want to keep the Worker plain JS: skip the SDK and just `fetch` the cron check-in URL `https://o<org>.ingest.sentry.io/api/<project>/cron/<slug>/<key>/?status=ok` on each dispatch.)*
  - **(2) Slack routing — CLEANEST is to REUSE eachie's already-deployed handler.** `~/DevKev/personal/eachie/app/api/webhooks/sentry/route.ts` already HMAC-verifies (`SENTRY_WEBHOOK_SECRET`, `sentry-hook-signature` header) → builds a Block Kit card → posts per-project via `getWebhookForProject(projectSlug)`. Just add `'list-maker': process.env.SLACK_WEBHOOK_LIST_MAKER_ERRORS` to that map (route.ts ~88) + the channel in `~/DevKev/personal/eachie/src/lib/slack.ts`, set the new webhook env var in eachie's deploy, redeploy eachie, and point list-maker's Sentry alert (internal integration webhook) at eachie's `/api/webhooks/sentry` URL. No new Worker route needed; one tested handler serves all projects. (Insight: a missed cron check-in becomes a Sentry *issue*, so the issue-webhook handler covers the dead-trigger alarm too.)
  - **Then:** make a #list-maker-errors Slack incoming webhook, create the cron monitor + an alert rule (webhook action) in Sentry, fire a test error (`setTimeout(() => { throw new Error() })`), verify the card lands in Slack. Source maps optional: `npx @sentry/wizard@latest -i sourcemaps --saas --org khg-y1 --project list-maker`.
- **📚 Add the 4 research guides to the GLOBAL CLAUDE.mds (Kevin asked; do in clean context).** Canonical sources: `~/DevKev/helper/claude-configs/`. Add a "Standing research references" section (thoughtful wrapper: what each is + when to apply) pointing at the 4 `*_AGENTS MAIN READ ME FILE.md` guides above. They're universal. Then note in `~/DevKev/hg-agents` (a plan/memory) to turn them into proper skills (`/skill-creator`). Commit + push helper; rebuild symlinks if structural (`setup-claude-configs.sh`). *(Deferred from this session deliberately — a global config edit deserves clean context, not a 100k-token tail.)*
- **SOP/TAL catch-up** — in progress / blocked on the music-pipeline debug above.
- **Sentry project** — Kevin creating (`list-maker`, Cloudflare Workers platform, "I'll create my own alerts later", name list-maker).

---

## The bar
Five questions answered with one named thing each; five contracts closed. We're close: the only one not yet solid is "how do you know the output stayed good?" on the MUSIC side — which is exactly the next workstream. Hold the depth + the verify-against-reality discipline. Don't do the minimum.

*Embodies hg-save-it (reasoning over rules) + hg-project-management (useful density, single source of truth, continuity).*

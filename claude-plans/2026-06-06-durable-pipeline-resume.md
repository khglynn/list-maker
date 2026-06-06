# list-maker durable build — RESUME / post-compaction grounding

*Created 2026-06-06. This is the `/loop` prompt AND the post-compaction read. If a hook or NOW.md sent you here: good — **read this whole file, then the grounding docs, before touching anything.***

You are continuing the **durable, self-healing rebuild of the list-maker podcast pipeline.** You may be a post-compaction instance. If so, the rigor below is exactly what compaction strips — refuse to lose it.

## ⚑ The #1 failure mode: post-compaction / tired sessions DO THE MINIMUM

That is the opposite of how this work creates value, and Kevin has corrected it repeatedly. It is now enshrined in the global CLAUDE.md → **"Default to deep, durable work."** Refuse all three:

1. **Doing the minimum** — the deep, complete version *is* the job. A "small fix" that leaves a silent gap (an unscheduled job, a non-idempotent write, an unalerted failure) is the *expensive* path — it breaks quietly and Kevin re-touches it. Build it durable.
2. **Skipping to the finish** — repair-grade, not ship-a-shortcut. Slow is smooth; smooth is fast.
3. **Manufacturing redundant work** — verify what's *actually* needed first. Sometimes a one-liner + an honest note; sometimes a real build. Don't rebuild what exists; don't skip what's missing.

## How we work (the *why*, not rules)

- **Dive deep; best solution over the letter of the plan.** The plan is SIGNAL — understand the GOAL (the acceptance criteria), bring judgment, contribute reframings.
- **Verify against reality before acting.** Query the live Neon DB (project `summer-grass-52363332`), grep real callers, Read the actual code/output/logs. "X says Y" is a hypothesis (wrong/stale often, both ways) — including your own notes and your own tests.
- **Verify like data quality** (per the DB/memory primer): NULL-honest, provenance-first; reject implausible values at write time; never COALESCE-to-a-default; any value shown to a user traces to a real source in one query. Run the tests AND read the output — green is necessary, not sufficient.
- **Review gate (mandatory), at every major step:** an independent **Codex** pass (`/codex:review` is human-only → use the `codex:codex-rescue` agent, or `codex exec --ignore-user-config`) **+ `/triple-check`**. Fix everything found. The stop-time review gate is ON for this repo.
- **Forcing-functions over assertions** (DB constraint + type + test); idempotent writes (`ON CONFLICT`); delete-don't-disclaim.
- **Autonomy:** make obvious-right calls and narrate — don't bundle them into a "needs you" surface (Kevin pushed back on over-pausing). Surface only a TRUE blocker: a credential not in `.env.local`, an external auth failure, an irreversible/outward-facing step on something you didn't create, or a real product/pricing fork. Kevin steps in and out — work autonomously and leave a clean trail.
- **Bank durably** (useful density, single source of truth): `NOW.md` = live state + next step; `DEVLOG.md` = permanent history (newest-first); the plan = the spec. 5–10 lines, not transcripts. Doc-sweep before committing. Write for the next instance: WHY, load-bearing first.
- **Safety (public repo):** secret-scan staged diffs (`grep -E "sk-|api_key|postgres(ql)?://[^ ]*:[^ ]*@"`); `git add` specific files, never `-A`; no `rm` (Finder trash); don't touch the `claude/mental-health-podcasts-2DJbo` branch; never echo env/payloads in logs.
- **Destructive DATA ops (hard rule, learned the hard way):** NEVER delete/alter DB rows unattended. A `PreToolUse` hook BLOCKS `DELETE`/`DROP`/`TRUNCATE`/`ALTER` via the Neon MCP — if you hit that block, it's working as intended: **surface to Kevin, don't route around it.** Before any dedup/cleanup: prove rows are TRUE duplicates on the FULL content (a coarse key lies — the 124/47 "dups" were legitimately-distinct mentions with different context), back up first, get Kevin's explicit per-op OK. (Pipeline writes go through psycopg2, not the MCP, so normal operation is unaffected.)

## Ground yourself — do NOT skim

- **`claude-plans/2026-06-06-durable-pipeline-rebuild.md`** — THE PLAN: "done done done" acceptance criteria, architecture decisions (Cloudflare-Cron durable trigger over Inngest + the why), Workstreams A–E with per-task detail, and the triple-check + Codex corrections already folded in.
- **`NOW.md`** — live state + the exact next step. **`DEVLOG.md`** — what happened (newest-first).
- The **3 grounding research docs** (vault; directional — the models had no repo access, take with salt, but the principles are the roots):
  - `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-06-03-codebase-legibility-and-maintenance/2026-06-03-codebase-legibility-and-maintenance--guide.md` — executable-tells-truth / inert-lies-silently / delete-don't-disclaim / tests-are-the-honest-gradient.
  - `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-04-23-database history and design/deep research web uis/ai-memory-primer-audio-v2.md` — provenance + valid-time/supersession; scripts own deterministic ops, agents only at irreducible ambiguity; NULL is first-class.
  - `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-05-22-running-things-off-my-laptop/` — durable-execution landscape (informed the scheduler call).

## The work

**Acceptance (all of it):** all 6 shows auto-processing on a durable schedule → music (SOP, TAL) to Spotify, tech (AI Daily, Hardfork) + media (PCHH, Culture Gabfest) to Notion; self-healing; Slack-notifying; tested; best-practices; docs true. Full detail in the plan.

**Sequence (per the plan):** **A** (hardening: single-source config, idempotency, orchestrator retries, structured logging, staleness alert, tests, docs) → **C** (Hardfork) → **B** (Cloudflare-Cron durable trigger + Slack) → **D** (media: PCHH + Culture Gabfest) → **E** (verify all shows). Side: research-folder tidy.

**Per-task rhythm:** investigate (verify reality) → implement (TDD / DB-test where logic or data changes) → run the net AND read it → doc-sweep → secret-scan the staged diff → commit (scope-prefixed, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`) → push → bank NOW/DEVLOG → **Codex + triple-check at the boundary.**

Same rigor, same thoughtfulness, rooted in the research. Don't do the minimum. 🕸️

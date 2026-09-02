# Post-Compaction Prompt — list-maker: Cloudflare + Evals + Transcripts
*Written 2026-06-07 evening by the instance that did the work, for the next instance. You are MID-BUILD on a durable rebuild — not starting fresh. The #1 failure mode here is doing the minimum / executing the literal next bullet without re-grounding in WHY. Refuse it.*

> **✅ EXECUTED 2026-06-07 (session 2) — see `NOW.md` for live state.** All three workstreams done: (1) Cloudflare Worker deployed (5 crons, both workflows + eval, trigger-failure Slack) — only the `GH_PAT` secret + `schedule:`-removal pending Kevin; (2) eval harness shipped (`evals/`, gate calibrated to measured noise); (3) transcripts searchable BOTH (Neon FTS `bbba4fa` + Notion DB `9220b52`, backfill of 1,193 eps run). Don't re-do these — read NOW.md, then continue from whatever's still open (Kevin's PAT → verify dispatch + remove schedules). The WHY-grounding below is still the way of working.

---

## READ FIRST — required, in order. These are the *why*, not background. Do not skip them and do not start work until you've rooted in their reasoning.

**1. Both research primers — read them FULLY and root your decisions in their arguments. They are the reason the three workstreams below exist:**
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-05-22-running-things-off-my-laptop/2026-05-22-running-things-off-my-laptop--primer.md`
  — *Running Things Off Your Laptop.* Durable execution as the convergent substrate; the **five operational contracts** (secrets, idempotency, retry, dead-letter, observability) + the human-attention contract; **the GitHub-cron 60-day silent-disable** (it OPENS with this — it is our #1 live risk); the **five questions** every project must answer with one named thing each (what starts the work / where it runs / where it remembers / where a human approves / how you'll know the output stayed good); "if a human must touch the system in normal operation, you have a bug."
- `~/Documents/HG Main/0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/Q2/2026-04-23-database history and design/deep research web uis/ai-memory-primer-audio-v2.md`
  — *How AI Systems Remember.* **Scripts own deterministic operations; agents earn their keep only at irreducible ambiguity** (list-maker IS this — script-first with one LLM call at the ambiguous extraction step); **evals are the only honest gradient** ("the first thing you do before any architecture change is build the eval harness"); the **filesystem/Willison test** (SQLite + import scripts beats fancy memory systems; transparent stores a smart model can navigate); **provenance + time** (valid-time, ingestion-time, source, superseded_by — don't overwrite history).

**2. `claude-plans/2026-06-07-session-handoff.md`** — the WAYS OF WORKING (the 7 hard-won insights, load-bearing). The condensed version is below; that doc is the full version. Read it.

**3. `NOW.md`** — live state + exact next step. **4. `claude-plans/2026-06-06-durable-pipeline-resume.md`** — failure-modes + grounding.

> **Requirement:** for each workstream below, state which primer principle it serves before you build it. If you can't connect the work to a WHY, you're doing the minimum — stop and re-read.

---

## WAYS OF WORKING — rolled up (hg-project-management + hg-save-it + this session's scars)

1. **The plan is the FLOOR of ambition, not the letter to follow.** This session's best work came from *diverging* when reality demanded — and that was correct: Option A (one shared Notion DB, global entities + a Shows tag) over the plan's "clone a DB per show"; the `COALESCE(transcript, description_body)` source path so Gabfest (no transcripts) extracts from show-notes; every silent-failure root-cause. Read the plan for intent; synthesize with what's actually TRUE. Kevin's direction is *signal*, not spec — bring new framings, push back out loud.

2. **Verify against reality — ALWAYS check the output, never the log.** The scars this session: CI runs showed "cancelled," not "failed," so 3 weeks of Spotify hangs sent ZERO Slack alerts; the orchestrator swallowed partial failures and exited 0 ("success"); "cool archived it" — the Hard Fork DB was **not actually archived** (caught by fetching it); the importer log said "imported 196" while the DB had 3. Never declare done from a success message. Query the real DB, the real run history, the real Notion page.

3. **The gate is non-negotiable: `codex:codex-rescue` + `/triple-check` at every meaningful step.** It earned its keep repeatedly: caught 4 media blockers (the `ai_mentions.mention_type` CHECK would've crashed the first media load; Gabfest's inner-JOIN found 0 episodes), the Gabfest-import gap (stop-review), and 6 durability gaps (timeout→silent, partial-failure→silent, staleness→no-Slack, fragile Slack delivery). Fix everything it finds — critical, important, AND minor.

4. **Surface genuine forks to Kevin; don't grind through them.** Option A was his call (shared vs per-show DB); the transcripts approach was his call (he chose BOTH). When a re-issued prompt contradicts a just-discovered reality, hold the conflicting part and say so — but keep everything un-blocked moving.

5. **Autonomy + narrate; the loop drives; bank continuously.** Self-paced `/loop`; commit each coherent unit (scope-prefixed, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`), push, then bank `NOW.md`. Long runs go to the background; verify on the completion notification. Neon-MCP destructive ops are hook-blocked → psycopg2 for migrations; deletions/archives need Kevin's clear intent (a question ≠ consent).

6. **Durable / self-healing / observable — the contracts (this is literally the laptop primer).** This session closed the failure-VISIBILITY contracts: hangs now fail loud (step timeouts), partial failures exit non-zero and alert, the staleness check now Slacks. "An operation has failure states you can see; it distinguishes *nothing to do* from *we didn't check*." Keep closing contracts — the remaining gaps (control plane, evals) are exactly what the next workstreams are.

7. **POST-COMPACTION: do NOT do the minimum.** The whole value was depth — root-causing, redesigning, hardening, and mapping the work to the primers' reasoning. An instance that does the literal next task without re-grounding in the WHY will quietly undo that. Re-ground (primers + handoff), then continue with that depth.

---

## STATE — DONE this session (do not redo; verify if unsure)
- **Media build COMPLETE + validated** — shared "Media Recommendations" Notion DB (`3780501ef95081a783ebf8a32fa94657`); PCHH + Gabfest extract (Gabfest from Megaphone show-notes via the COALESCE source path); one extractor / two profiles (tech vs media). All 6 shows flow end-to-end.
- **Option A shared Tech DB** (`982dafa0…`) — AI Daily + Hard Fork, global entities + Shows tag, verified 1275/1275.
- **Durability hardening** — step-level timeouts (hang → loud failure), orchestrator exits non-zero on partial failure, `data_health.py` posts staleness to Slack, Gabfest daily import wired, Slack `curl --retry`. Codex-reviewed.
- **Hard Fork old DB archived** (`3780501ef9508154998ff4cbe82afedf` → trash). Hub page "Pod Lists" turned into an ops manual (tech stack, schedules, troubleshooting, links, emojis).
- Daily cron covers all 4 entity/media shows; SOP/TAL music on Mon/Wed/Fri; secrets all set.

---

## THE WORK — three workstreams. GATE each (verify reality → build/TDD → pytest → codex + triple-check → fix all → secret-scan → commit → push → bank NOW). Root each in its primer WHY.

### 1. Deploy the Cloudflare Worker  *(WHY: Primer 1's headline — GitHub disables public-repo crons after 60 idle days; this is the exact anti-pattern it opens with, and it is LIVE in our system right now. This is the highest-leverage move on the board.)*
Collaborative. **Kevin does:** (a) `! env -u CLOUDFLARE_API_TOKEN wrangler login` → pick **trimm** (if not cached); (b) create a fine-grained GitHub PAT for `khglynn/list-maker` with **Actions: read & write**. **You do:** `env -u CLOUDFLARE_API_TOKEN wrangler whoami` → put the account_id in `cloudflare-trigger/wrangler.toml`; `wrangler secret put GH_PAT` (Kevin's PAT); `wrangler deploy`; hit the Worker URL → confirm a run appears in the Actions tab; then remove the `schedule:` blocks from the workflows it now triggers (one commit). **Design point to resolve:** the Worker currently dispatches `entities.yml` only — extend it to ALSO dispatch `pipeline.yml` (music, Mon/Wed/Fri) so BOTH lose the fragile `schedule:`, OR consciously decide pipeline.yml keeps its GitHub cron. Don't half-do it. Steps: `cloudflare-trigger/README.md`. Verify a real dispatch before declaring done.

### 2. Build the extraction eval harness  *(WHY: Primer 2 — "evals are the single highest-leverage investment; the first thing before any architecture change." Right now a model bump = shipping on vibes. Also closes Primer 1's fifth question: "how will you know the output stayed good?")*
For the TECH shows (AI Daily + Hard Fork). Check first whether an A8 golden-master test already exists (`tests/` + the plan mention) — extend, don't duplicate. Build: (a) a **frozen test set** — ~30-50 real tech episodes with the current good extraction captured as a golden baseline, PLUS a small hand-verified subset (~10 eps) with the *correct* entities annotated; (b) a **runner** that re-extracts and scores against it with **deterministic metrics** (entity precision/recall, type accuracy, confidence-in-range) — per the primer, reserve LLM-as-judge only for narrow checks ("is this a real product?"), not for grading overall quality (transitivity/position bias); (c) wire it to run before/after a model change and ideally a weekly CI job against the frozen set. The point isn't "did we write a file" — it's "does extraction quality hold when the model under us shifts."

### 3. Tech-show transcripts searchable — BOTH (Kevin's call)  *(WHY: Primer 2 — transcripts are evidence-memory. Neon FTS is the Willison-baseline where they already live (the system's structured store); the Notion DB is the human layer — and **Kevin specifically wants the Notion copy so he can query the transcripts with Notion AI** (natural-language Q&A over the whole corpus). That's the concrete payoff of the Notion side, beyond browsing — so the Notion build must be Notion-AI-friendly: real per-episode pages with the full transcript text in the body, clean titles/dates so Notion AI can cite. The two halves solve different jobs, exactly as the primer describes.)*
Transcripts live in Neon `episode_transcripts` for the Taddy shows (AI Daily, Hard Fork, PCHH; Gabfest is show-notes). For AI Daily + Hard Fork:
- **(a) Neon full-text search:** add a generated `tsvector` column + GIN index on the transcript text (migration via psycopg2, additive), and a small `search_transcripts.py` (websearch_to_tsquery, `--query`, `--show`, snippets). The power-search.
- **(b) Notion Transcripts DB:** create a "Transcripts" DB under the Pod Lists page (`31c0501e-f950-80d1-a3fd-e8fa8d5ce907`); one page per episode (Name/Show/Date/Episode-link as properties) with the transcript in the page body, chunked to Notion's block + 2000-char limits. ~1,200 episodes → a **background, idempotent, batched** job (don't re-create existing pages; resumable). Verify a sample renders + is searchable. Note the build cost honestly before the big push; consider tech-only + recent-first if it's huge.

---

## The bar
Hold the depth from the handoff. Gate every step. Complete each build (no hanging chads). Verify against reality before declaring done. When all three ship (or all that's left is blocked on Kevin), bank NOW + tell Kevin one consolidated summary. The five questions and the five contracts from the primers are the rubric — by the end, list-maker should answer all five questions with one named thing each, and have all five contracts closed, not partial.

*This handoff embodies hg-save-it (durable instructions for Claude — reasoning over rules) and hg-project-management (useful density, single source of truth, session continuity).*

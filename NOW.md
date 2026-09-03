# NOW — list-maker

**Last updated:** 2026-09-03 · **Mode:** live (routine cron-driven operation since 2026-06-07; hardening on top)

## Right now
- **The curated intake is live and has ingested its first batch.** Arc on `main` 2026-09-02 (PR #33); Kevin approved all 75 eval labels unchanged the same evening, so auto-ingest was flipped on (PR #38) and the per-post Notion sync batched into one pass per run (PR #39). The first live run (09-02, 18:17–18:45 CT) saved **49 posts** — 38 OpenAI, 8 Anthropic, 3 other — all 49 mirrored to Blog Posts, **481 mentions across 389 entities** into the Tech DB, 30 skipped, 0 failed. The sponsor retag was applied (230 mentions / 45 entities). Next scheduled intake: Monday 2026-09-07, 15:30 CT.
- Hotfix PR #30 merged 16:34 CT; today's 15:30 daily run had already failed on that bug and was re-run for the tech shows (extraction fine; the health check stayed red on the PCHH backfill's Notion drift, which tomorrow's run clears). Issue #34 closed with the cause.
- The Cloudflare Worker is unchanged (`92b6a638`): entities daily, blogs Mondays now run `run_intake.py`.
- Full story: `DEVLOG.md` 2026-09-02; design and eval reads: `claude-plans/2026-09-02-curated-intake-v2/PLAN.md`.

## Open items (need Kevin)
1. **Glance at the Tech DB and Blog Posts** for last night's 49 saves (the "Would save (latest)" view in 📥 Blog Intake lists them). Anything wrong is a rubric note for v3; anything missing is a "Pull anyway" tick.
2. **Read the first shadow verdicts** in 📥 Blog Intake (40 would-saves, 11 of them disputed — the second judge is save-happy on OpenAI's feed, e.g. the teachers rollout post). Tick **Pull anyway** on anything skipped that you want; nothing waits on you.
3. **Dependabot #27–#29** still open.
4. ~~Backfill (decision 6)~~ — **complete 2026-09-02 17:38 CT:** PCHH 2,073 mentions + Culture Gabfest 3,660, every media entity mirrored to Notion (the ten-minute sync steps timed out and resumed until done; the limit is parked in `BACKLOG.md`). Working tree back on `main`, worktrees and merged branches removed.

## Next arc (agreed 2026-09-01, in progress since 2026-09-02) — "curated intake v2 + ads as data"
**PR 1 of 3 (ads as data) is open as a draft against `arc/curated-intake-v2`.**
Automated blog/article intake judged by an inexpensive classifier instead of a Notion checkbox; sponsor reads kept, tagged, and weight-capped. **Arc plan (design, schema, PR split): `claude-plans/2026-09-02-curated-intake-v2/PLAN.md`.** Spec, acceptance, and the kickoff paste: `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → "Next arc". After it: plan Phases 4 (feed check by identity, run watchdog via `fleet-watchdog`, transactional load) and 5 (Spotify-path tests).

## Accepted gaps (dated)
- 2026-09-01: the feed check compares dates, not episode identities — a re-dated episode can inflate a BEHIND count and a mid-series hole is invisible. Phase 4.
- 2026-09-01: nothing alarms when a run never starts (08-06 was cancelled, 08-16 never fired). Phase 4 watchdog.
- 2026-09-01: the Blog Pull Queue's 31 June rows are stale by design until intake v2 retires the checkbox model.
- 2026-09-02: a company that sponsors an episode AND is cited editorially in that same episode has the citation counted as an ad (2 of KPMG's 7 mentions, 4 of Gemini's 197). Deliberate — the entity-level Sponsor flag is true either way, both counts are published, and the 5-ad cap bounds the cost. Separating the two needs a judgement the deterministic detector shouldn't be making.
- 2026-09-02: three known ad mentions are missed because the model paraphrased the `context_snippet` (it wrote "Blitzi" where the transcript says "Blitzy"), so the snippet can't be located in the transcript and neither window nor roster applies.

## Pointers
Plan `claude-plans/2026-09-01-ground-it-cleanup-plan.md` · history `DEVLOG.md` · parking lot `BACKLOG.md` · design `ARCHITECTURE.md` · rules `docs/principles.md` · trigger `cloudflare-trigger/worker.js`

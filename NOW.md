# NOW — list-maker

**Last updated:** 2026-09-02 · **Mode:** live (routine cron-driven operation since 2026-06-07; hardening on top)

## Right now
- `main` carries PR #4 (alert noise + failure paths), #11 (hygiene + dependency gate), #23 (an all-filtered extraction is a declared outcome) and the first Dependabot floor bumps. The Cloudflare Worker was redeployed 2026-09-01 (version `92b6a638`): one 20:30 UTC cron; the pulse runs *after* the import on the 1st/15th.
- Local venv rebuilt on Python 3.12 (matches CI). 224 pytest + 6 node tests green.
- The layout cleanup landed with this file: `web/` deleted, 2025/June plans archived, ROADMAP/COMPLETED folded into `BACKLOG.md` + DEVLOG, compaction hooks repointed here, five accidental batch dirs untracked.
- Full diagnosis of the August alerts and the blogs: `DEVLOG.md` 2026-09-01. Kevin's decisions: the plan's "Decisions" section.

## Open items (need Kevin)
1. **Merge PR #30** (the CSV-columns hotfix) before the 20:30 UTC daily run — still open at 13:45 CT on 09-02.
2. **Two SQL pastes, in order:** `pipeline/scrapers/ai_daily/sql/009_sponsor_provenance.sql` (one nullable column on `ai_mentions`), then `010_intake_candidates.sql` (the intake table). Both additive and idempotent. Paste-ready commands are in the chat.
3. **Then the retag:** `retag_sponsor_mentions.py --dry-run` (prints 229 mentions / 45 entities), then `--apply` (no deletes; touches the entities so the next daily sync republishes their Notion pages).
4. **The labels page** (artifact 69d95337): flip what's wrong, answer seven questions, press Done. Until then the eval runs on the reviewers' provisional labels.
5. **After 2–3:** the arc branch goes to `main` as one PR (intake in shadow mode + ads as data; 446 tests) — Kevin merges. The first real weekly run then fills the 📥 Blog Intake log with verdicts; auto-ingest stays off until the eval floor clears on Kevin's labels and one shadow week reads right.
6. **Dependabot #27–#29** still open.
7. **Backfill (decision 6):** PCHH done (2,073 mentions, Notion synced in ten-minute resumable attempts); Culture Gabfest's 909 episodes are extracting now, several hours, laptop open. The main working tree stays on the pre-merge arc commit until it finishes (the run reads pipeline files from disk); the merged arc is on origin.

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

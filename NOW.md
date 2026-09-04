# NOW — list-maker

**Last updated:** 2026-09-04 · **Mode:** live (routine cron-driven operation since 2026-06-07; hardening on top)

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

## Shipped: Phase 4 (2026-09-03 → 09-04) — health checks and data you can trust
All four PRs are merged into `arc/phase-4` (#41 Worker run verification + `/health`; #42 feed check by episode identity; #43 transactional batch load + two run checks; #44 honest-data fixes: NULL confidence, exit 2 = deterministic, the pulse on identity, scoped zero-mention check, ids in every FAIL, a 25% missing-confidence ceiling on the eval). Each got a five-lens review with Opus refuters; 550 Python + 40 Worker tests green; production verified read-only (0 failures / 2 warnings / 15 checks). Story: `DEVLOG.md` 2026-09-03. Plan + the five readers' notes: `claude-plans/2026-09-03-phase-4/`.
**Deployed 2026-09-04 00:40 CT:** PR #45 merged to `main`; the Worker runs version `ae31ff84` with the `DISPATCH_LOG` namespace bound; the fleet-watchdog runs `3e368f32` and reaches the Worker through a service binding (a same-zone `fetch()` was a 404 — found and fixed the same hour, self-hosted-mcps #2). Fleet status: healthy, `list-maker-cron` in its never-fired grace window until the 20:30 UTC cron. **Open for Kevin (one paste):** mention 1222's "NA10" is the transcriber's spelling of **n8n** ("the NA10 and the Zapiers and the MAKES"); the model's 0.5 was honest and stays. `sql/011_merge_na10_into_n8n.sql` folds the phantom entity into n8n (12 mentions, in Notion) and closes the review. Run it with:
```
cd ~/DevKev/personal/list-maker && set -a && source .env.local && set +a && ./pipeline/venv/bin/python -c "import sys; sys.path.insert(0,'pipeline'); from common import get_db_connection; c=get_db_connection(); cur=c.cursor(); cur.execute(open('pipeline/scrapers/ai_daily/sql/011_merge_na10_into_n8n.sql').read()); c.commit(); cur.execute('SELECT id, canonical_name, aliases FROM ai_entities WHERE id IN (295,793)'); print(cur.fetchall()); cur.execute('SELECT id, entity_id, canonical_name, confidence, needs_review, review_status FROM ai_mentions WHERE id=1222'); print(cur.fetchall())"
```
Expected: one entity row (295, n8n, aliases now including NA10) and the mention pointing at 295 with review resolved. n8n's Notion page picks up the extra mention on the next daily sync. 

## Mapped, not started: Phase 5 (2026-09-04, overnight) — cover the seams that carry money and music
`claude-plans/2026-09-04-phase-5/PLAN.md` (five reader notes beside it). **The map found a live outage, not just a coverage gap:** no This American Life episode published after **2026-05-10** has a single song row, and every Monday run has reported success. Two causes, both verified against the code and Neon on 09-04: (1) since the Taddy discovery fix, TAL rows arrive with `scraped_at` already stamped and a `api.taddy.org` url, while the website scraper queues only `scraped_at IS NULL` rows — so the queue is permanently empty and the URL would be wrong anyway (14 Taddy-discovered episodes since June, 0 songs); (2) the two May episodes that did get a website scrape (886, 887, on 08-03) also have 0 songs, so the page parser may have a second problem — PR 1's acceptance test will tell. Also found: Taddy dates TAL one day later than the website, so 886 and 887 exist twice and the duplicate repair keys on that date and cannot merge them. Six PRs, one owner per file, on a proposed `arc/phase-5`: the TAL fix first (queue by "has no songs yet" with a date floor; page URL from the RSS link with a title-slug fallback; never send Firecrawl to a Taddy URL), then tests for the matcher's confidence decisions, the playlist diff/dedup, frozen-fixture song parsers, saved items/episodes, and one shared Spotify matcher. Spec corrections: `feed_check` is already covered (Phase 4), `build_pull_queue` is gone, the venv is already 3.12.
**Kevin:** read the plan and say go (or not). One yes/no inside it: delete `scrapers/tal/scoring_match.py` (default yes). One deferred paste: the duplicate TAL rows.

## Done arc (2026-09-02) — "curated intake v2 + ads as data"
**PR 1 of 3 (ads as data) is open as a draft against `arc/curated-intake-v2`.**
Automated blog/article intake judged by an inexpensive classifier instead of a Notion checkbox; sponsor reads kept, tagged, and weight-capped. **Arc plan (design, schema, PR split): `claude-plans/2026-09-02-curated-intake-v2/PLAN.md`.** Spec, acceptance, and the kickoff paste: `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → "Next arc". After it: plan Phases 4 (feed check by identity, run watchdog via `fleet-watchdog`, transactional load) and 5 (Spotify-path tests).

## Accepted gaps (dated)
- 2026-09-04: TAL's Spotify playlist has had no new songs since 2026-05-10 and nothing alarmed — the health checks watch episodes (imported, caught up) but not songs per episode, and "found no work" is a success exit. Phase 5 PR 1 fixes the cause; a songs-per-episode check for the music shows is the follow-up that makes the next silence visible.
- 2026-09-01: the feed check compares dates, not episode identities — a re-dated episode can inflate a BEHIND count and a mid-series hole is invisible. Phase 4.
- 2026-09-01: nothing alarms when a run never starts (08-06 was cancelled, 08-16 never fired). Phase 4 watchdog.
- 2026-09-01: the Blog Pull Queue's 31 June rows are stale by design until intake v2 retires the checkbox model.
- 2026-09-02: a company that sponsors an episode AND is cited editorially in that same episode has the citation counted as an ad (2 of KPMG's 7 mentions, 4 of Gemini's 197). Deliberate — the entity-level Sponsor flag is true either way, both counts are published, and the 5-ad cap bounds the cost. Separating the two needs a judgement the deterministic detector shouldn't be making.
- 2026-09-02: three known ad mentions are missed because the model paraphrased the `context_snippet` (it wrote "Blitzi" where the transcript says "Blitzy"), so the snippet can't be located in the transcript and neither window nor roster applies.

## Pointers
Plan `claude-plans/2026-09-01-ground-it-cleanup-plan.md` · history `DEVLOG.md` · parking lot `BACKLOG.md` · design `ARCHITECTURE.md` · rules `docs/principles.md` · trigger `cloudflare-trigger/worker.js`

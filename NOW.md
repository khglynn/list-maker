# NOW — list-maker

**Last updated:** 2026-09-01 · **Mode:** live (routine cron-driven operation since 2026-06-07; hardening on top)

## Right now
- `main` carries PR #4 (alert noise + failure paths), #11 (hygiene + dependency gate), #23 (an all-filtered extraction is a declared outcome) and the first Dependabot floor bumps. The Cloudflare Worker was redeployed 2026-09-01 (version `92b6a638`): one 20:30 UTC cron; the pulse runs *after* the import on the 1st/15th.
- Local venv rebuilt on Python 3.12 (matches CI). 224 pytest + 6 node tests green.
- The layout cleanup landed with this file: `web/` deleted, 2025/June plans archived, ROADMAP/COMPLETED folded into `BACKLOG.md` + DEVLOG, compaction hooks repointed here, five accidental batch dirs untracked.
- Full diagnosis of the August alerts and the blogs: `DEVLOG.md` 2026-09-01. Kevin's decisions: the plan's "Decisions" section.

## Open items (need Kevin)
1. **Two SQL pastes** (decision 10, DDL is Kevin-run by design): `pipeline/scrapers/ai_daily/sql/007_drop_duplicate_episodes_url_index.sql`, then `008_songs_unique_per_episode.sql` (deletes the 3 space-padded duplicate song rows first).
2. **Decision 6:** PCHH + Culture Gabfest full-archive backfill — run once (~11h, ~$7.50) or write it off.
3. **Dependabot PRs still open:** #6–#10 (all `web/` — close themselves now that `web/` is gone), plus any floor bumps that were stale when merged one by one (#14, #19–#22 at last check).

## Next arc (agreed 2026-09-01) — "curated intake v2 + ads as data"
Automated blog/article intake judged by an inexpensive classifier instead of a Notion checkbox; sponsor reads kept, tagged, and weight-capped. Spec, acceptance, and the kickoff paste: `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → "Next arc". After it: plan Phases 4 (feed check by identity, run watchdog via `fleet-watchdog`, transactional load) and 5 (Spotify-path tests).

## Accepted gaps (dated)
- 2026-09-01: the feed check compares dates, not episode identities — a re-dated episode can inflate a BEHIND count and a mid-series hole is invisible. Phase 4.
- 2026-09-01: nothing alarms when a run never starts (08-06 was cancelled, 08-16 never fired). Phase 4 watchdog.
- 2026-09-01: the Blog Pull Queue's 31 June rows are stale by design until intake v2 retires the checkbox model.

## Pointers
Plan `claude-plans/2026-09-01-ground-it-cleanup-plan.md` · history `DEVLOG.md` · parking lot `BACKLOG.md` · design `ARCHITECTURE.md` · rules `docs/principles.md` · trigger `cloudflare-trigger/worker.js`

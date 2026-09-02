# NOW — list-maker

**Last updated:** 2026-09-02 · **Mode:** live (routine cron-driven operation since 2026-06-07; hardening on top)

## Right now
- `main` carries PR #4 (alert noise + failure paths), #11 (hygiene + dependency gate), #23 (an all-filtered extraction is a declared outcome) and the first Dependabot floor bumps. The Cloudflare Worker was redeployed 2026-09-01 (version `92b6a638`): one 20:30 UTC cron; the pulse runs *after* the import on the 1st/15th.
- Local venv rebuilt on Python 3.12 (matches CI). 224 pytest + 6 node tests green.
- The layout cleanup landed with this file: `web/` deleted, 2025/June plans archived, ROADMAP/COMPLETED folded into `BACKLOG.md` + DEVLOG, compaction hooks repointed here, five accidental batch dirs untracked.
- Full diagnosis of the August alerts and the blogs: `DEVLOG.md` 2026-09-01. Kevin's decisions: the plan's "Decisions" section.

## Open items (need Kevin)
1. **Merge PR #30** (hotfix: PR #23 left the episode-summary CSV columns out of step with the row, so every extraction batch failed after the model call) — before the 20:30 UTC daily run on 2026-09-02, or that run fails the same way. The working tree stays on `fix/episode-summary-csv-fieldnames` while the backfill below runs.
2. **Dependabot PRs still open:** #27–#29 (floor bumps for python-dotenv, requests, spotipy in `pipeline/`).
3. **Decision 6 is running:** the PCHH + Culture Gabfest full-archive extraction, relaunched 2026-09-02 09:33 CT on the fix branch (`backfill-media.log` at the repo root; the 09-01 attempt failed 64/64 batches on the bug above, log kept as `backfill-media-failed-2026-09-01.log`). ~5h; it syncs Notion at the end.
4. ~~Two SQL pastes~~ — 007 and 008 ran (verified 2026-09-02: `episodes_url_key` gone, the 3 duplicate songs gone, `songs_episode_title_artist_unique` present).
5. **SQL paste 009 — required before merging the ads-as-data PR.** `pipeline/scrapers/ai_daily/sql/009_sponsor_provenance.sql` adds `ai_mentions.sponsor_source` (additive, idempotent, no rewrite of existing rows). The loader writes that column unconditionally, so an un-migrated database fails the next extraction load. DDL is your paste; agents don't run it.
6. **Then the sponsor retag**, once 009 is in: `cd pipeline && ./venv/bin/python scrapers/ai_daily/retag_sponsor_mentions.py --dry-run` to re-read the list, then `--apply`. It reclassifies 246 already-stored mentions across 55 entities as sponsor reads (73 Blitzy, 13 Web3 with A16Z Crypto, 12 each Superintelligent and Vanta…). It only ever sets `is_editorial=false` + `sponsor_source`; it never deletes a row. The dry-run report lands in `pipeline/_cache/retag-sponsors-<date>.json`.

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

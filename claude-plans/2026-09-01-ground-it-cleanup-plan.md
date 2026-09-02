# Ground-it cleanup plan — list-maker

**Written:** 2026-09-01 (Fable session, from a 13-agent pass: six Opus refuters on the root causes, six Sonnet auditors reading the repo against the four `hg-ground-it` foundation docs, one Opus synthesis). **Status:** proposed — waiting on Kevin's decisions below, then execution phase by phase. **Live cursor:** `NOW.md`. **History:** `DEVLOG.md` (2026-09-01 entry).

## What this is for

Kevin's ask: "what's causing all these behinds and failures? what happened with the blogs? … may be time to clean up our approach and/or re-organize files and generally make sure everything is clean and solid." The diagnosis is done and the certain fixes are in three PRs (below). This plan is the rest: the repo becoming a best-in-class exemplar of an agentic project — docs that tell the truth, a dependency gate that is on, a layout with no dead weight, health checks that mean something on a bad Tuesday, and tests on the seams that carry money and music.

**Goal state (the acceptance for the whole pass):** an agent that opens this repo cold grounds on current reality in one read; every Slack alert means something and every silence means "checked and fine"; no user-facing entity is an ad; a known CVE produces a PR or a red build, never nothing; and `pytest` covers the Spotify write path.

## What shipped today (settled — don't redo)

| PR | What | State |
|---|---|---|
| **#4** `fix/alert-noise-and-failure-paths` | grace windows (feed, transcript, integrity), pulse after import in one snapshot, curated sources rendered honestly + Blog Pull Queue count, `issues: write` + per-workflow idempotent issues, shared `get_db_connection` (timeout/keepalives/retry) across every scheduled-path module, DB preflight first in every workflow, `--strict` health check, concurrency groups, dead-letter artifact, Worker day logic tested | open, CI green — **Kevin merges, then deploys the Worker** |
| **#5** `fix/empty-extraction-declared-outcome` (stacked on #4) | an all-filtered extraction is recorded as `completed_empty` with its reasons; one declared answer is final; health check stays honest | open, CI green — retarget to `main` after #4 |
| **#11** `chore/hygiene-and-dependency-gate` (from `main`) | Dependabot alerts + security updates ON (were off), `dependabot.yml`, runtime/dev requirements split, blocking `pip-audit`, action majors current, `claude.yml` 404 fixed, stale `/Users/KevinHG` paths, dead `process_batch.py` deleted, 3.9 import ratchet, schedule pointers | open, CI green |

The full diagnosis (what the August alerts actually were, the 08-31 runner blackhole that was not Neon, the 08-23 filter-to-zero that was not "the model found nothing", the eleven dry blog weeks) is in `DEVLOG.md` 2026-09-01. Two of my six root-cause claims were refuted by the verifiers and the fixes changed shape because of it — that is the point of the adversarial pass, and the corrected story is what the DEVLOG carries.

## Decisions for Kevin (answer by number; a recommendation is given for each)

1. **Blogs — what feeds the Pull Queue.** Discovery only surfaces URLs the podcasts cite; it found zero new candidates in eleven weeks, and the newest ones were cited by the two posts you ingested yourself (circular). 31 candidates have waited since 06-14.
   a. **Auto-poll (recommended):** add a feed/index step to `blogs.yml` before discovery — OpenAI via its official RSS (`openai.com/news/rss.xml`, verified 200), Anthropic via a Firecrawl scrape of `/news` (no official feed exists; a community mirror would be a trust cost). Keeps your checkbox as the pull decision; changes only what is *offered*. Cap stays at 25 new/run.
   b. Checkbox-only: nothing more than the weekly line + pulse count PR #4 adds; triage the 31.
   c. Retire the two blog shows; `save_item.py --url` stays the only door.
2. **Sponsor reads in the Tech DB.** Every one of 15,342 stored mentions is `is_editorial=true`; ~70 read like sponsor reads (Blitzy, HyperAgent, OutSystems, Zen Coder…) and 6 entities exist only as ads. The model's flag flipped between two identical calls.
   a. **Guard + clean (recommended):** a deterministic sponsor-block detector ("brought to you by", "today's sponsors"…) that overrides the model's flag; keep non-editorial mentions with `is_editorial=false` and filter at sync time so what was dropped is auditable; re-tag or delete the existing ad rows (a production write — your per-op OK, done via psycopg2 with a backup first).
   b. Guard only. c. Leave it.
3. **`web/`** — one commit (2026-01-25), untouched since, undeployed, pinned to a Next.js that predates three 2026 security releases (one RCE-class), and its scraper duplicates `pipeline/scrapers/sop/scrape.py`. Within minutes of Dependabot being switched on it opened **five PRs (#6–#10) for `web/` alone** — the landmine, live. **Recommended: delete** (git history keeps it), which closes those five PRs by itself. Alternatives: wire into CI + bump (merge #6–#10), or mark dormant in CLAUDE.md. Don't merge #6–#10 until this is decided.
4. **Dead-man's switch for the Worker cron.** A dark Worker (Cloudflare issue, expired `GH_PAT`, a cancelled run like 2026-08-06) is silent on every channel — the pulse is dispatched by the thing it would be checking. **Recommended: add a check-in** from `scheduled()` to Healthchecks.io / Cronitor / the Sentry Cron Monitor the code already names. New third-party account: your call.
5. **`origin/claude/mental-health-podcasts-2DJbo`** — 97 commits ahead of `main`, last 2026-05-31, "taste targeting / canonical tier" language that matches nothing here. **Recommended: you look before anything** — recover, or delete a stray push.
6. **PCHH + Culture Gabfest full-archive backfill** (~11h, ~$7.50, 357 + 871 episodes) — deferred since 2026-06-07. **Recommended: run it once**, or write "decided against, 2026-09" so it stops being an open item.
7. **Local venv 3.9 vs CI 3.12.** The split hid two crashing scripts. **Recommended: rebuild the local venv on 3.12** (the ratchet test in #11 catches the class either way).
8. **Retire `ROADMAP.md` + `COMPLETED.md`** into `BACKLOG.md` (live items) + one DEVLOG archive entry. Both are ~100% stale and duplicate NOW/DEVLOG. **Recommended: yes.**
9. **Small deletions/archives** (all git-reversible): `saved-transcripts/` (superseded by the Saved Episodes show; archive to `claude-plans/archive/2026/` or Trash), `run_mentions_until_done.py` and the two TAL repair scripts (no callers since January — delete, or keep one as break-glass and say so in the README), untrack 5 of the 6 committed `codex-notes/…/batch-*` dirs (keep `batch-01-focused-mini`, the README's example), delete the three merged branches + turn on "automatically delete head branches". **Recommended: yes to all.**
10. **Schema investments that need your per-op OK** (ALTERs are guard-blocked for a reason): `UNIQUE (episode_id, lower(btrim(title)), lower(btrim(artist)))` on `songs` (zero violations today — free now, expensive later); drop the duplicate unique index on `episodes(url)`; a `source`/`imported_by` provenance column on `episodes` (**recommended: defer** — every check already routes through `show_config.py`; do it when it bites).

## The phases (in order; each is a branch + PR + triple-check; commit before any restructuring so every step is one revert away)

### Phase 1 — Docs tell the truth [M]
*Goal:* a cold agent, or one waking from compaction, grounds on current reality. First because every later phase is executed by an agent reading these files.
- Retire CLAUDE.md's "⚑ Active work" banner (the June rebuild finished 2026-06-07); archive `claude-plans/2026-06-06-durable-pipeline-{resume,rebuild}.md` to `claude-plans/archive/2026/`; repoint both compaction hooks in `.claude/settings.json` at `NOW.md` (today every compacted session is sent to a finished plan).
- Rewrite `NOW.md` to live-state shape (≤60 lines): *Right now* / *Open items needing Kevin* / *Accepted gaps, dated* / *Pointers*. Today's top block stays; the ten stacked banners move to DEVLOG (backfill short entries for 198cbb8, e0d5277, 5cbda3b too).
- Replace the counts tables in `CLAUDE.md` and `README.md` with a pointer to `data_health.py` / the Notion hub (the ETH finding: don't write what an agent can grep); list all ten shows/sources with destinations (routing is a design fact worth keeping). README still says PCHH's pipeline is "not built yet".
- `ARCHITECTURE.md` scheduling section → point at `worker.js`; add one line on `feed_grace_days`. Trim `cloudflare-trigger/README.md` to redeploy-only (bootstrap finished in June). Trim `pipeline/README.md` to what it still gets right (tests, data_health, the transcript-race mechanism, command reference); archive `pipeline/scrapers/ai_daily/README.md` (Feb 2026). One dated line in `docs/curation-runbook.md` reflecting decision 1.
- Fold decisions 8 + 9's doc parts here if answered.
*Acceptance:* no `.md` outside `claude-plans/archive/` asserts a per-show count or a clock time; `NOW.md` < 60 lines with a *Right now* naming HEAD; both hooks name `NOW.md`; `grep -rn "11:00 UTC\|10:00 UTC\|13:00 UTC"` is empty.

### Phase 2 — Dependency gate [S, mostly done in #11]
*Remaining:* decide on a hash-pinned lockfile (`pip-compile`; the auditor found CI and local resolving two different trees from one floors-only file, including an unreviewed `redis` major via `spotipy`). It carries a small recurring habit (re-compile when Dependabot PRs land) — worth it; do it after #11 merges. Add the npm ecosystem to `dependabot.yml` only if decision 3 keeps `web/`.
*Acceptance:* `pip install --require-hashes -r pipeline/requirements.lock` in every workflow; Dependabot PRs arrive weekly.

### Phase 3 — Layout: retire what is dead, archive what is history [M]
*Goal:* a smaller, unambiguous repo so an agent's file-location guesses are right by default. After Phase 1 so the ROADMAP/COMPLETED retirement lands into a NOW/DEVLOG that is already correct.
- Execute decisions 3, 5, 8, 9. Move `claude-plans/` 2025 files and finished 2026 plans into `claude-plans/archive/YYYY/`; keep only live plans at the top.
- `sync_playlist.py` imports its show config from `show_config.SHOWS` instead of its own dict (the single-source-of-truth docstring is currently false).
- Consider moving the CI extraction output dir out of `codex-notes/` (a runtime artifact dir inside a notes folder) to `pipeline/_runs/` — legibility, low risk, one constant in `run_new_episodes.py`.
*Acceptance:* `git status` clean; nothing at the repo root that isn't named in README/CLAUDE.md (the repo-hygiene hook currently warns at 26 root items); `git branch -a` lists no merged branches; `git ls-files codex-notes/` returns only the one example.

### Phase 4 — Health checks and data you can trust on a bad Tuesday [L]
*Goal:* every alert means something, every silence means checked-and-fine, and no user-facing entity is an ad.
- Decision 2: sponsor-block guard; retain non-editorial rows with `is_editorial=false`; sync filters on it; clean the existing ad rows (backup → psycopg2 → verify → Notion re-sync).
- **Compare episode identities, not dates,** in `check_import_caught_up` (set difference on Taddy uuid between the feed's recent N and `episodes`). Kills the re-dating false positive (a TAL episode re-dated by Taddy inflated a BEHIND count) and, the real prize, surfaces a hole in the *middle* of a series, which `MAX(publish_date)` can never see.
- **Alarm on the check's absence:** the Worker records each dispatched run id and on the next fire polls `GET /actions/runs/<id>`, Slacking anything not `success` (2026-08-06 was a cancelled run nobody saw; 2026-08-16 had no run at all). Decision 4 adds the external ping for a dark Worker.
- Make the batch load transactional (`insert_run` commits `completed` before a single mention lands; a mid-batch crash leaves a permanent undercount nothing can see); add a run-completeness check.
- `zero_mention_runs` gets a show filter and a rolling window; `check_optional_null_map` leaves the alerting list (it can only ever pass).
- Decision 10's schema items; write `NULL` instead of a fabricated `0.5` confidence (`extract_entities.py:547,551`); a per-episode Taddy dedup fallback key instead of the shared `"unknown-episode"` literal.
- `run_script` distinguishes retryable from deterministic failures (exit code 2 = don't retry).
*Acceptance:* a seeded mid-series gap fails the feed check; a day with no entities run produces a Slack line; `SELECT count(*) FROM ai_mentions WHERE is_editorial=false > 0`; no sponsor-only entity in Notion; the health run's every FAIL is actionable.

### Phase 5 — Cover the seams that carry money and music [L]
- Decision 7 (3.12 venv). Tests for `spotify_match.py` (confidence thresholds) and `sync_playlist.py` (playlist diff/dedup) — the live path for SOP's 4,864 and TAL's 1,087 songs, currently zero coverage; mock the API/DB boundary, test the pure decision functions, the pattern the suite already uses.
- Tests for `save_item.py` / `build_pull_queue.run_build` / `feed_check` HTTP paths where cheap.
*Acceptance:* `pytest` green on 3.12 locally and in CI; `tests/` imports `spotify_match` and `sync_playlist` directly.

## How to run it

- One branch + PR per phase; Kevin merges (repo has live deploys). Triple-check + a Codex pass at each phase boundary. Commit before restructuring. Finder Trash, never `rm`, for anything not reproducible. DB `ALTER`/`DELETE` only with Kevin's per-op OK (the Neon-MCP guard is doing its job).
- **Parallelism:** Phases 3 and 5 fan out well as Sonnet/Opus worktree agents (bounded, file-scoped, confirmable) once decisions 3/5/7/8/9 are in. Phases 1 and 4 stay in the main session — coupled doc edits and production data. Never Fable in a workflow without Kevin's say-so in that session.
- **Deploy note:** the Worker only changes on `npx wrangler deploy` from the personal (trimm) profile; `npx wrangler whoami` must say trimm first. Merging changes what the workflows do; deploying changes what the Worker asks for. Both.

## Kickoff paste (for the fresh session that runs Phase 1)

> This is a fresh session with a clear mind stepping into work we spent one long diligent session grounding (2026-09-01: six adversarial refuters on the root causes, a six-lens audit against the four hg-ground-it foundation docs, three PRs shipped). Run the re-entry ritual first — CLAUDE.md, then the top block of NOW.md (the marching orders), then the work's own grounding: `claude-plans/2026-09-01-ground-it-cleanup-plan.md` in full (its "What shipped" and "Decisions" sections are closed — don't redo the diagnosis) and DEVLOG.md's 2026-09-01 entry. Settled law you build on, never relitigate: (1) the August alerts were noise by design and the fixes in PR #4 are the shape (reasoning: DEVLOG 2026-09-01); (2) an all-filtered extraction is a declared outcome, not a failure (PR #5); (3) the Worker is the single trigger and the schedule lives only in `cloudflare-trigger/worker.js` (2026-08-26 + PR #11); (4) Kevin's answers to the plan's numbered decisions, dated where he wrote them. The deliverable is Phase 1 — docs that tell the truth (NOW.md rewritten to live state, the CLAUDE.md rebuild banner retired and both compaction hooks repointed, counts tables and restated schedules replaced by pointers) — same discipline as PR #4: root-cause each stale claim, one concern per commit, tests where a fact can be pinned, a PR for Kevin to merge. Before doing any work: echo back your understanding of where we are and where we're going, clearly and crisply, and ask questions. Big brain, great work, tight communication. Let's go.
>
> launch-pad: (filled in by /save-session)

## Escape hatches

If a step's premise is wrong when you get there (a file already moved, a decision changed, a check already honest), say so in the PR and skip it — the plan is a map from 2026-09-01, not a contract. Sizes are t-shirts, not hours. Anything that touches production data or money stops for Kevin.

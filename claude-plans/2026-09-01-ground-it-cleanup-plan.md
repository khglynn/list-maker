# Ground-it cleanup plan — list-maker

**Written:** 2026-09-01 (Fable session, from a 13-agent pass: six Opus refuters on the root causes, six Sonnet auditors reading the repo against the four `hg-ground-it` foundation docs, one Opus synthesis). **Status:** proposed — waiting on Kevin's decisions below, then execution phase by phase. **Live cursor:** `NOW.md`. **History:** `DEVLOG.md` (2026-09-01 entry).

## What this is for

Kevin's ask: "what's causing all these behinds and failures? what happened with the blogs? … may be time to clean up our approach and/or re-organize files and generally make sure everything is clean and solid." The diagnosis is done and the certain fixes are in three PRs (below). This plan is the rest: the repo becoming a best-in-class exemplar of an agentic project — docs that tell the truth, a dependency gate that is on, a layout with no dead weight, health checks that mean something on a bad Tuesday, and tests on the seams that carry money and music.

**Goal state (the acceptance for the whole pass):** an agent that opens this repo cold grounds on current reality in one read; every Slack alert means something and every silence means "checked and fine"; no user-facing entity is an ad; a known CVE produces a PR or a red build, never nothing; and `pytest` covers the Spotify write path.

## What shipped today (settled — don't redo)

| PR | What | State |
|---|---|---|
| **#4** `fix/alert-noise-and-failure-paths` | grace windows (feed, transcript, integrity), pulse after import in one snapshot, curated sources rendered honestly + Blog Pull Queue count, `issues: write` + per-workflow idempotent issues, shared `get_db_connection` (timeout/keepalives/retry) across every scheduled-path module, DB preflight first in every workflow, `--strict` health check, concurrency groups, dead-letter artifact, Worker day logic tested | merged 2026-09-01; Worker deployed (`92b6a638`) |
| **#5** `fix/empty-extraction-declared-outcome` (stacked on #4) | an all-filtered extraction is recorded as `completed_empty` with its reasons; one declared answer is final; health check stays honest | auto-closed by GitHub when #4's branch was deleted; re-created as **#23**, merged 2026-09-01 |
| **#11** `chore/hygiene-and-dependency-gate` (from `main`) | Dependabot alerts + security updates ON (were off), `dependabot.yml`, runtime/dev requirements split, blocking `pip-audit`, action majors current, `claude.yml` 404 fixed, stale `/Users/KevinHG` paths, dead `process_batch.py` deleted, 3.9 import ratchet, schedule pointers | merged 2026-09-01 |

The full diagnosis (what the August alerts actually were, the 08-31 runner blackhole that was not Neon, the 08-23 filter-to-zero that was not "the model found nothing", the eleven dry blog weeks) is in `DEVLOG.md` 2026-09-01. Two of my six root-cause claims were refuted by the verifiers and the fixes changed shape because of it — that is the point of the adversarial pass, and the corrected story is what the DEVLOG carries.

## Decisions — answered by Kevin, 2026-09-01 (settled; don't relitigate)

1. **Blogs → automated intake with a cheap classifier.** "Any me-in-the-loop go-to-Notion-check-a-box thing is never gonna work." Decide worth-saving with inexpensive checker models tuned on the kinds of posts Kevin saves; the same models serve any LLM loop here, always with a checker. See "Next arc".
2. **Ads → keep, tag, cap.** "Sometimes the ads are helpful… we shouldn't have them overweight by mentions." Store sponsor-read mentions with an ad tag; count them toward weight at most ~5 times; log a product the first time it appears in an ad. Not deleted.
3. **`web/` → deleted** (this PR). The five Dependabot PRs for it close themselves.
4. **Dead-man's switch → reuse the existing pattern**, not a new vendor: `personal/self-hosted-mcps/watchdog` (fleet-watchdog, a Cloudflare Worker on the trimm account that Slacks on transitions) is what already says "this thing is dead" for his MCP fleet. list-maker's Worker exposes its last-fire time; the watchdog polls and Slacks when stale. Note: the watchdog runs off a GitHub Actions cron today because list-maker once held all five Cloudflare cron slots — the 2026-08-26 consolidation freed four, so it can move back to a Cloudflare cron.
5. **Mystery branch → Kevin's own** (mental-health podcasts, May 2026). Kept. Pen task added 2026-09-01: "Find good mental-health podcasts and books" (`TBD Bot: someday`).
6. **PCHH + Gabfest full backfill → still open.** Kevin read it as a Spotify problem; it isn't (Spotify is current). Plain restatement: only recent episodes of the two *media* shows have been processed into the Media DB; the whole archive costs ~11h and ~$7.50. Run once, or write off.
7. **Python 3.12 → yes**, local venv rebuilt 2026-09-01. Kevin's wider wish: "I don't know how to know when things should get updated." The answer in this repo is Dependabot (now on) for packages, `setup-clis.sh` for CLIs, and a `.python-version` + venv rebuild for the runtime — worth generalizing in `helper`.
8. **ROADMAP + COMPLETED → retired** into `BACKLOG.md` + a DEVLOG archive entry (this PR).
9. **Small deletions → yes to all** (this PR): `saved-transcripts/` archived to `claude-plans/archive/2026/`, three orphan scripts deleted, five accidental batch dirs untracked, 2025 + June-2026 plans archived. Merged branches deleted + auto-delete on merge enabled.
10. **Schema tidy-ups → yes**, run by Kevin (DDL is guard-blocked for agents): `sql/007` (drop the duplicate index), `sql/008` (delete the three duplicate song rows, then the unique index). Provenance columns deferred.
11. **Merge → done** (#4, #11, #23; Worker deployed).

## Next arc — curated intake v2 + ads as data (agreed 2026-09-01) [L]

*Why first:* it is the thing Kevin actually wants from the blogs, and a live use case is waiting — a TLT/board deck needs industry usage numbers that live in posts he only hears about via AI Daily Brief. "Ideally our databases would bring in things like this so that even if I don't know about them, they can be found by agents."

**Shape.**
- **Intake sources, polled weekly by `blogs.yml`:** OpenAI's official RSS (`openai.com/news/rss.xml`, verified), Anthropic's `/news` via Firecrawl (no official feed), plus every `report`/`paper`/`blog_post` mention the podcasts cite that carries a URL (the existing discovery). Candidates get scraped once (Firecrawl, capped per run) and stored as candidates with word count and links-out.
- **The judge:** an inexpensive classifier (start with `google/gemini-3.7-flash` and `gpt-5.6-luna`, per eachie's 2026-08-31 eval; a second model as checker on disagreement) answering "would Kevin save this?" against a rubric distilled from his positives — the 3 posts he saved, the Saved Articles show, the research folder, and the Blog Posts he ingested — and negatives (the 14 rows he marked *skipped* in June). Every verdict stored with model, prompt version, confidence, and one-line reason (provenance travels with the value).
- **Auto-ingest** what passes (extract mentions → Tech DB, full text → Blog Posts DB, exactly as `save_item.py` does today); log what fails with the reason. The weekly Slack line reports both. The Notion checkbox goes away; `save_item.py --url` stays as the manual door.
- **Ads as data:** a deterministic sponsor-read detector ("brought to you by", "today's sponsors"…) overriding the model's `is_editorial`; ad mentions stored with `is_editorial=false`; the Notion rollup counts an entity's ad mentions at most 5 toward weight and shows an "Ad" tag; a first-seen log for products that only ever appear in ads. Re-tag the ~70 existing ad mentions (no deletes).
- **Eval before trust:** a frozen set of ~40 labeled candidates (positives + skips), the judge must clear a floor (recall on positives ≥ 0.9, precision ≥ 0.7) before auto-ingest is turned on; the eval runs in `eval.yml` weekly like the extraction eval.

**Acceptance:** the next OpenAI/Anthropic post Kevin would have saved appears in the Tech DB within a week with no action from him; a report AI Daily Brief highlights is findable by an agent (mention → source URL → full text) the day after the episode; the weekly line names what was judged in and out; ads are visible, tagged, and never top a "most mentioned" view on their own.

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

## Kickoff paste (for the fresh session that runs the next arc)

> This is a fresh session with a clear mind stepping into work we spent one long diligent session grounding (2026-09-01: six adversarial refuters on the root causes, a six-lens audit against the four hg-ground-it foundation docs, three PRs shipped). Run the re-entry ritual first — CLAUDE.md, then the top block of NOW.md (the marching orders), then the work's own grounding: `claude-plans/2026-09-01-ground-it-cleanup-plan.md` in full (its "What shipped" and "Decisions" sections are closed — don't redo the diagnosis) and DEVLOG.md's 2026-09-01 entry. Settled law you build on, never relitigate: (1) the August alerts were noise by design and the fixes in PR #4 are the shape (reasoning: DEVLOG 2026-09-01); (2) an all-filtered extraction is a declared outcome, not a failure (PR #5); (3) the Worker is the single trigger and the schedule lives only in `cloudflare-trigger/worker.js` (2026-08-26 + PR #11); (4) Kevin's answers to the plan's numbered decisions, dated where he wrote them. The deliverable is the "Next arc" — curated intake v2 + ads as data — designed first (the rubric, the eval set, the model choice), then built behind the eval floor, same discipline as PR #4: root-cause each stale claim, one concern per commit, tests where a fact can be pinned, a PR for Kevin to merge. Before doing any work: echo back your understanding of where we are and where we're going, clearly and crisply, and ask questions. Big brain, great work, tight communication. Let's go.
>
> launch-pad: (filled in by /save-session)

## Escape hatches

If a step's premise is wrong when you get there (a file already moved, a decision changed, a check already honest), say so in the PR and skip it — the plan is a map from 2026-09-01, not a contract. Sizes are t-shirts, not hours. Anything that touches production data or money stops for Kevin.

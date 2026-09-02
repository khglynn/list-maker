# Dev Log

Chronological session journal. Most recent at top. Never delete entries.

---

## 2026-09-02 — Curated intake v2 + ads as data: designed, built, reviewed (arc branch; PR to main pending Kevin's two pastes)

**Trigger:** the kickoff paste banked on 09-01. Kevin's four answers the same morning: he corrects a pre-labeled eval set rather than labeling cold; the judges run through OpenRouter (key "ListMaker", $15/week cap); link resolution for podcast-cited reports is in scope; Notion stays the human surface (the Pull Queue DB becomes the intake log, nothing waits on a checkbox).

**What the live data said before designing:** the 14 June "skips" were dead links, not taste; the driving case ("How people are using ChatGPT") had sat unchecked in the queue since June; 101 of 106 recent AI Daily episodes carry a "Brought to you by" block in their show notes — a standing sponsor *slate* of 7–9 companies, stable for weeks, not the four reads in an episode; only 3 of 103 report mentions in 120 days carried a URL, and show notes hold no report links at all (248 links in 30 days, all sponsor or promo), so link resolution is web search on the cited name; OpenAI's RSS is ~4 posts a day, Anthropic has no feed.

**The rubric** (`docs/intake-rubric.md`): three Opus drafts from different angles, an adversary against the real feed, one synthesis — six jobs a saved document serves, thirteen save shapes before nine skip forms, a brand-swap test for the residue, six whole-document flags a script computes, real headlines as traps. v2 the same afternoon after the first live read (a "superseded by its publisher" skip, the mismatch/empty checks for every source, S7-over-K1 when the rollout mechanism is nameable).

**The eval** (`evals/intake/`): 75 real candidates (OpenAI feed, Anthropic news, the old queue, three cited reports), labeled by five Opus reviewers against the rubric and Kevin's grounding, provisional until Kevin's pass on the correction page (artifact 69d95337, db-backed, reads back into the fixture). The two cheap judges (gemini-3.7-flash + gpt-5.6-luna, save on disagreement) against those labels: v1 recall 0.925 / precision 0.86; v2 recall 0.962 / precision 0.911, agreement 0.84, floors ok. A second job in `eval.yml` runs it weekly.

**Shipped on the arc branch (`arc/curated-intake-v2`):** `intake/sources.py` (OpenAI RSS + Anthropic index parsers on frozen fixtures; the /engineering cards wrap a thumbnail in the link text and its hero is undated — 1 of 24 parsed until a prod dry run caught it), `intake/judge.py` (pre-checks → two models → the disputed-save rule; rule + job provenance on every verdict), `intake/links.py` (search the cited name, trust the primary source at rank 1, never guess a generic name; the 14-mention probe is its fixture), `sql/010_intake_candidates.sql`, and **PR #31** (store, the Notion intake log repurposed in place, `run_intake.py` in shadow mode, `build_pull_queue.py` retired) — reviewed by six Sonnet lenses with an Opus refuter per finding: 8 of 13 confirmed and fixed with tests (a failed Slack post now fails the run; a crash mid-judge is retried; a pre-check that overturns a verdict clears it in Neon and in Notion; the weekly line follows `AUTO_INGEST`). A prod dry run: 65 candidates, 60 would be judged, ~$0.12 a week. **PR #32** (ads as data: the roster + phrase detector, extraction keeps ads with `is_editorial=false` and `sponsor_source`, the rollup caps ads at 5, Sponsor + Ad mentions in Notion, a retag script that found 246 ad mentions across 55 entities, a health check) is under the same review — 7 of 14 confirmed (the phrase detector tagging the show's own "subscribe on Apple Podcasts" outro and Gabfest article titles; a retag that never told Notion to resync), fixes in progress.

**Also today:** a hotfix for a regression PR #23 shipped the night before — the episode-summary CSV's column list fell out of step with the row, so every extraction batch failed after the model call; the PCHH + Gabfest backfill Kevin started 09-01 20:32 had failed 64/64 batches before loading anything (PR #30, lockstep test; the backfill relaunched detached and loaded 2,073 PCHH mentions). Kevin's SQL pastes 007/008 had run. An OpenRouter key on the wrong account (balance $0) produced 402s on 73 of 75 calls until the funded key replaced it. A reasoning-model quirk (gemini spending its output budget thinking, returning empty JSON) is now a fall-through, not a crash.

**Parked:** the repo's tests mock the cursor, so nothing catches a statement Postgres rejects (`BACKLOG.md`).

**Waiting on Kevin:** the labels page; PR #30; then `sql/009` + `sql/010` and the retag `--apply` before the arc PR to main.

## 2026-09-01 (evening) — merged, deployed, decided

**Merged (Kevin's "merge" in chat; the main-branch rule wants a review, so `--admin`):** #4 alert noise + failure paths, #11 hygiene + dependency gate, #23 declared-empty extraction (a re-creation of #5, which GitHub auto-closed when #4's branch was deleted), and Dependabot floor bumps #12, #13, #15–#18. **Deployed:** the Worker, version `92b6a638` — the pulse now runs after the import; the daily 20:30 UTC cron is unchanged. **Dependabot** was off on this public repo; switching it on opened 16 PRs within the hour, five of them for `web/` alone — the auditor's "landmine" claim, live.

**Kevin's decisions (his words, paraphrased):** (1) blogs — no human-checkbox loop will ever work; automate intake and let an inexpensive classifier decide what is worth saving, trained on what he actually saves (eachie's 08-31 eval found `google/gemini-3.7-flash` and `gpt-5.6-luna` at ~$0.003/row matching Sonnet-5 quality — start there, with a checker). The driving case: a ChatGPT usage post he only knew about because AI Daily Brief highlighted it, needed for a TLT/board deck. (2) ads — keep them, tag them, cap their weight (~5 mentions), log first-seen products; "sometimes the ads are helpful." (3) delete `web/`. (4) dead-man's switch — reuse whatever his other "this thing is dead" Slacks use (that is `personal/self-hosted-mcps/watchdog`, a Cloudflare Worker); no new vendor unless nothing fits. (5) the 97-commit `claude/mental-health-podcasts-2DJbo` branch was his; kept, and a Pen task was added for finding mental-health podcasts and books. (6) confused by the backfill question — it is PCHH/Gabfest history, not Spotify; still open. (7) yes to Python 3.12 everywhere, and a standing wish that things would update themselves. (8)–(10) yes. (11) merge.

**Also:** `gab` (Kevin's read-aloud tool in helper) printed nothing to stdout, so every Claude session read `!gab` as a stray keystroke; it now announces itself. Local venv rebuilt on 3.12 (224 tests). Permissions left as they are — the prompts came from force-pushes and `cd && git` chains, both avoidable.

## 2026-09-01 — Why the channel cried wolf all August, what happened to the blogs, and the fix (PR #4)

**Trigger:** Kevin, from the 09-01 pulse: "what's causing all these behinds and failures? what happened with the blogs?" — plus a call for a ground-it cleanup pass on a repo built months ago.

**Method:** root-cause the four symptoms against the actual runs, logs and live Neon; then six Opus refuters tried to knock each root cause down, and six Sonnet auditors read the repo against the four foundation docs. Two claims were refuted and the fixes changed shape because of it.

**What the alerts actually were (9 of 9 August "behind" alarms were false):**
- *"1 show(s) behind their feed"* — SOP publishes Tuesday, imports Wednesday; TAL publishes Monday, imports Monday at the same minute as the check. The feed check ran daily with no grace window, so it fired most Mon/Tue/Wed. It never once observed a same-day music import (entities' health ran ~20:34–20:44, the music import ~20:45–20:47). The pipeline.yml post-import `--strict` check, the one correctly ordered, has never failed. Fix: `feed_grace_days` per show (SOP 4, TAL 2, daily 2); missing-but-inside-window is *pending*, older is BEHIND and still loud.
- *Pulse "AI Daily BEHIND 1 / PCHH BEHIND 2 / integrity issue"* — the pulse had no ordering dependency on the import (08-01 and 08-15 at 20:16 lied the same way), and read Neon twice on READ COMMITTED so one digest contradicted itself. Fix: the Worker asks entities.yml to run the pulse as a follow-on job after the import; one REPEATABLE READ snapshot; 6h grace on "transcripted without mentions."
- *Five "❓ feed unverified" lines* — curated sources have no feed. Fix: a curated state, plus a Blog Pull Queue line.
- *Two entities.yml failures.* **08-31 was not Neon** (two sibling jobs connected to the same pooler the same minute); one runner VM had a dead path to all three IPv4 addresses and every step paid ~7 minutes rediscovering it — 41 minutes for one fact. Fix: per-address connect timeout + keepalives + bounded retry in one shared function, and a DB preflight as the first step of every workflow (about a minute to a diagnostic Slack line). **08-23 was not "the LLM found nothing"** — token counts prove it emitted a comparable mention list both days; the post-filters removed every candidate on 08-23, and the next day's "recovery" stored two sponsor reads (Blitzy, HyperAgent) as editorial mentions. That is a data-quality design fix, deferred to its own PR (see NOW.md).
- *"Create issue on failure" never worked* — GITHUB_TOKEN is read-only here and no workflow granted `issues: write`; zero `pipeline-failure` issues exist. Fix: permissions blocks + idempotent create (comment on the open issue, not one per day).

**What happened with the blogs:** nothing broke. The design waits on Kevin's checkbox in the Blog Pull Queue; he triaged once on 06-14 (14 skipped, 0 pulled) and 31 candidates have sat since. Discovery produced zero new candidates in eleven weeks because it only surfaces URLs the podcasts cite — and the newest ones were cited by the two blog posts Kevin ingested himself, so the queue can only grow from work it exists to trigger. The weekly job was silent by construction on a dry week. OpenAI has an official RSS feed; Anthropic has none (only community mirrors) — a feed/index poll is the decision in front of Kevin. Fix so far: the weekly line posts every week, and the pulse shows the count.

**Shipped:** PR #4 (`fix/alert-noise-and-failure-paths`, commits 77b7067 + b8e03d3), CI green. Tests 185 → 206 (+6 node tests on the Worker's day logic, now in CI). Worker redeploy after merge.

**Left for the plan:** compare feed episode identities (a re-dated TAL episode inflated a BEHIND count; MAX(publish_date) is blind to holes mid-series); a watchdog for runs that never start (08-06 was a silent day); the sponsor-read / empty-extraction design; ~18 private copies of `get_db_connection`; and the doc-truth / layout / dependency cleanup the auditors are writing up.

## 2026-08-02 — The transcript race, healed: two damaged episodes re-extracted + a recovery loop

**Trigger:** PR #1 prevented the race for future episodes but explicitly left the two already-damaged episodes alone ("the remedy is a delete + re-extract, which is a production write and Kevin's call"). Kevin's call came, with a wider steer: *"if that 'arrived a day late' thing isn't patched/allowed for in the code and'll cause issues again in the future please allow for that in the code… we keep hitting problems because the code isn't permissive enough and the builds aren't self-healing enough."*

**The damage, confirmed against prod before touching anything:** exactly two episodes fleet-wide. Episode 5133 (hard-fork) extracted 06-17 11:09, transcript landed 06-18 11:06. Episode 7261 (ai-daily-brief) extracted 07-30 20:34, transcript landed 07-31 20:31. Both had mentions mined from show-notes boilerplate — 5133's included "Find 'Hard Fork' on YouTube and TikTok" twice; 7261's three mentions were *entirely* newsletter promos.

**The detail that would have caused a second bug:** run 287 covered 7261 AND 7262, and 7262 was fine. `delete_existing_run` keys on `(show_id, batch_name)`, so re-extracting only the damaged episode under that batch name would have deleted 7262's four healthy mentions and never replaced them. The heal therefore re-extracts by **whole original batch**, which is why `find_transcript_race_batches` returns batches rather than episodes.

**Result (runs 291/292):** 5133 now yields Figma-in-context, Claude Design, Sora, Substack, Bitcoin; 7261 now yields GPT-5.6, OpenAI Codex, Claude Code, OpenClaw, Copilot Super app, MAI models, the Zuckerberg WSJ op-ed. 7262 preserved and re-extracted through the same reload. 14 mentions → 21. Zero damaged episodes remain. Notion synced (1 create, 16 updates) so the user-facing surface matches.

**Three holes closed so it cannot recur silently** (see `pipeline/README.md` § the transcript race):
- **Recovery** — every run re-extracts up to 3 episodes whose mentions lack a transcript_id though a transcript now exists, loud in the run summary.
- **Truthful provenance** — `transcript_id` was resolved at LOAD time, so a transcript landing mid-batch (extraction takes minutes) would be stamped onto notes-derived mentions. That fabricated provenance would have made the recovery loop permanently blind to the episode. Provenance is now recorded when the text is read and passed to the loader via `--provenance-json`.
- **A bounded wait** — `require_transcript` blocked forever if Taddy never delivered. After 7 days the notes are extracted anyway, announced. No show is blocked today; nothing stopped it.

**Also fixed:** `prepare_extraction_inputs` skipped writing a cached source file whenever one existed, so a heal on a machine holding the stale blurb would have silently re-mined the same wrong text — the self-heal would have been a no-op that reported success.

**Observability:** `check_transcript_race_selfheal` warns while the queue drains and fails once an episode sits unhealed past 3 days — a count alone can't distinguish "healing in progress" from "healing broken." `check_ai_daily_extraction` narrowed to the orphan case so one problem raises one alert.

**Tests:** 165 → 185.

## 2026-06-10/11 — Beyond podcasts: curated sources + the Notion-staleness fix

**Trigger:** Kevin's ethical-AI-use talk (June 11) — he wanted blog posts, one-off articles, and his research-run citations in the same mentions DB, plus he caught AI Daily's Notion mirror stuck at June 6.

**Root cause of the staleness (the load-bearing find):** `sync_transcripts_notion.py` was a one-time backfill never added to any schedule — the daily pipeline ran green for 4 days while the Transcripts DB silently froze. The failure class: *Notion is a destination; a green run only proves data reached Neon.* Fixed three ways: the sync now runs daily in `entities.yml` (both mirrors); a new `check_notion_sync_freshness` health check fails when any mirror drifts >2 days; a Codex finding made the sync exit non-zero when episodes fail (no more green-step-with-lost-work).

**Shipped (commits `67fedfa`→`77dfdda`+):**
- **Curated sources** (shows 60–63, `medium` + `importer` ShowConfig fields, gabfest slug-hack generalized): openai-blog, anthropic-blog, saved-articles, agentic-research → shared Tech DB. Exempt from staleness/feed checks (no cadence). Drift tests rewritten to assert full DB→show-set group maps.
- **`import_blog.py`** storage primitive (Firecrawl; `canonicalize_url` guards the episodes.url dedup key; thin-scrape guard; metadata→URL-path→fallback date parsing) + **`save_item.py`** ("save this article": domain→show resolution, per-episode extraction, entity + mirror syncs; PDFs → Obsidian research folder; `--podcast` explicitly deferred).
- **Blog Pull Queue** (Notion DB + `build_pull_queue.py` + weekly `blogs.yml` + Worker cron entry): discovery from the mentions DB, Firecrawl enrichment, **Links Out = the pull signal**, Kevin's checkboxes = ground truth for a future auto-pull rule. First build: 30 candidates, 25 queued.
- **Curated Notion qualifier** in `fetch_entity_rollup`: curated mentions qualify at 1, podcasts still need min_mentions — validated live (Fable 5 release post yielded FrontierCode/CursorBench/ViBench benchmarks + the system card, all in Notion at 1 mention).
- **Blog Posts Notion DB** (parametrized `sync_transcripts_notion --target`; URL + Links Out properties).
- **Research importer** (`import_research.py`): obsidian:// URI keys (stable + clickable), infra-file filter (validation caught CLAUDE.md/backlogs polluting the candidate set), local-only.
- **`docs/principles.md`**: the 4 research guides distilled into the repo (legibility, automation planes, provenance, dependency hygiene) + `docs/curation-runbook.md`.

**Validated end-to-end:** MIT TR jobs article + Fable 5 release + OpenAI "Built to benefit everyone" → stored, extracted (22 sane mentions, conf 0.90–0.99), entity pages + full-text mirror pages live; re-save = refresh, no dup.

**Apple Notes mining handed off** to Kevin's preso session (export JSONL of all 371 notes in `pipeline/_cache/apple-notes/`).

**Pending Kevin:** GH_PAT Worker secret (still!); `wrangler deploy` of the new Worker cron (deploy gate); full research-corpus GO (397→~filtered count files, projects 1–3k new Tech-DB entities); Spotify re-auth (music workstream, unchanged).

## 2026-06-06 — Durable pipeline rebuild: Workstream A (hardening) complete

**What happened:** Hardened the pipeline into a durable, self-healing, tested system under a `/loop` autonomous run with a Codex + triple-check gate on every step. Workstream A done except A4 (deferred — needs a schema migration):
- **A1** single-source show registry (importer derives from `show_config.py` + drift test).
- **A2** idempotent batch load (`delete_existing_run` on (show_id, batch_name); re-runs replace, don't duplicate).
- **A3** `RECENT_EPISODE_WINDOW_DAYS` const + `--backfill` full-archive path.
- **A5** `get_logger()` structured-logging foundation + orchestrator per-stage timing.
- **A6** `run_script` bounded retry + backoff, incl. `subprocess.TimeoutExpired`.
- **A7** `check_episode_freshness` staleness check (closes the silent-stale hole that let AI Daily drift weeks unnoticed).
- **A8** extraction + load data-contract tests; DRY'd the duplicated entity_type validation into `normalize_entity_type`.
- **A9** ARCHITECTURE.md + this entry + CLAUDE.md status refresh.
- **Deferred:** A4 (per-entity Notion sync state — ALTER), A5b (print→logging sweep), A6b (aggregated failed-steps summary), A7-Slack (send needs the webhook secret).

**Safety added:** a `PreToolUse` hook blocks destructive SQL (DELETE/DROP/TRUNCATE/ALTER) via the Neon MCP during autonomous runs — motivated by a near-miss where coarse-key grouping nearly deleted 124 legitimately-distinct mentions. Plus `PreCompact` + `SessionStart:compact` compaction-survival hooks.

**Compaction method VALIDATED:** a real auto-compaction fired mid-A7; the `SessionStart:compact` hook re-grounded the new instance (resume doc + NOW.md) with zero lost state — WIP intact, tests green. The survival kit works.

**Tests:** 45 passing (up from ~20). **Next:** Workstream C (Hard Fork onboarding) → B (Cloudflare-Cron trigger + Slack) → D (media: PCHH + Culture Gabfest) → E (verify all). Most of C/B/D need Kevin (GitHub secrets, Notion DB, Cloudflare deploy, the Culture Gabfest transcript-source decision).

---

## 2026-05-28 — Repo renamed back to `list-maker`

**What happened:** Renamed the local folder (`pod-lists` → `list-maker`) and `git remote set-url` to point at `khglynn/list-maker.git`. GitHub canonical name has been `list-maker` since 2025-12-20 (the `pod-lists` URL was just a rename-redirect); other Mac (camillas-MacBook-Pro) already used `list-maker`. This machine was the last holdout.

**Touched in this repo:**
- CLAUDE.md heading + folder tree
- README.md heading
- NOW.md heading
- pipeline/README.md heading
- pipeline/scrapers/{ai_daily,taddy}/README.md `cd` examples
- pipeline/scrapers/tal/repair_metadata.py User-Agent string

**Side effect:** the local `pipeline/venv/` has hardcoded paths to the old `pod-lists` location (shebangs + `VIRTUAL_ENV` in `activate`) and won't work as-is. Delete and recreate with `python -m venv pipeline/venv && pipeline/venv/bin/pip install -r pipeline/requirements.txt` next time you need it.

**Left alone:** historical references in claude-plans/, codex-notes/, COMPLETED.md, and prior DEVLOG entries — those were accurate at their dates.

## 2026-05-16 — Transcript + AI Daily Notion Catch-Up

**What happened:**
- Ran a Taddy dry-run, then imported all missing Taddy-backed transcripts found in the current catalog.
- Imported 150 transcripts total:
  - AI Daily Brief: 60 new transcripts, now 978/978 through 2026-05-15
  - Pop Culture Happy Hour: 58 new transcripts, now 356/356 through 2026-05-15
  - Switched On Pop: 19 new Taddy-catalog transcripts, now 531/531 through 2026-05-15
  - This American Life: added official Taddy source and imported 13 new transcript rows; 15/15 current-feed episodes covered
- Spent 150 Taddy transcript credits; 1,850 remained after import.
- Extracted 60 recent AI Daily episodes in 12 batches and loaded them into Neon.
- Extracted 141 historical AI Daily gap episodes in 29 batches and loaded them into Neon.
- Retried four empty `gpt-4.1-mini` extractions with `gpt-4.1`; retries loaded 20 mentions total.
- Ran alias normalization after each extraction phase.
- Synced AI Daily entities to Notion:
  - Main sync updated 122 pages.
  - Retry sync created 1 page and updated 1 page.
  - Historical sync created 149 pages and updated 135 pages.

**Key numbers after verification:**
- SOP: 698 episodes, 531 transcripts, latest 2026-05-15.
- TAL: 893 episodes, 13 transcripts, latest 2026-05-10; official Taddy source has 15 current-feed episodes and all are covered.
- AI Daily: 978 episodes, 978 transcripts, 978 episodes with mentions, latest 2026-05-15.
- AI Daily mentions: 10,785 mentions across 5,463 entities; 2,723 mentions flagged for review.
- PCHH: 356 episodes, 356 transcripts, latest 2026-05-15.
- AI Daily Notion: 1,067 eligible entities, 1,067 synced.
- AI Daily remaining gap: 0 unextracted transcripted episodes.

**Notes:**
- `import_transcripts.py --max-pages 40` fails against Taddy because Taddy currently accepts pages 1-20 only.
- `import_transcripts.py` now loads repo env files directly when run as documented.
- TAL's official Taddy source only exposes a rolling recent feed; the 883-episode archive source exists in Taddy search but is not transcribing.
- Spotify credentials were not loaded in this repo session, so playlist sync/matching was not run.

**Next:** Restore Spotify credentials for playlist verification, PCHH extraction-to-Notion design, and automation for ongoing catch-up.

---

## 2026-03-13 — Full Catch-Up: Merge, Pipeline Runs, Automation

**What happened:**
- Merged Spotify refactors from this Mac with AI Daily work from other machines
  - `spotify_match.py` and `sync_playlist.py` refactored to be callable from orchestrators
  - New `run_pipeline.py` orchestrator for music shows (scrape→match→sync)
  - New Python scrapers for SOP and TAL (ported from TypeScript / unified wrappers)
  - GitHub Actions workflow for scheduled runs (SOP Wed+Fri, TAL Mon)
- AI Daily catch-up: 3 new episodes (Mar 10-13), 55 mentions extracted, 47 Notion pages updated
- SOP catch-up: 11 episodes scraped, 211 songs found, 199 matched (81% HIGH), 166 tracks added to Spotify playlist
- TAL: already current, no new episodes
- Fixed `fill_songs.py` import (`tal_parse` → `parse` — old rename not updated)
- GitHub Actions: workflow pushed, 6/7 secrets configured (missing SLACK_WEBHOOK_URL)
- Installed missing `openai` package in pipeline venv

**Key numbers:**
- SOP playlist: 3,542 songs across 675 episodes
- TAL playlist: 778 songs across 882 episodes
- AI Daily: 918 episodes imported, 777 extracted, 8,460 mentions, 853+ in Notion

**Next:** Verify GH Actions dry-run, PCHH pipeline, SOP/TAL NOT_FOUND cleanup (369 + 214 songs)

---

## 2026-03-06 — Roadmap Review + Docs Cleanup

**What happened:**
- Full project audit — reviewed all code, plans, and docs for accuracy
- Discovered AI Daily backfill stalled since Feb 11 (quality gate too strict)
- Discovered SOP/TAL matching improvements were planned but never executed
- Updated all project docs: README, pipeline/README, CLAUDE.md, COMPLETED.md, ROADMAP.md
- Created this DEVLOG
- Established 6-phase roadmap (see ROADMAP.md)

**Key findings:**
- Docs were significantly stale — CLAUDE.md didn't mention AI Daily at all
- READMEs still said "list-maker"
- ROADMAP described transcript integration as future work (it's built)
- Neon has way more data than docs reflected: SOP 664 ep (not 462), AI Daily 734 ep extracted (not 230), PCHH 300 ep imported
- SOP matching was partially improved (NOT_FOUND 534 → 357) — earlier claim it was "never executed" was wrong
- Codex branch `codex/ai-daily-brief-kickoff` did the bulk of AI Daily work, was fast-forward merged to main

**Next:** Finish AI Daily backfill (154 episodes remaining), then Notion sync.

---

## 2026-03-01 — Taddy Scraper Added

**What happened:**
- Built Taddy API transcript importer supporting AI Daily, PCHH, SOP
- 888 AI Daily transcripts imported
- Project renamed from list-maker to pod-lists

---

## 2026-02-11 — AI Daily Backfill Stalled

**What happened:**
- Running parallel backfill with `run_mentions_until_done.py`
- Quality gate failures: `mentions_per_episode_too_low` on lighter episodes
- 230 episodes successfully processed, 658 remaining
- Last 7 attempts all failed quality checks

---

## 2026-02-05 — AI Daily Lean Schema + Extraction Pipeline

**What happened:**
- Simplified AI Daily to 3-table Neon schema (ai_runs, ai_entities, ai_mentions)
- Built full extraction pipeline with quality gates
- Validated on 25-episode batch (11.6 mentions/ep average)
- Added guarded backfill runner, alias normalization, link discovery

---

## 2026-01-25 — Folder Reorg + Mosaic Art

**What happened:**
- Restructured project: scripts/ -> pipeline/, scrapers/ subdirectories
- Created mosaic artwork for SOP and TAL playlist covers
- TAL backfill completed: 882 episodes, 1,094 songs, 880 matched

---

## 2025-12-21 — SOP Song Review + Matching Analysis

**What happened:**
- Processed all LOW confidence matches (200 songs)
- Detailed analysis of 534 NOT_FOUND songs by category
- Created improvement plan (feat. format fixes, major artist search)
- Plan documented but not executed — pivoted to AI Daily

---

## 2025-12-12 — Project Kickoff

**What happened:**
- Created Neon database, schema, SOP scraper
- First 3 episodes scraped, 16 songs extracted
- Spotify MCP configured
- Session handoff doc created

---

## Archive — NOW.md banners 2026-06-06 → 2026-08-02 (moved here 2026-09-01)

*NOW.md used to accrete one banner per session. These are the banners as they stood when NOW.md was rewritten to live-state shape; nothing edited. Newest first, as they were.*

> **✅ 2026-08-02 — the transcript race is now self-healing, and the two damaged episodes are repaired.**
> PR #1 prevented the race going forward; this closes it. **Data:** episodes 5133 (hard-fork) and
> 7261 (ai-daily-brief) re-extracted from their real transcripts (runs 291/292) — 5133's mentions went
> from "Find 'Hard Fork' on YouTube and TikTok" to Figma-in-context / Claude Design / Sora / Substack;
> 7261's three newsletter-promo mentions became GPT-5.6 / OpenAI Codex / Claude Code / OpenClaw /
> Copilot Super app / MAI models / the Zuckerberg WSJ op-ed. Episode 7262 shared that batch and was
> healthy — it was preserved through the same reload, which is *why* the heal re-extracts by whole
> original batch (`delete_existing_run` keys on `(show_id, batch_name)`). Notion synced (1 create,
> 16 updates). **0 damaged episodes remain fleet-wide.**
> **Code (PR #2, branch `fix/transcript-race-self-healing`):** a recovery loop re-extracts up to 3
> such episodes per run, loud in the run summary; provenance is recorded when the text is READ rather
> than looked up at load time (a transcript landing mid-batch used to fabricate a transcript_id that
> would have made the recovery permanently blind); `require_transcript` no longer blocks forever —
> after 7 days the notes are extracted anyway, announced; and `prepare_extraction_inputs` now
> overwrites a stale cached source file instead of trusting it (that alone would have made the heal a
> silent no-op). `check_transcript_race_selfheal` warns while the queue drains, fails past 3 days.
> Tests 165 → 185. Full narrative: DEVLOG 2026-08-02; mechanism: `pipeline/README.md`.
> **Pre-existing, not addressed:** `import_caught_up_to_feed` shows ai-daily-brief 1 episode behind
> (feed 08-02, we have 07-31) — the daily import lag, clears on the next scheduled run.

> **⚠️ 2026-07-24 — two silent failures fixed (found by an eachie-side error sweep):**
> (1) **Cloudflare cron day-of-week is 1=Sunday..7=Saturday** — the 06-11 crons assumed
> standard 0=Sunday, so every weekday cron fired ONE DAY EARLY (Sun/Tue/Thu) for six
> weeks and **TAL never auto-synced once** (worker.js's Monday check never matched a
> real fire day). Fixed: Mon=2/Wed=4/Fri=6 in wrangler.toml + worker.js; blogs moved to
> Mon 20:00 UTC and pulse to 20:15 UTC 1st/15th (Kevin's ~3pm-CT report window).
> (2) **Missing `shell: bash` meant no pipefail** — `python run_pipeline.py | tee ...`
> reported tee's exit 0, so every scheduled run since 06-11 read "success" while
> actually dying on `RuntimeError: Spotify token missing/expired` — and the
> failure-Slack step never fired. Fixed: workflow-level `defaults: run: shell: bash`
> across all five workflows; dead `schedule:`-event branches deleted.
> **✅ ALL RESOLVED same day (2026-07-24 late morning):** Worker redeployed with the
> corrected crons (version `3ccd1176`, all 5 schedules verified in deploy output). The
> Spotify token turned out fine LOCALLY (silent refresh) — only the GitHub secret copy
> was stale; refreshed cache pushed to `SPOTIFY_CACHE_JSON` (source of truth:
> `~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache` — shared with the
> spotify-bulk-actions project) and a live dispatched run (30107449205) synced **226
> tracks to SOP — the first real successful sync since 06-11**, six-week backlog
> flushed in one run, pipefail visibly active. Monday brings TAL's first-ever
> auto-sync.

> **▶ LATEST (2026-06-11 late session): clips + one-offs SHIPPED.** `highlight_clips.py` (20 in-DB Castro clips → audio highlights w/ transcript anchor links, 100% anchored) + `save_episode.py` (Saved Episodes show 64: 31 one-off episodes — 19 full Taddy transcripts, 9 clip excerpts, 3 show-notes; Taddy search needs `searchId` + nested `uuid`, terms ≤8 words; Notion selects reject commas). Final rebuild pass was IN FLIGHT at session end (stale adopted pages healed: archived 19, manifest pruned, re-run launched) — **verify: all 31 show-64 episodes paged + highlights anchored, no dupes** (`pipeline/_cache/podcast-clips/manifest.json`). 2 junk show-64 rows (garbage castro titles, pre-fix) need find+delete (Kevin OK). UX renames live: Tech/Media DBs say Sources/Items/URL. Notes spelunk queued 8 articles (incl. free a16z piece). Spotify: union scope shipped; Kevin's one browser consent + secret re-push + backlog flush remain.

> **✅ SHIPPED (2026-06-10/11 eve session): blog sources + curation queue build** — all 5 phases done, 6 commits (`67fedfa`…`99ff453`), pytest 143/143, 2 Codex gates clean (5 findings fixed). Spec archive: `claude-plans/2026-06-10-blog-sources-and-curation.md`; runbook: `docs/curation-runbook.md`; narrative: DEVLOG 2026-06-10/11 entry. Live artifacts: Blog Pull Queue Notion DB (25 candidates queued, ranked by Links Out), Blog Posts DB (3 posts: MIT TR jobs, Fable 5 release, OpenAI benefit-everyone — extracted + synced), curated shows 60–63, weekly blogs.yml, Notion-staleness fix (transcripts sync daily + notion_sync_freshness check; root cause: the sync was a one-time backfill never scheduled). Apple Notes mining handed to the preso session (`pipeline/_cache/apple-notes/notes_export.jsonl`).
> **✅ GH_PAT RESOLVED (2026-06-11, no new PAT):** reused the existing fine-grained `GITHUB_HG_CLAUDE_TOKEN` from `~/.env` (expires **2027-01-20**; a dispatch failure Slacks when it does). Set as the Worker's `GH_PAT` + a fresh `TRIGGER_TOKEN` (saved in `.env.local` as `LIST_MAKER_TRIGGER_TOKEN`). **Verified end-to-end twice:** raw API dispatch (dry-run) + the live Worker's fetch endpoint both landed real runs in Actions. `schedule:` blocks REMOVED from entities.yml + pipeline.yml — the Worker is now their only trigger; pulse.yml + eval.yml now actually fire too.
> **✅ WORKER FULLY DEPLOYED (2026-06-11):** Kevin's first deploy failed on the schedules API — **Workers Free caps a Worker at 5 cron triggers** and the map had 7. Consolidated music to ONE `0 10 * * 1,3,5` cron (worker.js picks Mon→TAL / Wed,Fri→SOP by `event.scheduledTime` UTC day) → exactly 5; redeploy clean (version `60d783bc`), new-version dispatch verified (pipeline.yml run landed). ALL `schedule:` blocks now removed (entities, pipeline, blogs) — the Worker is the single trigger for everything.
> **PENDING KEVIN (1):** **research full-corpus GO** — `import_research.py` + `--shows agentic-research --backfill`: ~440 filtered docs ≈ ~7k mentions / 1–3k new Tech-DB entities, ~$2–4, hours-long background (validation: 5 docs → 83 sane mentions). Note: corpus includes Tecovas work-research docs — entity names are public tools, but consider a folder include/exclude pass first. *(Spotify re-auth for the music workstream still open, unchanged below.)*
> **▶ POST-COMPACTION / FRESH SESSION — read `claude-plans/2026-06-07-resume-music-pipeline-and-observability.md` FIRST** (the current handoff: ways-of-working, the 4 research guides, what shipped, the NEXT workstream = the MUSIC-PIPELINE debug, + open items). The prior resume `2026-06-07-resume-cloudflare-evals-transcripts.md` is ✅ executed. Don't do the minimum — root in the WHYs.
> **⚠️ Live (2026-06-07 eve):** Second source caught the **music pipeline is months-behind** (SOP scrapes 0 songs / 0 added; TAL finds 0 episodes) — pre-existing, surfaced by the new observability. That's the next workstream. **🔑 GH PAT still pending** → pulse.yml + eval.yml (Worker-only) DON'T run until it's set. Sentry `list-maker` project created (org khg-y1); notifications wiring captured in the resume doc for next session. Per-run success Slack pings removed (errors only).
>
> **3-workstream status (2026-06-07 session 2):**
> 1. **Cloudflare control plane — DEPLOYED ✅** `list-maker-cron.kevinhg.workers.dev` (trimm, account_id `759a850a…`, 5 crons). Worker now drives BOTH workflows (entities daily + music Mon/Wed/Fri) AND the weekly eval (Mon 12:00) — single durable control plane, kills the 60-day-disable everywhere. Added trigger-failure Slack alert (optional `SLACK_WEBHOOK_URL` Worker secret) since a silent dead trigger was the new single-point-of-failure. **PENDING KEVIN:** create a fine-grained GH PAT (khglynn/list-maker, Actions: read+write) → `env -u CLOUDFLARE_API_TOKEN wrangler secret put GH_PAT` (+ optionally `SLACK_WEBHOOK_URL`). THEN I: set a TRIGGER_TOKEN, hit the Worker URL to verify a real dispatch lands in Actions, and **remove the `schedule:` blocks from entities.yml + pipeline.yml** (one commit). Until then the GitHub schedules stay = zero gap. Steps: `cloudflare-trigger/README.md`. Commits `5088082`, `cbbc4d0`.
> 2. **Extraction eval harness — DONE ✅ (committed `52e3ffb`)** `evals/extraction/` (metrics.py + 24 tests, build_baseline.py, run_eval.py, fixtures, README) + `.github/workflows/eval.yml` (dispatch-only; Worker drives weekly). **KEY FINDING:** same-model re-extraction at temp 0 reproduces only ~60% of the entity set, and the churn is NOT low-confidence — so per-episode set identity is too noisy to gate. Gate = stable aggregates (yield ratio, type-distribution shift, gold recall, confidence contract); Jaccard/core_recall are diagnostics. Green same-model, catches real regressions. Codex clean 5/5; triple-check caught + fixed a shared-psycopg2-conn-across-threads bug. Same-model refs: yield ~0.92–1.05, type-shift ~0.05, gold recall 0.83/type-acc 1.0.
> 3. **Tech-show transcripts searchable BOTH — DONE ✅ (committed); full backfill running.**
>    - **(a) Neon FTS ✅** `bbba4fa` — generated tsvector + GIN on `episode_transcripts` (sql/005, auto-maintained) + `search_transcripts.py` (websearch_to_tsquery, --show, ts_headline snippets, CTE so headline only touches top results). Verified: "ChatGPT" → 841 eps.
>    - **(b) Notion "Transcripts" DB ✅** `9220b52` — `sync_transcripts_notion.py` (idempotent/resumable, <=100 blocks/req, <=1900 char chunks, rollback-isolated). Created DB `3780501e-f950-81c9-a3e3-eca7f1162c9d` under Pod Lists. Codex Critical (create/commit not atomic → dup-on-resume) FIXED via `fetch_existing_notion_pages` adopt-don't-duplicate — **verified by simulating a crash: 1 page, no dup**. Migration 006 = tracking columns. **Full backfill DONE ✅ — synced 1193, 0 failed; verified AI Daily 997/997 + Hard Fork 199/199 = 1,196/1,196 in Notion.** (TODO: add a Pod-Lists hub pointer to the Transcripts DB + search tool.)
>
> **Observability (2026-06-07 session 2) — error reporting + pulse:** Answered Kevin's "how are we doing error reporting." Push failure-alerts already wired (workflow-fail→Slack+issue, data_health→Slack, eval→Slack, transcript-sync→Slack, Worker trigger-fail→Slack once its secret's set). **Built a biweekly Slack PULSE** (`pulse_report.py` + `pulse.yml` + Worker 1st/15th cron) — positive heartbeat + per-show freshness + counts + actionable failures; absence = trigger down. **Fixed alert-quality false positives** (`data_health.py`): Gabfest (show-notes, no transcripts by design) was failing 2 checks EVERY daily run → added a "none" transcript policy + episode-has-transcript guard on the NULL-link check + music-show transcript lag is now WARN not FAIL. data_health now fails only on actionable issues; pulse reads green when healthy. Committed `301ea0b`. **NEXT for the dead-trigger blind spot: Sentry Cron Monitor** (Kevin already has Sentry) — Worker checks in each run, Sentry alerts on a missed check-in (his call to set up).
>
> Earlier overnight-build context retained below. DONE since overnight: durability hardening (timeouts fail loud, partial failures alert, staleness Slacks, Gabfest daily import), Hard Fork DB archived, hub page = ops manual.
> **Overnight progress (2026-06-07):** Tech-DB re-sync ✅ DONE + VERIFIED — full-reset created **1275/1275, 0 failed**; ChatGPT confirmed in the shared "Tech Tools & Mentions" DB (982dafa0) with Shows=[AI Daily, Hard Fork] → **Option A WORKS**. (1st attempt died on a Notion ReadTimeout → fixed `notion_request` to retry Timeout [`ce2948a`] → re-run `byl8yqfsk` clean.) `clear_notion_ids_for_group` scoped [`81a7b2c`]. **Media build IN PROGRESS:** extract profile [`168a9fd`] + media-capable pipeline [`fb942e4`] committed — orchestrator routing, loader media types, ai_entities+ai_mentions CHECK widened (sql/004), PCHH→media_extraction, and a show-notes COALESCE source path so Gabfest's 871 `description_body` eps extract (no transcripts) and PCHH's 357 transcripts do too. Codex caught **4 real blockers** (ai_mentions.mention_type CHECK would've crashed the first load; PCHH extraction_type=None; Gabfest no-transcript inner-JOIN→0 eps; untracked migration) — all fixed + re-verified PASS. **Media build ✅ COMPLETE + VALIDATED:** validation (6 eps) → 42 sane media entities (movies/books/albums/tv/theater/podcasts, conf 0.70-0.98, segment-aware; Gabfest's from show-notes ✓). Shared **"Media Recommendations" DB** `3780501ef95081a783ebf8a32fa94657` created + wired (pchh+gabfest, +Shows); 42 synced, correct Types, shared entity "Obsession" tagged BOTH shows [`3c462ef`]; media notion_min_mentions=1 [`0ab86be`]. **Catch-up + backfill ✅ DONE + E-VERIFIED — all 6 shows flow end-to-end:** AI Daily caught up (997 eps, latest 2026-06-06); Hard Fork 198 eps/1267 ents; PCHH 52 eps→365 media ents (all synced); Gabfest 17 eps→122 (all synced); SOP 4244/4864 matched; TAL 875/1087 matched. Docs updated to reality (CLAUDE.md + ARCHITECTURE.md). **OVERNIGHT BUILD COMPLETE** — remaining items are all Kevin's call. **Kevin-items:** full media archive (357 PCHH + 871 Gabfest ≈ 11h/$7.50) is a cost fork — scoped-recent running now, surface the full option; Cloudflare Worker deploy; ep-3049 delete; archive the now-orphaned OLD Hard Fork DB `3780501ef9508154998ff4cbe82afedf` (≠ the new Media DB above).

## Active: durable, self-healing rebuild (plan approved 2026-06-06)
Goal — all 6 shows auto-processing on a durable schedule → music (SOP, TAL) to Spotify; tech (AI Daily, Hardfork) + media (PCHH, Culture Gabfest) to Notion; self-healing; Slack-notifying; tested; best-practices.
- Full spec: `claude-plans/2026-06-06-durable-pipeline-rebuild.md`
- Way-of-working + grounding (read after any compaction): `claude-plans/2026-06-06-durable-pipeline-resume.md`
- **Push-hold LIFTED (2026-06-06):** no Vercel link in repo → pushing to `khglynn/list-maker` main is safe. Push each commit going forward (no more local-only).
- **Compaction method VALIDATED (2026-06-06):** a real auto-compaction fired mid-A7; the `SessionStart:compact` hook re-grounded the new instance (resume doc + NOW.md) with **zero lost state** — WIP intact, pytest green. The survival kit works. *(Closeout TODO: document in DEVLOG + save the methodology.)*
- **ENV NOTE:** a "learning" output style is active (it wants Claude to ask Kevin to write code) — conflicts with the autonomous-away loop; continued autonomously per instruction-priority. Kevin: `/output-style default` if unintended.

## Next step (exact)
**Workstream A — hardening** (scheduler-agnostic durability core):
- **A1 ✅ DONE** — single-source show registry: importer derives `SHOWS`/`RAW_CONTENT_SHOW_SLUGS` from `show_config.py` (added `fallback_website_url` + `store_raw_content`); `cfg.series_uuid`→`cfg.taddy_uuid`; drift-guard test tightened. pytest 24/24; Codex SAFE; parity verified.
- **A2 ✅ DONE** — idempotent batch load via `delete_existing_run(show_id, batch_name)` before `insert_run` (re-load replaces; self-heals partials). Scoped psycopg2 DELETE (not the MCP, so unaffected by the destructive-op guard). pytest 25/25; Codex SAFE. *Deferred: full single-transaction atomicity — low value vs blast-radius, and the orchestrator is already episode-idempotent via `find_unextracted_episodes`.*
- **A3 ✅ DONE** — `RECENT_EPISODE_WINDOW_DAYS` const + `make_interval(days => %s)` (no hardcoded literal) + `--backfill` flag threaded through `process_show`/`main` (→ `recent_only=False` + Taddy cap 500). pytest 28/28; Codex SAFE.
- **A4 ✅ APPROVED (2026-06-07) — do it:** per-entity Notion sync state — `ALTER TABLE ai_entities ADD COLUMN IF NOT EXISTS notion_sync_status / notion_sync_error / notion_sync_attempt_at` via a **psycopg2 migration** (NOT the MCP — guard-blocked) + record status in `sync_notion.py` + alert if >10% of a run's entities fail. Kevin OK'd the columns.
- **A5 ✅ DONE** — `get_logger()` foundation in `common.py` (idempotent, `LOG_LEVEL` env, refreshed after env-load) + orchestrator per-show/per-run timing (structured, timestamped). pytest 31/31; Codex SAFE. *A5b (incremental, deferred): convert the per-step `print()`s in import/extract/sync/load to `log` calls — a 5-file sweep, low-risk but churny; do when convenient.*
- **A6 ✅ DONE** — `run_script` retries transient subprocess failures (`MAX_STEP_RETRIES=2`, exponential backoff 5s/10s) — safe because steps are idempotent (A2 + upserts); catches `TimeoutExpired` (retryable); logs retries/recovery/final-failure via `log`. pytest 34/34; Codex SAFE (timeout gap fixed). *A6b (deferred): cross-show aggregated failed-steps summary (per-failure `log.error` already gives greppable observability).*
- **A7 ✅ DONE** — `check_episode_freshness` in `data_health.py`: per-show `STALENESS_MAX_DAYS` (ai-daily 3, pchh 7, hard-fork/gabfest 10, sop 14, tal 21; default 14), flags shows past their freshness threshold; registered in `run_checks`. Closes the silent-staleness hole (AI Daily's 17d drift). pytest 36/36; Codex SAFE. *Slack-send of the report is downstream (workflow + `SLACK_WEBHOOK_URL`) — deferred to B.*
- **A8 ✅ DONE** — extraction-contract tests (`test_extract_entities.py`: `sanitize_mention`/`sanitize_fact`/`parse_json_object` — confidence∈[0,1], required core fields, unknown-type→other+review, low-conf→review) + DRY'd the duplicated entity_type validation into `normalize_entity_type` (used in `insert_mention` + `main`) with its own test. pytest 45/45; Codex SAFE. *(No OpenAI-mock golden-master needed — the post-LLM sanitizers ARE the testable contract seam.)*
- **A9 ✅ DONE** — `ARCHITECTURE.md` (data flow, schema, code map, hardening table, scheduler plan) + DEVLOG entry + CLAUDE.md status pointer. pytest 45/45.

### ✅ WORKSTREAM A COMPLETE (2026-06-06)
A1–A9 done. Pipeline is idempotent, self-healing, structured-logged, staleness-aware, tested (45), single-sourced, documented — and survived a live compaction.

## 🚀 RUN TO COMPLETION (2026-06-07) — loop widened, Kevin away
**Access resolved:** GH secrets set (OPENAI/NOTION/TADDY/SLACK ✅); Slack webhook wired + tested (posts to #list-maker ✅); wrangler logged into trimm ✅; A4 columns approved ✅; Gabfest path solved (Megaphone RSS show-notes carry the endorsements → scrape, NO transcription).

**Sequence (loop drives, gate every step):**
1. **A4 ✅ DONE (2026-06-07)** — migration `003` applied (3 `notion_sync_*` columns) + `sync_notion.py` records synced/failed status (`mark_sync_failed`, best-effort/never-raises) + reusable `post_slack` in `common.py` + >10%-per-phase Slack alert. pytest 51/51; Codex SAFE.
2. **C — Hard Fork — IN PROGRESS (importer bug found 2026-06-07):**
   - ✅ Registered: Neon `shows` row id=48 + `show_config.py` "hard-fork" entry (committed `789619a`).
   - ⚠️ **Taddy import BUG:** only 3/199 episodes landed. ROOT CAUSE — Taddy returns a **generic `websiteUrl`** (`https://www.nytimes.com/column/hard-fork`) for most Hard Fork episodes; `import_transcripts.py` dedups on `url` (`find_existing_episode_id` ~L398-408, **global**, + `episodes.url` UNIQUE / `ON CONFLICT (url)`), so all generic-url episodes collapse onto ONE row → 196 "skipped_existing". The 3 that landed had unique per-episode URLs (2022 eps).
   - **✅ FIX DONE (committed `d18e5d0`):** `episode_url_key` prefers the unique Taddy `uuid` (both upsert + find_existing). Dry-run verified existing shows skip 980/980 (ai-daily) + 532/532 (sop) — **zero re-imports**; only genuinely-new eps import (also surfaced a catch-up backlog: 17 new ai-daily + 3 sop). pytest 54/54; Codex SAFE. Re-imported Hard Fork → **199 episodes + 199 transcripts** (2022→2026-06-05). *(SURFACE this core-importer change in the final summary — minor tradeoff: new eps' `url` is now the synthetic taddy-uuid dedup key, not a human page.)*
   - ⚠️ **CLEANUP for Kevin (destructive, 1 episode):** ep 3049 ("Legs Are Coming") carries episode "Hot I.P.O Summer"'s transcript (mismatch from the ORIGINAL buggy import; it has the generic url). Fix = DELETE ep 3049 + its transcript (psycopg2) + re-import that one. Destructive → **Kevin's per-op OK** (not done unattended). Minor (1/199).
   - **✅ VALIDATION extraction passed (13 recent eps):** 139 mentions / 123 entities, confidence 0.70–0.95 (avg 0.88), 0 null names, 0 out-of-range — real entities (OpenBSD, Claude, ChatGPT, Gemini 3.5 Flash, Polymarket, FFmpeg, Claude Code…). AI-Daily tech profile fits Hard Fork.
   - **⏳ FULL backfill RUNNING (bg, healthy — verified progressing ~48s/ep: 6 runs / 291 mentions as of 09:06 UTC, new run every ~4 min).** On completion → verify count (→ all 199 eps) → sync to Notion.
   - **✅ (a) Hard Fork Notion DB CREATED + wired (committed `e1f03f6`):** cloned the AI Daily 8-prop schema → DB `3780501ef9508154998ff4cbe82afedf` (NO integration-share needed); `notion_database_id` set in show_config + drift test updated.
   - **✅ (b) B trigger CODE DONE (committed `e4bd551`):** `entities.yml` (daily schedule + workflow_dispatch, env-var inputs [injection-hardened], data_health step, Slack + issue-on-failure) + `cloudflare-trigger/` Worker (cron `scheduled()` + token-guarded `fetch()` → GitHub workflow_dispatch) + `run_new_episodes.py` `sys.executable` CI fix. Codex SAFE; pytest 54/54. **DEPLOY = Kevin** (trimm account_id + a fine-grained GH PAT as the `GH_PAT` Worker secret; steps in `cloudflare-trigger/README.md`).
   - **NEXT (non-extraction, while backfill churns): (c) D media PROFILE code** — the media-extraction prompt + media entity types (code only, no extraction run; the media DB migration + backfills wait for the Hard Fork backfill).
   - **DEFER until the Hard Fork backfill finishes (sequential — avoid concurrent OpenAI):** sync Hard Fork → Notion; AI Daily catch-up; PCHH/media backfills.
   - Then: create Hard Fork Notion DB (clone AI Daily 982dafa0... via NOTION_TOKEN integration) + set `notion_database_id` in show_config + update `test_only_configured_notion_shows`. Catch AI Daily up (19d stale).
3. **D — media (investigated 2026-06-07; build in progress):**
   - **⚠️ BACKFILL-BLOCKING CONSTRAINT:** the Hard Fork backfill **subprocess-spawns `extract_entities.py` + `load_entity_batch.py` per batch** — editing those mid-backfill risks the next batch. So the MEDIA-EXTRACTION PROFILE (in extract_entities.py: add `MEDIA_TYPES` + a media system prompt [endorsement/creators[]/platform/caveats/release_year] + media facts, selected by `extraction_type`; existing `LOCKED_TYPES`@42, `CORE_TYPES`@58, prompt@~230-295, mention JSON shape@258-292) + the `load_entity_batch` VALID-types + the entity_type CHECK migration ALL WAIT until the backfill finishes.
   - **SAFE NOW (new files): the Gabfest RSS importer.** Reality-checked: `feeds.megaphone.fm/slatesculturegabfest` = 200 but it's the broad Slate Culture feed (3008 items, MIXED shows incl. "ICYMI") → FILTER to titles starting `Culture Gabfest`. Show-notes = PROSE episode summaries (Backrooms/A24, OnlyFantasy podcast, "Let's Talk About Love" book) — NOT clean "Endorsements:" lists → needs MEDIA LLM extraction (not regex), THINNER than a transcript (~a few cultural items/ep). *(Finding for Kevin: Gabfest-via-RSS-show-notes is a real but LIMITED feed.)* Build = register Gabfest (Neon row + config: `taddy_uuid=None`, `extraction_type='media_extraction'`, `store_raw_content=True`) + a custom importer (filter → upsert episodes + raw_content show-notes) + test.
   - PCHH media profile + media Notion DB + media backfills (PCHH transcripts ready ✅): AFTER the backfill + the media profile. Tiny validation batch + projected-cost note before any big media backfill.
4. **B — trigger:** write `entities.yml` (daily) + the Cloudflare Worker (trimm) code + the `sys.executable` CI fix. **DEPLOY = Kevin** (`wrangler deploy` w/ trimm + a fine-grained GH PAT as a CF secret).
5. **E — verify:** all shows end-to-end; the SOP/TAL `added_to_playlist` anomaly.

**Still needs Kevin (loop stops + PushNotifies):** the Cloudflare worker DEPLOY (trimm account_id + GH PAT); possibly sharing the Notion integration with new DBs. Everything else is solo.

Per-task rhythm: investigate (verify reality) → implement (TDD/DB-test where logic or data changes) → run the net AND read it → doc-sweep → secret-scan the staged diff → commit (scope-prefixed, `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`) → bank NOW/DEVLOG → **Codex + triple-check at the boundary.**

## Just set up (2026-06-06)
- Plan approved + archived; resume/grounding doc written; global CLAUDE.md gained "Default to deep, durable work"; ralph-loop uninstalled (`/loop` = native paradigm); Codex stop-time review gate enabled for this repo.
- Compaction-survival: project CLAUDE.md pointer + PreCompact + SessionStart-compact hooks LIVE in `.claude/settings.json` (committed `10219ff`); destructive-DB-op PreToolUse guard live too. **Validated 2026-06-06** by a real compaction (see Active section).

## Pre-flight Taddy gate findings (2026-06-06) — affects WS-C / WS-D
- **Hardfork = "Hard Fork"** (NYT, two words). Taddy uuid `ff1d51d4-4fc9-4161-b23b-f0079f6dd5a0`, 199 eps, **TRANSCRIBING** → ✅ Taddy method works. Register under the exact name "Hard Fork" + this uuid.
- **Culture Gabfest** (uuid `7732402c-ed24-4b3c-b344-3570eedd8020`) **NOT_TRANSCRIBING** on Taddy (iHeart-distributed; Taddy lacks the rights — not audience size). **SOLVED 2026-06-07:** the endorsements/recs are published in the episode show-notes (Megaphone feed `feeds.megaphone.fm/slatesculturegabfest`) → scrape the RSS descriptions, **no transcription needed**. D-task: build the RSS endorsement scraper; verify main-episode descriptions list the endorsements completely (one bonus ep confirmed; confirm across main eps).
- **Standing practice:** pre-flight verify every new show on Taddy (exists? transcribing?) BEFORE building — catches exactly this. (Save as reusable once proven.)

## Carry-over (pre-rebuild — fold into the workstreams, don't lose)
- AI Daily ~17 days stale (newest ep 2026-05-18; last run 2026-05-20) → fixed by the daily trigger (WS-B) + a catch-up run.
- **SOP/TAL Spotify — ROOT-CAUSED 2026-06-07 (E):** the music pipeline (`run_pipeline.py`) TIMES OUT (30-min cap → GH run "cancelled") on every scheduled run with real work; the quick "successes" (23-27s) are no-new-episode exits. Cause CONFIRMED in code: `get_spotify_client` (`sync_playlist.py:67`) builds `SpotifyOAuth` with NO `open_browser=False`/non-interactive guard → when the `SPOTIFY_CACHE_JSON` secret token is stale, spotipy enters the interactive flow + blocks on `input()` (no stdin in CI) → 30-min hang → cancelled BEFORE the playlist sync. So **SOP/TAL playlists have NOT updated since the token went stale** (cancelled runs visible back to ≥2026-05-20). The `added_to_playlist` (35/0) column is a separate STALE/unmaintained red herring (sync dedups vs the LIVE playlist, not that column). FIX: **(b) ✅ DONE (committed `975df48`):** `common.ensure_spotify_token` validates/refreshes the token upfront + raises a clear error ONLY when headless (`not sys.stdin.isatty()` — so local re-auth in a real terminal still works); `open_browser=False` + the guard wired into ALL 3 `get_spotify_client` (sync_playlist/spotify_match/scoring_match) + `from __future__ import annotations` fixed a pre-existing py3.9 `dict | None` break in scoring_match. 6 tests; Codex SAFE (caught a refresh-returns-None gap, fixed). **(a) ⏳ PENDING KEVIN:** re-auth — creds are NOT in local `.env.local`; he's grabbing CLIENT_ID/SECRET from the Spotify Developer Dashboard (confirm `http://127.0.0.1:8080/callback` is a registered Redirect URI), then runs `spotify_match.py --show-id 1 --limit 1` in his terminal; after that, set the `SPOTIFY_CACHE_JSON` secret from `~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache`. Matched tracks waiting in Neon: SOP 3,542, TAL 778.
- TAL historical transcripts: official Taddy source only exposes the current rolling feed (archive not transcribing).

## ✅ RESOLVED 2026-06-07 → Option A (shared Tech DB)
Kevin chose A. Implemented + committed `7025b62`: sync_notion is group-aware (shows sharing a `notion_database_id` → one DB, one page/entity, global-within-group counts, a "Shows" multi-select tag); hard-fork's `notion_database_id` → AI Daily's DB `982dafa0…` (renamed "Tech Tools & Mentions" + Shows property added); 69 tests; Codex SAFE. **Re-sync RUNNING** (full-reset task `bvg6gw6xr`, ~15min: archive 1072 old pages [recoverable in Notion trash] + create 1275 with global counts + Shows). On completion → verify ChatGPT/OpenAI/Claude appear once, tagged with both shows.
**FOLLOW-UPS:** (1) Kevin archives the now-orphaned separate Hard Fork DB `3780501ef…` (~234 pages). (2) **Before any media DB:** scope `clear_all_notion_ids` to the group's show_ids (a tech full-reset's global clear would otherwise wipe media notion_page_ids). (3) MEDIA = same Option A pattern: ONE shared "Media Recommendations" DB for pchh + culture-gabfest.

### original decision context ⚠️ global entities vs per-show Notion DBs
**Found while verifying the Hard Fork → Notion sync (198/199 eps, 1267 entities, sync said "created 115 + updated 119, 0 failed").** `ai_entities` are GLOBAL (no show_id; deduped by canonical_name across shows; linked to shows only via mentions→runs). ChatGPT/OpenAI/Claude are ONE entity each, mentioned in BOTH AI Daily (show 3) AND Hard Fork (show 48), with a SINGLE `notion_page_id`. So `sync_notion --show hard-fork` saw shared entities already had a page (their AI Daily page) → UPDATED the AI Daily page with Hard Fork data instead of creating a Hard Fork page. **Verified:** ChatGPT + OpenAI `notion_page_id` → parent_db = AI_DAILY (982dafa0…), NOT the Hard Fork DB (3780501ef…).
**Impact:** (1) Hard Fork Notion DB is MISSING the ~119 shared entities (incl. the biggest: ChatGPT/OpenAI/Claude) — it only got the 115 Hard-Fork-only ones. (2) Those ~119 AI Daily pages were overwritten with Hard Fork numbers (the **AI Daily catch-up re-sync self-heals this** — re-syncing AI Daily restores AI-Daily data to its own pages).
**Fork (Kevin's call — it's his Notion workspace; same decision applies to the media DB since PCHH+Gabfest share media entities):**
- **Option A (recommended): ONE shared "Tech Tools & Mentions" DB** for AI Daily + Hard Fork (+ future tech shows) with a "Shows" multi-select property. Matches the global-entity model: one page per entity, shows which shows mention it. Drop the separate Hard Fork DB.
- **Option B: keep separate per-show DBs** + add per-(entity, database) page tracking (schema change: replace the single `notion_page_id` with a per-DB mapping). Keeps separation; duplicates shared entities across DBs with per-show counts.
**HOLD until decided:** Hard Fork DB completion, the media Notion DB creation, any further multi-show Notion sync. SAFE meanwhile: AI Daily catch-up (re-syncs AI Daily correctly), the media EXTRACTION profile code (Notion-independent).

---

## Archive — COMPLETED.md milestones, December 2025 → May 2026 (file retired 2026-09-01)

*COMPLETED.md was a second "what's done" system next to this log. Its content, verbatim; live items from its sibling ROADMAP.md moved to `BACKLOG.md`.*

## May 2026

### AI Daily Full Transcript Entity Backfill + Notion Sync
**Completed:** May 16, 2026

- ✅ 978/978 AI Daily episodes have transcripts
- ✅ 978/978 AI Daily episodes have entity mentions
- ✅ 1,067 Notion-eligible AI Daily entities synced
- ✅ Historical gap of 141 older transcripted episodes extracted
- ✅ Empty mini-model edge cases retried with `gpt-4.1`

### Taddy Transcript Catch-Up Across Active Shows
**Completed:** May 16, 2026

- ✅ AI Daily Brief current through 2026-05-15
- ✅ Pop Culture Happy Hour current through 2026-05-15
- ✅ Switched On Pop Taddy catalog current through 2026-05-15
- ✅ This American Life official rolling Taddy feed added and current
- ⚠️ TAL historical archive feed is not transcribing in Taddy

---

## March 2026

### Taddy Multi-Show Transcript Importer
**Completed:** Mar 1, 2026

Built `pipeline/scrapers/taddy/import_transcripts.py` — imports transcripts from Taddy API for multiple shows (AI Daily, PCHH, SOP). Handles retries, credit management, short transcript rejection.

### Project Rename: list-maker -> pod-lists
**Completed:** Mar 1, 2026

Renamed project and repo. Updated CLAUDE.md (README and pipeline README updated Mar 6).

---

## February 2026

### AI Daily Entity Extraction Pipeline
**Completed:** Feb 5-11, 2026

Full pipeline for extracting app/tool/platform mentions from AI Daily Brief transcripts:
- Lean 3-table Neon schema (`ai_runs`, `ai_entities`, `ai_mentions`)
- LLM extraction via OpenAI gpt-4.1-mini with locked 12-type taxonomy
- Quality-gated backfill runner with configurable thresholds
- Parallel orchestrator (`run_mentions_until_done.py`)
- Alias normalization, link discovery, QA summary scripts
- Validated on 25-episode batch, then scaled to 230 episodes

**Status:** 734/888 episodes extracted (83%), 7,982 mentions across 4,292 entities. 154 episodes remaining — quality gate may need threshold tuning to finish.

**Scripts:** `pipeline/scrapers/ai_daily/`

### AI Daily Transcript Backfill
**Completed:** Feb 2026

- AI Daily Brief episodes imported to Neon via RSS + Firecrawl + OpenAI STT
- Transcripts stored in `episode_transcripts` table and local cache
- Taddy API added later (Mar 2026) as a cheaper bulk transcript source

---

## January 2026

### Folder Reorganization
**Completed:** Jan 25, 2026

Restructured project folders for clarity:
- `scripts/` → `pipeline/` (describes purpose)
- `scripts/sop/`, `scripts/tal/` → `pipeline/scrapers/sop/`, `pipeline/scrapers/tal/` (grouped as scrapers)
- `scripts/fetched/` → `pipeline/_cache/` (underscore = internal)
- `scripts/album-cover-mosaic/` → `marketing/` (separate from pipeline)

### Mosaic Artwork Complete
**Completed:** Jan 25, 2026

Created mosaic artwork for both SOP and TAL Spotify playlists:
- **SOP:** Album art mosaic + episode art mosaic
- **TAL:** Episode art mosaic with tinted variants
- See `marketing/CLAUDE.md` for settings and outputs

### TAL Backfill Complete
**Completed:** Jan 2026

- ✅ 882 episodes scraped from thisamericanlife.org
- ✅ 1,094 songs extracted with episode credits
- ✅ 880 tracks matched to Spotify (80%)
- ✅ Synced to playlist: [TAL Songs](https://open.spotify.com/playlist/3d7fjfrTTKvrl7VHv5JzIz)
- 214 NOT_FOUND songs remaining (need manual review)

**Scripts:** `pipeline/scrapers/tal/`

---

## December 2025

### SOP Backfill Complete
**Completed:** Dec 21, 2025

- ✅ **462 episodes** initially scraped from switchedonpop.com (now 664 in Neon)
- ✅ **4,544 songs** initially extracted (now 4,417 after dedup, 4,043 matched)
- ✅ Playlist live with matched tracks
- ✅ Neon database with shows, episodes, songs tables
- ✅ Built `pipeline/spotify_match.py` for matching
- ✅ Built `pipeline/sync_playlist.py` for syncing
- ✅ Reviewed all LOW matches (200 songs processed)
- ⚠️ NOT_FOUND analyzed (534 songs) but fixes not executed — see `claude-plans/2025-12-21-song-review-progress.md`
- ✅ Playlist: [Every Song on Switched On Pop](https://open.spotify.com/playlist/0cEVeX4pdHf5RJOiTRzgxX)

**Match results:**
| Confidence | Count | % |
|------------|-------|---|
| HIGH | 3,251 | 71.5% |
| MEDIUM | 566 | 12.5% |
| MANUAL | 333 | 7.3% |
| NOT_FOUND | 376 | 8.3% |
| UNAVAILABLE | 18 | 0.4% |

**Files:**
- `src/lib/db.ts` - Neon client + queries
- `pipeline/spotify_match.py` - Match songs to Spotify
- `pipeline/sync_playlist.py` - Sync to playlist
- `claude-plans/prompts/sop/` - Scraping prompts

---

### Session Handoff Doc Created
**Completed:** Dec 12, 2025

Created `claude-plans/2025-12-12-session-handoff.md` for continuity between sessions.

---

## December 2025

### Spotify Bulk Actions MCP - Published
**Completed:** Dec 12, 2025
**Plan:** `claude-plans/2025-12-12-spotify-mcp-publish.md`

Moved Kevin's existing Spotify MCP to a public repo, updated it, and published to package registries. This tool powers the music → Spotify pipeline.

- ✅ Moved from festival-navigator to standalone repo
- ✅ Batch playlist creation with confidence scoring (HIGH/MEDIUM/LOW)
- ✅ Library exports (tracks, artists, albums)
- ✅ Human-in-the-loop CSV review workflow
- ✅ Published to PyPI: [spotify-bulk-actions-mcp](https://pypi.org/project/spotify-bulk-actions-mcp/)
- ✅ Listed on mcp.so
- ✅ Published to official MCP Registry (`io.github.khglynn/spotify-bulk-actions-mcp`)
- ✅ PR submitted to awesome-mcp-servers

**Repo:** [github.com/khglynn/spotify-bulk-actions-mcp](https://github.com/khglynn/spotify-bulk-actions-mcp)

---

### Project Planning & Setup
**Completed:** Dec 12, 2025
**Plan:** `claude-plans/2025-12-12-initial-plan.md`

- ✅ Created CLAUDE.md for project instructions
- ✅ Created project stack file at `~/DevKev/helper/project-stacks/pod-lists.md`
- ✅ Archived initial plan to `claude-plans/2025-12-12-initial-plan.md`
- ✅ Created context doc summarizing original research chats
- ✅ Decided on Vercel App (Next.js + Neon) over n8n
- ✅ Initialized Next.js project structure

---

### Original Research
**Completed:** Oct-Nov 2025 (before this repo)
**Docs:** `claude-plans/2025-12-12-project-context.md`

Two long chats with ChatGPT exploring:
- ✅ Scraping show notes vs transcription costs
- ✅ Destination platforms (Spotify, Notion, Trakt)
- ✅ Workflow orchestration options (n8n, Vercel, etc.)
- ✅ Show-specific extraction strategies


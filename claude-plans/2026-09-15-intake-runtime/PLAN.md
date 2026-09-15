# Curated-intake runtime — stop the timeout doom-loop

**Written:** 2026-09-15 (Fable session, prompted by a `blogs.yml` run that died at the finish line). **Status:** Tier 1 shipped in the same PR as this doc; Tier 2 is captured here as a considered future change, NOT yet implemented. **Parent arc:** `claude-plans/2026-09-02-curated-intake-v2/PLAN.md` (the intake pipeline this hardens). **Live cursor:** `NOW.md`.

## Problem

The weekly curated-intake run (`blogs.yml` → `run_intake.py`) is a single synchronous GitHub Actions job. Its wall-clock is `sum(external-call latency) × (every candidate)` — every Firecrawl scrape, every OpenRouter judge pair, every Notion write, one after another. On a 53-candidate backlog week it hit the step's 60-minute timeout.

**Timeline of that run (~60 min wall, killed):**
- ~51 min (**84%**) in the per-candidate loop — 53 candidates at **~55 s/candidate**, all sequential. Each candidate pays: one Firecrawl scrape (measurements) + two OpenRouter judge calls + a save-path Firecrawl re-scrape + entity extraction + a Notion mirror write.
- ~7.5 min in the saved-articles Notion sync at the end — one silent span with no log line, during which the single held DB connection sits idle.
- The remainder in discovery, upserts, schema-ensure, and the report block.

Two failures compounded:
1. **The false-alarm crash:** the report block ran its three reads on the ONE connection `main()` opened ~an hour earlier. After the long silent Notion sync, Neon had dropped that SSL socket, so `store.weekly_counts` raised `psycopg2.OperationalError: SSL connection has been closed unexpectedly` and the run failed at the finish line even when the real work had landed.
2. **The doom-loop:** because the job dies before finishing, the backlog never fully drains. Each subsequent run re-pays the catch-up (mirror stragglers, ingest stragglers, re-judge), so a heavy week begets another heavy week.

## What Tier 1 already did (this PR)

Two surgical changes — enough to stop the false alarm and let the backlog drain, but NOT the durable fix:
1. **Fresh connection for the report reads.** `run_intake.py` opens a new `get_db_connection()` immediately before both the main reporting block and the `--overrides-only` branch's report call, runs `weekly_counts` / `titles` on it, and closes it in a `finally`. The stale long-held connection no longer decides whether the run reports success.
2. **Timeout headroom in `blogs.yml`.** The "Discover, judge, log" step went 60 → 90 min and the job 70 → 100 min, so a backlog-catch-up week has room to finish and drain instead of being killed mid-loop.

Tier 1 buys time; it does not change the fact that the run is O(candidates) sequential external I/O on one connection.

## Tier 2 — the durable fix, ranked by leverage

1. **Parallelize the per-candidate loop** (`run_intake.py:670`, `for row in work:`). Candidates are independent and spend almost all their time waiting on Firecrawl / OpenRouter / Notion. A bounded thread pool (~5–10 workers) turns ~51 min into a few. Prerequisite: each worker needs its OWN DB connection — the loop currently threads the single held `conn` through `process_candidate` / `ingest_one` / `_mirror`, which is exactly what (2) has to undo first.
2. **Stop holding one DB connection across all I/O.** `main()` opens one connection (`run_intake.py` `main`) and hands it to every step for the whole ~hour. Move to short-lived connections per unit of work, or a small pool. This is the root cause that Tier 1's reconnect only patches at the report step — every other long-idle span has the same latent staleness, and it's the precondition for (1).
3. **Eliminate the double Firecrawl scrape.** `scrape_measurements` (`run_intake.py:206`) already fetches the post's markdown to measure words/links for the pre-check; the save path then re-fetches the SAME url via `save_for_intake` → `save_url` (`save_item.py:111`) → `ingest_url` (`import_blog.py:170`, which re-scrapes in `scrape_post`, `import_blog.py:77`). Thread the already-fetched markdown into the ingest path so it isn't scraped twice — halves Firecrawl calls and latency on every saved candidate.
4. **Bound the corpus-scaling Notion read.** `existing_page_ids` (`notion_log.py:287`, called at `run_intake.py:650`) scans the entire ever-growing intake log every run to map url → page id. Restrict the adoption scan to rows where Neon's `notion_page_id IS NULL` (the only rows that could need a page id discovered), so this stops growing linearly with the log.
5. **Split discover / judge / ingest into separately-timeboxed phases.** The Neon `intake_candidates` table is already a durable queue (`status`: judged | saved | skipped | failed | held). Make each phase its own step (or its own `blogs.yml` job) with its own timeout, so a partial death in one phase doesn't force the next run to re-pay the whole backlog — it resumes from the queue.

## Risks / considerations for Tier 2

- **Rate limits and cost under parallelism.** Fanning out Firecrawl and OpenRouter calls concurrently multiplies request rate and spend against the $15/week OpenRouter cap. Needs bounded concurrency (the ~5–10 cap) plus backoff/retry on 429s, not unbounded fan-out.
- **Notion must stay serial.** Notion self-throttles at ~3 req/s and returns 429s past it. Keep Notion writes (mirrors, the final sync) on a serial path even while the judge/scrape work runs in parallel — parallelize the waiting-on-Firecrawl/OpenRouter work, not the Notion writes.
- **Connection budget.** Per-worker connections must respect Neon's pooler limits; size the pool with the worker cap in mind.

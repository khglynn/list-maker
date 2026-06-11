# list-maker beyond podcasts: blog sources, curation queue, research extraction — plus close the Notion-staleness gap

**Date:** 2026-06-10 (evening — Kevin's talk is **tomorrow, June 11**)
**Repo:** `~/DevKev/personal/list-maker` · main · pipeline green (45+ tests), Workstream A complete

## Context

Kevin's ethical-AI-use talk surfaced that list-maker should capture more than podcasts: info-dense blog posts, one-off articles/episodes, and citations from his Agentic Research archive. His steers this session: **curate, don't bulk-scrape** (discovery from the mentions DB; pull signal = outbound-link density; PDFs/reports → Obsidian research folder as files, not DB rows); **blog pulls run weekly on a schedule** ("if it's not programmatic it won't be kept up"); **bake the 4 research guides into the repo**; and **fix the AI Daily Notion staleness for good**.

**Staleness root cause (diagnosed tonight, read-only):** entities.yml runs green daily (June 9 episode imported this morning; extraction + entities-DB sync clean, `failed=[]`). But `sync_transcripts_notion.py` was a one-time backfill **never added to any schedule** — the Transcripts DB froze at June 6/7. Two systemic holes behind it: `data_health` checks Neon freshness but is **blind to Notion freshness**, and the GH_PAT→Worker takeover is still pending (dead-trigger blind spot).

Architecture rides existing seams (verified by exploration + adversarial Opus review): blog/publication = `shows` row + ShowConfig; post = `episodes` row; full text = `episode_transcripts` (`source_type='blog_post'`) → Neon FTS + extraction via the existing `COALESCE(transcript_text, description_body)` (run_new_episodes.py:103) for free; `blog_post` is already an entity type (sql/004). Mentions → shared Tech DB `982dafa0…` (group-aware sync, Shows multi-select). Full texts → a **new Notion "Blog Posts" DB** à la Transcripts (its sync already has a show allowlist; no leak risk).

## Phase 0 — TONIGHT first: Apple Notes → talk resources .md

Kevin opted into a Workflow. **Agents: sonnet only (never fable — cost rule).**
1. Export all 371 Apple Notes (Breaded, Notes, Older) via one AppleScript pass → `pipeline/_cache/apple-notes/` (title, folder, date, body HTML→text).
2. Workflow: ~19 sonnet agents × ~20 notes, structured schema `{relevant, talk_sections[ENV|SOCIETY|CREATIVITY|SANITY|SECURITY|GENERAL], excerpt, links_found[], saveables[], why}`; talk-section definitions from the handwritten-outline transcription in every prompt.
3. Fetch the MIT Tech Review "reality check on the AI jobs hysteria" article (Firecrawl; fallback Taddy transcript) — stats feed the outline's SOCIETY "QUANT" gap tonight.
4. Main loop synthesizes → `…/session-6-thoughtful-ai-use/content/apple-notes-talk-resources.md`: by talk section, note excerpts w/ titles+dates, MIT TR stats, closing "saveables" list (seeds the pull queue).

## Phase 1 — TONIGHT second: close the Notion gap + persistence hardening + guides

1. **Wire transcripts sync into the daily run**: add a `sync_transcripts_notion.py` step to entities.yml (idempotent, allowlisted shows) + run it once now to catch up June 7–9. Kills the actual staleness.
2. **Notion-freshness health checks** in `data_health.py`: (a) transcripts — any allowlisted-show transcript with `notion_transcript_page_id IS NULL` older than 2 days → FAIL; (b) entities — `notion_sync_status='failed'` backlog or `updated_at > notion_synced_at` beyond threshold → FAIL. Closes the "green run, stale Notion" class permanently.
3. **Worker takeover (3-min Kevin item, he's here):** mint the fine-grained GH PAT → `wrangler secret put GH_PAT` → verify a real dispatch lands → remove `schedule:` blocks from entities.yml + pipeline.yml (per NOW.md pending item). Optional same pass: Sentry Cron Monitor check-in in the Worker (dead-man alarm).
4. **Bake in the 4 research guides** (codebase-legibility, running-off-laptop, memory-systems, dependency-security-hygiene): distill each guide's repo-relevant principles (~10 lines each) into `docs/principles.md` + a pointer section in project CLAUDE.md, linking the canonical vault copies. **Distill, don't copy — the repo is public and the guides are personal docs.**

## Phase 2 — Blog-source infrastructure

Files: `show_config.py`, `run_new_episodes.py`, new `scrapers/blog/import_blog.py`, `sync_transcripts_notion.py` (parametrize), `data_health.py`, tests.
1. `ShowConfig.importer` field (`"taddy"|"gabfest_rss"|"blog"|None`) — generalizes the `cfg.slug == "culture-gabfest"` hack; + `medium: str = "podcast"` (config-only, no migration).
2. Register static shows: `openai-blog`, `anthropic-blog`, `saved-articles` (catch-all — no dynamic show creation; would break `check_expected_shows`), `agentic-research`. All `extraction_type="entity_extraction"`, Tech DB. **Update the drift test in the same commit** (it asserts the exact notion-show set).
3. `import_blog.py`: Firecrawl scrape per URL (pattern: sop/scrape.py `scrape_url`) + `canonicalize_url()` (https, strip query/utm/fragment/trailing slash — episodes.url is the UNIQUE dedup key) → upsert episode + full markdown into `episode_transcripts`.
4. Notion "Blog Posts" DB à la Transcripts (Name, Show, Date, URL, Characters, Episode ID; chunked full text). Parametrize transcripts sync (target DB + show set per invocation); podcast invocation untouched.
5. Health policies: blogs/research/saved-articles exempt from `check_episode_freshness` + skipped in `feed_check` (else daily alert spam). NOT in entities.yml default show list (no CI volume risk).

## Phase 3 — One-off saves + the weekly curation loop (programmatic)

1. **`save_item.py`** — ingestion primitive: `--url` → resolve show (domain match, else `saved-articles`) → scrape → insert → extract just that episode (not the 5-step orchestrator) → incremental entities sync + Blog-DB transcript sync. `--podcast` → Taddy lookup. PDFs/reports detected → saved to the Obsidian research folder + linked, no DB full text.
2. **Pull queue = a Notion DB** ("Blog Pull Queue": URL, source, date, word count, **outbound-link count**, linked domains, why-flagged, ☑ Pull checkbox, status). Switched from the checkbox-.md idea because the weekly-schedule steer makes git-edit friction wrong — Kevin checks boxes in Notion; the pipeline reads them. *(First run can also emit an .md snapshot if Kevin prefers reading it that way.)*
3. **Weekly cron** (Worker → new `blogs.yml` workflow): discover new candidates (blog_post mentions w/ source_url from the mentions DB + recent OpenAI/Anthropic feed items) → enrich (Firecrawl: length, link-out count) → upsert queue rows → **ingest all checked rows** → Slack summary ("3 new candidates, 2 ingested"). Kevin's marks vs. link-density rank = ground truth to graduate an auto-pull threshold later (his "rules vs agent-in-the-loop" question — doc first, rule once validated).
4. Validate end-to-end with the MIT TR article, then the two posts Kevin named (OpenAI "AI for everyone" doc, Claude Fable release blog).

## Phase 4 — Research-runs ingestion (local one-time + repeatable method)

1. `scrapers/research/import_research.py`: walk Agentic Research (Cowork deep-research files first — consistent `[Source, Date](url)` citations; 237 md total), key = **vault-relative path**, body → `episode_transcripts` (`source_type='research_run'`) under `agentic-research`.
2. Extraction → same Tech DB group (Kevin's explicit ask). Accepted trade-offs documented: group counts gain a research dimension; full-reset blast radius grows. 5-file validation batch + cost note (~$1–3) before the full 237.
3. Local-only: excluded from CI lists/freshness/feed checks. Re-running the importer = the ongoing method (idempotent by path key); command documented.

## Phase 5 — Docs + closeout

ARCHITECTURE.md (source types, curation funnel, blast-radius note), CLAUDE.md status, NOW.md/DEVLOG, runbook for the queue loop, archive this plan to `claude-plans/2026-06-10-blog-sources-and-curation.md`.

## Verification (each phase: pytest + Codex gate + commit/push — owned repo, push proactively)

- Phase 0: Kevin opens the .md; spot-check 3 flagged notes against the source notes.
- Phase 1: catch-up run → Transcripts DB shows June 9 AI Daily; force an unsynced transcript in a test → health FAILs; Worker dispatch verified in Actions before schedule-block removal.
- Phase 2–3: `save_item` MIT TR end-to-end → Neon rows, mentions, Tech-DB entity w/ Shows tag, full text in Blog Posts DB; re-run = no dupes. Queue: candidate rows appear w/ correct stats; a checked row ingests on the weekly run path (manual dispatch test).
- Phase 4: validation batch → eyeball 5 entities against source docs (check the output, not just the code).
- Known-deferred (documented): edited-post re-extraction (content-hash gate); auto-pull rule; a16z/paid substacks.

## Open Kevin-items (needed from him, in order)
1. GH PAT (Phase 1.3, ~3 min, tonight ideally). 2. Glance at the Blog Pull Queue DB once created. 3. Carried from NOW.md, unchanged: Spotify re-auth; music-pipeline debug is the other live workstream.

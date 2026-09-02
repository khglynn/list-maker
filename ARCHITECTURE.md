# Architecture — list-maker

*Last updated: 2026-06-11. The data flow + the durable-pipeline design. Live task state → `NOW.md`; history → `DEVLOG.md`; full rebuild spec → `claude-plans/2026-06-06-durable-pipeline-rebuild.md`; engineering principles → `docs/principles.md`.*

## What this is

A pipeline that extracts recommendations from podcasts — and, since 2026-06-11, from curated blog posts, one-off saved articles, and local research-run docs — and routes them to the right destination. **Neon (Postgres) is the source of truth** — everything else (Notion, Spotify) is a downstream sync.

## Data flow

```
Source                      Extract                 Store (Neon)        Sync to
──────────────────────────────────────────────────────────────────────────────────
Taddy API (transcripts) ┐
podcast websites        ┼─► scrapers/ ─► entity/song ─► Neon ──┬─► Notion (tech + media)
blog posts (curated)    ┼    extraction     rows             ├─► Notion full-text mirrors
research-run docs       ┘                                    └─► Spotify (music)
```

- **Music shows** (SOP, TAL): song data scraped from the show's website → matched to Spotify → `songs` rows → one Spotify playlist per show.
- **Tech/media shows** (AI Daily, Hard Fork; PCHH, Culture Gabfest): Taddy transcripts → LLM entity extraction → `ai_entities` / `ai_mentions` → Notion.
- **Curated sources** (`medium != "podcast"`: openai-blog, anthropic-blog, saved-articles, agentic-research): no feed, no cadence — items are pulled deliberately via `save_item.py` or the **curated intake** (weekly `blogs.yml` → `run_intake.py`: discover from the OpenAI feed, Anthropic's index pages and podcast citations → deterministic pre-checks → two cheap models judge against `docs/intake-rubric.md` → every verdict logged to the **Blog Intake** Notion DB). *Changed 2026-09-02: this used to queue candidates behind a Notion checkbox, which stalled for eleven straight weeks; the checkbox survives only as the "Pull anyway" override, and `AUTO_INGEST` stays False until the intake eval clears its floor.* Their entities qualify for Notion at **1 mention** (`fetch_entity_rollup`'s curated qualifier); podcast chatter still needs `min_mentions`. PDFs/long reports skip the DB and save into the Obsidian research folder.

Cascading source strategy (cheapest first): website show-notes → free transcripts → Taddy transcript API → Whisper (last resort). Don't pay to transcribe what's already public.

## Shows (live, 2026-06-07 — all 6 processing end-to-end)

| Show | slug | Type | Episodes | Latest ep | Destination |
|------|------|------|----------|-----------|-------------|
| AI Daily Brief | `ai-daily-brief` | tech | 997 | 2026-06-06 | shared Tech DB (`982dafa0…`) |
| Hard Fork | `hard-fork` | tech | 198 | 2026-06-05 | shared Tech DB (`982dafa0…`) |
| Pop Culture Happy Hour | `pchh` | media | 357 | 2026-06-05 | shared Media DB (`3780501e…94657`) |
| Culture Gabfest | `culture-gabfest` | media | 871 | 2026-06-03 | shared Media DB (`3780501e…94657`) |
| Switched on Pop | `sop` | music | 699 | 2026-06-02 | Spotify |
| This American Life | `tal` | music | 889 | 2026-05-10 | Spotify |

**Curated sources (2026-06-11):** `openai-blog` (60), `anthropic-blog` (61), `saved-articles` (62, one-off catch-all), `agentic-research` (63, local Obsidian docs keyed by `obsidian://` URI — vault-relative, never in CI). All feed the shared Tech DB; full blog texts also mirror to the Notion **Blog Posts** DB (`37c0501e…93f5`); candidates and their verdicts land in the **Blog Intake** DB (`37c0501e…1f53`). Curated shows are exempt from staleness/feed health checks (no cadence to be late against).

**Shared Notion DBs (Option A):** tech (AI Daily + Hard Fork + the curated sources) and media (PCHH + Gabfest) each share one DB — entities are global, deduped across shows, tagged with a "Shows" multi-select. **Heads-up:** a `--full-reset` of the tech group now also wipes/recreates curated-source pages — the blast radius grew with the group. **Gabfest has no transcripts** (Taddy won't transcribe it — iHeart rights); it extracts from Megaphone RSS show-notes via the orchestrator's `COALESCE(transcript_text, description_body)` source path. PCHH + Gabfest show full-archive episode counts; scoped-recent backfill has extracted/synced 52 + 17 so far — full archive (~11h/$7.50) deferred to Kevin.

## Neon schema (key tables)

- `shows` — registry (slug, name, id). Mirrored in code by `pipeline/show_config.py` (**single source of truth**; a drift test fails the build if they diverge).
- `episodes` — per-show episodes (publish_date, url, title; `raw_content` for Taddy shows).
- `episode_transcripts` — transcript text per episode. Generated `search_vector` (tsvector + GIN) powers FTS; `notion_transcript_page_id` tracks the Notion mirror (idempotent sync).
- `songs` — music-show song rows (+ Spotify match state).
- `ai_runs` / `ai_entities` / `ai_mentions` — entity-extraction store. `ai_mentions.run_id → ai_runs` (ON DELETE CASCADE); `ai_entities` deduped by (entity_type, normalized_name, platform); `ai_mentions.entity_id` ON DELETE SET NULL.

## Code map

- `pipeline/show_config.py` — single source of truth for show config (Taddy uuid, extraction_type, store_raw_content…). The Taddy importer derives its show list from here.
- `pipeline/common.py` — DB connection, env loading, `get_logger()` (structured-logging foundation).
- `pipeline/run_new_episodes.py` — orchestrator for entity shows: Taddy import → extract → normalize aliases → Notion sync → Spotify sync. `--backfill` extracts the full archive; transient step failures retry with backoff.
- `pipeline/run_pipeline.py` — orchestrator for music shows (SOP/TAL): scrape → match → sync.
- `pipeline/scrapers/{sop,tal,ai_daily,taddy}/` — per-show extractors.
- `pipeline/scrapers/ai_daily/extract_entities.py` — LLM extraction (gpt-4.1-mini); pure sanitizers enforce the data contract (confidence ∈ [0,1], required fields, valid entity_type).
- `pipeline/scrapers/ai_daily/load_entity_batch.py` — batch → Neon loader; idempotent on (show_id, batch_name) via `delete_existing_run`.
- `pipeline/sync_notion.py` / `pipeline/sync_playlist.py` — Neon → Notion (entities) / Spotify. Entity qualifier: group `min_mentions` OR any curated-source mention.
- `pipeline/sync_transcripts_notion.py` — Neon → Notion full-text mirrors, parametrized by `--target`: `transcripts` (tech shows) | `blog-posts` (curated; + URL/Links Out properties). Idempotent/resumable (NULL-marker gated, adopt-don't-duplicate on resume), chunked + rate-limited; runs daily in `entities.yml`.
- `pipeline/save_item.py` — "save this article": resolve show by domain → scrape/store → extract that episode → sync entities + blog mirror. Also the ingest primitive the intake's override path calls.
- `pipeline/run_intake.py` — the curated intake: discover → pre-check → scrape → judge → Notion log → one Slack line. Weekly via `blogs.yml`; `--sources podcast-cited` is daily-able. Its parts live in `pipeline/scrapers/intake/` (`sources`, `mentions`, `links`, `judge`, `store`, `notion_log`).
- `pipeline/scrapers/blog/import_blog.py` — blog storage primitive: Firecrawl scrape, `canonicalize_url` (the episodes.url dedup key), thin-scrape guard, metadata/URL-path date parsing.
- `pipeline/scrapers/research/import_research.py` — local-only Agentic Research ingester (obsidian:// keys, infra-file filter).
- `pipeline/search_transcripts.py` — Postgres FTS over transcripts (websearch_to_tsquery + ts_headline snippets).
- `pipeline/data_health.py` — read-only health checks (transcript coverage, episode freshness/staleness, mention integrity…); Slacks on a failed check.
- `evals/extraction/` — extraction eval harness (deterministic scorers, frozen baseline + gold fixtures, gated runner). The honest gradient for the one LLM step; see `evals/README.md`.
- `cloudflare-trigger/` — the durable control plane: a Worker cron → GitHub `workflow_dispatch` for all three workflows.
- `tests/` — pytest (110 tests): config drift, idempotency, retry, logging, staleness, extraction + load contracts, eval scorers, transcript chunking.

## Durability (the 2026-06-06 hardening)

Built to run unattended and self-heal:

| | What | Where |
|---|------|-------|
| A1 | Single source of truth for show config (+ drift test) | `show_config.py` |
| A2 | Idempotent batch load — re-runs replace, don't duplicate | `delete_existing_run` |
| A3 | Backfill path + parameterized recency window | `--backfill`, `RECENT_EPISODE_WINDOW_DAYS` |
| A5 | Structured logging + per-stage timing | `get_logger()` |
| A6 | Self-healing retry + backoff (incl. timeouts) | `run_script` |
| A7 | Staleness alert — a silently-stopped feed becomes loud | `check_episode_freshness` |
| A8 | Data contracts pinned in tests | `tests/` |

Since shipped: A4 per-entity Notion sync state (migration 003 + `mark_sync_failed`); A7's Slack send (`data_health.py` posts on a failed check); **`check_notion_sync_freshness` (2026-06-11)** — Notion is a destination, and a green pipeline run only proves data reached Neon. The Transcripts DB silently froze for 4 days in June 2026 because its sync was a one-time backfill nobody scheduled; now the sync runs daily in `entities.yml` and the health check fails loudly if either Notion mirror (or entity pages) drifts >2 days behind Neon. Still deferred (low-value): A5b print→logging sweep; A6b aggregated failed-steps summary.

## Scheduling — the durable control plane (deployed 2026-06-07)

A **Cloudflare Worker Cron** (`cloudflare-trigger/`, trimm account) calls GitHub `workflow_dispatch` for the workflows — entities daily, music Mon/Wed/Fri, eval Mon, **blogs (pull queue) Mon ~3pm CT**, pulse 1st/15th ~3:15pm CT — so GitHub's own `schedule:` cron (which silently disables after 60 idle days on a public repo) is no longer the trigger. A failed dispatch posts to Slack (the trigger itself is observable). Every run also Slacks on success / failure / staleness. Chosen over Inngest (no rewrite, no new account) per the rebuild plan.

*Live as of 2026-06-11: `GH_PAT` = the reused fine-grained `GITHUB_HG_CLAUDE_TOKEN` (expires 2027-01-20 — dispatch failures alert at the trigger when it does); all `schedule:` blocks removed — the Worker is the only trigger. **Constraint learned the hard way: Workers Free caps a Worker at 5 cron triggers** (a 7-cron deploy failed), so music runs on ONE Mon/Wed/Fri cron and worker.js picks the show by fire day. **Second constraint learned the hard way (2026-07-24): Cloudflare cron day-of-week is 1=Sunday..7=Saturday, NOT standard cron's 0=Sunday — the original 1,3,5 values fired Sun/Tue/Thu for six weeks and TAL never auto-synced. Cloudflare Mon=2/Wed=4/Fri=6.***

## Quality gradient — the eval harness

`evals/extraction/` re-extracts a frozen set of tech episodes and gates on **stable aggregate signals** (entity yield, type distribution, gold recall, the confidence contract) — never per-episode set identity, because measured same-model churn is ~40% at temp 0. Run before/after any model or prompt change; weekly CI via `eval.yml`. This is how we answer "how will you know the output stayed good?" — see `evals/README.md`.

## Operational safety

- A `PreToolUse` hook blocks destructive SQL (`DELETE`/`DROP`/`TRUNCATE`/`ALTER`) via the Neon MCP during autonomous runs (pipeline writes go through psycopg2 and are unaffected).
- Compaction-survival hooks (`PreCompact` + `SessionStart:compact`) re-ground a post-compaction agent to the resume doc + `NOW.md` — validated live on 2026-06-06.

## Pointers

- Live state + next step → `NOW.md`
- History → `DEVLOG.md`
- Rebuild spec + acceptance criteria → `claude-plans/2026-06-06-durable-pipeline-rebuild.md`
- Post-compaction grounding → `claude-plans/2026-06-06-durable-pipeline-resume.md`
- Orchestrator usage → `pipeline/README.md`

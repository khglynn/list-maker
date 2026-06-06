# Architecture — list-maker

*Last updated: 2026-06-06. The data flow + the durable-pipeline design. Live task state → `NOW.md`; history → `DEVLOG.md`; full rebuild spec → `claude-plans/2026-06-06-durable-pipeline-rebuild.md`.*

## What this is

A pipeline that extracts recommendations from podcasts and routes them to the right destination. **Neon (Postgres) is the source of truth** — everything else (Notion, Spotify) is a downstream sync.

## Data flow

```
Source                      Extract                 Store (Neon)        Sync to
──────────────────────────────────────────────────────────────────────────────────
Taddy API (transcripts) ┐
podcast websites        ┼─► scrapers/ ─► entity/song ─► Neon ──┬─► Notion (tech + media)
                        ┘    extraction     rows             └─► Spotify (music)
```

- **Music shows** (SOP, TAL): song data scraped from the show's website → matched to Spotify → `songs` rows → one Spotify playlist per show.
- **Tech/media shows** (AI Daily, Hard Fork; PCHH, Culture Gabfest): Taddy transcripts → LLM entity extraction → `ai_entities` / `ai_mentions` → Notion.

Cascading source strategy (cheapest first): website show-notes → free transcripts → Taddy transcript API → Whisper (last resort). Don't pay to transcribe what's already public.

## Shows (live, 2026-06-06)

| Show | slug | Type | Episodes | Transcripts | Latest ep | Destination |
|------|------|------|----------|-------------|-----------|-------------|
| AI Daily Brief | `ai-daily-brief` | tech | 980 | 980 | 2026-05-18 | Notion |
| Pop Culture Happy Hour | `pchh` | media | 357 | 357 | 2026-05-18 | Notion (pipeline pending) |
| Switched on Pop | `sop` | music | 699 | 532 | 2026-06-02 | Spotify |
| This American Life | `tal` | music | 889 | 14* | 2026-05-17 | Spotify |

*TAL: Taddy only exposes the current rolling feed; the historical archive isn't transcribing. Hard Fork + Culture Gabfest are onboarding (Workstreams C/D). AI Daily's latest ep being weeks old is exactly what A7's staleness check now flags.

## Neon schema (key tables)

- `shows` — registry (slug, name, id). Mirrored in code by `pipeline/show_config.py` (**single source of truth**; a drift test fails the build if they diverge).
- `episodes` — per-show episodes (publish_date, url, title; `raw_content` for Taddy shows).
- `episode_transcripts` — transcript text per episode.
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
- `pipeline/sync_notion.py` / `pipeline/sync_playlist.py` — Neon → Notion / Spotify.
- `pipeline/data_health.py` — read-only health checks (transcript coverage, episode freshness/staleness, mention integrity…).
- `tests/` — pytest (45 tests): config drift, idempotency, retry, logging, staleness, extraction + load contracts.

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

Deferred to Kevin / later: A4 per-entity Notion sync state (needs a schema migration); A5b print→logging sweep; A6b aggregated failed-steps summary; A7's Slack send (needs `SLACK_WEBHOOK_URL`).

## Scheduling (Workstream B — in progress)

Today: GitHub Actions (`pipeline.yml`) on a `schedule:` cron. Durable target: a **Cloudflare Worker Cron** calling GitHub `workflow_dispatch` — removes the 60-day public-repo cron-disable risk — plus Slack notifications on every run / failure / staleness. Rationale (Cloudflare over Inngest: no rewrite, no new account) is in the rebuild plan.

## Operational safety

- A `PreToolUse` hook blocks destructive SQL (`DELETE`/`DROP`/`TRUNCATE`/`ALTER`) via the Neon MCP during autonomous runs (pipeline writes go through psycopg2 and are unaffected).
- Compaction-survival hooks (`PreCompact` + `SessionStart:compact`) re-ground a post-compaction agent to the resume doc + `NOW.md` — validated live on 2026-06-06.

## Pointers

- Live state + next step → `NOW.md`
- History → `DEVLOG.md`
- Rebuild spec + acceptance criteria → `claude-plans/2026-06-06-durable-pipeline-rebuild.md`
- Post-compaction grounding → `claude-plans/2026-06-06-durable-pipeline-resume.md`
- Orchestrator usage → `pipeline/README.md`

# AI Daily Brief Transcript Pipeline

*Created: 2026-02-05*
*Last updated: 2026-02-07*

This script pulls recent episodes, gets full transcripts, and saves them in two places:
- Neon table: `episode_transcripts`
- Local files: `pipeline/_cache/ai_daily/transcripts/`

## Scripts

- `transcripts.py`
- `extract_entities.py`
- `init_entity_schema.py`
- `load_entity_batch.py`
- `normalize_aliases.py`
- `discover_links.py`
- `report_summary.py`
- `dedupe_links.py`

## What it does

1. Reads latest episodes from RSS.
2. Upserts show + episodes in Neon.
3. Uses official transcript URL when available.
4. Otherwise generates transcript from audio with OpenAI STT.
5. Saves transcript text to both database and local cache files.

## Required env vars

- `DATABASE_URL` (or `NEON_DATABASE_URL`)
- `OPENAI_API_KEY`

Optional:
- `FIRECRAWL_API_KEY` (used by `discover_links.py`)

## Run

From repo root:

```bash
cd pipeline/scrapers/ai_daily
python3 transcripts.py --limit 25 --dry-run
```

Then real run:

```bash
cd pipeline/scrapers/ai_daily
python3 transcripts.py --limit 25
```

## Useful options

- `--force` overwrite existing transcripts for the selected episodes
- `--model` choose STT model (default: `whisper-1`)
- `--feed-url` override podcast feed URL

## Entity Extraction Test (5-episode batches)

This is a schema-validation step (does not write schema tables yet).

From repo root:

```bash
cd pipeline/scrapers/ai_daily
python3 extract_entities.py --limit 5 --offset 0
```

Then next batch after prompt/schema tweaks:

```bash
cd pipeline/scrapers/ai_daily
python3 extract_entities.py --limit 5 --offset 5
```

Current defaults in extraction:
- Focuses on core types (software_product/model/report/survey/benchmark/account/etc.)
- Excludes non-editorial sponsor/ad mentions unless `--include-non-editorial` is set

Output artifacts are saved under:

- `codex-notes/ai-daily-entity-extraction/<batch-name>/`
  - `summary.md`
  - `mentions.csv`
  - `review_queue.csv`
  - `episode_summary.csv`
  - `episodes/*.json`

## Neon Draft Tables (for review)

Create the draft AI Daily entity tables (`ai_*`):

```bash
cd pipeline/scrapers/ai_daily
python3 init_entity_schema.py
```

Load one extraction batch into those tables:

```bash
cd pipeline/scrapers/ai_daily
python3 load_entity_batch.py \
  --batch-dir /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/ai-daily-entity-extraction/batch-01-initial
```

This creates reviewable rows in Neon tables like:
- `ai_entity_type_definitions`
- `ai_entities`
- `ai_entity_mentions`
- `ai_entity_facts`
- `ai_mention_review_queue`

Review-first views (recommended in Neon UI):
- `ai_v_run_summary`
- `ai_v_priority_entity_counts`
- `ai_v_priority_mentions`
- `ai_v_link_hunt_queue`
- `ai_v_open_review_items`

Note on empty tables at this stage:
- `ai_entity_aliases` stays empty until we start alias normalization.
- `ai_reference_link_candidates` stays empty until URL discovery is running.
- `ai_episode_reference_links` stays empty until URLs are verified and promoted.

## Alias + Link Enrichment (current quality pass)

Run alias normalization:

```bash
cd pipeline/scrapers/ai_daily
python3 normalize_aliases.py
```

Run link discovery for selected runs:

```bash
cd pipeline/scrapers/ai_daily
python3 discover_links.py --run-ids 4,3 --limit 200
```

Generate an easy quality summary:

```bash
cd pipeline/scrapers/ai_daily
python3 report_summary.py --run-id 3 --top 20
```

Clean duplicate promoted links if needed:

```bash
cd pipeline/scrapers/ai_daily
python3 dedupe_links.py
```

## Output location

- Local text + metadata files:
  - `pipeline/_cache/ai_daily/transcripts/*.txt`
  - `pipeline/_cache/ai_daily/transcripts/*.json`

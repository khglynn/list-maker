# AI Daily Brief Transcript Pipeline

*Created: 2026-02-05*
*Last updated: 2026-02-05*

This script pulls recent episodes, gets full transcripts, and saves them in two places:
- Neon table: `episode_transcripts`
- Local files: `pipeline/_cache/ai_daily/transcripts/`

## Scripts

- `transcripts.py`
- `extract_entities.py`
- `init_entity_schema.py`
- `load_entity_batch.py`

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
- `FIRECRAWL_API_KEY` (not needed by this script)

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

## Output location

- Local text + metadata files:
  - `pipeline/_cache/ai_daily/transcripts/*.txt`
  - `pipeline/_cache/ai_daily/transcripts/*.json`

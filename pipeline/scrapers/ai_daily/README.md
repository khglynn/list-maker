# AI Daily Brief Pipeline (Lean Schema)

*Created: 2026-02-05*  
*Last updated: 2026-02-07*

This version intentionally keeps Neon simple:
- `ai_runs`
- `ai_entities`
- `ai_mentions`

No required custom views and no extra AI review/link tables.

## Scripts

- `transcripts.py` (pulls/saves transcripts)
- `extract_entities.py` (creates batch artifacts from transcripts)
- `init_entity_schema.py` (creates/reset lean schema)
- `load_entity_batch.py` (loads batch into lean schema)
- `normalize_aliases.py` (merges obvious duplicates)
- `discover_links.py` (finds URLs and writes back to mentions)
- `report_summary.py` (prints quality summary)

## Required Env Vars

- `DATABASE_URL` (or `NEON_DATABASE_URL`)
- `OPENAI_API_KEY`

Optional:
- `FIRECRAWL_API_KEY` (used by `discover_links.py`)

## End-to-End Flow

From repo root:

### 1) Build/Reset Lean AI Schema

```bash
cd pipeline/scrapers/ai_daily
python3 init_entity_schema.py --reset
```

### 2) Load a Batch (5-episode sanity pass)

```bash
cd pipeline/scrapers/ai_daily
python3 load_entity_batch.py \
  --batch-dir /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/ai-daily-entity-extraction/batch-01-focused-mini \
  --prompt-version extract_entities_v2_lean
```

### 3) Normalize Aliases

```bash
cd pipeline/scrapers/ai_daily
python3 normalize_aliases.py
```

### 4) Discover Missing Links

```bash
cd pipeline/scrapers/ai_daily
python3 discover_links.py --run-ids 1 --limit 300
```

### 5) Quick Quality Summary

```bash
cd pipeline/scrapers/ai_daily
python3 report_summary.py --run-id 1 --top 25
```

## Transcript Output Location

- `pipeline/_cache/ai_daily/transcripts/*.txt`
- `pipeline/_cache/ai_daily/transcripts/*.json`

## Extraction Batch Output Location

- `codex-notes/ai-daily-entity-extraction/<batch-name>/`
  - `batch_manifest.json`
  - `mentions.csv`
  - `review_queue.csv`
  - `episode_summary.csv`
  - `episodes/*.json`

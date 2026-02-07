# AI Daily Extraction Batch Progress

*Created: 2026-02-06*

## What we ran

1. `batch-01-initial` (episodes 1339, 1342, 1343, 1344, 1349) using `gpt-4.1-mini`
2. `batch-02-after-prompt-tuning` (episodes 1350, 1352, 1351, 1353, 1354) using `gpt-4.1-mini`
3. `spotcheck-1351-gpt41` (episode 1351 only) using `gpt-4.1`
4. `batch-01-gpt41-apples-to-apples` (same first 5 episodes) using `gpt-4.1`
5. `batch-01-focused-mini` (same first 5 episodes, filtered focus mode) using `gpt-4.1-mini`

Outputs:
- `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/ai-daily-entity-extraction/batch-01-initial`
- `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/ai-daily-entity-extraction/batch-02-after-prompt-tuning`
- `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/ai-daily-entity-extraction/spotcheck-1351-gpt41`

## Early quality read

- Batch 1 had lower review-queue volume but too much person/org/sponsor noise.
- Batch 2 reduced sponsor false positives but under-captured some target categories.
- Spot check with `gpt-4.1` showed stronger benchmark/account extraction on the same episode.

## Structural update now in place

To make progress visible in Neon:

- Added draft schema SQL:
  - `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily/sql/001_ai_entity_schema.sql`
- Added schema init script:
  - `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily/init_entity_schema.py`
- Added batch loader script:
  - `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily/load_entity_batch.py`

Applied to Neon (now visible in Database Studio):
- `ai_entity_type_definitions`
- `ai_entities`
- `ai_entity_aliases`
- `ai_extraction_runs`
- `ai_entity_mentions`
- `ai_entity_facts`
- `ai_mention_review_queue`
- `ai_reference_link_candidates`
- `ai_episode_reference_links`

Loaded runs currently in Neon:
- Run `1`: `batch-01-initial` (`gpt-4.1-mini`) → 146 mentions, 93 facts, 5 review rows
- Run `3`: `batch-01-gpt41-apples-to-apples` (`gpt-4.1`) → 292 mentions, 123 facts, 50 review rows
- Run `4`: `batch-01-focused-mini` (`gpt-4.1-mini`) → 75 mentions, 57 facts, 16 review rows

Expected empty tables right now:
- `ai_entity_aliases` (we have not run alias normalization yet)
- `ai_reference_link_candidates` (URL discovery phase not run yet)
- `ai_episode_reference_links` (URL verification/promotion phase not run yet)

Recommended review views in Neon:
- `ai_v_run_summary`
- `ai_v_priority_entity_counts`
- `ai_v_priority_mentions`
- `ai_v_link_hunt_queue`
- `ai_v_open_review_items`

## Next move

1. Initialize draft `ai_*` tables in Neon.
2. Load one extraction batch so tables are visible in Database Studio.
3. Compare run quality in Neon and choose default extraction mode.
4. Tune for stronger recall on survey/report/benchmark categories before batch 3.

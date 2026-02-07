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

## Structural update (lean re-architecture, 2026-02-07)

After reviewing real data in Neon, we simplified the AI layer from 9+ tables to **3 tables**:

- `ai_runs`
- `ai_entities`
- `ai_mentions`

What moved where:

- Review queue is now inline on `ai_mentions` via:
  - `needs_review`
  - `review_reason`
  - `review_status`
- Facts are now inline on `ai_mentions.facts` (`jsonb`).
- Link candidates and final link status are now inline on `ai_mentions` via:
  - `link_candidates`
  - `source_url`
  - `link_status`
  - `link_confidence`
- Aliases are now inline on `ai_entities.aliases` (`jsonb` array).

Why this is better:

- Fewer tables to click through in Neon.
- No required custom views.
- Easier to explain and maintain for weekly ops.
- Still supports rollups by type/date/entity for all target questions.

## Next move

1. Reset and apply lean schema (`init_entity_schema.py --reset`).
2. Reload a 5-episode batch (`load_entity_batch.py`).
3. Run `normalize_aliases.py` and `discover_links.py`.
4. Validate with `report_summary.py`.
5. Load larger batch and confirm quality before production cron.

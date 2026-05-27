# Podcast Data Quality Guardrails

*Created: 2026-05-22*

## Why this exists

Kevin's concern was not mainly "how do we prepare for Notion later?" It was more basic and more important: how do we know the current podcast database is not quietly full of gaps, duplicate episode rows, missing dates, and unexplained nulls?

The answer is two layers:

1. **Tests** catch code behavior drift before we ship changes.
2. **Data health checks** inspect Neon itself and report whether the live database is clean.

Both are necessary. Tests alone do not prove old data is clean. Manual database scanning does not protect future runs from recreating the same mess.

## What changed

- Added `pipeline/data_health.py`, a read-only health report for Neon.
- Added first pytest coverage under `tests/`.
- Added `pipeline/scrapers/tal/repair_metadata.py`, a dry-run-first TAL metadata repair command.
- Added `pipeline/repair_duplicate_episodes.py`, a dry-run-first duplicate episode merge command.
- Fixed `pchh` in `pipeline/show_config.py` to match Neon show ID `11`.
- Added `pytest` to `pipeline/requirements.txt`.

## Data repairs applied

TAL metadata:

- Filled/corrected official TAL title, publish date, and episode number metadata from official This American Life episode pages.
- Repaired the one broken TAL episode 481 URL from `/481/kids-these-days` to `/481/this-week`.

Duplicate episodes:

- Merged 9 duplicate episode groups.
- SOP duplicates were split between Taddy transcript/audio rows and website song/raw-content rows; the merge kept the website row and moved the transcript/audio data onto it.
- TAL duplicates were alternate URL rows for the same episode; the merge kept the richer canonical row and moved/de-duplicated songs before deleting donor rows.

## Current health status after repairs

Command:

```bash
pipeline/venv/bin/python pipeline/data_health.py
```

Result after repairs:

- 0 hard failures.
- 1 warning: possible AI Daily entity alias splits.

Show counts after duplicate cleanup:

| Show | Episodes | Transcripts | Latest transcript |
|---|---:|---:|---|
| AI Daily Brief | 980 | 980 | 2026-05-18 |
| Pop Culture Happy Hour | 357 | 357 | 2026-05-18 |
| Switched on Pop | 696 | 532 | 2026-05-19 |
| This American Life | 889 | 14 | 2026-05-17 |

## How to run the guardrails

Tests:

```bash
pipeline/venv/bin/python -m pytest
```

Live read-only health report:

```bash
pipeline/venv/bin/python pipeline/data_health.py
```

Strict mode for automation:

```bash
pipeline/venv/bin/python pipeline/data_health.py --strict
```

## Null policy

The health check now separates "bad missing data" from "expected blank data."

Hard failures:

- Missing episode show, title, URL, or publish date.
- Duplicate show/title/date episode rows.
- AI Daily transcripts without mentions.
- AI mentions without a valid transcript link.
- Completed AI runs with zero mentions.

Expected or currently allowed blanks:

- `episodes.raw_content`: stored for AI Daily/PCHH Taddy imports and many website scrapes; null for some SOP/TAL rows is expected.
- `episodes.has_songs_discussed`: legacy music triage field; null means not evaluated or not applicable.
- `episodes.episode_number`: provider-specific; AI Daily does not provide it.
- `episodes.audio_url` / `image_url`: expected on recent Taddy rows, optional on older website-scraped rows.

## Remaining cleanup queue

AI Daily entity aliases need a curated merge pass. Examples from the health report:

- `GPT-4.1` vs `GPT-4-1`
- `DeepSeek` vs `Deep Seek`
- `Grok 4` vs `Grok4`
- `TerminalBench` vs `Terminal Bench`

Do not solve this with a blind "remove all spaces" normalization change without a migration plan. The existing `ai_entities.normalized_name` values are already in Neon, and changing the normalizer without merging existing entities would create fresh duplicates.

Recommended next phase:

1. Add a curated compact-alias merge mode.
2. Dry-run and review the merge queue.
3. Merge aliases into canonical names that are actually correct, not merely shortest.
4. Re-run `pipeline/data_health.py` until the warning clears.


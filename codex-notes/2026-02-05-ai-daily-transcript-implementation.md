# AI Daily Transcript Implementation

*Created: 2026-02-05*
*Last updated: 2026-02-05*
*Branch: `codex/ai-daily-brief-kickoff`*

## What Was Implemented

- Added script: `pipeline/scrapers/ai_daily/transcripts.py`
- Added doc: `pipeline/scrapers/ai_daily/README.md`
- Updated: `pipeline/README.md` with run commands
- Updated: `pipeline/requirements.txt` with `requests`

## Key Behavior

- Pulls latest episodes from podcast RSS (`--limit`, default 25)
- Upserts show + episodes in Neon
- Tries official transcript URL first when feed provides it
- Falls back to OpenAI STT from audio URL for full transcript generation
- Stores transcripts in two places:
  - Neon table: `episode_transcripts`
  - Local cache: `pipeline/_cache/ai_daily/transcripts/*.txt` and `*.json`
- Handles large-audio upload limits by chunking oversized files before STT upload.

## Safety/Idempotency

- Creates `episode_transcripts` table automatically if missing
- Skips existing transcript rows unless `--force` is set
- Saves transcript metadata with source type and whether transcript was generated

## Run

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/scrapers/ai_daily
python3 transcripts.py --limit 25 --dry-run
python3 transcripts.py --limit 25
```

## Run Result (2026-02-05)

- AI Daily show created/found in Neon: `slug = ai-daily-brief`, `show_id = 3`
- Episodes in Neon for AI Daily: `25`
- Transcripts in Neon for AI Daily: `25`
- Local cache files created: `50` (25 `.txt` + 25 `.json`)
- Transcript source for this batch: `openai_stt` (`whisper-1`)

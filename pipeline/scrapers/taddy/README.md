# Taddy Transcript Importer

Script:
- `pipeline/scrapers/taddy/import_transcripts.py`

Required env vars:
- `DATABASE_URL` (or `NEON_DATABASE_URL`)
- `TADDY_USER_ID`
- `TADDY_API_KEY`

## Safe Dry Run (read-only, no credits spent)

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python pipeline/scrapers/taddy/import_transcripts.py \
  --shows ai-daily-brief,pchh,sop \
  --per-show-limit 25 \
  --dry-run
```

## Recommended Full Run (credit guard + quality guard)

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python pipeline/scrapers/taddy/import_transcripts.py \
  --shows ai-daily-brief,pchh,sop \
  --per-show-limit 2000 \
  --max-pages 40 \
  --max-credit-spend 1701 \
  --check-credits-every 10 \
  --max-failures-per-show 40 \
  --min-transcript-chars 5000 \
  --reject-short-transcripts
```

Notes:
- `--allow-processing-status` is off by default. This prevents saving transcripts still in `PROCESSING` state.
- `raw_content` is only written for `ai-daily-brief` and `pchh`.
- `raw_content` is never written for `sop`/`tal` to avoid conflicts with existing scrape workflows.

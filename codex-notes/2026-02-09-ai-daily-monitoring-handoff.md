# AI Daily Backfill Monitoring Handoff

Updated: 2026-02-08 (local)

## Current State
- Show: `AI Daily` (`show_id=3`)
- Total episodes with transcripts: `888`
- Episodes with mentions loaded: `230`
- Remaining transcripted episodes without mentions: `658`
- Last loaded runs:
  - `run_id=24` batch `fullrun-20260209-main-par2-resume2-b001`
  - `run_id=25` batch `fullrun-20260209-main-par2-resume2-b002`

## Parallel Strategy
- Parallel extraction workers: `2`
- Episodes per worker batch: `10`
- Episodes per wave: `20`
- Quality gate before load:
  - `review_rate <= 0.40`
  - `5 <= mentions_per_episode <= 30`
  - `core_mentions_per_episode >= 3`
- Load policy: sequential loads after all extraction batches in wave pass quality.

## Reliability Hardening
- Extract-level OpenAI retry/backoff in `extract_entities.py`:
  - retries for transient HTTP/network failures (`429`, `5xx`, timeouts)
  - exponential backoff + jitter
  - `Retry-After` support
- Orchestrator retry/backoff in `run_mentions_until_done.py`:
  - retries extract/load subprocesses on transient failures
  - hard subprocess timeouts
  - per-batch extraction logs
- Runner lock file to prevent overlapping orchestrators:
  - `codex-notes/ai-daily-entity-extraction/_full_runs/.runner.lock`

## Resume Command
```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python pipeline/scrapers/ai_daily/run_mentions_until_done.py \
  --chunk-size 10 \
  --parallel-workers 2 \
  --run-label fullrun-20260209-main-par2-continue
```

## Monitoring Commands
```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
ps aux | rg "run_mentions_until_done.py|extract_entities.py --episodes"
```

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
tail -n 120 codex-notes/ai-daily-entity-extraction/_full_runs/<run-label>/progress.json
```

```bash
cd /Users/kevinhalladay-glynn/DevKev/personal/pod-lists
pipeline/venv/bin/python - <<'PY'
import os
from dotenv import load_dotenv
import psycopg2
load_dotenv(os.path.expanduser('~/.env')); load_dotenv('.env.local'); load_dotenv('pipeline/.env.local')
conn=psycopg2.connect(os.getenv('DATABASE_URL') or os.getenv('NEON_DATABASE_URL'))
cur=conn.cursor()
cur.execute('''
select
  count(distinct case when m.id is not null then e.id end) as with_mentions,
  count(distinct e.id) as total,
  count(distinct case when et.id is not null and m.id is null then e.id end) as remaining
from episodes e
join shows s on s.id=e.show_id
left join episode_transcripts et on et.episode_id=e.id
left join ai_mentions m on m.episode_id=e.id
where s.id=3
''')
print(cur.fetchone())
cur.close(); conn.close()
PY
```

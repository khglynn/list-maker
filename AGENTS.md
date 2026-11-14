# list-maker - Agent Instructions

*Inherits from ~/DevKev/AGENTS.md*
*Last verified: 2026-11-13*

## ⚑ Resuming (especially after a compaction)

Read `NOW.md` first — its top block is the live state and the exact next step — then whatever plan it points at. The June-2026 "durable rebuild" this section used to route you to finished on 2026-06-07 (its docs live in `claude-plans/archive/2026/`); since then the repo runs on a daily cron with hardening PRs on top. Post-compaction sessions drift toward doing the minimum — still the #1 failure mode here; do the complete version.

## About This Project

Automated pipeline that extracts recommendations from podcasts and routes them to the right platforms.

**Source of truth:** Neon (Postgres) - all data lives here first, then syncs to other platforms.

**Destinations:**
- **Music** → Neon → Spotify playlists (one per show)
- **Movies/TV, books and other media** → Neon → Notion (the Media DB; Trakt is a backlog idea, not built)
- **Apps/Products** → Neon → Notion (the Tech DB)

**Data strategy:** Avoid transcription when possible. Many podcasts list songs/recommendations on their websites (FREE). Use cascading logic: website → free transcripts → transcript API → Whisper (last resort).

## Key Abbreviations

| Abbreviation | Full Name | Data Source |
|--------------|-----------|-------------|
| SOP | Switched On Pop | Website show notes |
| TAL | This American Life | Website song credits |
| AI Daily | AI Daily Brief | Taddy transcripts → LLM extraction |
| PCHH | Pop Culture Happy Hour | Taddy transcripts → media extraction (Media DB) |

## Tech Stack

- **Database:** Neon (Postgres) - source of truth
- **APIs:** Spotify (spotipy, in `pipeline/spotify_match.py` and `sync_playlist.py`), Notion, Firecrawl (web scraping)
- **Transcripts:** Taddy API (multi-show transcript import)
- **Extraction:** OpenAI gpt-4.1-mini (AI Daily entity extraction from transcripts)

## Spotify MCP

We have a custom Spotify MCP built for this exact use case!

**Location:** `~/DevKev/personal/self-hosted-mcps/spotify-bulk-actions-mcp/` (moved there from `~/DevKev/personal/`)
**Repo:** https://github.com/khglynn/spotify-bulk-actions-mcp

**Key tools:**
- `batch_search_tracks` - Search songs with confidence scoring (HIGH/MEDIUM/LOW)
- `import_and_create_playlist` - CSV → playlist workflow
- `create_playlist_from_search_results` - Create from batch search
- `add_reviewed_tracks` - Add human-reviewed uncertain matches

**Where it's registered:** helper's `cross-machine-sync/tool-catalog.json` (the hosted "claude.ai Spotify Bulk" connector is hidden from terminal sessions since 2026-10-28). The pipeline itself doesn't use the MCP; it talks to Spotify through spotipy.

## Folder Structure

```
list-maker/
├── pipeline/                # The pipeline (Python). Run scripts from inside pipeline/ with ./venv/bin/python
│   ├── common.py            # DB connection (the ONE place: timeout + keepalives + retry), env, Slack, logging
│   ├── show_config.py       # Single source of truth for every show/source (grace windows, importers, destinations)
│   ├── run_new_episodes.py  # Orchestrator: import → self-heal → extract → normalize → Notion sync (entity/media shows)
│   ├── run_pipeline.py      # Orchestrator: scrape → match → sync (SOP/TAL → Spotify)
│   ├── db_preflight.py      # First step of every workflow: fail in ~1 min if Neon is unreachable
│   ├── data_health.py       # Health checks (staleness, integrity, feed second-source); --strict in CI
│   ├── announce.py          # Last step of entities/pipeline/blogs: the one Slack voice + self-closing issues (§ Alerts)
│   ├── alert_guides.py      # The plain words for every alert: what failed, usual cause, what to check first
│   ├── pulse_report.py      # Biweekly Slack digest (runs after the import on the 1st/15th)
│   ├── feed_check.py        # The independent second source: what each show's real feed says is latest
│   ├── sync_notion.py / sync_transcripts_notion.py / sync_playlist.py / spotify_match.py
│   ├── run_intake.py         # Weekly curated intake: discover → pre-check → judge → ingest saves → Notion log
│   ├── save_item.py / save_episode.py / highlight_clips.py / search_transcripts.py
│   ├── scrapers/            # sop/, tal/ (music) · taddy/ (transcript import) · ai_daily/ (LLM extraction + sql/ migrations)
│   │                        # blog/, gabfest/, research/ (curated + RSS sources)
│   │                        # intake/ (sources · mentions · links · judge · store · notion_log)
│   └── _cache/              # Cached episode data (gitignored)
├── tests/                   # pytest (hermetic — no DB, no network); CI runs it on every PR and push
├── evals/extraction/        # The extraction eval harness (frozen set + aggregate gates)
├── cloudflare-trigger/      # The Worker cron that starts everything (worker.js + worker.test.js)
├── .github/workflows/       # entities / pipeline / eval / blogs / pulse dispatched by the Worker; test on PRs and main;
│                            # claude (comment-triggered) and dependabot-automerge
├── docs/                    # principles.md (the engineering rules) · curation-runbook.md · intake-rubric.md · alerts.md
├── codex-notes/             # Historical AI Daily design notes + one kept example batch; CI also writes extraction
│                            # output here (codex-notes/ai-daily-entity-extraction/incremental-*, gitignored)
├── marketing/               # Playlist artwork (mosaic generator; its own AGENTS.md)
└── claude-plans/            # Live plans at the top; archive/YYYY/ for finished ones (+ early ideation, guides, prompts)
```

## What's live (no counts here — run `pipeline/data_health.py` or open the Notion hub for numbers)

Eleven sources (one ended), three destinations. **Podcasts** (Taddy transcripts unless noted): AI Daily Brief + Hard Fork → the shared **Tech DB**; Pop Culture Happy Hour + Culture Gabfest (Megaphone show-notes; the show ended 2026-07-01) → the shared **Media DB**; Switched On Pop + This American Life (website song lists) → one **Spotify playlist** each. **Curated sources** (no feed, no cadence): openai-blog, anthropic-blog, saved-articles, agentic-research → Tech DB, full texts mirrored to the Blog Posts DB; saved-episodes → the Transcripts DB. Entities are global across shows, deduped, tagged with a "Shows" multi-select (Option A, 2026-06-07). Design and data flow: `ARCHITECTURE.md`. History: `DEVLOG.md`.

## Automation

The pipeline runs automatically. The **durable trigger** is the Cloudflare Worker (`cloudflare-trigger/`) calling `workflow_dispatch` — NOT GitHub's own `schedule:` (which silently disables after 60 idle days). Workflows: `pipeline.yml` (music → Spotify), `entities.yml` (tech + media → Notion), `blogs.yml` (the weekly curated intake), `eval.yml` (weekly extraction eval), `pulse.yml` (the 1st/15th digest), plus `test.yml` (pytest on every PR and push to main).

**Schedule:** one Cloudflare cron fans out by day — entities daily; SOP Wed+Fri, TAL Mon; eval + blogs Mon; pulse 1st/15th (after the import). The time and the day logic live in `cloudflare-trigger/worker.js` (`dispatchesFor`, tested) — read it there rather than trusting a restated copy.

**Manual trigger:** Actions tab → the workflow → Run workflow (or the Worker's `/?token=…` endpoint).

**Secrets:** `SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI/CACHE_JSON`, `NEON_DATABASE_URL`, `FIRECRAWL_API_KEY`, `SLACK_WEBHOOK_URL`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` (the two intake judge models), `NOTION_TOKEN`, `TADDY_USER_ID/API_KEY`. `claude.yml` has its own (`ANTHROPIC_API_KEY`, `HELPER_REPO_PAT`). Cloudflare Worker secret: `GH_PAT` (+ optional `SLACK_WEBHOOK_URL`, `TRIGGER_TOKEN`).

**If Spotify auth fails:** Re-auth locally from `pipeline/` (`./venv/bin/python spotify_match.py --show-id 1 --limit 1`), then update the `SPOTIFY_CACHE_JSON` secret with the new token cache's contents. Careful: the scripts' default cache path (`DEFAULT_CACHE_PATH` in `spotify_match.py` and `sync_playlist.py`) still names the MCP's old folder, `~/DevKev/personal/spotify-bulk-actions-mcp/.spotify_cache/.cache`, so a local re-auth writes there or fails to save until that is fixed in code. CI is unaffected: `pipeline.yml` passes `--cache-path` to `run_pipeline.py`.

See `pipeline/README.md` for orchestrator docs, `evals/README.md` for the eval harness, `cloudflare-trigger/README.md` for the trigger.

## Alerts in #list-maker

**The rule for failures:** say it when a check starts failing (or the set of failing checks changes), again **every 7 days** while it is still unfixed, and **once on recovery**. Never daily. Kevin's standard: a check that talks every day trains its reader to ignore it; one that talks only once can't tell him a problem was never fixed. `pipeline/announce.py` carries it, and each failing check keeps one GitHub issue (label `pipeline-failure`) that closes itself on recovery.

What each Slack message means, the first move for each, the known trade-offs and where to change a threshold or its words: `docs/alerts.md`. Read it before touching `announce.py`, `data_health.py` or `alert_guides.py`, or when a message needs explaining.

## Gabby watches this (2026-11-06)

Kevin, 2026-11-06, in his Slack DM with Gabby: "That list automation should live here too", and that the always-on Air should "keep an eye on all our automated pipes". The jobs stay where they are (the Cloudflare cron, GitHub Actions, Neon, Notion); Gabby on the Air is the one who knows whether they ran, runs them on his ask and fixes them. Why: from 2026-09-29 the daily entities run was red on one check (issue #68) and its only word went to `#list-maker`, which nobody read.

- **How she hears** (from her next install on the Air). Her 06:30 morning check reads this repo's open `pipeline-failure` issues (one per check that keeps failing, as above), each dispatched workflow's last run against the rhythm the Worker gives it (`cloudflare-trigger/worker.js` is the one source for that), since a cron that stops firing files no issue, and a failed last run, the eval's only word there since it files no issue. A change is a quiet line in her log channel and one line on her own work list, which her morning roundup picks up; Kevin hears only what needs him. `#list-maker` keeps getting the messages in `docs/alerts.md`.
- **Checking by hand.** `gh issue list -R khglynn/list-maker --label pipeline-failure --state open` and `gh run list -R khglynn/list-maker --limit 10`; a failed run's checks: `gh run view <id> -R khglynn/list-maker --log | grep -E '\[(FAIL|WARN)\]' -A3`, or read the "Data health check" step.
- **Running one.** On his ask only: `gh workflow run entities.yml -R khglynn/list-maker` (or `pipeline.yml` with `-f show_id=...`, `blogs.yml`). A real run can spend a little OpenAI, OpenRouter and Firecrawl credit as well as Actions minutes. `pipeline.yml` and `blogs.yml` take `-f dry_run=true`, a preview that writes nothing; `entities.yml`'s `dry_run` previews extraction, but its Notion transcript sync step still runs for real.
- **Fixing.** A worktree and a PR, as any change here; a data fix is a numbered file in `pipeline/scrapers/ai_daily/sql/` that Kevin pastes (or she runs, once Neon is one of her connectors and he says so).
- **His lists.** The Notion side (Pod Lists: Blog Posts, Transcripts, the Tech DB, Blog Intake) through her Notion connector; edits there are hers to make on his ask. The Spotify playlists through his `spotify-bulk-actions-mcp`, once it is one of the Air's connectors.

## AI Daily Pipeline

Extracts app/tool/platform mentions from transcripts using LLM extraction.

**Neon schema:** 3 tables — `ai_runs`, `ai_entities`, `ai_mentions` (plus `notion_page_id` / `notion_synced_at` on entities)
**Extraction model:** gpt-4.1-mini via OpenAI API
**Transcripts:** imported via Taddy API (originally RSS + Firecrawl, migrated to Taddy)
**Extraction status:** new episodes are extracted automatically. About 141 old episodes (pre-Dec 2025) are intentionally skipped — they failed quality gates on lighter episodes and aren't worth re-processing; the orchestrator's recent-only window (`RECENT_EPISODE_WINDOW_DAYS = 90` in `run_new_episodes.py`) leaves them out. Counts: `pipeline/data_health.py`.
**Notion destination:** the Tech DB (🛠️ Tech Tools & Mentions, which began as "AI Daily Brief — Tools & Mentions"; DB ID: `982dafa0ad374d618e25207e67860e33`, MCP data source: `a72f8f82-1ca0-4973-9dc2-3757aa729c6e`). Sync via `pipeline/sync_notion.py`.
**Orchestrator:** `pipeline/run_new_episodes.py` — chains Taddy import → entity extraction → alias normalization → Notion sync → Spotify sync. Run with `--shows ai-daily-brief` or `--all`.

**Required env vars** (in `.env.local`):
- `DATABASE_URL` — Neon connection string (`common.py` also takes `NEON_DATABASE_URL`, the name CI uses)
- `OPENAI_API_KEY` — for entity extraction
- `NOTION_TOKEN` — for Notion sync
- `TADDY_USER_ID` / `TADDY_API_KEY` — for Taddy transcript import

See `pipeline/scrapers/ai_daily/README.md` for full pipeline docs.

### Running Pipeline Scripts

The pipeline uses a Python venv and two `.env.local` files (repo root and `pipeline/`) that don't use `export`. The orchestrators and `import_transcripts.py` load them themselves (`load_environment()`); a few helper modules (`announce.py`, `feed_check.py`, the intake judge) only read the environment, so run one of those on its own with the vars exported.

**Working command pattern** (exports both files first, so it works for any script):
```bash
cd ~/DevKev/personal/list-maker && set -a && source .env.local && source pipeline/.env.local && set +a && cd pipeline && ./venv/bin/python3 <script>
```

- `set -a` / `set +a` — exports all sourced vars to child processes
- Must use `./venv/bin/python3` — system python3 doesn't have deps (psycopg2, etc.)
- The orchestrator (`run_new_episodes.py`) uses `common.py`'s `load_environment()` so it handles env loading itself, but still needs the venv

## Project-Specific Notes

- **SOP and TAL automated** - Both shows backfilled, playlists updated automatically via GitHub Actions
- **Scrape before transcribe** - SOP and TAL have song data on their websites
- **Mosaic artwork done** - See `marketing/` for playlist cover generators
- **Taddy covers every podcast but Culture Gabfest** (Megaphone show notes; Taddy won't transcribe it) - see each show's `taddy_uuid` in `pipeline/show_config.py`

## Notion surface — the Pod Lists hub

**Hub page (all DBs live under it):** https://app.notion.com/p/Pod-Lists-31c0501ef95080d1a3fde8fa8d5ce907 — **when you add/rename a DB or change what flows where, update the hub page's prose too** (Kevin browses from there; a DB he doesn't know exists might as well not).

| DB | ID | What |
|---|---|---|
| 🛠️ Tech Tools & Mentions | `982dafa0…` | Entities/mentions: podcasts + blogs + research (Sources/Items/URL columns; "Vetted sources" view) |
| 🍿 Media Recommendations | `3780501e…94657` | PCHH + Gabfest media entities |
| 📝 Tech Show Transcripts | `3780501e…62c9d` | Podcast full texts + Saved Episodes one-offs; **Clips** column = Kevin's highlights |
| 📰 Blog Posts | `37c0501e…93f5` | Curated article full texts (URL + Links Out) |
| 📥 Blog Intake | `37c0501e…1f53` | Every judged candidate + verdict/reason; **Pull anyway** ☑ is the override door |

## Relevant Docs & Links

- **Engineering principles:** `docs/principles.md` — distilled from Kevin's four research guides (legibility, automation planes, data provenance, dependency hygiene). Read before substantive pipeline changes; the full-reasoning canonical guides live in his Obsidian vault.
- **Plans:** no arc is live (NOW.md, 2026-11-06: routine operation). The last ones, all shipped, sit at the top of `claude-plans/`: `2026-09-02-curated-intake-v2/` (judged intake + ads as data), `2026-09-03-phase-4/`, `2026-09-04-phase-5/`, and `2026-09-01-ground-it-cleanup-plan.md` (the cleanup pass and Kevin's decisions)
- **Origins:** `claude-plans/archive/2025/2025-12-12-initial-plan.md` and `…-project-context.md` (the original research chats, summarized)
- **Original research chats:**
  - `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - CSV Playlist Creation Guide_67e70c24.md`
  - `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - Workflow and transcript strategy_68eaedfe.md`

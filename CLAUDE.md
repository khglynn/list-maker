# list-maker - Agent Instructions

*Inherits from ~/DevKev/CLAUDE.md*
*Last updated: 2026-09-23*

## ⚑ Resuming (especially after a compaction)

Read `NOW.md` first — its top block is the live state and the exact next step — then whatever plan it points at. The June-2026 "durable rebuild" this section used to route you to finished on 2026-06-07 (its docs live in `claude-plans/archive/2026/`); since then the repo runs on a daily cron with hardening PRs on top. Post-compaction sessions drift toward doing the minimum — still the #1 failure mode here; the global CLAUDE.md's "Default to deep, durable work" is the antidote.

## About This Project

Automated pipeline that extracts recommendations from podcasts and routes them to the right platforms.

**Source of truth:** Neon (Postgres) - all data lives here first, then syncs to other platforms.

**Destinations:**
- **Music** → Neon → Spotify playlists (one per show)
- **Movies/TV** → Neon → Notion + Trakt
- **Books** → Neon → Notion
- **Apps/Products** → Neon → Notion

**Data strategy:** Avoid transcription when possible. Many podcasts list songs/recommendations on their websites (FREE). Use cascading logic: website → free transcripts → transcript API → Whisper (last resort).

## Communication Default

Kevin prefers a "help me mode" by default:
- Use plain language first, minimal jargon.
- Be prescriptive: clear step-by-step actions with expected outcomes.
- Explain why each step matters in one short sentence.
- Keep asks small (1-2 actions at a time), then continue.
- Prefer "I handled X, now please do Y" over long implementation explanations.

## Key Abbreviations

| Abbreviation | Full Name | Data Source |
|--------------|-----------|-------------|
| SOP | Switched On Pop | Website show notes |
| TAL | This American Life | Website song credits |
| AI Daily | AI Daily Brief | Taddy transcripts → LLM extraction |
| PCHH | Pop Culture Happy Hour | Taddy transcripts (pipeline not built yet) |

## Tech Stack

- **Database:** Neon (Postgres) - source of truth
- **APIs:** Spotify (via MCP), Notion, Firecrawl (web scraping)
- **Transcripts:** Taddy API (multi-show transcript import)
- **Extraction:** OpenAI gpt-4.1-mini (AI Daily entity extraction from transcripts)

## Spotify MCP

We have a custom Spotify MCP built for this exact use case!

**Location:** `~/DevKev/personal/spotify-bulk-actions-mcp/`
**Repo:** https://github.com/khglynn/spotify-bulk-actions-mcp

**Key tools:**
- `batch_search_tracks` - Search songs with confidence scoring (HIGH/MEDIUM/LOW)
- `import_and_create_playlist` - CSV → playlist workflow
- `create_playlist_from_search_results` - Create from batch search
- `add_reviewed_tracks` - Add human-reviewed uncertain matches

**Settings:** Configured in `~/.claude/settings.local.json`

## Always-Allowed (project-specific)

*(Will add paths as we build)*

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
├── .github/workflows/       # entities / pipeline / eval / blogs / pulse / test — all workflow_dispatch from the Worker
├── docs/                    # principles.md (the engineering rules) · curation-runbook.md
├── codex-notes/             # Historical AI Daily design notes + one kept example batch; CI also writes extraction
│                            # output here (codex-notes/ai-daily-entity-extraction/incremental-*, gitignored)
├── marketing/               # Playlist artwork (mosaic generator; its own CLAUDE.md)
└── claude-plans/            # Live plans at the top; archive/YYYY/ for finished ones (+ early ideation, guides, prompts)
```

## What's live (no counts here — run `pipeline/data_health.py` or open the Notion hub for numbers)

Ten sources, three destinations. **Podcasts** (Taddy transcripts unless noted): AI Daily Brief + Hard Fork → the shared **Tech DB**; Pop Culture Happy Hour + Culture Gabfest (Megaphone show-notes; the show ended 2026-07-01) → the shared **Media DB**; Switched On Pop + This American Life (website song lists) → one **Spotify playlist** each. **Curated sources** (no feed, no cadence): openai-blog, anthropic-blog, saved-articles, agentic-research → Tech DB, full texts mirrored to the Blog Posts DB; saved-episodes → the Transcripts DB. Entities are global across shows, deduped, tagged with a "Shows" multi-select (Option A, 2026-06-07). Design and data flow: `ARCHITECTURE.md`. History: `DEVLOG.md`.

## Automation

The pipeline runs automatically. The **durable trigger** is the Cloudflare Worker (`cloudflare-trigger/`) calling `workflow_dispatch` — NOT GitHub's own `schedule:` (which silently disables after 60 idle days). Workflows: `pipeline.yml` (music → Spotify), `entities.yml` (tech + media → Notion), `eval.yml` (weekly extraction eval).

**Schedule:** one Cloudflare cron fans out by day — entities daily; SOP Wed+Fri, TAL Mon; eval + blogs Mon; pulse 1st/15th (after the import). The time and the day logic live in `cloudflare-trigger/worker.js` (`dispatchesFor`, tested) — read it there rather than trusting a restated copy.

**Manual trigger:** Actions tab → the workflow → Run workflow (or the Worker's `/?token=…` endpoint).

**Secrets:** `SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI/CACHE_JSON`, `NEON_DATABASE_URL`, `FIRECRAWL_API_KEY`, `SLACK_WEBHOOK_URL`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY` (the two intake judge models), `NOTION_TOKEN`, `TADDY_USER_ID/API_KEY`. Cloudflare Worker secret: `GH_PAT` (+ optional `SLACK_WEBHOOK_URL`, `TRIGGER_TOKEN`).

**If Spotify auth fails:** Re-auth locally (`python spotify_match.py --show-id 1 --limit 1`), then update `SPOTIFY_CACHE_JSON` secret with new `.spotify_cache/.cache` contents.

See `pipeline/README.md` for orchestrator docs, `evals/README.md` for the eval harness, `cloudflare-trigger/README.md` for the trigger.

## Alerts — what each Slack message in #list-maker means (2026-09-23)

**The rule for failures:** say it when a check starts failing (or the set of failing checks changes), again **every 7 days** while it is still unfixed, and **once on recovery**. Never daily. Kevin's standard: a check that talks every day trains its reader to ignore it; one that talks only once can't tell him a problem was never fixed.

**One invariant carries it** (`pipeline/announce.py`, simplified 2026-09-24). For each check or step, a Slack post is only ever one of:
1. **its first failure** ("N new problem(s)"), which also opens its own GitHub issue;
2. **the weekly word**: every open issue whose last post is 7 or more days old gets one, whatever today's result — "still failing since Sep 22 (8 days)", "failing again" if it relapsed after "recovered", "couldn't check today" when the run didn't get to it (a skipped step, a feed that didn't answer), or "passing, confirming recovery" when it's green but not yet verified. An open problem never goes silent;
3. **a verified recovery**, and only if the last thing said was "failing": two green runs in a row (consecutive runs of the workflow) for the daily entities run, the first green run for the weekly and twice-weekly workflows. A show the feed check couldn't see stays counted as failing until it's seen passing.

What follows from that:
- **Flapping is one problem:** a check that flips back and forth stays one issue, said at most weekly, and the weekly word counts the flips ("on and off: failed 4 of the last 7 runs"). A relapse within a week of "recovered" reopens the same issue quietly; the next word is the weekly one.
- **The issue is the memory** (label `pipeline-failure` + `entities` / `music` / `intake`): a hidden marker at the top holds the dates, what was last said, the green streak, the last 14 results and the failing shows. It closes itself on recovery. Closing one by hand means "I think it's fixed": it is never reopened, and if the problem is still there the next run opens a fresh one.
- **Per check, not per show:** a second show failing a check that is already open shows in the issue at once and in the next weekly word, not as a message of its own.
- **A music show's feed gap can have two threads:** `pipeline.yml`'s (judged right after each import) and the daily entities run's backstop (a week past the window). Each says at most one thing a week. That is the price of never letting one workflow silence another's check.
- **A cancelled run counts as a failure** (usually a time limit; every step has its own time limit, and they add up to less than the job's, so a hang normally fails a named step instead).
- **Manual `pipeline.yml` runs for `all` or show 3** post when they fail but keep no issue: no schedule re-checks them, so an issue would never be reminded or closed.

| Message | Sent by | When | What it means / first move |
|---|---|---|---|
| :rotating_light: `list-maker · daily entities run — N new problem(s)` (also `SOP music run`, `TAL music run`, `weekly curated intake`) | `pipeline/announce.py`, the last step of `entities.yml`, `pipeline.yml`, `blogs.yml` | A check or step started failing | Each item says what failed, why it usually happens and what to check first (text in `pipeline/alert_guides.py`), with links to its issue and the run |
| :hourglass_flowing_sand: `… — still failing` | same | The same failure, 7+ days after it was last announced (also when it couldn't be checked that day: "couldn't check today") | Nothing got fixed. "On and off" means it is flapping. The issue has the history |
| :hourglass_flowing_sand: `… — confirming recovery` | same | 7+ days since the last word, and it's passing but not yet verified | Probably fixed; "recovered" follows once it's confirmed |
| :rotating_light: `… — failing again` | same | It relapsed after "recovered", and a week has passed since the last word | It isn't fixed after all; same issue, reopened |
| :white_check_mark: `… — recovered` | same | Verified: two green runs in a row (daily), or the first green run (weekly) | Its issue is closed. Nothing to do |
| :warning: `… — the alert step itself crashed` | same | `announce.py` raised | The run's result wasn't announced; read the Announce step's log |
| :inbox_tray: `list-maker intake [all]: judged …` | `pipeline/run_intake.py` (`blogs.yml`) | Every Monday, even a quiet week | Not an alert: the week's intake report |
| :bar_chart: `list-maker pulse` | `pipeline/pulse_report.py`, the `pulse` job in `entities.yml` | 1st and 15th | The positive heartbeat. If one doesn't arrive, the trigger or the pulse is broken. `pulse FAILED` comes from `pulse.yml` |
| :rotating_light: `DB preflight failed` | `pipeline/db_preflight.py` | Only in `eval.yml` / `pulse.yml`; elsewhere it's folded into the announce message | The runner couldn't reach Neon; usually that runner's network, and the next run heals it |
| :rotating_light: `extraction eval FAILED` / `intake judge eval FAILED` | `eval.yml` (+ `evals/*/run_eval.py`) | Monday eval breaches a quality floor | Not yet on the announce step, so it can still post twice |
| `list-maker: <workflow> failed` / `cron trigger FAILED` / `dispatches could not be checked` | `cloudflare-trigger/worker.js` | Next day, about yesterday's dispatches | Only if the Worker's own `SLACK_WEBHOOK_URL` secret is set — none has appeared in the channel since June |

**Mid-run warnings** (a Notion sync with some failed pages) go through `common.alert_note`: inside the three announced workflows they ride along in that run's message, and are otherwise listed in the Announce step's summary. Run locally, they post directly. A sync that keeps failing on the same entity becomes a real failure after 2 days, and a Notion page that can't be created at all fails at once (`notion_sync_freshness`), so both are announced like any other failure.

**A feed check that couldn't reach a feed** ("feed UNVERIFIED") neither fails nor recovers an issue about that show: it stays open and still gets its weekly word, saying it couldn't check.

**How fast a music-show gap is caught (a known trade, 2026-09-23):** `pipeline.yml` judges SOP and TAL right after each import, but a missed episode only counts once it is older than the show's window (SOP 6 days, TAL 2), so a real miss usually surfaces at the show's *next* import, about a week in; the entities backstop follows a few days later if `pipeline.yml` has gone quiet. Before this, the daily entities run caught misses in 2–4 days but also cried wolf. A tighter window for the check that runs straight after an import (only the source's own lag) would bring this back to a day or two; it needs a few weeks of SOP website-lag data first.

**The Worker's own Slack secret is unset on purpose-ish:** if it is ever set, its next-day line (`list-maker: entities.yml failed …`) fires for every red day, outside the once-then-weekly rule.

**Where to change it:** thresholds live with each check in `pipeline/data_health.py`; words in `pipeline/alert_guides.py` (a test fails if a check has none); the once / weekly / recovered logic in `pipeline/announce.py`. The music shows are judged by `pipeline.yml` right after each import; the daily entities run checks their feeds only as a backstop a week past their window (`FEED_BACKSTOP_EXTRA_DAYS`, `--music-as-backstop`). Every other check about a music show (songs still arriving, freshness) belongs to the entities run alone. A workflow's `dry_run` input runs the announce step read-only and prints what it would have said.

## AI Daily Pipeline

Extracts app/tool/platform mentions from transcripts using LLM extraction.

**Neon schema:** 3 tables — `ai_runs`, `ai_entities`, `ai_mentions` (plus `notion_page_id` / `notion_synced_at` on entities)
**Extraction model:** gpt-4.1-mini via OpenAI API
**Transcripts:** 915 episodes imported via Taddy API (originally RSS + Firecrawl, migrated to Taddy)
**Extraction status:** 773/914 episodes extracted (85%). ~141 old episodes (pre-Dec 2025) are intentionally skipped — they failed quality gates on lighter episodes and aren't worth re-processing. The orchestrator's `recent_only` filter (90 days) excludes them automatically. New episodes are extracted automatically.
**Notion destination:** Connected. Database "AI Daily Brief — Tools & Mentions" (DB ID: `982dafa0ad374d618e25207e67860e33`, MCP data source: `a72f8f82-1ca0-4973-9dc2-3757aa729c6e`). Sync via `pipeline/sync_notion.py`.
**Orchestrator:** `pipeline/run_new_episodes.py` — chains Taddy import → entity extraction → alias normalization → Notion sync → Spotify sync. Run with `--shows ai-daily-brief` or `--all`.

**Required env vars** (in `.env.local`):
- `DATABASE_URL` — Neon connection string
- `OPENAI_API_KEY` — for entity extraction
- `NOTION_TOKEN` — for Notion sync
- `TADDY_USER_ID` / `TADDY_API_KEY` — for Taddy transcript import

See `pipeline/scrapers/ai_daily/README.md` for full pipeline docs.

### Running Pipeline Scripts

The pipeline uses a Python venv and `.env.local` files that don't use `export`. Scripts like `import_transcripts.py` read env vars via `os.getenv()` but don't call `load_environment()` from `common.py`, so you must export vars manually.

**Working command pattern:**
```bash
cd ~/DevKev/personal/list-maker && set -a && source .env.local && source pipeline/.env.local && set +a && cd pipeline && ./venv/bin/python3 <script>
```

- `set -a` / `set +a` — exports all sourced vars to child processes
- Must use `./venv/bin/python3` — system python3 doesn't have deps (psycopg2, etc.)
- The orchestrator (`run_new_episodes.py`) uses `common.py`'s `load_environment()` so it handles env loading itself, but still needs the venv

## Project-Specific Notes

- **SOP and TAL automated** - Both shows backfilled, playlists updated automatically via GitHub Actions
- **SOP matching partially improved** - NOT_FOUND dropped from 534 → 357 since Dec 2025. 17 more tagged UNAVAILABLE (not on Spotify).
- **Scrape before transcribe** - SOP and TAL have song data on their websites
- **Mosaic artwork done** - See `marketing/` for playlist cover generators
- **Taddy scraper supports multiple shows** - AI Daily, PCHH, SOP all configured

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
- **Live plan:** `claude-plans/2026-09-02-curated-intake-v2/PLAN.md` (the current arc: judged intake + ads as data); its parent `claude-plans/2026-09-01-ground-it-cleanup-plan.md` holds the cleanup pass, Kevin's decisions, and the phases after this arc
- **Origins:** `claude-plans/archive/2025/2025-12-12-initial-plan.md` and `…-project-context.md` (the original research chats, summarized)
- **Original research chats:**
  - `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - CSV Playlist Creation Guide_67e70c24.md`
  - `~/Documents/HG Main/0.0 Daily Notes + Projects/2025/Q4/11 Nov/Projects/Notes organizer workflow - agent/AI chats on this topic/Unknown - Workflow and transcript strategy_68eaedfe.md`

## Playwright Instance

Use `playwright-generic` for this project. No project-specific Playwright MCP set up yet.

# Off Taddy: plan (2026-11-23)

*Status: plan for Kevin's yes. Written 2026-11-23 (evening CT) by a planning session. Nothing in the pipeline was changed, committed, bought or signed up for. Every number below was read live that evening: the shows' public feeds, the publishers' transcript pages, and Neon (read-only session). The probe scripts are listed at the end.*

Kevin, 2026-11-23: "yes let's do a non-taddy path thanks. I think I always intended to cancel after we filled out backlog." Taddy Pro ($75/mo) next bills **2026-12-07**. Target: the new path is live and seen working by **2026-12-04**, Taddy is cancelled by **2026-12-05**.

> **Update, 2026-11-23 night: Hard Fork is replaced by Machine Gods, which removes speech-to-text from this plan.** Kevin: "hard fork has basically become machine gods. so we can drop hard fork if we can find machine gods." Machine Gods is Kevin Roose and Casey Newton's new twice-weekly show with NPR (launched October 2026). What was checked tonight:
> - Feed `https://feeds.npr.org/g-s1-143588/podcast.xml`, found through Apple's lookup. It had 12 items; the newest was 2026-11-20.
> - Each item's `<link>` is an npr.org story URL whose id (`nx-s1-…`) opens `https://www.npr.org/transcripts/<id>`. That is the same reader planned for Pop Culture Happy Hour. Three recent episodes (11-20, 11-18, 11-13) each had a full transcript of about 10,000 to 11,600 words. The 11-18 one has 137 "Roose:" / "Newton:" speaker turns.
>
> So in the sections below: Hard Fork stops being collected (its 226 stored transcripts stay), and Machine Gods joins as an NPR-transcript show. The open-source speech-to-text step on GitHub's machines, the hosted-runner terms question, and the local-Mac / Particle fallbacks all drop out. One open choice is whether to backfill Machine Gods' episodes from launch; that is about 12 episodes, all with transcripts.

> **Build status lives in `2026-11-23-off-taddy-STATUS.md`** (what's seen working, what's built, what waits on Kevin), along with the builder's calls where the build departs from this plan.

Builds on an earlier research note kept outside this repo (what list-maker calls, volumes, prices). Where this plan disagrees with that note or with the brief, it says so.

---

## The short version

1. **Recommendation: the publishers' own transcripts first, free speech-to-text for the rest.**
   a. AI Daily Brief posts its own transcript for every episode (`aidailybrief.ai/e/<date>/transcript.md`, "feed it to your own agent"). 39 of 39 episodes in the last 45 days had one.
   b. NPR posts a full, speaker-labelled transcript for every Pop Culture Happy Hour episode (`npr.org/transcripts/<story id>`). 12 of 12 recent episodes had one.
   c. Hard Fork (NYT, about 4 to 6 episodes a month) has no free transcript, so we transcribe its audio ourselves with open-source Whisper. That runs on GitHub's free machines, inside the daily run that already exists.
   d. This American Life doesn't need transcripts at all. Its songs come from its website. The 4 a month Taddy charged for were a side effect of using Taddy to find new episodes.
   e. Every show's new episodes come from its own public RSS feed.
2. **Monthly cost: $0** at today's volume (about 50 transcripts a month), against $75 for Taddy Pro. No new accounts, no paid per-use API, and no Firecrawl credits in the main path.
3. **Phases** (details in section 6):
   0. Before anything changes, save the one thing only Taddy holds: a Taddy-id-to-feed-id map for 28 TAL rows (one API call). (2026-11-24)
   1. Build the new feed reader, the two publisher-transcript readers and the speech-to-text step, all switched off. (11-24 to 11-26)
   2. Shadow-run them for at least 5 daily runs alongside Taddy, comparing results without writing anything. (11-26 to 12-01)
   3. Switch over: move every episode's stored id to the feed's own id, flip the switch, take Taddy out of the workflows. (about 12-02)
   4. Two green live days, then you cancel Taddy. (12-04 or 12-05)
   5. Afterwards: the manual saved-episodes tool and clip transcription, which both currently lean on Taddy and the closed OpenAI account.
4. **Needs your yes:** the plan; stopping TAL transcripts; the one-time id change in Neon (production data); cancelling Taddy (your click); deleting the two Taddy secrets from GitHub. Full list in section 8.

Two corrections to the brief, both load-bearing:
- **Local machines are not a pipeline plane.** None of the always-on machines can run the Apple-Silicon speech-to-text stack well, which is part of why speech-to-text went on GitHub's machines (section 4; moot since the update box).
- **list-maker doesn't run on your own runners.** Every workflow is `runs-on: ubuntu-latest`. That's GitHub's hosted machines, which are free and unlimited for a public repo (list-maker is public per `docs/principles.md`). It is not part of the PC runner pool, which only serves private repos.

---

## 1. What Taddy does today (read from the code, 2026-11-23)

| Where | Taddy call | What it's for | Replaced by |
|---|---|---|---|
| `pipeline/scrapers/taddy/import_transcripts.py` (daily via `run_new_episodes.step_import`) | `getPodcastSeries`, `getLatestPodcastEpisodes` (50 a page, up to 20 pages), `getEpisodeTranscript` (one credit each), `getTranscriptCreditsRemaining` | New episodes + transcripts for AI Daily, Hard Fork, PCHH | RSS importer + transcript sources (sections 2, 3) |
| `pipeline/run_pipeline.discover_tal_episodes` (Mondays, `pipeline.yml`) | the same importer, `--shows tal` | New TAL episode rows (and, as a side effect, a transcript credit each) | RSS importer for TAL, no transcript |
| `pipeline/feed_check.py` (`data_health.py` daily, `pipeline.yml` after each music run, `pulse.yml`) | `getLatestPodcastEpisodes` page 1 | The second source: "is our import behind the real feed?" | Read the RSS directly |
| `pipeline/save_episode.py` (manual, local only) | `search`, `getEpisodeTranscript` | Full transcript for a one-off saved episode | iTunes Search + local speech-to-text (phase 5) |
| `pipeline/show_config.py` | `taddy_uuid`, `taddy_episode_url`, `episode_identity="taddy_uuid"` | Show registry and episode identity | `feed_url`, `itunes_id`, `transcript_source`, identity = the feed's guid |
| `run_new_episodes.py:675` | `require_transcript=bool(cfg.taddy_uuid)` | "Wait up to 7 days for a transcript before mining show notes" | an explicit `require_transcript` field (same behaviour) |
| `.github/workflows/entities.yml`, `pipeline.yml`, `pulse.yml` | `TADDY_USER_ID` / `TADDY_API_KEY` secrets | | removed at cut-over |

What Neon holds (read-only, 2026-11-23):

| Show | Episodes | With transcript | Rows whose saved Taddy data carries the feed's guid | Stored id today |
|---|---|---|---|---|
| AI Daily Brief | 1,147 | 1,147 | 1,147 | 980 Spotify-page links (to 2026-05-18), 167 Taddy ids |
| Hard Fork | 226 | 226 | 226 | 223 Taddy ids, 3 nytimes.com links |
| PCHH | 464 | 464 | 464 | 356 npr.org links, 107 Taddy ids, 1 other |
| TAL | 917 | 42 | 0 (TAL stores no Taddy payload) | 888 TAL-site links, 28 Taddy ids, 1 other |
| SOP | 727 | 547 (backfill, last 2026-08-04) | 0 | website links (Taddy is only its feed check) |

Transcripts Taddy delivered by month: Sep 54, Oct 54, Nov to the 23rd 41 (AI Daily about 27, PCHH 17 to 19, Hard Fork 4 to 6, TAL 4 to 5). That matches the research's "about 55".

## 2. Finding new episodes without Taddy

**Each show's own RSS feed**, found through Apple's free lookup API (`https://itunes.apple.com/lookup?id=<collectionId>` returns `feedUrl`; no key, no account). The collection id goes into `show_config` beside the feed, so if a feed ever moves, re-finding it is one lookup. The importer also follows HTTP redirects and the feed's own `itunes:new-feed-url`, and logs when either fires.

| Show | Apple id | Feed (checked live 2026-11-23) | Items in feed | New per month | guid shape |
|---|---|---|---|---|---|
| AI Daily Brief | 1680633614 | `https://anchor.fm/s/f7cac464/podcast/rss` (8.6 MB, sends an ETag) | 1,147, back to 2023-04 | ~27 | UUID |
| Hard Fork | 1528594034 | `https://feeds.simplecast.com/6HKOhNgS` | **4** (3 newest + a 2022 trailer) | ~4 to 6 | UUID |
| PCHH | 278974813 | `https://feeds.npr.org/510282/podcast.xml` | 300, back to 2025-06 | ~17 | UUID |
| TAL | 201671138 | `https://www.thisamericanlife.org/podcast/rss.xml` | 16 | ~4 | `37237 at https://www.thisamericanlife.org` |
| SOP (feed check only) | 934552872 | `https://feeds.megaphone.fm/switchedonpop` | 562 | ~4 | UUID |

Podcast Index was considered and not needed: it needs a key (a new account) and adds nothing these feeds don't already give. Apple's episode list is not a usable second source: it mirrors the feed exactly (Hard Fork shows the same 4 items there), and its episode guids differ from the feed's for AI Daily and TAL.

**Identity: the feed's own guid**, the `rss_guid` scheme Culture Gabfest already uses (`show_config.episode_identity`). Checked in Neon: the guid Taddy saved for each episode is the live feed's guid (spot-checked on all three entity shows), every entity-show row has one, no guid repeats within a show, and none collides with any stored id on any show. So `episodes.url := guid` is a clean one-time move for 1,837 rows (phase 3). For TAL's 28 Taddy-id rows, the map comes from one Taddy call in phase 0.

Rules the new importer must keep. Each has bitten this repo before:
1. **Dates in UTC.** Taddy stored each episode's UTC date. TAL's feed says `Sun, 22 Nov 2026 20:00:00 -0500`, and Neon holds that episode (436) as 2026-11-23. `import_gabfest.parse_pubdate` keeps the local date. Reusing it would miss the title+date match and duplicate every TAL row in the feed, which is how 886 and 887 already exist twice (BACKLOG).
2. **Match before inserting, in this order:** stored id = guid; then the guid inside `raw_content` (covers rows not yet migrated); then lower(title) + UTC date, scoped to the show, as `upsert_episode` does today.
3. **Set `scraped_at` on insert.** `sync_playlist.get_latest_episode` reads `scraped_at IS NOT NULL` for the playlist description. Never touch it on rows already held: that side effect drained the TAL song queue in 2026 (`tal/fetch.get_episodes_missing_songs` docstring).
4. **Keep `raw_content`'s shape for the entity shows:** `provider` ("rss"), `guid`, `name`, `description` (the feed's HTML, as Taddy gave it; `sponsors.roster_from_raw_content` parses the "Brought to you by:" block from it), `link`, `enclosure_url`, `imported_at`.
5. **Hard Fork: transcribe in the same run that discovers it.** NYT's public feed is short and loses episodes. Neon holds 2026-11-06 ("Why the Tech Right Lost Big This Week") and 11-10 ("The Jensen Huang Affair"), and neither is in the feed or Apple's list today. Taddy saw them while they were up. A daily poll does too, but the audio link may die once an item is pulled, so we fetch it at once.

**The feed check** (`feed_check.py`, `data_health.check_import_caught_up`, the pulse) reads the same RSS through one shared parser, so the reader and the writer of the stored id can't drift (the existing contract in `feed_check.rss_recent_episodes`). SOP keeps its date-only comparison, now against Megaphone. The `TADDY_EPISODE_URL_PREFIX` guard in `show_config.is_tal_episode_page_url` stays, because legacy rows and old JSONs still carry Taddy URLs.

## 3. Transcripts without Taddy

### The options, compared

| Option | Covers | Monthly cost, today's volume | Needs Kevin | Quality | What can break |
|---|---|---|---|---|---|
| **Publisher transcripts** (AI Daily markdown, NPR pages) | AI Daily + PCHH, ~44 of ~50 | $0 | nothing | NPR: human-edited, speaker labels. AI Daily: a full transcript with timestamps (190 to 210 words per audio minute, so complete) | A page format change, or the runner's IP being refused (untested from a runner) |
| **Speech-to-text on GitHub's machines** (faster-whisper, CPU) | any show; used for Hard Fork + fallback | $0 (public repo: "free and unlimited", [GitHub docs](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)) | nothing | Whisper large-v3-turbo class, likely what Taddy used | Speed on 4 CPUs unmeasured; GitHub's terms bar hosted-runner use "unrelated to the production ... of the software project" ([terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)). This pipeline already runs there daily; 4 to 6 more jobs a month is small |
| **Speech-to-text on a Mac** (mlx-whisper, large-v3-turbo; already cached on a local Mac) | any show | $0 | yes: the only Apple Silicon Mac is a work laptop | best of the local options; likely 10 or more times real time on Apple Silicon (published benchmarks show 20 to 30 on newer chips; not measured here) | A laptop that sleeps and travels; a second place the pipeline runs, with its own secrets and its own monitoring. The repo's own rule (`docs/principles.md`) keeps runtime in one plane |
| **Particle through Firecrawl** | unverified for these shows | ~50 transcripts x 15 + ~50 lookups x 5 ≈ 1,000 credits of the existing 8,000 a month: $0 extra; top-ups are $5 per 1,000 and must never auto-buy | yes: accept Particle's terms once at https://www.firecrawl.dev/app/alexandria/particle | speaker-labelled | Another vendor in the critical path, the thing we're removing; "requestable" episodes may not be transcribed yet |
| Paid speech-to-text APIs (Deepgram, AssemblyAI) | any | about $1 to $3 for Hard Fork's hours (third-party rate figures, not re-checked) | yes: a paid per-use route and a new account | good | Not needed |
| The PC's RTX 3070 | any | $0 | yes | very fast | A shared PC that sleeps through trips and froze several times the week of 2026-11-10 |

### Recommendation, per show

| Show | Source | Fallback | Volume |
|---|---|---|---|
| AI Daily Brief | `https://aidailybrief.ai/e/<YYYY-MM-DD, US Eastern>/transcript.md` | speech-to-text after 3 days without one | ~27 a month |
| PCHH | `https://www.npr.org/transcripts/<nx-s1-NNNNNNN>`, the id taken from the feed item's `<link>` | speech-to-text after 3 days | ~17 a month |
| Hard Fork | speech-to-text, same run as discovery | a local Mac or Particle, only if phase 2's numbers fail | ~4 to 6 a month, ~3 to 6 hours of audio |
| TAL | none (recommended; your call, 8a). TAL publishes free transcripts at `thisamericanlife.org/<n>/transcript` if you want them searchable | | |
| SOP | none (unchanged) | | |

Why this and not the Mac: publishers cover nearly all of it for free and better than any machine transcript. The leftover, about one Hard Fork a week, fits on the machine that already runs every step, survives your trips and sleeping laptops, and costs nothing. The Mac is the documented fallback if GitHub's terms or speed ever say no. It is also where the manual tools already run, so they use it in phase 5.

**Guards on the publisher readers** (each found in this evening's probes):
1. **The title must match.** The transcript's first line is `# <title> — Transcript (<date>)`. The 2026-11-23 episode, 6 hours after publishing, had no transcript yet, and a date-only lookup that slipped a day returned the previous day's episode. A promo item on 2026-10-26 shares its date with the main episode. So: fuzzy title match ≥ 0.8 against the feed title, else "not yet", retried by the next run.
2. **Sanity on length:** words per audio minute between about 120 and 260, else "not yet" and logged.
3. **Normalise to the shape extraction was tuned on:** plain paragraphs. Strip AI Daily's header and `[00:00:00]` markers. Taddy's text carried no speaker names, so the default is to strip NPR's `NAME:` labels, with the raw page kept by `source_url`. The weekly eval (`eval.yml`, Mondays) watches for drift; per-episode comparisons are too noisy to gate on (`evals/README.md`: about 40% of entities churn run to run).
4. **Provenance in the row:** `source_type` = `publisher_transcript` or `stt`; `model` = `aidailybrief_md` / `npr_transcript_page` / `faster-whisper:<model>:<compute>`; `is_generated` false only for NPR; `source_url` = the page or the audio link.
5. **Terms:** NPR allows "personal noncommercial use" ([terms](https://www.npr.org/about-npr/179876898/terms-of-use)), which is what this is. `npr.org/robots.txt` allows `/transcripts/` apart from a few listed ids. `aidailybrief.ai/robots.txt` allows everything. **The repo and its Actions logs are public**, so no transcript text goes into code, test fixtures, logs, step summaries or artifacts. Fixtures are short and made up.

### Transcript lag and the existing safety net
Taddy's lag was about a day (p90, measured 2026-09-01). AI Daily's markdown wasn't up 6 hours after publishing; phase 2 measures the real lag. The pipeline already tolerates lag: `find_unextracted_episodes(require_transcript=True)` waits up to 7 days before falling back to show notes, and `step_self_heal_transcript_race` re-extracts once the real text lands. Nothing about that changes.

## 4. Where it runs

- **Control plane: unchanged.** The Cloudflare Worker's one daily cron (20:30 UTC) and `dispatchesFor` stay as they are. No new dispatch.
- **Runtime: GitHub's hosted machines, as today.**
  a. `entities.yml` gains one step after "Run entity pipeline": **Transcribe audio**, with its own time limit (about 45 minutes, sized from phase 2). It covers Hard Fork episodes plus any publisher transcript overdue by 3 days, capped per run by audio minutes. The job limit rises from 90 to about 135 minutes; `tests/test_workflows.py` keeps the step sum under it. The announce step already reports a failed step, so a broken transcriber is loud with no new alert code.
  b. Speech-to-text dependencies (`faster-whisper`, which brings CTranslate2 and PyAV; no system ffmpeg needed) go in their own requirements file, installed only by that step. Tests and the main install stay light. The model is cached with `actions/cache` (about 1.6 GB for turbo; the repo's cache limit is 10 GB).
  c. `pipeline.yml` (TAL Mondays) and `pulse.yml` lose their Taddy secrets; TAL discovery becomes the RSS importer.
- **Data plane: Neon, unchanged** apart from the one-time id move and new rows' provenance values.
- **Secrets:** `TADDY_USER_ID` and `TADDY_API_KEY` leave the three workflows at cut-over, the repo's secrets after cancel (your yes: a GitHub deletion), and `.env.local` on this Mac.
- **Monitoring:** `data_health.TRANSCRIPT_POLICIES` gains `hard-fork` as `complete`. Today it falls to the default "latest, 30 days, warn only", so a Hard Fork without a transcript would never fail a check. Gabby's morning check needs no change; she reads issues and runs.

## 5. The daily back-catalog re-scan (fixed by the new importer's design)

Today, every daily run pages through each show's whole Taddy history: about 1,000 AI Daily, 226 Hard Fork and 300 PCHH episodes, roughly 31 requests. Two costs hide in it:
1. The requests (about 1,300 a month).
2. A database write for every one of those ~1,500 rows every day (`upsert_episode` runs before the "already have it" check: it re-stamps `scraped_at` and rewrites `raw_content`).

The new importer does one feed download per show and one query for that show's ids already held. It then touches only:
- items it doesn't hold, published within the last 30 days, and
- held episodes from the last 14 days that still lack a transcript.

Rows it already holds get no write. A `--backfill` flag scans the whole feed for onboarding or repair. The feed's ETag can make an unchanged feed nearly free, but that's optional: 8.6 MB a day is nothing to a runner.

Patching the Taddy importer's paging is not worth doing: it gets deleted in phase 4. It would only matter as a bridge on Taddy's free tier, and that tier has no generated transcripts, so there is no such bridge.

## 6. Phases

*Build model: Opus 5.5. This is a multi-PR build (about 4 PRs plus a data move), so the global rule applies: propose `hg-durable-build` and wait for Kevin's okay (8g). Before starting, check `origin/gabby/fix-pipe-list-maker-2*` and `origin/harness-2027`. They touch `alert_guides.py`, `pipeline/README.md` and AGENTS.md; coordinate the doc edits rather than collide.*

### Phase 0: bank what only Taddy has (2026-11-24, before any change)
1. One `getLatestPodcastEpisodes` call for TAL (page 1, 50 items) while the key works. It gives uuid → guid for the 28 TAL rows held under Taddy ids. Saved as a small JSON input for the phase 3 move (episode guids and Taddy ids are public data, so the public repo is fine).
2. A read-only snapshot of counts as the baseline: transcripts per show, total characters, and the 1,837 guids. Nothing else needs saving: every transcript Taddy ever gave us is already in Neon (counts in section 1), and every past episode of every show has one.
3. Optional, low value: about 70 credits remain (~40 by 12-07). The 12 saved episodes with only a clip or show notes could be retried against Taddy. They were likely tried already, and `save_episode.main` refuses to start without the closed OpenAI account's key. I'd skip it.

**Check:** the map covers all 28 rows; the baseline file exists.

### Phase 1: build it, switched off (11-24 to 11-26; PRs the builder may merge itself after an independent review and green CI)
1. `show_config`: `feed_url`, `itunes_id`, `transcript_source`, `require_transcript`, identity `rss_guid` for every podcast. One shared feed parser and identity function (today's `import_gabfest.episode_url` moves to one home). Drift tests updated (`tests/test_show_config.py`).
2. `scrapers/rss/import_feed.py`: the importer in section 2, rules 1 to 5, with hermetic tests.
3. `scrapers/transcripts/`: `aidailybrief.py`, `npr.py`, `stt.py` behind one contract (`fetch(episode) -> Transcript | NotYet | Failed`), with section 3's guards and tests on tiny made-up fixtures.
4. `transcribe_pending.py` and the new `entities.yml` step, plus the read-only shadow mode. A switch (`TRANSCRIPTS_FROM=taddy|shadow|feeds`, default `taddy`) decides which path writes. Shadow writes nothing to Neon and puts only counts, titles, timings and match ratios in the step summary. A shadow problem is reported and never fails the daily run, so production can't be affected while Taddy is still the writer.

**Check:** full test suite green, workflow test passes with the new limits, and one shadow run on main reads every feed and finds every recent episode Taddy already imported.

### Phase 2: shadow alongside Taddy (11-26 to 12-01, at least 5 daily runs including Monday 11-30)
Each daily run reports, per show:
1. **Discovery agreement:** every episode Taddy imported that day is found in the feed with the same guid and UTC date, and nothing extra appears. The bar is 100%. Any miss is explained before cut-over.
2. **Transcript availability:** source found, hours since publishing, title match, words per minute. This is where AI Daily's real lag gets measured.
3. **Transcript agreement with Taddy's text for the same episode:** word overlap, plus a check that matters more for extraction. Of the entity names already extracted from that episode's Taddy transcript (`ai_mentions` → `ai_entities`, aliases included), what share appear in the new text? Bar: ≥ 90%. Deterministic, free, and no model call.
4. **Speech-to-text on the runner:** one Hard Fork episode (a new one if NYT publishes during Thanksgiving week, else the 2026-11-20 episode still in the feed). Record wall time and the same agreement numbers against Taddy. Decision rule:
   a. turbo transcribes an hour of audio in under about 35 minutes, with ≥ 90% entity recall: use turbo.
   b. too slow: try a distil or small English model.
   c. recall below 90% on any model: Hard Fork moves to the a local Mac or Particle, and that comes back to you as a choice.
5. **From the runner's network:** NPR and aidailybrief.ai answer the GitHub runner, not just this Mac. If either refuses, the fallback is a Firecrawl scrape on the existing key (1 credit a page, well inside the plan) or speech-to-text.

**Check:** five consecutive runs at full discovery agreement; AI Daily and PCHH transcripts found for every episode older than a day; one Hard Fork transcribed within the time limit at ≥ 90% recall.

### Phase 3: cut-over (about Wed 2026-12-02; needs Kevin's yes, 8b)
1. A Neon branch as the rollback point (`pre-off-taddy-2026-12-02`).
2. The id move, as a numbered, reviewed SQL file in `pipeline/scrapers/ai_daily/sql/`:
   a. `url := raw_content guid` for the 1,837 AI Daily, Hard Fork and PCHH rows;
   b. the phase 0 map for TAL's 28.
   It runs in one transaction, with before and after counts in the file, and a read-back that every row of those shows matches its feed guid. Notion is unaffected: the Transcripts DB doesn't store the episode URL (`sync_transcripts_notion.build_properties` sets URL only for blog posts), and the Tech and Media DBs store entity URLs, not episode ids.
3. Flip `TRANSCRIPTS_FROM=feeds`. Take Taddy out of `step_import`, `discover_tal_episodes`, `feed_check` and the three workflows. `save_episode.main` stops requiring the Taddy keys (it already degrades to clip or show notes when Taddy fails).
4. Alert words in `alert_guides.py` that name Taddy get rewritten.

**Check, every show, first live days (12-02, 12-03):** new episodes imported by the new path; transcripts from the expected source; extraction ran on transcripts, not show notes; Tech and Media DBs and the Transcripts DB (AI Daily, Hard Fork) got the new pages; `data_health` feed check PASS by guid for all five; transcript coverage PASS for AI Daily, PCHH and Hard Fork.

TAL's next scheduled run is Monday 12-07, after the cancel. So TAL is seen working before then in two ways:
- in shadow on Monday 11-30;
- in one hand-run of `pipeline.yml` for TAL right after the switch (part of yes 1). It re-finds the 11-30 episode by guid, adds no duplicate, and the feed check passes.

The 12-07 cron is the second sighting: a new row, songs found, the playlist updated.

**Note:** if the daily run is still red on issue #68's duplicate-title check (waiting on `sql/013` since 2026-11-06), judge by the checks above, not the run's colour. Better still, land `sql/013` first so green means green.

### Phase 4: cancel Taddy (Thu 12-04 or Fri 12-05; your click)
1. After two green live days, you cancel Pro on Taddy's billing page. The session finds the exact page and hands you the link. If Taddy keeps Pro running until the 12-07 period end (unchecked), we keep a short rollback window; after that, the fix goes forward.
2. Whether to keep the free account is your call. It costs nothing and nothing will use it.
3. Then: delete the two repo secrets (your yes); delete `scrapers/taddy/`, its tests and the Taddy branches in `feed_check`/`show_config`; update AGENTS.md (tech stack, abbreviations table, secrets list, AI Daily section), ARCHITECTURE.md, `pipeline/README.md`, DEVLOG, NOW, and BACKLOG (close the `taddy_query` retry item and the TAL-transcripts question).

**Check:** `grep -ri taddy` leaves only history (DEVLOG, archived plans) and the legacy-URL guard. The next billing date passes with no charge (watch the card on 12-07).

### Phase 5: the manual tools (after cancel; not on the deadline)
`save_episode.py` finds a saved episode through iTunes Search (`entity=podcastEpisode`, the existing show gate and `COMPANION_SHOWS` map reused against Apple's show name). It then takes an NPR transcript for any NPR show, or local speech-to-text on the episode's audio. `highlight_clips.transcribe` moves off the closed OpenAI account onto the same speech-to-text function, which uses mlx-whisper when it runs on Apple Silicon. This brings back Castro-clip transcription, broken since 2026-11-14.

## 7. Risks, and what I could not check

1. **Publisher pages from a GitHub runner:** tested only from this Mac. Phase 2 tests it; there are two fallbacks.
2. **AI Daily's transcript page is young.** The site's archive starts around 2026-06, and it could change shape or vanish. The title guard, the 3-day speech-to-text fallback and the coverage check make that loud, not silent.
3. **Hard Fork's short, lossy feed.** An episode NYT posts and pulls within a day would be missed, and no free source would show the gap. Taddy had the same exposure; it just polled more often.
4. **Speech-to-text speed and quality on 4 CPUs:** not measured. Phase 2 has the decision rule.
5. **GitHub's hosted-runner terms:** a judgement call, not a certainty. The fallback is a local Mac.
6. **Extraction drift from different transcript text** (timestamps, speaker labels, better or worse wording): normalised, then watched by the weekly eval.
7. **The public repo:** no transcript text in fixtures, logs, summaries or artifacts.
8. **Thanksgiving week** (11-26 to 11-27) may thin the shadow sample; extend phase 2 rather than cut over on fewer runs.
9. **Not checked:** the repo's visibility through GitHub's API (the `gh` call was declined; "public" comes from `docs/principles.md` and the workflows' comments); Taddy's cancellation terms; Particle's coverage (its terms aren't accepted); how long NPR takes to post a transcript (all 12 recent ones were there, the newest 3 days old); whether you want TAL transcripts.

## 8. Needs Kevin's yes

1. **Go on this plan** (shadow PRs merge after an independent review and green CI, per the repo's PR rule).
   a. Includes: transcripts come from AI Daily's and NPR's own pages, and Hard Fork's from speech-to-text on GitHub's free machines.
   b. Includes: the session may hand-run `pipeline.yml` for TAL once after the switch, and dispatch the shadow/probe runs, which write nothing.
2. **TAL transcripts:** stop (recommended; songs never used them), or keep them free from TAL's website.
3. **The one-time id change in Neon** (1,865 rows), after a Neon branch snapshot: production data. About 12-02.
4. **Cancel Taddy Pro:** your click, about 12-04, after two green days. Keep the free account or not.
5. **Delete `TADDY_USER_ID` and `TADDY_API_KEY` from the repo's secrets:** a GitHub deletion, after cancel.
6. **Only if phase 2 says so:** Hard Fork on a local Mac or Particle (your one-time terms click).
7. **`hg-durable-build`** for the build, per the global rule for multi-PR builds.

No paid per-use route and no new vendor account is in the recommended path.

---

## Evidence (2026-11-23)

Scripts and outputs: `claude-plans/2026-11-23-off-taddy-probes/` (committed 2026-11-24; the one saved web page was left out because it is the publisher's text).
- `probe_feeds.py` → `out/feeds.json`: feeds via Apple lookup, item counts, guid shapes, no `podcast:transcript` tags on any of the five.
- `volume.py`: last-30/60/90-day episodes and audio minutes per feed.
- `aidb_transcripts.py`: AI Daily transcript coverage, 39 of 39 in 45 days, words per minute 187 to 213 (two date-slip false matches, which is where guard 1 came from).
- `npr_transcripts.py`: PCHH, 12 of 12, 3,349 to 6,177 words, 182 to 214 words per minute.
- `neon_readonly.py`, `neon_identity.py`: read-only Neon counts, url schemes, guid coverage and collision check (`default_transaction_read_only=on`; nothing written).

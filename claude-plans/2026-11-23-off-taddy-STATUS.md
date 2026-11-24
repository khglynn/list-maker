# Off Taddy: status

*The live cursor for the move off Taddy. Plan: `2026-11-23-off-taddy-plan.md` (read its update box first). Newest first. Each line says whether a thing was **seen working**, is **built but not seen**, or is **waiting on Kevin**.*

**Clock:** Taddy Pro ($75/mo) next bills **2026-12-07**. Target: new path live and seen working by **12-04**, Kevin cancels by **12-05**.

## Owner from 2026-11-24 evening: Gabby
Kevin closed the off-Taddy session and handed list-maker to Gabby ("they're supposed to monitor and fix things on an ongoing basis now"). Her brief, with the Friday steps and the follow-up builds, is gabby#648 (`claude-plans/2026-11-24-list-maker-yours-now/` in the private gabby repo; the phase 5 design moved there too). The "Q4 budgeting and subs" session has closed, so the downgrade is Kevin's click on Gabby's word (the fallback below). She writes the next entries here.

## Latest (2026-11-24, 21:25 UTC / 3:25pm CT): cut over. The feed path is live and seen writing.
1. **The id move ran (Kevin's paste, about 21:11 UTC):** 1,868 rows moved (the rehearsal's 1,865 plus 3 that arrived since). After it: AI Daily 1,148, Hard Fork 227 and PCHH 465 on their feed guids, 0 Taddy ids; TAL's 28 on guids and 889 on page urls; `shows` row 65 is Machine Gods.
2. **PR #85 merged** (`36d27a7`, CI green on `7d1ab8a`).
3. **Seen working:** hand-dispatched entities run 45488726197 (21:12 UTC), green.
   a. Machine Gods: 11 inserted, 11 transcripts, the trailer skipped; 79 mentions extracted; Notion synced.
   b. AI Daily 1,148 and PCHH 300 feed items all held; nothing inserted, no duplicates (every show's urls unique).
   c. Health: `import_caught_up_to_feed` passes on guid identity; Hard Fork skipped as not collected. Two warnings, both known: SOP's transcript gap (follow-up PR) and the alias-split list.
4. **Rehearsal Neon branch deleted** (Kevin's yes). The snapshot `pre-off-taddy-014-2026-11-24` stays until the downgrade.
5. **Left for the teardown:** SOP's 2 rows still on Taddy ids (7852, a 07-14 Netflix promo; 7853, a 06-23 rerun). Nothing calls Taddy with them.

**Next:** Wed 11-25 and Thu 11-26 20:30 UTC runs green on the new path, then Fri 11-27: delete the two Taddy secrets and message the money session for the downgrade.

## Earlier (2026-11-24, 21:15 UTC / 3:15pm CT): the first shadow run from a GitHub runner is clean
**Seen working:** entities run 45481428202 (20:30 UTC, green; every step green, the shadow step included). Its "Shadow" step:
1. **Discovery 100% on every show:** AI Daily 1,147 of 1,147, PCHH 300 of 300 (by saved guid), TAL 16 of 16 (title and date). Nothing to insert on the shows the importer will write.
2. **Transcripts answer the runner's network:** AI Daily 6 of 6 older than a day (word overlap with Taddy 96–98%); PCHH 4 of 4 older than a day (81–85%), today's not posted 12.6 h in; Machine Gods 11 of 12, the missing one the September trailer.
3. **Entity misses:** none new. Every episode below 100% was already read by hand in the Mac run (Taddy mishearings and sponsor reads); 11-23, the one new AI Daily episode, is 100%.
4. **One timing note, not a problem:** AI Daily's 11-24 episode went into its feed at 20:12 UTC and wasn't in the copy the runner read at 20:37 (Anchor's cache); Taddy had it. On the new path an episode published that close to the 20:30 run lands the next day. It changes nothing in practice: AI Daily waits for its transcript, and the transcript is never up within the hour.
5. SOP's would-insert (today's episode) and its two one-day date shifts are the shadow's view only; SOP keeps its website scraper.

**Done after it (2026-11-24, 21:10 UTC):** the Neon snapshot branch `pre-off-taddy-014-2026-11-24` (`br-delicate-term-aeplpww9`, no compute, the rollback for the id move). PR #85 marked ready.

**Next:** Kevin's one-line paste of `apply_sql.py … 014`; Claude merges PR #85 right after it. Both must land before the next 20:30 UTC run (Wed 11-25, 2:30pm CT), with no daily run between them. Then a hand-dispatched entities run to see the new path write.

## Where it stood (2026-11-24, ~01:10 UTC / 2026-11-23 evening CT)

**Phase 0 (bank what only Taddy has): done, seen.**
- The 28 This American Life rows stored under a Taddy id are mapped to their feed guids: `2026-11-23-off-taddy-probes/out/tal-taddy-uuid-to-guid.json`. 28 of 28, all guids distinct, every title and date agrees with Taddy's. Taddy's "latest" list only covers the feed's 16 items, so the 15 older rows took one `getPodcastEpisode` lookup each (no transcript credits spent).
- Baseline before anything changes: `2026-11-23-off-taddy-probes/out/baseline-2026-11-23.json` (episodes, transcripts and characters per show, url schemes, a hash of the saved guids).

**Phase 1 (build it, switched off): built, reviewed, tests green (985) locally and in CI.**
- `pipeline/feeds.py`: the one RSS reader (UTC dates, guid identity). The Gabfest importer, the TAL page-link lookup and the feed check now all use it.
- `pipeline/scrapers/transcripts/`: AI Daily's markdown reader and NPR's transcript-page reader (PCHH, Machine Gods), one contract, with the title and length guards.
- `pipeline/scrapers/rss/import_feed.py`: the importer that replaces Taddy. Nothing scheduled calls it to write until the cut-over.
- `pipeline/shadow_feeds.py` + a read-only, can't-fail step in `entities.yml`.

**Seen working, read-only from this Mac against live feeds and Neon (2026-11-24 01:00 UTC):**
- Discovery agrees with Taddy on every episode of every feed: AI Daily 1,147 of 1,147, PCHH 300 of 300 (both by the guid Taddy saved), TAL 16 of 16 (by title and UTC date), Hard Fork 4 of 4. Nothing the new path would insert, nothing Taddy holds that the feeds lack, no date disagreements on the shows the importer will write.
- Transcripts: AI Daily 5 of 5 episodes older than a day found (today's not up 6.7 hours in, as expected); PCHH 4 of 4; Machine Gods 11 of 12 (the missing one is a September trailer).
- Agreement with Taddy's text: AI Daily word overlap 96 to 98%; PCHH 81 to 85% (NPR's text is human-edited, Taddy's was speech recognition). Of the entities already extracted from each episode, the new text names 75 to 100% exactly, 75 to 100% allowing a near spelling. Every miss read by hand was Taddy mishearing a name that the publisher spells right ("Being Human" for "Being Heumann", "Semaphore" for "Semafor", "Z-Wen" for "Qwen") or a sponsor read. **So the plan's flat "90% recall" bar would fail for the wrong reason**; the bar used instead is below.

**Not seen yet:** any of this from a GitHub runner (NPR and aidailybrief.ai answering the runner's network); five daily shadow runs; TAL on a Monday.

## Builder's calls (made, not waiting on anyone)
1. **No runtime switch.** The plan proposed `TRANSCRIPTS_FROM=taddy|shadow|feeds`. The cut-over is a PR instead (rollback is a revert), so there is no three-mode matrix to test or forget to flip. Shadow is its own read-only step.
2. **Hard Fork is out of the shadow** (it stops being collected at the cut-over). Machine Gods is shadow-only until its `shows` row exists, so `SHOWS` never names a show Neon lacks.
3. **Title match tolerates a one-day shift** (logged when it fires), on top of the plan's three match rules. It is the guard on the trap that doubled TAL 886/887.
4. **Transcript agreement bar:** discovery must be 100% and every AI Daily / PCHH / Machine Gods episode older than a day must have its transcript found. Entity agreement is reported, and every entity the new text misses is read: cut over if the misses are spelling corrections or sponsor reads, not missing content.
5. **Gabfest's feed moved to Acast** (Slate, by 2026-11; the old Megaphone url redirects). `show_config.feed_url` names the new one; the show ended in July, so this changes no data.

## In flight
- **PR #84** (phase 0 + 1 + shadow): **merged 2026-11-24** after three review rounds (four Claude lenses + Codex, then two Codex passes on the fixes). Round 1: 10 real findings, all fixed. Rounds 2 and 3 kept finding the same kind of hole, AI Daily accepting another episode's page, so the mechanism was cut: the reader now asks only the episode's own Eastern day and matches the page by where its last timestamp lands against the audio length, plus a title floor. Live check: 57 of 61 recent episodes found, the same-day promo refused. Remaining known trade (written in `aidailybrief.same_episode`): a retitled page that also ends early is refused rather than risk a wrong transcript.
- **Cut-over branch `off-taddy/2-cutover`** (part 2): built and banked, not for merge until the shadow window ends. The id move (`sql/014`) was rehearsed on a throwaway Neon branch (`rehearsal-off-taddy-014-2026-11-24`): 1,865 rows moved, a re-run moves 0; after it, the new importer and the feed check see every show caught up.

## PR #85 review (2026-11-24)
Codex found 3 medium issues and nothing high. It checked the id move SQL: preconditions, collisions, idempotence, and all 28 TAL mappings against the banked map. It also checked the workflow secrets and that no scheduled path still calls Taddy. All three issues are fixed in `pipeline: the cut-over review's three fixes`:
1. Machine Gods' start date would have dropped its first week.
2. The pulse would have called the stopped Hard Fork BEHIND forever.
3. Weekend recaps without transcripts would have read as a transcript lag.

CI is green on the fixes, which are small and tested, so they had no fourth review round.

## The shortened path (2026-11-24, after Kevin asked for a faster cut-over and for Claude to do the Taddy downgrade)
1. **Tue 11-24, 20:30 UTC:** the first shadow run from a GitHub runner, the one thing not yet seen (NPR and aidailybrief.ai answering the runner). PR #84 merged 2026-11-24.
2. **Between Tue 11-24 evening and Wed 11-25 2:30pm CT (the run was clean):**
   a. Claude makes a Neon snapshot branch. **Done 11-24 21:10 UTC.**
   b. Kevin pastes `apply_sql.py … 014` (one line). **Done 11-24, about 21:11 UTC.**
   c. Claude merges PR #85 right after and dispatches the entities run by hand, to see the new path write. **Done 11-24 21:12 UTC; the run was green.**
3. **Wed 11-25 and Thu 11-26, 20:30 UTC:** two green daily runs on the new path. That is the alert rule's own "verified".
4. **Fri 11-27:**
   a. Claude deletes the two Taddy secrets.
   b. The money session ("Q4 budgeting and subs") clicks "Downgrade to the free plan" on Taddy's dashboard. That leaves 10 days before the 12-07 bill.
   b2. **If that session is closed by then:** put "Taddy downgrade: go" at the top of this file, and give Kevin the click in chat: https://taddy.org/login, sign in with his personal (trimm.co) login, then "Downgrade to the free plan". The money session also put a Friday 11-27 reminder on his calendar as a backstop. If the dates move, send that session a one-line message.
   c. After PR #85 nothing scheduled calls Taddy; only comments name it. So the downgrade is safe whether it takes effect at once or on 12-07. Its timing isn't documented anywhere found.
5. **What the shorter path gives up:**
   a. The planned five shadow runs and the Monday TAL run. Discovery and transcripts were already seen agreeing live from the Mac on every show, and TAL's Monday import isn't exercised by the shadow anyway.
   b. Rollback to Taddy after 11-27. A revert of PR #85 would have nothing to fall back on, so a problem after that is fixed forward. The snapshot branch keeps the id move recoverable.

## Complete coverage, not stops (Kevin, 2026-11-24: "I'm asking for complete work please")
1. **This American Life transcripts are kept,** not stopped.
   a. Its site has every episode's transcript back to #1. The new reader is `scrapers/transcripts/tal.py`, and the importer fetches new episodes' transcripts.
   b. The archive is about 875 episodes; Taddy only ever gave us 42. `scrapers/rss/backfill_transcripts.py --shows tal` fills it.
   c. **Seen working, 2026-11-24:** after Codex's review and its two fixes, the backfill ran against production and added 867 of 875. TAL now has 909 of 917 episodes with a transcript, and the database grew from 177 to 229 MB.
   d. The 8 without one are the site's own gaps: 454, the retracted Mike Daisey episode (403); 460 and 497, which have no page; 511, which redirects to 510 and was correctly refused; and 4 pages that answer HTTP 500 (57, 79, 86, 375).
   e. TAL's `min_coverage` is raised to 0.95.
2. **Machine Gods arrives whole on its first import** (11 episodes from 10-21). It needs no separate backfill step: `import_since` now means "everything since".
3. **Switched On Pop has a gap Taddy left.** Its one-off backfill stopped at 2026-08-04, and 180 episodes have no transcript (15 of them since August). SOP publishes no transcript of its own, so filling it takes speech-to-text.
   a. The plan is to run it inside the scheduled `pipeline.yml`, on GitHub's free runner for this public repo, with a CPU build of the same Parakeet model.
   b. The 180 are backfilled once, locally, with `parakeet-mlx` on the Mac.
   c. This is a follow-up PR. It doesn't block the Taddy downgrade, because nothing fetches SOP transcripts from Taddy today.
   d. Until it lands, the warning-level check keeps the gap visible.

## Waiting on Kevin
- **Kevin's yeses, given in chat on 2026-11-24** (the 11-24 shadow run was clean):
  1. **The id move:** done 11-24 (snapshot, then his paste).
  2. **Deleting the two Taddy secrets** on Fri 11-27, after two green daily runs.
  3. **Deleting the rehearsal Neon branch:** done 11-24.
- Nothing else waits on him. The Taddy downgrade is the money session's click, once Claude messages it.

## Phase 5 (the manual tools): designed, not built (2026-11-24)
A read-only design pass. The full note is kept privately at `~/.claude/plans/2026-11-23-off-taddy-phase5-design.md` and not in this repo, because it quotes saved-episode titles.
1. **This has to land with or before the teardown.** `pipeline/save_episode.py:49` imports `scrapers.taddy` at module level, so deleting that folder breaks its tests and CI. Both manual tools are already broken: clip transcription used OpenAI's Whisper, and that account closed 2026-11-14.
2. **Finding an episode:**
   a. Apple's free iTunes Search (`entity=podcastEpisode`) gives the feed guid, the audio url and the duration. The guid matched the live feed on 15 of 15 episodes across 5 shows.
   b. NPR shows get their transcript from the existing NPR reader, through the story id in Apple's audio url.
3. **Speech-to-text for everything else:**
   a. The engine is local `parakeet-mlx` (tdt-0.6b-v2), not `mlx-whisper`. On this M2 Pro a 37-minute episode took 49 s against 92 s, and its published error rate is lower (4.70 against 6.36). `mlx-whisper` turbo stays the fallback.
   b. It installs from a separate `pipeline/requirements-stt.txt`, never in a workflow.
   c. The model, about 2.5 GB, is already cached in `~/.cache/huggingface`.
4. **Traps for the builder:**
   a. The readers' `Transcript` class is imported under a different module path than `save_episode`'s, so an `isinstance` check silently fails.
   b. An upgraded transcript probably never reaches Notion, because the sync adopts the old page by Episode ID. This came from reading the code and hasn't been run.
   c. One saved row (4801) holds another show's episode.
   d. 19 transcript rows lack `model` and `is_generated`.
5. **Dead code to delete at the teardown:** `scrapers/ai_daily/transcripts.py` and `run_guarded_backfill.py`. Nothing imports either; only the READMEs mention them.

## Carried into part 2 (the cut-over PR), from the review
1. **The feed check must use the importer's matcher** (`import_feed.HeldIndex.match`). Otherwise the importer can call an episode held (punctuation, a one-day shift) while the check calls it missing, and nothing would ever clear the alarm.
2. **Episodes that will never have a transcript** (trailers, NPR announcements, an AI Daily promo): skip `episodeType=trailer` items, and let the transcript-coverage check stop expecting a transcript for them. Otherwise one trailer is a permanent red check.
3. **Machine Gods' first run would import its last 30 days on its own.** Give a new show an explicit start date so the October backfill stays Kevin's call.
4. **Pass the episode's real web link to extraction**, not the guid: the extractor sometimes copies the episode url into a mention's source url (112 Taddy urls and 158 Spotify urls in `ai_mentions.source_url` today).
5. **Hard Fork stops being collected:** a "not collected since" state so its staleness, feed and transcript checks don't fail forever.
6. Set aside (taste or rare): conditional GET for AI Daily's 8.6 MB feed; a guid-less feed collapsing items onto one shared link (none of the feeds we read can reach it).

## Overlaps checked before branching (2026-11-23)
- `gabby/fix-pipe-list-maker-2` and `-2-workflows`: merged (#81, #82) and deleted; nothing to coordinate.
- `harness-2027`: a docs-only tidy of `AGENTS.md`, 2 commits, no PR, last touched 2026-11-13. It touches no pipeline code. The Taddy lines in `AGENTS.md` change at the teardown, so that edit waits for it or rebases onto it.
- Issue #68 is closed (`sql/013` ran; recovered 2026-11-13) and the daily run has been green 8 days straight, so "two green days" will mean what it says.

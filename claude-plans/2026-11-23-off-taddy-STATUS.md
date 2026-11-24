# Off Taddy: status

*The live cursor for the move off Taddy. Plan: `2026-11-23-off-taddy-plan.md` (read its update box first). Newest first. Each line says whether a thing was **seen working**, is **built but not seen**, or is **waiting on Kevin**.*

**Clock:** Taddy Pro ($75/mo) next bills **2026-12-07**. Target: new path live and seen working by **12-04**, Kevin cancels by **12-05**.

## Where it stands (2026-11-24, ~01:10 UTC / 2026-11-23 evening CT)

**Phase 0 (bank what only Taddy has): done, seen.**
- The 28 This American Life rows stored under a Taddy id are mapped to their feed guids: `2026-11-23-off-taddy-probes/out/tal-taddy-uuid-to-guid.json`. 28 of 28, all guids distinct, every title and date agrees with Taddy's. Taddy's "latest" list only covers the feed's 16 items, so the 15 older rows took one `getPodcastEpisode` lookup each (no transcript credits spent).
- Baseline before anything changes: `2026-11-23-off-taddy-probes/out/baseline-2026-11-23.json` (episodes, transcripts and characters per show, url schemes, a hash of the saved guids).

**Phase 1 (build it, switched off): built, tests green locally, PR not yet open.**
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
- **PR #84** (phase 0 + 1 + shadow): open; reviewed by four Claude lenses + Codex, every finding checked by a refuter. 12 checked findings, 10 real; all fixed in `21b5abe` / `f4db164` except those carried to part 2 below. A second Codex pass on the fixes is running; then green CI and merge. The next daily 20:30 UTC run after merge is the first shadow from a GitHub runner.

## Carried into part 2 (the cut-over PR), from the review
1. **The feed check must use the importer's matcher** (`import_feed.HeldIndex.match`). Otherwise the importer can call an episode held (punctuation, a one-day shift) while the check calls it missing, and nothing would ever clear the alarm.
2. **Episodes that will never have a transcript** (trailers, NPR announcements, an AI Daily promo): skip `episodeType=trailer` items, and let the transcript-coverage check stop expecting a transcript for them. Otherwise one trailer is a permanent red check.
3. **Machine Gods' first run would import its last 30 days on its own.** Give a new show an explicit start date so the October backfill stays Kevin's call.
4. **Pass the episode's real web link to extraction**, not the guid: the extractor sometimes copies the episode url into a mention's source url (112 Taddy urls and 158 Spotify urls in `ai_mentions.source_url` today).
5. **Hard Fork stops being collected:** a "not collected since" state so its staleness, feed and transcript checks don't fail forever.
6. Set aside (taste or rare): conditional GET for AI Daily's 8.6 MB feed; a guid-less feed collapsing items onto one shared link (none of the feeds we read can reach it).

## Waiting on Kevin
Nothing yet. The yes-items come with evidence when they're ready: the one-time episode-id change in Neon, stopping TAL transcripts, the Machine Gods backfill, deleting the two Taddy secrets, and the cancel click.

## Overlaps checked before branching (2026-11-23)
- `gabby/fix-pipe-list-maker-2` and `-2-workflows`: merged (#81, #82) and deleted; nothing to coordinate.
- `harness-2027`: a docs-only tidy of `AGENTS.md`, 2 commits, no PR, last touched 2026-11-13. It touches no pipeline code. The Taddy lines in `AGENTS.md` change at the teardown, so that edit waits for it or rebases onto it.
- Issue #68 is closed (`sql/013` ran; recovered 2026-11-13) and the daily run has been green 8 days straight, so "two green days" will mean what it says.

# Phase 5 build plan — cover the seams that carry money and music

**Written:** 2026-09-04, by a five-reader Sonnet map + Opus synthesis against the live code (readers: `spotify-match`, `sync-playlist`, `scrape-to-sync`, `http-paths`, `harness` — their full notes are the other files in this folder). **Parent:** `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → Phase 5. **Status (2026-09-04 14:00 CT): built.** Nine PRs merged into `arc/phase-5` after review (#46 #47 #48 #49 #50 #51 #52 #53 #54; 550 → 803 tests); one PR to `main` open. Corrections found while building: the TAL drought starts January 2026, not May; the parser is fine; the importer's dedup UPDATE (`import_transcripts.py:364`) stamps `scraped_at` on rows it re-sees; § (d) 2's "row 3021 has songs" is wrong (0). Three PRs were added on the day: #52 (the songs alarm + loud sync) and #53 (the show gate) on Kevin's "both", #54 (Taddy's eight-word cap) from #53's builder's finding.

**Everything below was checked against the code on `main` at `61dc3b9` (2026-09-04)**, and the headline finding was additionally confirmed with read-only SQL against Neon the same day. Where a reader's claim and the code disagreed, the code won and the difference is in section (c).

---

## (a) What the readers found that changes the spec

**1. The music seam is not just untested — for TAL it has been silently broken since spring.** No TAL episode published after **2026-05-10** has a single song row. Not one. The Monday cron has reported success every week anyway, because "found no work" is not an error.

The chain, all four links verified:

| # | What happens | Evidence |
|---|---|---|
| 1 | TAL discovery (added 2026-08-02) shells out to the Taddy importer, which writes `episodes.url` = `https://api.taddy.org/podcast-episode/<uuid>` — an identity key, not the real thisamericanlife.org page | `run_pipeline.py:78-121`, `show_config.py:251-263`, `import_transcripts.py:275-291` |
| 2 | That same importer stamps `scraped_at = NOW()` on the row it just created — on both the INSERT and the ON CONFLICT branch | `import_transcripts.py:364`, `:397` |
| 3 | The TAL website scraper's queue is `WHERE show_id = 2 AND scraped_at IS NULL AND url IS NOT NULL`, so a freshly discovered row is excluded the instant it exists | `scrapers/tal/fetch.py:61-77` |
| 4 | Even if it were queued, it would send Firecrawl at `api.taddy.org/podcast-episode/<uuid>`, which has no `## Song:` sections, so the parse would find nothing | `scrapers/tal/fetch.py:99-115`, `scrapers/tal/parse.py:25` |

Live confirmation (read-only, 2026-09-04): the newest TAL episode holding any songs is **2026-05-10**; **11** TAL episodes published since 2026-06-01 hold zero songs; **15** TAL rows carry a Taddy API url and **all 15** have zero songs; **zero** TAL rows have `scraped_at IS NULL`, so the queue at `fetch.py:66` is permanently empty and every Monday run is a guaranteed no-op.

The August-2026 discovery fix was written to close exactly this class of silent gap, and it reopened it one layer down. That is a Phase 5 problem by definition: the money-and-music seam is what Phase 5 exists to cover, and testing the matcher while its feeder is dead would be the half-measure. **PR 1 is a fix, not a test.**

**2. TAL's "have we read this page yet?" state lives in two incompatible places, neither of which is right.** `episodes.scraped_at` (written only by the Taddy importer, never by the website scrape — `grep -rn scraped_at pipeline/scrapers/tal/` returns exactly one line, the filter itself) and a **git-ignored local JSON cache** at `pipeline/scrapers/tal/fetched/tal/` that `scrape.py:78-83` treats as the "already fetched" set. That directory does not exist on this machine and is not in git, so in CI it is always empty. TAL's freshness has no durable record anywhere.

**3. The parent plan's Phase 5 targets have moved.** `build_pull_queue.py` no longer exists (deleted in `40a07b2`); `feed_check.py`'s HTTP paths are now fully covered (`tests/test_feed_check.py`, 270 lines, from PR #42 plus `964f624`). What is actually at zero coverage is narrower and different: `spotify_match.py`'s own functions, `sync_playlist.py`'s live surface, the SOP/TAL parsers, and `save_item.py` / `save_episode.py`.

**4. Two silent-failure behaviours in the playlist writer that no test pins today.** `get_playlist_tracks` (`sync_playlist.py:106-108`) catches a `SpotifyException` mid-pagination and `break`s, returning a **truncated** set — the caller's diff at `:287` then treats already-present tracks as new and re-adds them (Spotify permits duplicate playlist entries; nothing here dedupes or removes). And `add_tracks_to_playlist` (`:113-141`) drops a whole 100-track batch on any non-429 error, or on 429s that outlast `MAX_RETRIES`, **without raising** — `sync_show` reports the short count as success and the process exits 0. Both are current behaviour to *pin*, then discuss — not to fix quietly inside a test PR.

**5. Decision 7 is already done.** `pipeline/venv/bin/python` is **3.12.12**, CI pins 3.12 (`test.yml`), and the suite is **550 tests in 0.84s**, hermetic. Phase 5 needs no venv work — just don't slow the suite down.

---

## (b) The PR split — six PRs, one owner per file

The constraint that shapes this: `tests/spotify_fakes.py` is shared by two PRs, and the `tal/` tree is touched by two. Resolution — **one owner per file, and the only ordering constraint is PR 2 before PR 3.** Everything else runs in parallel.

| PR | Branch | Size | Owns (nobody else touches) |
|---|---|---|---|
| 1 | `fix/tal-website-scrape-unblocked` | M | `scrapers/tal/fetch.py`, `scrapers/tal/scrape.py`, `show_config.py`, `tests/test_tal_fetch.py` |
| 2 | `test/spotify-match-decisions` | M | `tests/spotify_fakes.py`, `tests/test_spotify_match.py` |
| 3 | `test/playlist-sync-diff-and-dedup` | M | `tests/test_sync_playlist.py`, `pipeline/sync_playlist.py` (one comment) |
| 4 | `test/song-parsers-frozen-fixtures` | S | `tests/test_sop_scrape.py`, `tests/test_tal_parse.py`, `tests/fixtures/music/` |
| 5 | `test/saved-items-and-episodes` | M | `tests/test_save_item.py`, `tests/test_save_episode.py` |
| 6 | `chore/one-spotify-matcher` | XS | `scrapers/tal/fill_songs.py`, `scrapers/tal/scoring_match.py`, `tests/test_common.py` |

Every test PR is **tests-only against source it does not modify.** If a test cannot be written without changing production code, that is a finding for the PR description and a follow-up — not a quiet edit. The one exception is PR 3's single comment correction, which is a factual error in a comment (see corrections #3).

---

### PR 1 — `fix/tal-website-scrape-unblocked` — "a TAL episode published Sunday has its songs by Monday night"

**Goal.** Every TAL episode the Taddy discovery finds also gets its real thisamericanlife.org page read for songs. After this PR the 15-episode backlog drains on the next Monday run and stays drained.

**Why.** `docs/principles.md`: *"A script that runs is not an operation. An operation has visible failure states, can distinguish 'nothing to do' from 'didn't check,' and leaves evidence."* Today TAL's scrape cannot tell those apart — an empty queue and a broken queue look identical, and it has been the broken one since 2026-08-02.

**Design.**

*Do not touch `episodes.url`.* TAL's feed check uses `episode_identity="taddy_uuid"` (`show_config.py:117`), so the Taddy url **is** the identity Phase 4 built on. Rewriting it would break the feed check. The page URL is derived at scrape time and never stored (no DDL, no new column — `sql/` migrations are Kevin's paste).

*Where the page URL comes from.* TAL's RSS (`show_config.py` `fallback_website_url`, `https://www.thisamericanlife.org/podcast/rss.xml`) carries the canonical page URL in each item's `<link>` — verified 2026-09-04: item 1 is `<title>896: I Know What You Need</title>` with `<link>https://www.thisamericanlife.org/896/i-know-what-you-need</link>`, matching the DB row's title exactly. Parse it with `scrapers/gabfest/import_gabfest.parse_feed` (already hermetically tested by `tests/test_import_gabfest.py`) — reuse, do not re-implement, same call Phase 4's `rss_recent_episodes` made.

*The fallback, because the RSS is a rolling window of exactly 15 items* (counted 2026-09-04 — the backlog is 15, so today it fits, but a three-month outage would not): derive the slug from the title. `"896: I Know What You Need"` → `/896/i-know-what-you-need`; `"895: Label Maker!"` → `/895/label-maker` (both verified live: the slug URL returns 200, and the number-only URL `thisamericanlife.org/896` returns **404**, so number-only is not an option). Put this in `show_config.py` as a pure `tal_episode_page_url(title) -> str | None` next to `taddy_episode_url`, so it is unit-testable with no network.

*The queue predicate.* Replace `scraped_at IS NULL` in `get_unscraped_episodes` (`fetch.py:66-71`). `scraped_at` now means "Taddy saw it", which is not the question being asked. The question is "have we read this episode's page for songs yet", and the honest answer in today's schema is **has it got songs**:

```sql
WHERE e.show_id = 2
  AND NOT EXISTS (SELECT 1 FROM songs s WHERE s.episode_id = e.id)
  AND e.publish_date >= %s          -- a floor; do not re-scrape the archive
```
A floor is required: **213** TAL rows have zero songs and most are legitimate archive episodes with no music credits. Default the floor to the discovery era (`2026-06-01`) and make it a parameter, so a deliberate backfill is one flag rather than an accident. Rename the function to say what it now means (`get_episodes_missing_songs`) and keep a thin alias only if `scrape.py` needs it in the same PR — it does not, the same PR owns both files.

*The local-JSON cache.* `scrape.py:78-83` skips episodes whose `<db_id>.json` exists in a git-ignored directory that is empty in CI. Leave the cache as a local convenience but stop treating it as authority: the DB predicate is now the queue, and the cache only avoids a repeat Firecrawl call within one machine's working set. Say that in a comment where the skip happens.

**Functions and lines.**

| Function | File:line | Change |
|---|---|---|
| `get_unscraped_episodes` | `scrapers/tal/fetch.py:61-77` | new predicate + date floor + rename |
| `fetch_episode` | `scrapers/tal/fetch.py:99-140` | takes the derived page URL, not `row["url"]` |
| `main` (fetch) | `scrapers/tal/fetch.py` | passes the URL map through |
| `scrape_new_episodes` | `scrapers/tal/scrape.py:61-140` | build the title → page-URL map once (RSS first, slug fallback), log how many resolved and how many did not |
| `tal_episode_page_url` | `show_config.py` (new, beside `:254`) | pure |

**The fake boundary.** Two, both already idiomatic in this suite. **DB:** `fetch.py` and `scrape.py` call the module-level `get_db_connection()` (`fetch.py:47-58`, `scrape.py:47-58`) which delegates to `common.get_db_connection` — monkeypatch the module attribute (`monkeypatch.setattr(fetch, "get_db_connection", lambda: fake_conn)`) and use a `_FakeCursor`/`_FakeConn` in the `tests/test_load_entity_batch.py:32-56` shape, with `fetchall()` returning **dict-like** rows (this path uses the default RealDictCursor). **HTTP:** don't fake it — test `tal_episode_page_url` as a pure function, and test the RSS mapping by handing `import_gabfest.parse_feed` a frozen XML fixture (three items, one with a punctuation-heavy title), exactly the pattern `tests/test_import_gabfest.py` uses.

**Tests to write** (`tests/test_tal_fetch.py`):
- `test_taddy_discovered_episode_is_queued_for_the_website_scrape` — a row with a `api.taddy.org` url, `scraped_at` set, no songs, published after the floor **is** returned. *This test fails on today's code — it is the regression pin for the whole finding.*
- `test_archive_episode_without_songs_is_not_queued` — same shape, `publish_date` 2011, not returned (the 213-row guard).
- `test_episode_with_songs_is_not_requeued`
- `test_page_url_comes_from_the_feed_link_when_the_title_matches`
- `test_page_url_falls_back_to_the_slug_when_the_feed_has_rolled_over` — `"896: I Know What You Need"` → `.../896/i-know-what-you-need`; `"895: Label Maker!"` → `.../895/label-maker`
- `test_page_url_is_none_for_an_untitled_or_unnumbered_episode` — and the scrape logs it rather than fetching a wrong URL (there is one such live row: id 7422, `episode_number` NULL, "Ira (Reluctantly) Gives a Graduation Speech")
- `test_scrape_never_sends_firecrawl_at_a_taddy_url` — assert on the URL handed to the fetcher; this is the second half of the bug and deserves its own pin

**Do not touch.** `episodes.url` for any row. `show_config.py`'s `episode_identity`/`taddy_uuid` values, `TADDY_EPISODE_URL_PREFIX`, or `taddy_episode_url` (Phase 4 owns those; `tests/test_feed_check.py` pins them). `import_transcripts.py` — the importer stamping `scraped_at` is not wrong for the shows it was written for; **this PR changes the reader, not the writer.** `fill_songs.py` (PR 6 owns it). The duplicate TAL rows (see (d)).

**Needs Kevin.** Nothing to run. One thing to know: the first Monday after this merges, TAL Firecrawls ~15 pages in one run — normal cost, no approval needed, but it is why the run will take minutes instead of seconds.

---

### PR 2 — `test/spotify-match-decisions` — "a wrong song cannot become HIGH by accident"

**Goal.** `tests/` imports `pipeline.spotify_match` and exercises its own seven functions, with the confidence thresholds and the NOT_FOUND write pinned.

**Why.** `calculate_match_confidence` and `get_confidence_category` are the entire correctness gate between a Firecrawl'd string and a track landing in a live playlist — 4,586 SOP and 837 TAL tracks are eligible for the playlists today on the strength of that one number. And `save_results`' NOT_FOUND write is terminal: `fetch_unmatched_songs` only ever selects `WHERE s.spotify_track_id IS NULL AND s.spotify_match_confidence IS NULL` (`:161-162`), so a row written `NOT_FOUND` is excluded from every future run forever. Nothing in the pipeline ever clears it. Live: 403 SOP and 212 TAL rows sit in that state.

**Functions and lines.**

| Function | Line | Pure | What the test pins |
|---|---|---|---|
| `calculate_match_confidence` | `:97-124` | yes | 55/45 title/artist blend of `fuzz.ratio`, both sides lower-cased, rounded to 3dp; `max()` over multiple artists, not first or mean; empty artist list → artist score 0, no crash |
| `get_confidence_category` | `:127-134` | yes | `0.90` → HIGH, `0.899` → MEDIUM, `0.70` → MEDIUM, `0.699` → LOW (both comparisons are `>=`) |
| `search_with_retry` | `:71-91` | no | 429 sleeps `int(Retry-After) + 1` and retries; a non-429 `SpotifyException` returns `None` with **no** retry (assert one call); a generic exception retries to `MAX_RETRIES=3` then returns `None` |
| `search_and_score` | `:225-258` | no | best-of-N uses strict `>` so a tie keeps Spotify's own ranking; empty `items` → `None` |
| `fetch_unmatched_songs` | `:155-179` | no | `show_id=None` omits the `AND e.show_id = %s` clause **and** its param; params are exactly `[show_id, limit]` or `[limit]`, in that order |
| `save_results` | `:182-221` | no | matched rows write eight columns keyed on `id`; not-found rows write only `spotify_match_confidence = 'NOT_FOUND'`; one `conn.commit()` after the whole loop, including when both lists are empty |
| `match_songs_batch` | `:261-305` | no | empty title skips the search entirely; **any exception inside `search_and_score` lands the song in `not_found`** (`:296-298`) |

**The fake boundary.** Build one `FakeSpotify` in a new `tests/spotify_fakes.py` exposing exactly the four methods this repo calls — `search(q=, type=, limit=)`, `playlist_tracks(playlist_id, offset=, limit=)`, `playlist_add_items(playlist_id, uris)`, `playlist_change_details(playlist_id, description=)` — recording calls, returning canned payloads, and able to raise a **real** `spotipy.exceptions.SpotifyException(http_status, code, msg, reason=None, headers=None)` on a configurable call number (a pure import, already a runtime dependency, no network). PR 3 imports the same class. Search results use the real shape: `{"tracks": {"items": [{"id", "name", "artists": [{"name"}], "album": {"name"}, "popularity"}]}}`.

DB: `spotify_match.get_db_connection` (`:140-152`) passes `cursor_factory=None` because this module indexes the COUNT row positionally (`:412`), while `fetch_unmatched_songs` asks for `RealDictCursor` explicitly at `:177`. **The fake `cursor()` must accept and ignore a `cursor_factory=` kwarg** — the `test_load_entity_batch.py` fake does not, and a fake that only accepts `cursor()` will blow up here. `fetch_unmatched_songs` and `save_results` take `conn` as a parameter, so pass the fake in directly; no monkeypatching needed for those two.

Monkeypatch `spotify_match.time.sleep` in every retry/batch test — `API_DELAY` is 0.3s per song and the suite finishes in under a second today.

**Tests to write** (`tests/test_spotify_match.py`): `test_confidence_is_fifty_five_forty_five_title_artist`, `test_confidence_uses_the_best_matching_artist`, `test_confidence_is_case_insensitive`, `test_confidence_survives_a_track_with_no_artists`, `test_category_boundaries_are_inclusive`, `test_rate_limit_sleeps_and_retries`, `test_a_non_rate_limit_spotify_error_is_not_retried`, `test_network_errors_retry_then_give_up`, `test_best_of_three_picks_the_highest_and_keeps_spotify_order_on_a_tie`, `test_no_results_returns_none`, `test_unmatched_query_omits_the_show_filter_when_show_id_is_none`, `test_unmatched_query_params_are_show_then_limit`, `test_saved_match_writes_every_column_keyed_on_song_id`, `test_not_found_writes_only_the_confidence_and_commits_once`, `test_an_exception_mid_search_marks_the_song_not_found_forever` (the important one — name it so the behaviour is legible in the failure output), `test_empty_title_never_calls_spotify`.

**Do not touch.** `pipeline/spotify_match.py` — this PR changes no production code. `tests/test_common.py`'s coverage of `ensure_spotify_token`, `SPOTIFY_SCOPE`, and `get_db_connection`'s retry/timeout/`cursor_factory` behaviour (`:65-260`) — already fully tested; do not re-test it, and do not exercise `get_spotify_client` (real OAuth). `tests/test_sync_playlist.py` (PR 3 owns it).

**Needs Kevin.** Nothing.

---

### PR 3 — `test/playlist-sync-diff-and-dedup` — "the playlist never gets a track twice, and a half-written sync says so"

**Goal.** Extend `tests/test_sync_playlist.py` (do **not** create a second file — it exists and its own docstring already says "that is Phase 5's job") to cover the diff, the dedup, the batching, and the two silent-failure paths.

**Why.** The diff at `sync_playlist.py:287` is the only thing standing between a re-run and a duplicated 4,586-track playlist, and the two ways it can be fed a wrong answer (finding #4 in section (a)) are both untested. A playlist **wipe** is structurally impossible today — `grep -rn "playlist_remove\|playlist_replace" pipeline/` returns nothing; the only writes are `playlist_add_items` and `playlist_change_details` — and that invariant is worth a test so it stays true rather than being true by luck.

**Functions and lines.**

| Function | Line | What the test pins |
|---|---|---|
| `get_playlist_tracks` | `:85-110` | pages at 100 (offset 0, then 100); an item whose `track` or `track.id` is `None` is skipped, not a crash; **a `SpotifyException` on page 3 of 5 returns pages 1–2 with no exception raised** — pinned as today's behaviour, flagged in the PR body |
| `add_tracks_to_playlist` | `:113-141` | 250 ids → three calls sized 100/100/50, `added == 250`; a 429 retries the **same** batch after `Retry-After + 1`; **250 ids with a non-429 error on batch 2 → `added == 150`, no exception**; a permanent 429 stops at `MAX_RETRIES=3` rather than looping forever |
| `get_matched_track_ids` | `:158-175` | the SQL contains `spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')` **and** `spotify_track_id IS NOT NULL`; `show_id` is a bound param; the order is `ORDER BY spotify_track_id` (alphabetical by Spotify id, **not** episode order — pin it, because it is surprising) |
| `get_playlist_stats` | `:195-222` | the songs count uses the same confidence filter as `get_matched_track_ids` (drift here makes the public description lie); the episodes count filters on `scraped_at IS NOT NULL` only |
| `update_playlist_description` | `:224-247` | the exact string handed to `playlist_change_details`: `{songs:,}` comma grouping, the show's acronym, `MM/YY`. `datetime` is imported **inside** the function (`:226`) so it cannot be monkeypatched — compute the expected date the same way in the test. A `SpotifyException` here is swallowed with a warning and must not fail the sync |
| `sync_show` | `:250-311` | unknown `show_id` raises `ValueError` **before** any DB or Spotify call (today only reached through `main()`); empty `db_tracks` returns early and **never builds a Spotify client**; the diff adds only non-overlapping tracks (overlapping / contained / disjoint cases); `dry_run=True` calls neither `add_tracks_to_playlist` nor `update_playlist_description`; with **no** new tracks and `dry_run=False` it still updates the description (that asymmetry at `:291-296` is deliberate — pin it) |
| invariant | — | a fake `sp` that raises `AttributeError` on any `playlist_remove_*` / `playlist_replace_*` access is never touched: `test_the_sync_never_removes_or_replaces_tracks` |

**One-line source change.** The comment at `:335-338` says exit 2 means "the orchestrator does not retry it". True for `run_new_episodes.run_script`, but the live SOP/TAL path is `pipeline.yml → run_pipeline.py → run_sync()` (`run_pipeline.py:160-167`), which calls `sync_show` **in process**, catches the `ValueError` in a bare `except Exception` (`:238-245`) and exits 1 (`:392-393`). Correct the comment to name both callers. No behaviour change.

**Drift guard, cheap and worth it now.** `sync_playlist.SHOWS` (`:40-51`) duplicates `show_config.SHOWS["sop"/"tal"].spotify_playlist_id` and is a Phase 3 refactor target. Assert against `sync_playlist.SHOWS` (today's real seam) so the tests survive that refactor, and add `test_playlist_ids_match_show_config` comparing the two — it catches the drift the moment it happens instead of when a playlist stops updating.

**The fake boundary.** `tests/spotify_fakes.FakeSpotify` from PR 2 (this is the ordering constraint). `get_playlist_tracks`, `add_tracks_to_playlist` and `update_playlist_description` all take `sp` as a parameter — pass the fake straight in. For `sync_show`, monkeypatch `sync_playlist.get_spotify_client` to return it. DB functions call the module-level `sync_playlist.get_db_connection` (`:144-155`) with no arguments — monkeypatch that name, not `common`'s. Rows are dict-like (`row["spotify_track_id"]`, `row["songs"]`). Monkeypatch `sync_playlist.time.sleep`.

**Do not touch.** The two existing exit-code tests. `get_latest_episode` (`:177-192`) — dead, see (c) #6; do not write a test for it. `pipeline/sync_playlist.py` beyond the one comment — in particular, **do not** "fix" the truncation or the swallowed batch failure in this PR; both are Kevin-visible behaviour changes and belong in a follow-up with their own decision.

**Needs Kevin.** Nothing to run. One question to put in the PR body (not blocking the merge): should a partial playlist sync fail loudly? Today it exits 0 with a quiet undercount.

---

### PR 4 — `test/song-parsers-frozen-fixtures` — "the strings that become songs"

**Goal.** Pin the four pure parsers that decide what a song row even is. These are the cheapest high-value tests in the phase: no DB, no HTTP, no fakes — text in, dicts out.

**Why.** Every song in both playlists entered the system through one of these four functions. A regex that silently stops matching produces zero songs and a green run — the exact failure shape PR 1 is cleaning up on the TAL side.

| Function | File:line | What the test pins |
|---|---|---|
| `parse_songs_discussed` | `sop/scrape.py:184-228` | the dash format (`- Artist -- Title`); the quote-format fallback firing **only** when the dash pattern found nothing (not a union); the `Previous`/`Next` nav filter; the `(Album)` and leading-underscore filters on the quote branch; no section at all → `has_songs_section=False` |
| `parse_episode_list` | `sop/scrape.py:151-181` | the `# [Title](url)` match; the `MM/DD/YY` lookup in the 100 characters before the title; no date → `publish_date=None`; an unparseable date is swallowed to `None` |
| `parse_description_body` | `sop/scrape.py:231-256` | title header stripped; truncation at `**Songs Discussed**`; each of the three footer regexes, and a fixture missing one footer still coming back clean |
| `parse_episode` | `tal/parse.py:25-88` | 404 detection; episode number from the URL; title suffix strip and curly-quote normalisation; both song-line formats (`["Title" by Artist](url)` and plain `"Title" by Artist`); the `## Song:` splitter at 0, 1 and many sections |
| `clean_quotes` | `tal/parse.py:20-22` | all four curly code points → straight |

**The fake boundary.** None. Freeze real inputs as fixtures under `tests/fixtures/music/` (the `tests/fixtures/intake/` directory is the precedent): one normal SOP episode, one SOP episode with no Songs Discussed section, one SOP quote-format episode, one TAL episode JSON with several `## Song:` sections, one TAL 404. Keep them small — trim to the section under test and say in a header comment where and when each was captured.

**Do not touch.** `insert_episode` / `insert_songs` (`sop/scrape.py:70-128`) — DB-touching, lower value, and `scrape.py` is not owned by this PR beyond reading it. `scrape_url` / `fetch_episode` — HTTP; testing the parsers is what makes faking Firecrawl unnecessary.

**Needs Kevin.** Nothing.

---

### PR 5 — `test/saved-items-and-episodes` — "the manual door still works"

**Goal.** Cover `save_item.py` (197 lines) and `save_episode.py` (380 lines), the two remaining zero-coverage files in the parent plan's Phase 5 line. `save_item.py --url` is the manual ingest door the curated-intake arc deliberately kept.

**Why.** Both are on the scheduled path list (`tests/test_common.py:250`) and neither has a test file. `save_episode.upsert_oneoff` carries a "never downgrade a full transcript to an excerpt" rule that exists in only one of its two write paths.

| Function | File:line | What the test pins |
|---|---|---|
| `resolve_show` | `save_item.py:64-67` | known blog domain → slug, `www.` stripped, unknown → `saved-articles`, host case-insensitive |
| `domain_to_show` | `save_item.py:53-62` | only `medium == "blog"` shows with a `fallback_website_url`; no two shows collide on a host (drift guard, same spirit as `test_show_config.py`) |
| `is_pdf` | `save_item.py:69-71` | `.pdf` suffix any case, `.pdf?query`, a path merely containing `pdf` is **False** — this gates a total silent skip of ingest |
| `episode_has_mentions` | `save_item.py:96-100` | row → True, none → False, SQL scoped to the episode id |
| `save_url` | `save_item.py:111-151` | PDF path never opens a DB connection; `skip_extract=True` skips extraction; an already-extracted URL re-saves without erroring; `sync=False` returns before `sync_curated`; a failed extraction still syncs and still returns False |
| `taddy_find_episode` | `save_episode.py:64-88` | below `TADDY_TITLE_MIN_RATIO = 0.80` is never selected however good the show match; the `0.7 * title + 0.3 * show` score breaks ties **only among candidates that already cleared the title bar**; empty show name defaults the show ratio to 0.5 |
| `taddy_transcript_text` | `save_episode.py:90-94` | under `MIN_FULL_TRANSCRIPT_CHARS = 1000` → `None`; paragraphs joined with a blank line; the exact boundary |
| `try_taddy_full` | `save_episode.py:96-109` | a raising Taddy lookup returns `(None, None)` and never propagates |
| `parse_og` | `save_episode.py:111-115` | both attribute orderings; missing tag → `""`; whitespace stripped |
| `upsert_oneoff` | `save_episode.py:157-205` | no title match → INSERT path, `created=True`; existing row, new source is not `taddy_transcript` → **no UPDATE issued**; existing excerpt + `taddy_transcript` → upgraded; existing `taddy_transcript` + a clip → the guard holds; **and the second path** (title lookup misses, `url` conflicts) writes `episode_transcripts … ON CONFLICT (episode_id) DO UPDATE SET transcript_text = EXCLUDED.transcript_text, source_type = EXCLUDED.source_type` with no check on the existing `source_type` — pin that a full transcript can be overwritten by a stub there, and flag it |
| `page_id_for` | `save_episode.py:215-223` | row → page id, none → `None` |

**The fake boundary.** `episode_has_mentions`, `page_id_for` and `upsert_oneoff` all take `conn` as a parameter — pass a `_FakeCursor`/`_FakeConn` (dict-like rows, canned `fetchone`) straight in, no monkeypatching. Everything else is a module-level collaborator faked with `monkeypatch.setattr(save_item, "…", fake)` — the idiom `tests/test_run_new_episodes.py` already uses for `rne.get_db_connection`, `rne.subprocess.run`, `rne.time.sleep`. Nothing here needs a live token: `taddy_query`, `get_episode_transcript`, Firecrawl's `scrape_post`, `httpx.get`/`httpx.stream` and `subprocess.run` are all swappable at the import boundary, and `scrape_link_meta`'s raw-httpx fallback is reached deterministically by leaving `FIRECRAWL_API_KEY` unset (which is CI's default — `test.yml` provisions no secrets). Use `tmp_path` for `save_pdf`.

**Do not touch.** `save_episode.py` and `save_item.py` themselves. Extending the never-downgrade guard to the second write path is the right fix and it is **not** this PR — write the test that documents today's behaviour, and open the follow-up.

**Needs Kevin.** Nothing.

---

### PR 6 — `chore/one-spotify-matcher` — "one implementation, on the shared connection"

**Goal.** The repo stops containing a second, cruder, unwired Spotify matcher with the pre-2026-08-31 connection bug still in it.

**Why.** `docs/principles.md`: *delete dead code, don't disclaim it.* `scrapers/tal/scoring_match.py` writes to a separate `scoring_tracks` table, is referenced by nothing but its own README line and old DEVLOG entries (grepped across `.py`, `.yml`, `.md`), is deliberately absent from `SCHEDULED_PATH_MODULES`, opens its own `psycopg2.connect()` with no timeout, keepalives or retry (`:36-41`), and re-implements match confidence as substring containment. Leaving it is a trap for whoever runs it by hand next.

**Changes.**
1. `git rm pipeline/scrapers/tal/scoring_match.py` (history keeps it — this is a tracked source file, not unreproducible content), and drop its `pipeline/README.md:32` line.
2. `scrapers/tal/fill_songs.py:29-34` — replace the private `psycopg2.connect(os.getenv("DATABASE_URL"))` with the same lazy `common.get_db_connection` delegate its neighbours `scrape.py:47-58` and `fetch.py:47-58` already carry, comment and all. It is reachable only via `fill_songs.py`'s own `__main__` today, which is exactly the hand-run that would rediscover the 41-minute hang.
3. Add `pipeline/scrapers/tal/fill_songs.py` to `SCHEDULED_PATH_MODULES` (`tests/test_common.py:234-260`) so the grep guard keeps it honest.

**Do not touch.** The two `download_episode_art.py` scripts — unwired, documented manual tools (`marketing/` owns the artwork ones). *Amended 2026-09-04 while building:* `tal/repair_metadata.py` also carried its own untimed `psycopg2.connect()` (`:54`), which made acceptance 3's grep impossible to satisfy under the original do-not-touch line; the acceptance wins — it moves onto the shared `common.get_db_connection` delegate and into the guard list in this PR, nothing else in that file changes.

**Needs Kevin.** One yes/no: delete `scoring_match.py`, or keep it? Default is delete. See (d).

---

## (c) Spec corrections — where the plan text and the readers were wrong

1. **"currently zero coverage" is not literally true.** `tests/test_sync_playlist.py` already exists with two exit-code tests, and its docstring already reserves the rest for Phase 5. PR 3 **extends** that file; creating a second one would collide on import. `tests/test_run_pipeline.py` also already covers `discover_tal_episodes` four ways (invocation, zero-is-loud, dry-run skip, failure-raises).
2. **`spotify_match.py`'s dependencies are covered.** `ensure_spotify_token`, the shared `SPOTIFY_SCOPE`, and `common.get_db_connection`'s `cursor_factory=None` delegation are fully tested in `tests/test_common.py:65-260` — including a test specifically about this module's positional row access. PR 2 scopes to the module's own seven functions and does not re-test that surface.
3. **`feed_check` and `build_pull_queue` are off the list.** `tests/test_feed_check.py` (270 lines, PR #42 + `964f624`) covers the HTTP paths end to end. `pipeline/build_pull_queue.py` and its test were deleted in `40a07b2`; `save_item.py`'s own docstring records the retirement. The Phase 5 bullet naming them is stale — a builder who goes looking will find a dead import.
4. **Decision 7 is done, not pending.** `pipeline/venv/bin/python` is 3.12.12; CI pins 3.12; 550 tests pass in 0.84s. Phase 5's venv line needs no work.
5. **"mock the API/DB boundary, test the pure decision functions" only half-applies.** `spotify_match.py` has two genuinely pure functions and `sop/scrape.py` + `tal/parse.py` have four more. **`sync_playlist.py` has none** — every function there touches Neon or Spotify, and the nearest thing to a decision (the diff at `:287`) is an inline list comprehension inside an impure orchestrator. PR 3 is a monkeypatch-and-assert-on-captured-calls PR, not a pure-function PR. Budget accordingly.
6. **Reader `sync-playlist` was right about a dead function:** `get_latest_episode` (`:177-192`) is called from nowhere (the only other grep hit is the unrelated plural in `import_transcripts.py`). Flag it for removal in PR 3's body; do not test it, and do not delete it in a tests-only PR.
7. **Reader `http-paths` misplaced the downgrade path.** It reported the risk in `upsert_oneoff`'s `INSERT INTO episodes … ON CONFLICT (url) DO UPDATE`. Reading `save_episode.py:186-204`: that statement sets **only** `title = EXCLUDED.title`. The actual overwrite is the next statement, the `episode_transcripts` upsert `ON CONFLICT (episode_id) DO UPDATE SET transcript_text = EXCLUDED.transcript_text, source_type = EXCLUDED.source_type`. Same conclusion, different line — write the test against that one.
8. **Reader `spotify-match` said "no test file imports `pipeline.spotify_match` today" — correct**, and it stays correct until PR 2.
9. **The task framing's "PR #42 … url keys" reference:** the url-as-identity work is `198cbb8` (TAL discovery) plus Phase 4's PR #42 (`show_config.taddy_episode_url`, `episode_url_key`). No mystery PR is missing.
10. **A dry run of the matcher re-reads the same batch.** `match_songs_for_show` (`:370-497`) loops `while processed < to_process`, and `fetch_unmatched_songs` has no `OFFSET` (`:167`) — in a live run rows leave the pool because `save_results` fills the column, but under `--dry-run` nothing is written, so batch 2 fetches the same 50 songs as batch 1 and pays for the searches again. Not a production incident (the cron never dry-runs) but worth one line in PR 2's body; do not fix it in a tests-only PR.

---

## (d) What Kevin has to do

**Almost nothing — one yes/no, and one data cleanup he may want to defer.**

1. **Delete `pipeline/scrapers/tal/scoring_match.py`?** (PR 6) It is a second Spotify matcher, unwired, writing to a `scoring_tracks` table, last functionally touched 2026-06-11. Nothing calls it. Default if you say nothing: delete it (git history keeps it). Say keep and PR 6 instead wires it onto `common.get_db_connection` and adds it to the guard list.
2. **Duplicate TAL episode rows — deferred, needs your paste when you want it.** The Taddy discovery has minted a second row for episodes that already existed with a website URL, because its dedup key is `show_id + lower(title) + publish_date` and Taddy dates TAL one day later than the website does. Live today: episode 886 is rows 3021 (2026-05-03, website url, has songs) and 7846 (2026-05-04, taddy url, no songs); episode 887 is rows 3040 and 7845 the same way. `pipeline/repair_duplicate_episodes.py` cannot merge them — it matches on the same date that differs. This is cosmetic for the playlist (`get_matched_track_ids` does `SELECT DISTINCT`), but it inflates the episode count in the public playlist description. Not in Phase 5's scope; a `sql/` file plus a runner paste when you want it.
3. **Nothing for the venv.** Decision 7 landed on 2026-09-01; 3.12.12 verified 2026-09-04.
4. **Nothing to deploy.** Phase 5 touches no Worker, no workflow YAML, no secret.

---

## (e) Merge order

```
arc/phase-5
├── PR 2  test/spotify-match-decisions        ← first: creates tests/spotify_fakes.py
│    └── PR 3  test/playlist-sync-diff-and-dedup   (imports that fake)
├── PR 1  fix/tal-website-scrape-unblocked    ← parallel, no shared file
├── PR 4  test/song-parsers-frozen-fixtures   ← parallel
├── PR 5  test/saved-items-and-episodes       ← parallel
└── PR 6  chore/one-spotify-matcher           ← parallel, after Kevin's yes/no
```

PR 2 → PR 3 is the only hard sequence. PR 1 is the one to start if only one gets built, because it is the only PR that changes what happens in production. Each PR: triple-check, CI green, a Codex or independent pass at the phase boundary, then one PR from `arc/phase-5` to `main` for Kevin to merge.

---

## (f) Acceptance — three things that must be true, each checkable

1. **The Spotify write path is covered, and the suite stays hermetic and fast.**
   `pytest` green on 3.12 locally and in CI; `grep -rln "import.*spotify_match\|from pipeline import spotify_match" tests/` and the same for `sync_playlist` both return files that exercise the modules' own functions — the 0.90/0.70 boundaries, the NOT_FOUND write, the playlist diff, the 429 and partial-failure paths. No test opens a socket or a database; total runtime stays under about two seconds (it is 0.84s at 550 tests today).

2. **TAL is producing songs again.** After PR 1 merges, on the first Monday run:
   ```sql
   SELECT e.publish_date, e.title, count(s.id) AS songs
   FROM episodes e LEFT JOIN songs s ON s.episode_id = e.id
   WHERE e.show_id = 2 AND e.publish_date > DATE '2026-06-01'
   GROUP BY 1, 2 ORDER BY 1 DESC;
   ```
   returns non-zero song counts for the 11 episodes that read 0 on 2026-09-04, and the TAL playlist's track count rises. Plus a regression test that **fails on today's code**: a Taddy-discovered TAL row with `scraped_at` set is still queued for the website scrape.

3. **There is exactly one Spotify matcher, on one connection helper.**
   `grep -rn "psycopg2.connect(" pipeline/scrapers/tal/` returns nothing; `grep -rln "calculate_match_confidence\|def search_spotify" pipeline/` names `spotify_match.py` and nothing else; `tests/test_common.py`'s `SCHEDULED_PATH_MODULES` guard covers every TAL module that touches Neon.

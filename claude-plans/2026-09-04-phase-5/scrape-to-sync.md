# Reader note — `scrape-to-sync`

*Sonnet reader, 2026-09-04. Scope: the whole SOP/TAL seam — `run_pipeline.py`, `scrapers/sop/scrape.py`, `scrapers/tal/{fetch,parse,fill_songs,scrape}.py`, and how they hand off to `spotify_match` and `sync_playlist`. This is the note that found the live incident. Verified and extended by the Opus synthesis; additions marked **Synthesis check**.*

## Summary

The seam is: `run_pipeline.py` orchestrates per-show {scrape → match → sync}, calling `scrapers.sop.scrape` / `scrapers.tal.scrape`, then `spotify_match.match_songs_for_show`, then `sync_playlist.sync_show` — all direct function calls (only TAL's discovery step shells out). **Zero retry at every level:** `run_pipeline()` wraps each of the three steps in a `try/except` that records the error and returns early (`run_pipeline.py:206-245`). Per-episode scraping and per-song matching do continue past individual failures, but the orchestrator never retries a step.

**SOP** (`scrapers/sop/scrape.py`): Firecrawl markdown only, no fallback, no local cache. Discovery diffs the episodes-list page against `SELECT url FROM episodes WHERE show_id = 1`. `insert_episode` does `ON CONFLICT (url) DO UPDATE … scraped_at = NOW()`, committed per-episode together with that episode's songs (`:348`). A per-episode failure rolls back just that episode and leaves it eligible for the next run. Healthy.

**TAL** (`scrapers/tal/{fetch,parse,fill_songs}.py`): "dumb fetch, smart parse" — `fetch.py` hits Firecrawl async and writes raw JSON to a git-ignored local cache; `parse.py` is a pure function over that JSON; `fill_songs.py` diffs parsed songs against the DB and inserts what's missing. Discovery was added 2026-08-02 (`198cbb8`) by shelling out to the Taddy importer.

## Headline finding — a live incident, not a hypothesis

The TAL discovery fix is silently defeating the scraper it was meant to feed, for every TAL episode published since 2026-08-02:

1. `discover_tal_episodes` (`run_pipeline.py:78-121`) runs `taddy/import_transcripts.py --shows tal`, which writes `episodes.url = taddy_episode_url(uuid)` = `https://api.taddy.org/podcast-episode/<uuid>` (`show_config.py:251-263`) — a synthetic identity key, not the thisamericanlife.org page.
2. That importer's INSERT and its `ON CONFLICT (url) DO UPDATE` both unconditionally set `scraped_at = NOW()` (`import_transcripts.py:364`, `:397`) — on the very insert that creates the row.
3. `tal/fetch.py:get_unscraped_episodes()` selects `WHERE show_id = 2 AND scraped_at IS NULL AND url IS NOT NULL` (`:66-71`) — every newly discovered row fails that filter the instant it exists.
4. Even if it were selected, `fetch_episode()` would Firecrawl the synthetic `api.taddy.org` URL, which has no `## Song:` sections, so song extraction would fail anyway.

Net effect: since 2026-08-02, every newly discovered TAL episode gets its transcript imported and is correctly counted "caught up" by the feed check (which compares episode identities only and never touches `songs`), while its songs are never scraped and its playlist entry is permanently skipped. The cron reports success every Monday.

> **Synthesis check (2026-09-04) — confirmed live, read-only SQL against Neon.** Newest TAL episode holding any songs: **2026-05-10**. **11** TAL episodes published since 2026-06-01 hold zero songs. **15** TAL rows carry a Taddy API url; all 15 have zero songs. **Zero** TAL rows have `scraped_at IS NULL`, so the queue is permanently empty. Two further findings the reader did not have: (a) TAL's `raw_content` is NULL (the show has `store_raw_content=False`), so the real page URL is **not** recoverable from the row — it must come from the RSS `<link>` or be derived from the title slug; (b) the discovery is also **minting duplicate rows** — episode 886 exists as ids 3021 (website url, has songs, 2026-05-03) and 7846 (taddy url, no songs, 2026-05-04) — because the importer's dedup key is `show_id + lower(title) + publish_date` and Taddy dates TAL one day later than the website. `repair_duplicate_episodes.py` cannot merge them: it keys on the same date that differs.

## Functions

| Function | File:line | Pure | What to test |
|---|---|---|---|
| `parse_songs_discussed` | `sop/scrape.py:184` | yes | Both formats (bullet + en-dash, and quoted); the Previous/Next nav filter; the `(Album)` and underscore-artist filters on the quote fallback; no section → `has_songs_section=False`; the fallback fires only when the dash pattern found zero, not as a union |
| `parse_episode_list` | `sop/scrape.py:151` | yes | The `# [Title](url)` regex on a multi-episode page; the MM/DD/YY lookup in the 100 chars before the match; no date → `None`; a malformed date is swallowed to `None` |
| `parse_description_body` | `sop/scrape.py:231` | yes | Title-header strip, truncation at Songs Discussed, each of the three footer regexes; a fixture missing one footer still comes back clean |
| `insert_songs` dedup | `sop/scrape.py:96` | no | Pre-insert SELECT-then-filter against a fake cursor pre-loaded with existing `(title, artist)` pairs: `executemany` receives only the new pairs, and zero-new returns 0 without calling `executemany` at all |
| `parse_episode` | `tal/parse.py:25` | yes | 404 detection; episode number from the URL; title suffix strip and curly-quote normalisation; both song-line formats; the `## Song:` splitter at 0/1/many |
| `clean_quotes` | `tal/parse.py:20` | yes | All four curly code points → straight |
| `calculate_match_confidence` | `spotify_match.py:97` | yes | The 55/45 weighting; multi-artist takes the **max**, not the first or the mean; both sides lower-cased |
| `get_confidence_category` | `spotify_match.py:127` | yes | Exactly 0.90 and 0.70 — a `>=`/`>` slip silently reclassifies borderline matches upward into auto-sync eligibility |
| `search_and_score` | `spotify_match.py:225` | no | Picks the single highest-confidence track (not first, not last); `None` on empty results |
| `save_results` | `spotify_match.py:182` | no | Matched rows get every column; not-found rows get only `NOT_FOUND`. Pair with `fetch_unmatched_songs` to show NOT_FOUND is terminal by construction — a test that documents this is intentional |
| `get_matched_track_ids` | `sync_playlist.py:158` | no | Only HIGH/MEDIUM/MANUAL with a non-null track id are eligible — the "wrong song on a live playlist" guardrail |
| the diff | `sync_playlist.py:287` | no | Only non-overlapping tracks reach `add_tracks_to_playlist`; and assert no removal-shaped method is ever called (a wipe is structurally impossible today — worth pinning) |
| `add_tracks_to_playlist` | `sync_playlist.py:113` | no | 100-track chunking; the 429 retry-after loop; a non-429 breaking out and undercounting `added` |
| `DESCRIPTION_TEMPLATE` | `sync_playlist.py:54` | yes | The `format()` itself: comma-grouped songs, MM/YY date |
| `get_unscraped_episodes` × discovery | `tal/fetch.py:61` | no | **The regression test:** a Taddy-style TAL row (taddy url, `scraped_at` set) must still surface for the website scrape. Today it does not. Fails on current code, passes once the fix lands |

## The boundary

Three shapes, all already faked elsewhere in `tests/`:

1. **DB cursor/connection** — every module here delegates to `common.get_db_connection()` (with `cursor_factory=None` for `spotify_match`, which indexes rows positionally — preserve that in any fake). Follow `tests/test_load_entity_batch.py`'s local `_FakeCursor`/`_FakeConn`. **There is no `conftest.py` in this repo; every test file defines its own fakes inline. That is the house style, not a gap to fill.**
2. **Firecrawl HTTP** — `sop/scrape.py:scrape_url()` and `tal/fetch.py:fetch_episode()` call httpx directly. Best move: don't fake HTTP at all — test the pure parsers on fixture markdown/JSON and sidestep the boundary entirely. Freeze 2–3 real pages (a normal episode, a no-songs episode, a quote-format episode); `tests/fixtures/` already has the precedent.
3. **Spotify (spotipy)** — none of the functions worth testing need a real client or `get_spotify_client`; they take `sp` as a parameter. Hand-build a fake with only `.search()`, `.playlist_tracks()`, `.playlist_add_items()`, `.playlist_change_details()`.

**Orchestration boundary:** `run_pipeline.py`'s `run_scrape`/`run_match`/`run_sync` do lazy `sys.path.insert` + module imports right before calling in, which makes them awkward to monkeypatch from outside. Prefer testing `sop.scrape.scrape_new_episodes`, `tal.scrape.scrape_new_episodes`, `spotify_match.match_songs_for_show` and `sync_playlist.sync_show` directly — each returns a plain summary dict — rather than intercepting through the dispatch layer, which `tests/test_run_pipeline.py` already covers where it matters.

## Production incidents this covers

- **The TAL incident above** — live, confirmed, and a fix-plus-regression-test, not a test-only PR.
- **A wrong song shipped at HIGH** — the 0.90 boundary in `get_confidence_category`, untested today.
- **A duplicate add** — `sync_playlist.py:287`, untested; Spotify allows duplicate entries and there is no downstream safety net.
- **A playlist wipe is structurally impossible** — grepped the whole `pipeline/` tree for `playlist_remove`/`playlist_replace` and found none. Worth a test that pins the invariant rather than trusting the current absence.
- **A NOT_FOUND overwrite is structurally guarded but untested** — `fetch_unmatched_songs` excludes them by construction (`:161-162`), which is intentional and matches CLAUDE.md's UNAVAILABLE note. A test should document it so a future WHERE-clause change doesn't silently start re-processing them.

## Corrections to the parent plan

1. "Currently zero coverage" is broader than the code shows: `tests/test_run_pipeline.py` already covers `discover_tal_episodes` four ways, and `tests/test_sync_playlist.py` exists with two tests. The real gap is (a) the pure parsers, (b) `sync_playlist`'s diff/dedup/description logic, (c) `tal/parse.py`, and now (d) the TAL discovery collision, which the plan (written 2026-09-01) could not have known about.
2. Decision 7 is already done: `test.yml` and every scheduled workflow pin 3.12, and the local venv was rebuilt 2026-09-01.
3. The task brief's "PR #42, url keys" reference does not resolve to anything in this repo's history under that framing — the url-as-identity semantics come from `198cbb8` and `show_config.episode_identity`. Flagged so nobody hunts for a missing PR.

## Dead code

- `scrapers/tal/fill_songs.py:29-34` — a private `get_db_connection()` on bare `psycopg2.connect(os.getenv("DATABASE_URL"))` with none of `common.py`'s timeout/keepalives/retry: the exact anti-pattern every sibling in the same directory replaced (each carries the identical comment citing the 2026-08-31 41-minute hang). `tal/scrape.py` imports five other helpers from this file but never its `get_db_connection`, so the stale copy is dead in the scheduled path and reachable only when `fill_songs.py` is run by hand — which is precisely how someone rediscovers the hang. One-line fix.
- `scrapers/sop/download_episode_art.py` and `scrapers/tal/download_episode_art.py` — not wired into `pipeline.yml` or `run_pipeline.py`; manual mosaic-artwork one-offs owned by `marketing/`. Out of scope, not "untested seam".
- `scrapers/tal/repair_metadata.py` and `scrapers/tal/scoring_match.py` — also absent from the cron; historical repair/backfill scripts. Untested manual scripts are a different risk tier from untested cron code.

> **Synthesis check.** `repair_metadata.py` is documented as a manual tool in `pipeline/README.md:95-96` and stays. `scoring_match.py` is documented nowhere but a README line and is a second Spotify matcher — promoted to PR 6 with a yes/no for Kevin.

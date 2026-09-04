# Reader note — `sync-playlist`

*Sonnet reader, 2026-09-04. Scope: `pipeline/sync_playlist.py` (343 lines) and its callers. Verified by the Opus synthesis; corrections marked **Synthesis check**.*

## Summary

Zero coverage except the two exit-code tests from PR #44 (`tests/test_sync_playlist.py`: unknown `--show-id` → `SystemExit(2)`, bad CLI type → `SystemExit(2)`). Nothing exercises the diff, dedup or batch logic that actually moves the songs.

Every function in the file touches Neon or the Spotify API — there is **no standalone pure function** to unit-test with plain inputs, so the tests here have to monkeypatch the module's own DB/Spotify entry points (module-level names, resolved at call time) and assert on captured SQL and calls, following `test_load_entity_batch.py`'s fake-cursor style.

Two real production-risk gaps (silent partial failure in `add_tracks_to_playlist`, silent partial read in `get_playlist_tracks` that can cause duplicate adds), one dead function, a three-way duplicated SHOWS/playlist-id definition, and a mismatch between what the exit-code-2 comment claims and what the live SOP/TAL cron path actually does.

## Functions

| Function | Line | What to test |
|---|---|---|
| `sync_show` | 250 | The unknown-show `ValueError` (`:262`) directly, before any DB/Spotify call. Then with the collaborators monkeypatched: empty `db_tracks` returns immediately and never calls `get_spotify_client`; no new tracks skips `add_tracks_to_playlist` but still updates the description when not `dry_run` (and not when `dry_run`); `dry_run=True` with tracks to add returns before either write; the returned stats dict at every branch |
| `get_matched_track_ids` | 158 | The SQL contains `spotify_match_confidence IN ('HIGH', 'MEDIUM', 'MANUAL')` and `spotify_track_id IS NOT NULL` — a typo here silently and permanently drops human-reviewed songs from the playlist with no error. `show_id` is a bound param. `ORDER BY spotify_track_id` is alphabetical by Spotify id, **not** episode order — pin it, it surprises people |
| `get_playlist_tracks` | 85 | Paging: 101 items across two pages → called twice with `offset=0` then `offset=100`, 101 unique ids. Items with `track is None` or no `id` are skipped, not a crash. **Partial-failure:** a `SpotifyException` on page 2 is caught at `:106-108` and `break`s, silently returning page 1 only. Pin as current behaviour and flag: that partial set flows into the diff, so already-present tracks read as "new" and get duplicated in Spotify |
| `add_tracks_to_playlist` | 113 | 250 ids → three `playlist_add_items` calls of 100/100/50, `added == 250`. 429 once then success → retries the **same** batch, sleeps `retry_after + 1` (monkeypatch `sync_playlist.time.sleep`). **Silent failure:** 429 through `MAX_RETRIES` exhausts the loop with no raise and no break-out, so the batch is silently dropped and `added` undercounts; a non-429 error is caught, printed, and `break`s that batch only. Both report as a clean run |
| `update_playlist_description` | 224 | The exact `DESCRIPTION_TEMPLATE` substitution: comma-grouped song count, episode count, the show's acronym, `datetime.now().strftime("%m/%y")`. `datetime` is imported **inside** the function, so it cannot be monkeypatched — compute the expected date the same way. A `SpotifyException` from `playlist_change_details` is caught and only warns; confirm that is intentional so a future change doesn't make it fatal |
| `get_playlist_stats` | 195 | Two SELECTs: the songs count must use the same confidence filter as `get_matched_track_ids` (drift makes the description lie); the episodes count filters `scraped_at IS NOT NULL` only |
| `get_db_connection` | 144 | Low value — a thin lazy-import wrapper. One delegation test at most; every other test should monkeypatch `sync_playlist.get_db_connection` directly |
| `main` | 314 | Already has exit-code coverage. A success-path test is worth little; don't let it eat the budget |

## The boundary

**DB.** Every DB function calls the module-level `get_db_connection()` (`:144`) then `with conn.cursor() as cur: cur.execute(...)`. Build a `_FakeCursor` with `__enter__`/`__exit__`, a recording `execute(sql, params)`, and `fetchall()`/`fetchone()` returning canned **dict-like** rows (RealDictCursor is used in production: `row["spotify_track_id"]`, `row["songs"]`), plus a `_FakeConn` with `.cursor()` and a no-op `.close()`. Monkeypatch `sync_playlist.get_db_connection`, not `common.get_db_connection` — the business functions resolve the name from this module's globals.

**Spotify.** `get_playlist_tracks`, `add_tracks_to_playlist` and `update_playlist_description` all take `sp` as an explicit parameter, so build a small fake exposing only `playlist_tracks(playlist_id, offset, limit)`, `playlist_add_items(playlist_id, uris)` and `playlist_change_details(playlist_id, description=)`, recording calls and able to raise a real `spotipy.exceptions.SpotifyException` (constructor signature confirmed by reading the installed venv). `get_spotify_client()` (`:67`) does real OAuth and must never run in a test — for `sync_show`, monkeypatch it to return the fake. Always monkeypatch `sync_playlist.time.sleep` (`API_DELAY` is 0.5s).

## Production incidents this covers

- **Duplicate playlist entries.** `get_playlist_tracks` (`:106-108`) truncates silently on any `SpotifyException`; the diff at `:287` then treats real already-present tracks as new and sends them to `playlist_add_items`. Spotify permits duplicates and nothing here dedupes or removes.
- **Silent partial-sync reported as success.** `add_tracks_to_playlist` (`:117-137`) breaks out with only a print, both when the 429 retries are exhausted and on any non-429 error. `sync_show` reports the count as if complete and `main()` exits 0.
- **Three independently maintained copies of show/playlist metadata.** `sync_playlist.py:40-51` duplicates `show_config.py:89-123` (which CLAUDE.md and its own docstring call the single source of truth) and a third, lighter copy lives in `run_pipeline.py:56-60`. They match today by luck; a playlist change in `show_config.py` would leave this file writing to a stale id with no error anywhere.
- **The exit-code-2 contract is not exercised on the live SOP/TAL path.** The comment at `:334-339` is true for `run_new_episodes.step_spotify_sync`, but SOP/TAL actually run `pipeline.yml → run_pipeline.py → run_sync()` (`run_pipeline.py:160-167`), which calls `sync_show` in-process, wraps it in a bare `except Exception` (`:239`) and always exits 1 (`:392-393`).
- **No removal path exists at all.** The only Spotify writes are `playlist_add_items` and `playlist_change_details`. A track later reclassified away from HIGH/MEDIUM/MANUAL stays in the live playlist forever. Likely intentional (append-only, never destructive) — worth a test that pins it so a future change doesn't assume removal exists.

## Corrections to the parent plan

1. "Mock the API/DB boundary, test the pure decision functions" implies pure functions exist here. None do. Plan on monkeypatching module-level entry points and asserting on captured calls.
2. The `ValueError` comment (`:335-338`) is accurate for one orchestrator and incomplete as written — it doesn't say that a *second* orchestrator is what actually runs SOP/TAL on the cron, and that path gets none of the exit-2 treatment. Worth a one-line clarification in the code when this PR touches the file.

## Dead code

- `get_latest_episode` (`:177-192`) — queries the most recent scraped episode for a show and is called by nothing (grep-confirmed; the only other hit is the unrelated plural `get_latest_episodes` in `import_transcripts.py`). Don't test it; flag it for removal, or wire it into the description template if that was the intent.

> **Synthesis check (2026-09-04).** All line numbers confirmed. The dead-function claim confirmed by grep. The three-way SHOWS duplication confirmed; note it is already a named Phase 3 item, so PR 3 asserts against `sync_playlist.SHOWS` (today's seam) plus a drift test against `show_config`, rather than hardcoding playlist ids.

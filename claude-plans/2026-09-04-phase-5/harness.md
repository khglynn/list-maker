# Reader note — `harness`

*Sonnet reader, 2026-09-04. Scope: the test harness itself — what CI runs, what the venv is, the existing fake conventions, and how a Phase 5 test file should be shaped. Verified by the Opus synthesis; corrections marked **Synthesis check**.*

## Summary

Phase 5's target is narrower than the task framing suggests: the parent plan's acceptance criterion is "`tests/` imports `spotify_match` and `sync_playlist` directly" — it does not scope in the SOP/TAL scrapers, which are also untested but were never named.

**The harness is ready.** `pipeline/venv/bin/python` is Python 3.12.12 with pytest installed (Decision 7 is done, not pending), CI matches (3.12, hermetic, blocking `pip-audit`), and **550 tests currently pass in 0.84s**. Coverage is not literally zero: `tests/test_sync_playlist.py` exists with two exit-code tests and its own docstring says "the module's live surface … has no coverage yet — that is Phase 5's job," so the new work should **extend that file**, not create a parallel one that would collide on import.

Two production-risk findings surfaced by reading (not hypothetical): `get_playlist_tracks` silently truncates on any `SpotifyException` mid-pagination and the caller's diff then re-adds already-present tracks; `add_tracks_to_playlist` silently drops a whole 100-track batch on a non-429 error, reported as success. A third script, `scrapers/tal/scoring_match.py`, shares the same Spotify-scope contract but is dead code that bypasses `common.get_db_connection` entirely — it should be explicitly deleted or wired in, not left both untested and unlisted.

## Functions

| Function | File:line | What to test |
|---|---|---|
| `calculate_match_confidence` | `spotify_match.py:97` | The flat 55/45 `fuzz.ratio` blend, no length or album-context penalty. Pin a table of inputs → expected float, **including a short generic title that string-collides its way to HIGH against an unrelated song** — the "wrong song at HIGH" category deserves a fixture even though the algorithm isn't changing |
| `get_confidence_category` | `spotify_match.py:127` | 0.90 → HIGH, 0.8999 → MEDIUM, 0.70 → MEDIUM, 0.6999 → LOW. The two boundary constants are the whole risk surface |
| `search_and_score` | `spotify_match.py:225` | Picks strictly the highest-confidence track (`>`, not `>=`, so a tie keeps the first — worth pinning); `None` on empty results |
| `search_with_retry` | `spotify_match.py:71` | 429 sleeps `Retry-After + 1` and retries to `MAX_RETRIES = 3`; any other `SpotifyException` returns `None` immediately; a plain exception retries then gives up |
| `match_songs_batch` | `spotify_match.py:261` | Empty-title rows skip Spotify entirely (`:277-280`) — assert `search_and_score` is never called for them. An exception for one song must not abort the batch: it is caught at `:296` and that song lands in `not_found` while later songs still process |
| `fetch_unmatched_songs` | `spotify_match.py:155` | SQL/param shape; the `show_id` clause is appended only when truthy (so `show_id=0` would read as "no filter" — show ids start at 1, so fine today, worth one line in the PR). **The fake must accept the `cursor_factory=RealDictCursor` kwarg this call passes at `:177`** — a fake that only supports a bare `cursor()` breaks here |
| `save_results` | `spotify_match.py:182` | The write seam, highest value in the file. (1) matched rows update eight columns keyed on song id, storing the **category string**, never the float — pin that the numeric confidence is persisted nowhere; (2) not-found rows update with only `WHERE id = %s` and no re-check of the current confidence, so a manual correction landing mid-batch is clobbered — pin the time-of-check/time-of-write gap; (3) all writes share one `conn.commit()` after the loop (`:219`), so an exception partway leaves `committed` False and the whole batch's paid-for API calls are discarded |
| `match_songs_for_show` | `spotify_match.py:370` | The single most important regression test here: with `dry_run=True`, `save_results` and `log_progress` must **never** be called across a multi-batch run — monkeypatch both to raise if invoked. Also: `yes=False` plus a `KeyboardInterrupt` at the prompt returns a zeroed dict without touching the DB or Spotify; `conn.close()` runs in the `finally` even when the batch raises |
| `get_playlist_tracks` | `sync_playlist.py:85` | **Incident-critical.** The pagination break on `len(items) < 100` is correct, but the `except SpotifyException` at `:106-108` breaks and returns what was fetched so far on **any** error. Raise on page 3 of 5 and assert only pages 1–2 come back with no exception surfaced — then trace forward: that truncated set makes already-added tracks look new. Fixing it (raise, or retry) is a design decision for Kevin, not something to change quietly inside a test PR. Also test the `item.get("track") is None` guard (`:98-100`) |
| `add_tracks_to_playlist` | `sync_playlist.py:113` | **Incident-critical.** Batches of 100, uri format `spotify:track:{id}`. On 429: sleeps and retries within `MAX_RETRIES`; verify a pathological always-429 fake really stops. On any other error the inner `break` gives up on that one batch while the outer loop continues — 250 ids with a non-429 on batch 2 → `added == 150`, no exception, no signal |
| `sync_show` | `sync_playlist.py:250` | Unknown show id raises before any call (add a direct test; `main()` only proves the CLI wrapping). Empty `db_tracks` short-circuits **without** building a Spotify client. `dry_run=True` writes nothing in **both** branches — and the "no new tracks" branch (`:291-296`) still updates the description when not dry-run; verify that asymmetry is preserved deliberately. The diff at `:287` tested directly with overlapping / contained / disjoint sets |
| `update_playlist_description` | `sync_playlist.py:224` | `{songs:,}` grouping on a 4-digit count, `{date}` as MM/YY. A raising `playlist_change_details` is caught and only printed (`:242-243`) — a broken description update must not fail the sync |

## The boundary

Two independent boundaries, both faked the way the suite already fakes DB access: **monkeypatch the module-level wrapper, not the shared implementation underneath it** (mirroring `tests/test_load_entity_batch.py`'s `monkeypatch.setattr(leb, "get_db_connection", lambda: conn)`).

**DB.** `spotify_match.py:140` and `sync_playlist.py:144` each define their own `get_db_connection()` delegating to `common.get_db_connection` — already covered by `tests/test_common.py`'s timeout/retry/keepalive tests and its `SCHEDULED_PATH_MODULES` grep guard. Phase 5 fakes past that layer, it does not re-test it. Reuse the `_FakeCursor`/`_FakeConn` shape but extend it: an optional `cursor_factory=` kwarg, a `.close()` flag for the finally-block test.

Where one test needs two different queries to answer differently (the COUNT vs the per-batch fetch), follow `tests/test_data_health.py`'s SQL-content dispatch fixture (`return X if "FROM ai_entities" in sql else Y`) rather than a blanket stub. **PR #44's own retro names this trap twice:** a blanket stub silently measures nothing when two queries share one seam, and a monkeypatch aimed at a seam a refactor stopped using either fails loudly (safe) or keeps passing while exercising only the old path (the `pulse_report` `cfg=None` case — 16 fixtures all silently took the date path).

**Spotify.** Both files build a real `spotipy.Spotify`, but every downstream call site uses only four methods: `.search()`, `.playlist_tracks()`, `.playlist_add_items()`, `.playlist_change_details()`. None of the functions worth testing need `get_spotify_client` at all — they take `sp` as a parameter. Build **one** shared `FakeSpotify` in a new `tests/spotify_fakes.py` covering all four (recording calls, returning canned dicts, able to raise a real `spotipy.exceptions.SpotifyException` on a configurable call number) and import it from both the new `test_spotify_match.py` and the extended `test_sync_playlist.py`, rather than growing two ad-hoc mocks. Always monkeypatch `time.sleep` in the retry paths so the suite stays sub-second.

## Production incidents this covers

- **Duplicate tracks on a live playlist** — the truncation at `sync_playlist.py:106-108` feeding the diff at `:287`.
- **Silent partial write reported as full success** — `add_tracks_to_playlist`'s inner `break` at `:133-135` with the outer loop continuing.
- **A wrong song at HIGH** — a flat fuzz blend with no length or album penalty; no fixture today pins a known false-positive shape at the 0.90 line.
- **A prior match silently overwritten** — `save_results`' NOT_FOUND update guards only on `WHERE id = %s`, with a real time-of-check/time-of-write gap across however long the batch's searches took.
- **One bad row discards a whole paid-for batch** — the single commit at `:219`.
- **`--dry-run` correctness has no guard** — `match_songs_for_show` gates writes at the call site (`:465`), not inside `save_results`, and nothing today proves a dry run never reaches the database.

## Corrections to the parent plan

1. "ZERO test coverage today" is not quite right — `tests/test_sync_playlist.py` exists and reserves the rest for Phase 5. Extend it; a second file would collide on import.
2. Decision 7 is done and verified: venv 3.12.12, CI `actions/setup-python@v7` pinned to 3.12. Phase 5 needs no venv work — just `pipeline/venv/bin/python -m pytest`.
3. Phase 3's separate finding still holds: `sync_playlist.py:40-51` carries its own SHOWS dict duplicating `show_config.SHOWS`, so the single-source-of-truth docstring is false. New tests should assert against `sync_playlist.SHOWS` (today's real seam) so they survive the Phase 3 refactor, plus one cross-check test now (`sync_playlist.SHOWS[1]["playlist_id"] == show_config.SHOWS["sop"].spotify_playlist_id`) that catches the drift immediately.
4. The plan's acceptance line is narrower than the "scrape → match → sync" framing. The scrape/parse step that decides what strings become `songs` rows has zero test files and is named nowhere in Phase 5's acceptance. Resolve it explicitly with Kevin — in scope, or a named follow-up — rather than silently including or dropping it.

> **Synthesis check (2026-09-04).** All of the above confirmed, including 550 tests in 0.84s and venv 3.12.12. On #4: the synthesis put the parsers **in** scope as PR 4 (S) — they are the cheapest tests in the phase and they are the seam that feeds everything else. On the two incident-critical behaviours: PR 3 pins them as current behaviour and raises the design question in its body; it does not change them.

## Dead code

- `scrapers/tal/scoring_match.py` — an orphaned script for a separate `scoring_tracks` table. Grepped across source, workflows, README and DEVLOG: the only references are its own file, one README line, and historical DEVLOG entries. Deliberately excluded from `SCHEDULED_PATH_MODULES`, meaning nobody is watching that it stays on the shared connection helper — and it does not: its own `get_db_connection()` (`:36-41`) calls `psycopg2.connect()` with no timeout, retry or keepalives, bypassing the fix that closed the 2026-08-31 41-minute hang for every other scheduled script. Its confidence algorithm (`search_spotify`, `:75`) is a cruder duplicate of `calculate_match_confidence` (substring containment, not fuzzy scoring). Last functional change 2026-06-11; created 2026-01-25. Per `docs/principles.md` ("delete dead code, don't disclaim it"), delete it or wire it onto `common.get_db_connection` and into the guard list — leaving it both untested and unlisted is itself an unflagged gap.

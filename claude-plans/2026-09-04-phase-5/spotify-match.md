# Reader note — `spotify-match`

*Sonnet reader, 2026-09-04. Scope: `pipeline/spotify_match.py` (527 lines). Verified by the Opus synthesis against the live file; corrections are marked **Synthesis check**.*

## Summary

One real boundary — spotipy's `sp.search()` and psycopg2 cursors — everything else is deterministic Python. Two pure decision functions (`calculate_match_confidence`, `get_confidence_category`), three boundary functions needing fakes (`search_with_retry`, `fetch_unmatched_songs`, `save_results`), one scoring function that touches the boundary (`search_and_score`), and one orchestration function (`match_songs_batch`) whose exception policy is the file's real production risk.

Any exception inside `search_and_score` — not just a genuine no-match — is caught in `match_songs_batch`'s `except Exception` and the song is written to the DB as `NOT_FOUND`. Since `fetch_unmatched_songs` only selects rows `WHERE spotify_match_confidence IS NULL`, and nothing ever clears that column, a transient bug or malformed API response **permanently** poisons the row: it is never retried by this pipeline again.

Not fully zero-coverage, despite the parent plan: the re-auth path (`ensure_spotify_token`), the shared `SPOTIFY_SCOPE` invariant, and `common.get_db_connection`'s `cursor_factory=None` delegation are already fully tested in `tests/test_common.py:65-260`, including a test specifically about this module's positional row access. What is genuinely untested is `spotify_match.py`'s own module-level functions — no test file imports `pipeline.spotify_match` today.

## Functions

| Function | Line | Pure | What to test |
|---|---|---|---|
| `calculate_match_confidence` | 97 | yes | 55%/45% title/artist weighted average of `thefuzz.fuzz.ratio`, lower-cased, rounded to 3dp. Exact match ≈ 1.0, case-insensitivity, empty `result_artists` (artist score 0, no crash), the exact weighting against a hand-computed example, unicode/diacritic titles |
| `get_confidence_category` | 127 | yes | Boundaries at `HIGH_THRESHOLD = 0.90` and `MEDIUM_THRESHOLD = 0.70`, both `>=`: 0.90 is HIGH, 0.899 MEDIUM, 0.70 MEDIUM, 0.699 LOW |
| `search_with_retry` | 71 | no | Fake `sp.search(q=, type=, limit=)`. Success returns the raw dict; 429 sleeps `int(Retry-After) + 1` (monkeypatch `time.sleep`) and retries; a non-429 `SpotifyException` returns `None` immediately with **no** retry (assert one call); a generic exception retries to `MAX_RETRIES = 3` with 2s backoff then returns `None` |
| `search_and_score` | 225 | no | Fake search returning the real Spotify shape. Best-of-N uses strict `>` (a tie keeps Spotify's ranking); empty `items` → `None`; a track missing an expected key raises here rather than being swallowed (the swallow is one level up) |
| `fetch_unmatched_songs` | 155 | no | `show_id=None` omits the `AND e.show_id = %s` clause and its param; a given `show_id` is appended before `LIMIT`; params are exactly `[show_id, limit]` or `[limit]` — a swap silently pulls the wrong show's songs |
| `save_results` | 182 | no | Exact SQL and params for a matched row (eight columns, `WHERE id`) and a not-found row (`spotify_match_confidence = 'NOT_FOUND'`); `conn.commit()` called exactly once regardless of list sizes, including both empty. Note: only the **category string** is persisted — the numeric confidence float is never written anywhere |
| `match_songs_batch` | 261 | no | Empty title skipped without calling search, lands in `not_found`. **Key test:** make `search_and_score` raise → the song lands in `not_found` alongside genuine no-matches, pinning the permanent-poisoning behaviour. `time.sleep(API_DELAY)` called once per song (counter, never a real sleep in CI) |

## The boundary

The only real I/O is `spotipy.Spotify.search()` (called only inside `search_with_retry`) and psycopg2 cursors (`fetch_unmatched_songs`, `save_results`, and the inline COUNT in `match_songs_for_show`).

Fake the Spotify side with a minimal object exposing `.search(q=, type=, limit=)` returning the real search-response shape:

```python
{"tracks": {"items": [{"id": str, "name": str,
                       "artists": [{"name": str}],
                       "album": {"name": str}, "popularity": int}]}}
```

Use the real `spotipy.exceptions.SpotifyException` (already a runtime dependency, pure import, no network) rather than a hand-rolled exception type — confirmed in the venv that the signature is `(http_status, code, msg, reason=None, headers=None)` and `headers` defaults to `{}`.

For the DB side, reuse the `_FakeCursor`/`_FakeConn` pattern from `tests/test_load_entity_batch.py:32-56` (`execute()` appends `(sql, params)` to `.calls`, cursor supports `__enter__`/`__exit__`, `conn.commit()` sets a flag). `get_spotify_client` and this module's `get_db_connection` are integration entry points backed by already-tested `common.py` functions — do not re-test them.

> **Synthesis check (2026-09-04).** Confirmed all seven line numbers and behaviours. One addition the reader did not catch: `fetch_unmatched_songs` calls `conn.cursor(cursor_factory=RealDictCursor)` explicitly at `:177`, while `match_songs_for_show` reads its COUNT row positionally at `:412` — so the fake `cursor()` **must accept and ignore a `cursor_factory=` kwarg**, which the `test_load_entity_batch.py` fake does not.

## Production incidents this covers

- **A wrong song matched at HIGH.** The thresholds and the 55/45 weighting are the entire correctness gate before a track id is written and later synced to a live playlist. Untested today.
- **A NOT_FOUND permanently overwriting a retriable row.** `fetch_unmatched_songs` selects only `WHERE spotify_match_confidence IS NULL`; `save_results` writes `'NOT_FOUND'` on any no-match **or** any caught exception. Once written, the song is excluded from every future run forever — there is no reset anywhere in this pipeline.
- **The wrong show's songs matched or limited.** The conditional WHERE/params construction in `fetch_unmatched_songs` would fail silently, not loudly.
- **A concurrent manual fix clobbered.** `save_results` UPDATEs by song id with no re-check of the row's current state, so a later write silently wins over a value a human set by hand (MANUAL / UNAVAILABLE statuses are set by direct SQL and are never referenced or protected by this module).

## Corrections to the parent plan

1. The Phase 5 line describes `spotify_match.py` as effectively zero-coverage. True for its own functions, false for the token/connection path it depends on (`tests/test_common.py:65-260`). Scope the PR to the module's own seven functions.
2. "`tests/` imports `spotify_match` and `sync_playlist`" is already half-true: `tests/test_sync_playlist.py` exists and imports the module, but pins only exit codes. That half needs **extension**, not creation.
3. The real risk surface is subtler than "confidence thresholds." The thresholds are the easy pure tests; the exception-swallowing at the orchestration boundary is the one that actually produces a production incident and deserves its own named test.

## Dead / duplicated code noticed

- `pipeline/scrapers/tal/scoring_match.py` hand-rolls its own `get_db_connection()` (`:36`) with no timeout, retry or keepalives — the exact bug class fixed elsewhere on 2026-08-31 — and is called from no orchestrator or workflow. A manual-only, unscheduled script for a separate `scoring_tracks` table (TAL playlist id `3d7fjfrTTKvrl7VHv5JzIz`). Out of scope for automated-path coverage, worth a flag to Kevin. *(Synthesis: promoted to PR 6.)*

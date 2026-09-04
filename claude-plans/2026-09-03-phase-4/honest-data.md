# Item 4 — small honest-data fixes: implementation map

Repo: `list-maker` @ `main`. Read-only investigation; nothing edited. Verified against
production Neon (read-only) where noted.

---

## (a) `zero_mention_runs` — show filter + rolling window

**Today:** `pipeline/data_health.py:566` `check_ai_daily_extraction()`. Its first query
(the `ai_show` CTE, lines 570-596) already scopes to `slug = 'ai-daily-brief'`. But the
`zero_mention_runs` query two blocks down does **not** reuse that CTE and has no time
bound:

```
pipeline/data_health.py:628-644
    zero_mention_runs = int(
        _one(conn, """
            SELECT COUNT(*) AS count
            FROM (
              SELECT r.id
              FROM ai_runs r
              LEFT JOIN ai_mentions m ON m.run_id = r.id
              WHERE r.status = 'completed'
              GROUP BY r.id
              HAVING COUNT(m.id) = 0
            ) x;
        """).get("count") or 0
    )
```

`ai_runs.show_id` exists and is indexed (`pipeline/scrapers/ai_daily/sql/001_ai_entity_schema.sql:7,20`)
— the table now covers every entity/media show (ai-daily-brief, hard-fork, PCHH,
culture-gabfest), not just AI Daily. Without a show filter, a zero-mention `completed`
run from *any* of them (or a legacy row with `show_id IS NULL`, which the FK allows via
`ON DELETE SET NULL`) permanently pollutes a check named for AI Daily. Without a window,
one old anomaly — e.g. from before `EMPTY_RUN_STATUS = "completed_empty"` existed
(`pipeline/scrapers/ai_daily/load_entity_batch.py:196`) — pins this check red forever
regardless of current health, the exact "silence never distinguishable from fine"
problem this phase targets.

Confirmed empirically (read-only, production Neon, 2026-09-03): **0 rows** currently
match the unscoped query, so this is a forward-hardening fix, not a live false
positive — safe to ship without changing today's health-check output.

**Design:** reuse the function's own `ai_show` CTE (consistent with `missing_mentions`
right above it) and add the same 30-day window the function already uses one block
later for `declared_empty_runs` (line 606: `r.created_at >= NOW() - INTERVAL '30 days'`)
— match the existing convention rather than inventing a new one.

```sql
WITH ai_show AS (
  SELECT id FROM shows WHERE slug = 'ai-daily-brief'
)
SELECT COUNT(*) AS count
FROM (
  SELECT r.id
  FROM ai_runs r
  JOIN ai_show s ON s.id = r.show_id
  LEFT JOIN ai_mentions m ON m.run_id = r.id
  WHERE r.status = 'completed'
    AND r.created_at >= NOW() - INTERVAL '30 days'
  GROUP BY r.id
  HAVING COUNT(m.id) = 0
) x;
```

Add a WHY comment above it (principles.md: comment the why) explaining both the show
scope and the window, same style as the `declared_empty_runs` comment just below it.

**Spec correction:** the plan doesn't say *which* show(s) to scope to. I scoped to
ai-daily-brief only, matching the function's name and its sibling query in the same
function — not "all entity/media shows." If PCHH/Hard Fork/Gabfest zero-mention runs
should also be caught, that's a broader, separate change (new/renamed check) — flag it
for Kevin rather than silently widening scope here.

**Tests (hermetic, `tests/test_data_health.py`):** two existing tests stub `dh._one`
with a catch-all default (`test_extraction_integrity_no_longer_double_reports_the_race`
line 209, `test_extraction_integrity_ignores_declared_empty_episodes` line 327) — they
don't pattern-match the zero_mention_runs SQL text, so they're unaffected by the
rewrite. Nothing today exercises the zero_mention_runs branch going to `fail`. Add:
- `test_extraction_integrity_flags_recent_zero_mention_runs` — dispatch `fake_one` on
  `"HAVING COUNT(m.id) = 0"` in the flattened SQL, return `{"count": 1}`, assert
  `status == "fail"` and the detail string mentions "zero mentions".
- Pin the shape the way line 346 already pins `declared_empty_runs`'s SQL: assert the
  zero-mention SQL contains both `"ai_show"` (or `JOIN ai_show`) and `"30 days"`.

---

## (b) `check_optional_null_map` — take it out of the alerting list

**Today:** `pipeline/data_health.py:886-916`. The function is hardcoded:

```python
return CheckResult(
    "optional_null_map", "pass",
    "Optional/null-prone episode fields mapped for human review.",
    details,
)
```

Status is a literal `"pass"` — there is no code path in this function that can ever
produce `"fail"` or `"warn"`. It sits in `run_checks()` at `pipeline/data_health.py:932`
— **"the alerting list"** is `run_checks()`'s returned list, which both callers reduce
to fail/warn sets:
- `pipeline/data_health.py:1007` — `main()`: `failed = [r for r in results if r.status == "fail"]`, feeds `post_slack` and `--strict`'s exit code.
- `pipeline/pulse_report.py:275,183-184` — `pulse_report.main()` calls `run_checks(conn)`, then `build_digest()` computes `fails`/`warns` the same way; only fail/warn entries ever appear in the digest.

Because the status can only ever be `"pass"`, this check can never appear in either
list — it contributes zero signal to alerting while still running a per-show
`COUNT(*) FILTER (...)` over the full `episodes` table on *every* daily health run and
*every* biweekly pulse run, for no purpose beyond the CLI's own human-readable/JSON
dump (its `details` — the actual per-show null counts — are for a human to eyeball).

**Design:** remove it from `run_checks()` (line 932); call it directly in
`data_health.py:main()` and append to `results` after `run_checks()` returns, so the
CLI `render_text`/`--json` output is unchanged for a human reading it, but
`pulse_report.py` stops paying for a query whose result it can never use:

```python
# pipeline/data_health.py — run_checks(): drop check_optional_null_map(conn) from the list

# pipeline/data_health.py — main():
results = run_checks(conn, include_feed_check=True)
# Always "pass" by construction (a per-show null map for human review, not a
# pass/fail signal) — appended to the printed/JSON report only, never to
# run_checks(), so it never occupies a slot in the fail/warn list that drives
# Slack alerting or the pulse digest.
results.append(check_optional_null_map(conn))
```

Leave the `--feed-check-only` branch (line 988-992) untouched — it never included
`check_optional_null_map` (its `results` list was always just the feed check).

**Tests:** no existing test pins `check_optional_null_map` inside `run_checks()`
(confirmed by grep), so removing it breaks nothing today. Add, mirroring the existing
pattern at `tests/test_data_health.py:427-433` (`test_sponsor_share_is_in_the_standard_check_set`):

```python
def test_optional_null_map_is_not_in_the_alerting_list() -> None:
    """It can only ever return 'pass' — it belongs in the human-readable report, not
    in the list pulse_report.py and main()'s Slack alert reduce to fail/warn."""
    import inspect
    from pipeline import data_health
    assert "check_optional_null_map" not in inspect.getsource(data_health.run_checks)
```

---

## (c) `extract_entities.py` — fabricated `0.5` confidence → `NULL`

**Today (line numbers after the 2026-09-02 sponsor-block/ads-as-data edits — the
plan's `:547,551` citation is stale by ~37 lines; confirmed via `git show d4eebf8~1`
that pre-edit lines 547/551 are this exact block):**

```
pipeline/scrapers/ai_daily/extract_entities.py:584-589  (sanitize_mention)
    confidence = mention.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
```

This is the field that matters: `ai_mentions.confidence` (DB column,
`pipeline/scrapers/ai_daily/sql/001_ai_entity_schema.sql:78`, `NUMERIC(5,4)`, **already
nullable, no CHECK constraint** — no migration needed) and the field the eval's
confidence-contract gate inspects. When the model omits `confidence` (or returns
something non-numeric), today's code silently writes `0.5` — a fabricated
"we're exactly half-sure" that is indistinguishable from a model-stated `0.5`.

There is a second, textually identical fabrication one block up, in `sanitize_fact`
(`extract_entities.py:539,543`), for the `confidence` sub-field inside each mention's
`facts[]` array. **This one is out of scope for this item**: it isn't a DB column
(lives only inside the `facts` JSONB blob), nothing downstream reads it numerically
(`load_entity_batch.py:437-445 derive_tags()` only reads `fact_key`/`fact_value`), and
it isn't gated by the eval. Flagging it so the implementer can decide, not silently
fixing it — same fabrication pattern, lower stakes, and fixing it would touch a pinned
test (`tests/test_extract_entities.py:142`) that isn't otherwise in scope.

**Design — `sanitize_mention` (lines 584-589):**

```python
confidence_raw = mention.get("confidence")
if confidence_raw is None:
    confidence = None
else:
    try:
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    except (TypeError, ValueError):
        confidence = None

if confidence is None:
    needs_review = True
    if review_reason is None:
        review_reason = "missing_confidence"
elif confidence < confidence_review_threshold:
    needs_review = True
    if review_reason is None:
        review_reason = "low_confidence"
```

An unknown confidence is inherently review-worthy — same treatment as low confidence,
distinct reason so it's traceable — rather than crashing on `None < threshold`.

**Three downstream sites that must accept `None` (this is the "make sure the loader,
the eval, and the confidence-in-[0,1] gate accept NULL" part of the ask):**

1. **CSV export — will crash without a fix.** `extract_entities.py:1333` and `:1348`
   both do `f"{mention['confidence']:.4f}"` — `:.4f` on `None` raises `TypeError`
   immediately once confidence can be `None`. Change both to:
   ```python
   "confidence": f"{mention['confidence']:.4f}" if mention["confidence"] is not None else "",
   ```
   (empty cell, matching the loader's existing convention for `sponsor_source` at
   line 1332 and its own read-side handling below).

2. **Loader — already correct, no change needed.** `pipeline/scrapers/ai_daily/load_entity_batch.py:476`:
   `confidence = float(row["confidence"]) if row["confidence"] else None` — an empty
   CSV cell already becomes SQL `NULL`. This line was written defensively before any
   caller could actually produce an empty cell; it now gets exercised for real.

3. **Eval confidence-in-[0,1] gate — currently *fails* on any missing value; must
   accept it.** This is the one that will break CI on the very first honestly-NULL
   extraction if left unchanged:

   ```
   evals/extraction/metrics.py:204-224  confidence_report()
       values = [_as_float(m.get("confidence"), None) for m in mentions]
       present = [v for v in values if v is not None]
       out_of_range = [v for v in present if v < 0.0 or v > 1.0]
       n_missing = sum(1 for v in values if v is None)
       return {
           ...
           "all_in_range": not out_of_range and n_missing == 0,   # <- missing = failure today
           ...
       }
   ```
   ```
   evals/extraction/run_eval.py:189-190,251-255  build_report() / check_floors()
       conf_out = sum(c["n_out_of_range"] + c["n_missing"] for c in confs)   # <- both lumped together
       ...
       if not report["confidence"]["all_in_range"]:
           breaches.append(f"{...['n_out_of_range_or_missing']} confidence value(s) out of [0,1] or missing")
   ```
   Change `confidence_report` so `all_in_range` is `not out_of_range` only —
   `n_missing` stays a reported, non-gating field (same treatment the function already
   gives "degenerate distribution" — surfaced, not failed). Change `check_floors`'s
   `conf_out` to sum only `n_out_of_range` (drop `+ c["n_missing"]`), and reword the
   breach message to "outside [0,1]" (no longer "or missing," since missing is no
   longer a breach). Update `print_report`'s matching line
   (`evals/extraction/run_eval.py:302-303`) the same way.

   **This is a deliberate contract loosening, not an oversight** — flag it explicitly
   per `docs/principles.md` ("never loosen a check without a grace window and a test").
   The grace window here is logical, not time-based: `n_missing` was only ever `0` in
   practice before this fix (the sanitizer always fabricated `0.5`), so the eval gate's
   "missing = failure" branch was dead code that would fire on *every* run the moment
   NULLs become real. A test must pin the new behavior so nobody re-tightens it by
   accident (below). The gate that remains — out-of-range values — is the one that was
   ever catching a real sanitizer bug.

**Tests — pinned tests that must change, plus new ones (found by running the exact
old behavior through grep, not guessed):**

`tests/test_extract_entities.py`:
- `test_sanitize_mention_clamps_confidence_to_unit_interval` (line 147-150): line 150
  `assert sanitize_mention(_mention(confidence="nope"), 1, 0.4)["confidence"] == 0.5`
  → change to `is None`.
- `test_sanitize_mention_confidence_always_in_unit_interval` (line 177-181): the loop
  `for value in [-5, 0, 0.5, 1, 99, "x", None]: ... assert 0.0 <= out["confidence"] <= 1.0`
  will **crash** (not just fail) once `"x"`/`None` produce `confidence = None` —
  `0.0 <= None` raises `TypeError` in Python 3. Split into two tests: valid numerics
  (`[-5, 0, 0.5, 1, 99]`) keep the unit-interval assertion; `["x", None]` get their own
  test asserting `out["confidence"] is None and out["needs_review"] is True`.
- Add `test_sanitize_mention_missing_confidence_becomes_null_and_flagged` — mention
  dict with no `"confidence"` key at all → `confidence is None`,
  `review_reason == "missing_confidence"`.
- `test_sanitize_fact_clamps_confidence_and_requires_key` (line 140-142) — **unchanged**,
  since `sanitize_fact` is out of scope (see above). Leave it pinning the `0.5`
  fabrication there; note this explicitly in the PR description so it doesn't read as
  an oversight.

`tests/test_eval_metrics.py`:
- `test_confidence_missing_value_fails` (line 196-199) — currently asserts
  `all_in_range is False`. Rename to `test_confidence_missing_value_is_reported_not_failed`
  and flip: `assert r["all_in_range"] is True` and keep `assert r["n_missing"] == 1`.
- `test_confidence_out_of_range_fails` (line 190-193) — unchanged, still the real gate.
- Add a mixed case: `confidence_report([{"confidence": 0.9}, {}, {"confidence": 1.5}])`
  → `all_in_range is False` (the out-of-range one, not the missing one),
  `n_out_of_range == 1`, `n_missing == 1` — proves the two are now independent.

No existing test imports `evals/extraction/run_eval.py`'s `check_floors`/`build_report`
directly (confirmed by grep — only `tests/test_intake_eval.py` has its own unrelated
`check_floors` for the intake-v2 eval). Recommend one new light test there too if the
implementer wants `run_eval.py`'s aggregation covered, but it's not currently tested at
all, so not a regression risk either way.

---

## (d) Taddy importer — per-episode dedup fallback key

**Today:** `pipeline/scrapers/taddy/import_transcripts.py:275-291`:

```python
def episode_url_key(episode: dict[str, Any]) -> str:
    uuid = episode.get("uuid")
    if uuid:
        return f"https://api.taddy.org/podcast-episode/{uuid}"
    return (
        episode.get("websiteUrl")
        or episode.get("audioUrl")
        or episode.get("guid")
        or "unknown-episode"
    )
```

`episodes.url` is `UNIQUE` (this is the constraint sql/007 recently touched — see the
repo's most recent commit, `episodes_url_key`). When an episode has *none* of
uuid/websiteUrl/audioUrl/guid (malformed Taddy payload — rare, but the function exists
specifically because Hard Fork's generic per-show `websiteUrl` already proved Taddy
data can be degenerate), every such episode from *every* show falls back to the same
literal string `"unknown-episode"`. The upsert at
`pipeline/scrapers/taddy/import_transcripts.py:319-334` runs `ON CONFLICT` semantics
keyed on this value (via the earlier `ORDER BY id LIMIT 1` dedup lookup and the
`episodes.url` unique index), so a second such episode silently **collapses onto the
first row** instead of getting its own episode — data loss with no error, exactly the
class of bug this phase is about.

Called from two places, both of which already have `show_id` in scope:
- `upsert_episode(conn, show_id, show_slug, series_uuid, episode)` at line 301.
- `find_existing_episode_id(conn, show_id, episode)` at line 407.

**Design:** thread `show_id` into the key so two different malformed episodes at least
don't collide *across* shows, and use the episode's own title + publish date so they
don't collide *within* a show either — both are always present in the Taddy payload
(the schema's own `Untitled Episode` / `epoch_to_date(None)` fallbacks already assume
that, see lines 302, 387, 409):

```python
def episode_url_key(episode: dict[str, Any], show_id: int | None = None) -> str:
    """Stable, per-episode-unique URL key for dedup (episodes.url is UNIQUE).
    ...(existing docstring)...

    Beyond that chain, fall back to a key scoped to THIS show and episode (title +
    publish date) rather than one shared literal — a shared "unknown-episode" fallback
    silently collapsed distinct episodes from different malformed-feed shows onto a
    single row via the URL's UNIQUE constraint, because episodes.url has no
    per-show scoping of its own.
    """
    uuid = episode.get("uuid")
    if uuid:
        return f"https://api.taddy.org/podcast-episode/{uuid}"
    explicit = episode.get("websiteUrl") or episode.get("audioUrl") or episode.get("guid")
    if explicit:
        return explicit
    name = (episode.get("name") or "untitled").strip().lower()
    published = episode.get("datePublished") or "no-date"
    show = show_id if show_id is not None else "no-show"
    return f"taddy-unidentified:{show}:{published}:{name}"
```

`show_id` defaults to `None` so the signature change doesn't break the two existing
"explicit chain" tests that don't pass it. Update both call sites to pass it:
- line 301: `episode_url = episode_url_key(episode, show_id)`
- line 407: `episode_url = episode_url_key(episode, show_id)`

This key is deterministic across re-imports (idempotent — a re-run of the same feed
finds and updates the same row via title+date, doesn't create a duplicate), which
matters because `find_existing_episode_id` already has a title+date fallback path of
its own for old rows (see `tests/test_import_transcripts.py:29` —
`test_find_existing_episode_id_uses_title_date_fallback_for_old_url_rows`) — this new
key composes with that existing safety net rather than fighting it.

**Tests (`tests/test_import_transcripts.py`):**
- Line 26: `assert episode_url_key({}) == "unknown-episode"` → update to assert the new
  shape, e.g. `assert episode_url_key({}, show_id=3).startswith("taddy-unidentified:3:")`.
- Lines 11-25 (uuid preference, explicit-chain fallback) — unchanged; they don't hit
  the new branch.
- Add `test_episode_url_key_scopes_unidentified_fallback_by_show_and_episode`:
  two episodes with no uuid/url/guid, different `name`, same `show_id` → different keys;
  same `name`+`datePublished`+`show_id` called twice → identical key (idempotent); same
  `name`+`datePublished`, different `show_id` → different keys (the cross-show collision
  this fix closes).

---

## (e) `run_new_episodes.run_script` — retryable vs. deterministic failures

**Today:** `pipeline/run_new_episodes.py:81-121`. Every non-zero exit is retried
identically up to `MAX_STEP_RETRIES = 2` times with exponential backoff (`5s, 10s` —
line 112). There is no distinction by exit code:

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
ok = result.returncode == 0
...
if attempt < attempts:
    backoff = 5 * (2 ** (attempt - 1))
    ...
    time.sleep(backoff)
```

**Nowhere in the repo does exit code 2 exist today** (checked every `sys.exit(...)`
call under `pipeline/`) — this is a new convention to introduce, not a bug fix to an
existing one. All 6 scripts `run_script` invokes currently use a single blanket
`except Exception: sys.exit(1)` (or `except ValueError` in one case) with no
retryable/deterministic split at all.

**`run_script` change:**

```python
result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
ok = result.returncode == 0
tail = "" if ok else (result.stderr[-500:] if result.stderr else "(no stderr)")
...
if ok:
    ...
    return True
if result is not None and result.returncode == 2:
    # Deterministic failure (bad config/input/secrets) — retrying reproduces the
    # identical failure. Fail fast instead of burning 2 retries + up to 15s of
    # backoff on a problem only a human or a config/secret change can fix.
    log.error("step failed deterministically (exit 2), not retrying: %s — %s", label, tail)
    print(f"  FAILED ({label}) — deterministic failure, not retrying")
    return False
if attempt < attempts:
    ...unchanged...
```
(`result is not None` guards the `subprocess.TimeoutExpired` branch, where `result`
stays `None` and must keep retrying — a timeout is the canonical transient failure per
the function's own docstring, must not become exit-2-like behavior.)

**Which callees can raise deterministic failures, and what changes in each** — found
by reading each script's argument validation / precondition checks and its top-level
`except` handling, not guessed:

| Step (`run_script` call site) | Script | Deterministic site(s) found | Change |
|---|---|---|---|
| Taddy import — `run_new_episodes.py:278` | `pipeline/scrapers/taddy/import_transcripts.py` | `run():550-558` — missing `TADDY_USER_ID`/`TADDY_API_KEY` (line 553); unknown show slug (line 558). Both raised as `RuntimeError`, **before any network/DB call**, currently uncaught → default exit 1. | Replace both `raise RuntimeError(...)` with `print(..., file=sys.stderr); sys.exit(2)` **inline at those two sites** — not a type-based `except RuntimeError` wrapper, because `RuntimeError` is *also* raised for genuinely transient conditions in the same file (`Taddy GraphQL error` at line 135, exhausted-retry `raise last_error` at line 149) and a blanket catch would wrongly stop retrying real API blips. No `try/except __main__` wrapper needed — everything else keeps today's default (unhandled exception → exit 1). |
| Gabfest RSS import — `run_new_episodes.py:285` | `pipeline/scrapers/gabfest/import_gabfest.py` | None found. `main()` (line 158) has no explicit `raise` at all — every failure mode is `requests.get(...).raise_for_status()` (network, transient) or a DB error. | No change. Note explicitly so it doesn't look skipped by accident. |
| Extraction — `run_new_episodes.py:426` | `pipeline/scrapers/ai_daily/extract_entities.py` | `main():1130-1161` — missing `OPENAI_API_KEY` (line 1138, `RuntimeError`); missing `--episodes-csv` file (line 1149) or `--transcripts-dir` (line 1151), both `FileNotFoundError`; `"No episodes selected"` after filtering (line 1161, `RuntimeError`). A fourth `FileNotFoundError` (line 996, inside `read_episode_inputs`, called from `main()` before any OpenAI call) — a per-episode cached transcript file the orchestrator was supposed to have written is missing, a caching bug that reproduces identically on retry. All four run before any network call. | Same collision problem as Taddy: `RuntimeError` is *also* used for OpenAI HTTP errors (lines 462, 497, 500 — genuinely transient/ambiguous, leave as exit 1). Convert the two `RuntimeError` deterministic sites (1138, 1161) to inline `sys.exit(2)`. `FileNotFoundError` has no other use in this file (verified by grep) — safe to add `except FileNotFoundError as exc: print(...); sys.exit(2)` in the `__main__` block (`extract_entities.py:1438-1446`), ordered before the existing `except Exception`. |
| Load — `run_new_episodes.py:435` | `pipeline/scrapers/ai_daily/load_entity_batch.py` | `get_show_id():107-113` — unknown show slug, `RuntimeError` (only `RuntimeError` raise in the file — no collision risk). `main():552-555` — missing `manifest.json` or `mentions.csv` in the batch dir, both `FileNotFoundError`. All run before any DB write. | Add `except (FileNotFoundError, RuntimeError) as exc: print(...); sys.exit(2)` before the existing `except Exception` in `__main__` (`load_entity_batch.py:693-699`). |
| Normalize aliases — `run_new_episodes.py:576` | `pipeline/scrapers/ai_daily/normalize_aliases.py` | None found — no `raise` anywhere in the file (confirmed by grep); every failure is a DB error. | No change. |
| Notion sync — `run_new_episodes.py:589` | `pipeline/sync_notion.py` | `main():578-586` — two direct `sys.exit(1)` calls (not raises): show has no `notion_database_id` configured (line 580-582); missing `NOTION_TOKEN` env var (line 584-586). Both are config problems, before any Notion API call. | Trivial: change both `sys.exit(1)` → `sys.exit(2)`. No exception-type risk since these are direct exits, not a shared `except` clause. |
| Spotify sync — `run_new_episodes.py:602` | `pipeline/sync_playlist.py` | `sync_show():261-262` — unknown `show_id`, `ValueError` (the only `ValueError` raised in the file). Caught today at `__main__:329-331`: `except ValueError as e: sys.exit(1)`. | Change `sys.exit(1)` → `sys.exit(2)` on that one line. |

**Deliberately not changed (flagged, not fixed, to keep this pass bounded):**
Spotify **auth** failure (expired/invalid cached OAuth token) is the other clearly
deterministic case here — CLAUDE.md's own runbook says the fix is "re-auth locally,
update `SPOTIFY_CACHE_JSON`," i.e. it needs a human, not a retry — but it raises
`spotipy.oauth2.SpotifyOauthError` from inside `get_spotify_client()`
(`pipeline/sync_playlist.py:67`), which isn't caught by any existing `except` clause at
all today (propagates as an unhandled exception, default exit 1). Wiring that
correctly means adding a new except clause AND deciding whether a currently-uncaught,
untested path should get one — out of scope for "smallest correct change matching the
plan's exit-code convention," called out for the implementer/Kevin as a good follow-up.

**Tests (`tests/test_run_new_episodes.py`):** the existing hermetic harness
(lines 151-217) is a drop-in pattern — `monkeypatch.setattr(rne.subprocess, "run", fake_run)`
returning a fake `_Result(returncode=...)`. Add, right after
`test_run_script_gives_up_after_max_retries` (line 173-193):

```python
def test_run_script_does_not_retry_on_deterministic_exit_code(monkeypatch) -> None:
    from pipeline import run_new_episodes as rne

    class _Result:
        returncode = 2
        stdout = ""
        stderr = "bad config"

    calls = {"n": 0}
    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(rne.subprocess, "run", fake_run)
    monkeypatch.setattr(rne.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("should not sleep/retry")))

    assert rne.run_script("x.py", [], dry_run=False, label="step") is False
    assert calls["n"] == 1  # no retry on a deterministic failure
```

Per-callee: for the 4 files where I recommend a mechanical exit-code change with no
new exception-type routing (`sync_notion.py`, `sync_playlist.py`), a one-line assertion
addition to each file's existing CLI/argument test (if one exists) is enough — I did
not find `tests/` coverage of either script's `__main__` exit codes today, so this is
net-new coverage, not a change to a pinned test. For Taddy import and
`extract_entities.py`, where the deterministic checks are now inline `sys.exit(2)`
calls, a subprocess-level assertion is awkward to make hermetic (no DB/network); the
cheaper, still-real test is calling `run()`/`main()` with the relevant env var unset (or
a bad show slug / missing CSV path) inside `pytest.raises(SystemExit)` and asserting
`.code == 2` — consistent with how `tests/test_run_pipeline.py`-style tests already
exercise these scripts' `main()` directly where they do (verify the existing pattern
in-repo before adding; not confirmed present for these two files, so may be new).

---

## Cross-cutting notes for the implementer

- **Order of operations matters for (c):** ship the `sanitize_mention` NULL fix and
  the CSV-format fix in the *same* commit/PR as the eval-gate change — landing the
  data-model change without the gate change breaks CI on the next extraction run;
  landing the gate change alone (without ever writing real NULLs) is a no-op that
  can't be verified.
- **(d) backward-compat check — done, clean.** Ran the read-only check against
  production Neon (2026-09-03): `SELECT id, show_id, title, url FROM episodes WHERE
  url = 'unknown-episode';` → **0 rows.** No existing episode is sitting on the old
  fallback literal, so changing the key format has no migration to worry about.
- **(a) and (b) are both `data_health.py` edits in the same function/module** — land
  them together, one PR, to avoid two small diffs touching adjacent lines twice.
- None of (a)-(e) touch production data or require Kevin's per-op DB sign-off (no
  `ALTER`/`DELETE`/`DROP`) — they're all application-code + test changes, except the
  one read-only verification query called out for (d) above.

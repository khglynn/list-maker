# ITEM 3 — Transactional batch load + run-completeness check

Scope: `pipeline/scrapers/ai_daily/load_entity_batch.py`, `pipeline/run_new_episodes.py` (read-only,
no change needed there), `pipeline/data_health.py`. Read-only investigation — no edits made.

## What exists today

### The bug, precisely

`insert_run()` (`load_entity_batch.py:168-193`) does ONE `INSERT ... VALUES (..., %s, NOW(), NOW(), NOW())`
with `status` defaulting to `'completed'`, followed immediately by `conn.commit()` (line 192). In
`main()` this is called at **line 606-618**, *before* the mentions loop at line 629 even starts. So the
run row is durably `status='completed'` in Neon before a single `ai_mentions` row exists.

Worse: every row-level write inside the loop also commits immediately and independently:
- `upsert_entity()` (`load_entity_batch.py:302-361`) — `conn.commit()` at 344 (update branch) and 360
  (insert branch).
- `insert_mention()` (`load_entity_batch.py:464-541`) — `conn.commit()` at 541.
- `record_first_seen_as_ad()` (`load_entity_batch.py:364-400`) — `conn.commit()` at 399, called in the
  second pass over `sponsor_stamps` (lines 670-672).

Every row is therefore its own separate, already-durable transaction (psycopg2 default is
`autocommit=False`, so each `conn.commit()` closes one transaction and opens the next — see
`pipeline/common.py:95-123`, `get_db_connection()`, no `autocommit=True` set anywhere).

**Failure mode this produces:** if the Python process dies partway through the `for row in rows:` loop
(OOM, host reboot, killed subprocess) — say after mention 8 of 20 — the `ai_runs` row is already
`status='completed'` (committed at the top) and 8 of 20 `ai_mentions` rows are already committed. The
other 12 never land, and nothing marks the run as incomplete. This is the "mid-batch crash undercount"
named in `DEVLOG.md`'s 2026-09-01 entry.

**Why it's invisible today, specifically:**
1. `data_health.check_ai_daily_extraction`'s `zero_mention_runs` sub-check (`data_health.py:628-644`)
   only catches **zero**-mention completed runs (`HAVING COUNT(m.id) = 0`). A partial load (8 of 20)
   has a nonzero count and passes clean.
2. `find_unextracted_episodes()` (`run_new_episodes.py:124-200`) treats an episode as "already
   extracted" purely on `ep.id NOT IN (SELECT DISTINCT m.episode_id FROM ai_mentions m)` (line 180-182)
   — it does not look at `ai_runs.status` at all. So **which episodes get silently skipped forever
   depends on CSV row order**, not on anything meaningful: an episode whose mentions happened to be
   inserted before the crash is (wrongly) considered "done" and is never retried; an episode whose
   mentions hadn't been reached yet is (correctly, by luck) retried on the next run. A 5-episode batch
   with mentions spread unevenly across episodes can end up permanently under-counted for exactly the
   episodes that happened to load first.
3. `run_script()` retries (see below) don't help this case: the retry only fires when the **subprocess
   itself returns nonzero or times out**. A crash that kills the whole orchestrator process (not just
   the loader subprocess) means there's no in-run retry at all — the damaged `completed` row is the
   final state until someone notices by hand.

### `delete_existing_run` / idempotency (already correct, keep as-is)

`delete_existing_run()` (`load_entity_batch.py:254-279`) deletes every `ai_runs` row (and, via
`ai_mentions.run_id ON DELETE CASCADE`, every `ai_mentions` row) for the `(show_id, batch_name)` key,
**unconditionally on status**, before every load (called at line 227 in `record_empty_batch` and line
600 in `main()`). This is the thing that already makes *retries* safe: a second invocation of
`load_entity_batch.py main()` for the same `batch_name` wipes whatever the previous attempt left
(complete, partial, or `'loading'`) and starts clean. **No change needed here.** This also means the
fix below doesn't need to invent new idempotency — it only needs to stop a *non-retried* crash from
leaving a `completed` row that lies about its mention count.

### `completed_empty` outcome (PR #23) — already correct, keep as-is

`record_empty_batch()` (`load_entity_batch.py:199-251`) is inherently atomic already: it's a single
`insert_run(..., status=EMPTY_RUN_STATUS)` call with no loop after it (called from `main()` at line
571 when `rows` is empty). `EMPTY_RUN_STATUS = "completed_empty"` (line 196) is deliberately a
different status than `'completed'` specifically so `zero_mention_runs` and `find_unextracted_episodes`
don't treat a declared "nothing worth storing" as a bug. Nothing here needs to change for Item 3.

### `run_new_episodes.py` — the retry wrapper

`run_script()` (`run_new_episodes.py:81-121`) runs a script as a subprocess with up to
`MAX_STEP_RETRIES = 2` retries (3 attempts total, line 51/92) and exponential backoff (`5 * 2**(attempt-1)`
seconds, line 112). `extract_and_load_batch()` (`run_new_episodes.py:395-438`) calls `run_script` twice,
independently: once for `extract_entities.py` (line 426, `timeout=900`), once for
`load_entity_batch.py` (line 435, default `timeout=600` since no `timeout=` kwarg is passed).

Each retry of the **load** step is a brand-new `python load_entity_batch.py` process — a fresh
`main()`, fresh connection, and (per the idempotency note above) it starts by deleting whatever the
previous attempt left. So **a retried load is already safe under today's code** — the real gap is a
crash that isn't retried at all (whole-process death) or a final attempt that fails after already
having committed partial rows and is not retried again (`step_entity_extraction` at
`run_new_episodes.py:471-490` just logs `total_ok = False` and moves to the next batch — it never calls
`delete_existing_run` as cleanup on give-up).

## Design: the smallest correct change

Make the load one atomic unit — everything from "who does this batch belong to" through "all mentions
landed" commits together, or none of it does. Combine both variants named in the spec:

1. **Write the run row as `'loading'` first, its own commit** (so there's a visible, queryable signal
   that a batch is in flight — useful for the new stuck-run check below).
2. **Do every entity upsert + mention insert + the final status flip to `'completed'` inside one
   uncommitted transaction, with exactly one `conn.commit()` at the end.** A crash anywhere in that
   window rolls back everything since the `'loading'` commit — so `ai_mentions` for that run is either
   *complete* or *entirely absent*, never partial.

### Code changes

**A. `insert_run()` — add a `commit: bool = True` parameter; fix `completed_at`.**

Current SQL (`load_entity_batch.py:180-190`) hardcodes `completed_at = NOW()` unconditionally, even
though the row may not represent a completed run yet. Make `completed_at` a bound parameter instead of
inline `NOW()`, computed in Python as `None if status == "loading" else datetime.now(timezone.utc)`.
Guard the trailing `conn.commit()` (currently unconditional, line 192) behind the new flag.

```python
def insert_run(
    conn, *, show_id, batch_name, model, prompt_version, parameters,
    status: str = "completed", commit: bool = True,
) -> int:
    completed_at = None if status == "loading" else datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_runs (
              show_id, batch_name, run_type, provider, model, prompt_version,
              parameters, status, started_at, completed_at, created_at
            )
            VALUES (%s, %s, 'entity_extraction', 'openai', %s, %s, %s::jsonb,
                    %s, NOW(), %s, NOW())
            RETURNING id;
            """,
            (show_id, batch_name, model, prompt_version, json.dumps(parameters),
             status, completed_at),
        )
        row = cur.fetchone()
    if commit:
        conn.commit()
    return int(row["id"])
```

*Existing call sites unaffected in behavior:* `record_empty_batch` (line 230) and `main()`'s empty-batch
path pass no `status="loading"`, so `completed_at` stays `NOW()` exactly as today. **Test impact:**
`tests/test_load_entity_batch.py:258-264` (`test_insert_run_defaults_to_completed`) currently asserts
`params[-1] == "completed"`; adding `completed_at` as a new trailing bound param shifts `status` off the
last position. Update that assertion to `params[5] == "completed"` (0-indexed: show_id, batch_name,
model, prompt_version, parameters_json, status, completed_at) and add
`assert params[6] is not None` to the same test. Add a new test asserting `status="loading"` produces
`params[6] is None`.

**B. `upsert_entity()`, `insert_mention()`, `record_first_seen_as_ad()` — add `commit: bool = True`.**

Same pattern: guard each function's existing `conn.commit()` call
(`load_entity_batch.py:344`/`360`, `541`, `399`) behind the new flag, default `True` so every other
caller (tests, any hand-invocation) is unaffected.

**C. New `finalize_run_completed()` function.**

```python
def finalize_run_completed(conn, run_id: int, *, commit: bool = True) -> None:
    """Flip a 'loading' run to 'completed' once every entity + mention has landed.
    This is the LAST statement of the batch transaction — its commit is what makes
    entities, mentions, and the run's completed status appear atomically together."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ai_runs SET status = 'completed', completed_at = NOW() WHERE id = %s;",
            (run_id,),
        )
    if commit:
        conn.commit()
```

**D. Extract the per-batch loop into a testable, commit-free function.**

Currently the loop lives inline in `main()` (lines 620-672), which also parses argv and touches the
filesystem — untestable as a unit (confirmed: no existing test calls `main()`). Pull it out:

```python
def load_batch_rows(
    conn, *, run_id: int, rows: list[dict], transcript_map: dict[int, int | None],
    publish_dates: dict[int, Any],
) -> tuple[int, int, int, int, dict]:
    """Insert every entity + mention for one batch. Never commits — the caller commits
    once, atomically with the run's status flip to 'completed' (see finalize_run_completed).
    Returns (mention_inserted, review_open, sponsor_inserted, first_seen_as_ad, entity_cache)."""
    mention_inserted = review_open = sponsor_inserted = first_seen_as_ad = 0
    entity_cache: dict[tuple[str, str, str], int] = {}
    sponsor_stamps: list[tuple[int, Any]] = []

    for row in rows:
        entity_type = normalize_entity_type(row["entity_type"])
        canonical_name = row["canonical_name"].strip()
        mention_text = row["mention_text"].strip()
        platform = row["platform"].strip() or None
        key = (entity_type, normalize_name(canonical_name), platform or "")

        entity_id = entity_cache.get(key)
        if entity_id is None:
            entity_id = upsert_entity(
                conn, entity_type=entity_type, canonical_name=canonical_name,
                platform=platform,
                source_alias=mention_text if mention_text != canonical_name else None,
                commit=False,
            )
            entity_cache[key] = entity_id

        insert_mention(conn, run_id=run_id, transcript_map=transcript_map, row=row,
                        entity_id=entity_id, commit=False)
        mention_inserted += 1
        if row["needs_review"].strip().lower() == "true":
            review_open += 1
        if normalize_sponsor_source(row.get("sponsor_source")):
            sponsor_inserted += 1
            sponsor_stamps.append((entity_id, publish_dates.get(int(row["episode_id"]))))

    for entity_id, publish_date in sponsor_stamps:
        if record_first_seen_as_ad(conn, entity_id, publish_date, commit=False):
            first_seen_as_ad += 1

    return mention_inserted, review_open, sponsor_inserted, first_seen_as_ad, entity_cache
```

**E. `main()`'s non-empty path (currently lines 592-689) becomes:**

```python
episode_ids = sorted({int(r["episode_id"]) for r in rows})
transcript_map, inferred = resolve_transcript_map(conn, episode_ids, provenance)
# ...(warning print unchanged)...
removed_runs = delete_existing_run(conn, show_id=show_id, batch_name=batch_name)  # own commit, unchanged
# ...(print unchanged)...
run_id = insert_run(
    conn, show_id=show_id, batch_name=batch_name, model=model,
    prompt_version=args.prompt_version, status="loading",
    parameters={
        "batch_dir": str(batch_dir), "episodes": episode_ids,
        "source": "extract_entities.py",
        "expected_mentions": len(rows),  # <-- what the health check compares against
        "loaded_at_utc": datetime.now(timezone.utc).isoformat(),
    },
)  # commit=True (default) — the 'loading' row is immediately visible

publish_dates = get_episode_publish_dates(conn, episode_ids)
try:
    mention_inserted, review_open, sponsor_inserted, first_seen_as_ad, entity_cache = (
        load_batch_rows(conn, run_id=run_id, rows=rows, transcript_map=transcript_map,
                         publish_dates=publish_dates)
    )
    finalize_run_completed(conn, run_id, commit=False)
    conn.commit()  # the ONE commit: every entity, every mention, and status='completed' land together
except Exception:
    conn.rollback()
    raise

# ...(print block unchanged, using the returned counts)...
```

If anything raises inside the `try` (a bad row, a DB error, a `KeyboardInterrupt`, the process being
killed), `conn.rollback()` discards every uncommitted entity/mention write; the run row is left at
`status='loading'` (committed earlier, untouched) with **zero** mentions ever visible for it. A process
that's simply killed outright (no chance to run the `except` block) leaves the same result — the
server-side connection close aborts the open transaction. Either way: no partial `ai_mentions` for that
`run_id` can ever exist while its run says `'completed'`.

### Why this fixes the silent-skip problem specifically

Because a crash can now only ever produce **zero** mentions for the batch (never a partial count), every
episode in that batch is still absent from `ai_mentions` afterward — so
`find_unextracted_episodes()` (`run_new_episodes.py:180-182`) correctly considers *all* of them
unextracted on the next run, not a lucky subset. The CSV-row-order dependence in today's bug disappears
entirely; there is no longer a "some episodes happened to load before the crash" case to have.

### Retry safety (unchanged, verified)

A retry (either `run_script`'s in-process retry, or tomorrow's cron picking the episode up again because
it has no mentions) re-invokes `main()` fresh. `delete_existing_run` at the top wipes whatever's there —
a `'loading'` row with zero mentions, or nothing at all — before the fresh `insert_run(..., status="loading")`.
No new idempotency work required.

## Health check: run-completeness

### Where the evidence lives

`extract_entities.py` writes `mentions.csv` (the rows `load_entity_batch.py` reads) and
`batch_manifest.json` with a `filter_summary.kept` count
(`extract_entities.py:940` `stats["kept"] = len(out)`, aggregated into `filter_totals` and written at
`extract_entities.py:1395`). Both live in the **gitignored** batch dir under
`codex-notes/ai-daily-entity-extraction/` (`pipeline/_cache`/`codex-notes` per the repo's folder-structure
notes) — CI uploads that dir as a workflow artifact, but it is not durable, queryable state. **Neon is
the only durable place this needs to live.**

Rather than trust the manifest's `filter_summary.kept` (a second file, indirection, and a potential
drift point), the loader should record what **it itself** read from `mentions.csv` — `len(rows)`,
computed once in `main()` right after the `csv.DictReader` (line 561-562), already present as a local
variable. This is store this in `ai_runs.parameters.expected_mentions` (design step E above). It's the
simplest, most direct "what the CSV said" number, sourced from the same file, in the same process, as
the thing that gets compared against it — no manifest schema dependency.

### New checks in `pipeline/data_health.py`

**`check_ai_run_completeness(conn)`** — new function, registered in `run_checks()`
(`data_health.py:919-938`, add after `check_ai_daily_extraction` at line 927):

```python
def check_ai_run_completeness(conn) -> CheckResult:
    """Did every 'completed' run actually load as many mentions as its CSV had?

    expected_mentions is written once, at load time, from len(rows) read from
    mentions.csv (see load_entity_batch.main). Older runs loaded before this field
    existed have no expected_mentions key and are intentionally skipped here — there
    is no honest number to compare them against, and flagging them would just be
    retroactive noise on the day this check ships.
    """
    rows = _rows(
        conn,
        """
        WITH run_counts AS (
          SELECT r.id AS run_id, s.slug, r.batch_name,
                 (r.parameters->>'expected_mentions')::int AS expected_mentions,
                 COUNT(m.id) AS actual_mentions
          FROM ai_runs r
          JOIN shows s ON s.id = r.show_id
          LEFT JOIN ai_mentions m ON m.run_id = r.id
          WHERE r.status = 'completed'
            AND r.parameters ? 'expected_mentions'
          GROUP BY r.id, s.slug, r.batch_name, r.parameters
        )
        SELECT * FROM run_counts WHERE actual_mentions <> expected_mentions
        ORDER BY run_id;
        """,
    )
    if not rows:
        return CheckResult(
            "ai_run_completeness", "pass",
            "Every completed AI run loaded as many mentions as its CSV had.", [],
        )
    details = [
        f"{r['slug']} run {r['run_id']} ({r['batch_name']}): "
        f"expected {r['expected_mentions']}, has {r['actual_mentions']}"
        for r in rows
    ]
    return CheckResult(
        "ai_run_completeness", "fail",
        f"{len(rows)} completed AI run(s) loaded fewer (or more) mentions than their CSV had.",
        details,
    )
```

No warn tier: a `'completed'` run whose mention count doesn't match its own CSV is unambiguously wrong,
not "still in progress" (that state is `'loading'`, covered by the check below). Fail is correct.

**`check_ai_run_stuck_loading(conn)`** — new function, catches the other half: a run that never made it
to `'completed'` at all (crash left it in `'loading'`) and — because of design step A — never claimed a
false `completed_at`, so `started_at` is the only honest age signal:

```python
AI_RUN_LOADING_WARN_MINUTES = 10  # a normal batch load is pure-DB work over <=5 episodes'
AI_RUN_LOADING_FAIL_MINUTES = 30  # worth of CSV rows — seconds, not minutes, in the healthy case.
# 30min is comfortably past run_script's own retry envelope for the load step (up to 3
# attempts x 600s timeout + backoff ≈ 30min worst case) — past that, the orchestrator
# process itself is gone, not merely slow.

def check_ai_run_stuck_loading(conn) -> CheckResult:
    """Is any batch load stuck mid-transaction — a crash that never reached 'completed'?

    Mirrors check_transcript_race_selfheal's warn-then-fail-by-age shape: a 'loading'
    row a few minutes old is very likely still in flight (or one retry attempt away from
    finishing); one older than the load step's own retry envelope is abandoned.
    """
    rows = _rows(
        conn,
        """
        SELECT r.id AS run_id, s.slug, r.batch_name,
               EXTRACT(EPOCH FROM (NOW() - r.started_at)) / 60 AS minutes_pending
        FROM ai_runs r
        JOIN shows s ON s.id = r.show_id
        WHERE r.status = 'loading'
        ORDER BY r.started_at;
        """,
    )
    if not rows:
        return CheckResult("ai_run_stuck_loading", "pass", "No batch load is stuck mid-transaction.", [])

    stuck = [r for r in rows if r["minutes_pending"] > AI_RUN_LOADING_FAIL_MINUTES]
    details = [
        f"{r['slug']} run {r['run_id']} ({r['batch_name']}): "
        f"{r['minutes_pending']:.0f}m in 'loading'"
        for r in rows
    ]
    if stuck:
        return CheckResult(
            "ai_run_stuck_loading", "fail",
            f"{len(stuck)} batch load(s) stuck in 'loading' past {AI_RUN_LOADING_FAIL_MINUTES}m — "
            "abandoned, not in progress.",
            details,
        )
    return CheckResult(
        "ai_run_stuck_loading", "warn",
        f"{len(rows)} batch load(s) currently in 'loading'; should clear on the next check.",
        details,
    )
```

Register both in `run_checks()` (`data_health.py:919-938`), next to `check_ai_daily_extraction` /
`check_transcript_race_selfheal`.

## Tests (hermetic — mirror existing patterns exactly)

`tests/test_load_entity_batch.py` uses a `_FakeConn`/`_FakeCursor` pair that records `.calls` and a
`.committed` bool (lines 34-58, and a near-identical pair at 386-397 for the ad-stamp tests) — extend
that pattern rather than inventing a new one.

1. **`test_insert_run_defaults_to_completed`** (existing, line 258-264) — update the assertion index
   (`params[5]` not `params[-1]`) and add `assert params[6] is not None` (completed_at is set).
2. **`test_insert_run_with_loading_status_leaves_completed_at_null`** (new) — call
   `insert_run(..., status="loading", ...)`, assert `params[5] == "loading"` and `params[6] is None`.
3. **`test_insert_run_skips_commit_when_commit_is_false`** (new) — `commit=False`, assert
   `conn.committed is False`.
4. **`test_finalize_run_completed_sets_status_and_commits`** (new) — assert the SQL text contains
   `status = 'completed'`, and `conn.committed` reflects the `commit` kwarg.
5. **`test_load_batch_rows_commits_nothing_itself`** (new, the load-bearing one for the whole fix) — a
   fake cursor/conn that records every `execute()` call and `commit()` call; feed `load_batch_rows` two
   CSV-row dicts; assert `conn.committed is False` throughout and after — pins that entity/mention
   inserts never commit on their own, only the caller does.
6. **`test_load_batch_rows_raises_without_partial_commit`** (new) — a fake cursor whose `execute()`
   raises on the 2nd call (simulating a mid-batch crash after 1 of 2 rows); assert the exception
   propagates AND `conn.committed is False` — pins that a mid-batch failure leaves nothing committed,
   which is the actual bug fix being verified. This is the test that would have caught the original bug.

`tests/test_data_health.py` — extend the existing monkeypatch style (dispatch `_rows`/`_one` by SQL
substring, e.g. `test_extraction_integrity_...` at line 209, `_sponsor_rows` helper at line 353):

7. **`test_run_completeness_passes_when_counts_match`** — `_rows` returns `[]` → `"pass"`.
8. **`test_run_completeness_fails_on_a_mismatched_run`** — `_rows` returns one row with
   `expected_mentions=20, actual_mentions=8` → `"fail"`, detail string names the run/batch/counts.
9. **`test_run_stuck_loading_passes_when_nothing_is_loading`** — `_rows` returns `[]` → `"pass"`.
10. **`test_run_stuck_loading_warns_under_the_fail_threshold`** — one row at `minutes_pending=5` →
    `"warn"`.
11. **`test_run_stuck_loading_fails_past_the_threshold`** — one row at `minutes_pending=45` → `"fail"`.
12. **`test_both_new_checks_are_in_the_standard_check_set`** — mirrors
    `test_sponsor_share_is_in_the_standard_check_set` (line 427): assert both new `CheckResult.name`
    values appear in `[r.name for r in dh.run_checks(conn=None, ...)]`-style enumeration (whatever the
    existing test's exact mechanism is — read it first, line 427-onward, and match it).

None of these touch Neon or the network — same hermetic contract as the rest of `tests/`.

## Risks

- **`test_insert_run_defaults_to_completed` is a real, disclosed break**, not collateral damage —
  `insert_run`'s parameter tuple genuinely gains a new trailing bound value. Fix the assertion in the
  same PR, don't work around it by reordering SQL columns just to preserve `params[-1]`.
- **Historical `ai_runs` rows have no `expected_mentions`** — `check_ai_run_completeness`'s
  `AND r.parameters ? 'expected_mentions'` guard is what keeps every pre-existing row from flagging on
  day one (per `docs/principles.md`'s "never loosen a check without a grace window" instinct, applied
  here to a *new* check rather than a loosened one: don't retroactively judge data written under a
  different contract). Confirm this guard survives review — it's the one line standing between "clean
  rollout" and "the pulse Slack message goes red for every historical run the moment this ships."
- **Batch size bounds the transaction size** — `EXTRACTION_BATCH_SIZE = 5`
  (`run_new_episodes.py:392`) means at most a few dozen mention rows per transaction; a single
  uncommitted transaction of that size for a few seconds is not a realistic risk for Neon connection/
  statement timeouts. Worth a one-line sanity check against `common.py`'s connection timeout config if
  the implementer wants belt-and-suspenders, but not expected to matter.
- **Two related Phase 4 bullets are explicitly OUT of scope for Item 3** (same plan section,
  `claude-plans/2026-09-01-ground-it-cleanup-plan.md:76,78`): "`zero_mention_runs` gets a show filter
  and a rolling window" and "`run_script` distinguishes retryable from deterministic failures (exit
  code 2 = don't retry)." Don't fold either into this change — flag them as separate follow-ups if the
  implementer notices the overlap.
- **Pre-existing, not introduced or worsened by this change:** a theoretical race if two processes ever
  loaded the same `batch_name` concurrently (`delete_existing_run` + insert has a TOCTOU gap). Batches
  are named by episode-id range and the orchestrator runs sequentially per show, so this isn't realistic
  today; not part of Item 3's fix.

## Spec corrections / things the prompt implies that aren't quite right

- The spec's phrasing "the completed_empty outcome from PR #23" and "decision 10's schema items" are
  correctly marked out of scope in the task prompt and confirmed independent in this investigation —
  `record_empty_batch` needs no change, and no `sql/0NN` migration is required for this item (`ai_runs.status`
  is a plain `VARCHAR(32)` with no `CHECK` constraint — see `sql/001_ai_entity_schema.sql:13` — so adding
  the `'loading'` status value is a pure application-code change, not a schema change).
- The task description says "what a crash between them leaves" (between `insert_run` and mentions
  landing) — worth being precise that there are actually **two** distinct crash windows in the current
  code, not one: (1) between the early `insert_run(status='completed')` commit and the first mention
  commit — leaves a `completed` run with **zero** mentions, which `zero_mention_runs` already catches
  today; (2) mid-loop, after some mentions have already individually committed — leaves a `completed`
  run with a **nonzero but wrong** mention count, which nothing catches today and is the actual gap this
  item closes. The design above collapses both windows into one (everything is either fully committed
  or not committed at all), so the distinction disappears post-fix, but it's the reason `zero_mention_runs`
  alone was never going to be sufficient and a new check is genuinely needed.

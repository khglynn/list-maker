# Item 5 — Acceptance and the test harness (Phase 4)

Scope: map the plan's three acceptance lines to concrete hermetic tests, describe the
existing fixture/mocking patterns so new tests match them, and audit every current
`data_health.py` check for whether its FAIL is actionable today.

## 1. The hermetic test patterns that already exist

All three languages/surfaces in this repo test the same way: **fake the boundary
object, assert on what was sent to it** — never a real DB, HTTP call, or subprocess.
CI proves this is true (`.github/workflows/test.yml`: no `env:` block, `pytest -q` runs
with no `DATABASE_URL`/`.env.local` present).

### Pattern A — module-level monkeypatch of `_rows`/`_one` (data_health.py checks)
`tests/test_data_health.py:13-26, 52-134, 230-238`. Every `check_*` function in
`pipeline/data_health.py` does its own querying through two tiny helpers,
`_rows(conn, sql, params)` and `_one(conn, sql, params)` (`data_health.py:117-127`).
Tests `monkeypatch.setattr(dh, "_rows", lambda *a, **k: <canned rows>)` (or `_one`,
dispatched on SQL substring when a check makes more than one query — see
`_patch_notion_freshness`, `test_data_health.py:13-26`) and then call the check with
`conn=None`, since `conn` is never touched once `_rows`/`_one` are faked. This is the
cheapest pattern in the repo and is what any new `data_health.py` check (a
run-completeness check, an identity-based feed check) should use.

### Pattern B — a fake connection/cursor object (load_entity_batch.py, run_new_episodes.py)
Used where the code under test calls `conn.cursor()` / `cur.execute(sql, params)`
directly rather than through `_rows`/`_one`, and the test wants to assert on the raw
SQL and params sent.
- `tests/test_load_entity_batch.py:33-58` (`_FakeCursor`/`_FakeConn`, captures
  `(sql, params)` tuples in `.calls`, tracks `.committed`), `:138-163`
  (`_LookupCursor`/`_LookupConn`, canned `fetchall()` rows), `:216-229`
  (`_RunCursor`/`_RunConn`, canned `fetchone()`), `:364-397`
  (`_RecordingCursor`/`_RecordingConn`, single-statement capture of `.sql`/`.params`).
- `tests/test_run_new_episodes.py:21-47` (`_Cursor`/`_Conn`, captures `.sql`/`.params`
  as attributes, not a list — one query per test), `:219-245` (`_PrepCursor`/`_PrepConn`
  for `prepare_extraction_inputs`, canned `fetchall()` rows keyed by episode).

Every fake conn class is small, local to its test file, and purpose-built (some track
commits, some track SQL text, some canning `fetchone` vs `fetchall`) — there is no
shared `conftest.py` fixture for this. A new hermetic test picks the shape it needs
and writes a 10-20 line fake conn class next to the others in the same file, or reuses
one already in that file if the shape matches.

### Pattern C — monkeypatch the network function directly (feed_check.py)
`tests/test_feed_check.py:36-55`. `feed_recent_dates` calls the module-level function
`taddy_recent_dates` (or `rss_recent_dates`) — tests `monkeypatch.setattr(feed_check,
"taddy_recent_dates", lambda uuid, limit: <canned dates>)`, never touching `requests`.
`data_health.py`'s own tests go one level up and monkeypatch `dh.feed_recent_dates`
itself (`test_data_health.py:100-129, 230-280`) since `check_import_caught_up` imports
it by name (`data_health.py:22`).

### Pattern D — pure-function tests with an injected clock
`test_data_health.py:230-238` (`_feed_check` helper) also monkeypatches `dh._today`
(`data_health.py:95-96`) so grace-window tests control "today" without touching the
system clock — every `_today()` call in the module goes through this one patchable
function, which is why `split_missing_feed_dates` (`data_health.py:99-114`) takes
`today` as an optional parameter rather than calling `date.today()` itself.

### Pattern E — the Worker (`cloudflare-trigger/worker.test.js`)
`node --test cloudflare-trigger/worker.js` (no deps). Today this ONLY tests the pure
function `dispatchesFor(when: Date) -> [{workflow, inputs}]` (`worker.js:47-68`) by
calling it directly and asserting on the returned array (`worker.test.js:10-55`).
**There is no fetch-mocking pattern in this file yet** — `dispatch()`
(`worker.js:90-111`) and `scheduled()` (`worker.js:115-136`) call the real global
`fetch` and are never invoked from the test file at all. Any test of the "alarm on
absence" feature (item 4, see below) will need to establish this pattern for the
first time: stub `globalThis.fetch` before calling the function under test, restore
it after (`node:test`'s `t.mock.method(globalThis, "fetch", ...)` is the idiomatic
way, or a manual save/restore around the call — the file has no precedent either way,
so the implementer should pick one and it becomes the pattern for future Worker tests).

### Pattern F — structural assertion on `inspect.getsource`
`test_load_entity_batch.py:399-425` (`test_first_seen_as_ad_is_stamped_after_the_batch_not_during_it`)
asserts on the *ordering of statements inside `main()`* via `inspect.getsource` and
string-index comparison, because the bug it guards (stamping inline vs. in a second
pass) is about sequencing that a mocked-return-value test can't see. This is the
precedent to follow for the "batch load is transactional" acceptance below, where the
property under test is also "when does X happen relative to Y" rather than "what value
does X return."

## 2. Mapping the plan's acceptance lines to tests

### "a seeded mid-series gap fails the feed check"

**Not proven today, and not true of the current code.** `check_import_caught_up`
(`data_health.py:497-563`) only ever compares `MAX(episodes.publish_date)` against the
feed's dates (`db_latest` from the SQL at `:516-523`; `split_missing_feed_dates` at
`:99-114` computes `missing = [d for d in feed if d > db_latest]`). A gap in the
*middle* of a show's history — an episode the feed has and we don't, older than our
current latest — produces an empty `missing` list and the check reports `pass`
("caught up"). This is exactly the blind spot the plan names (`plan:73`, DEVLOG
2026-09-01: "a re-dated TAL episode inflated a BEHIND count; MAX(publish_date) is
blind to holes mid-series").

Existing tests only exercise the boundary case where the newest feed date is missing
(`test_data_health.py:254-264, 267-280`) or where nothing is missing
(`test_data_health.py:241-251`) — none seeds a hole behind the current latest.

**What has to exist before this acceptance line can be tested:**
1. `feed_check.taddy_recent_dates` (`feed_check.py:54-82`) has to return episode
   identity (the Taddy `uuid`, already requested in the GraphQL query's field list at
   `:62` as `uuid datePublished` but discarded — only `datePublished` survives into
   `_ts_to_date`/the returned date list, `:81`) alongside each date, since
   `episodes.url` is built from that same uuid at import time
   (`pipeline/scrapers/taddy/import_transcripts.py:283-285`,
   `f"https://api.taddy.org/podcast-episode/{uuid}"`) — the uuid *is* the join key
   between "what the feed has" and "what we have."
2. `feed_recent_dates` (`feed_check.py:108-127`) and its RSS sibling need to return
   `(date, identity)` pairs, not bare dates — a real signature change every caller of
   `feed_recent_dates` touches (`check_import_caught_up`, both feed_check tests,
   `pulse_report.py` if it calls this too — check before changing).
3. `check_import_caught_up` needs a set-difference against `episodes.url` (or a new
   `episodes.taddy_uuid` column, cheaper to query if added) instead of the
   `db_latest` date comparison, still passed through the same grace-window split so
   the August false-positive fix (PR #4) isn't undone.

**The test to add**, following Pattern A (`_feed_check` helper,
`test_data_health.py:230-238`, extended to feed `(date, uuid)` pairs and DB rows
carrying urls/uuids instead of a single `db_latest` date):
```python
def test_feed_check_catches_a_mid_series_gap(monkeypatch) -> None:
    # Feed has 3 episodes; we're missing the MIDDLE one even though our latest is newer.
    ...
    result = _feed_check(monkeypatch, known_identities={"sop": {"uuid-A", "uuid-C"}},
                          feed={"sop": [("uuid-C", newest), ("uuid-B", middle), ("uuid-A", oldest)]},
                          today=..., slugs=["sop"])
    assert result.status == "fail"
    assert any("sop: BEHIND 1" in d and "uuid-B" in d for d in result.details)
```
This is the acceptance test itself — it currently has no home because the function it
would call doesn't do identity comparison yet.

### "a day with no entities run produces a Slack line"

**Not proven today, and not built.** This is plan item 4 ("Alarm on the check's
absence," `plan:74`): the Worker should record each dispatched run id and, on its next
fire, poll `GET /actions/runs/<id>` and Slack anything not `success`. `worker.js` today
has no run-id capture (`dispatch()`, `:90-111`, discards the response body after
checking `resp.ok`), no state persisted between invocations (no KV/D1 binding declared
anywhere in `worker.js`), and no polling logic. `worker.test.js` correspondingly has
no test of this path — it only covers `dispatchesFor`.

**What has to exist first:**
1. Somewhere to remember "did entities.yml actually get dispatched (and succeed)
   yesterday" across cron fires — a Workers KV binding is the natural fit (already a
   documented Cloudflare primitive; no new vendor per Kevin's dead-man's-switch
   preference in DEVLOG 2026-09-01 decision 4, but this is a different, smaller need
   than the watchdog — it's "did my own last dispatch succeed," not "is the whole
   pipeline dead"). Simplest alternative that needs no new binding: on each `scheduled()`
   fire, before dispatching today's work, call `GET
   /repos/khglynn/list-maker/actions/workflows/entities.yml/runs?created=<yesterday's
   date>` and Slack if the list is empty or every run's `conclusion` is not `success`.
   This avoids state entirely — GitHub's API is already the source of truth — at the
   cost of one extra `fetch` per cron fire.
2. A pure, testable function shaped like `dispatchesFor` — e.g.
   `checkPreviousRun(runsResponse: {workflow_runs: [...]}) -> {ok: bool, detail: string}`
   — kept separate from the `fetch` call itself, exactly how `dispatchesFor` is kept
   separate from `dispatch()` today. That split is *why* `dispatchesFor` is easy to
   test and `dispatch()`/`scheduled()` are not (Pattern E above).

**The test to add** (establishes Pattern E's fetch-mock for the first time):
```js
test("a day with no entities run for yesterday posts a Slack alert", async (t) => {
  const runs = { workflow_runs: [] }; // nothing ran yesterday
  t.mock.method(globalThis, "fetch", async (url) => {
    if (String(url).includes("/runs?")) return new Response(JSON.stringify(runs));
    if (String(url).includes("hooks.slack.com")) { slackCalled = true; return new Response("ok"); }
    return new Response("ok");
  });
  let slackCalled = false;
  await checkPreviousRunAndAlert(fakeEnv, "entities.yml", yesterday);
  assert.equal(slackCalled, true);
});
```
Until `checkPreviousRunAndAlert` (or equivalent) exists, this acceptance line has no
test to point at — flag it to the implementer as the first thing to design, not the
last thing to test.

### "the health run's every FAIL is actionable"

This is provable today, check by check, against the existing suite — see the audit in
section 3. The pattern already used for "does the detail line carry the fact you need"
is asserting on substring content of `result.details`
(`test_data_health.py:64, 115, 141, 150, 206, 264, 280`, etc.) — e.g.
`assert any("hard-fork ep 5133" in d for d in result.details)`
(`test_data_health.py:206`) proves `check_transcript_race_selfheal` names the exact
episode. The same assertion shape is how you'd pin the fix for each non-actionable
check below once it's changed to carry an id.

## 3. Every current health check, and whether its FAIL is actionable

`run_checks()` (`data_health.py:919-938`) order; `check_import_caught_up` only runs
when `include_feed_check=True` (the daily CLI; the pulse omits it).

| # | Check (`data_health.py:`) | Can it FAIL? | Actionable today? |
|---|---|---|---|
| 1 | `check_expected_shows` (136-159) | yes | **Yes.** Names the exact slug and the mismatch kind (missing row / id mismatch / unconfigured slug). |
| 2 | `check_episode_identity` (162-212) | yes | **Yes.** `details` includes a JSON dump of up to 10 sample bad rows with `id`, `slug`, `title`, `url`, `publish_date` (`:192-206`) — you can go straight to the row. |
| 3 | `check_duplicate_episodes` (215-244) | yes | **Yes.** Each detail line carries `episode_ids` (`:236-239`) — an `ARRAY_AGG`, directly queryable/fixable. |
| 4 | `check_transcript_coverage` (253-364) | yes (strict-mode shows only; music shows only warn) | **Partially.** Names the show and the oldest overdue date (`:335-338`), enough to find the row via `WHERE show=... AND publish_date<=oldest`, but doesn't list the episode id(s) directly the way #2/#3/#8 do. |
| 5 | `check_episode_freshness` (367-420) | yes | **Yes.** Names the show, days since, and the threshold (`:410-413`) — the action is "check that show's importer." |
| 6 | `check_notion_sync_freshness` (423-494) | yes | **Split.** The transcript-backlog branch is actionable (show + oldest date, `:475-479`). The stale-entity branch is **not**: `"{N} entity page(s) have Neon updates >{d}d old that never reached Notion"` (`:480-484`) gives a bare count with no entity id — you'd have to hand-write the query in the check's own docstring to find which rows. **Gap to close.** |
| 7 | `check_ai_daily_extraction` (566-671) | yes | **Not actionable as written.** All three failure lines are bare counts with no identifiers: `"AI Daily episodes transcripted >6h ago without mentions: {N}"` (:649-651), `"AI mentions pointing at a deleted transcript: {N}"` (:653-655), `"completed AI runs with zero mentions: {N}"` (:657). None names an episode id, mention id, or run id. **Gap to close** — this is the check most in need of item-3's "actionable FAIL" work; each of the three sub-queries already groups by something identifiable (episode, mention, run) and just needs to surface a sample. |
| 8 | `check_transcript_race_selfheal` (674-731) | yes | **Yes.** Every detail line names `slug`, `episode_id`, and days pending (`:712-717`) — the gold-standard shape for this table. |
| 9 | `check_ai_mention_fields` (734-759) | yes | **Not actionable as written.** `details` are `f"{key}={count}"` (`:749-753`, e.g. `missing_mention_text=4`) with no mention id. **Gap to close.** |
| 10 | `check_sponsor_share` (786-851) | yes (only the "100% ads" case; the >30% case is warn) | **Coarse but usable.** Names the show and the mention/ad counts (`:830-844`) — actionable as "go look at this show's recent extraction," not as "here is the row," which is arguably fine for a ratio-based systemic alert rather than a per-row data check. Not flagging as a gap. |
| 11 | `check_possible_entity_alias_splits` (854-883) | **no** — `_status_from_count(len(rows), warn_only=True)` (`:879`) means this can only be `pass` or `warn`, never `fail`. | N/A — never contributes a FAIL. Its detail lines (`:872-877`) do carry `entity_ids` when it does warn. |
| 12 | `check_optional_null_map` (886-916) | **no** — hardcoded `CheckResult(..., "pass", ...)` (`:911-916`); it's a report, not a gate. | N/A. Matches the plan's own note (`plan:76`, "`check_optional_null_map` leaves the alerting list — it can only ever pass") — this one isn't broken, it's mis-filed: it should probably move out of `run_checks()`'s alerting set into a separate `--report`-only path so a reader doesn't scan it every day expecting it to ever say something. |
| 13 | `check_import_caught_up` (497-563, opt-in via `include_feed_check`) | yes | **Actionable but sometimes wrong.** When it fires, the message names the show, the feed's latest date, our latest date, and the oldest missing date (`:546-549`) — genuinely actionable. The defect isn't unactionability, it's correctness: date-only comparison (a) false-positives on a re-dated episode (the TAL bug) and (b) is structurally blind to a mid-series hole (see acceptance line 1 above) — it will report `pass` with nothing to act on when there IS a real gap. This is the one check where "every FAIL is actionable" is true today but "every real problem produces a FAIL" is not — and the acceptance line is really asking for the second property. |

**Actionability gaps to close for "every FAIL is actionable"** (in priority order —
#7 and #9 are the two checks that currently fail this bar outright; #6's stale-entity
branch is a smaller version of the same gap):
- `check_ai_daily_extraction` (7): add a `LIMIT`-ed sample of episode ids /mention ids
  /run ids to each of the three detail lines (the existing `_one` queries already
  compute the count; each would need to become `_rows` with `episode.id`/`m.id`/`r.id`
  selected and joined into the message, same shape as `check_episode_identity`'s
  sample-rows block at `:192-206`).
- `check_ai_mention_fields` (9): same fix — the query already does `COUNT(*) FILTER
  (...)` per column; add a parallel `_rows` query with `LIMIT 10` ids for whichever
  columns are nonzero, same shape as #7.
- `check_notion_sync_freshness` (6): the stale-entity branch's `_one` query
  (`:452-464`) needs to become `_rows` with entity ids, mirroring the transcript
  branch it sits next to (which already names slugs).

Each fix follows Pattern A exactly — the hermetic test for each is the same shape as
`test_notion_sync_freshness_fails_on_transcript_backlog`
(`test_data_health.py:132-141`): monkeypatch `_rows`/`_one` to return canned rows
*with* ids, assert the id appears in `result.details`.

## 4. The "run-completeness" acceptance (transactional batch load)

Not one of the plan's three named acceptance lines verbatim, but it's the other
concrete `assert` implied by `plan:75` ("a mid-batch crash leaves a permanent
undercount nothing can see... add a run-completeness check") and belongs in the same
test pass. Today: `insert_run` (`load_entity_batch.py:168-193`) calls `conn.commit()`
at `:192` — before a single `ai_mentions` row exists — and `main()`'s row loop
(`:629-661`) inserts mentions one call at a time with no batching commit visible until
`conn.close()` (`:689`) via psycopg2's connection-level autocommit-off default (worth
confirming `common.get_db_connection`'s isolation/autocommit setting — if autocommit is
off, `conn.commit()` is required somewhere for the mentions to persist at all, which
this file never calls again after `:192` and `record_first_seen_as_ad`'s commits inside
its own helper). **Read this in `common.py` before designing the fix** — whether the
mentions currently persist without an explicit second `commit()` changes whether the
bug is "the run row is committed early" (visible half-state) or "nothing after
`insert_run` persists at all without an explicit commit the code never makes" (a
different, worse bug). This wasn't in this item's scope to resolve — flagging it as
the one fact the implementing agent must ground-truth before touching this file.

The test to add, using Pattern F (`inspect.getsource`, same shape as
`test_first_seen_as_ad_is_stamped_after_the_batch_not_during_it`,
`test_load_entity_batch.py:399-425`):
```python
def test_run_is_not_marked_completed_until_every_mention_lands() -> None:
    """A crash between insert_run and the last insert_mention must not leave a
    'completed' run with fewer mentions than the manifest claims."""
    source = inspect.getsource(leb.main)
    insert_run_at = source.index("run_id = insert_run(")
    loop_at = source.index("for row in rows:")
    # whatever the fix is (a status flip after the loop, or moving insert_run's
    # commit to the end) — pin that insert_run's COMMIT happens after the loop,
    # not before it.
```
The exact assertion depends on the fix's shape (deferred commit vs. a
status='pending'→'completed' flip vs. a separate completeness check reading manifest
counts) — that design choice is out of this item's scope and belongs to whichever
item designs Decision-10-adjacent schema/loader changes.

## Files read for this map

- `pipeline/data_health.py` (full, 1019 lines)
- `pipeline/feed_check.py` (full, 127 lines)
- `pipeline/scrapers/ai_daily/load_entity_batch.py` (partial: 1-400, 460-700)
- `pipeline/scrapers/taddy/import_transcripts.py` (uuid usage only)
- `pipeline/show_config.py` (ShowConfig fields only)
- `tests/test_data_health.py` (full, 433 lines)
- `tests/test_feed_check.py` (full, 55 lines)
- `tests/test_load_entity_batch.py` (full, 425 lines)
- `tests/test_run_new_episodes.py` (full, 395 lines)
- `cloudflare-trigger/worker.js` (full, 164 lines)
- `cloudflare-trigger/worker.test.js` (full, 55 lines)
- `.github/workflows/test.yml` (full)
- `docs/principles.md` (full)
- `claude-plans/2026-09-01-ground-it-cleanup-plan.md` (Phase 4 section)
- DEVLOG.md, 2026-09-01 entries (grep excerpts on re-dated episode, cancelled/never-fired
  runs, mid-batch crash)

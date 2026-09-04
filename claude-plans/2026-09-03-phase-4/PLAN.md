# Phase 4 build plan — health checks and data you can trust on a bad Tuesday

**Written:** 2026-09-03 evening, by a five-reader Sonnet map + Opus synthesis against the live code (readers: feed-identity, run-watchdog, transactional-load, honest-data, acceptance-tests — their full notes are in this folder). **Parent:** `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → Phase 4. **Integration branch:** `arc/phase-4`; sub-PRs target it; one PR to main at the end. **Status (2026-09-03, late):** all four PRs merged into `arc/phase-4` after five-lens reviews — #41 (5 findings fixed; Worker tests 6 → 40), #42 (3 fixed; 449 → 476), #43 (builder's own reviewer closed 2 blocking findings over four rounds, independent review 0 of 6; 476 → 499), #44 (1 confirmed and fixed — the 25% missing-confidence ceiling on the eval; 499 → 550). **PR #45 `arc/phase-4` → `main` is open.** Kevin's part unchanged: the `DISPATCH_LOG` KV namespace id, then deploys (§ Kevin's part).

# Phase 4 — the build plan

**Written:** 2026-09-03, from five readers' maps (`phase4/*.md`) plus an independent re-read of every load-bearing line. **Spec:** Phase 4 of `claude-plans/2026-09-01-ground-it-cleanup-plan.md`. **Why:** `DEVLOG.md` 2026-09-01 — the re-dated TAL episode that inflated a BEHIND count, 08-06 cancelled unseen, 08-16 never fired, the mid-batch crash undercount.

**Out of scope, already shipped:** the sponsor-block guard (ads-as-data, 2026-09-02) and decision 10's schema items (`sql/007`, `sql/008` ran). Do not re-open either.

**Everything below was verified against the code on 2026-09-03**, not taken from the plan text. Where a reader's claim and the code disagreed, the code won and the difference is in *Spec corrections*.

---

## (a) The items

| # | Item | Size | Primary files | Risk |
|---|---|---|---|---|
| 1 | Feed check by **episode identity**, not `MAX(publish_date)` | M | `feed_check.py`, `show_config.py`, `import_transcripts.py`, `data_health.py` | **Med** — six existing tests monkeypatch the two seams being replaced; miss one and a "hermetic" test silently tests nothing |
| 2 | Alarm on a run that **never started or never finished** | L | `cloudflare-trigger/*`, + companion in `self-hosted-mcps/watchdog` | **Med** — cross-repo; needs a KV namespace, a deploy, and a live URL only Kevin can confirm |
| 3 | **Transactional** batch load + run-completeness check | M | `load_entity_batch.py`, `data_health.py` | **Med** — one existing test does `inspect.getsource(main)` and breaks with a `ValueError` if the loop is extracted (see corrections #5) |
| 4a | `zero_mention_runs` gets a show filter + 30-day window | XS | `data_health.py` | Low — 0 live rows affected (verified read-only) |
| 4b | `check_optional_null_map` leaves the alerting list | XS | `data_health.py` | Low — it can only ever return `pass` |
| 4c | **NULL** confidence instead of a fabricated `0.5` | M | `extract_entities.py`, `evals/extraction/{metrics,run_eval}.py` | **High** — three sites must ship together or extraction crashes / the weekly eval reddens |
| 4d | Per-episode Taddy dedup key, not the shared `"unknown-episode"` | S | `import_transcripts.py` | Low — 0 live rows on the literal today |
| 4e | `run_script` doesn't retry a deterministic failure (exit 2) | M | `run_new_episodes.py` + 5 callees | Med — new convention; per-callee classification is a judgment call |
| 4f | Every FAIL names ids (from Item 5's audit) | S | `data_health.py` | Low |

Item 5 ("acceptance and the test harness") is **not a ninth item** — it is the acceptance criteria for 1–4 plus one real gap, 4f. Its testing patterns are folded into each brief below.

---

## (b) The PR split — four PRs, two lanes

The constraint: `pipeline/data_health.py` and `tests/test_data_health.py` are touched by items 1, 3, 4a, 4b and 4f; `import_transcripts.py` by 1 and 4d; `load_entity_batch.py` by 3 and 4e. **Resolution: one owner per file per PR, and the Python PRs merge in sequence.** PR 4 is deliberately last, which is what lets it own `import_transcripts.py` and `load_entity_batch.py` uncontested — no rebase gymnastics, no auto-merge on a 1,000-line safety-critical file.

Two lanes run at once:

- **Lane W** — PR 1 (Worker + watchdog). Touches zero Python. Fully parallel with everything; merge whenever it is green and the KV namespace exists.
- **Lane P** — PR 2 → PR 3 → PR 4, strictly sequenced. Each rebases on `main` after the previous merges.

---

### PR 1 — `feat/worker-run-verification` — "a dispatched run that never finished says so"

**Goal.** Today a *successful dispatch* is recorded nowhere, so 2026-08-06 (a cancelled run) and 2026-08-16 (no run at all) both passed in silence. After this PR: every dispatch is recorded, the next day's fire checks what happened to it, and anything that is not `success` produces a Slack line. Separately, a Worker that goes fully dark — cron deleted, crashed before any alert — is caught from outside by fleet-watchdog polling a new `/health`.

**Why.** `docs/principles.md`: *"A script that runs is not an operation. An operation has visible failure states, can distinguish 'nothing to do' from 'didn't check,' and leaves evidence."* Right now the Worker cannot distinguish those. Plan decision 4 already settled the external half: reuse fleet-watchdog, do not add a vendor.

**Files.**
- `cloudflare-trigger/worker.js`
- `cloudflare-trigger/wrangler.toml`
- `cloudflare-trigger/worker.test.js`
- `cloudflare-trigger/README.md`
- `.github/workflows/pulse.yml` — one comment line only (see corrections #9)
- Companion, **separate repo, separate PR**: `personal/self-hosted-mcps/watchdog/src/index.js`, `wrangler.jsonc`, `README.md`

**Design.**

*Correlation without a run id.* `workflow_dispatch` returns `204 No Content` — GitHub gives back no run id (confirmed in `dispatch()`, worker.js:90-111). Do **not** add a `dispatch_id` input to the four workflow YAMLs. All four (`entities`, `pipeline`, `eval`, `blogs`) already carry `concurrency: {group: github.workflow}`, so scoping the list-runs call to one workflow file and filtering by a time window after the recorded dispatch is unambiguous.

Three new **pure, exported** functions (same shape as `dispatchesFor`, which is the file's existing testable-logic precedent):

- `correlateRun(runs, dispatchedAtIso, toleranceMs = 5 * 60 * 1000)` → the **earliest** run whose `created_at >= dispatchedAt - tolerance`, else `null`. Earliest-wins is deliberate: a later manual re-run must never mask a missed scheduled run.
- `verdictFor(run)` → `"missing"` when `run` is null; `` `stuck-${run.status}` `` when `status !== "completed"`; else `run.conclusion`.
- `dispatchKey(workflow, iso)` → `` `dispatch:${workflow}:${iso}` ``.

*Recording.* Inside `dispatch()` itself, on success: `env.DISPATCH_LOG.put(dispatchKey(workflow, nowIso), JSON.stringify({workflow, dispatchedAt: nowIso}), {expirationTtl: 3 * 24 * 3600})`. Putting it inside `dispatch()` (not in `scheduled()`) means the manual `?token=` path self-records too.

*Verifying.* New `verifyPreviousDispatches(env, now)`:
- no-op if `!env.GH_PAT` (the existing PAT-missing alert already fires; do not double-alarm)
- `DISPATCH_LOG.list({prefix: "dispatch:"})`
- for each record **≥ 20h old** (longest workflow timeout is 70 min, so 20h is safely "done"): fetch that workflow's runs, `correlateRun`, `verdictFor`
- on anything but `"success"` → `notifyVerifyIssue(env, …)`, worded differently from `notifyFailure` (a cancelled or failed run *did* start; conflating the two is how an alert stops meaning something)
- delete the key after processing (success **or** alerted) so it is never re-checked; **leave it in place** if the GitHub call itself threw — the TTL then retries it tomorrow

Extract a shared `postSlack(env, text)` out of `notifyFailure` so both alert paths share one POST/console-fallback body.

*Wiring.* In `scheduled()`, **before** the existing `GH_PAT` and `DAILY_CRON` guards: unconditionally write `meta:last_fire` to KV, then run `verifyPreviousDispatches` (writing `meta:last_verify`) — each in its own `ctx.waitUntil`, so a broken PAT or a dead Slack still leaves `last_fire` fresh. That ordering is the whole point: `last_fire` must be the one signal that survives every other failure.

*New `/health` route.* In `fetch()`, checked **before** the token gate (which is otherwise untouched): unauthenticated `GET /health` → `{worker, last_fire, last_verify}` read straight from KV. Mirrors fleet-watchdog's own ungated-status convention. It exposes two timestamps and no secrets.

*`wrangler.toml`*: add one `[[kv_namespaces]]` block, `binding = "DISPATCH_LOG"`. **No new cron trigger** — this rides the existing single daily fire.

*Companion (self-hosted-mcps/watchdog).* Add a `CRON_TARGETS` array and `probeCronHealth(target)` that GETs `/health` and flags `"stale"` when `last_fire.at` is older than `maxAgeMs` (26h). Return the same `{name, healthy, cold, problems}` shape as `probe()` so it flows through the existing transition-based `reconcile()` unchanged. **Required one-char fix:** `statusBody` (src/index.js:262) does `SERVICES.find(...).account` — make it `?.account ?? null` or it throws on a `CRON_TARGETS` entry. Split the freshness test into a pure `isCronStale(lastFireAt, now, maxAgeMs)` so it is testable if a harness ever lands.

**Tests** (`worker.test.js`, plain `node:test`, no deps — the file's `package.json` says "no dependencies on purpose", keep it that way).
- `correlateRun`: (1) a run created seconds after `dispatchedAt` is picked; (2) a run created **before** `dispatchedAt` (yesterday's leftover, still inside `per_page=10`) is filtered out; (3) empty array → `null`; (4) two post-dispatch runs — the scheduled one and a later manual re-run — the **earlier** wins; (5) a run exactly at the tolerance boundary is **included** (pins the inclusive choice).
- `verdictFor`: `null`→`"missing"`; completed+success→`"success"`; completed+failure→`"failure"`; completed+cancelled→`"cancelled"`; `in_progress`→`"stuck-in_progress"`; `queued`→`"stuck-queued"`.
- `dispatchKey`: one format-pin test — `verifyPreviousDispatches`'s prefix scan depends on it staying `dispatch:…`.
- Existing `dispatchesFor` tests untouched.
- `fetchRunsForWorkflow`, the KV write, `verifyPreviousDispatches`, `notifyVerifyIssue` stay untested at the network/KV level — exactly the precedent `dispatch()` and `notifyFailure()` already set. Only the pure decisions are pinned.

**Verification.** `node --test cloudflare-trigger/worker.test.js` locally and in CI (`test.yml` already runs it). After Kevin deploys: `curl https://<worker>.workers.dev/health` returns both timestamps; the next daily fire populates `last_verify`. The end-to-end proof is the first non-success run producing a Slack line — do not claim the alarm works before that, or before the watchdog has seen one `/health` response.

**Do not touch.** The four workflow YAMLs (no `dispatch_id` input). `dispatchesFor` and its tests. The `?token=` gate's behaviour for any path other than `/health`. `wrangler.toml`'s `[triggers]` — no new cron.

**Needs Kevin.** KV namespace creation, the id paste, two `wrangler deploy`s, the live workers.dev URL, and approval to push to `khglynn/self-hosted-mcps`. See section (d).

---

### PR 2 — `fix/feed-check-by-episode-identity` — "a hole in the middle of a series is visible"

**Goal.** `check_import_caught_up` stops comparing dates and starts comparing episode identities. This kills the re-dating false positive (a TAL episode re-dated by Taddy inflated a BEHIND count) and — the real prize — surfaces a gap in the *middle* of a series, which `MAX(publish_date)` can never see.

**Why.** `data_health.py:497-563` pulls one `MAX(e.publish_date)` per show (:516-523) and `split_missing_feed_dates` (:99-114) calls a feed date missing **iff it is newer than `db_latest`**. So an episode we never imported, sitting behind the newest one we do hold, passes silently forever. And a date the feed changed on an episode we *do* hold reads as a brand-new missing episode. Both are the same bug: dates are not identity.

**Why it is cheap:** `taddy_recent_dates` (`feed_check.py:54-82`) *already* asks Taddy for each episode's `uuid` in its GraphQL query (:62) and throws it away at :81. And `episodes.url` is already the durable per-episode key (`UNIQUE` constraint `episodes_url_key`). Both upsert paths are `ON CONFLICT (url) DO UPDATE … publish_date = COALESCE(EXCLUDED.publish_date, …)` — a re-date updates the same row's date and never touches `url`. Identity-based comparison is therefore immune to re-dating by construction.

**Files.** `pipeline/show_config.py`, `pipeline/feed_check.py`, `pipeline/data_health.py`, `pipeline/scrapers/taddy/import_transcripts.py`, `tests/test_feed_check.py`, `tests/test_data_health.py`, `tests/test_import_transcripts.py`.

**Design — add, don't replace.**

1. **`show_config.py`**: new `TADDY_EPISODE_URL_PREFIX = "https://api.taddy.org/podcast-episode/"` and a pure `taddy_episode_url(uuid) -> str`. `import_transcripts.episode_url_key` (:275-291) then *delegates* to it — this removes the one existing hardcoded copy of the format string rather than adding a second.

2. **Item 4d, folded in here** (see corrections #10 — same function, same test file, one owner): change the signature to `episode_url_key(episode, show_id: int | None = None)` (default keeps existing no-arg test calls working) and replace the shared `"unknown-episode"` literal with
   ```
   f"taddy-unidentified:{show_id or 'no-show'}:{episode.get('datePublished') or 'no-date'}:{(episode.get('name') or 'untitled').strip().lower()}"
   ```
   Deterministic across re-imports (same episode → same key, so upserts still land on one row) but scoped per-show and per-episode, so two malformed episodes can never collapse onto one row via the `UNIQUE` constraint. Pass `show_id` at both call sites (`upsert_episode`:301 and `find_existing_episode_id`:407 — both already have it in scope).

3. **`feed_check.py`**: add `taddy_recent_episodes` / `rss_recent_episodes` / `feed_recent_episodes` returning `(identity, date)` tuples, **alongside** the existing date-only functions, which stay untouched (`pulse_report.py` and existing tests still use them). `rss_recent_episodes` must reuse `import_gabfest.parse_feed` + `episode_url` **directly** rather than re-implementing the guid/enclosure/link fallback chain — that chain is already hermetically tested by `tests/test_import_gabfest.py`, and reusing it makes divergence structurally impossible. `feed_recent_episodes` branches on `cfg.taddy_uuid` vs `"megaphone" in url`, mirroring `feed_recent_dates`'s existing branch exactly.

4. **`data_health.py`**:
   - new `split_missing_feed_episodes(feed, held_ids, grace_days, today=None)` — same overdue/pending grading as `split_missing_feed_dates`, but `missing` = *identity not in `held_ids`*, with the date used **only** for the grace-window grading, never for membership.
   - new `_held_episode_urls_by_show(conn)` — one bulk `SELECT s.slug, e.url, e.publish_date FROM shows s JOIN episodes e ON e.show_id = s.id WHERE e.url IS NOT NULL`. **Keep `db_latest` from the same query** (max of `publish_date` per slug) so the Slack line keeps saying `(feed at X, we have Y)`; dropping it would be a visible, pointless UX regression. Add a `WHY` comment: unbounded on purpose (~4,300 rows on 2026-09-03); a date filter is *forbidden* here because Culture Gabfest ended 2026-07-01 and its RSS still returns 15 pre-July episodes — any rolling window would eventually report the entire show as missing. If the table ever gets big, scope by `show_id` per iteration, not by date.
   - rewrite `check_import_caught_up` to call `feed_recent_episodes` + the held set. **Same signature, same curated-skip, same UNVERIFIED-on-`None` structure, same message shapes.**
   - Add a code comment at the identity comparison: pre-June-2026 rows predate the Taddy migration and carry legacy url schemes, so they are invisible to identity comparison. Harmless today (the url-scheme split is strictly chronological and `limit=15` never reaches that far for any show's cadence) — but a future `limit` bump would quietly reintroduce false positives. Write the reason down, not the fact.

**Tests.**
- `tests/test_feed_check.py`: `taddy_recent_episodes` pairs identity+date correctly; **a drift test** asserting `show_config.taddy_episode_url(uuid) == import_transcripts.episode_url_key({"uuid": uuid})` exactly (mirrors the existing drift-test pattern in `tests/test_show_config.py`) — this single test is what makes the delegation non-accidental; `rss_recent_episodes` matches `import_gabfest.episode_url` on a shared fixture; `feed_recent_episodes` drops future dates and sorts.
- `tests/test_data_health.py`, the two acceptance tests:
  - **the TAL regression:** a re-dated-but-held episode never appears as missing, at any grace window.
  - **the mid-series gap (the plan's own acceptance line):** `held = {A, C}`, `B` missing, `B` *older* than the newest held `A` → caught as **overdue**, even though `MAX(publish_date) = A` is held. This is the test that fails on today's code and passes on the new code.
  - plus an end-to-end `check_import_caught_up` test seeding the same gap via monkeypatched `feed_recent_episodes` + held-urls.
- `tests/test_import_transcripts.py`: update line 26 (`episode_url_key({}) == "unknown-episode"`) to the new shape; add a test proving the fallback is (i) different for different names, (ii) **identical when called twice with the same inputs** (idempotent, so upserts still converge), (iii) different for the same name/date under a different `show_id`. Lines 11-25 are unaffected — they never reach the new branch.
- **Mandatory sweep before opening the PR:** `grep -n "feed_recent_dates\|dh\._rows\|_feed_check" tests/test_data_health.py tests/test_feed_check.py pipeline/pulse_report.py`. Six existing tests hard-monkeypatch the two seams being replaced (`dh._rows` for the `MAX(publish_date)` query, `dh.feed_recent_dates`). Each one must be *updated*, not merely left passing — a monkeypatch pointing at a seam the code no longer calls is a test that silently exercises nothing. This is the highest-risk part of the PR and it fails **quietly**, so treat the grep output as a checklist and account for every hit in the PR body.

**Verification.** `pytest -q` green. Then, read-only against live Neon, confirm the new check's verdict per show matches the old one for every show that is genuinely caught up (a diff here means either a real gap the old check was hiding — good, report it — or a bug — fix it). Do not merge on a diff you cannot explain.

**Do not touch.** `split_missing_feed_dates`, `feed_recent_dates`, `taddy_recent_dates`, `rss_recent_dates` — `pulse_report.py` depends on them and they stay. `run_checks`. Anything in `check_sponsor_share` (ads-as-data is shipped). Any `sql/` file.

**Needs Kevin.** Nothing. No DDL, no writes, no secrets.

---

### PR 3 — `fix/transactional-batch-load` — "a crashed batch is retried whole, not in lucky pieces"

**Goal.** One transaction per batch. A crash anywhere in the row loop leaves the run at `status='loading'` with exactly **zero** mentions — never a `completed` run with a partial count. Plus two checks that make an incomplete run visible.

**Why.** `insert_run` commits `status='completed'` in one insert (`load_entity_batch.py`:192, called from main :606-618) **before the mentions loop at :629 starts**, and every write inside the loop commits independently (`upsert_entity` :344/:360, `insert_mention` :541, `record_first_seen_as_ad` :399). `get_db_connection` never sets `autocommit`, so psycopg2's default `False` holds and each explicit `conn.commit()` closes and reopens a transaction — **every row is its own durable unit.** A process killed mid-loop therefore leaves `ai_runs` already `completed` with a nonzero-but-wrong `ai_mentions` count. Worse, `find_unextracted_episodes` (`run_new_episodes.py`:124-200, exclusion at :180-182) decides "already extracted" purely by `episode_id NOT IN (SELECT episode_id FROM ai_mentions)` and never looks at `ai_runs.status` — so whichever episodes happened to commit before the crash are **never retried**, and the rest are. Which episodes survive depends on CSV row order. Nothing catches this: `zero_mention_runs` (`data_health.py`:628-644) only sees `COUNT(m.id) = 0`.

**Files.** `pipeline/scrapers/ai_daily/load_entity_batch.py`, `pipeline/data_health.py`, `tests/test_load_entity_batch.py`, `tests/test_data_health.py`.

**Design.**

1. `insert_run` gains `commit: bool = True`, and `completed_at` becomes a **bound param** — `None` when `status="loading"`, `NOW()` otherwise. (This also fixes a real secondary bug: today a `'loading'` row would claim a false completion timestamp, because the SQL hardcodes `NOW()` for both `started_at` and `completed_at`.)
2. `main()` inserts the run as `status="loading"` with its **own** commit, so the row is visible immediately, and puts `parameters.expected_mentions = len(rows)` — read from the same `mentions.csv` the loader already parsed (`main()` :561-562). Not from `batch_manifest.json`: keeping the evidence and the comparison in one process/one read is the point.
3. `upsert_entity`, `insert_mention`, `record_first_seen_as_ad` gain the same `commit: bool = True` flag.
4. Extract the row loop **and the sponsor-stamp second pass** out of `main()` into a new `load_batch_rows(conn, *, run_id, rows, transcript_map, publish_dates)` that calls all three with `commit=False` and never commits itself. **Both passes move together** — the ordering invariant (stamps collected in the loop, applied after it) must survive intact.
5. New `finalize_run_completed(conn, run_id, commit=True)` → `UPDATE ai_runs SET status='completed', completed_at=NOW()`.
6. `main()` wraps `load_batch_rows(...)` + `finalize_run_completed(..., commit=False)` + one `conn.commit()` in a `try/except: conn.rollback(); raise`.

`delete_existing_run` (:254-279) already deletes any prior run + mentions for `(show_id, batch_name)` regardless of status, so retries stay idempotent — **no change needed there.** `record_empty_batch` (:199-251, PR #23's `completed_empty`) is already atomic (single insert, no loop) — **no change needed there either.**

Two new checks in `data_health.py`, both registered in `run_checks()`:
- `check_ai_run_completeness` — **fail tier.** `ai_runs.status='completed' AND parameters ? 'expected_mentions'`, joined to a live `COUNT(ai_mentions)`, flagging any mismatch. **The `? 'expected_mentions'` guard is not optional** — without it every historical `ai_runs` row written before this field existed floods the check red on rollout day. This is the single most important line in the PR.
- `check_ai_run_stuck_loading` — warn/fail by `started_at` age, tiered like `check_transcript_race_selfheal`; fail at 30 min, comfortably past the load step's ~30-min worst-case retry envelope inside `run_script`.

**Tests.**
- `tests/test_load_entity_batch.py`:
  - **Fix the existing `test_insert_run_defaults_to_completed` (:258-264).** `params[-1] == "completed"` breaks once `completed_at` is appended: params go from `(show_id, batch_name, model, prompt_version, parameters, status)` to `(…, status, completed_at)`. Assert `params[5] == "completed"` and `params[6] is not None`. Do **not** reorder the SQL columns to dodge this — a disclosed test change is cheaper than a reshuffled insert.
  - **Repoint the existing ordering test** — see corrections #5. It is a hard break, not a soft one.
  - New: `insert_run(status='loading')` leaves `params[6]` `None`; `insert_run` / `finalize_run_completed` honour `commit=False` (`conn.committed` stays `False`); `load_batch_rows` never commits across two fake CSV rows; **`load_batch_rows` on a cursor that raises on its second `execute()` propagates the exception AND leaves `conn.committed` `False`** — that last one is the test that would have caught the original bug.
- `tests/test_data_health.py`: extend the existing monkeypatch-by-SQL-substring pattern (`_rows`/`_one`, see `test_extraction_integrity_*` :209 and the `_sponsor_rows` helper :353) — `check_ai_run_completeness` passes on empty and fails with a detail naming run/batch/expected/actual; `check_ai_run_stuck_loading` passes on empty, warns under the threshold, fails over it; both names present in `run_checks()` (mirror `test_sponsor_share_is_in_the_standard_check_set` :427).

**Verification.** `pytest -q` green. Read-only against live Neon: no `completed` run carries an `expected_mentions` that disagrees with its live count, and no run sits at `'loading'`. A batch is bounded at `EXTRACTION_BATCH_SIZE = 5` episodes (`run_new_episodes.py`:392), so the single open transaction lasts seconds — sanity-check it against `common.py`'s timeout config, but no timeout risk is expected.

**Do not touch.** `delete_existing_run`. `record_empty_batch` / the `completed_empty` outcome (PR #23 law). `zero_mention_runs` and `run_script` — those are 4a and 4e, PR 4's, deliberately kept out. `sql/` — none needed (corrections #3).

**Needs Kevin.** Nothing. No DDL: `ai_runs.status` is a plain `VARCHAR(32)` with no CHECK constraint (`sql/001_ai_entity_schema.sql`:13), so `'loading'` is pure application code.

---

### PR 4 — `fix/honest-health-and-failure-paths` — "every FAIL is real, actionable, and not retried forever"

Last on purpose: by now it owns `data_health.py`, `import_transcripts.py` and `load_entity_batch.py` uncontested. **Two commits, one theme.**

**Commit 1 — "the checks and the values tell the truth" (4a, 4b, 4c, 4f).**

- **4a** — `check_ai_daily_extraction`'s `zero_mention_runs` subquery (`data_health.py`:628-644) has no show filter and no time window, so one old or cross-show zero-mention `completed` run would pin the check red forever. JOIN the function's **own existing `ai_show` CTE** into the subquery (it already scopes `missing_mentions`), and add `AND r.created_at >= NOW() - INTERVAL '30 days'`, matching the `declared_empty_runs` convention two blocks below. Smallest fix consistent with the code that is already there. *(Verified read-only: 0 such rows exist today — this is forward-hardening.)*
- **4b** — remove `check_optional_null_map(conn)` from `run_checks()`'s list (:932); in `main()`, after `results = run_checks(...)`, do `results.append(check_optional_null_map(conn))`. The CLI text/JSON report is unchanged; `pulse_report.py` (which calls `run_checks()` directly) stops paying a per-show `COUNT(*)` on every daily and biweekly run for a check that hardcodes `status='pass'` (:886-916) and can therefore never appear in the alerting reduction at :1007 or `pulse_report.py`:275. Leave the `--feed-check-only` branch alone — it never included it.
- **4c — the highest-risk change in Phase 4. All three sites ship in this one commit or none of them do.**
  1. `extract_entities.py` `sanitize_mention` (:584-589, verified — the plan's `:547,551` is stale): `confidence_raw = mention.get("confidence")`; if `None` or non-numeric → `confidence = None`, `needs_review = True`, `review_reason = review_reason or "missing_confidence"` (a new reason, parallel to the existing `"low_confidence"`); else clamp to `[0,1]` and keep the existing low-confidence logic. `ai_mentions.confidence` (`sql/001`:78) is already nullable with no CHECK.
  2. **CSV export crashes without this:** :1333 and :1348 both do `f"{mention['confidence']:.4f}"`, which raises `TypeError` on `None`. Both become `f"{…:.4f}" if mention["confidence"] is not None else ""`. The loader (`load_entity_batch.py`:476) already maps empty-string → `None` correctly; no change there.
  3. **The weekly eval reddens without this:** `metrics.py` `confidence_report` (:204-224) defines `all_in_range = not out_of_range and n_missing == 0`, and `run_eval.py` `check_floors` (:189-190, :251-255) sums `n_out_of_range + n_missing` into the breach. That branch has been **dead code** — the sanitizer always faked `0.5`, so `n_missing` was structurally always 0. It fires on the very first honest NULL. Redefine `all_in_range = not out_of_range`; `conf_out` sums only `n_out_of_range`; reword the messages from "out of [0,1] or missing" to "outside [0,1]". `n_missing` stays a **reported, not gated** field — the same treatment the existing "degenerate distribution" diagnostic already gets.
  **This is a deliberate loosening of a gate, and `docs/principles.md` says never loosen a check without a grace window and a test.** The justification, stated in the PR body: the branch was structurally unreachable before this change and would fire on every future run for a reason that is now *correct behaviour*, not a defect. The grace window is that `n_missing` is still computed and reported; the test is `test_confidence_missing_value_is_reported_not_gated`. If the reviewer disagrees, the alternative is a floor on `n_missing` as a *ratio* — say it in review, don't ship it silently.
  `sanitize_fact` (:539/:543) has the identical fabrication pattern and is **out of scope** — a jsonb sub-field, unused downstream, and fixing it would also break a pinned test at `tests/test_extract_entities.py`:142. Flagged, not silently fixed.
- **4f** — three FAIL paths report bare counts with no ids, so the alert cannot be acted on: `check_ai_daily_extraction` (:566-671, three bare counts), `check_ai_mention_fields` (:734-759, `key=count`), and the stale-entity branch of `check_notion_sync_freshness` (:480-484). Convert each `COUNT(*) FILTER` `_one` query to a `_rows` query with `LIMIT 10` ids, exactly the shape `check_episode_identity` already uses at :192-206, and put the ids in `details`. *(`check_possible_entity_alias_splits` is `warn_only=True` and `check_optional_null_map` hardcodes `pass` — neither can ever FAIL, so both are correctly out of scope for this acceptance line.)*

**Commit 2 — "a deterministic failure is not retried" (4e).**

`run_script` (`run_new_episodes.py`:81-121) retries every non-zero exit identically — 2 retries, 5s/10s backoff. There is **zero** existing `sys.exit(2)` usage anywhere in the repo; this is entirely new plumbing.

In `run_script`, after computing `ok` and after the `if ok:` block, before the retry/backoff branch:
```python
if result is not None and result.returncode == 2:
    # exit 2 = the callee says "this will fail identically next time" (missing
    # credential, unknown slug, absent file). Retrying spends 15s to relearn it.
    log.error("step failed deterministically (exit 2), not retrying: %s — %s", label, tail)
    print(f"  FAILED ({label}) — deterministic, not retried")
    return False
```
The `result is not None` guard matters: on `subprocess.TimeoutExpired` `result` stays `None`, and a timeout must keep retrying — the function's own docstring calls it "the canonical transient failure."

Per-callee, convert **only unambiguous precondition checks**, using an inline `sys.exit(2)` at the check site rather than a broad except-by-type wrapper wherever that exception type is reused elsewhere in the same file for a genuinely transient condition (`RuntimeError` collides this way in both the Taddy importer and `extract_entities.py` — verified by grep; no other file has the collision):
- `import_transcripts.py` — missing `TADDY_USER_ID`/`TADDY_API_KEY`, unknown show slug (inline; everything else in the file already defaults to uncaught-exception → exit 1)
- `extract_entities.py` — missing `OPENAI_API_KEY`, "No episodes selected" (inline); the three `FileNotFoundError` sites via a new `except FileNotFoundError: sys.exit(2)` in `__main__` (safe — `FileNotFoundError` has no other use in the file)
- `load_entity_batch.py` — unknown-show-slug `RuntimeError` + two `FileNotFoundError` sites, one new except clause, no collision
- `sync_notion.py` — two existing direct `sys.exit(1)` calls for missing `notion_database_id`/`NOTION_TOKEN` → `sys.exit(2)`. Zero risk; not even an exception.
- `sync_playlist.py` — the unknown-`show_id` `ValueError` (the only `ValueError` in the file) → its existing except clause's `sys.exit(1)` becomes `sys.exit(2)`
- **No change** to `import_gabfest.py` or `normalize_aliases.py` — neither contains a single `raise` (grep-confirmed); there is no deterministic precondition to distinguish.
- **Flagged, not fixed:** Spotify OAuth refresh failure (`spotipy.oauth2.SpotifyOauthError`) is the other clear deterministic case — CLAUDE.md's own runbook says it needs a human re-auth — but no existing `except` clause catches it today. Adding one is a real, separate change. Say so in the PR body; don't quietly add it.
- The OpenAI HTTP-error `RuntimeError`s in `extract_entities.py` and the Taddy GraphQL-error `RuntimeError` stay **retryable/exit 1** — they could legitimately be a 429 or a 5xx. If the implementer's reading differs, argue it in review rather than changing it silently.

**Tests.**
- `tests/test_data_health.py` — `test_extraction_integrity_flags_recent_zero_mention_runs` (dispatch the fake `_one` on `"HAVING COUNT(m.id) = 0"` in the flattened SQL → `{"count": 1}`, assert `status == "fail"`), **plus a SQL-shape pin** asserting both `"ai_show"` and `"30 days"` appear in that query's text, mirroring the existing pattern at :337-346; `test_optional_null_map_is_not_in_the_alerting_list` via `assert "check_optional_null_map" not in inspect.getsource(data_health.run_checks)`, mirroring :427-433; three actionability tests (one per fixed check) monkeypatching `_rows` to return a row carrying an explicit id and asserting that id string appears in `result.details`, mirroring `test_notion_sync_freshness_fails_on_transcript_backlog` (:132-141).
- `tests/test_extract_entities.py` — fix `test_sanitize_mention_clamps_confidence_to_unit_interval` (:150: `"nope"` → `0.5` becomes `is None`); **split** `test_sanitize_mention_confidence_always_in_unit_interval` (:177-181) into a valid-numerics case and a new invalid-values case (`["x", None]` → `confidence is None`, `needs_review is True`) — the current combined loop will *crash* with `TypeError: '<=' not supported between int and NoneType` once `None` is a real output, which reads as a broken test rather than a caught regression; add `test_sanitize_mention_missing_confidence_becomes_null_and_flagged`. Leave `test_sanitize_fact_clamps_confidence_and_requires_key` (:142) alone.
- `tests/test_eval_metrics.py` — flip `test_confidence_missing_value_fails` (:196-199) to assert `all_in_range is True` with `n_missing == 1` still reported, and rename it to say so; add a mixed case proving out-of-range and missing are now independently tracked.
- `tests/test_run_new_episodes.py` — `test_run_script_does_not_retry_on_deterministic_exit_code`, using the existing fake-subprocess harness (:151-217): `returncode=2`, assert `calls["n"] == 1` and the result is `False`, with `time.sleep` monkeypatched to **raise** if called (that is what proves no backoff happened, rather than merely that it was fast).

**Verification.** `pytest -q` green. Then, before merge, run the extraction eval **once** (`eval.yml` via workflow_dispatch, or locally) and confirm it passes with the loosened confidence gate — 4c's whole risk is that the weekly Monday eval reddens, and waiting for Monday to find out is not verification.

**Do not touch.** `sanitize_fact`. `check_import_caught_up` (PR 2's). The batch transaction (PR 3's). `check_sponsor_share` / `sponsor_source`. Any `sql/` file.

**Needs Kevin.** Nothing mandatory. **One decision worth surfacing:** 4a scopes `zero_mention_runs` to `ai-daily-brief` only, matching the function's name and its sibling query. Widening it to every entity/media show is a legitimate alternative reading of "gets a show filter" and a bigger call — ask, don't decide unilaterally.

---

## (c) Spec corrections

1. **`extract_entities.py:547,551` is stale.** Those were correct before the 2026-09-02 sponsor-block commits (`d4eebf8`, `0df8588`) shifted the file. The live site is **:584-589** in `sanitize_mention` (verified by reading it, and by `git show d4eebf8~1:…`).
2. **"Set difference on Taddy uuid" is Taddy-only wording for a check that covers six shows via two identity schemes** — five Taddy-uuid shows plus Culture Gabfest's Megaphone RSS `<guid>` (Taddy won't transcribe Gabfest for rights reasons). Both already share one branch in `feed_recent_dates`; the new `feed_recent_episodes` mirrors that same branch. One mechanism, not two designs.
3. **No migration is needed for the `'loading'` status.** `ai_runs.status` is `VARCHAR(32)` with no CHECK constraint (`sql/001_ai_entity_schema.sql`:13). Phase 4 needs **zero** `sql/` files.
4. **There are two crash windows, not one.** (i) Between `insert_run`'s early commit and the first mention commit → a `completed` run with **zero** mentions, which `zero_mention_runs` already catches. (ii) Mid-loop after some mentions committed → a `completed` run with a **nonzero but wrong** count, which nothing catches today and is the actual gap. The one-transaction design collapses both, which is why `zero_mention_runs` alone was never sufficient.
5. **NEW — no reader caught this.** Extracting the row loop out of `main()` **breaks an existing test with a `ValueError`, not an assertion failure.** `tests/test_load_entity_batch.py:399-425` (`test_first_seen_as_ad_is_stamped_after_the_batch_not_during_it`) does `inspect.getsource(leb.main)` then `.index("for row in rows:")` / `.index("sponsor_stamps.append(")` / `.index("for entity_id, publish_date in sponsor_stamps:")` / `.index("record_first_seen_as_ad(")`. Once those live in `load_batch_rows`, every one of those `.index()` calls raises. **Repoint it at `inspect.getsource(leb.load_batch_rows)` and keep both the collect and the second pass inside that function** — the invariant it pins (a newest-first batch must not let an ad claim "first seen" before the older editorial mention lands) is real and must survive the refactor intact.
6. **Item 5's blocker is dissolved.** It could not decide whether the transactional fix was "early commit of a half-state" or "nothing persists without an explicit commit". Answer, verified: `common.py`'s `get_db_connection` never sets `autocommit`, so psycopg2's default `False` holds, and each helper's explicit `conn.commit()` makes each row individually durable. It is the *early-commit-of-a-half-state* bug. Item 3's design is the correct one.
7. **Two competing absence-alarm designs; take Item 2's.** Item 5 proposed querying `GET /actions/workflows/<wf>/runs?created=<date>` for "yesterday" — and flagged that it never verified the `created=` filter's semantics against the live API. Item 2's KV dispatch-log + `correlateRun` needs no unverified API behaviour, records the manual `?token=` path too, and pairs with decision 4's fleet-watchdog `/health` poll. Build Item 2's.
8. **GH_PAT needs no change.** Its scope is already "Actions: Read and write", which covers listing runs. No new scope, no second secret, no rotation. (Expiry is 2027-01-20 per `entities.yml`'s comment — worth Kevin knowing, since a PAT expiry silences both the dispatch alert and the verify no-op by design, leaving the watchdog's `/health` poll as the only surviving signal.)
9. **Two comments go stale when PR 1 ships.** `pulse.yml`'s header names "a Sentry Cron Monitor check-in" as the intended dead-trigger alarm — that guarantee is now met by fleet-watchdog polling `/health`, no new vendor. Update the line in PR 1. Separately, the "5 cron triggers" ceiling comments in `cloudflare-trigger/wrangler.toml` and fleet-watchdog's `README`/`wrangler.jsonc` are stale post the 2026-08-26 consolidation (4 slots free) — not load-bearing here since this design adds no cron, but stale doc is worse than none; fix in PR 1 or open a follow-up.
10. **4d moves into PR 2, not PR 4.** The plan lists the Taddy dedup fallback key alongside the other small honest-data fixes, but it edits the *same function* (`episode_url_key`) that PR 2 already refactors to delegate to `show_config.taddy_episode_url`, and the *same test file*. Splitting it across two PRs would guarantee a conflict for no benefit. One function, one owner, one PR.
11. **Keep `MAX(publish_date)` for display.** The plan's identity fix makes `db_latest` non-load-bearing for the *verdict*, but the Slack line's `(feed at X, we have Y)` wording is real UX. Select `publish_date` in the same bulk held-urls query and keep the message shape. Dropping it would be a silent regression traded for nothing.
12. **A rolling date window on the held-episodes query is forbidden.** It looks like an obvious optimisation and it is a trap: Culture Gabfest ended 2026-07-01 and its RSS still returns 15 pre-July episodes, so any window that eventually excludes them reports the entire show as missing. Bound by `show_id` if the table grows, never by date. Write the reason in the code.
13. **"`zero_mention_runs` gets a show filter" does not say which shows.** Scoped to `ai-daily-brief`, matching the function's name and its sibling query — the least-surprising, smallest change. Widening is a real decision for Kevin, not the implementer.
14. **Nothing in items 1, 2, 3, 4a–4f was already built.** Every gap was confirmed against current source on 2026-09-03, not inferred from the plan. The sponsor-block guard and decision 10's schema items are confirmed shipped and untouched by any of this.

---

## (d) What Kevin has to do

All of it belongs to **PR 1**. PRs 2, 3 and 4 need nothing from him but a merge — no DDL, no writes, no secrets.

**1. Create the KV namespace** (before PR 1 can deploy; the id goes in the PR diff). Personal **trimm** profile — check first, the wrong account is the failure mode this repo has already had:

```
cd ~/DevKev/personal/list-maker/cloudflare-trigger
npx wrangler whoami            # must say Kevin@trimm.co's Account. If it says Tecovas, STOP.
env -u CLOUDFLARE_API_TOKEN npx wrangler kv namespace create DISPATCH_LOG
```

Paste the returned `id` to the session — it goes into `wrangler.toml`'s new `[[kv_namespaces]]` block:

```toml
[[kv_namespaces]]
binding = "DISPATCH_LOG"
id = "<paste the id here>"
```

**2. Confirm the Worker's live URL** (fleet-watchdog needs it for `CRON_TARGETS.healthUrl`; it was not discoverable read-only):

```
cd ~/DevKev/personal/list-maker/cloudflare-trigger
env -u CLOUDFLARE_API_TOKEN npx wrangler deployments list
```

**3. Deploy list-maker-cron** — after PR 1 merges. Merging changes what the workflows do; **deploying** changes what the Worker asks for. Both are needed:

```
cd ~/DevKev/personal/list-maker/cloudflare-trigger
npx wrangler whoami            # trimm, again
env -u CLOUDFLARE_API_TOKEN npx wrangler deploy
curl -s https://<worker>.workers.dev/health    # expect {"worker":…,"last_fire":…,"last_verify":…}
```

`last_fire` is `null` until the next 20:30 UTC cron; that is expected, not a failure.

**4. Approve the fleet-watchdog companion.** `khglynn/self-hosted-mcps` is a repo Phase 4 otherwise never touches, so this needs an explicit yes before anything is pushed. Then deploy it — **trimm profile only, never Tecovas**, per both repos' README warnings:

```
cd ~/DevKev/personal/self-hosted-mcps/watchdog
npx wrangler whoami
env -u CLOUDFLARE_API_TOKEN npx wrangler deploy
```

**5. Merge each PR** (the repo has live deploys, so agents open, Kevin merges).

**6. One optional decision** — 4a's scope. Default is `ai-daily-brief` only. Say so if you want it widened to every entity/media show.

**Not needed, confirmed:** no `sql/` migration (the `'loading'` status needs no DDL); no DB writes; no new GitHub secret or PAT scope; no new Cloudflare cron trigger.

---

## (e) Merge order

```
Lane W (parallel, any time it is green + the KV id exists):
  PR 1  feat/worker-run-verification          → then Kevin deploys, then the watchdog companion

Lane P (strictly sequenced; each rebases on main after the previous merges):
  PR 2  fix/feed-check-by-episode-identity    (items 1 + 4d)
  PR 3  fix/transactional-batch-load          (item 3)
  PR 4  fix/honest-health-and-failure-paths   (items 4a,4b,4c,4e,4f)
```

**Why this order.** PR 2 before PR 3 because both edit `data_health.py` and PR 2's edit is the structural one (a new helper plus a rewritten check); PR 3's is additive. PR 4 last because it is the only PR that touches `run_checks()` alongside PR 3's two registrations, and because going last is what lets it own `import_transcripts.py` and `load_entity_batch.py` with zero contention. PR 1 is independent of all three and blocked only on Kevin's KV namespace, so it should not sit behind them.

**Per-PR gate**, per `docs/principles.md` and the plan's own "How to run it": one concern per commit, `pytest -q` + `node --test` green locally, CI green (watch it in the background — local-green is not CI-green), a triple-check pass, then Kevin merges. Anything touching production data stops for him.

**Acceptance for the phase**, restated as three things you can point at:
1. **A seeded mid-series gap fails the feed check** — PR 2's `held={A,C}, missing B, B older than A` test, which fails on today's code.
2. **A day with no entities run produces a Slack line** — PR 1, proven end-to-end by the first non-`success` verdict reaching Slack, not by the unit tests alone.
3. **Every FAIL in the health run is actionable** — PR 4's 4f, ids in the details of the three checks that report bare counts today.

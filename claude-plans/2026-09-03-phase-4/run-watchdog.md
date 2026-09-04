# Item 2 — Alarm on a run that never started or never finished

## What exists today

**`cloudflare-trigger/worker.js`** (repo `khglynn/list-maker`, deployed as Worker `list-maker-cron` on the **trimm** personal Cloudflare account, `account_id 759a850a3af3e2fbf1ab036e7eb2f231`):

- `dispatchesFor(when)` (L47-68) — pure, exported, tested. Given a `Date`, returns `[{workflow, inputs}]` for that fire. This is the only exported/tested function today.
- `notifyFailure(env, message)` (L72-88) — logs, and if `SLACK_WEBHOOK_URL` is set, POSTs one hardcoded message shape: `":rotating_light: *list-maker cron trigger FAILED* — the pipeline was NOT started.\n${message}"`. Never throws.
- `dispatch(env, workflow, inputs)` (L90-111) — POSTs `workflow_dispatch` to `https://api.github.com/repos/khglynn/list-maker/actions/workflows/${workflow}/dispatches`. Throws on non-2xx. **The GitHub API returns 204 No Content on success — no run id, no Location header.** This is the entire gap: once this call returns, the Worker has no way to find out what happened downstream.
- `scheduled(event, env, ctx)` (L115-136) — guards on `GH_PAT` present and `event.cron === DAILY_CRON`, then fans out `dispatchesFor(now)` through `dispatch()`, each call individually `.catch()`-wrapped into `notifyFailure`. **No record of a successful dispatch is kept anywhere** — success and forgetting are the same code path.
- `fetch(request, env)` (L142-163) — token-gated manual trigger (`?token=...&workflow=...&show_id=...`), used both for hand-triggering and as the deploy-verification step in README.md. Every request needs `GH_PAT` and the right `token`; there is no unauthenticated route today (no `/health`).

**`cloudflare-trigger/wrangler.toml`** — `account_id`, one `[triggers].crons` entry (`"30 20 * * *"`). **No `kv_namespaces` block — no KV bound to this Worker at all today.** Comment block says the personal account is at the "5 cron trigger" ceiling; that was true before 2026-08-26, when this Worker itself held all 5. It was consolidated to **one** cron that day specifically to free slots for `remembrall-hub`. **The account currently has 4 free cron-trigger slots** (see Spec corrections #3) — not load-bearing for this design (it needs none), but worth knowing.

**`cloudflare-trigger/worker.test.js`** — `node --test`, zero dependencies (package.json: "No dependencies on purpose — nothing to audit, nothing to drift"). Only tests `dispatchesFor`. Run in CI by `.github/workflows/test.yml`'s last step, `node --test cloudflare-trigger/worker.test.js`, on a bare `ubuntu-latest` runner with **no `actions/setup-node` step** — whatever Node ships on the image (currently Node 20+, which has `fetch` and `node:test`'s `mock` built in, but nothing beyond core is available or should be assumed).

**GH_PAT** — fine-grained PAT for `khglynn/list-maker`, scope **"Actions: Read and write"** (per worker.js's own header comment and README). Read-and-write already covers reading workflow runs — **no new scope or second secret is needed** to list runs.

**The 4 workflows the Worker ever dispatches** (confirmed by reading all five `.yml` files): `entities.yml`, `pipeline.yml`, `eval.yml`, `blogs.yml`. Each declares `concurrency: {group: github.workflow, cancel-in-progress: false}` — at most one run of a given workflow is ever in flight, queued rather than parallel. `pulse.yml` is **never** dispatched directly by the Worker — `entities.yml` calls it via `workflow_call` (`uses: ./.github/workflows/pulse.yml`) as an internal job (`entities.yml` L197-206) when `inputs.pulse == 'true'`. A pulse-job failure flips `entities.yml`'s own run `conclusion` to `failure` (default GitHub Actions behavior for a failed job with no `continue-on-error`), so pulse failures are already covered by watching `entities.yml`'s run outcome — no separate tracking needed.

**`self-hosted-mcps/watchdog`** (repo `khglynn/self-hosted-mcps`? — local path `~/DevKev/personal/self-hosted-mcps/watchdog`), Worker `fleet-watchdog`, live at `https://fleet-watchdog.kevinhg.workers.dev`, also on the **trimm** personal Cloudflare account:

- `src/index.js` `SERVICES` array (L32-49) — five fixed-shape MCP targets: `{name, account, baseUrl?, oauthStore?}`. `url(name)` (L59-60) builds a Cloud-Run URL from a template; `baseUrl` overrides it for the one non-templated service (Spotify).
- `probe(svc)` (L76-140) — GET `/health` (liveness + warms), then GET `/authorize?client_id=<never-registered>` expecting `400` (proves the OAuth store, not the app, is alive), with a short retry to tell "cold start" from "actually down." Returns `{name, healthy, cold, problems}`.
- `reconcile(env, result, now, collected)` (L186-240) — reads `FLEET_STATE` KV key `status:${name}`, alerts **only on state transitions** (healthy→down, down→healthy) plus a 6-hourly re-nag while down and a daily cold-start note, writes the new state back. This transition-based alerting is exactly the pattern Item 2 should reuse conceptually (though Item 2's design below doesn't need its own transition state — see design).
- `runChecks`/`statusBody` (L242-268) — `Promise.all` over `SERVICES.map(probe)`, then `reconcile` sequentially, then builds `{fleet, checked_at, alerts, services}`; `statusBody` does `SERVICES.find((s) => s.name === r.name).account` (L262) — **this line assumes every probed result has a matching `SERVICES` entry**; it will throw if a differently-shaped target (like a cron-health check) is added to the probed set without a matching change here.
- `fetch(request, env)` (L287-317) — header/query token gates the full run (probes + KV writes + returns alerts); ungated path runs probes read-only, no KV writes, no alerts, returns 200/503.
- `wrangler.jsonc` — `kv_namespaces: [{binding: "FLEET_STATE", id: "99f236d8..."}]`; `triggers` commented out (schedule lives in GitHub Actions `fleet-watchdog.yml` on a public fork, every 10 min, specifically because this account was previously maxed on Cloudflare cron slots — see spec correction #3, that constraint is gone now but nothing here currently needs to change because of it).

## What must change

### A. `cloudflare-trigger/worker.js`

1. **New pure, exported functions** (the only things the tests touch directly):
   - `correlateRun(runs, dispatchedAtIso, toleranceMs = 5 * 60 * 1000)` — `runs` is the array from GitHub's `GET .../workflows/{workflow}/runs?event=workflow_dispatch` response (`workflow_runs`), each with at least `{id, status, conclusion, created_at, html_url}`. Filters to `created_at >= dispatchedAt - toleranceMs`, sorts ascending by `created_at`, returns the earliest (i.e. the run closest to and after the actual dispatch — not a later manual re-run that might also be in the list), or `null` if none.
   - `verdictFor(run)` — `null` → `"missing"`; `run.status !== "completed"` → `"stuck-" + run.status` (e.g. `stuck-in_progress`, `stuck-queued` — a run 20+ hours old that's still not completed is its own anomaly worth alerting on, not a normal state to wait out); else `run.conclusion` verbatim (`"success" | "failure" | "cancelled" | "timed_out" | "action_required" | ...`).
   - `dispatchKey(workflow, dispatchedAtIso)` — `` `dispatch:${workflow}:${dispatchedAtIso}` ``. Keyed by the full ISO timestamp (not just a date) so same-day re-dispatches (a manual retrigger, or `entities.yml` running every day) never collide or silently overwrite each other's record before verification.

2. **`dispatch(env, workflow, inputs)`** — after the existing `resp.ok` success path, add one KV write recording the dispatch:
   ```js
   const dispatchedAt = new Date().toISOString();
   await env.DISPATCH_LOG.put(
     dispatchKey(workflow, dispatchedAt),
     JSON.stringify({ workflow, dispatchedAt }),
     { expirationTtl: 3 * 24 * 3600 } // self-cleans if verify never runs (e.g. Worker broken)
   );
   ```
   Recording lives inside `dispatch()` itself (not duplicated in `scheduled()`/`fetch()`) so **both** the cron path and the manual `?token=` trigger path get verified — the manual trigger is literally how the README's own deploy-verification step works, so it should self-check too. Record only on success: a failed dispatch already alerts immediately via the existing `.catch(notifyFailure)` path; recording a failed dispatch would create a KV entry with nothing to correlate against and double-alert 24h later for the same root cause.

3. **`fetchRunsForWorkflow(env, workflow)`** — thin GitHub API wrapper, not unit-tested directly (matches existing precedent: `dispatch()` itself isn't unit-tested either — only the pure logic it delegates to is):
   ```js
   async function fetchRunsForWorkflow(env, workflow) {
     const resp = await fetch(
       `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/runs?event=workflow_dispatch&per_page=10`,
       { headers: { Authorization: `Bearer ${env.GH_PAT}`, Accept: "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "list-maker-cron" } }
     );
     if (!resp.ok) throw new Error(`list runs for ${workflow} failed: ${resp.status}`);
     return (await resp.json()).workflow_runs;
   }
   ```

4. **`verifyPreviousDispatches(env, now)`** — the watchdog step:
   ```js
   const VERIFY_AFTER_MS = 20 * 60 * 60 * 1000; // longest workflow timeout is 70 min; 20h is comfortably past "done"

   async function verifyPreviousDispatches(env, now) {
     if (!env.GH_PAT) return; // the missing-PAT case already alerts on the dispatch side
     const list = await env.DISPATCH_LOG.list({ prefix: "dispatch:" });
     for (const k of list.keys) {
       const record = await env.DISPATCH_LOG.get(k.name, { type: "json" });
       if (!record) continue;
       const age = now.getTime() - new Date(record.dispatchedAt).getTime();
       if (age < VERIFY_AFTER_MS) continue; // too recent to judge yet — leave it, check next fire
       try {
         const runs = await fetchRunsForWorkflow(env, record.workflow);
         const run = correlateRun(runs, record.dispatchedAt);
         const verdict = verdictFor(run);
         if (verdict !== "success") {
           await notifyVerifyIssue(env, record, verdict, run);
         }
         await env.DISPATCH_LOG.delete(k.name); // processed either way — don't re-alert next fire
       } catch (e) {
         // Leave the record in place — TTL gives ~3 more daily fires to retry a transient
         // GitHub API failure before this alert would go silent.
         await notifyFailure(env, `verify ${record.workflow} (dispatched ${record.dispatchedAt}) failed — ${e.message}`);
       }
     }
   }
   ```

5. **`notifyVerifyIssue(env, record, verdict, run)`** — a second, differently-worded alert (the existing `notifyFailure` text "the pipeline was NOT started" is *wrong* for `cancelled`/`failed`/`stuck-*` — those runs did start). Extract the shared Slack-POST/console-fallback body out of `notifyFailure` into a small `postSlack(env, text)` helper so both alert functions share it instead of duplicating the try/catch:
   ```js
   async function postSlack(env, text) {
     console.error(`list-maker-cron: ${text}`);
     if (!env.SLACK_WEBHOOK_URL) return;
     try {
       await fetch(env.SLACK_WEBHOOK_URL, { method: "POST", headers: {"Content-Type":"application/json"},
         body: JSON.stringify({ text }) });
     } catch (e) { console.error(`list-maker-cron: Slack notify also failed — ${e.message}`); }
   }
   async function notifyFailure(env, message) {
     await postSlack(env, `:rotating_light: *list-maker cron trigger FAILED* — the pipeline was NOT started.\n${message}`);
   }
   async function notifyVerifyIssue(env, record, verdict, run) {
     const label = { missing: "never started", cancelled: "cancelled", failure: "failed" }[verdict]
       ?? verdict; // stuck-* etc. fall through verbatim
     const link = run ? `\n${run.html_url}` : "";
     await postSlack(env,
       `:rotating_light: *list-maker: ${record.workflow} run ${label}* — dispatched ${record.dispatchedAt}, ` +
       `no successful run followed.${link}`);
   }
   ```

6. **`/health` route + last-fire/last-verify state**, for `fleet-watchdog` to poll. In `scheduled()`, unconditionally (before the `GH_PAT`/`DAILY_CRON` guards, so a broken PAT doesn't also blind the freshness signal) record that the Worker fired, then run verify, then dispatch:
   ```js
   async scheduled(event, env, ctx) {
     const now = new Date(event.scheduledTime);
     ctx.waitUntil(env.DISPATCH_LOG.put("meta:last_fire", JSON.stringify({ at: now.toISOString(), cron: event.cron })));
     ctx.waitUntil(
       verifyPreviousDispatches(env, now)
         .then((results) => env.DISPATCH_LOG.put("meta:last_verify", JSON.stringify({ at: now.toISOString(), results })))
         .catch((e) => notifyFailure(env, `verify pass crashed — ${e.message}`))
     );
     if (!env.GH_PAT) { ... unchanged ... }
     ...
   }
   ```
   (`verifyPreviousDispatches` should return a small results array — `[{workflow, dispatchedAt, verdict}]` — instead of `void`, purely so `/health` has something to show; the alerting side-effect stays as-is.)

   In `fetch()`, add an unauthenticated branch **before** the existing `GH_PAT`/token checks (those stay exactly as-is for every other path):
   ```js
   async fetch(request, env) {
     const url = new URL(request.url);
     if (url.pathname === "/health") {
       const lastFire = await env.DISPATCH_LOG.get("meta:last_fire", { type: "json" });
       const lastVerify = await env.DISPATCH_LOG.get("meta:last_verify", { type: "json" });
       return new Response(JSON.stringify({ worker: "list-maker-cron", last_fire: lastFire, last_verify: lastVerify }, null, 2),
         { status: 200, headers: { "Content-Type": "application/json" } });
     }
     if (!env.GH_PAT) return new Response("GH_PAT not set\n", { status: 500 });
     ... unchanged ...
   }
   ```
   `/health` is intentionally read-only and ungated, same reasoning fleet-watchdog already documents for its own ungated path: it can never spend Slack or trigger a dispatch, so it's safe to leave open to `fleet-watchdog`'s poller.

### B. `cloudflare-trigger/wrangler.toml`

Add a KV binding (none exists today):
```toml
[[kv_namespaces]]
binding = "DISPATCH_LOG"
id = "<new-namespace-id>"
```
The namespace itself must be created once (`wrangler kv namespace create DISPATCH_LOG`) — this is a live-account write, so it's a **Kevin** step (see needs_kevin), same as the original `GH_PAT`/`SLACK_WEBHOOK_URL` secret-puts in README.md's deploy steps. Redeploy (`wrangler deploy`) picks up the new binding.

### C. `cloudflare-trigger/README.md`

Add `DISPATCH_LOG` to the bindings list, add a deploy step for `wrangler kv namespace create DISPATCH_LOG` + pasting the id into `wrangler.toml`, and document `GET /health` next to the existing manual-trigger docs.

### D. `self-hosted-mcps/watchdog/src/index.js`

`fleet-watchdog` needs a second, differently-shaped kind of target: a JSON-status GET instead of the two-request MCP OAuth-store probe. Minimal changes:

1. New const, parallel to `SERVICES`:
   ```js
   const CRON_TARGETS = [
     { name: "list-maker-cron", healthUrl: "https://list-maker-cron.<subdomain>.workers.dev/health", maxAgeMs: 26 * 60 * 60 * 1000 },
   ];
   ```
   `<subdomain>` is the actual deployed `workers.dev` subdomain for the trimm account — **not confirmed in this pass** (read-only; the URL isn't recorded in either repo's docs, only the pattern `https://<worker-name>.<subdomain>.workers.dev/` used for `fleet-watchdog` itself). `maxAgeMs` set to 26h (daily 24h cadence + slack) rather than exactly 24h, so ordinary jitter in Worker cron trigger timing doesn't false-positive.

2. New probe function, same result shape `{name, healthy, cold, problems}` as `probe()` so it can flow through the existing `reconcile()`/`runChecks()`/`statusBody()` machinery unchanged in spirit:
   ```js
   async function probeCronHealth(target) {
     const problems = [];
     try {
       const res = await fetch(target.healthUrl, { signal: AbortSignal.timeout(10_000) });
       if (!res.ok) { problems.push(`/health returned ${res.status}`); }
       else {
         const body = await res.json();
         const lastFireAt = body.last_fire?.at ? new Date(body.last_fire.at).getTime() : NaN;
         if (!Number.isFinite(lastFireAt) || Date.now() - lastFireAt > target.maxAgeMs) {
           problems.push(`stale — last fire ${body.last_fire?.at ?? "unknown"}`);
         }
       }
     } catch (err) {
       problems.push(`/health unreachable: ${err.message}`);
     }
     return { name: target.name, healthy: problems.length === 0, cold: false, problems };
   }
   ```
   This is the true dead-man's-switch: it's the one check that still fires if `list-maker-cron` stops firing *entirely* (cron deleted, account issue, Worker crashing before any `notifyFailure` call) — the one failure mode nothing inside `list-maker-cron` itself can ever report on its own. `pulse.yml`'s own header comment already names this exact gap ("For an ACTIVE dead-trigger alert, pair with a Sentry Cron Monitor check-in") — this closes it without adding a new vendor.

3. Wire it into `runChecks`/`statusBody` (L242-268): `Promise.all([...SERVICES.map(probe), ...CRON_TARGETS.map(probeCronHealth)])`, and fix `statusBody`'s `SERVICES.find((s) => s.name === r.name).account` (L262) to not throw for a `CRON_TARGETS` entry — e.g. `SERVICES.find((s) => s.name === r.name)?.account ?? null`.

4. `wrangler.jsonc` needs no change — `probeCronHealth` uses no new binding, only `fetch`.

## Design summary (smallest correct version)

- **No client-side dispatch id, no workflow-YAML edits.** Correlation is done by scoping the GitHub "list runs" call to the one workflow file and filtering by a time window after the recorded dispatch timestamp (`correlateRun`). The existing `concurrency: {group: github.workflow}` on all four workflows already guarantees at most one legitimate run per workflow per fire, so timestamp-scoped correlation is unambiguous in the normal case and correctly prefers the earliest post-dispatch run over any later manual re-run.
- **No new Cloudflare cron.** The check rides the existing single daily fire: `scheduled()` now does record-last-fire → verify-yesterday's-dispatches → (unchanged) dispatch-today's-workflows, all still isolated per-step so one failure never blocks another.
- **One new KV namespace** (`DISPATCH_LOG`) on `list-maker-cron`, holding short-lived (`3d` TTL) per-dispatch records plus two `meta:*` keys for `/health`.
- **`/health` is new and ungated** on `list-maker-cron`, mirroring `fleet-watchdog`'s own ungated-status convention.
- **`fleet-watchdog` gains a second probe kind** (`CRON_TARGETS`/`probeCronHealth`) alongside its existing `SERVICES`/`probe` MCP-OAuth kind, feeding the same transition-based `reconcile()` alerting fleet-watchdog already has — this design deliberately does **not** duplicate transition/re-nag logic inside `list-maker-cron`; `list-maker-cron` only ever posts once per bad verdict per dispatch (the KV record is deleted after processing either way), and the *"is the Worker even alive"* question is fleet-watchdog's job via its existing 6-hourly re-nag machinery.

## Tests (hermetic — `cloudflare-trigger/worker.test.js`, `node --test`, no fetch/KV mocking needed)

All new logic worth testing is pure and exported, same pattern as the existing `dispatchesFor` tests:

- **`correlateRun`**
  - a run created a few seconds after `dispatchedAt` → picked.
  - a run created *before* `dispatchedAt` (a leftover run from the previous day, still inside GitHub's most-recent-10 window) → filtered out, not picked.
  - empty `runs` array → `null`.
  - two runs after `dispatchedAt` — one right after (the scheduled one), one hours later (a manual re-run someone kicked off afterward) → the **earlier** one is picked, not the later manual one.
  - a run exactly at the tolerance boundary (`dispatchedAt - toleranceMs`) → included (inclusive boundary, pin the choice explicitly).
- **`verdictFor`**
  - `null` → `"missing"`.
  - `{status:"completed", conclusion:"success"}` → `"success"`.
  - `{status:"completed", conclusion:"failure"}` → `"failure"`.
  - `{status:"completed", conclusion:"cancelled"}` → `"cancelled"`.
  - `{status:"in_progress"}` → `"stuck-in_progress"`.
  - `{status:"queued"}` → `"stuck-queued"`.
- **`dispatchKey`** — format pin, e.g. `dispatchKey("pipeline.yml", "2026-09-03T20:30:01.234Z") === "dispatch:pipeline.yml:2026-09-03T20:30:01.234Z"` — cheap but guards against an accidental format change silently breaking `verifyPreviousDispatches`'s `list({prefix:"dispatch:"})` scan.
- Existing `dispatchesFor` tests are untouched.

`fetchRunsForWorkflow`, `dispatch`'s new KV write, and `verifyPreviousDispatches`/`notifyVerifyIssue` stay **untested at the network/KV level**, matching the file's existing precedent (`dispatch()` and `notifyFailure()` aren't unit-tested today either — only the pure decision logic they delegate to is). If deeper coverage is wanted later, Node's built-in `node:test` `mock` module (no new dependency, available on the CI runner's stock Node) can stub `globalThis.fetch` and a fake `DISPATCH_LOG` object (`{get,put,list,delete}` as plain async functions) to exercise `verifyPreviousDispatches` end-to-end — flagging this as optional, not required for correctness, since the pure functions already carry the actual risk.

`self-hosted-mcps/watchdog` — no test harness currently exists in this repo (not investigated further; out of the read-only tour for `list-maker`). If one is added, `probeCronHealth`'s freshness check should be split into a pure `isCronStale(lastFireAt, now, maxAgeMs)` helper so it's testable without a real fetch, same discipline as above.

## Risks

- **Clock/timezone drift in the correlation window.** `correlateRun`'s tolerance (5 min) assumes GitHub starts a queued run within a few minutes of the dispatch POST. A GitHub Actions queue backlog (rare, but real on shared runners) could push actual start past the tolerance and cause a false "missing" verdict. Mitigation: the tolerance only affects which run is *picked from the list*, not whether one exists — widening it is a one-line, low-risk fix if false positives show up; starting at 5 min is a deliberate "alert if in doubt" choice per the pipeline's own stated bias (data quality > silence).
- **KV eventual consistency.** Cloudflare KV writes can take up to ~60s to be globally consistent. Since `dispatch()` writes the record and `verifyPreviousDispatches` reads it roughly 24h later (never in the same request), this is not a practical risk here — noted only because it *would* matter if the verify window were ever shortened.
- **A manual re-run inside the 20h grace window.** If Kevin manually re-runs a workflow from the Actions UI in the hours right after a scheduled dispatch, and the manual run also shows up in the `per_page=10` list, `correlateRun`'s "earliest after dispatch" rule should still pick the original scheduled run, not the manual one — verified in tests above. If Kevin's manual run happens to land within the 5-minute tolerance window of the *next* scheduled dispatch (unlikely given the daily cadence), it's a low-stakes false match, not a missed alarm.
- **`GH_PAT` expiry stops both dispatch *and* verify at once.** `verifyPreviousDispatches` explicitly no-ops when `GH_PAT` is absent, so a dead PAT doesn't generate a confusing second alarm on top of the existing "GH_PAT secret not set" one — but it also means once the PAT expires, the fleet-watchdog `/health` freshness check (which only reflects `last_fire`, always written regardless of `GH_PAT`) is the *only* surviving signal, since neither dispatch alerts nor verify alerts fire. This is by design (avoids alert duplication) but worth Kevin knowing: the PAT's expiry (2027-01-20, per `entities.yml`'s comment) is a real forward date to remember.
- **Cross-repo change.** The `fleet-watchdog` half of this item lives in a different repo (`self-hosted-mcps`) than the rest of Phase 4. It shares the same trimm Cloudflare account and the same "Kevin deploys personal, never Tecovas" discipline, but an implementing agent needs to open that repo separately — it is not part of `list-maker`'s own PR surface.
- **`<subdomain>` for `CRON_TARGETS`.** The actual deployed `list-maker-cron` `workers.dev` URL was not discoverable read-only in this pass (not recorded in either repo's docs — only `fleet-watchdog`'s own URL is documented). Needs a live check (`wrangler deployments list` or the Cloudflare dashboard) before the fleet-watchdog change can be written for real.

## needs_kevin

- Create the KV namespace: `wrangler kv namespace create DISPATCH_LOG` (from the trimm profile, `env -u CLOUDFLARE_API_TOKEN wrangler ...` per the repo's existing convention) and paste the returned id into `cloudflare-trigger/wrangler.toml`'s new `[[kv_namespaces]]` block.
- `wrangler deploy` for both `list-maker-cron` (new binding + code) and, separately, `fleet-watchdog` (new `CRON_TARGETS` probe) — both from the trimm profile, never Tecovas, per both repos' existing README warnings.
- Confirm `list-maker-cron`'s live `workers.dev` subdomain (for `fleet-watchdog`'s `CRON_TARGETS.healthUrl`) — check via `wrangler deployments list` or the Cloudflare dashboard.
- Approve pushing to `khglynn/self-hosted-mcps` (a repo not otherwise touched by this Phase 4 pass) if the fleet-watchdog half is built in the same session.

## Spec corrections

1. **GH_PAT already has sufficient scope.** "Actions: Read and write" (the existing secret) already covers `GET .../actions/workflows/{id}/runs` — no new scope, no second secret, no rotation needed to add the verify step.
2. **`workflow_dispatch` genuinely returns no run id** (204 No Content, confirmed by reading `dispatch()` — it never reads a response body on success). The spec's own phrasing already anticipated this ("the workflow_dispatch response has no run id — how do you correlate?") and offered a client-side-id input as one option; this design deliberately picks the **non-invasive** alternative (time-window + workflow-scoped correlation) over adding a `dispatch_id` input to all four workflow YAMLs, because the existing `concurrency: {group: github.workflow}` already makes the correlation unambiguous without touching those files.
3. **The "5 cron trigger ceiling" account note is stale.** `wrangler.toml`'s own comment (and `fleet-watchdog`'s README/`wrangler.jsonc` comments, which say "all five belong to list-maker-cron") predate the 2026-08-26 consolidation of `list-maker-cron` down to **one** cron. The account currently has 4 free Workers-cron slots on the free plan. Not load-bearing for this design (it adds no cron), but both repos' comments are now slightly wrong and worth a one-line fix whenever someone's next in either file — not done here since it's outside Phase 4's scope.
4. **`pulse.yml` is out of scope for direct dispatch-tracking**, correctly — it's never called via `workflow_dispatch` from the Worker (it's a `workflow_call` job inside `entities.yml`), so it has no independent dispatch record to correlate. A pulse failure already flips `entities.yml`'s run conclusion, so watching `entities.yml`'s verdict covers it.
5. **This item overlaps a documented-but-unbuilt alternative.** `pulse.yml`'s own header comment names "a Sentry Cron Monitor check-in in the Cloudflare Worker" as the intended dead-man's-switch. This design achieves the same guarantee (an external, independent freshness check that fires even if `list-maker-cron` itself goes fully silent) via `fleet-watchdog` polling `/health` instead, avoiding a new vendor dependency. That comment in `pulse.yml` becomes stale once this ships and should get a one-line update pointing at `fleet-watchdog` instead — not done in this read-only pass.
6. **Decision 10 / sponsor-block items confirmed out of scope**, per the task framing — not touched, not re-verified beyond noting they're unrelated to this item's files.

## Size estimate

**L** — spans two repos (`list-maker`'s `cloudflare-trigger/` and `self-hosted-mcps/watchdog/`), a new live Cloudflare resource (KV namespace) that only Kevin can create, and two separate deploys. The core `list-maker-cron` logic (worker.js + wrangler.toml + tests) alone is closer to **M**.

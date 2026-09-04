# list-maker-cron — durable pipeline trigger (Cloudflare Worker)

This Worker is the **durable control plane** for the whole pipeline. ONE cron
(`30 20 * * *`, ~3:30pm CT) fires daily; `dispatchesFor()` in `worker.js` decides
what that day dispatches via GitHub `workflow_dispatch` (pinned by `worker.test.js`):

| When (UTC 20:30)  | Workflow                    | What runs                                          |
|-------------------|-----------------------------|----------------------------------------------------|
| every day         | `entities.yml`              | AI Daily, Hard Fork, PCHH, Culture Gabfest → Notion |
| Mon               | `pipeline.yml` show_id=2    | This American Life (music → Spotify)               |
| Wed, Fri          | `pipeline.yml` show_id=1    | Switched on Pop (music → Spotify)                  |
| Mon               | `eval.yml`, `blogs.yml`     | extraction eval; curated intake (discover + judge)    |
| 1st + 15th        | `entities.yml` `pulse=true` | the biweekly Slack pulse, run AFTER that day's import |

**Why it exists:** GitHub auto-disables `schedule:` crons in public repos after 60
days of inactivity — silently. A Cloudflare Worker Cron has no such limit, so every
workflow's `schedule:` block is gone and this Worker is their only trigger. Why one
cron: the Workers Free plan caps cron triggers at 5 **per account** (verified
2026-08-26) and other projects need slots. The single cron string lives in two places
that must match: `wrangler.toml [triggers].crons` and `DAILY_CRON` in `worker.js` —
the Worker alerts to Slack if they ever drift.

## Did the work actually run? (since 2026-09-03)

Dispatching is not the same as running, and until this landed the difference was
invisible: a *successful* dispatch was recorded nowhere, so success and forgetting
were the same code path. That is how 2026-08-06 (a run GitHub cancelled) and
2026-08-16 (no run at all) both passed in silence.

Now every successful dispatch writes a record to the `DISPATCH_LOG` KV namespace,
and the **next** day's fire reads yesterday's records back, asks GitHub what became
of each run, and posts to Slack anything that is not `success`. Three states, worded
apart on purpose — an alert that blurs them stops meaning anything:

| Slack line | Means |
|---|---|
| `cron trigger FAILED — the pipeline was NOT started` | the dispatch never left (expired PAT, GitHub down) |
| `<workflow> never started / failed / was cancelled` | the dispatch landed; the run did not succeed |
| `⚠️ … could not be checked` | the *verifier* failed. The run may be fine. Retried next fire |

Correlation is by time, not by run id: `workflow_dispatch` answers `204 No Content`
and GitHub's run objects carry no record of the inputs a dispatch supplied, so there
is nothing to match on but the clock. Scoping the lookup to one workflow file is
enough because all four workflows carry `concurrency: {group: github.workflow}`, so
at most one legitimate run is ever in flight. When two runs follow a dispatch (a
scheduled run plus a manual re-run — this really happened on 2026-09-02) the
**earliest** wins, so a re-run can never bury the failure the alarm exists to report.

**`GET /health`** — ungated, no secrets, starts no work:

```json
{ "worker": "list-maker-cron",
  "last_fire":   { "at": "…", "cron": "30 20 * * *" },
  "last_verify": { "at": "…", "results": [ { "workflow": "…", "verdict": "success" } ] } }
```

`last_fire` is written *before* every guard in `scheduled()`, so it survives an
expired PAT, a drifted cron string and a dead Slack. That is deliberate: it is the
one signal an outside watcher can use to tell "this Worker is alive" from "this
Worker is gone" — the single failure no code inside a Worker can ever report about
itself. `fleet-watchdog` (in `khglynn/self-hosted-mcps`) polls it and alerts when
`last_fire` is older than 26h.

## Deploy (personal **trimm** Cloudflare — account_id already set)

Deploy from a **personal-profile** Claude session or your own terminal, never from
the Tecovas profile. Check first — `npx wrangler whoami` must say
`Kevin@trimm.co's Account` (verified 2026-09-01: the personal profile's
`CLOUDFLARE_API_TOKEN` is the trimm token; a bare terminal falls back to your trimm
OAuth login). If it names Tecovas, stop. Then: `npx wrangler deploy`.

`account_id` is already filled in `wrangler.toml` (`759a850a…`, kevin@trimm.co).

**A routine redeploy is just those two commands** — `whoami`, then `deploy`. The
numbered list below is FIRST-TIME setup: steps 1–4 were done on 2026-06-11 and are
kept as the record of what this Worker needs in order to exist. **Step 0 is new
(2026-09-03) and has not been done yet** — the verification code needs it.

0. **KV namespace (once, before the first deploy of the verification code):**
   ```
   env -u CLOUDFLARE_API_TOKEN npx wrangler kv namespace create DISPATCH_LOG
   ```
   Paste the id it prints into `wrangler.toml`'s `[[kv_namespaces]]` block, which
   ships with a placeholder. This is a live-account write, so it is a Kevin step —
   same as the `GH_PAT` secret below. The namespace holds only short-lived dispatch
   receipts (3-day TTL) plus the two `meta:*` keys `/health` returns.
1. **Deploy:** `env -u CLOUDFLARE_API_TOKEN wrangler deploy` (creates the Worker; it
   no-ops safely until `GH_PAT` is set).
2. **GitHub PAT (Kevin):** create a fine-grained PAT for `khglynn/list-maker` with
   **Actions: Read and write**, then store it:
   `env -u CLOUDFLARE_API_TOKEN wrangler secret put GH_PAT` (paste at the prompt).
   - **Also set the Slack webhook** (recommended — makes a failed *trigger* alert,
     not just a failed run): `env -u CLOUDFLARE_API_TOKEN wrangler secret put SLACK_WEBHOOK_URL`
     (paste the same `#list-maker` webhook used by the GitHub workflows).
3. **Verify:** set a one-off trigger token and hit the Worker once, then confirm a run
   appears under the repo's Actions tab:
   - `env -u CLOUDFLARE_API_TOKEN wrangler secret put TRIGGER_TOKEN` (paste any value)
   - `curl "https://list-maker-cron.kevinhg.workers.dev/?token=<value>"`
4. **Remove both `schedule:` blocks** from `.github/workflows/entities.yml` and
   `.github/workflows/pipeline.yml` — the Worker is now the durable trigger (one
   commit). *Done 2026-06-11; every workflow here is `workflow_dispatch`-only now.*

## Endpoints

Live URL: `https://list-maker-cron.kevinhg.workers.dev` (verified 2026-09-03).

```
GET /health                                                           # ungated
GET /?token=<TRIGGER_TOKEN>                                           # entities.yml
GET /?token=<TRIGGER_TOKEN>&workflow=pipeline.yml&show_id=1
```

Without `TRIGGER_TOKEN` the manual trigger returns 403 (the cron is unaffected).
`/health` is answered before both the token gate and the `GH_PAT` check, because an
unset PAT is exactly the kind of outage it exists to stay legible through. A manual
trigger records itself like any other dispatch, so it gets verified too.

Right after a deploy, `/health` reports `last_fire: null` until the next 20:30 UTC
cron. That is expected, not a failure.

## Local test
`env -u CLOUDFLARE_API_TOKEN wrangler dev`, then trigger a scheduled event.

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

## Deploy (personal **trimm** Cloudflare — account_id already set)

Deploy from a **personal-profile** Claude session or your own terminal, never from
the Tecovas profile. Check first — `npx wrangler whoami` must say
`Kevin@trimm.co's Account` (verified 2026-09-01: the personal profile's
`CLOUDFLARE_API_TOKEN` is the trimm token; a bare terminal falls back to your trimm
OAuth login). If it names Tecovas, stop. Then: `npx wrangler deploy`.

`account_id` is already filled in `wrangler.toml` (`759a850a…`, kevin@trimm.co).

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
   - `curl "https://list-maker-cron.<subdomain>.workers.dev/?token=<value>"`
4. **Remove both `schedule:` blocks** from `.github/workflows/entities.yml` and
   `.github/workflows/pipeline.yml` — the Worker is now the durable trigger (one commit).

## Manual trigger (optional, after `TRIGGER_TOKEN` is set)

```
GET https://<worker-url>/?token=<TRIGGER_TOKEN>                       # entities.yml
GET https://<worker-url>/?token=<TRIGGER_TOKEN>&workflow=pipeline.yml&show_id=1
```

Without `TRIGGER_TOKEN` the HTTP endpoint returns 403 (the cron is unaffected).

## Local test
`env -u CLOUDFLARE_API_TOKEN wrangler dev`, then trigger a scheduled event.

# list-maker-cron — durable pipeline trigger (Cloudflare Worker)

This Worker is the **durable control plane** for the whole pipeline. On a set of
crons it calls GitHub `workflow_dispatch` for both workflows:

| Cron (UTC)      | Workflow       | What runs                                   |
|-----------------|----------------|---------------------------------------------|
| `0 11 * * *`    | `entities.yml` | AI Daily, Hard Fork, PCHH, Culture Gabfest  |
| `0 10 * * 1`    | `pipeline.yml` | This American Life (music → Spotify)        |
| `0 10 * * 3`    | `pipeline.yml` | Switched on Pop (music → Spotify)           |
| `0 10 * * 5`    | `pipeline.yml` | Switched on Pop (music → Spotify)           |

**Why it exists:** GitHub auto-disables `schedule:` crons in public repos after 60
days of inactivity — silently. A Cloudflare Worker Cron has no such limit. Once this
is deployed, the `schedule:` blocks are removed from **both** workflows, so this
Worker is their only trigger. (The cron strings live in two places that must stay in
sync: `wrangler.toml [triggers].crons` and the `SCHEDULE` map in `worker.js`.)

## Deploy (personal **trimm** Cloudflare — account_id already set)

The global `CLOUDFLARE_API_TOKEN` is the **Tecovas** token, so every command unsets
it (`env -u CLOUDFLARE_API_TOKEN ...`) to use the trimm OAuth login.

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

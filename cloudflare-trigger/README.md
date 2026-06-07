# list-maker-cron — durable pipeline trigger (Cloudflare Worker)

On a daily cron, this Worker calls GitHub `workflow_dispatch` for
`.github/workflows/entities.yml`. It's the **durable** trigger: GitHub auto-disables
`schedule:` crons in public repos after 60 days of inactivity; a Cloudflare Worker
Cron doesn't have that limit.

## Deploy (Kevin — personal **trimm** Cloudflare)

The global `CLOUDFLARE_API_TOKEN` is the **Tecovas** token, so unset it for these
commands (so wrangler uses your trimm OAuth):

1. **Log into trimm** (once): `env -u CLOUDFLARE_API_TOKEN wrangler login` → pick trimm.
2. **Account id:** `env -u CLOUDFLARE_API_TOKEN wrangler whoami` → copy the Account ID
   into `wrangler.toml` (`account_id = "..."`).
3. **GitHub PAT:** create a fine-grained PAT for `khglynn/list-maker` with
   **Actions: Read and write**, then:
   `env -u CLOUDFLARE_API_TOKEN wrangler secret put GH_PAT` (paste the PAT).
4. **Deploy:** `env -u CLOUDFLARE_API_TOKEN wrangler deploy`
5. **Verify:** hit the Worker's URL once (it also dispatches on GET) and confirm a
   run appears under the repo's Actions tab.
6. **Then** delete the `schedule:` block from `.github/workflows/entities.yml` — the
   Worker is now the durable trigger (one commit).

## Manual trigger (optional)
The Worker also dispatches on an HTTP GET — but only if you set a `TRIGGER_TOKEN`
secret and pass it: `env -u CLOUDFLARE_API_TOKEN wrangler secret put TRIGGER_TOKEN`,
then `GET https://<worker-url>/?token=<TRIGGER_TOKEN>`. Without `TRIGGER_TOKEN` the
HTTP endpoint returns 403 (the cron is unaffected).

## Local test
`env -u CLOUDFLARE_API_TOKEN wrangler dev`, then trigger the scheduled event.

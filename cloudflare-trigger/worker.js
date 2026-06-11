// list-maker-cron — Cloudflare Worker: the DURABLE control plane for the pipeline.
//
// On a set of crons it calls GitHub's workflow_dispatch for the repo's workflows.
// HARD CONSTRAINT: Workers Free allows max 5 cron triggers per Worker — the 2026-06-11
// deploy with 7 crons failed exactly there. So music shares ONE Mon/Wed/Fri cron and
// the Worker picks the show by day. The 5:
//   - daily 11:00 UTC     → entities.yml  (AI Daily, Hard Fork, PCHH, Culture Gabfest)
//   - Mon/Wed/Fri 10:00   → pipeline.yml  (Mon → show_id=2 TAL; Wed+Fri → show_id=1 SOP)
//   - Mon   12:00 UTC     → eval.yml      (weekly extraction-quality eval — gated)
//   - Mon   13:00 UTC     → blogs.yml     (weekly blog pull queue: discover + ingest checked)
//   - 1st+15th 13:30      → pulse.yml     (biweekly Slack health heartbeat)
//
// Why this exists: GitHub silently disables `schedule:` crons in public repos after
// 60 days of repo inactivity. A Cloudflare Worker Cron has no such limit, so THIS
// Worker — not GitHub's own scheduler — is what "starts the work." Both workflows
// have their `schedule:` blocks removed; this Worker is their only trigger.
//
// Observability: this Worker is now the single point that starts everything, so a
// silent dispatch failure (expired PAT, GitHub outage) would stop the whole pipeline
// with no signal — the downstream Slack alerts only fire once a workflow actually
// RUNS, and the staleness check lives inside the workflow that wouldn't fire. So a
// failed dispatch posts to Slack here, at the trigger. (Set the optional
// SLACK_WEBHOOK_URL secret to enable; without it, failures still hit console.error.)
//
// Secrets:
//   GH_PAT            (required) fine-grained GitHub PAT for khglynn/list-maker,
//                     Actions: read & write. `wrangler secret put GH_PAT`.
//   SLACK_WEBHOOK_URL (optional) Slack incoming webhook for trigger-failure alerts.
//   TRIGGER_TOKEN     (optional) shared secret to enable the manual HTTP trigger.
// See README.md for deploy steps.

const REPO = "khglynn/list-maker";

// cron string (MUST match wrangler.toml [triggers].crons) → workflow file + inputs.
// Keeping the schedule here, in one place, is the point: the Worker is the single
// control plane. Changing cadence means editing this map + wrangler.toml together.
const SCHEDULE = {
  "0 11 * * *": { workflow: "entities.yml", inputs: {} },
  // One cron, two music shows (free-plan 5-cron cap): the fire day picks the show.
  // getUTCDay(): Mon=1 → TAL (show_id 2); Wed=3 / Fri=5 → SOP (show_id 1).
  "0 10 * * 1,3,5": {
    workflow: "pipeline.yml",
    inputsFor: (event) => ({
      show_id: new Date(event.scheduledTime).getUTCDay() === 1 ? "2" : "1",
    }),
  },
  "0 12 * * 1": { workflow: "eval.yml", inputs: {} },                   // Mon — weekly eval
  "0 13 * * 1": { workflow: "blogs.yml", inputs: {} },                  // Mon — blog pull queue
  "30 13 1,15 * *": { workflow: "pulse.yml", inputs: {} },              // 1st+15th — pulse
};

// Best-effort failure alert. Logs always; posts to Slack if the webhook is set. Never
// throws — a notify failure must not mask the original error in the cron path.
async function notifyFailure(env, message) {
  console.error(`list-maker-cron: ${message}`);
  if (!env.SLACK_WEBHOOK_URL) return;
  try {
    await fetch(env.SLACK_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text:
          ":rotating_light: *list-maker cron trigger FAILED* — the pipeline was " +
          `NOT started.\n${message}`,
      }),
    });
  } catch (e) {
    console.error(`list-maker-cron: Slack notify also failed — ${e.message}`);
  }
}

async function dispatch(env, workflow, inputs) {
  const body = { ref: "main" };
  if (inputs && Object.keys(inputs).length) body.inputs = inputs;
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_PAT}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "list-maker-cron",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    }
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`workflow_dispatch ${workflow} failed: ${resp.status} ${text}`);
  }
}

export default {
  // Fired by the crons in wrangler.toml. event.cron tells us which one fired.
  async scheduled(event, env, ctx) {
    if (!env.GH_PAT) {
      ctx.waitUntil(notifyFailure(env, "GH_PAT secret not set — cannot dispatch"));
      return;
    }
    const target = SCHEDULE[event.cron];
    if (!target) {
      ctx.waitUntil(
        notifyFailure(env, `no SCHEDULE mapping for cron "${event.cron}"`)
      );
      return;
    }
    const inputs = target.inputsFor ? target.inputsFor(event) : target.inputs;
    ctx.waitUntil(
      dispatch(env, target.workflow, inputs).catch((e) =>
        notifyFailure(env, `dispatch ${target.workflow} failed — ${e.message}`)
      )
    );
  },

  // Manual trigger (also used to VERIFY the deploy):
  //   GET <worker-url>/?token=<TRIGGER_TOKEN>[&workflow=entities.yml|pipeline.yml][&show_id=1]
  // Disabled (403) unless the TRIGGER_TOKEN secret is set and matches; the cron path
  // is unaffected. Defaults to entities.yml; pipeline.yml defaults to show_id=all.
  async fetch(request, env) {
    if (!env.GH_PAT) return new Response("GH_PAT not set\n", { status: 500 });
    const url = new URL(request.url);
    const token = url.searchParams.get("token");
    if (!env.TRIGGER_TOKEN || token !== env.TRIGGER_TOKEN) {
      return new Response("Forbidden\n", { status: 403 });
    }
    const workflow = url.searchParams.get("workflow") || "entities.yml";
    if (workflow !== "entities.yml" && workflow !== "pipeline.yml") {
      return new Response("Unknown workflow\n", { status: 400 });
    }
    const inputs = {};
    if (workflow === "pipeline.yml") {
      inputs.show_id = url.searchParams.get("show_id") || "all";
    }
    try {
      await dispatch(env, workflow, inputs);
      return new Response(`Dispatched ${workflow}\n`);
    } catch (e) {
      return new Response(`Error: ${e.message}\n`, { status: 502 });
    }
  },
};

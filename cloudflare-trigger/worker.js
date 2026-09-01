// list-maker-cron — Cloudflare Worker: the DURABLE control plane for the pipeline.
//
// ONE daily cron (20:30 UTC ≈ 3:30pm CT) fans out to the day's GitHub
// workflow_dispatch calls. Consolidated from five crons on 2026-08-26: the
// Workers Free plan caps cron triggers at 5 PER ACCOUNT, and other projects
// (remembrall-hub) need slots. Per-workflow cadence is unchanged in kind:
//   - entities.yml  every day            (AI Daily, Hard Fork, PCHH, Culture Gabfest)
//   - pipeline.yml  Mon/Wed/Fri          (Mon → show_id=2 TAL; Wed+Fri → show_id=1 SOP)
//   - eval.yml      Mon                  (weekly extraction-quality eval — gated)
//   - blogs.yml     Mon                  (weekly blog pull queue)
//   - pulse         1st + 15th           (biweekly Slack heartbeat — NOT its own
//                                         dispatch: entities.yml runs it after the
//                                         import when asked, via inputs.pulse)
// Only the minute moved — everything dispatches at the entities slot, 20:30 UTC,
// so the AI Daily brief's timing is exactly what it was. Day-of-week logic uses
// JS Date (Mon=1) here, NOT cron day fields — Cloudflare's 1=Sun..7=Sat cron
// convention already cost six weeks of missed Mondays once (2026-06/07).
// dispatchesFor is exported so worker.test.js can pin that logic in CI.
//
// Why this exists: GitHub silently disables `schedule:` crons in public repos after
// 60 days of repo inactivity. A Cloudflare Worker Cron has no such limit, so THIS
// Worker — not GitHub's own scheduler — is what "starts the work." The workflows
// have their `schedule:` blocks removed; this Worker is their only trigger.
//
// Observability: this Worker is the single point that starts everything, so a
// silent dispatch failure (expired PAT, GitHub outage) would stop the whole pipeline
// with no signal — the downstream Slack alerts only fire once a workflow actually
// RUNS. So a failed dispatch posts to Slack here, at the trigger, and each
// workflow's dispatch is isolated: one failure never blocks the rest of the day's
// fan-out. (Set the optional SLACK_WEBHOOK_URL secret to enable; without it,
// failures still hit console.error.)
//
// Secrets:
//   GH_PAT            (required) fine-grained GitHub PAT for khglynn/list-maker,
//                     Actions: read & write. `wrangler secret put GH_PAT`.
//   SLACK_WEBHOOK_URL (optional) Slack incoming webhook for trigger-failure alerts.
//   TRIGGER_TOKEN     (optional) shared secret to enable the manual HTTP trigger.
// See README.md for deploy steps.

const REPO = "khglynn/list-maker";

// The single cron string — MUST match wrangler.toml [triggers].crons.
const DAILY_CRON = "30 20 * * *";

// What the daily fire dispatches, decided by the fire timestamp. Exported shape
// kept simple on purpose: given a Date, return [{workflow, inputs}].
export function dispatchesFor(when) {
  const day = when.getUTCDay(); // JS convention: Sun=0, Mon=1 ... Sat=6
  const date = when.getUTCDate();
  const entities = { workflow: "entities.yml", inputs: {} }; // the AI Daily brief — every day
  if (date === 1 || date === 15) {
    // The pulse reads the tables entities.yml fills. Until 2026-09-01 it was a
    // separate dispatch at this same minute, so it usually queried Neon BEFORE the
    // day's import landed and reported shows "BEHIND" that were caught up five
    // minutes later. entities.yml now runs it as a follow-on job when asked.
    entities.inputs = { pulse: "true" };
  }
  const out = [entities];
  if (day === 1 || day === 3 || day === 5) {
    // Mon/Wed/Fri music: Monday is TAL (show_id=2), Wed+Fri are SOP (show_id=1).
    out.push({ workflow: "pipeline.yml", inputs: { show_id: day === 1 ? "2" : "1" } });
  }
  if (day === 1) {
    out.push({ workflow: "eval.yml", inputs: {} });
    out.push({ workflow: "blogs.yml", inputs: {} });
  }
  return out;
}

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
  // Fired by the single daily cron in wrangler.toml.
  async scheduled(event, env, ctx) {
    if (!env.GH_PAT) {
      ctx.waitUntil(notifyFailure(env, "GH_PAT secret not set — cannot dispatch"));
      return;
    }
    if (event.cron !== DAILY_CRON) {
      // A cron fired that this code doesn't know — config and code drifted.
      ctx.waitUntil(notifyFailure(env, `unexpected cron "${event.cron}" — wrangler.toml and worker.js are out of sync`));
      return;
    }
    const targets = dispatchesFor(new Date(event.scheduledTime));
    // Isolated per-workflow: one failed dispatch alerts but never blocks the rest.
    ctx.waitUntil(
      Promise.all(
        targets.map((t) =>
          dispatch(env, t.workflow, t.inputs).catch((e) =>
            notifyFailure(env, `dispatch ${t.workflow} failed — ${e.message}`)
          )
        )
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

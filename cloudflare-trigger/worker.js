// list-maker-cron — Cloudflare Worker that triggers the entity pipeline on a cron
// by calling GitHub's workflow_dispatch for .github/workflows/entities.yml.
//
// This is the DURABLE trigger: GitHub auto-disables `schedule:` crons in public
// repos after 60 days of inactivity, but a Cloudflare Worker Cron has no such limit.
//
// Secret (set with `wrangler secret put GH_PAT`): a fine-grained GitHub PAT scoped
// to khglynn/list-maker with Actions: read & write. See README.md for deploy steps.

const REPO = "khglynn/list-maker";
const WORKFLOW = "entities.yml";

async function dispatch(env) {
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_PAT}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "list-maker-cron",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`workflow_dispatch failed: ${resp.status} ${body}`);
  }
}

export default {
  // Fired by the cron in wrangler.toml.
  async scheduled(_event, env, ctx) {
    if (!env.GH_PAT) {
      console.error("list-maker-cron: GH_PAT secret not set — cannot dispatch");
      return;
    }
    ctx.waitUntil(
      dispatch(env).catch((e) =>
        console.error(`list-maker-cron: dispatch failed — ${e.message}`)
      )
    );
  },
  // Manual trigger: GET <worker-url>/?token=<TRIGGER_TOKEN> fires a dispatch now.
  // Disabled (403) unless the TRIGGER_TOKEN secret is set and matches — the cron
  // path is unaffected.
  async fetch(request, env) {
    if (!env.GH_PAT) return new Response("GH_PAT not set\n", { status: 500 });
    const token = new URL(request.url).searchParams.get("token");
    if (!env.TRIGGER_TOKEN || token !== env.TRIGGER_TOKEN) {
      return new Response("Forbidden\n", { status: 403 });
    }
    try {
      await dispatch(env);
      return new Response("Dispatched entities.yml\n");
    } catch (e) {
      return new Response(`Error: ${e.message}\n`, { status: 502 });
    }
  },
};

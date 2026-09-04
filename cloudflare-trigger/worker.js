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
// A SUCCESSFUL dispatch used to be recorded nowhere, so success and forgetting were
// the same code path — which is how 2026-08-06 (a run GitHub cancelled) and
// 2026-08-16 (no run at all) both passed in silence. Since 2026-09-03 there are
// three distinguishable states instead of one:
//   1. we could not start the work            → notifyFailure   (red)
//   2. the work started and did not succeed   → verifyMessage   (red, next fire)
//   3. we could not find out                  → unverifiedMessage (yellow)
// Each dispatch is written to the DISPATCH_LOG KV namespace; the NEXT day's fire
// reads yesterday's records back, asks GitHub what became of each run, and says so.
// The one failure this Worker can never report is its own absence — for that,
// GET /health exposes last_fire/last_verify and fleet-watchdog (a Worker in
// khglynn/self-hosted-mcps) polls it from outside.
//
// Secrets:
//   GH_PAT            (required) fine-grained GitHub PAT for khglynn/list-maker,
//                     Actions: read & write — the read half covers listing runs,
//                     so verification needs no second credential.
//   SLACK_WEBHOOK_URL (optional) Slack incoming webhook for trigger-failure alerts.
//   TRIGGER_TOKEN     (optional) shared secret to enable the manual HTTP trigger.
// Bindings:
//   DISPATCH_LOG      (KV) dispatch records + the two meta:* keys /health reads.
// See README.md for deploy steps.

const REPO = "khglynn/list-maker";

// The single cron string — MUST match wrangler.toml [triggers].crons.
const DAILY_CRON = "30 20 * * *";

// How old a dispatch record must be before its run can be judged. The longest job
// timeout in .github/workflows is 70 minutes (blogs.yml), so by 20h "whatever was
// going to happen has happened" — while staying under the 24h gap between fires, so
// every record is judged on the very next fire rather than waiting two.
const VERIFY_AFTER_MS = 20 * 60 * 60 * 1000;

// A dispatch record self-destructs after 3 days. If this Worker is broken for a
// while, KV cleans up behind it instead of leaving a pile of records to alert about
// ancient history on the day it recovers.
const DISPATCH_TTL_SECONDS = 3 * 24 * 3600;

// At most this many records judged per fire. A normal day leaves 4; this is double
// that. The cap exists because a Workers Free invocation gets 50 subrequests and
// 10 ms of CPU, SHARED with the dispatch fan-out that runs alongside it — so an
// unbounded verify loop (someone hammering the manual trigger, say) could starve
// the dispatching it exists to watch, which would be a spectacular own goal.
// Records over the cap are not lost: they keep until the next fire, bounded by the
// TTL above.
const VERIFY_MAX_PER_PASS = 8;

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

// ---------------------------------------------------------------------------
// Run verification — pure decisions, exported so worker.test.js can pin them.
// ---------------------------------------------------------------------------

// KV key for one dispatch. Keyed by the full ISO instant, not the date, so two
// dispatches of the same workflow on one day (the daily fire plus a manual
// ?token= trigger) never overwrite each other's record before it is checked.
// verifyPreviousDispatches scans on the "dispatch:" prefix, which is also what
// keeps the meta:* keys out of that scan — hence the format pin in the tests.
export function dispatchKey(workflow, dispatchedAtIso) {
  return `dispatch:${workflow}:${dispatchedAtIso}`;
}

// Which run belongs to a dispatch. workflow_dispatch answers 204 No Content — no
// run id, no Location header — so there is nothing to correlate ON except time,
// and GitHub's run object carries no record of the inputs a dispatch supplied
// (verified against the live API 2026-09-03: the run's `display_title` is the
// workflow's `name:`, never an input). Scoping the list call to ONE workflow file
// plus `concurrency: {group: github.workflow}` on all four workflows already makes
// at most one legitimate run in flight, so time is enough.
//
// EARLIEST-wins, deliberately. Not hypothetical: on 2026-09-02 entities.yml has TWO
// workflow_dispatch runs, 20:30:37 (the cron's, conclusion `failure`) and 21:35:37 —
// so a second run following the scheduled one is a thing that happens. Picking the
// latest would let whatever came after bury the failure the alarm exists to report.
// The tolerance absorbs clock skew only; observed lag from dispatch to created_at
// is under a second (2026-09-03: cron 20:30:00 → created_at 20:30:37, which is
// when the POST actually landed).
//
// KNOWN EDGE, accepted: two dispatches through this Worker inside the tolerance
// window — a manual ?token= trigger fired minutes BEFORE the cron — give the
// cron's record the manual run to read, because that run is the earliest one
// after (dispatchedAt - tolerance). Usually harmless: both dispatches ask for the
// same work, the second queues behind the first, and the verdict answers "did a
// run succeed", which is the question worth asking. The residue is that a failure
// of the LATER run could go unreported, so every alert carries the run's URL and
// meta:last_verify records run_url for every verdict — the reader can always see
// which run was judged. If that ever bites, the fix is to let a run be claimed by
// only one record per verify pass (records sort oldest-first on the key), not to
// shrink the tolerance.
export function correlateRun(runs, dispatchedAtIso, toleranceMs = 5 * 60 * 1000) {
  const floor = new Date(dispatchedAtIso).getTime() - toleranceMs;
  if (!Number.isFinite(floor)) return null;
  const after = (runs || [])
    .map((run) => ({ run, at: new Date(run.created_at).getTime() }))
    .filter((c) => Number.isFinite(c.at) && c.at >= floor)
    .sort((a, b) => a.at - b.at);
  return after.length ? after[0].run : null;
}

// One word for what became of a dispatch. Anything that is not "success" alerts,
// including a run still going 20+ hours later — a run that never finishes is its
// own anomaly, not a normal state to keep waiting out.
export function verdictFor(run) {
  if (!run) return "missing";
  if (run.status !== "completed") return `stuck-${run.status}`;
  return run.conclusion ?? "unknown";
}

const describeInputs = (inputs) => {
  const entries = Object.entries(inputs || {});
  return entries.length ? ` (${entries.map(([k, v]) => `${k}=${v}`).join(", ")})` : "";
};

// The wording is the point, so it is pure and tested. notifyFailure says "the
// pipeline was NOT started" — true for a dispatch that never left, and a lie for a
// run that started and was cancelled. Conflating the two is how an alert stops
// meaning anything.
export function verifyMessage(record, verdict, run) {
  const named = {
    missing: "never started",
    failure: "failed",
    cancelled: "was cancelled",
    timed_out: "timed out",
  };
  // stuck-in_progress reads like a log line; "is still in progress" reads like a
  // person telling you something. Everything else GitHub can conclude falls through
  // in its own words rather than being guessed at.
  const label =
    named[verdict] ??
    (verdict.startsWith("stuck-")
      ? `is STILL ${verdict.slice("stuck-".length).replace(/_/g, " ")}`
      : `ended as "${verdict}"`);
  const what = `${record.workflow}${describeInputs(record.inputs)}`;
  const tail = run
    ? `\n${run.html_url}`
    : "\nNo run appeared in GitHub Actions for that dispatch.";
  return (
    `:rotating_light: *list-maker: ${what} ${label}* — ` +
    `dispatched ${record.dispatchedAt}, more than 20 hours ago.${tail}`
  );
}

// A third state, and a quieter one: GitHub would not tell us. The run may well be
// fine — what failed is the checker. Saying that plainly keeps the red lines red.
export function unverifiedMessage(problems) {
  const n = problems.length;
  return (
    `:warning: *list-maker: ${n} dispatch${n === 1 ? "" : "es"} could not be checked* — ` +
    `this is the verifier failing, not the pipeline. Retrying on the next fire.\n` +
    problems.join("\n")
  );
}

// ---------------------------------------------------------------------------
// Side effects
// ---------------------------------------------------------------------------

// One POST body for every alert path, so a new alert cannot drift from the old one.
// Never throws: a Slack outage must not mask the thing it was trying to report.
async function postSlack(env, text) {
  console.error(`list-maker-cron: ${text}`);
  if (!env.SLACK_WEBHOOK_URL) return;
  try {
    await fetch(env.SLACK_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    console.error(`list-maker-cron: Slack notify also failed — ${e.message}`);
  }
}

// Best-effort failure alert for the DISPATCH half: the work never started.
async function notifyFailure(env, message) {
  await postSlack(
    env,
    ":rotating_light: *list-maker cron trigger FAILED* — the pipeline was " +
      `NOT started.\n${message}`
  );
}

// Every KV touch goes through here. Bookkeeping must never be able to fail the
// work it is bookkeeping about: if this Worker dispatched successfully and then
// could not write the record, a thrown error would surface as "dispatch failed",
// which is a lie — and a lying alert is worse than a missing one. A dead KV is
// caught one layer out instead: /health goes stale, and fleet-watchdog, which is
// the only thing that can see this Worker's absence, alerts on that.
async function withDispatchLog(env, label, fn) {
  if (!env.DISPATCH_LOG) {
    console.error(`list-maker-cron: DISPATCH_LOG binding missing — skipped ${label}`);
    return null;
  }
  try {
    return await fn(env.DISPATCH_LOG);
  } catch (e) {
    console.error(`list-maker-cron: KV ${label} failed — ${e.message}`);
    return null;
  }
}

// The `created` filter is what keeps this affordable, and it is load-bearing rather
// than an optimisation: a cron invocation on Workers Free gets **10 ms of CPU**, and
// the dispatch fan-out shares that budget with this check — so a check that parses
// too much JSON could kill the dispatching it exists to watch.
//
// Measured against the live API on 2026-09-03, because a run object embeds the whole
// repository, actor and head_commit and is ~12 KB:
//   unfiltered, per_page=30 → 395 KB, 0.70 ms to parse
//   unfiltered, per_page=10 → 122 KB, 0.23 ms
//   created=>=<the day before the dispatch> → 3 runs, ~12 KB, well under 0.1 ms
// The filter is honoured (85 runs → 3, verified). If GitHub ever stops honouring it,
// this degrades to "the newest per_page runs", which is where it started — so it is
// safe in both directions.
//
// Why the day BEFORE: the filter's granularity is a date, and correlateRun's window
// opens 5 minutes before the dispatch. A dispatch just after midnight UTC would
// otherwise have its own window fall outside the filter.
async function fetchRunsForWorkflow(env, workflow, dispatchedAtIso) {
  const since = new Date(new Date(dispatchedAtIso).getTime() - 24 * 3600 * 1000)
    .toISOString()
    .slice(0, 10);
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/runs` +
      `?event=workflow_dispatch&per_page=30&created=${encodeURIComponent(">=" + since)}`,
    {
      headers: {
        Authorization: `Bearer ${env.GH_PAT}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "list-maker-cron",
      },
    }
  );
  if (!resp.ok) throw new Error(`list runs for ${workflow} failed: ${resp.status}`);
  const body = await resp.json();
  return body.workflow_runs || [];
}

// Yesterday's dispatches, judged. Returns what it found so /health can show it.
async function verifyPreviousDispatches(env, now) {
  // The missing-PAT case already alerts on the dispatch side; a second alarm for
  // one root cause is how a channel learns to ignore both.
  if (!env.GH_PAT) return [];
  // No pagination: the key set is bounded by (dispatches per day × the 3-day TTL) —
  // about a dozen, well under KV's 1000-key page. Note what that bound rests on: the
  // cron contributes at most 4 a day, but the manual ?token= trigger is unbounded, so
  // someone hammering it would make this loop iterate (a KV read each) past the
  // VERIFY_MAX_PER_PASS cap below, which only limits how many are JUDGED. Left as is
  // — the records still expire on their own, and the failure is self-inflicted and
  // has never happened. If it ever does, page the listing instead of widening the cap.
  const listing = await withDispatchLog(env, "list dispatches", (kv) =>
    kv.list({ prefix: "dispatch:" })
  );
  if (!listing) return [];

  const results = [];
  const problems = [];
  let judged = 0;
  for (const key of listing.keys) {
    if (judged >= VERIFY_MAX_PER_PASS) {
      console.error(
        `list-maker-cron: verify cap reached (${VERIFY_MAX_PER_PASS}) — the rest keep until the next fire`
      );
      break;
    }
    const record = await withDispatchLog(env, `get ${key.name}`, (kv) =>
      kv.get(key.name, { type: "json" })
    );
    if (!record) continue;
    const age = now.getTime() - new Date(record.dispatchedAt).getTime();
    if (!Number.isFinite(age)) {
      // Unparseable timestamp: it can never be correlated, so it would sit here
      // being skipped until the TTL. Say so once and drop it.
      console.error(`list-maker-cron: dropping malformed dispatch record ${key.name}`);
      await withDispatchLog(env, `delete ${key.name}`, (kv) => kv.delete(key.name));
      continue;
    }
    if (age < VERIFY_AFTER_MS) continue; // too recent to judge — the next fire will

    judged += 1;
    try {
      const runs = await fetchRunsForWorkflow(env, record.workflow, record.dispatchedAt);
      const run = correlateRun(runs, record.dispatchedAt);
      const verdict = verdictFor(run);
      results.push({
        workflow: record.workflow,
        dispatched_at: record.dispatchedAt,
        verdict,
        run_url: run ? run.html_url : null,
      });
      if (verdict !== "success") await postSlack(env, verifyMessage(record, verdict, run));
      // Deleted whether it passed or alerted: one bad verdict, one Slack line.
      await withDispatchLog(env, `delete ${key.name}`, (kv) => kv.delete(key.name));
    } catch (e) {
      // Left in place on purpose — the TTL gives ~3 more fires to retry a transient
      // GitHub failure before the question goes unanswered for good.
      problems.push(`${record.workflow} (dispatched ${record.dispatchedAt}): ${e.message}`);
      results.push({
        workflow: record.workflow,
        dispatched_at: record.dispatchedAt,
        verdict: "unverified",
        run_url: null,
      });
    }
  }
  // One line per pass, not per record: a GitHub outage should cost one message.
  if (problems.length) await postSlack(env, unverifiedMessage(problems));
  return results;
}

// The verify pass as the cron calls it: it records what it saw, and it can never
// take the dispatch down with it.
async function runVerifyPass(env, now) {
  if (!env.GH_PAT) {
    // "Nothing to report" and "could not look" must not render identically. An
    // empty results array on /health reads as a clean bill of health, and without
    // a PAT no health was ever assessed — so say which it was. (No Slack line: the
    // dispatch side already alerts on the missing PAT, and one root cause should
    // cost one alarm.)
    await withDispatchLog(env, "put meta:last_verify", (kv) =>
      kv.put(
        "meta:last_verify",
        JSON.stringify({ at: now.toISOString(), skipped: "GH_PAT not set — nothing was checked" })
      )
    );
    return;
  }
  let results;
  try {
    results = await verifyPreviousDispatches(env, now);
  } catch (e) {
    await postSlack(
      env,
      ":warning: *list-maker: the run verifier crashed* — today's dispatches still " +
        `fired; yesterday's runs went unchecked.\n${e.message}`
    );
    return;
  }
  await withDispatchLog(env, "put meta:last_verify", (kv) =>
    kv.put("meta:last_verify", JSON.stringify({ at: now.toISOString(), results }))
  );
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
  // Recorded here rather than in scheduled() so the manual ?token= path — which is
  // the README's own deploy-verification step — gets checked too. Only successes
  // are recorded: a failed dispatch already alerted, and a record of it would
  // re-alert 24h later for the same root cause with nothing to correlate against.
  //
  // This one write does NOT go through withDispatchLog, and the difference matters.
  // Everywhere else, a swallowed KV error is caught a layer out — /health goes stale
  // and fleet-watchdog alarms. Not here: meta:last_fire is a separate put, so if only
  // THIS one fails, /health stays green, no receipt exists, and the next fire judges
  // nothing for a run that may well have been cancelled. That is the exact 2026-08-06
  // blind spot this Worker was built to close, reopened by its own bookkeeping.
  // So it gets its own catch and says so — in the yellow register, because the
  // dispatch itself succeeded. (Checking withDispatchLog's return cannot work:
  // kv.put resolves undefined on success and the helper returns null on failure, so
  // success and failure are both falsy.)
  const dispatchedAt = new Date().toISOString();
  if (!env.DISPATCH_LOG) {
    console.error(`list-maker-cron: DISPATCH_LOG binding missing — skipped record ${workflow}`);
    return;
  }
  try {
    await env.DISPATCH_LOG.put(
      dispatchKey(workflow, dispatchedAt),
      JSON.stringify({ workflow, dispatchedAt, inputs: inputs || {} }),
      { expirationTtl: DISPATCH_TTL_SECONDS }
    );
  } catch (e) {
    await postSlack(
      env,
      `:warning: *list-maker: ${workflow} was dispatched but its receipt was not written* — ` +
        `the run started; it will NOT be verified on the next fire.\n${e.message}`
    );
  }
}

// GET /health — what an outside watcher reads. Two timestamps, no secrets, and it
// can start no work, which is why it is safe to leave ungated (the same reasoning
// fleet-watchdog documents for its own ungated status path). Always 200: this is a
// data endpoint, and the staleness verdict belongs to the poller, which knows the
// expected cadence. A KV read that fails says so in kv_error rather than pretending
// the Worker never fired.
async function healthResponse(env) {
  const read = (key) => withDispatchLog(env, `get ${key}`, (kv) => kv.get(key, { type: "json" }));
  const body = {
    worker: "list-maker-cron",
    last_fire: await read("meta:last_fire"),
    last_verify: await read("meta:last_verify"),
  };
  if (!env.DISPATCH_LOG) body.kv_error = "DISPATCH_LOG binding missing";
  else if (body.last_fire === null && body.last_verify === null) {
    // Distinguishable from a broken read only by the console log; say the honest
    // thing either way — nothing has been recorded yet.
    body.note = "no fire recorded yet (expected until the next cron)";
  }
  return new Response(JSON.stringify(body, null, 2), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

export default {
  // Fired by the single daily cron in wrangler.toml.
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime);
    // FIRST, and before every guard below. last_fire is the one signal that has to
    // survive an expired PAT, a drifted cron string, a dead Slack and a GitHub
    // outage — it is how something outside this Worker tells "alive" from "gone",
    // and no code inside a Worker can report its own absence. Both of these run in
    // their own waitUntil so neither can block the dispatch that follows.
    ctx.waitUntil(
      withDispatchLog(env, "put meta:last_fire", (kv) =>
        kv.put("meta:last_fire", JSON.stringify({ at: now.toISOString(), cron: event.cron }))
      )
    );
    ctx.waitUntil(runVerifyPass(env, now));
    if (!env.GH_PAT) {
      ctx.waitUntil(notifyFailure(env, "GH_PAT secret not set — cannot dispatch"));
      return;
    }
    if (event.cron !== DAILY_CRON) {
      // A cron fired that this code doesn't know — config and code drifted.
      ctx.waitUntil(notifyFailure(env, `unexpected cron "${event.cron}" — wrangler.toml and worker.js are out of sync`));
      return;
    }
    const targets = dispatchesFor(now);
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

  // Two routes:
  //   GET <worker-url>/health   — ungated freshness for fleet-watchdog (see above)
  //   GET <worker-url>/?token=<TRIGGER_TOKEN>[&workflow=entities.yml|pipeline.yml][&show_id=1]
  //     manual trigger, also used to VERIFY the deploy. Disabled (403) unless the
  //     TRIGGER_TOKEN secret is set and matches; the cron path is unaffected.
  //     Defaults to entities.yml; pipeline.yml defaults to show_id=all.
  async fetch(request, env) {
    const url = new URL(request.url);
    // Before the GH_PAT check as well as the token gate: an unset PAT is exactly
    // the kind of outage /health exists to stay legible through.
    // Trailing slash tolerated: a poller or a human typing /health/ getting a bare
    // "Forbidden" from the token gate would be a confusing way to learn nothing.
    if (url.pathname.replace(/\/+$/, "") === "/health") return healthResponse(env);
    if (!env.GH_PAT) return new Response("GH_PAT not set\n", { status: 500 });
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

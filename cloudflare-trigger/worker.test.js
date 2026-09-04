// Pins the Worker's fan-out logic and its run verification. Runs in CI via
// `node --test cloudflare-trigger/worker.test.js` (test.yml) — no dependencies,
// plain node:test. Name the FILE, not the directory: `node --test <dir>` treats the
// argument as a module path on Node 25 and dies with MODULE_NOT_FOUND.
//
// Why this exists: the day logic once lived in five cron strings, where Cloudflare's
// 1=Sunday convention silently shifted every weekday run a day early for six weeks
// (2026-06/07). Now it lives here, in JS Date terms, where a test can hold it still.
import { test } from "node:test";
import assert from "node:assert/strict";

import worker, {
  dispatchesFor,
  correlateRun,
  verdictFor,
  dispatchKey,
  verifyMessage,
  unverifiedMessage,
} from "./worker.js";

const at = (iso) => new Date(iso); // all fires are 20:30 UTC; the date is what matters
const names = (d) => dispatchesFor(d).map((t) => t.workflow);
const entities = (d) => dispatchesFor(d).find((t) => t.workflow === "entities.yml");
const pipeline = (d) => dispatchesFor(d).find((t) => t.workflow === "pipeline.yml");

test("every day dispatches the entities run, and only that on a plain weekend day", () => {
  assert.deepEqual(names(at("2026-09-05T20:30:00Z")), ["entities.yml"]); // Saturday
  assert.deepEqual(names(at("2026-09-06T20:30:00Z")), ["entities.yml"]); // Sunday
});

test("Monday is TAL music + the weekly eval + the blog queue", () => {
  const mon = at("2026-08-31T20:30:00Z");
  assert.equal(mon.getUTCDay(), 1);
  assert.deepEqual(names(mon), ["entities.yml", "pipeline.yml", "eval.yml", "blogs.yml"]);
  assert.deepEqual(pipeline(mon).inputs, { show_id: "2" });
});

test("Wednesday and Friday are SOP music", () => {
  for (const iso of ["2026-09-02T20:30:00Z", "2026-09-04T20:30:00Z"]) {
    const d = at(iso);
    assert.deepEqual(names(d), ["entities.yml", "pipeline.yml"], iso);
    assert.deepEqual(pipeline(d).inputs, { show_id: "1" }, iso);
  }
});

test("the 1st and 15th ask entities to run the pulse AFTER the import — never a separate dispatch", () => {
  for (const iso of ["2026-09-01T20:30:00Z", "2026-09-15T20:30:00Z"]) {
    const d = at(iso);
    assert.deepEqual(entities(d).inputs, { pulse: "true" }, iso);
    assert.ok(!names(d).includes("pulse.yml"), `${iso} must not dispatch pulse.yml on its own`);
  }
});

test("other days do not ask for a pulse", () => {
  assert.deepEqual(entities(at("2026-09-02T20:30:00Z")).inputs, {});
  assert.deepEqual(entities(at("2026-09-14T20:30:00Z")).inputs, {});
});

test("a Monday that is also the 1st gets everything at once", () => {
  const d = at("2026-06-01T20:30:00Z");
  assert.equal(d.getUTCDay(), 1);
  assert.deepEqual(names(d), ["entities.yml", "pipeline.yml", "eval.yml", "blogs.yml"]);
  assert.deepEqual(entities(d).inputs, { pulse: "true" });
});

// ---------------------------------------------------------------------------
// Run verification (2026-09-03). The pure decisions are pinned directly; the
// side-effecting paths get a fake fetch and a fake KV, both a few lines of plain
// JS, because the thing worth proving is behavioural: a run that did not succeed
// says so, and nothing about the checking can take the DISPATCHING down with it.
// ---------------------------------------------------------------------------

const run = (created_at, extra = {}) => ({
  id: 1,
  created_at,
  status: "completed",
  conclusion: "success",
  html_url: "https://github.com/khglynn/list-maker/actions/runs/1",
  ...extra,
});

test("correlateRun picks the run that started just after the dispatch", () => {
  const r = run("2026-09-03T20:30:37Z");
  assert.equal(correlateRun([r], "2026-09-03T20:30:35Z"), r);
});

test("correlateRun ignores a run from before the dispatch", () => {
  // Yesterday's run is still inside GitHub's most-recent-10 window. Counting it
  // would report a missed day as healthy.
  assert.equal(correlateRun([run("2026-09-02T20:30:37Z")], "2026-09-03T20:30:35Z"), null);
});

test("correlateRun returns null when GitHub lists no runs at all", () => {
  assert.equal(correlateRun([], "2026-09-03T20:30:35Z"), null);
});

test("correlateRun prefers the scheduled run over a later manual re-run", () => {
  // Shaped on the real 2026-09-02, where entities.yml has two workflow_dispatch
  // runs: 20:30:37 (the cron's, failed) and 21:35:37. Latest-wins would let the
  // second bury the first.
  const scheduled = run("2026-09-02T20:30:37Z", { id: 10, conclusion: "failure" });
  const manual = run("2026-09-02T21:35:37Z", { id: 11 });
  assert.equal(correlateRun([manual, scheduled], "2026-09-02T20:30:35Z").id, 10);
});

test("correlateRun's tolerance boundary is inclusive", () => {
  const dispatchedAt = "2026-09-03T20:30:00Z";
  const exactly = run("2026-09-03T20:25:00Z"); // dispatchedAt - 5 min
  assert.equal(correlateRun([exactly], dispatchedAt), exactly);
  assert.equal(correlateRun([run("2026-09-03T20:24:59Z")], dispatchedAt), null);
});

test("verdictFor names every state the alarm cares about", () => {
  assert.equal(verdictFor(null), "missing");
  assert.equal(verdictFor({ status: "completed", conclusion: "success" }), "success");
  assert.equal(verdictFor({ status: "completed", conclusion: "failure" }), "failure");
  assert.equal(verdictFor({ status: "completed", conclusion: "cancelled" }), "cancelled");
  assert.equal(verdictFor({ status: "in_progress" }), "stuck-in_progress");
  assert.equal(verdictFor({ status: "queued" }), "stuck-queued");
});

test("dispatchKey keeps the prefix verifyPreviousDispatches scans on", () => {
  assert.equal(
    dispatchKey("pipeline.yml", "2026-09-03T20:30:01.234Z"),
    "dispatch:pipeline.yml:2026-09-03T20:30:01.234Z"
  );
});

test("verifyMessage tells a run that never started apart from one that was cancelled", () => {
  const record = { workflow: "entities.yml", dispatchedAt: "2026-09-02T20:30:35Z", inputs: {} };
  const missing = verifyMessage(record, "missing", null);
  assert.match(missing, /never started/);
  assert.match(missing, /No run appeared/);

  const cancelled = verifyMessage(record, "cancelled", run("2026-09-02T20:30:37Z"));
  assert.match(cancelled, /was cancelled/);
  assert.ok(!cancelled.includes("never started"), "a cancelled run did start");
  assert.match(cancelled, /actions\/runs\/1/); // the link is what makes it actionable
});

test("verifyMessage says a stuck run in words, not in GitHub's status string", () => {
  const record = { workflow: "blogs.yml", dispatchedAt: "2026-09-02T20:30:35Z", inputs: {} };
  const stuck = verifyMessage(record, "stuck-in_progress", run("2026-09-02T20:30:37Z"));
  assert.match(stuck, /is STILL in progress/);
  assert.ok(!stuck.includes("stuck-"), "the raw verdict should not reach Slack");
  // an unmapped GitHub conclusion still says something readable
  assert.match(verifyMessage(record, "action_required", null), /ended as "action_required"/);
});

test("verifyMessage names the inputs, so the Slack line says WHICH show", () => {
  const record = {
    workflow: "pipeline.yml",
    dispatchedAt: "2026-09-02T20:30:35Z",
    inputs: { show_id: "2" },
  };
  assert.match(verifyMessage(record, "failure", null), /pipeline\.yml \(show_id=2\)/);
});

test("unverifiedMessage blames the checker, not the pipeline", () => {
  const text = unverifiedMessage(["entities.yml (dispatched X): 503"]);
  assert.match(text, /:warning:/);
  assert.match(text, /verifier failing, not the pipeline/);
  assert.match(text, /1 dispatch could not/); // not "1 dispatch(es)"
  assert.match(unverifiedMessage(["a", "b"]), /2 dispatches could not/);
});

// --- fakes -----------------------------------------------------------------

function fakeKv(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    store,
    failOn: null, // "get" | "put" | "list" — simulates a broken namespace
    async get(name, opts) {
      if (this.failOn === "get") throw new Error("KV get exploded");
      const raw = store.get(name);
      if (raw === undefined) return null;
      return opts && opts.type === "json" ? JSON.parse(raw) : raw;
    },
    async put(name, value) {
      if (this.failOn === "put") throw new Error("KV put exploded");
      store.set(name, value);
    },
    async delete(name) {
      store.delete(name);
    },
    async list({ prefix } = {}) {
      if (this.failOn === "list") throw new Error("KV list exploded");
      const keys = [...store.keys()]
        .filter((k) => !prefix || k.startsWith(prefix))
        .map((name) => ({ name }));
      return { keys, list_complete: true };
    },
  };
}

const jsonResponse = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// Routes by URL substring. Anything unrouted throws, so a test can never pass by
// silently reaching a network it thinks it stubbed.
function fakeFetch(routes) {
  const calls = [];
  const impl = async (url, init = {}) => {
    const href = String(url);
    calls.push({ url: href, method: init.method || "GET", body: init.body });
    for (const [pattern, handler] of routes) {
      if (href.includes(pattern)) return handler(href, init);
    }
    throw new Error(`unrouted fetch: ${href}`);
  };
  impl.calls = calls;
  return impl;
}

async function withFetch(impl, fn) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  try {
    return await fn();
  } finally {
    globalThis.fetch = original;
  }
}

// scheduled() hands its work to waitUntil; a test has to drain that to see it.
async function runScheduled(env, { cron = "30 20 * * *", when = "2026-09-03T20:30:00Z" } = {}) {
  const pending = [];
  await worker.scheduled({ cron, scheduledTime: new Date(when).getTime() }, env, {
    waitUntil: (p) => pending.push(p),
  });
  await Promise.all(pending);
}

const SLACK = "https://hooks.slack.test/webhook";
const slackTexts = (impl) =>
  impl.calls.filter((c) => c.url === SLACK).map((c) => JSON.parse(c.body).text);
const dispatchedTo = (impl) =>
  impl.calls.filter((c) => c.url.includes("/dispatches")).map((c) => c.url.split("/workflows/")[1]);

const okRoutes = (runs = []) => [
  ["/dispatches", () => new Response(null, { status: 204 })],
  ["/runs?", () => jsonResponse({ workflow_runs: runs })],
  [SLACK, () => new Response("ok")],
];

// --- /health ---------------------------------------------------------------

test("/health answers before the token gate and before the PAT check", async () => {
  const kv = fakeKv({
    "meta:last_fire": JSON.stringify({ at: "2026-09-03T20:30:00Z", cron: "30 20 * * *" }),
    "meta:last_verify": JSON.stringify({ at: "2026-09-03T20:30:00Z", results: [] }),
  });
  // No GH_PAT, no TRIGGER_TOKEN, no token in the URL: the two gates that stop
  // every other path. The outside watcher must still get an answer.
  const res = await worker.fetch(new Request("https://w.example/health"), { DISPATCH_LOG: kv });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.worker, "list-maker-cron");
  assert.equal(body.last_fire.at, "2026-09-03T20:30:00Z");
  assert.deepEqual(body.last_verify.results, []);
});

test("/health says the binding is missing rather than implying the Worker never fired", async () => {
  const res = await worker.fetch(new Request("https://w.example/health"), {});
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.last_fire, null);
  assert.match(body.kv_error, /DISPATCH_LOG/);
});

test("/health tolerates a trailing slash", async () => {
  const res = await worker.fetch(new Request("https://w.example/health/"), {});
  assert.equal(res.status, 200);
  assert.equal((await res.json()).worker, "list-maker-cron");
});

test("the manual trigger is still gated — /health changed nothing else", async () => {
  const forbidden = await worker.fetch(new Request("https://w.example/?token=nope"), {
    GH_PAT: "x",
    TRIGGER_TOKEN: "secret",
  });
  assert.equal(forbidden.status, 403);
  const noPat = await worker.fetch(new Request("https://w.example/"), {});
  assert.equal(noPat.status, 500);
});

test("the manual trigger records itself, so the deploy-verification dispatch is checked too", async () => {
  const kv = fakeKv();
  const impl = fakeFetch(okRoutes());
  const res = await withFetch(impl, () =>
    worker.fetch(new Request("https://w.example/?token=secret&workflow=pipeline.yml&show_id=1"), {
      GH_PAT: "pat",
      TRIGGER_TOKEN: "secret",
      DISPATCH_LOG: kv,
    })
  );
  assert.equal(res.status, 200);
  const [key] = [...kv.store.keys()];
  assert.match(key, /^dispatch:pipeline\.yml:/);
  assert.deepEqual(JSON.parse(kv.store.get(key)).inputs, { show_id: "1" });
});

// --- recording -------------------------------------------------------------

test("every dispatch is recorded under its own key, with its inputs", async () => {
  const kv = fakeKv();
  const impl = fakeFetch(okRoutes());
  // A Monday: entities + pipeline(TAL) + eval + blogs — four dispatches, four records.
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", DISPATCH_LOG: kv }, { when: "2026-08-31T20:30:00Z" })
  );
  const records = [...kv.store.keys()].filter((k) => k.startsWith("dispatch:"));
  assert.equal(records.length, 4);
  assert.equal(dispatchedTo(impl).length, 4);
  const pipelineKey = records.find((k) => k.includes("pipeline.yml"));
  assert.deepEqual(JSON.parse(kv.store.get(pipelineKey)).inputs, { show_id: "2" });
});

test("a KV write failure never turns a successful dispatch into a failure alert", async () => {
  const kv = fakeKv();
  kv.failOn = "put";
  const impl = fakeFetch(okRoutes());
  await withFetch(impl, () =>
    runScheduled(
      { GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv },
      { when: "2026-09-05T20:30:00Z" }
    )
  );
  assert.deepEqual(dispatchedTo(impl), ["entities.yml/dispatches"]);
  assert.deepEqual(slackTexts(impl), [], "the work started; nothing should claim otherwise");
});

test("last_fire is written before the PAT guard, so a dead PAT still leaves a heartbeat", async () => {
  const kv = fakeKv();
  const impl = fakeFetch([[SLACK, () => new Response("ok")]]);
  await withFetch(impl, () => runScheduled({ SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv }));
  assert.equal(JSON.parse(kv.store.get("meta:last_fire")).at, "2026-09-03T20:30:00.000Z");
  assert.match(slackTexts(impl)[0], /GH_PAT secret not set/);
  assert.equal(slackTexts(impl).length, 1, "one root cause, one alarm");
  // and /health must not read as "checked, all fine" for a check that never ran
  assert.match(JSON.parse(kv.store.get("meta:last_verify")).skipped, /nothing was checked/);
});

// --- verifying -------------------------------------------------------------

const YESTERDAY = "2026-09-02T20:30:35Z"; // 24h before the default fire above
const yesterdayRecord = (workflow = "entities.yml", inputs = {}) => ({
  [dispatchKey(workflow, YESTERDAY)]: JSON.stringify({
    workflow,
    dispatchedAt: YESTERDAY,
    inputs,
  }),
});

test("yesterday's cancelled run produces a Slack line and the record is dropped", async () => {
  const kv = fakeKv(yesterdayRecord());
  const impl = fakeFetch(
    okRoutes([run("2026-09-02T20:30:37Z", { status: "completed", conclusion: "cancelled" })])
  );
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  const texts = slackTexts(impl);
  assert.equal(texts.length, 1);
  assert.match(texts[0], /entities\.yml was cancelled/);
  assert.equal(kv.store.has(dispatchKey("entities.yml", YESTERDAY)), false, "judged once, then gone");
  // and the verdict is on /health, for the outside watcher to see
  assert.equal(JSON.parse(kv.store.get("meta:last_verify")).results[0].verdict, "cancelled");
});

test("a run that succeeded is silent, and its record is cleaned up", async () => {
  const kv = fakeKv(yesterdayRecord());
  const impl = fakeFetch(okRoutes([run("2026-09-02T20:30:37Z")]));
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  assert.deepEqual(slackTexts(impl), []);
  assert.equal(kv.store.has(dispatchKey("entities.yml", YESTERDAY)), false);
});

test("a run that never appeared is reported as never started", async () => {
  const kv = fakeKv(yesterdayRecord("pipeline.yml", { show_id: "1" }));
  const impl = fakeFetch(okRoutes([])); // GitHub lists nothing for that workflow
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  assert.match(slackTexts(impl)[0], /pipeline\.yml \(show_id=1\) never started/);
});

test("a record younger than the verify window is left alone, not judged early", async () => {
  const key = dispatchKey("entities.yml", "2026-09-03T08:00:00Z"); // 12.5h before the fire
  const kv = fakeKv({
    [key]: JSON.stringify({
      workflow: "entities.yml",
      dispatchedAt: "2026-09-03T08:00:00Z",
      inputs: {},
    }),
  });
  const impl = fakeFetch(okRoutes([]));
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  assert.deepEqual(slackTexts(impl), [], "a run still in flight is not a missing run");
  assert.ok(kv.store.has(key), "the record waits for the next fire");
  assert.ok(!impl.calls.some((c) => c.url.includes("/runs?")), "GitHub was not even asked");
});

test("a GitHub failure keeps the record and says the CHECK failed, not the pipeline", async () => {
  const kv = fakeKv(yesterdayRecord());
  const impl = fakeFetch([
    ["/dispatches", () => new Response(null, { status: 204 })],
    ["/runs?", () => jsonResponse({ message: "Bad gateway" }, 502)],
    [SLACK, () => new Response("ok")],
  ]);
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  const texts = slackTexts(impl);
  assert.equal(texts.length, 1);
  assert.match(texts[0], /could not be checked/);
  assert.ok(
    kv.store.has(dispatchKey("entities.yml", YESTERDAY)),
    "left for the next fire — the TTL bounds the retries"
  );
  assert.equal(JSON.parse(kv.store.get("meta:last_verify")).results[0].verdict, "unverified");
});

test("the runs lookup asks GitHub for the window, not for everything", async () => {
  // Not a micro-optimisation: a cron invocation on Workers Free has 10ms of CPU,
  // shared with the dispatch fan-out. Unfiltered, per_page=30 is 395KB of JSON to
  // parse (measured against the live API); filtered it is a handful of runs.
  const kv = fakeKv(yesterdayRecord());
  const impl = fakeFetch(okRoutes([run("2026-09-02T20:30:37Z")]));
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  const lookup = impl.calls.find((c) => c.url.includes("/runs?"));
  // the day BEFORE the dispatch, so a just-after-midnight dispatch keeps its window
  assert.match(lookup.url, /created=%3E%3D2026-09-01/);
  assert.match(lookup.url, /event=workflow_dispatch/);
});

test("a pile-up of records is judged a batch at a time, never all at once", async () => {
  // 12 stale receipts (someone leaning on the manual trigger). Judging all of them
  // would spend 12 GitHub calls plus 12 Slack posts against a 50-subrequest ceiling
  // the day's dispatches also draw on.
  const seeded = {};
  for (let i = 0; i < 12; i++) {
    const iso = `2026-09-02T${String(i).padStart(2, "0")}:00:00Z`;
    seeded[dispatchKey("entities.yml", iso)] = JSON.stringify({
      workflow: "entities.yml",
      dispatchedAt: iso,
      inputs: {},
    });
  }
  const kv = fakeKv(seeded);
  const impl = fakeFetch(okRoutes([run("2026-09-02T09:30:00Z")]));
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv })
  );
  assert.equal(impl.calls.filter((c) => c.url.includes("/runs?")).length, 8);
  const left = [...kv.store.keys()].filter((k) => k.startsWith("dispatch:entities.yml:2026-09-02"));
  assert.equal(left.length, 4, "the rest keep until the next fire rather than being dropped");
});

// --- the invariant: checking never breaks starting ---------------------------

test("a verify pass that crashes outright still lets the day's dispatches fire", async () => {
  const kv = fakeKv();
  kv.list = async () => ({ keys: null }); // KV answers something unusable
  const impl = fakeFetch(okRoutes());
  await withFetch(impl, () =>
    runScheduled(
      { GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK, DISPATCH_LOG: kv },
      { when: "2026-09-05T20:30:00Z" }
    )
  );
  assert.deepEqual(dispatchedTo(impl), ["entities.yml/dispatches"]);
  assert.match(slackTexts(impl)[0], /run verifier crashed/);
});

test("no DISPATCH_LOG binding at all does not stop the day's dispatches", async () => {
  const impl = fakeFetch(okRoutes());
  await withFetch(impl, () =>
    runScheduled({ GH_PAT: "pat", SLACK_WEBHOOK_URL: SLACK }, { when: "2026-09-05T20:30:00Z" })
  );
  assert.deepEqual(dispatchedTo(impl), ["entities.yml/dispatches"]);
  assert.deepEqual(slackTexts(impl), []);
});

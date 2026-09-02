// Pins the Worker's fan-out logic. Runs in CI via `node --test cloudflare-trigger/`
// (test.yml) — no dependencies, plain node:test.
//
// Why this exists: the day logic once lived in five cron strings, where Cloudflare's
// 1=Sunday convention silently shifted every weekday run a day early for six weeks
// (2026-06/07). Now it lives here, in JS Date terms, where a test can hold it still.
import { test } from "node:test";
import assert from "node:assert/strict";

import { dispatchesFor } from "./worker.js";

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

# Engineering principles for this repo

**Distilled 2026-06-10** from four research guides in Kevin's Obsidian vault (`0.2 Clips + Social + AI/Agentic Research/Cowork Agentic Research/runs/2026/`): *codebase legibility & maintenance* (2026-06-03), *running things off my laptop* (2026-05-22), *database history & design / memory systems* (2026-04-23), *dependency security hygiene* (2026-06-03). Those are the canonical, full-reasoning versions; this file is the repo-relevant distillation (the repo is public, the guides are personal). If a principle here seems wrong for a case, the full guide usually names the escape hatch — check it before overriding.

## Legibility (for agents and future readers)

- Comment the WHY, not the HOW — the reason for a weird retry interval survives refactors; a comment that mirrors the code goes stale the moment the code changes.
- Stale documentation is worse than none: agents treat it as ground truth and act on it. If a doc can't be kept current, delete it. Inert assertions should either be executable (a test, a drift guard) or carry a freshness date.
- Delete dead code, don't disclaim it. Commented-out defective code measurably pushes agents toward defective generation (~58% in the studied case); "ignore this" disclaimers recover at most 22%.
- CLAUDE.md earns its keep only with non-inferable facts (env gotchas, schema decisions, custom commands). Architecture prose an agent can grep doesn't improve success and costs context. Keep it trimmed.
- Any value shown downstream (Notion, pulse, playlist) must trace to its source in one query. Prefer NULL for "no data" over a plausible default — never COALESCE to a fake number; missing data should fail visibly.
- Tests are the precondition for safe agent-assisted change, not QA hygiene. An untested module is a haunted graveyard — add tests before touching old pipeline code.

*Bites hardest in:* `pipeline/` scripts and scrapers, Neon schema defaults, Notion sync logic, this repo's CLAUDE.md.

## Automation that stays alive (control / runtime / data planes)

- Name the three planes separately: control (what fires work — here, the Cloudflare Worker cron), runtime (where it runs — GitHub Actions), data (where state lives — Neon). Mixing them up is why automations break silently.
- GitHub Actions `schedule:` is a trigger, not a workflow engine: no smart retries, no event waits, and on public repos it silently disables after 60 idle days. Never the durable substrate for work that matters.
- A script that runs is not an operation. An operation has visible failure states, can distinguish "nothing to do" from "didn't check," and leaves evidence (run ID, status, one place to inspect).
- Five contracts every automation needs: secrets (managed store, not scattered .env), idempotency (safe re-runs — this repo's `delete_existing_run` + `notion_page_id` gates), retries (bounded, with backoff), dead-letter (failed work lands somewhere inspectable, e.g. the failure-issue + Slack alerts), observability (logged runs with IDs).
- Pin model versions in code — an unpinned extraction call silently changes behavior when the provider swaps the model underneath (this is why `gpt-4.1-mini` is pinned and the eval harness gates changes).
- Cap agent loops; context quality degrades well before window limits. Prefer short runs + durable state over long threads.

*Bites hardest in:* `cloudflare-trigger/`, `.github/workflows/`, `run_new_episodes.py`, LLM extraction.

## Data with provenance (the memory-systems shape)

- Provenance is a first-class column, not an afterthought: a durable fact should answer "where from, when, what replaced it" without a join (`source`, `ingested_at`, `superseded_by`, `confidence`). Retrofitting costs orders of magnitude more than building it in.
- Unitemporal-with-provenance is the right shape here; full bitemporal modeling is for "what did you know on date X?" regulators we don't have.
- Scripts handle schema-stable inputs; the LLM earns its keep only at irreducible ambiguity. Taddy import, Spotify matching, Notion sync stay deterministic; extraction from transcripts is where the model belongs.
- Append-only audit trails where silent corruption is catastrophic — one history table you can reconstruct from, not event-sourcing ceremony.
- Single-writer discipline is a correctness boundary: if two processes can update the same entity you can't reason about which produced a bad value.
- Vendor benchmarks are marketing. Validate any model/threshold change against ~50 known-good examples from OUR data (the `evals/` harness exists for exactly this). Use deterministic graders; LLM-as-judge has structural biases (position, transitivity) for evaluating your own outputs.
- The right level of sophistication is what you can debug on a bad Tuesday.

*Bites hardest in:* `ai_entities`/`ai_mentions` schema, match-confidence tracking, `data_health.py`, `evals/`.

## Dependency hygiene

- A dependency can become dangerous overnight without your code changing — that's the system working, not a mistake you made.
- Minimum viable gate: a production-scoped audit in CI (`pip-audit -r pipeline/requirements.txt` here). Scope matters — a gate that screams about dev-only tooling trains you to ignore it. *(Adoption pending: tracked for the weekly workflow once the Worker drives it.)*
- CVSS measures worst-case severity across all deployments, not your exposure. HIGH = patch when you reasonably can; CISA KEV (confirmed exploited) = patch now.
- Most fixes are a one-package bump within the same major: lockfile-only diff. Verify with the audit AND the test suite before pushing.
- Frozen/pinned installs in CI block the freshly-published-malicious-version supply-chain class; let new releases age ~7–14 days before auto-adopting.
- Never blind-merge a grouped or major-version bump without the changelog — version coupling hides there, invisible in the diff.

*Bites hardest in:* `pipeline/requirements.txt`, workflow action versions, Dependabot PRs.

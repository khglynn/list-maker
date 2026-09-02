# Extraction eval harness

*Created 2026-06-07. The honest gradient for the one LLM step in this pipeline — entity
extraction (tech shows: AI Daily + Hard Fork). Run it before and after any model or
prompt change so quality changes stop being a matter of vibes.*

## Why this exists

Two research primers drive it (read them for the full reasoning):

- **"How AI Systems Remember"** — *"evals are the single highest-leverage investment; the
  first thing you do before any architecture change is build the eval harness."* And:
  trust **deterministic metrics**, not an LLM judge (LLM judges have transitivity
  violations + position bias). Reserve an LLM judge for narrow checks only.
- **"Running Things Off Your Laptop"** — every durable system must answer *"how will you
  know the output stayed good?"* with one named thing. This harness is that named thing.

The pipeline is script-first with a single LLM call at the irreducibly-ambiguous step
(transcript → entities). A model bump underneath that call could silently change
extraction quality. This catches it.

## The honest finding that shaped the design

When first built, the eval re-extracted the baseline episodes with the **same model at
temperature 0** and compared. The result was a surprise worth remembering:

- **~40% of the entity set churns run-to-run even with identical model/prompt/temp.**
  (macro Jaccard ≈ 0.60 over 30 episodes.)
- **Confidence does not predict stability** — dropped and retained entities had nearly
  identical mean confidence (0.878 vs 0.889). You cannot "just look at the high-confidence
  ones" to get stability; the high-confidence core only reproduces ~77%.

So **per-episode set identity is too noisy to gate on** — a gate built on it would fail
every run and get ignored. The trustworthy signals are the **stable aggregate** ones.
That is the whole design:

| Signal | Role | Why |
|---|---|---|
| Confidence in [0,1] | **GATE** | A contract a model change could break; deterministic. |
| No failed extractions | **GATE** | Couldn't certify a run that didn't complete. |
| Entity **yield ratio** (count now / baseline) | **GATE** | Aggregate counts are stable; a model that extracts half as many is a real regression. Same-model ≈ 0.92–1.05. |
| **Type-distribution** shift (max per-type proportion move) | **GATE** | The entity *mix* is stable; a model that stops producing `model` types is a real signal. |
| **Gold** recall + type-accuracy | **GATE** | Correctness vs hand-verified truth, averaged over a set. |
| Jaccard, core_recall, dropped/added | diagnostic | Reported for insight; **not gated** (inherent churn). |
| Gold precision / F1 | diagnostic | Gold is *must-include*, not exhaustive, so precision is meaningless. |

Floors live in `run_eval.py::FLOORS`, calibrated to measured same-model noise. Loosening
one is a real decision — note why in the commit.

## Run it

```bash
# Full run (both modes), current model — ~30 OpenAI calls, a few minutes:
./pipeline/venv/bin/python evals/extraction/run_eval.py

# Quick read while iterating on a prompt:
./pipeline/venv/bin/python evals/extraction/run_eval.py --limit 8

# Test a candidate model (the actual point — before/after a swap):
./pipeline/venv/bin/python evals/extraction/run_eval.py --model gpt-5-mini --json after.json

# CI gate: exit non-zero + Slack on a floor breach:
./pipeline/venv/bin/python evals/extraction/run_eval.py --ci
```

Needs `DATABASE_URL` + `OPENAI_API_KEY` (from `.env.local`, or CI secrets). Transcripts
are pulled fresh from Neon by episode_id and hash-checked against the frozen fixture, so
an episode whose transcript changed upstream is flagged (`input drifted`) rather than
silently comparing apples to oranges.

## Interpreting a model swap

1. `run_eval.py --json before.json` on the current model (or just trust the committed
   same-model numbers below).
2. `run_eval.py --model <candidate> --json after.json`.
3. Compare. The candidate is **safe** if the gates hold: yield stays in band, the type
   distribution doesn't lurch, gold recall/type-accuracy hold, confidence stays in range.
   Don't over-read a Jaccard/core_recall move — that's mostly noise.

## Fixtures

- **`fixtures/golden_baseline.json`** — 30 tech episodes (15 AI Daily + 15 Hard Fork)
  with the current known-good extraction frozen, plus a per-episode hash of the exact text
  fed to extraction. Regenerate **only on an intentional re-baseline** (after deliberately
  improving the prompt and confirming the new output is good):
  `./pipeline/venv/bin/python evals/extraction/build_baseline.py --per-show 15`
- **`fixtures/gold_verified.json`** — hand-verified **must-include** core entities,
  annotated by reading each transcript. Biased to unambiguous models/products that survive
  `focus_core_types`. Seed set (3 eps / 6 entities); same-model recall ≈ 0.83, type-acc
  1.0. **Expand it** by reading more transcripts and adding entities you're confident any
  correct extraction must catch — recall is the meaningful metric, so keep the bar fair.

## Faithfulness

The runner re-extracts through the **exact production path** —
`openai_extract → process_episode_mentions` (the single shared sanitize→postprocess→classify→filter
function that `main()` also uses) → `collapse_to_entities` (production's `normalize_name`).
So "did the run find entity X" means exactly what it means downstream; the eval can't
drift from production. That includes sponsor detection: the runner passes the episode's
parsed roster and its truncated transcript, the same two inputs the orchestrator passes.

**The frozen baseline predates ads-as-data (2026-09-02).** Extraction used to DROP
sponsor mentions and now keeps them tagged, so entity yield against a baseline built
before that change reads high by however many ads each episode carried. The
`yield_ratio_max` band should absorb it, but re-freeze the baseline
(`build_baseline.py`) once the sponsor retag has run, so the band is measuring model
drift again rather than a known one-time step.

## Same-model reference numbers (gpt-4.1-mini, 2026-06-07)

Yield ratio ≈ 0.92–1.05 · type-shift ≈ 0.05 · gold recall 0.83 / type-acc 1.0 ·
diagnostics: Jaccard ≈ 0.60, core_recall ≈ 0.77. These are the noise floor; a candidate
model meaningfully below them is a real regression.

## CI

`.github/workflows/eval.yml` runs `run_eval.py --ci` weekly against the frozen set and on
manual dispatch. A floor breach fails the job and posts to Slack.

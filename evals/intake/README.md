# Intake judge eval

*Created 2026-09-02. The honest gradient for the second LLM step in this pipeline — the
curated-intake judge (two cheap models deciding save/skip against `docs/intake-rubric.md`).
Same discipline as `evals/extraction/`: a frozen labeled set, deterministic graders, floors
that fail CI, and auto-ingest stays off until it passes.*

## What is measured

| Signal | Role | Floor |
|---|---|---|
| **Recall on `save`** | GATE | ≥ 0.90 — missing the usage report Kevin needed is the expensive error |
| **Precision on `save`** | GATE | ≥ 0.70 — an extra launch post costs one row nobody browses |
| Judge agreement rate, disputed count | diagnostic | how often the two models disagree, and how the save-first rule lands |
| Per-source recall/precision | diagnostic | where the rubric is weak (feeds vs citations vs the old queue) |
| Mismatch list | the review surface | false negatives first, with the judge's reason next to the label's note |

The graders are arithmetic on labels (`metrics.py`). No model grades a model.

## Ground truth

`fixtures/labeled_candidates.json` — 75 real posts: 34 from OpenAI's feed (60 days,
stratified toward the rarer categories), 13 from Anthropic's news, 25 from the retired
Blog Pull Queue, 3 podcast-cited reports. Each row: metadata, the label, a one-line note,
and a sha256 of the text the judge saw. **The texts are not in the repo** (they are other
people's copyright): they live in `pipeline/_cache/intake-eval/` (gitignored) and re-scrape
when missing; an upstream edit reports as *drifted*, never as a judge regression.

`_meta.labeled_by` says whose labels these are. **`opus-panel-provisional`** (2026-09-02)
means five Opus reviewers labeled them against the rubric and Kevin's grounding; Kevin's
own pass on the correction page replaces them — rebuild with `build_fixture.py` and
`--labeled-by kevin` when it lands. Re-freeze only on an intentional re-baseline.

## Run it

```bash
./pipeline/venv/bin/python evals/intake/run_eval.py                 # both judges, all 75 (~$0.30)
./pipeline/venv/bin/python evals/intake/run_eval.py --limit 10      # a quick read
./pipeline/venv/bin/python evals/intake/run_eval.py --model openai/gpt-5.6-luna   # one model alone
./pipeline/venv/bin/python evals/intake/run_eval.py --ci --json report.json      # the weekly gate
```

Needs `OPENROUTER_API_KEY` (and `FIRECRAWL_API_KEY` to refill missing texts).

## Reads so far (rubric version = sha prefix of `docs/intake-rubric.md`)

| Date | Rubric | Labels | Recall | Precision | Agreement | Note |
|---|---|---|---|---|---|---|
| 2026-09-02 | v1 `e414eb19` | opus-panel | 0.925 | 0.86 | — | archival pages saved for stale facts; JS-shell scrape saved |
| 2026-09-02 | v2 `60f3a098` | opus-panel | 0.962 | 0.91 | 0.84 | floors ok; the residue is the torn band (grants with criteria, teen safeguards, rollout mechanism inside a customer story) |

## CI

`.github/workflows/eval.yml` → job `intake-eval`, weekly with the extraction eval (the Worker
dispatches both). A floor breach fails the job and posts to Slack.

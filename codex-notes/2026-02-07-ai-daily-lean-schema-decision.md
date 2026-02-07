# AI Daily Lean Schema Decision

*Date: 2026-02-07*

## Why we changed course

The previous model worked, but it was hard to use in Neon because data was split across many specialized tables.

We moved to a simpler design that still answers the key questions but is much easier to maintain:

- **3 AI tables instead of 9+**
- **No required custom views**
- **Most workflow happens in one table (`ai_mentions`)**

## Final schema (lean)

### 1) `ai_runs`

One row per extraction load run.

Stores:
- batch/model metadata
- run timing/status

### 2) `ai_entities`

Canonical entity list.

Stores:
- entity type
- canonical name
- aliases (JSON array)
- primary URL (when known)
- attributes (JSON for extra metadata)

### 3) `ai_mentions`

One row per mention in one episode (this is the main table you use).

Stores:
- episode + run + entity linkage
- mention text + context snippet + optional quote
- sentiment + confidence
- review flags (`needs_review`, `review_reason`, `review_status`)
- facts JSON (for flexible extracted properties)
- link info (`source_url`, `link_status`, `link_confidence`, `link_candidates`)

## Why this still covers target questions

Examples:

- "What report was mentioned last week?"
  - filter `ai_mentions` where `mention_type='report'` and join `episodes` on date.

- "What surveys were mentioned?"
  - filter `mention_type='survey'`.

- "Top 5 most common tools in Q4 2025?"
  - filter `mention_type='software_product'`, date range, group by `canonical_name`.

- "Which X accounts are mentioned most?"
  - filter `mention_type='account'` and `platform in ('x','twitter')`, group by name.

- "Text + link for social post?"
  - filter `mention_type='social_post'`, read `quoted_text` + `source_url`.

## Quality/consistency workflow

1. Load extraction batch to `ai_mentions`.
2. Run alias normalization (`normalize_aliases.py`) to merge obvious duplicates.
3. Run link discovery (`discover_links.py`) to fill reliable URLs.
4. Use `needs_review` + missing links queue for manual pass.

This keeps agent-in-the-loop and human-in-the-loop simple:
- Agent proposes.
- Schema stores confidence + review flags.
- Human only reviews uncertain rows.

## Validation runs completed on lean schema

- Small batch: 5 episodes (`batch-01-focused-mini`)
- Large batch: 25 episodes (`batch-25-lean-v2`)

Current shape in Neon:
- run 1: 5 episodes, 75 mentions
- run 2: 25 episodes, 290 mentions

## Open improvements (next)

- tighten extraction prompt to reduce noisy/fictional entity names
- improve survey/report typing consistency
- add a very small manual review checklist per run (links + low-confidence rows)

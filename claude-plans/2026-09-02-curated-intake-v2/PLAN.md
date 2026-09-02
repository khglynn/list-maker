# Curated intake v2 + ads as data — arc plan

**Written:** 2026-09-02 (Fable session "list maker with llm judges"). **Status:** designing → building. **Live cursor:** `NOW.md`. **Parent:** `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → "Next arc" (goal, acceptance, and Kevin's decisions 1–2 live there; not restated).

**Decisions from Kevin, 2026-09-02 (chat):** (1) the eval set is pre-labeled by the session and corrected by Kevin in one pass; (2) the judge models run through OpenRouter — key "Listmaker", $15/week cap, expires 2027-09-02, stored as the repo secret `OPENROUTER_API_KEY` and in `.env.local`; (3) link resolution for podcast-cited reports is in scope; (4) Notion stays the human surface — the Blog Pull Queue DB becomes the **intake log** (every judged candidate with its verdict and reason), nothing waits on a checkbox.

## What the live data said before designing (2026-09-02)

- The 14 June "skips" are not a taste signal: 11 are dead links or 22-word scrape stubs, 2 are 404s, 1 is a real no. Negatives come from the feeds themselves.
- The driving case, "How people are using ChatGPT", sat in the queue unchecked since June.
- Positives on hand: 3 saved posts, 5 research docs, 1 clipped article, ~10 obvious yeses among the 31 queue rows.
- Ads: 101 of the last 106 AI Daily episodes carry a "Brought to you by:" block in their Taddy show notes with sponsor names + URLs; 63 mentions across 28 entities read as ads; all 15,342 mentions are flagged editorial. Hard Fork has no such block.
- URLs: 3 of 103 report/paper/survey mentions in 120 days carry a URL. `discover_links.py` exists but never ran on a schedule; show-notes links go unused.
- Feeds: OpenAI RSS is official, 102 posts in 60 days (Company 22, Product 19, Global Affairs 11, Security 7, Safety 5, AI Adoption 4, …). Anthropic has no feed; `/news` is an HTML index (featured block + a 10-item list; `/engineering` is a second index).
- Models on OpenRouter: `google/gemini-3.7-flash` $0.75/$3.75 per M, `openai/gpt-5.6-luna` $0.20/$1.20 per M. Both present; ~$0.002 per verdict.

## Design

### Sources (weekly, `blogs.yml`, dispatched by the Worker on Mondays)
1. **OpenAI RSS** (`openai.com/news/rss.xml`): items published since the last run.
2. **Anthropic** `/news` (featured block + list) and `/engineering` (list), scraped once each via Firecrawl and parsed for date, category, title, URL.
3. **Podcast-cited reports** (`report`/`paper`/`survey`/`blog_post` mentions from the tech shows, last 14 days): resolve a URL by (a) matching the episode's show-notes links, (b) Firecrawl search on the mention name + host, capped at 15 per run. A resolved URL becomes a candidate; unresolved stays visible in the log line.
4. **Manual door:** `save_item.py --url` bypasses the judge (Kevin chose it).

### Candidate lifecycle and schema — `intake_candidates` (sql/009, Kevin's paste)
`url` (canonical, unique) · `source` (openai-rss | anthropic-news | anthropic-engineering | podcast-cited | manual) · `title` · `published_on` · `discovered_at` · `discovered_via` (jsonb: feed item, or episode/mention ids) · scrape: `words`, `links_out`, `text_sha256`, `scraped_at` · verdict: `verdict` (save | skip), `confidence`, `reason`, `judge_model`, `checker_model`, `checker_verdict`, `disputed`, `prompt_version`, `judged_at` · outcome: `status` (judged | saved | skipped | failed | held), `episode_id`, `ingested_at`, `failed_reason`, `override_by` (kevin | null) · Notion: `notion_page_id`, `notion_synced_at`. Provenance travels with the value: every verdict says which model, which prompt, when, why.

### Deterministic pre-checks (a script decides; the model only sees the residue)
- already in `episodes.url` → `skipped: duplicate` · scrape under 200 words → `skipped: thin` (the June "skips") · `.pdf` → `held: pdf` (PDFs live in the Obsidian research folder, local-only; the weekly line names them) · dead link → `skipped: dead`.

### The judge
- **Rubric:** `docs/intake-rubric.md`, versioned (`prompt_version` = rubric hash prefix). Produced by a three-angle Opus panel + adversary + synthesis on 2026-09-02; refined against Kevin's corrections.
- **Input:** title, source, date, category, first ~3,000 words, word count, links out, how it was found.
- **Output:** `{verdict: save|skip, confidence: 0–1, reason: one line}` — strict JSON.
- **Two models, always** (cost is trivial): `google/gemini-3.7-flash` and `openai/gpt-5.6-luna` via OpenRouter, ordered fallback lists like eachie's config. Agree → final. **Disagree → save, marked `disputed`** (recall first: the expensive error is missing the report Kevin needed; a disputed save is visible in Notion and the weekly line).
- Pinned model ids in `show_config`-style constants; a model change re-runs the eval.

### Auto-ingest (after the floor clears)
`save` → `save_item.save_url` (scrape → store → extract mentions → Tech DB → Blog Posts mirror); failure → `failed` + reason. Weekly Slack line: judged N · saved K (titles) · skipped · disputed · held · failed · unresolved podcast citations.

### Notion intake log (the human surface)
The Blog Pull Queue DB is repurposed as **📥 Blog Intake**: Name, URL, Source, Published, Words, Links Out, Verdict, Confidence, Reason, Judge, Disputed, Status, **Pull anyway** (checkbox — the override door: the next weekly run ingests it and records `override_by = kevin`; nothing waits on it). Hub page prose updated. The 31 existing candidates are re-judged through the new path.

### Ads as data (PR 1)
- **Roster:** parse the episode's Taddy show notes for the "Brought to you by:" block → sponsor names + URLs, stored in `episodes.raw_content` → `sponsors` at import.
- **Phrase detector:** windows around "brought to you by", "today's sponsor(s)", "sponsored by", "promo code", "use code", "dot com slash" in the transcript.
- **Rule:** a mention is a sponsor read if its name fuzzy-matches the roster OR its context falls in a phrase window; that overrides the model's `is_editorial`. Extraction **keeps** ads with `is_editorial=false` (today it drops them); the loader stores them with `sponsor_source` (roster | phrase | model) — a provenance column in sql/009.
- **Rollup:** `fetch_entity_rollup` counts editorial mentions fully and ad mentions at most 5; Notion gets **Sponsor** (checkbox) and **Ad mentions** (number); a "Sponsors" view. An entity whose first-ever mention is an ad gets `attributes.first_seen_as_ad`.
- **Re-tag the existing 63** (28 entities): script with `--dry-run` → Kevin's OK → run; no deletes.
- **Health:** a warning if ads exceed 30% of a show's mentions in 30 days (a roster parse failure would show up here).

### Eval (before trust)
`evals/intake/fixtures/labeled_candidates.json` — ~50 candidates (OpenAI feed, Anthropic news, the queue rows, a few podcast-cited reports) with Kevin's labels; `evals/intake/run_eval.py` reports recall on `save`, precision, judge agreement, and per-candidate diffs; floors recall ≥ 0.9 / precision ≥ 0.7; a second job in `eval.yml` weekly. **Shadow mode** (judge + log, no ingest) until the floor clears and one shadow week reads right.

### PRs
1. **Ads as data** — detector, extraction keeps ads, loader provenance, rollup cap, Notion tag, re-tag script, health check, tests.
2. **Intake in shadow mode** — sources, schema, judge, link resolution, eval harness, intake log, weekly line; nothing auto-ingests.
3. **Auto-ingest on** — the switch, the runbook rewrite, hub prose, the checkbox retired.
Each: one concern per commit, tests, CI green, Kevin merges. Never Fable in a fan-out.

### Cost
~60 candidates/week × 2 judges × ~3k tokens ≈ $0.30/week on the $15 cap; ~60 Firecrawl scrapes/week; hard cap 60 new candidates per run, overflow logged.

## Open questions for Kevin
*(filled from the rubric panel's synthesis; only questions whose answer changes a verdict)*

## Log
- 2026-09-02 09:34 — grounding banked (scratchpad), OpenRouter key verified, rubric panel launched (Opus ×3 + adversary + synthesis), OpenAI 60-day feed (102) and Anthropic index (10 + featured) pulled.

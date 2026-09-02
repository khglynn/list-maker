# Curated intake v2 + ads as data — arc plan

**Written:** 2026-09-02 (Fable session "list maker with llm judges"). **Status:** shadow mode live on `main` since 2026-09-02 17:02 CT; PR 3 (auto-ingest on) waits on Kevin's labels + one shadow week. **Live cursor:** `NOW.md`. **Parent:** `claude-plans/2026-09-01-ground-it-cleanup-plan.md` → "Next arc" (goal, acceptance, and Kevin's decisions 1–2 live there; not restated).

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
3. **Podcast-cited reports** (`report`/`paper`/`survey`/`blog_post` mentions from the tech shows, last 14 days): resolve a URL by web search on the cited name (`intake/links.py`, capped at 40 per run). *Show-notes matching was dropped 2026-09-02 after measuring: AI Daily's notes carry 248 links in 30 days, all sponsor or host promos, none to a cited report.* The probe of 14 real mentions: specific titles resolve to the primary source at search rank 1 (openai.com/signals, ramp.com, arxiv, the author's own site); generic names ("BCG paper") never auto-resolve — their hits are stored as candidates. A full-title match is trusted only at rank 1 or on the org's own domain. Resolved URLs are written back to the mention (`source_url`, `link_status=auto_verified`, `link_candidates`) and become `podcast-cited` candidates. Mentions that already carried a URL are a third deterministic source (`intake/mentions.py`): a document-typed mention (report / paper / survey / blog_post) is `podcast-cited` too (a show cited a document; old ones stay findable, so the staleness pre-check is waived), while a URL carried by any other mention type (a product page a host name-dropped) is **`podcast-linked`** and is pre-checked like a feed item — the split the plumbing agent found on 2026-09-02, when seven archival product pages arrived as "cited". Runs daily (`--sources podcast-cited`) so a report cited Monday is judged Tuesday.
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

## Open questions for Kevin (from the rubric panel, 2026-09-02) — **answered 2026-09-02 evening: Kevin approved all 75 labels unchanged and every default below stands** ("the calls are great, no notes or disagreements")
1. **Volume.** The rubric saves ~40% of OpenAI's feed (about 4–5 posts a week from that source alone). Fine, or should the bar rise? His label pass on the 75 candidates answers this empirically.
2. **AI-only, or technology-broad?** The domain gate admits only AI/agents/how-tech-changes-work. His research library has a `tecovas-business` folder; retail tech and e-commerce platforms may belong. Default until answered: AI-only.
3. **Education, teens, consumer safety.** Saves the randomized study of 1,000 students (a live board question); skips teacher rollouts, the teen product, the teen-access position. Default: as is.
4. **Who is a peer?** `PEER_INDUSTRY` fires on consumer retail, apparel, footwear, DTC, specialty retail, e-commerce marketplaces named in the title or lede. Default: that list.
5. **The consumer-channel rule (S11).** Ads, shopping, checkout, agentic purchasing inside an assistant save as a deck item because his employer sells to consumers — the one rule invented on his behalf. Default: on.
6. **Frontier science and safety artifacts** (math proofs, system cards, safeguard notices) save at 0.60–0.70 as the board's "how fast this moves" slide. Default: on; ~4 posts per 60 days.
7. **Podcast-named items no rule wants** (a lawsuit, a codename) skip; the candidate row keeps the name findable. Default: skip.

## Eval reads (the cheap pair vs the Opus panel's labels on the 75-candidate pool; Kevin's corrections replace these as ground truth)
| Rubric | Agree | Recall (save) | Precision (save) | Disputed | Notes |
|---|---|---|---|---|---|
| v1 `e414eb19` (2026-09-02) | 63/75 | 0.925 | 0.86 | 12 | 9 of 12 misses: archival pages saved for stale facts, a JS-shell scrape saved as an article, mixed documents |
| v2 `60f3a098` (2026-09-02) | 67/75 | 0.962 | 0.895 | 12 | remaining misses are the torn band: pricing inside a program post, teen safeguards, rollout mechanism inside a customer story, a grant program with criteria — Kevin's labels decide |

## Log
- 2026-09-02 17:05 — **Landed on main** (PR #33) after Kevin's pastes; retag applied (230/45); first CI run: 69 candidates, 40 would-save (11 disputed), 7 skips, 13 thin, 9 deferred, 60 mirrored. The driving case ("How people are using ChatGPT") judged S1/deck at 0.95.
- 2026-09-02 13:45 — **PR #32 merged into the arc branch** (446 tests): 7 of 14 review findings confirmed and fixed with tests; the retag dry run now finds 229 ad mentions across 45 entities. The arc's shadow-mode scope is complete; what remains before main is Kevin's two pastes and the retag.
- 2026-09-02 13:20 — **PR #31 merged into the arc branch** (346 tests): six-lens review + adversarial verification found 8 real issues, all fixed with tests — a failed Slack post now fails the run, a crash mid-judge is retried, a pre-check that overturns a verdict clears it in Neon and Notion. PR #32 (ads) under the same review. Prerequisites for arc → main: Kevin pastes sql/009 and sql/010, then runs the retag.
- 2026-09-02 13:50 — rubric v1 → v2 from the first live read; Kevin's correction page published (artifact 69d95337, db-backed); PR #31 (intake shadow mode, plumbing) open as draft against the arc branch, under review; backfill 54/64.
- 2026-09-02 11:45 — on the arc branch: `intake/sources.py` (feed + index parsers on frozen fixtures), `intake/judge.py` (pre-checks, two models, the disputed-save rule), `sql/010_intake_candidates.sql`, `intake/links.py` (the probe is its fixture). Two Opus agents building PR 1 (ads) and PR 2's plumbing (store, Notion log, run_intake, blogs.yml) in worktrees; the rubric panel still running.
- 2026-09-02 09:34 — grounding banked (scratchpad), OpenRouter key verified, rubric panel launched (Opus ×3 + adversary + synthesis), OpenAI 60-day feed (102) and Anthropic index (10 + featured) pulled.

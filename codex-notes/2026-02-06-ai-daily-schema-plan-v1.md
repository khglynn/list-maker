# AI Daily Schema Plan (V1, grounded in 25 real transcripts)

*Created: 2026-02-06*

## 1) Confirmed Data Status

- Yes: we have **25 AI Daily episodes + 25 transcripts** in Neon.
- Episode/transcript list (with links):  
  `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/2026-02-06-ai-daily-25-transcript-links.md`
- CSV export of those rows:  
  `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/codex-notes/2026-02-06-ai-daily-25-episodes.csv`
- Local transcript cache (txt + json):  
  `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/pipeline/_cache/ai_daily/transcripts`

## 2) What We Learned From The 25 Transcripts

Real examples found in the transcripts:
- Reports/surveys: `"AI as a Healthcare Ally"`, `"AI Usage Pulse Survey"`, `"PWC survey of CEOs"`.
- Benchmarks/evals: `GPQA`, `LM Arena` evaluations.
- Tools/models/apps: `Claude Code`, `Claude Cowork`, `ChatGPT Health`, `Xcode`, `Codex`, `Gemini`, `Sora`.
- Social references: many "X/Twitter" mentions and quoted posts, often with person names.

Important constraint discovered:
- Transcript text contains very few explicit URLs (only 2 unique URLs in this 25-episode batch), so social-post links are often **not** in transcript text.
- That means URL capture must include external discovery + verification; transcript parsing alone is not enough.

## 3) Schema Goals

- Flexible enough for AI Daily now, reusable for PCHH later.
- Track **what** was mentioned, **where**, **how often**, **when**, and **sentiment**.
- Keep extraction auditable (which model/prompt/run produced each mention).
- Support both LLM Q&A and sortable SQL tables.

## 4) Proposed Core Tables (V1)

### `entity_type_definitions`
Locked dictionary for allowed types (prevents drift over time).

```sql
CREATE TABLE IF NOT EXISTS entity_type_definitions (
  type_key VARCHAR(64) PRIMARY KEY,   -- software_product, model, benchmark, report, survey, paper, account, social_post, blog_post, organization, person, other
  description TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### `entities`
Canonical thing being referenced (tool/report/account/benchmark/model/etc).

```sql
CREATE TABLE IF NOT EXISTS entities (
  id SERIAL PRIMARY KEY,
  entity_type VARCHAR(64) NOT NULL REFERENCES entity_type_definitions(type_key),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  platform VARCHAR(64),             -- x, github, arxiv, web, etc.
  primary_url TEXT,
  external_id TEXT,                 -- e.g., tweet id if available
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  canonical_status VARCHAR(32) NOT NULL DEFAULT 'auto',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_type_name_platform
  ON entities (entity_type, normalized_name, COALESCE(platform, ''));
```

### `entity_aliases`
Alternative spellings/names mapped to one canonical entity.

```sql
CREATE TABLE IF NOT EXISTS entity_aliases (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  alias_text TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  alias_kind VARCHAR(32) NOT NULL DEFAULT 'aka',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE (entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_norm
  ON entity_aliases (normalized_alias);
```

### `extraction_runs`
Audit trail for each extraction pass.

```sql
CREATE TABLE IF NOT EXISTS extraction_runs (
  id SERIAL PRIMARY KEY,
  show_id INTEGER REFERENCES shows(id) ON DELETE SET NULL,
  episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
  run_type VARCHAR(64) NOT NULL DEFAULT 'entity_extraction',
  provider VARCHAR(64),
  model VARCHAR(128),
  prompt_version VARCHAR(64),
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'completed',
  started_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### `entity_mentions`
Atomic mention rows (what was said in a specific episode context).

```sql
CREATE TABLE IF NOT EXISTS entity_mentions (
  id SERIAL PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  transcript_id INTEGER REFERENCES episode_transcripts(id) ON DELETE SET NULL,
  entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  run_id INTEGER REFERENCES extraction_runs(id) ON DELETE SET NULL,

  mention_text TEXT NOT NULL,        -- literal mention
  mention_type VARCHAR(64) NOT NULL, -- same family as entity_type; allows unresolved mentions
  mention_count INTEGER NOT NULL DEFAULT 1,

  sentiment_label VARCHAR(16) NOT NULL DEFAULT 'neutral', -- positive/negative/neutral/mixed/unknown
  sentiment_score NUMERIC(5,4),      -- optional scalar score
  confidence NUMERIC(5,4),           -- extraction confidence

  is_editorial BOOLEAN NOT NULL DEFAULT TRUE, -- false for sponsor/ad mentions
  section_label VARCHAR(64),         -- headlines/main/ad/etc.

  start_char INTEGER,
  end_char INTEGER,
  context_snippet TEXT,
  quoted_text TEXT,                  -- useful for social post quotes
  source_url TEXT,                   -- direct URL if spoken or parsed
  platform VARCHAR(64),              -- x, web, youtube, github, arxiv, etc.
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entity_mentions_episode ON entity_mentions(episode_id);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_entity  ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_type    ON entity_mentions(mention_type);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_date_filter
  ON entity_mentions(created_at);
```

### `mention_review_queue`
Human-in-the-loop queue for uncertain matches/new-type candidates.

```sql
CREATE TABLE IF NOT EXISTS mention_review_queue (
  id SERIAL PRIMARY KEY,
  mention_id INTEGER NOT NULL REFERENCES entity_mentions(id) ON DELETE CASCADE,
  issue_type VARCHAR(64) NOT NULL,   -- low_confidence_match, ambiguous_entity, unknown_type, possible_duplicate, url_unverified
  status VARCHAR(32) NOT NULL DEFAULT 'open', -- open, approved, rejected
  reviewer_notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mention_review_queue_status ON mention_review_queue(status);
```

### `entity_facts`
Flexible facts/tags per entity (example: report contains survey questions).

```sql
CREATE TABLE IF NOT EXISTS entity_facts (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  fact_key VARCHAR(128) NOT NULL,      -- e.g. contains_survey_questions, modality, provider
  fact_value JSONB NOT NULL,           -- true, "image", "OpenAI", etc.
  confidence NUMERIC(5,4),
  source_episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
  source_mention_id INTEGER REFERENCES entity_mentions(id) ON DELETE SET NULL,
  run_id INTEGER REFERENCES extraction_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_entity_facts_key ON entity_facts(fact_key);
CREATE INDEX IF NOT EXISTS idx_entity_facts_entity ON entity_facts(entity_id);
```

### `episode_reference_links`
Store links associated with an episode from any source (transcript, external discovery, manual verification).

```sql
CREATE TABLE IF NOT EXISTS episode_reference_links (
  id SERIAL PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  run_id INTEGER REFERENCES extraction_runs(id) ON DELETE SET NULL,
  url TEXT NOT NULL,
  link_text TEXT,
  platform VARCHAR(64),              -- x, substack, github, arxiv, web
  linked_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  source_kind VARCHAR(64) NOT NULL DEFAULT 'episode_page', -- transcript, episode_page, newsletter, manual
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_episode_reference_links_episode ON episode_reference_links(episode_id);
CREATE INDEX IF NOT EXISTS idx_episode_reference_links_platform ON episode_reference_links(platform);
```

### `reference_link_candidates`
Candidate links discovered externally for mentions; only verified links become trusted.

```sql
CREATE TABLE IF NOT EXISTS reference_link_candidates (
  id SERIAL PRIMARY KEY,
  mention_id INTEGER REFERENCES entity_mentions(id) ON DELETE CASCADE,
  entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
  candidate_url TEXT NOT NULL,
  discovery_method VARCHAR(64) NOT NULL, -- transcript, web_search, firecrawl, manual
  match_confidence NUMERIC(5,4),
  verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified', -- unverified, auto_verified, human_verified, rejected
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  verified_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reference_link_candidates_status
  ON reference_link_candidates(verification_status);
```

## 5) Why This Supports Your Questions

Examples:

- "What was that report mentioned in AI Daily last week?"
  - Filter `entity_mentions` by episode date range + `mention_type='report'`.

- "What reports contain survey questions?"
  - `entities` (`entity_type='report'`) + `entity_facts` where `fact_key='contains_survey_questions'`.

- "Text and link to that social post mentioned in AI Daily Brief?"
  - `entity_mentions.quoted_text` + `entity_mentions.source_url`
  - fallback to `episode_reference_links` if transcript had no URL.

- "Top 5 most commonly mentioned tools in Q4 2025?"
  - Aggregate `SUM(mention_count)` by entity where `entity_type='software_product'` + episode publish date filter.

- "Which X accounts are mentioned most often?"
  - `entity_type='account'` with platform `x` + grouped mention counts.

- "Which benchmarks are most often mentioned for LLMs vs video?"
  - `entity_type='benchmark'` + `entity_facts` modality (`llm`, `video`) + grouped counts.

- "What image models were mentioned in the last 6 months?"
  - `entity_type='model'` + `entity_facts` modality = `image` + date filter.

## 6) Practical Build Order

1. Create the V1 tables above in a Neon branch.
2. Run extraction on the same 25 transcripts into `entities` + `entity_mentions`.
3. Review entity normalization manually (aliases + canonical merges).
4. Run URL discovery + verification (do not assume episode-page links are sufficient).
5. Build one simple sortable web table (MVP) from these tables.

## 7) Decision Before We Execute

- Keep V1 as above (flexible generic model), then test it on the 25 transcripts.
- After the first pass, we lock any needed tweaks before scaling to full backfill and PCHH.

## 8) Test Cadence (User Decision)

- Run extraction in **5-episode batches** (not all 25 at once).
- Reason: each batch gives us feedback, and leaves fresh episodes for the next validation cycle.
- Batch order:
  1. Batch 1: most recent 5 episodes
  2. Batch 2-5: next 5 episodes each, only after schema/prompt adjustments

-- 010: intake_candidates — every blog/article/report the intake surfaced, with the
-- judge's verdict and what happened next. One row per canonical URL.
--
-- Why a table and not the Notion queue: the Notion DB is the human surface (Kevin
-- reads it), Neon is the source of truth (agents and the eval read it). Provenance
-- travels with the value: a verdict row says which model, which rubric version,
-- when, with what confidence and reason — so a bad Monday can be reconstructed
-- from one SELECT (docs/principles.md, "Data with provenance").
-- Additive + idempotent (IF NOT EXISTS), safe to re-run. Kevin's paste (DDL is
-- guard-blocked for agents by design); arc plan claude-plans/2026-09-02-curated-intake-v2/PLAN.md.
CREATE TABLE IF NOT EXISTS intake_candidates (
  id              SERIAL PRIMARY KEY,
  url             TEXT NOT NULL UNIQUE,            -- canonical (import_blog.canonicalize_url)
  source          TEXT NOT NULL,                   -- openai-rss | anthropic-news | anthropic-engineering | podcast-cited | podcast-linked | manual
                                                   -- podcast-cited = a DOCUMENT a show cited (report/paper/survey/blog_post): exempt
                                                   --   from the staleness pre-check, because an old report is still worth reading.
                                                   -- podcast-linked = any other URL a mention carried (a product page a host
                                                   --   name-dropped): staleness applies. Split 2026-09-02.
  title           TEXT,
  published_on    DATE,
  category        JSONB NOT NULL DEFAULT '[]'::jsonb,
  discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  discovered_via  JSONB NOT NULL DEFAULT '{}'::jsonb,  -- feed/index/episode+mention ids

  -- the scrape (NULL until scraped; a thin scrape is a verdict, see status)
  words           INTEGER,
  links_out       INTEGER,
  text_sha256     TEXT,
  scraped_at      TIMESTAMPTZ,

  -- the judge (NULL until judged)
  verdict         TEXT CHECK (verdict IN ('save', 'skip')),
  confidence      NUMERIC(4,3) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  reason          TEXT,
  rule            TEXT,                             -- the rubric rule that fired (S1…K9, R-*, X-*)
  job             TEXT,                             -- the later use a save serves (deck | build | policy | playbook | landscape | findable)
  judge_model     TEXT,
  checker_model   TEXT,
  checker_verdict TEXT CHECK (checker_verdict IN ('save', 'skip')),
  disputed        BOOLEAN NOT NULL DEFAULT FALSE,   -- the two judges disagreed; recall-first rule saved it
  prompt_version  TEXT,                             -- rubric hash prefix; a rubric edit is a new version
  judged_at       TIMESTAMPTZ,

  -- the outcome
  status          TEXT NOT NULL DEFAULT 'discovered' CHECK (status IN (
                    'discovered',  -- surfaced, not yet scraped/judged
                    'judged',      -- verdict recorded, nothing ingested (shadow mode, or a skip)
                    'saved',       -- ingested: episode_id set
                    'skipped',     -- judge said skip, or a pre-check did (see failed_reason for which)
                    'held',        -- needs a human-only step (a PDF → Obsidian, local-only)
                    'failed'       -- ingest attempted and failed; failed_reason says why
                  )),
  precheck        TEXT,                             -- duplicate | thin | pdf | dead — the script decided, not the model
  episode_id      INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
  ingested_at     TIMESTAMPTZ,
  failed_reason   TEXT,
  override_by     TEXT,                             -- 'kevin' when the Notion "Pull anyway" box drove the ingest

  -- the Notion mirror (the intake log Kevin reads)
  notion_page_id  TEXT,
  notion_synced_at TIMESTAMPTZ,

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_intake_candidates_status ON intake_candidates(status);
CREATE INDEX IF NOT EXISTS idx_intake_candidates_source_published ON intake_candidates(source, published_on DESC);
CREATE INDEX IF NOT EXISTS idx_intake_candidates_judged_at ON intake_candidates(judged_at DESC) WHERE judged_at IS NOT NULL;

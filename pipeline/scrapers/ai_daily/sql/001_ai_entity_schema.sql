-- AI Daily entity extraction schema (lean v2)
-- Goal: keep this easy to understand in Neon with minimal tables.
-- Uses only 3 ai_* tables: runs, entities, mentions.

CREATE TABLE IF NOT EXISTS ai_runs (
  id SERIAL PRIMARY KEY,
  show_id INTEGER REFERENCES shows(id) ON DELETE SET NULL,
  batch_name TEXT NOT NULL,
  run_type VARCHAR(64) NOT NULL DEFAULT 'entity_extraction',
  provider VARCHAR(64) NOT NULL DEFAULT 'openai',
  model VARCHAR(128),
  prompt_version VARCHAR(64),
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'completed',
  started_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_runs_show_id ON ai_runs(show_id);
CREATE INDEX IF NOT EXISTS idx_ai_runs_batch_name ON ai_runs(batch_name);


CREATE TABLE IF NOT EXISTS ai_entities (
  id SERIAL PRIMARY KEY,
  entity_type VARCHAR(64) NOT NULL CHECK (
    entity_type IN (
      'software_product', 'model', 'benchmark', 'report', 'survey', 'paper',
      'account', 'social_post', 'blog_post', 'organization', 'person', 'other'
    )
  ),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  platform VARCHAR(64),
  primary_url TEXT,
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
  attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
  review_status VARCHAR(32) NOT NULL DEFAULT 'auto' CHECK (
    review_status IN ('auto', 'reviewed', 'ignored')
  ),
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_entities_unique
  ON ai_entities (entity_type, normalized_name, COALESCE(platform, ''));
CREATE INDEX IF NOT EXISTS idx_ai_entities_type ON ai_entities(entity_type);


CREATE TABLE IF NOT EXISTS ai_mentions (
  id SERIAL PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES ai_runs(id) ON DELETE CASCADE,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  transcript_id INTEGER REFERENCES episode_transcripts(id) ON DELETE SET NULL,
  entity_id INTEGER REFERENCES ai_entities(id) ON DELETE SET NULL,

  mention_text TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  mention_type VARCHAR(64) NOT NULL CHECK (
    mention_type IN (
      'software_product', 'model', 'benchmark', 'report', 'survey', 'paper',
      'account', 'social_post', 'blog_post', 'organization', 'person', 'other'
    )
  ),
  mention_count INTEGER NOT NULL DEFAULT 1,
  platform VARCHAR(64),

  context_snippet TEXT NOT NULL,
  quoted_text TEXT,
  source_url TEXT,
  link_status VARCHAR(32) NOT NULL DEFAULT 'missing' CHECK (
    link_status IN ('missing', 'auto_verified', 'manual_verified', 'rejected')
  ),
  link_confidence NUMERIC(5,4),
  link_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,

  sentiment_label VARCHAR(16) NOT NULL DEFAULT 'unknown',
  confidence NUMERIC(5,4),
  is_editorial BOOLEAN NOT NULL DEFAULT TRUE,

  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  review_reason TEXT,
  review_status VARCHAR(32) NOT NULL DEFAULT 'open' CHECK (
    review_status IN ('open', 'resolved', 'ignored')
  ),

  facts JSONB NOT NULL DEFAULT '[]'::jsonb,
  tags JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_mentions_run ON ai_mentions(run_id);
CREATE INDEX IF NOT EXISTS idx_ai_mentions_episode ON ai_mentions(episode_id);
CREATE INDEX IF NOT EXISTS idx_ai_mentions_entity ON ai_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_ai_mentions_type ON ai_mentions(mention_type);
CREATE INDEX IF NOT EXISTS idx_ai_mentions_review ON ai_mentions(review_status);
CREATE INDEX IF NOT EXISTS idx_ai_mentions_source_url ON ai_mentions(source_url);

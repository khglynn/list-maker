-- AI Daily entity extraction schema (draft v1)
-- Non-destructive: creates new ai_* tables only.

CREATE TABLE IF NOT EXISTS ai_entity_type_definitions (
  type_key VARCHAR(64) PRIMARY KEY,
  description TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ai_entities (
  id SERIAL PRIMARY KEY,
  entity_type VARCHAR(64) NOT NULL REFERENCES ai_entity_type_definitions(type_key),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  platform VARCHAR(64),
  primary_url TEXT,
  external_id TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  canonical_status VARCHAR(32) NOT NULL DEFAULT 'auto',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_entities_type_name_platform
  ON ai_entities (entity_type, normalized_name, COALESCE(platform, ''));

CREATE TABLE IF NOT EXISTS ai_entity_aliases (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES ai_entities(id) ON DELETE CASCADE,
  alias_text TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  alias_kind VARCHAR(32) NOT NULL DEFAULT 'aka',
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  UNIQUE (entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_ai_entity_aliases_norm
  ON ai_entity_aliases (normalized_alias);

CREATE TABLE IF NOT EXISTS ai_extraction_runs (
  id SERIAL PRIMARY KEY,
  show_id INTEGER REFERENCES shows(id) ON DELETE SET NULL,
  episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
  batch_name TEXT,
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

CREATE INDEX IF NOT EXISTS idx_ai_extraction_runs_show_id ON ai_extraction_runs(show_id);
CREATE INDEX IF NOT EXISTS idx_ai_extraction_runs_batch_name ON ai_extraction_runs(batch_name);

CREATE TABLE IF NOT EXISTS ai_entity_mentions (
  id SERIAL PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  transcript_id INTEGER REFERENCES episode_transcripts(id) ON DELETE SET NULL,
  entity_id INTEGER REFERENCES ai_entities(id) ON DELETE SET NULL,
  run_id INTEGER REFERENCES ai_extraction_runs(id) ON DELETE CASCADE,

  mention_text TEXT NOT NULL,
  mention_type VARCHAR(64) NOT NULL REFERENCES ai_entity_type_definitions(type_key),
  mention_count INTEGER NOT NULL DEFAULT 1,

  sentiment_label VARCHAR(16) NOT NULL DEFAULT 'neutral',
  sentiment_score NUMERIC(5,4),
  confidence NUMERIC(5,4),
  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  review_reason TEXT,

  is_editorial BOOLEAN NOT NULL DEFAULT TRUE,
  section_label VARCHAR(64),

  start_char INTEGER,
  end_char INTEGER,
  context_snippet TEXT,
  quoted_text TEXT,
  source_url TEXT,
  platform VARCHAR(64),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_entity_mentions_episode ON ai_entity_mentions(episode_id);
CREATE INDEX IF NOT EXISTS idx_ai_entity_mentions_entity ON ai_entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_ai_entity_mentions_type ON ai_entity_mentions(mention_type);
CREATE INDEX IF NOT EXISTS idx_ai_entity_mentions_run ON ai_entity_mentions(run_id);
CREATE INDEX IF NOT EXISTS idx_ai_entity_mentions_needs_review ON ai_entity_mentions(needs_review);

CREATE TABLE IF NOT EXISTS ai_mention_review_queue (
  id SERIAL PRIMARY KEY,
  mention_id INTEGER NOT NULL REFERENCES ai_entity_mentions(id) ON DELETE CASCADE,
  issue_type VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  reviewer_notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_mention_review_queue_status
  ON ai_mention_review_queue(status);

CREATE TABLE IF NOT EXISTS ai_entity_facts (
  id SERIAL PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES ai_entities(id) ON DELETE CASCADE,
  fact_key VARCHAR(128) NOT NULL,
  fact_value JSONB NOT NULL,
  confidence NUMERIC(5,4),
  source_episode_id INTEGER REFERENCES episodes(id) ON DELETE SET NULL,
  source_mention_id INTEGER REFERENCES ai_entity_mentions(id) ON DELETE SET NULL,
  run_id INTEGER REFERENCES ai_extraction_runs(id) ON DELETE CASCADE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_entity_facts_key ON ai_entity_facts(fact_key);
CREATE INDEX IF NOT EXISTS idx_ai_entity_facts_entity ON ai_entity_facts(entity_id);
CREATE INDEX IF NOT EXISTS idx_ai_entity_facts_run ON ai_entity_facts(run_id);

CREATE TABLE IF NOT EXISTS ai_reference_link_candidates (
  id SERIAL PRIMARY KEY,
  mention_id INTEGER REFERENCES ai_entity_mentions(id) ON DELETE CASCADE,
  entity_id INTEGER REFERENCES ai_entities(id) ON DELETE SET NULL,
  candidate_url TEXT NOT NULL,
  discovery_method VARCHAR(64) NOT NULL,
  match_confidence NUMERIC(5,4),
  verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  verified_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_reference_link_candidates_status
  ON ai_reference_link_candidates(verification_status);

CREATE TABLE IF NOT EXISTS ai_episode_reference_links (
  id SERIAL PRIMARY KEY,
  episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
  run_id INTEGER REFERENCES ai_extraction_runs(id) ON DELETE SET NULL,
  url TEXT NOT NULL,
  link_text TEXT,
  platform VARCHAR(64),
  linked_entity_id INTEGER REFERENCES ai_entities(id) ON DELETE SET NULL,
  source_kind VARCHAR(64) NOT NULL DEFAULT 'manual',
  verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_episode_reference_links_episode
  ON ai_episode_reference_links(episode_id);
CREATE INDEX IF NOT EXISTS idx_ai_episode_reference_links_platform
  ON ai_episode_reference_links(platform);

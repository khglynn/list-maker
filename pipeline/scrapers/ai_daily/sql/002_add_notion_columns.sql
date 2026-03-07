-- Add Notion sync tracking columns to ai_entities
-- Run: 2026-03-06

ALTER TABLE ai_entities
  ADD COLUMN IF NOT EXISTS notion_page_id TEXT,
  ADD COLUMN IF NOT EXISTS notion_synced_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_ai_entities_notion_page_id
  ON ai_entities(notion_page_id)
  WHERE notion_page_id IS NOT NULL;

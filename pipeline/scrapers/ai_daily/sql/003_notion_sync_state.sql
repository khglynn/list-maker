-- 003_notion_sync_state.sql  (Workstream A4)
-- Per-entity Notion sync state: record each sync attempt's outcome instead of
-- swallowing failures, so failures can be retried and a high failure rate alerts.
-- Additive + nullable + IF NOT EXISTS → safe to re-run, no impact on existing rows.
ALTER TABLE ai_entities ADD COLUMN IF NOT EXISTS notion_sync_status     text;
ALTER TABLE ai_entities ADD COLUMN IF NOT EXISTS notion_sync_error      text;
ALTER TABLE ai_entities ADD COLUMN IF NOT EXISTS notion_sync_attempt_at timestamp without time zone;

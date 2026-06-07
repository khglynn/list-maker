-- 006: track which transcripts have been mirrored to the Notion Transcripts DB (WS3b).
--
-- Enables an idempotent, resumable sync: the job processes only episodes whose
-- notion_transcript_page_id is NULL, so a re-run never duplicates a page and a crash
-- mid-backfill just resumes. Additive + idempotent (IF NOT EXISTS), safe to re-run.

ALTER TABLE episode_transcripts
    ADD COLUMN IF NOT EXISTS notion_transcript_page_id text,
    ADD COLUMN IF NOT EXISTS notion_transcript_synced_at timestamp;

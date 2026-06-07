-- 005: full-text search over episode transcripts (Workstream 3a).
--
-- A GENERATED tsvector column + GIN index. Generated-always means it is recomputed on
-- every INSERT/UPDATE automatically — new transcripts are indexed with no pipeline
-- change (self-healing). coalesce(...,'') keeps the expression non-null and immutable so
-- the generated column is valid. Additive + idempotent (IF NOT EXISTS), safe to re-run.
--
-- This indexes the transcript shows (AI Daily, Hard Fork, PCHH, SOP/TAL where present);
-- query with pipeline/search_transcripts.py (websearch_to_tsquery + ts_headline snippets).

ALTER TABLE episode_transcripts
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(transcript_text, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_episode_transcripts_search_vector
    ON episode_transcripts USING GIN (search_vector);

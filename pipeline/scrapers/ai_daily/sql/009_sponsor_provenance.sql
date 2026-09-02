-- 009: record HOW we know a mention is a sponsor read, not just that it is.
--
-- Until now extraction DROPPED every mention the model flagged as an ad, so the only
-- ads in this table are the ones the model missed — 891 of them, all sitting at
-- is_editorial = true (measured 2026-09-02). Kevin's rule (2026-09-01) is that ads are
-- kept, tagged, and weight-capped, never deleted, so extraction now keeps them and the
-- loader has to say where the verdict came from. Provenance is a first-class column
-- here (docs/principles.md): 'roster' = the publisher's own "Brought to you by:" block
-- in the episode's show notes, 'phrase' = the mention's context sits inside a sponsor
-- read in the transcript, 'model' = the extractor's own is_editorial=false.
--
-- NULL means editorial — the absence of evidence, not a fourth category, and the reason
-- there is no DEFAULT and no CHECK forbidding NULL. Prefer NULL to a fake value.
--
-- Additive and idempotent: no rewrite of the 16,285 existing rows, which stay NULL
-- (editorial) until retag_sponsor_mentions.py reclassifies them. Safe to re-run.
-- PREREQUISITE: run this before merging the ads-as-data PR — the loader writes the
-- column unconditionally, so an un-migrated database fails the next extraction load.
-- DDL is Kevin's paste; agents don't run it.
ALTER TABLE ai_mentions
  ADD COLUMN IF NOT EXISTS sponsor_source TEXT;

-- Keep the vocabulary closed so a typo can't invent a fourth source. NOT VALID would
-- skip the scan of existing rows, but every existing row is NULL and NULL passes, so a
-- plain validated constraint costs one cheap pass and leaves nothing to validate later.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ai_mentions_sponsor_source_check'
  ) THEN
    ALTER TABLE ai_mentions
      ADD CONSTRAINT ai_mentions_sponsor_source_check
      CHECK (sponsor_source IS NULL OR sponsor_source IN ('roster', 'phrase', 'model'));
  END IF;
END $$;

-- The health check and the rollup both ask "which mentions are ads", never "which are
-- editorial", so the index only covers the rows that are actually queried.
CREATE INDEX IF NOT EXISTS idx_ai_mentions_sponsor_source
  ON ai_mentions (sponsor_source)
  WHERE sponsor_source IS NOT NULL;

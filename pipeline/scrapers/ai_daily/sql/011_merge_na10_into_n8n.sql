-- 011: merge the phantom entity "NA10" (id 793) into n8n (id 295). Data fix, not DDL.
--
-- Why: mention 1222 (AI Daily Brief 2025-10-25, "Why Data is the Biggest Barrier to AI
-- Readiness") reads "the NA10 and the Zapiers and the MAKES" — the transcriber's spelling
-- of n8n, named beside Zapier and Make. The model itself wrote "possibly misheard" and
-- scored it 0.5 — an honest 0.5, not the pre-Phase-4 sanitizer default, so the score
-- stays. Found while Phase 4 removed that default (PR #44); Kevin agreed 2026-09-04.
--
-- Mirrors normalize_aliases.merge_entity_into: mentions re-pointed to the winner, the
-- loser's name kept as an alias so a future "NA10" folds into n8n automatically, loser
-- deleted. The review question on the mention is answered, so it closes. Idempotent by
-- precondition: a second run raises instead of touching anything. Kevin's paste (DELETE
-- is guard-blocked for agents by design); runner in NOW.md.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM ai_entities WHERE id = 295 AND canonical_name = 'n8n') THEN
    RAISE EXCEPTION 'winner 295 is not n8n — stop';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM ai_entities WHERE id = 793 AND canonical_name = 'NA10') THEN
    RAISE EXCEPTION 'loser 793 is not NA10 — already merged?';
  END IF;
END $$;

UPDATE ai_mentions
SET entity_id      = 295,
    canonical_name = 'n8n',
    needs_review   = FALSE,
    review_status  = 'resolved',
    review_reason  = review_reason || ' Resolved 2026-09-04: a transcription of n8n; merged into entity 295 (sql/011).',
    updated_at     = NOW()
WHERE entity_id = 793;

UPDATE ai_entities
SET aliases    = (SELECT jsonb_agg(DISTINCT a) FROM jsonb_array_elements_text(aliases || '["NA10"]'::jsonb) AS a),
    updated_at = NOW()
WHERE id = 295;

DELETE FROM ai_entities WHERE id = 793;

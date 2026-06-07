-- 004: extend ai_entities.entity_type CHECK with the media taxonomy (Workstream D).
--
-- Additive only: every existing row already satisfies the broader constraint, so this
-- is safe to run against live data. DROP IF EXISTS + ADD makes it idempotent (a CHECK
-- constraint can't be altered in place; you replace it).
ALTER TABLE ai_entities DROP CONSTRAINT IF EXISTS ai_entities_entity_type_check;
ALTER TABLE ai_entities ADD CONSTRAINT ai_entities_entity_type_check
    CHECK (entity_type IN (
        -- tech (AI Daily, Hard Fork)
        'software_product', 'model', 'benchmark', 'report', 'survey', 'paper', 'account',
        'social_post', 'blog_post', 'organization', 'person', 'other',
        -- media (PCHH, Culture Gabfest)
        'movie', 'tv_series', 'book', 'music_album', 'music_track', 'game',
        'podcast_series', 'theater_production', 'social_account', 'artist_profile',
        'visual_media_other'
    ));

-- ai_mentions.mention_type mirrors the entity's type at write time (load_entity_batch),
-- so it needs the identical widened taxonomy — otherwise the first media load violates it.
ALTER TABLE ai_mentions DROP CONSTRAINT IF EXISTS ai_mentions_mention_type_check;
ALTER TABLE ai_mentions ADD CONSTRAINT ai_mentions_mention_type_check
    CHECK (mention_type IN (
        -- tech (AI Daily, Hard Fork)
        'software_product', 'model', 'benchmark', 'report', 'survey', 'paper', 'account',
        'social_post', 'blog_post', 'organization', 'person', 'other',
        -- media (PCHH, Culture Gabfest)
        'movie', 'tv_series', 'book', 'music_album', 'music_track', 'game',
        'podcast_series', 'theater_production', 'social_account', 'artist_profile',
        'visual_media_other'
    ));

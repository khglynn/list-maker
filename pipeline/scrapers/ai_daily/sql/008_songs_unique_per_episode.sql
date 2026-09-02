-- 008: one row per (episode, song). Application code deduped songs; the schema didn't.
--
-- Three true duplicate pairs existed on 2026-09-01 — identical Spotify track ids, the
-- only difference a trailing space in the title (ids 4738/5645, 5410/5211, 5413/5310).
-- Step 1 removes the space-padded extras (verified by hand; Kevin's per-op OK required
-- for the DELETE). Step 2 makes the class impossible. Run by Kevin, in order.
DELETE FROM songs WHERE id IN (5645, 5211, 5310);

CREATE UNIQUE INDEX IF NOT EXISTS songs_episode_title_artist_unique
    ON songs (episode_id, LOWER(BTRIM(title)), LOWER(BTRIM(artist)));

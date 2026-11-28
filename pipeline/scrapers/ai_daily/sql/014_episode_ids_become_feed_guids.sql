-- 014: every podcast episode's stored id becomes its own feed guid (the move off Taddy, phase 3).
--
-- Why: episodes.url is each episode's identity (UNIQUE), and until now it held whatever the
-- writer of the day used: a Taddy id (https://api.taddy.org/podcast-episode/<uuid>), a Spotify
-- page, an npr.org page. The feed importer (scrapers/rss/import_feed.py) writes the feed's own
-- <guid>, the scheme Culture Gabfest has always used. This moves the rows already held onto it,
-- so the feed check compares one identity per show and nothing keeps a Taddy id after the cancel.
--   - AI Daily Brief (3), Pop Culture Happy Hour (11), Hard Fork (48): every row carries the guid
--     Taddy saved in raw_content; url := that guid. 1,837 rows on 2026-11-23, plus any added since.
--   - This American Life (2): its 28 Taddy-id rows have no saved payload, so the guids come from the
--     map banked on 2026-11-23 (claude-plans/2026-11-23-off-taddy-probes/out/tal-taddy-uuid-to-guid.json).
--     Its 889 website-url rows are untouched: they are older than the feed's 16 items, and the
--     TAL scraper reads them as page urls.
--   - Switched On Pop is untouched: its website scraper writes its urls (show_config.episode_identity).
--   - Machine Gods gets its shows row (it replaces Hard Fork; Kevin, 2026-11-23).
-- Notion is unaffected: the Transcripts DB stores no episode url, and the Tech and Media DBs store
-- entity urls, not episode ids.
--
-- Run it back to back with merging the cut-over PR, either order, with no daily run (20:30 UTC)
-- between them: that run is the only writer of new Taddy-id rows, and the first one after the merge
-- uses the feed importer. Take the Neon branch first (pre-off-taddy-<date>); that is the rollback.
-- One transaction. Every precondition is checked before anything changes, and a re-run changes
-- nothing (rows already on their guid are skipped; the TAL rows match only while they still hold
-- their Taddy url). Kevin's paste (UPDATE + INSERT), with the runner in the cut-over PR.
BEGIN;

CREATE TEMP TABLE tal_map (episode_id int PRIMARY KEY, taddy_url text NOT NULL, guid text NOT NULL) ON COMMIT DROP;
INSERT INTO tal_map (episode_id, taddy_url, guid) VALUES
    (7423, 'https://api.taddy.org/podcast-episode/0b3f0d5f-4673-4bc5-8b60-f4b1596eeb7d', '45933 at https://www.thisamericanlife.org'),
    (13672, 'https://api.taddy.org/podcast-episode/85097c98-5fb1-441c-a98d-08e043e4b160', '46104 at https://www.thisamericanlife.org'),
    (7422, 'https://api.taddy.org/podcast-episode/5320abd4-1eab-4a1a-8d2a-ccd59e06faf3', '46148 at https://www.thisamericanlife.org'),
    (7846, 'https://api.taddy.org/podcast-episode/a4d1bd8d-1b8a-4e23-96a4-46ff7e239890', '46146 at https://www.thisamericanlife.org'),
    (7845, 'https://api.taddy.org/podcast-episode/1078d642-3408-4974-8ff1-3ae4bc6eae01', '46156 at https://www.thisamericanlife.org'),
    (7421, 'https://api.taddy.org/podcast-episode/add5ecc2-e038-4bde-a0e8-5ebaa8310db5', '46163 at https://www.thisamericanlife.org'),
    (7420, 'https://api.taddy.org/podcast-episode/cdfba1d6-383f-4a2a-9fda-a639221ed5a4', '46170 at https://www.thisamericanlife.org'),
    (7419, 'https://api.taddy.org/podcast-episode/f1116008-8557-436b-bbe6-9c87ccd068f6', '46176 at https://www.thisamericanlife.org'),
    (7418, 'https://api.taddy.org/podcast-episode/bdd599c5-1bb0-4fc8-a3b6-6ae3aa7570a0', '46177 at https://www.thisamericanlife.org'),
    (7417, 'https://api.taddy.org/podcast-episode/c804efe0-077d-463f-aad6-270478d7f48a', '46192 at https://www.thisamericanlife.org'),
    (7416, 'https://api.taddy.org/podcast-episode/b4a17459-b752-45ed-b784-e0a2d01af010', '46201 at https://www.thisamericanlife.org'),
    (7526, 'https://api.taddy.org/podcast-episode/3975842c-2961-4327-83db-783ed61bdc38', '46200 at https://www.thisamericanlife.org'),
    (7837, 'https://api.taddy.org/podcast-episode/7157432e-b6f1-4597-9b6a-f1b9a27cae17', '37230 at https://www.thisamericanlife.org'),
    (8168, 'https://api.taddy.org/podcast-episode/9739c752-f131-4d8a-9a91-d45962d8b31d', '46208 at https://www.thisamericanlife.org'),
    (8532, 'https://api.taddy.org/podcast-episode/6ff904d4-46d6-47bd-ad4f-d5ba60542822', '37467 at https://www.thisamericanlife.org'),
    (8843, 'https://api.taddy.org/podcast-episode/e457f2c3-000d-47ac-a94e-0abac595ca71', '46218 at https://www.thisamericanlife.org'),
    (9707, 'https://api.taddy.org/podcast-episode/150faab0-2a6d-4240-bac6-d14cbe47664d', '37224 at https://www.thisamericanlife.org'),
    (10119, 'https://api.taddy.org/podcast-episode/bc45776b-0d42-44e3-8f7c-c0d2fdef0150', '43933 at https://www.thisamericanlife.org'),
    (10535, 'https://api.taddy.org/podcast-episode/e6b77a0b-59b9-4e60-bee7-92450915d95e', '46224 at https://www.thisamericanlife.org'),
    (10947, 'https://api.taddy.org/podcast-episode/1388a4dd-2f1a-4c7c-972f-c5fa82f6ddad', '46226 at https://www.thisamericanlife.org'),
    (11362, 'https://api.taddy.org/podcast-episode/cee25f1a-bfd0-454d-aa13-23810eedb86d', '46233 at https://www.thisamericanlife.org'),
    (11677, 'https://api.taddy.org/podcast-episode/d70c7c31-1e57-4735-9fec-c064257c03a1', '37246 at https://www.thisamericanlife.org'),
    (12086, 'https://api.taddy.org/podcast-episode/5661fc47-da9b-42d7-bd81-d1b1af8985f5', '37184 at https://www.thisamericanlife.org'),
    (12495, 'https://api.taddy.org/podcast-episode/184dbabd-9732-4271-8882-fd211dcf50fb', '37167 at https://www.thisamericanlife.org'),
    (12903, 'https://api.taddy.org/podcast-episode/02ddd53b-4e49-47b8-a1b2-dd8416e012df', '46252 at https://www.thisamericanlife.org'),
    (13317, 'https://api.taddy.org/podcast-episode/42962444-d5a3-4121-8784-10cacab37340', '46253 at https://www.thisamericanlife.org'),
    (13671, 'https://api.taddy.org/podcast-episode/0975286b-95ae-4a8f-b166-a323376ffcbd', '46259 at https://www.thisamericanlife.org'),
    (14085, 'https://api.taddy.org/podcast-episode/178d4cef-6c21-4270-bb05-15c360614833', '37237 at https://www.thisamericanlife.org');

CREATE TEMP TABLE moves ON COMMIT DROP AS
  SELECT e.id, e.show_id, e.url AS old_url, (e.raw_content::jsonb ->> 'guid') AS new_url
  FROM episodes e
  WHERE e.show_id IN (3, 11, 48)
    AND e.raw_content LIKE '{%'
    AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL
    AND e.url IS DISTINCT FROM (e.raw_content::jsonb ->> 'guid')
  UNION ALL
  SELECT e.id, e.show_id, e.url, m.guid
  FROM episodes e JOIN tal_map m ON m.episode_id = e.id AND m.taddy_url = e.url
  WHERE e.show_id = 2;

DO $$
DECLARE
  n_moves int; n_dupe int; n_clash int; n_left int; n_tal_other int;
BEGIN
  SELECT COUNT(*) INTO n_moves FROM moves;
  -- Every row of the three entity shows must have a guid to move to; one without would be left
  -- on its old id and silently fail the identity check later.
  SELECT COUNT(*) INTO n_left FROM episodes e
    WHERE e.show_id IN (3, 11, 48)
      AND NOT (e.raw_content LIKE '{%' AND (e.raw_content::jsonb ->> 'guid') IS NOT NULL);
  IF n_left > 0 THEN
    RAISE EXCEPTION '% row(s) of AI Daily / PCHH / Hard Fork have no saved guid; nothing changed', n_left;
  END IF;
  -- No guid may repeat among the moves...
  SELECT COUNT(*) INTO n_dupe FROM (SELECT new_url FROM moves GROUP BY new_url HAVING COUNT(*) > 1) d;
  IF n_dupe > 0 THEN
    RAISE EXCEPTION '% guid(s) would be shared by two rows; nothing changed', n_dupe;
  END IF;
  -- ...or already be some OTHER row's url (episodes.url is unique across every show).
  SELECT COUNT(*) INTO n_clash FROM moves mv JOIN episodes e ON e.url = mv.new_url AND e.id <> mv.id;
  IF n_clash > 0 THEN
    RAISE EXCEPTION '% guid(s) are already another row''s url; nothing changed', n_clash;
  END IF;
  -- TAL: a mapped row whose url is no longer its Taddy url has been changed by something else.
  SELECT COUNT(*) INTO n_tal_other FROM tal_map m JOIN episodes e ON e.id = m.episode_id
    WHERE e.url <> m.taddy_url AND e.url <> m.guid;
  IF n_tal_other > 0 THEN
    RAISE EXCEPTION '% TAL row(s) hold neither their Taddy url nor their guid; nothing changed', n_tal_other;
  END IF;
  RAISE NOTICE 'moving % row(s)', n_moves;
END $$;

UPDATE episodes e SET url = mv.new_url FROM moves mv WHERE e.id = mv.id;

-- An explicit id, so show_config can name it before this runs. 65 follows the last hand-numbered
-- show (64, saved-episodes); the id sequence is far past it (566 on 2026-11-23), so nothing else
-- will ever take it.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM shows WHERE id = 65 AND slug <> 'machine-gods') THEN
    RAISE EXCEPTION 'shows.id 65 is taken by another show; nothing changed';
  END IF;
END $$;
INSERT INTO shows (id, name, slug, website_url, created_at)
VALUES (65, 'Machine Gods', 'machine-gods', 'https://www.npr.org/podcasts/g-s1-143588/machine-gods', NOW())
ON CONFLICT (slug) DO NOTHING;

-- Read-back inside the transaction: no Taddy id is left on any of the four shows, and every row of
-- the three entity shows now holds its own guid.
DO $$
DECLARE n_taddy int; n_off int;
BEGIN
  SELECT COUNT(*) INTO n_taddy FROM episodes
    WHERE show_id IN (2, 3, 11, 48) AND url LIKE 'https://api.taddy.org/podcast-episode/%';
  SELECT COUNT(*) INTO n_off FROM episodes e
    WHERE e.show_id IN (3, 11, 48) AND e.url IS DISTINCT FROM (e.raw_content::jsonb ->> 'guid');
  IF n_taddy > 0 OR n_off > 0 THEN
    RAISE EXCEPTION 'read-back failed: % Taddy id(s) left, % row(s) off their guid; rolled back', n_taddy, n_off;
  END IF;
END $$;

COMMIT;

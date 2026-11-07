-- 013: give four saved essays their own titles. Data fix, not DDL.
--
-- darioamodei.com titles its pages "Dario Amodei — <essay>", and the blog importer kept the part
-- before the dash, so four different essays were stored as "Dario Amodei". Three were saved on
-- 2026-09-28, and the duplicate-episode check (show + title + date) has read them as one post stored
-- three times ever since: the daily entities run was red on that alone from 2026-09-29 (issue #68).
-- import_blog.pick_title now drops a site name that comes first; this fixes the rows already there.
-- The Notion Blog Posts copies were renamed by hand on 2026-11-06 (their sync never re-titles).
-- Each row is matched on its id, its URL and the wrong title, so a re-run changes nothing and a row
-- that isn't what this file expects stops it. Kevin's paste (UPDATE).
DO $$ BEGIN
  IF (SELECT COUNT(*) FROM episodes WHERE title = 'Dario Amodei' AND (id, url) IN (
        (10604, 'https://darioamodei.com/post/we-must-pace-the-frontier'),
        (11038, 'https://darioamodei.com/essay/the-adolescence-of-technology'),
        (11041, 'https://darioamodei.com/post/the-urgency-of-interpretability'),
        (11048, 'https://darioamodei.com/essay/machines-of-loving-grace'))) <> 4 THEN
    RAISE EXCEPTION 'the four rows are not as expected — already retitled, or the ids moved?';
  END IF;
END $$;
UPDATE episodes SET title = 'We Must Pace the Frontier'      WHERE id = 10604 AND title = 'Dario Amodei';
UPDATE episodes SET title = 'The Adolescence of Technology'  WHERE id = 11038 AND title = 'Dario Amodei';
UPDATE episodes SET title = 'The Urgency of Interpretability' WHERE id = 11041 AND title = 'Dario Amodei';
UPDATE episodes SET title = 'Machines of Loving Grace'       WHERE id = 11048 AND title = 'Dario Amodei';

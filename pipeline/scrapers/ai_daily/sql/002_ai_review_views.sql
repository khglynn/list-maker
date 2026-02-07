-- AI Daily review views (read-friendly)

CREATE OR REPLACE VIEW ai_v_run_summary AS
SELECT r.id AS run_id,
       r.batch_name,
       r.model,
       r.prompt_version,
       r.created_at,
       COUNT(DISTINCT m.id) AS mention_rows,
       COUNT(DISTINCT f.id) AS fact_rows,
       COUNT(DISTINCT q.id) AS review_rows,
       COUNT(DISTINCT CASE WHEN m.is_editorial THEN m.id END) AS editorial_mentions,
       COUNT(DISTINCT CASE WHEN m.needs_review THEN m.id END) AS needs_review_mentions
FROM ai_extraction_runs r
LEFT JOIN ai_entity_mentions m ON m.run_id = r.id
LEFT JOIN ai_entity_facts f ON f.run_id = r.id
LEFT JOIN ai_mention_review_queue q ON q.mention_id = m.id
GROUP BY r.id;


CREATE OR REPLACE VIEW ai_v_priority_mentions AS
SELECT m.id AS mention_id,
       m.run_id,
       r.batch_name,
       r.model,
       e.publish_date,
       e.id AS episode_id,
       e.title AS episode_title,
       m.mention_type,
       COALESCE(ent.canonical_name, m.mention_text) AS canonical_name,
       m.mention_text,
       m.platform,
       m.source_url,
       m.sentiment_label,
       m.confidence,
       m.needs_review,
       m.review_reason,
       m.context_snippet,
       m.quoted_text
FROM ai_entity_mentions m
JOIN ai_extraction_runs r ON r.id = m.run_id
JOIN episodes e ON e.id = m.episode_id
LEFT JOIN ai_entities ent ON ent.id = m.entity_id
WHERE m.is_editorial = TRUE
  AND m.mention_type IN (
    'software_product', 'model', 'benchmark', 'report', 'survey', 'paper',
    'account', 'social_post', 'blog_post'
  );


CREATE OR REPLACE VIEW ai_v_priority_entity_counts AS
SELECT m.run_id,
       r.batch_name,
       r.model,
       m.mention_type,
       COALESCE(ent.canonical_name, m.mention_text) AS canonical_name,
       COUNT(*) AS mention_rows,
       COUNT(DISTINCT m.episode_id) AS episodes_mentioned,
       MIN(e.publish_date) AS first_mention_date,
       MAX(e.publish_date) AS last_mention_date,
       COUNT(*) FILTER (WHERE m.needs_review) AS needs_review_rows
FROM ai_entity_mentions m
JOIN ai_extraction_runs r ON r.id = m.run_id
JOIN episodes e ON e.id = m.episode_id
LEFT JOIN ai_entities ent ON ent.id = m.entity_id
WHERE m.is_editorial = TRUE
  AND m.mention_type IN (
    'software_product', 'model', 'benchmark', 'report', 'survey', 'paper',
    'account', 'social_post', 'blog_post'
  )
GROUP BY m.run_id, r.batch_name, r.model, m.mention_type, COALESCE(ent.canonical_name, m.mention_text);


CREATE OR REPLACE VIEW ai_v_link_hunt_queue AS
SELECT m.id AS mention_id,
       m.run_id,
       r.batch_name,
       r.model,
       e.publish_date,
       e.id AS episode_id,
       e.title AS episode_title,
       m.mention_type,
       COALESCE(ent.canonical_name, m.mention_text) AS canonical_name,
       m.mention_text,
       m.platform,
       m.source_url,
       m.needs_review,
       m.review_reason,
       m.context_snippet
FROM ai_entity_mentions m
JOIN ai_extraction_runs r ON r.id = m.run_id
JOIN episodes e ON e.id = m.episode_id
LEFT JOIN ai_entities ent ON ent.id = m.entity_id
WHERE m.is_editorial = TRUE
  AND m.mention_type IN ('report', 'survey', 'paper', 'social_post', 'blog_post', 'account')
  AND (m.source_url IS NULL OR BTRIM(m.source_url) = '');


CREATE OR REPLACE VIEW ai_v_open_review_items AS
SELECT q.id AS review_id,
       m.run_id,
       r.batch_name,
       r.model,
       q.issue_type,
       q.status,
       e.publish_date,
       e.id AS episode_id,
       e.title AS episode_title,
       m.mention_type,
       COALESCE(ent.canonical_name, m.mention_text) AS canonical_name,
       m.mention_text,
       m.confidence,
       m.review_reason,
       m.context_snippet
FROM ai_mention_review_queue q
JOIN ai_entity_mentions m ON m.id = q.mention_id
JOIN ai_extraction_runs r ON r.id = m.run_id
JOIN episodes e ON e.id = m.episode_id
LEFT JOIN ai_entities ent ON ent.id = m.entity_id
WHERE q.status = 'open';

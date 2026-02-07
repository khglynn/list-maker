-- AI Daily Neon review queries

-- 1) Confirm run-level volumes
SELECT r.id,
       r.batch_name,
       r.model,
       r.prompt_version,
       COUNT(DISTINCT m.id) AS mention_rows,
       COUNT(DISTINCT f.id) AS fact_rows,
       COUNT(DISTINCT q.id) AS review_rows
FROM ai_extraction_runs r
LEFT JOIN ai_entity_mentions m ON m.run_id = r.id
LEFT JOIN ai_entity_facts f ON f.run_id = r.id
LEFT JOIN ai_mention_review_queue q ON q.mention_id = m.id
GROUP BY r.id
ORDER BY r.id;

-- 2) Type distribution by run
SELECT m.run_id,
       r.batch_name,
       m.mention_type,
       COUNT(*) AS mention_count
FROM ai_entity_mentions m
JOIN ai_extraction_runs r ON r.id = m.run_id
GROUP BY m.run_id, r.batch_name, m.mention_type
ORDER BY m.run_id, mention_count DESC, m.mention_type;

-- 3) Priority types only by run
SELECT m.run_id,
       r.batch_name,
       m.mention_type,
       COUNT(*) AS mention_count
FROM ai_entity_mentions m
JOIN ai_extraction_runs r ON r.id = m.run_id
WHERE m.mention_type IN ('report','survey','benchmark','account','social_post','blog_post','software_product','model')
GROUP BY m.run_id, r.batch_name, m.mention_type
ORDER BY m.run_id, mention_count DESC, m.mention_type;

-- 4) Sample rows for quick UI sanity check
SELECT m.run_id,
       e.publish_date,
       e.title AS episode_title,
       m.mention_type,
       ent.canonical_name,
       m.mention_text,
       m.platform,
       m.source_url,
       m.needs_review,
       m.review_reason
FROM ai_entity_mentions m
JOIN episodes e ON e.id = m.episode_id
LEFT JOIN ai_entities ent ON ent.id = m.entity_id
WHERE m.run_id IN (1, 3, 4)
ORDER BY m.run_id, e.publish_date DESC, m.id
LIMIT 200;

-- 5) Review queue detail
SELECT m.run_id,
       r.batch_name,
       q.issue_type,
       q.status,
       COUNT(*) AS queue_count
FROM ai_mention_review_queue q
JOIN ai_entity_mentions m ON m.id = q.mention_id
JOIN ai_extraction_runs r ON r.id = m.run_id
GROUP BY m.run_id, r.batch_name, q.issue_type, q.status
ORDER BY m.run_id, queue_count DESC, q.issue_type;

-- AI Daily review queries (lean schema: ai_runs, ai_entities, ai_mentions)

-- 1) Run-level volume + quality
SELECT r.id AS run_id,
       r.batch_name,
       r.model,
       COUNT(DISTINCT m.episode_id) AS episodes,
       COUNT(*) AS mentions,
       COUNT(*) FILTER (WHERE m.needs_review) AS needs_review,
       COUNT(*) FILTER (WHERE m.source_url IS NOT NULL AND BTRIM(m.source_url) <> '') AS with_links
FROM ai_runs r
LEFT JOIN ai_mentions m ON m.run_id = r.id
GROUP BY r.id, r.batch_name, r.model
ORDER BY r.id;

-- 2) Top entities for a run
SELECT m.mention_type,
       e.canonical_name,
       COUNT(*) AS mention_count,
       COUNT(DISTINCT m.episode_id) AS episodes_mentioned
FROM ai_mentions m
JOIN ai_entities e ON e.id = m.entity_id
WHERE m.run_id = 1
GROUP BY m.mention_type, e.canonical_name
ORDER BY mention_count DESC, e.canonical_name
LIMIT 50;

-- 3) "What report was mentioned last week?"
SELECT ep.publish_date,
       ep.title AS episode_title,
       m.canonical_name AS report_name,
       m.context_snippet,
       m.source_url
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'report'
  AND ep.publish_date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY ep.publish_date DESC, m.id;

-- 4) "What surveys were mentioned?"
SELECT ep.publish_date,
       ep.title AS episode_title,
       m.canonical_name AS survey_name,
       m.context_snippet,
       m.source_url
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'survey'
ORDER BY ep.publish_date DESC, m.id;

-- 5) "Top 5 tools in Q4 2025"
SELECT e.canonical_name AS tool_name,
       COUNT(*) AS mention_count
FROM ai_mentions m
JOIN ai_entities e ON e.id = m.entity_id
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'software_product'
  AND ep.publish_date >= DATE '2025-10-01'
  AND ep.publish_date < DATE '2026-01-01'
GROUP BY e.canonical_name
ORDER BY mention_count DESC, e.canonical_name
LIMIT 5;

-- 6) "Which X accounts are mentioned most?"
SELECT e.canonical_name AS account_name,
       COUNT(*) AS mention_count
FROM ai_mentions m
JOIN ai_entities e ON e.id = m.entity_id
WHERE m.mention_type = 'account'
  AND COALESCE(LOWER(m.platform), '') IN ('x', 'twitter')
GROUP BY e.canonical_name
ORDER BY mention_count DESC, e.canonical_name
LIMIT 25;

-- 7) Missing links queue (for manual fix)
SELECT ep.publish_date,
       ep.title AS episode_title,
       m.mention_type,
       m.canonical_name,
       m.context_snippet
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type IN ('account', 'report', 'survey', 'paper', 'blog_post', 'social_post')
  AND (m.source_url IS NULL OR BTRIM(m.source_url) = '')
ORDER BY ep.publish_date DESC, m.id
LIMIT 200;

-- 8) Social post text + link
SELECT ep.publish_date,
       ep.title AS episode_title,
       m.canonical_name,
       m.quoted_text,
       m.source_url
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'social_post'
ORDER BY ep.publish_date DESC, m.id;

-- 9) Benchmarks for LLMs (best effort from tags/facts)
SELECT m.canonical_name,
       COUNT(*) AS mention_count
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'benchmark'
  AND (
    LOWER(COALESCE(m.tags->>'benchmark_domain', '')) = 'llm'
    OR EXISTS (
      SELECT 1
      FROM jsonb_array_elements(m.facts) f
      WHERE LOWER(COALESCE(f->>'fact_key', '')) IN ('benchmark_domain', 'domain')
        AND LOWER(COALESCE(f->>'fact_value', '')) LIKE '%llm%'
    )
  )
GROUP BY m.canonical_name
ORDER BY mention_count DESC, m.canonical_name;

-- 10) Benchmarks for video (best effort from tags/facts)
SELECT m.canonical_name,
       COUNT(*) AS mention_count
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'benchmark'
  AND (
    LOWER(COALESCE(m.tags->>'benchmark_domain', '')) = 'video'
    OR EXISTS (
      SELECT 1
      FROM jsonb_array_elements(m.facts) f
      WHERE LOWER(COALESCE(f->>'fact_key', '')) IN ('benchmark_domain', 'domain')
        AND LOWER(COALESCE(f->>'fact_value', '')) LIKE '%video%'
    )
  )
GROUP BY m.canonical_name
ORDER BY mention_count DESC, m.canonical_name;

-- 11) Image models mentioned in last 6 months (best effort from tags/facts)
SELECT m.canonical_name,
       COUNT(*) AS mention_count,
       MIN(ep.publish_date) AS first_seen,
       MAX(ep.publish_date) AS last_seen
FROM ai_mentions m
JOIN episodes ep ON ep.id = m.episode_id
WHERE m.mention_type = 'model'
  AND ep.publish_date >= CURRENT_DATE - INTERVAL '6 months'
  AND (
    LOWER(COALESCE(m.tags->>'modality', '')) = 'image'
    OR EXISTS (
      SELECT 1
      FROM jsonb_array_elements(m.facts) f
      WHERE LOWER(COALESCE(f->>'fact_key', '')) IN ('modality', 'model_modality')
        AND LOWER(COALESCE(f->>'fact_value', '')) LIKE '%image%'
    )
  )
GROUP BY m.canonical_name
ORDER BY mention_count DESC, m.canonical_name;

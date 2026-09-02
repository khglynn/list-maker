-- 007: drop one of two identical UNIQUE indexes on episodes(url).
--
-- pg_indexes showed episodes_url_key AND episodes_url_unique, both unique btree on
-- (url) — leftover from a past migration, doubling index maintenance on every episode
-- write for nothing. Every ON CONFLICT (url) clause matches by column, not index name,
-- so dropping either is safe. Kevin's per-op OK 2026-09-01 (decision 10); DDL is run by
-- Kevin (the Neon MCP guard and the auto-mode classifier both block DROP on purpose).
DROP INDEX IF EXISTS episodes_url_key;

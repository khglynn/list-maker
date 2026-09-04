# Reader note — `http-paths`

*Sonnet reader, 2026-09-04. Scope: the parent plan's "`save_item.py` / `build_pull_queue.run_build` / `feed_check` HTTP paths where cheap" line. Verified by the Opus synthesis; corrections marked **Synthesis check**.*

## Summary

`feed_check.py` is a false target — it already has full, hermetic coverage (`tests/test_feed_check.py`, 270 lines, from PR #42 plus a same-day follow-up), covering both date and identity readers plus the drift guard against the importer. `build_pull_queue.py` no longer exists.

The real zero-coverage surface is `save_item.py` (197 lines) and `save_episode.py` (380 lines). `save_item.py` has three cleanly pure decision functions (`resolve_show`, `domain_to_show`, `is_pdf`) plus a thin DB boundary and an orchestrator that composes mockable collaborators. `save_episode.py` is the richer and riskier file: `taddy_find_episode`'s fuzzy threshold, `taddy_transcript_text`'s length gate, `parse_og`'s regex, and — the most important single function in this seam — `upsert_oneoff`, which enforces "never downgrade a full transcript to an excerpt" but only in one of its two write paths.

All of it is fakeable with the same monkeypatch-module-attribute + fake-cursor pattern already used in `tests/test_run_new_episodes.py` and `tests/test_load_entity_batch.py`. **No live tokens are needed anywhere in this seam.**

## Functions

| Function | File:line | Pure | What to test |
|---|---|---|---|
| `resolve_show` | `save_item.py:64` | yes | Known blog domain → its slug; `www.` stripped; unregistered domain → `saved-articles`; host case-insensitive |
| `domain_to_show` | `save_item.py:53` | yes | Only `medium == "blog"` shows with a `fallback_website_url` appear; no two shows collide on a host (drift guard, same spirit as `test_show_config.py`) |
| `is_pdf` | `save_item.py:69` | yes | `.pdf` suffix any case; `.pdf` followed by a query string; a non-pdf URL; a path that merely *contains* `pdf` must be False — this gates a silent full-ingest skip |
| `episode_has_mentions` | `save_item.py:96` | no | Fake cursor: a row → True, `None` → False; SQL scoped to the given episode id |
| `save_pdf` | `save_item.py:73` | no | Monkeypatch `httpx.stream` (context manager yielding a fake response with `iter_bytes`), `tmp_path` as the docs dir. `SystemExit` when the parent dir doesn't exist; written bytes match the streamed chunks |
| `sync_blog_mirror` | `save_item.py:102` | no | Monkeypatch `subprocess.run` → True on returncode 0, False otherwise, and it invokes `sync_transcripts_notion.py` with `--target blog-posts` |
| `save_url` | `save_item.py:111` | no | With every collaborator monkeypatched: the PDF path never opens a DB connection; `skip_extract=True` never calls extraction; already-extracted skips extraction without erroring; `sync=False` returns before `sync_curated`; a failed extraction still syncs and still returns False |
| `sync_curated` | `save_item.py:153` | no | `sync_blog_mirror()` always runs (it is the left operand of `and`, so a failing Notion sync cannot short-circuit it); the return is the AND of both |
| `taddy_find_episode` | `save_episode.py:64` | yes | Below `TADDY_TITLE_MIN_RATIO = 0.80` is never selected even on a perfect show match; the `0.7 * title + 0.3 * show` score picks the better of two candidates that both clear 0.80; an empty show name defaults the show ratio to 0.5; nothing clears the gate → `None` |
| `taddy_transcript_text` | `save_episode.py:90` | yes | Text below `MIN_FULL_TRANSCRIPT_CHARS = 1000` → `None` even if non-empty; paragraphs join with a blank line; the exact boundary and one under |
| `try_taddy_full` | `save_episode.py:96` | no | A raising lookup returns `(None, None)` and never propagates; a hit with no transcript returns `(hit, None)`. Confirms a Taddy hiccup can never abort the caller's fallback |
| `parse_og` | `save_episode.py:111` | yes | Both attribute orderings; missing tag → `""`; whitespace stripped |
| `scrape_link_meta` | `save_episode.py:117` | no | With `FIRECRAWL_API_KEY` unset (forcing the raw httpx fallback) and `httpx.get` monkeypatched to frozen castro.fm-shaped HTML: the `(1h51m)` duration suffix is stripped; a colon in the title produces both the `first` and `alt` candidates; HTML entities are unescaped |
| `upsert_oneoff` | `save_episode.py:157` | no | **The dedupe / never-downgrade contract.** (a) no title match → INSERT path, `created=True`; (b) an existing row and a non-`taddy_transcript` source → no UPDATE issued; (c) an existing excerpt + `taddy_transcript` → upgraded; (d) an existing `taddy_transcript` + a clip → the guard holds; (e) the second write path overwrites `transcript_text`/`source_type` with no check on the existing value — pin it and flag it |
| `page_id_for` | `save_episode.py:215` | no | Fake cursor: row → the page id; `None` → `None` |
| `sync_saved_pages` | `save_episode.py:207` | no | Monkeypatch `subprocess.run`; assert `--target transcripts --shows saved-episodes` |

## The boundary

Two shapes, both with an established idiom in `tests/`:

1. **DB** — a `_FakeCursor` recording `(sql, params)` with settable `fetchone`/`fetchall`, wrapped in a `_FakeConn` that flips a `committed` flag: the exact pattern in `tests/test_load_entity_batch.py` and `tests/test_run_new_episodes.py`. `episode_has_mentions`, `page_id_for` and `upsert_oneoff` all take `conn` as a parameter, so no monkeypatching is needed — pass the fake in.
2. **Module-level collaborators** — `get_db_connection`, `ingest_url`, `step_entity_extraction`, `step_notion_sync`, `taddy_query`, `get_episode_transcript`, `subprocess.run`, `httpx.get`/`httpx.stream` — faked with `monkeypatch.setattr(save_item, "name", fake)`, the same idiom `test_run_new_episodes.py` uses.

Nothing needs a live token. `scrape_link_meta`'s raw-httpx fallback is reached deterministically by leaving `FIRECRAWL_API_KEY` unset, which is CI's default (`test.yml` provisions no secrets on purpose). The only "not cheap" piece is `save_pdf`'s filesystem write, still fakeable with `tmp_path`.

## Production incidents this covers

- **A good transcript silently downgraded to a stub.** `upsert_oneoff` has two write paths and the never-downgrade guard exists in only one. The title-match branch checks `source_type == "taddy_transcript" and row["source_type"] != "taddy_transcript"` before UPDATE-ing. The other path — taken when the title lookup misses but the url already exists — writes unconditionally. A re-run where Taddy previously succeeded but fails this time (rate limit, timeout), and the title has drifted slightly, downgrades a full transcript back to a stub with no error and no distinguishing log line. This is the closest thing in this seam to "a NOT_FOUND overwriting a HIGH-confidence match."
- **The wrong show's transcript attached.** `taddy_find_episode` gates purely on `title_ratio >= 0.80`; the combined score only breaks ties among candidates that already cleared the title bar, and never gates on show match. Two podcasts with a near-identical episode title ("Election Night", "Season Finale") can attach the wrong show's transcript, invisible unless Kevin notices the byline. Worth pinning as a documented tradeoff — and worth asking whether a show-ratio floor should also gate. *(Answered 2026-09-04 07:45 CT: Kevin said yes — PR #53 adds `TADDY_SHOW_MIN_RATIO = 0.60` on a containment-aware show match.)*
- **A silent full skip of ingest.** `save_url`'s `is_pdf()` check runs before any DB write and returns True after only downloading the file — no episode row, no extraction, no Notion sync, and the caller sees success. A URL whose path merely ends in `.pdf` but resolves to HTML disappears with zero error anywhere in the run's output.
- **Re-extraction forever on a legitimately mention-free article.** `episode_has_mentions` only checks for the *existence* of rows, so a real zero-entity outcome is indistinguishable from "never attempted" on every future re-save. Low severity, worth a one-line test so a future reader doesn't mistake it for a bug.

## Corrections to the parent plan

1. **Drop `feed_check` from Phase 5.** `tests/test_feed_check.py` covers `_rss_date`, `_ts_to_date`, `feed_recent_dates`, `taddy_recent_episodes`, `rss_recent_episodes`, `feed_recent_episodes`, the future-date filter, the None-on-unverifiable contract for every failure mode, the identity drift guard against `import_transcripts.episode_url_key`, and the real culture-gabfest `ShowConfig` dispatch. Nothing left to add.
2. **Drop `build_pull_queue.run_build`.** `pipeline/build_pull_queue.py` and `tests/test_build_pull_queue.py` were both deleted in `40a07b2`; `save_item.py`'s docstring records the replacement ("the old `--from-queue` flag retired with the checkbox queue on 2026-09-02"). A brief that goes looking will find a dead import.
3. **Net:** this seam's remaining scope is `save_item.py` and `save_episode.py` only — 16 functions between them.

> **Synthesis check (2026-09-04).** Corrections 1–3 confirmed. One correction *to this note*: the unconditional overwrite is **not** in the `INSERT INTO episodes … ON CONFLICT (url) DO UPDATE` (that statement sets only `title = EXCLUDED.title`, `save_episode.py:186-196`). It is the following statement, `INSERT INTO episode_transcripts … ON CONFLICT (episode_id) DO UPDATE SET transcript_text = EXCLUDED.transcript_text, source_type = EXCLUDED.source_type` (`:198-204`). The conclusion stands; the test goes on that line.

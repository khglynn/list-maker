# Post-Compaction Handoff — list-maker durable build
*Written 2026-06-07, end of a long deep session, by the instance that did the work. For the next instance.*

**You are MID-BUILD, not starting fresh. This rolls up the live STATE and — more importantly — the hard-won WAYS OF WORKING from this session. Read it fully before you touch anything. The #1 failure mode here is doing the minimum / executing the literal next bullet of a stale plan. Refuse that. We went deep for good reasons; understand them.**

## Read order (post-compaction)
1. **This file** — ways of working + the real picture (load-bearing; don't skim).
2. `NOW.md` — live state + the exact next step.
3. `claude-plans/2026-06-06-durable-pipeline-resume.md` — failure-modes + grounding.
4. `claude-plans/2026-06-06-durable-pipeline-rebuild.md` — the full plan (but: **the plan is the floor, not the ceiling** — see below).

---

## WAYS OF WORKING — the hard-won insights (the heart of this handoff)

**1. The plan is the FLOOR of ambition, not the letter to follow.** This session's best work came from *diverging* from the plan when reality demanded it — and that was correct, not a deviation to apologize for. Concretely:
- The plan said "clone the AI Daily DB for Hard Fork" (separate DB per show). Verifying the actual sync output revealed entities are *global* (shared across shows) — a separate-DB-per-show model silently sent shared entities (ChatGPT, Claude, OpenAI) to the wrong DB. We redesigned to **Option A: one shared "Tech Tools & Mentions" DB** (Kevin's call, surfaced with tradeoffs). The plan never anticipated this; the *data* told us.
- The plan didn't predict the importer dedup bug, the Spotify silent-failure, the Gabfest feed being a thin mixed RSS, or the Notion ReadTimeout. We found every one by **understanding the full picture**, not by ticking bullets.
- So: read the plan for intent, then synthesize with what's actually true. Bring new framings. Kevin's direction is *signal*, not spec.

**2. Verify against reality — ALWAYS check the output, never just the code.** This is Kevin's backend-data-quality discipline and it caught every major issue this session:
- The importer "imported 196, skipped 196" looked fine in the log — but querying the DB showed only 3 episodes landed (a dedup collapse). The *log lied*; the DB didn't.
- The Notion sync said "created 115 + updated 119, 0 failed" — looked done. But the numbers didn't add up (234 touched, 346 had pages) → dug in → found the global-entity cross-DB contamination.
- The re-sync "completed (exit 0)" — but ChatGPT's page id was unchanged → it had actually died on a timeout ~200 pages in.
- **Pattern: never declare done from a success message. Query the real DB / fetch the real Notion page / count the real rows. The scary failures are the silent ones that look fine.**

**3. The gate is non-negotiable: `codex:codex-rescue` + `/triple-check` at every meaningful step.** Codex caught real defects this session that would've shipped: the importer's ON-CONFLICT assumption, an unhandled `TimeoutExpired`, the Spotify `refresh_access_token`-returns-None gap, the Option-A re-sync run-order. Fix *everything* it finds (critical, important, AND minor). It catches what you miss.

**4. Surface genuine forks to Kevin; don't grind through them — but don't stall on safe work either.** The Notion shape (shared vs per-show DB) was a real decision about *his* workspace → surfaced it with Option A/B + a recommendation + honest tradeoffs; he chose A. When a re-issued `/loop` prompt contradicted a just-discovered reality (it still said "create the media DB" on an undecided shape), I held the Notion work + said so rather than blindly executing. ("If a request contradicts recent context, ask." ) But everything *not* blocked kept moving.

**5. Durable / self-healing / observable — every fix made the system harder to break.** Idempotent writes, retry+backoff (incl. the ReadTimeout fix that resumes a 1000-page rebuild), fail-fast-on-stale-token instead of a silent 30-min CI hang, staleness alerts, single-source config. A *silent* failure (the Spotify hang; the Notion contamination) is the worst kind — when you find one, root-cause it, harden so it fails *loud + fast*, then continue.

**6. Autonomy + narrate; the loop drives; bank continuously.** Self-paced `/loop` (60s when actively building to keep cache warm, long fallback ~1800s when waiting on a background run). Commit every coherent unit (scope-prefixed, ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`), push, then bank `NOW.md`. Long extractions/syncs run in the background; verify on the completion notification. Destructive DB ops via the Neon MCP are hook-blocked — use psycopg2 for migrations; deletions are Kevin's per-op call.

**7. POST-COMPACTION: do NOT do the minimum.** This is the failure mode the whole kit exists to fight. The value of this session was depth — root-causing, redesigning when reality demanded, hardening. An instance that does the literal next step without re-grounding in the *why* will quietly undo that. Re-ground fully, then continue with the same depth.

---

## STATE — done / in flight / next

**DONE (committed + gated):**
- Workstream A — pipeline hardening (idempotency, retry/backoff, structured logging, staleness alerts, single-source config). 45 tests.
- Importer dedup fix — episodes now dedup on the unique Taddy uuid, not the sometimes-generic websiteUrl (which had collapsed 196 Hard Fork eps onto one row). `episode_url_key`.
- **Hard Fork** — registered (Neon id 48), imported (199 eps + transcripts), extracted (198/199, validation clean — real entities, 0 null/out-of-range). Backfill done.
- **Gabfest** — Megaphone-RSS importer (defusedxml, filters "Culture Gabfest" titles) + full 871-ep archive (2008→2026). Neon id 54. *Finding: the feed is thin prose show-notes, not endorsement lists — a real but limited media feed (flagged to Kevin).*
- **B-trigger code** — `entities.yml` (daily + dispatch, injection-hardened) + `cloudflare-trigger/` Worker + the `sys.executable` CI fix. **Deploy is Kevin's** (trimm account_id + a fine-grained GH PAT as the `GH_PAT` Worker secret; steps in `cloudflare-trigger/README.md`).
- **Spotify silent-failure** — root-caused (CI hung 30 min on a stale token → cancelled → SOP/TAL playlists never synced for ~3 weeks). Fixed: `common.ensure_spotify_token` (fail-fast only when headless via `sys.stdin.isatty()`, so local re-auth still works) + `open_browser=False` in all 3 `get_spotify_client`. Kevin re-authed; the `SPOTIFY_CACHE_JSON` secret is updated → playlists revive on the next run. 69 tests.
- **Option A (Notion)** — shared "Tech Tools & Mentions" DB for AI Daily + Hard Fork; `sync_notion` is group-aware (global-within-group counts + a "Shows" multi-select tag); hard-fork's `notion_database_id` → AI Daily's `982dafa0…` (renamed + Shows property added). Committed `7025b62`; the ReadTimeout retry fix `ce2948a`.

**IN FLIGHT — verify first thing:**
- The shared-Tech-DB **full-reset re-sync** (background task `byl8yqfsk`, retry-hardened). On completion: query a shared entity (ChatGPT = entity 16) — its `notion_page_id` should be NEW and parented to the shared DB `982dafa0…` (NOT the old separate DB), and the page should carry **Shows = both shows**. Expect ~1275 entities synced. If timeouts recurred past the retries, re-run `sync_notion.py --show ai-daily-brief --full-reset`.

**NEXT (same Option A pattern; same depth):**
- **Media build** — ONE shared "Media Recommendations" DB for pchh + culture-gabfest (NOT one each). The media extraction PROFILE in `extract_entities.py` (MEDIA_TYPES + a media prompt: endorsement/creators[]/platform/caveats/release_year; segment-aware PCHH "What's Making Me Happy" + Gabfest endorsements; selected by `extraction_type='media_extraction'`) + `load_entity_batch` VALID types + an additive `entity_type` CHECK migration (psycopg2). Tiny validation batch (PCHH+Gabfest ~5-10 eps) → verify sane media entities → SCOPED backfill (recent only; surface cost — Gabfest's 871 eps × thin notes isn't worth a full backfill).
- **Before the media DB exists:** scope `clear_all_notion_ids` to the group's show_ids (a tech full-reset's global clear would otherwise wipe media `notion_page_id`s). Codex flagged this.
- **AI Daily catch-up** — `run_new_episodes.py --shows ai-daily-brief` (~19 days stale; also re-confirms its pages after the shared-DB rebuild).
- **E verify** — each show newest-ep → correct destination; the SOP/TAL live-playlist matched-vs-live check (now that re-auth is done): SOP 3,542 / TAL 778 matched tracks waiting in Neon.

**NEEDS KEVIN (record + move on, don't block):**
- Cloudflare Worker **deploy** (trimm account_id + GH PAT).
- Archive the now-orphaned separate **Hard Fork Notion DB `3780501ef…`** (~234 pages — superseded by Option A).
- **ep-3049 delete** cleanup (1 Hard Fork episode with a mismatched transcript from the original importer bug — a destructive op, his per-op OK).

---

*This handoff embodies hg-save-it (durable instructions for Claude — reasoning over rules) and hg-project-management (useful density, single source of truth, session continuity). NOW.md is the live state; the resume doc is the grounding; this is the way-of-working memory. Keep all three true.*

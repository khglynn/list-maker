# Curation runbook — blogs, one-off saves, research runs

*Written 2026-06-11. How items that aren't podcast episodes get into list-maker.*

## The weekly loop (runs itself)

Every Monday (dispatched by the Cloudflare Worker's daily cron — see `cloudflare-trigger/worker.js`), `blogs.yml`:

1. **Discovers** candidate posts from the mentions DB — URLs the podcasts actually cited (registered blog domains + any `blog_post`-typed mention with a source URL), minus anything already ingested or already queued.
2. **Enriches** each new candidate via Firecrawl: word count + **Links Out** (outbound-link density — the pull signal: posts citing many resources improve the mentions DB most). Capped at 25 new rows per run; the overflow logs and waits.
3. **Upserts** them as rows in the **Blog Pull Queue** Notion DB.
4. **Ingests** every row Kevin has checked (`Pull` ☑ + `Status=candidate`) via `save_item`, then marks it `pulled` (or `failed`). PDFs are marked `pdf-report` and never auto-ingested.

**Kevin's only job:** open the Blog Pull Queue in Notion whenever, sort by Links Out, check the boxes worth pulling. The checks are also ground truth — once enough marks exist to validate a threshold rule, auto-pull can graduate from them. Don't automate the choice before then.

## One-off saves (any article, any time)

```
cd pipeline && ./venv/bin/python save_item.py --url <article-url>
```

- Show resolves by domain (openai.com → openai-blog, anthropic.com → anthropic-blog, anything else → saved-articles).
- Flow: scrape → store (episode + full text) → extract mentions for just that episode → entity sync (shared Tech DB) → Blog Posts full-text mirror. Re-saving the same URL refreshes the text without duplicating or re-extracting.
- A `.pdf` URL downloads into the Obsidian research folder instead (`RESEARCH_DOCS_DIR` overrides the default vault path) — reports live as files there, linked from Obsidian, not as DB rows.
- One-off **podcast episode** saves are deliberately not built yet (no concrete case has needed one; narrated articles save better via `--url`). If it comes up: the backlog item is a Taddy episode lookup.

## Research runs (local-only, on demand)

```
cd pipeline && ./venv/bin/python scrapers/research/import_research.py   # ingest new/changed docs
./venv/bin/python run_new_episodes.py --shows agentic-research --backfill  # extract them
```

- Walks the Agentic Research folder in the Obsidian vault; keys each doc by a stable `obsidian://` URI (clickable; vault-relative, so machine-independent). Infrastructure files (CLAUDE.md, backlogs, prompt bodies) are filtered out — only research outputs extract.
- Re-running is the maintenance method: idempotent by key, refreshed text upserts in place. Run it after a batch of new research lands.
- Never in CI: the vault only exists on Kevin's machines, and the show is excluded from all scheduled paths and health cadence checks.

## Podcast clip highlights (local-only, on demand)

```
cd pipeline && ./venv/bin/python highlight_clips.py --dry-run   # match report
./venv/bin/python highlight_clips.py                            # process new clips
```

Drop Castro clip exports (.MOV) into `~/Downloads/Podcast Clips/` and run. For
episodes in the DB, each clip becomes a highlight callout at the top of the
episode's Notion transcript page: audio player + the clip's quote + an anchor
link jumping to that spot in the transcript. Matching uses the export's embedded
metadata (show + episode title); Whisper transcribes only the clip to find the
in/out points.

**Where the files live:** the audio's permanent home is Notion (uploaded to the
page). The original .MOVs (video + audio, ~10× larger) are disposable after a
run — archive to NAS or delete, nothing depends on them. Extracted m4as +
the manifest live in `pipeline/_cache/podcast-clips/`.

Clips from shows we don't carry go through `save_episode.py` instead: it creates
"Saved Episodes" pages (show 64) in the Transcripts DB under their REAL show
names — full Taddy transcript when search finds one, honest clip-excerpt or
show-notes page otherwise — and lays the same highlights on top. The **Clips**
column on the Transcripts DB marks which episodes carry highlights.

## Where things land

| Artifact | Notion DB | Notes |
|---|---|---|
| Entities/mentions (all curated sources) | Tech Tools & Mentions (`982dafa0…`) | Curated mentions qualify at 1; Shows multi-select shows the source |
| Blog/article full texts | Blog Posts (`37c0501e…93f5`) | URL + Links Out properties; chunked full text |
| Pull candidates | Blog Pull Queue (`37c0501e…1f53`) | Kevin's checkbox = the pull decision |
| Research-run full texts | — (stay in Obsidian) | Only their mentions sync |
| PDFs/reports | — (Obsidian research folder) | Files, linked from Obsidian |

## Known-deferred (documented, not silent)

- Edited-post re-extraction: a refreshed text never re-extracts (the orchestrator skips episodes that already have mentions). Fix when it bites: content-hash gate.
- Auto-pull threshold: waits for enough checkbox ground truth.
- Cross-URL duplicate posts (same content, two paths — e.g. the DALL·E 3 pair) aren't merged; check one, skip the other.
- a16z + paid substacks: out of scope (paywalled).

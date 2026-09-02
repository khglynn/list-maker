# Curation runbook — blogs, one-off saves, research runs

*Written 2026-06-11. How items that aren't podcast episodes get into list-maker.*

## The weekly loop (runs itself, and decides for itself)

*Rewritten 2026-09-02, when the checkbox queue became the judged intake.*

Every Monday (dispatched by the Cloudflare Worker's daily cron — see `cloudflare-trigger/worker.js`), `blogs.yml` runs `pipeline/run_intake.py`:

1. **Discovers** from four sources: OpenAI's RSS feed, Anthropic's `/news` and `/engineering` index pages (scraped once each), URLs the podcasts pointed at, and — for citations that arrived without a link — a search that resolves the URL (`scrapers/intake/links.py`). Everything lands in the `intake_candidates` table in Neon, one row per canonical URL, deduped.

   A podcast URL is filed by what the mention *is*, not by how it was found. A cited **document** (report, paper, survey, blog post) is `podcast-cited` and is exempt from the staleness skip — an old report a show just cited is still worth reading. Any other URL a mention carried (a product page a host name-dropped, e.g. `openai.com/dall-e-2`) is `podcast-linked`, and staleness applies. *Split 2026-09-02, after nine of twelve first-run misses turned out to be archival product pages.*
2. **Pre-checks, deterministically.** Seven structural reasons, cheapest first, and the models never see any of them — a script decided, and the row says which rule did it. Four need no network at all, so they run before a Firecrawl credit is spent: already an episode → `duplicate`; a `.pdf` → `held` (reports live as files in the Obsidian research folder); an OpenAI Academy course → `academy`; a hiring announcement → `people-news`; published over 400 days ago → `stale` (never applied to a podcast citation — a show citing an old report still counts). Two need the scrape: it failed → `dead`; under 200 words → `thin`.
3. **Judges** what survives: two cheap models (`google/gemini-3.7-flash` and `openai/gpt-5.6-luna`, through OpenRouter) read the post against `docs/intake-rubric.md` and answer `save` or `skip` with a confidence and a one-line reason. Agree → that verdict. Disagree → **save, marked disputed** — the expensive mistake is missing the report Kevin needed, and a disputed save is visible in Notion.
4. **Logs everything** to the **📥 Blog Intake** Notion DB (the old Blog Pull Queue, repurposed in place — same rows, same URLs): verdict, confidence, reason, which two models, disputed, status.
5. **Posts one Slack line**, every week, including a week where nothing happened: judged N · would save K (named) · skipped (with reasons) · disputed · held · failed.

**Shadow mode is on** (`AUTO_INGEST = False` in `run_intake.py`). Verdicts are recorded but nothing is ingested automatically — a `save` sits at status `judged`, meaning "we would have saved this". PR 3 flips it once the eval in `evals/intake/` clears recall ≥ 0.9 on `save` and precision ≥ 0.7, and one shadow week reads right.

**Kevin's only job — and it is optional:** nothing waits on him. If the judge skipped something he wants, tick **Pull anyway** on that row; the next run ingests it and records `override_by = kevin`. Ticking nothing is a valid week.

Running it by hand:

```
cd pipeline
./venv/bin/python run_intake.py --dry-run              # the plan: no writes, no model calls, no per-post scrapes
./venv/bin/python run_intake.py                        # the full weekly pass
./venv/bin/python run_intake.py --sources podcast-cited  # just the citations (fine to run daily)
./venv/bin/python run_intake.py --overrides-only       # just ingest the rows you ticked
```

*(What this replaced: `build_pull_queue.py` discovered candidates and waited for a checkbox. Between 2026-06-21 and 08-31, eleven consecutive runs found nothing new, said nothing, and left 31 candidates un-triaged — including "How people are using ChatGPT", the post the whole idea existed to catch. The lesson isn't "a better nudge"; it's that a pipeline whose last step is a human's attention will stall at that step.)*

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
| Judged candidates | Blog Intake (`37c0501e…1f53`) | Every candidate + verdict + reason; "Pull anyway" is the override |
| Research-run full texts | — (stay in Obsidian) | Only their mentions sync |
| PDFs/reports | — (Obsidian research folder) | Files, linked from Obsidian |

## Known-deferred (documented, not silent)

- Edited-post re-extraction: a refreshed text never re-extracts (the orchestrator skips episodes that already have mentions). Fix when it bites: content-hash gate.
- Auto-ingest: shadow mode until `evals/intake` clears recall ≥ 0.9 / precision ≥ 0.7 (PR 3 flips `AUTO_INGEST`).
- The Notion DB's legacy Status options (`candidate`, `pulled`, `pdf-report`) are kept until no row still uses one — removing a select option blanks it on every row that carries it.
- Cross-URL duplicate posts (same content, two paths — e.g. the DALL·E 3 pair) aren't merged; check one, skip the other.
- a16z + paid substacks: out of scope (paywalled).

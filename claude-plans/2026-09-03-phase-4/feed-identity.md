# Phase 4, Item 1 — Feed check by episode identity: design map

Scope: `check_import_caught_up` in `pipeline/data_health.py` only. Read-only investigation;
no edits made. Db reads used `pipeline.common.get_db_connection` against live Neon
(read-only `SELECT`s) to ground-truth `episodes.url`/`episodes.raw_content` per show —
facts below are load-bearing and were queried, not assumed.

## 1. What exists today

**`pipeline/data_health.py:99-114` `split_missing_feed_dates`** — pure function. Takes
`feed: Iterable[date]`, `db_latest: date | None`, `grace_days`. A feed date is "missing"
if `db_latest is None or d > db_latest`; missing dates older than `today - grace_days`
are `overdue`, the rest `pending`. **This is the whole bug**: it only ever compares
against `MAX(publish_date)`, never against which *episodes* (by identity) we actually
hold.

**`pipeline/data_health.py:497-563` `check_import_caught_up`** — for each non-curated
show: bulk-fetches `db_latest` per slug (`SELECT s.slug, MAX(e.publish_date)::date ...
GROUP BY s.slug`, lines 516-523), calls `feed_recent_dates(cfg)` (line 537, dates only,
newest first), then `split_missing_feed_dates(feed, latest, cfg.feed_grace_days)` (line
544). `overdue` → FAIL ("BEHIND"), `pending` → informational detail, neither → "caught
up". Curated slugs (`curated_show_slugs()`) are skipped outright (line 532) — no feed to
compare against, and that skip is correct and untouched by this design.

**`pipeline/feed_check.py`** — the second source. Contract (module docstring, lines
13-18): returns a **non-empty list of dates, newest first, all ≤ today** on success, or
**`None`** for every "couldn't verify" case (unreachable/HTTP error/GraphQL-200-with-
`errors`/malformed/empty) — callers must render `None` as unverified, never green.
- `taddy_recent_dates(series_uuid, limit=15)` (54-82): POSTs a GraphQL query that already
  requests `uuid` and `datePublished` per episode (line 61-62: `getLatestPodcastEpisodes
  { uuid datePublished }`) — **the uuid is fetched today and immediately thrown away**
  at line 81 (`dates = [d for d in (_ts_to_date(e.get("datePublished")) for e in eps) if
  d]`). This is the one-line reason the fix is cheap: no new API call, no new field to
  request — just stop discarding what's already in the response.
- `rss_recent_dates(feed_url, title_prefix="", limit=15)` (85-105): parses the Megaphone
  RSS feed with `defusedxml`, filters items by `title.startswith(title_prefix)`, extracts
  only `pubDate` per item. `guid`/`enclosure`/`link` are read by nobody here — but they
  are read by `pipeline/scrapers/gabfest/import_gabfest.py`'s `parse_feed()` (lines
  86-110), which already extracts exactly those fields per item into a dict shaped for
  `episode_url()` (below).
- `feed_recent_dates(cfg, limit=15)` (108-127): picks Taddy vs RSS by `cfg.taddy_uuid` /
  `"megaphone" in cfg.fallback_website_url`, drops future-dated entries, re-sorts newest
  first, collapses an empty result to `None`.

**`pipeline/show_config.py`** — `ShowConfig.feed_grace_days` (per-show int, default 2;
sop=4, tal=2, ai-daily-brief/pchh/hard-fork=2 via default, culture-gabfest=2 via
default). `curated_show_slugs()` = shows with `medium != "podcast"`. Nothing about
episode identity lives here today; this design adds two small pure helpers here (§3).

**How `episodes.url` is actually populated — this is the identity key, already unique
in the schema** (`episodes_url_key`, the constraint sql/007 kept). Read `pipeline/
scrapers/taddy/import_transcripts.py:275-291` `episode_url_key(episode)`:
```python
def episode_url_key(episode: dict[str, Any]) -> str:
    uuid = episode.get("uuid")
    if uuid:
        return f"https://api.taddy.org/podcast-episode/{uuid}"
    return (episode.get("websiteUrl") or episode.get("audioUrl")
            or episode.get("guid") or "unknown-episode")
```
Every Taddy-sourced episode (all 5 Taddy shows) gets `episodes.url =
"https://api.taddy.org/podcast-episode/{uuid}"`. The upsert (`upsert_episode`,
lines 294-399) first tries to reuse an existing row by `show_id + lower(title) +
publish_date` (320-338) — if found, it **updates that row's `publish_date` in place and
never touches `url`** (COALESCE, line 376: `publish_date = COALESCE(EXCLUDED.publish_date,
episodes.publish_date)` — new value wins when present). If not found, it inserts with
`ON CONFLICT (url) DO UPDATE` — same COALESCE on `publish_date`. **This is exactly why a
re-date doesn't create a new row or change the identity**: the url is uuid-derived and
stable; only the date column moves.

Gabfest (`pipeline/scrapers/gabfest/import_gabfest.py:71-83`) `episode_url(item)`:
`guid` → `enclosure_url` → `link` → a synthetic `gabfest:{title}:{pubdate_raw}` fallback,
explicitly *not* preferring `<link>` (comment: that caused the Hard Fork url-collapse
bug). `upsert_episode` (118-152) is `ON CONFLICT (url) DO UPDATE`, same COALESCE-on-date
shape.

**Ground-truthed against live Neon** (read-only query, 2026-09-03):

| slug | episodes | url = Taddy uuid pattern | other url scheme |
|---|---:|---:|---:|
| sop | 716 | 2 | 714 (switchedonpop.com/episodes/...) |
| tal | 904 | 15 | 889 (thisamericanlife.org/...) |
| ai-daily-brief | 1076 | 96 | 980 (podcasters.spotify.com/pod/show/nlw/...) |
| pchh | 419 | 62 | 357 (npr.org/...) |
| hard-fork | 212 | 209 | 3 (nytimes.com/...) |
| culture-gabfest | 931 | 0 | 931 (Megaphone RSS guid, e.g. `6a874b65a103c59e028eb8d5`) |

Sampling the newest rows per show confirms the split is **chronological, not random**:
every show's *most recent* rows (the last several weeks) use the Taddy-uuid url; the
non-Taddy urls are all pre-migration legacy rows (ai-daily-brief's newest non-Taddy row
is 2026-05-18; hard-fork's newest is 2022-10-14). `feed_recent_dates`/the new identity
equivalent only ever asks for the **most recent 15** episodes from the live feed, so
there is no overlap risk between the two url schemes in practice — the identity compare
only ever has to match against the Taddy-shaped rows.

## 2. Identity key per show, and the no-feed case

| slug | identity key | source of the key on the feed side | already in `episodes.url`? |
|---|---|---|---|
| sop, tal, ai-daily-brief, pchh, hard-fork | Taddy episode `uuid`, as the url `https://api.taddy.org/podcast-episode/{uuid}` | `getLatestPodcastEpisodes.uuid` — already fetched, currently discarded | yes (confirmed above) |
| culture-gabfest | RSS `<guid>` (→ enclosure → link fallback, same rule as the importer) | Megaphone RSS `<item><guid>` | yes (confirmed above) |
| openai-blog, anthropic-blog, saved-articles, agentic-research, saved-episodes (all `medium != "podcast"`) | **none — no feed exists** | — | n/a |

For the no-feed group: **no change**. `check_import_caught_up` already special-cases
`curated_show_slugs()` (line 532) and skips them with a "no feed second-source" detail
line before any date/identity comparison happens. That branch is correct as written and
this design does not touch it — an identity key literally cannot be computed for a
source with no upstream feed, and inventing one would just manufacture a permanent
false "UNVERIFIED".

## 3. The design — smallest correct change

**Principle:** stop asking "is the feed's date newer than our max date?" and start
asking "is the feed's episode (by identity) in the set of urls we hold for this show?".
Grace-window/overdue-vs-pending grading still applies, keyed off each *missing*
episode's own publish date — nothing about the grace-window contract changes, only what
counts as "missing."

### 3a. `pipeline/show_config.py` — one new constant + one new pure function

```python
TADDY_EPISODE_URL_PREFIX = "https://api.taddy.org/podcast-episode/"

def taddy_episode_url(uuid: str) -> str:
    """The exact url the Taddy importer writes to episodes.url for this episode uuid
    (see scrapers/taddy/import_transcripts.episode_url_key). Single source so any
    other reader of a Taddy uuid — today just feed_check's identity compare — can
    never drift from what the importer actually persists."""
    return f"{TADDY_EPISODE_URL_PREFIX}{uuid}"
```
Then change `episode_url_key()` in `import_transcripts.py` to `return
taddy_episode_url(uuid)` in its `if uuid:` branch, instead of inlining the f-string —
this *removes* the one place the format string is hardcoded today, rather than adding a
second copy of it. `show_config.py` is already the proven-safe place for this: both
`import_transcripts.py` (flat `from show_config import SHOWS`) and `pipeline/tests`
(`from pipeline.show_config import ...`) already import it in both styles without
incident (`tests/test_show_config.py` does exactly this dual-import today), so this is
not a new import-path risk.

*(Gabfest needs no equivalent constant — its identity function, `episode_url()`, is
reused directly from `import_gabfest.py`, see 3b. There's nothing to centralize: it's
already one function, already imported hermetically by `tests/test_import_gabfest.py`
today.)*

### 3b. `pipeline/feed_check.py` — extend, don't replace

Add a self-import guard at the top (mirrors `data_health.py:20` exactly, needed because
`feed_check.py` is dual-imported both as `from feed_check import ...` — relative, when
run via `cd pipeline && python data_health.py`, per CLAUDE.md's documented command
pattern — and as `from pipeline import feed_check` in tests; without this insert, a
flat `import show_config` inside feed_check.py would only work by accident of import
order):
```python
import sys
from pathlib import Path
_PIPELINE_DIR = Path(__file__).resolve().parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from show_config import taddy_episode_url  # noqa: E402
from scrapers.gabfest.import_gabfest import parse_feed as _gabfest_parse_feed  # noqa: E402
from scrapers.gabfest.import_gabfest import episode_url as _gabfest_episode_url  # noqa: E402
```
(`scrapers/__init__.py` exists; `scrapers/gabfest` has no `__init__.py` but is importable
as a PEP 420 namespace package — the exact pattern `tests/test_show_config.py` and
`tests/test_import_gabfest.py` already rely on.)

Add, **alongside** the existing functions (don't touch `taddy_recent_dates`,
`rss_recent_dates`, or `feed_recent_dates` — see §5 on why leaving them alone matters):

```python
def taddy_recent_episodes(series_uuid: str, limit: int = 15) -> Optional[list[tuple[str, date]]]:
    """Like taddy_recent_dates, but keeps each episode's identity — the exact url
    episode_url_key()/taddy_episode_url() would write to episodes.url — paired with
    its date. Same request + same None contract as taddy_recent_dates; only the
    mapping at the end differs. uuid is always present in this query's response
    (Taddy requires it), so nothing here silently drops episodes the date-only path
    would have kept."""
    # identical body to taddy_recent_dates() through the `payload.get("errors")` /
    # `getLatestPodcastEpisodes` list checks, then:
    episodes = [
        (taddy_episode_url(e["uuid"]), d)
        for e in eps
        if e.get("uuid") and (d := _ts_to_date(e.get("datePublished")))
    ]
    return sorted(episodes, key=lambda ep: ep[1], reverse=True)


def rss_recent_episodes(feed_url: str, title_prefix: str = "", limit: int = 15) -> Optional[list[tuple[str, date]]]:
    """Like rss_recent_dates, but pairs each item's identity (import_gabfest.episode_url's
    guid > enclosure > link > synthetic fallback — reused directly, not re-implemented)
    with its date. Reuses import_gabfest.parse_feed for the actual XML→dict step so this
    can't drift from what the importer stores; keeps its own try/except (parse_feed
    itself doesn't validate the root tag, but an unparseable/wrong-shaped feed still
    ends up empty -> None here, same end state as today's explicit root.tag guard)."""
    try:
        resp = requests.get(feed_url, timeout=TIMEOUT, headers={"User-Agent": "list-maker-health"})
        resp.raise_for_status()
        items = _gabfest_parse_feed(resp.content)
    except Exception:  # noqa: BLE001
        return None
    episodes: list[tuple[str, date]] = []
    for it in items:
        if title_prefix and not it["title"].startswith(title_prefix):
            continue
        d = it.get("publish_date")  # parse_feed already ran parse_pubdate
        if d:
            episodes.append((_gabfest_episode_url(it), d))
    return sorted(episodes, key=lambda ep: ep[1], reverse=True)[:limit]


def feed_recent_episodes(cfg, limit: int = 15) -> Optional[list[tuple[str, date]]]:
    """(identity, publish_date) pairs from the show's real feed — the identity-aware
    second source for check_import_caught_up. Same None/empty/future-date contract as
    feed_recent_dates (module docstring), just richer per-item."""
    if getattr(cfg, "taddy_uuid", None):
        episodes = taddy_recent_episodes(cfg.taddy_uuid, limit)
    elif (url := getattr(cfg, "fallback_website_url", None)) and "megaphone" in url:
        episodes = rss_recent_episodes(url, title_prefix="Culture Gabfest", limit=limit)
    else:
        return None
    if episodes is None:
        return None
    today = datetime.now(timezone.utc).date()
    episodes = sorted(
        ((uid, d) for uid, d in episodes if d <= today), key=lambda ep: ep[1], reverse=True
    )
    return episodes or None
```

`taddy_recent_dates` / `rss_recent_dates` / `feed_recent_dates` are **left as-is,
byte-for-byte** — still used by `pulse_report.py` (its own `feed_dates` display +
`split_missing_feed_dates` call, lines 39-41/98/134) and still exercised by the existing
`tests/test_feed_check.py` monkeypatches. New functions are additive, not derived from
the old ones (a derive-from-old design was considered and rejected — see §5).

### 3c. `pipeline/data_health.py` — the actual check

New pure helper next to `split_missing_feed_dates` (same file, same `_today()` pattern
so `monkeypatch.setattr(dh, "_today", ...)` keeps working):

```python
def split_missing_feed_episodes(
    feed: Iterable[tuple[str, date]], held: set[str], grace_days: int, today: date | None = None
) -> tuple[list[tuple[str, date]], list[tuple[str, date]]]:
    """Identity-based analogue of split_missing_feed_dates. A feed episode is MISSING
    when its identity isn't in `held` — regardless of what date it now carries, which
    is what makes a re-dated episode a non-event (the url is stable; only publish_date
    moves, see import_transcripts.upsert_episode's ON CONFLICT). Grading into
    overdue/pending still uses each missing episode's OWN date vs the grace cutoff, so
    the grace-window contract from split_missing_feed_dates is unchanged. Unlike the
    date-only version, this also catches a hole in the MIDDLE of the feed's recent
    list — a missing episode older than the newest held one used to be invisible to
    MAX(publish_date); here it's just another set-difference entry.
    """
    today = today or _today()
    cutoff = today - timedelta(days=grace_days)
    missing = [(uid, d) for uid, d in feed if uid not in held]
    return (
        [(uid, d) for uid, d in missing if d <= cutoff],
        [(uid, d) for uid, d in missing if d > cutoff],
    )
```

New bulk query (replaces the `MAX(publish_date)` query at lines 516-523 — one round
trip either way, same shape: fetch everything up front, loop per show after):
```python
def _held_episode_urls_by_show(conn) -> dict[int, set[str]]:
    rows = _rows(conn, "SELECT show_id, url FROM episodes WHERE url IS NOT NULL;")
    by_show: dict[int, set[str]] = {}
    for r in rows:
        by_show.setdefault(r["show_id"], set()).add(r["url"])
    return by_show
```
(~4,300 rows fleet-wide today — one cheap query, no per-show round trips.)

Rewrite `check_import_caught_up` (lines 497-563), same signature, same
skip-curated / skip-when-unwanted / UNVERIFIED-on-`None` structure, only the compare
step changes:
```python
def check_import_caught_up(conn, slugs: Iterable[str] | None = None) -> CheckResult:
    wanted = set(slugs) if slugs is not None else None
    held_by_show = _held_episode_urls_by_show(conn)
    failures: list[str] = []
    warnings: list[str] = []
    details: list[str] = []
    curated = curated_show_slugs()
    for slug, cfg in SHOWS.items():
        if wanted is not None and slug not in wanted:
            continue
        if slug in curated:
            details.append(f"{slug}: curated source — no feed second-source (skipped)")
            continue
        feed = feed_recent_episodes(cfg)
        if not feed:
            warnings.append(f"{slug}: feed UNVERIFIED — second source unreachable")
            continue
        held = held_by_show.get(cfg.show_id, set())
        overdue, pending = split_missing_feed_episodes(feed, held, cfg.feed_grace_days)
        if overdue:
            oldest = min(d for _, d in overdue)
            failures.append(
                f"{slug}: BEHIND {len(overdue)} — {len(overdue)} feed episode(s) not "
                f"held (oldest missing {oldest}), past the {cfg.feed_grace_days}-day "
                f"import window"
            )
        elif pending:
            details.append(
                f"{slug}: caught up — {len(pending)} newer feed episode(s) pending "
                f"inside the {cfg.feed_grace_days}-day import window (feed at {feed[0][1]})"
            )
        else:
            details.append(f"{slug}: caught up ({len(feed)}/{len(feed)} recent feed episodes held)")
    # status/summary block unchanged (lines 557-563)
```
Note the old "(feed at {feed[0]}, we have {latest})" wording is gone because `latest`
(MAX(publish_date)) is no longer computed or load-bearing — under identity comparison it
can be actively misleading (a re-dated episode can *move* what "our latest" means without
changing whether we're actually caught up). If a human-readable "as of" date is still
wanted in the message, it's one more cheap aggregate query (`MAX(publish_date)` per
show) added purely for display — decide at implementation time; it changes no logic.

## 4. Confirms the three required properties

- **Grace window kept**: `split_missing_feed_episodes` takes `grace_days` and grades
  exactly like today — a missing episode newer than the cutoff is `pending`, not a
  failure. `cfg.feed_grace_days` (per-show, sop=4/tal=2/etc.) is untouched.
- **Re-dated episode does NOT fail**: identity (`episodes.url`, uuid-derived) is stable
  across a date correction — only `publish_date` moves (`import_transcripts.py:376`,
  `import_gabfest.py:138`, both `COALESCE(EXCLUDED.publish_date, ...)` on `ON CONFLICT
  (url)`). Membership in `held` never depends on the date value, so a re-date can only
  ever move an episode's *position* in the feed's recent-N ranking (or drop it out of
  the window entirely) — never make it appear "missing" while we hold it.
- **A hole mid-series FAILS**: the old check only asked "is anything newer than our
  max?" — an episode strictly older than the current max was structurally invisible no
  matter how missing it was. The new check asks "is *every* recent feed identity in
  `held`?" — a missing identity sandwiched between two held ones shows up in `missing`
  exactly the same as a missing one at the front. This is the "real prize" the plan
  names.

## 5. Design choices considered and rejected

- **Deriving `feed_recent_dates`/`taddy_recent_dates` from the new `_episodes`
  functions** (single fetch, mapped two ways) was the first draft — rejected because
  `tests/test_feed_check.py:39-49` monkeypatches `feed_check.taddy_recent_dates`
  directly and asserts on `feed_check.feed_recent_dates`'s behavior; rerouting that
  seam through `taddy_recent_episodes` would silently stop exercising those mocks
  (the tests would still pass, green, while testing nothing — worse than a loud
  break). Keeping the old functions untouched and adding new ones costs ~15 lines of
  duplicated Taddy request/parse boilerplate, which is a smaller risk than breaking a
  test's mock target invisibly.
- **Parsing the uuid back out of `episodes.url` with a regex**, rather than building
  the identity string from the feed side and comparing directly to `episodes.url`, was
  considered and rejected: it would need its own hardcoded knowledge of the
  `https://api.taddy.org/podcast-episode/` prefix on the DB-reading side too — same
  amount of coupling, but as a regex instead of a reused function, with no drift test
  possible (nothing to import and assert equal to). Building the identity string via
  `show_config.taddy_episode_url()` and comparing it directly against the `url` column
  is strictly simpler and is provably non-divergent from what the importer writes.
- **Storing a dedicated `episodes.taddy_uuid` / `episodes.identity_key` column** was not
  designed — out of scope for this item (schema changes are decision-10 territory,
  already closed per the plan) and unnecessary: `episodes.url` already *is* the unique
  identity key by construction and by DB constraint (`episodes_url_key`).

## 6. Tests to add (hermetic — mirrors existing patterns exactly)

**`tests/test_feed_check.py`** (extends the existing file's style — `SimpleNamespace`
cfgs, `monkeypatch.setattr(feed_check, ...)`, no network/DB):
1. `test_taddy_recent_episodes_pairs_identity_with_date` — monkeypatch the HTTP layer
   (same technique the existing Taddy tests would need — inspect how
   `test_feed_recent_dates_drops_future_dated_entries_and_sorts_newest_first` mocks
   `taddy_recent_dates` wholesale; for the new function, mock `requests.post` to return
   a fixture payload with 2-3 known uuids/dates) and assert each identity string equals
   `f"https://api.taddy.org/podcast-episode/{uuid}"`, sorted newest first.
2. `test_taddy_identity_matches_importer_url_scheme` (**the drift guard** — this is the
   single test that makes the whole design's correctness non-accidental): `from
   pipeline.scrapers.taddy.import_transcripts import episode_url_key` and `from
   pipeline.show_config import taddy_episode_url`; assert
   `taddy_episode_url("abc-123") == episode_url_key({"uuid": "abc-123"})`. Mirrors the
   existing `tests/test_show_config.py::test_taddy_show_configs_stay_in_sync` pattern
   exactly (same drift-test philosophy, same codebase precedent).
3. `test_rss_recent_episodes_uses_gabfest_identity_scheme` — reuse (or closely mirror)
   `tests/test_import_gabfest.py`'s `SAMPLE_FEED` fixture; monkeypatch `requests.get`
   to return it; assert identities match `import_gabfest.episode_url()` called on the
   same items directly (structural equality, not just a hardcoded string, since the
   function is literally reused).
4. `test_feed_recent_episodes_drops_future_dated_and_sorts` — same shape as the
   existing `test_feed_recent_dates_drops_future_dated_entries_and_sorts_newest_first`,
   for the new function/return type.

**`tests/test_data_health.py`**:
1. `test_split_missing_feed_episodes_ignores_a_redated_episode` — **the named
   regression test for the TAL bug**: `feed = [("ep-X", date(2026, 8, 20))]`,
   `held = {"ep-X"}` (we hold it, but the feed now reports a *different* date than
   whatever we originally stored it under — the point is the date value is irrelevant
   to membership). Assert `split_missing_feed_episodes(feed, held, grace_days=2,
   today=date(2026, 9, 1)) == ([], [])`. Comment referencing the DEVLOG 2026-09-01 TAL
   incident.
2. `test_split_missing_feed_episodes_catches_a_mid_series_hole` — **the named
   regression test for the "real prize"**: `feed = [("ep-A", date(2026,9,1)),
   ("ep-B", date(2026,8,25)), ("ep-C", date(2026,8,18))]`, `held = {"ep-A", "ep-C"}`
   (B — the middle one — is missing; A, the newest/MAX, is held). Assert `overdue ==
   [("ep-B", date(2026,8,25))]` with a `today`/grace combination that puts ep-B past
   the cutoff. Comment noting the old date-only check would report this show
   "caught up" because its MAX(publish_date) episode (ep-A) is held.
3. `test_check_import_caught_up_catches_a_mid_series_hole_end_to_end` — integration
   shaped: `monkeypatch.setattr(dh, "feed_recent_episodes", ...)` returning a 3-episode
   feed with a middle gap; monkeypatch `dh._held_episode_urls_by_show` (or `dh._rows`,
   whichever the implementation lands on) to supply the held set; assert
   `result.status == "fail"` and the gap's identity/date is named in `result.details`.
   This is the plan's Phase 4 acceptance line made concrete: *"a seeded mid-series gap
   fails the feed check."*
4. **Update, don't just add** — these five existing tests call `check_import_caught_up`
   via the `_feed_check` helper (lines 230-238) or inline monkeypatches, and all
   currently target `dh._rows` (for the old `MAX(publish_date)` bulk query) and
   `dh.feed_recent_dates` (date lists). Both seams move. Each needs its monkeypatch
   targets and fixture shapes changed from `date` lists / `{"slug": ..., "db_latest":
   ...}` rows to `(identity, date)` tuple lists / `{"show_id": ..., "url": ...}` rows
   (or whatever the final helper name is) — same assertions on `result.status` /
   `result.details` substrings should still hold once the fixtures carry identities:
   - `test_feed_check_can_scope_to_one_show` (95-116)
   - `test_feed_check_unscoped_still_covers_every_show` (118-130)
   - `test_feed_check_tolerates_a_fresh_episode_inside_the_import_window` (241-251)
   - `test_feed_check_fails_once_a_missing_episode_is_older_than_the_grace` (254-265)
   - `test_feed_grace_is_per_show` (267-281)
   - the `_feed_check` helper itself (230-238)
   `test_split_missing_feed_dates_partitions_by_grace` (283-294) stays exactly as-is —
   it tests the *old*, still-live function (`pulse_report.py` still uses it), not the
   new one.

## 7. Risks

- **Test-seam breakage is the main risk, not the logic.** Six existing tests in
  `tests/test_data_health.py` hard-monkeypatch `dh._rows` and `dh.feed_recent_dates`
  as `check_import_caught_up`'s two dependencies. Missing even one during
  implementation leaves it silently calling the real (unmocked) `_rows`/network path in
  a "hermetic" test — CI would either hang/error loudly (good) or, worse, get a
  monkeypatched double that happens to still satisfy the old call shape while the new
  code path goes untested (bad, silent). Enumerated all six above precisely so none are
  missed.
- **Legacy non-Taddy urls are a latent false-negative, not exercised today.** If a
  show's real feed ever returned something outside its most-recent ~15 episodes (limit
  is a constant, not configurable per call site in `check_import_caught_up`), and that
  older episode's DB row still carries a pre-migration url (website/RSS-of-record
  rather than the Taddy uuid), the identity compare would report it as missing even
  though we hold it under a different url. Not a real risk *today* (confirmed above:
  the legacy/Taddy url split is strictly chronological, and `limit=15` never reaches
  back that far for any show's cadence), but worth a one-line comment in the code so a
  future increase to `limit` doesn't quietly reintroduce false positives.
- **`episodes.url IS NULL` rows are invisible to the held-set query** (`WHERE url IS
  NOT NULL` in `_held_episode_urls_by_show`) — `check_episode_identity` (line 162-212)
  already treats a missing url as a data-integrity failure fleet-wide, so this is
  consistent with an existing invariant, not a new gap; noted for completeness.
- **Message wording change** (dropping "we have {latest}") is a small, deliberate UX
  regression in the Slack text unless a display-only `MAX(publish_date)` query is added
  back — call this out to whoever reviews the PR since it's a visible-in-Slack change,
  not just internal refactor.

## 8. Spec corrections / clarifications

- The plan's Phase 4 bullet says "set difference on Taddy uuid" as if uuid-only — it
  covers 5 of 6 podcast shows. Culture Gabfest has no `taddy_uuid` (Taddy won't
  transcribe it — iHeart rights, per `show_config.py:139`) and needs the RSS-guid
  identity path instead. Both paths already share one mechanism today
  (`feed_recent_dates`'s `if cfg.taddy_uuid: ... elif "megaphone" in url: ...`
  branch) — this design's `feed_recent_episodes` mirrors that same branch, so "compare
  by identity instead of date" is one change that covers both, not two separate
  designs.
- Nothing in this item was already done — `split_missing_feed_dates` /
  `check_import_caught_up` are exactly as described in the plan (verified by reading,
  not assumed), and the sponsor-block / schema items called out as already-shipped in
  the task brief are confirmed absent from this file's logic (only referenced
  elsewhere, e.g. `check_sponsor_share`'s `m.sponsor_source` — unrelated to this item).

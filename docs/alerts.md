# Alerts — what each Slack message in #list-maker means

*Moved here from the root `AGENTS.md` on 2026-11-13 (Harness 2027 tidy), unchanged, so the root file stays small enough for Codex to read whole; the root keeps the rule and a pointer. Written 2026-09-23, simplified 2026-09-24.*

**The rule for failures:** say it when a check starts failing (or the set of failing checks changes), again **every 7 days** while it is still unfixed, and **once on recovery**. Never daily. Kevin's standard: a check that talks every day trains its reader to ignore it; one that talks only once can't tell him a problem was never fixed.

**One invariant carries it** (`pipeline/announce.py`, simplified 2026-09-24). For each check or step, a Slack post is only ever one of:
1. **its first failure** ("N new problem(s)"), which also opens its own GitHub issue;
2. **the weekly word**: every open issue whose last post is 7 or more days old gets one, whatever today's result — "still failing since Sep 22 (8 days)", "failing again" if it relapsed after "recovered", "couldn't check today" when the run didn't get to it (a skipped step, a feed that didn't answer), or "passing, confirming recovery" when it's green but not yet verified. An open problem never goes silent;
3. **a verified recovery**, and only if the last thing said was "failing": two green runs in a row (consecutive runs of the workflow) for the daily entities run, the first green run for the weekly and twice-weekly workflows. A show the feed check couldn't see stays counted as failing until it's seen passing.

What follows from that:
- **Flapping is one problem:** a check that flips back and forth stays one issue, said at most weekly, and the weekly word counts the flips ("on and off: failed 4 of the last 7 runs"). A relapse within a week of "recovered" reopens the same issue quietly; the next word is the weekly one.
- **The issue is the memory** (label `pipeline-failure` + `entities` / `music` / `intake`): a hidden marker at the top holds the dates, what was last said, the green streak, the last 14 results and the failing shows. It closes itself on recovery. Closing one by hand means "I think it's fixed": it is never reopened, and if the problem is still there the next run opens a fresh one.
- **Per check, not per show:** a second show failing a check that is already open shows in the issue at once and in the next weekly word, not as a message of its own.
- **A music show's feed gap can have two threads:** `pipeline.yml`'s (judged right after each import) and the daily entities run's backstop (a week past the window). Each says at most one thing a week. That is the price of never letting one workflow silence another's check.
- **A cancelled run counts as a failure** (usually a time limit; every step has its own time limit, and they add up to less than the job's, so a hang normally fails a named step instead).
- **Manual `pipeline.yml` runs for `all` or show 3** post when they fail but keep no issue: no schedule re-checks them, so an issue would never be reminded or closed.

| Message | Sent by | When | What it means / first move |
|---|---|---|---|
| :rotating_light: `list-maker · daily entities run — N new problem(s)` (also `SOP music run`, `TAL music run`, `weekly curated intake`) | `pipeline/announce.py`, the last step of `entities.yml`, `pipeline.yml`, `blogs.yml` | A check or step started failing | Each item says what failed, why it usually happens and what to check first (text in `pipeline/alert_guides.py`), with links to its issue and the run |
| :hourglass_flowing_sand: `… — still failing` | same | The same failure, 7+ days after it was last announced (also when it couldn't be checked that day: "couldn't check today") | Nothing got fixed. "On and off" means it is flapping. The issue has the history |
| :hourglass_flowing_sand: `… — confirming recovery` | same | 7+ days since the last word, and it's passing but not yet verified | Probably fixed; "recovered" follows once it's confirmed |
| :rotating_light: `… — failing again` | same | It relapsed after "recovered", and a week has passed since the last word | It isn't fixed after all; same issue, reopened |
| :white_check_mark: `… — recovered` | same | Verified: two green runs in a row (daily), or the first green run (weekly) | Its issue is closed. Nothing to do |
| :warning: `… — the alert step itself crashed` | same | `announce.py` raised | The run's result wasn't announced; read the Announce step's log |
| :inbox_tray: `list-maker intake [all]: judged …` | `pipeline/run_intake.py` (`blogs.yml`) | Every Monday, even a quiet week | Not an alert: the week's intake report |
| :bar_chart: `list-maker pulse` | `pipeline/pulse_report.py`, the `pulse` job in `entities.yml` | 1st and 15th | The positive heartbeat. If one doesn't arrive, the trigger or the pulse is broken. `pulse FAILED` comes from `pulse.yml` |
| :rotating_light: `DB preflight failed` | `pipeline/db_preflight.py` | Only in `eval.yml` / `pulse.yml`; elsewhere it's folded into the announce message | The runner couldn't reach Neon; usually that runner's network, and the next run heals it |
| :rotating_light: `extraction eval FAILED` / `intake judge eval FAILED` | `eval.yml` (+ `evals/*/run_eval.py`) | Monday eval breaches a quality floor | Not yet on the announce step, so it can still post twice |
| `list-maker: <workflow> failed` / `cron trigger FAILED` / `dispatches could not be checked` | `cloudflare-trigger/worker.js` | Next day, about yesterday's dispatches | Only if the Worker's own `SLACK_WEBHOOK_URL` secret is set — none has appeared in the channel since June |

**Mid-run warnings** (a Notion sync with some failed pages) go through `common.alert_note`: inside the three announced workflows they ride along in that run's message, and are otherwise listed in the Announce step's summary. Run locally, they post directly. A sync that keeps failing on the same entity becomes a real failure after 2 days, and a Notion page that can't be created at all fails at once (`notion_sync_freshness`), so both are announced like any other failure.

**A feed check that couldn't reach a feed** ("feed UNVERIFIED") neither fails nor recovers an issue about that show: it stays open and still gets its weekly word, saying it couldn't check.

**How fast a music-show gap is caught (a known trade, 2026-09-23):** `pipeline.yml` judges SOP and TAL right after each import, but a missed episode only counts once it is older than the show's window (SOP 6 days, TAL 2), so a real miss usually surfaces at the show's *next* import, about a week in; the entities backstop follows a few days later if `pipeline.yml` has gone quiet. Before this, the daily entities run caught misses in 2–4 days but also cried wolf. A tighter window for the check that runs straight after an import (only the source's own lag) would bring this back to a day or two; it needs a few weeks of SOP website-lag data first.

**The Worker's own Slack secret is unset on purpose-ish:** if it is ever set, its next-day line (`list-maker: entities.yml failed …`) fires for every red day, outside the once-then-weekly rule.

**Where to change it:** thresholds live with each check in `pipeline/data_health.py`; words in `pipeline/alert_guides.py` (a test fails if a check has none); the once / weekly / recovered logic in `pipeline/announce.py`. The music shows are judged by `pipeline.yml` right after each import; the daily entities run checks their feeds only as a backstop a week past their window (`FEED_BACKSTOP_EXTRA_DAYS`, `--music-as-backstop`). Every other check about a music show (songs still arriving, freshness) belongs to the entities run alone. A workflow's `dry_run` input runs the announce step read-only and prints what it would have said.

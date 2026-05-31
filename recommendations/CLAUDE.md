# Recommendations — Agent Instructions

*Inherits from /home/user/list-maker/CLAUDE.md (and ~/DevKev/CLAUDE.md)*
*Last updated: 2026-05-31*

## Purpose

This is the **recommendations** side of list-maker. The rest of the repo extracts
recommendations *out of* podcasts (songs, tools). This section is the inverse:
finding great things to **recommend to Kevin** — starting with **podcasts**, then
expanding to **books**, **TV**, and **movies** (see folder skeleton).

The job is curation, not aggregation. A short list of vetted, defensible picks beats
a long list every time.

## Communication Default (inherited)

Help-me mode: plain language, prescriptive, small asks, explain the "why" in a sentence.
When recommending, lead with the pick and the one reason it fits — then the caveats.

---

## Kevin's Taste Profile

### The non-negotiable: a reporting backbone

This is the #1 filter. Kevin will not tolerate shoot-from-the-hip, credulous audio.
A recommendation **must** show evidence of:
- **Fact-checking** (ideally a named fact-checker or an outlet with editorial standards)
- **Sourced claims** — studies cited, experts named, uncertainty acknowledged
- **Reporting/editorial structure** — not two people riffing and "letting it roll"

> **Anti-pattern (reject these):** *No Such Thing As A Fish* — Kevin likes the
> question-answering *format* but it fails his bar: not credulous enough, no sources,
> interview-and-let-it-roll. Any show that "throws medical terms around" without
> backing them up is disqualified, especially on health/science topics.
>
> Subjective/lived-experience content gets more latitude (it's experience, not a
> factual claim) — but the *science* parts still need backing.

### Format & length

- ✅ **Tight & short** (<25 min): dense, one-idea-per-episode explainers.
- ✅ **Doc-style** (25–55 min): RadioLab / This American Life production — story + science woven, high production value.
- ✅ **Length-agnostic IF genuinely great and rigorous.**
- ❌ **Hard pass on rambly long-form interview shows** — the "long interview with someone who isn't good at interviewing" format Kevin specifically dislikes. A sourced interview *segment* inside a produced episode is fine; a 2-hour unstructured chat is not.

### Host archetype

Either works — **journalist/producer-led** OR **clinician/scientist-led** — *as long as
claims are cited and checked*. The host's credentials matter less than whether the show
sources what it says. (A psychiatrist who cites evidence = good; a journalist with a
fact-checker = good; anyone selling supplements/courses = bad.)

### Production-house signal (from his library)

Strong positive signal: Vox, NYT, NPR, Pushkin, Gimlet, Serial Productions, and
reported-narrative indies (In The Dark, Search Engine, Heavyweight, 99% Invisible,
Science Vs). See `podcasts/library.md` for the full subscription list — treat it as the
ground truth of "what good looks like."

---

## Topic Focus (podcasts, this round)

**Mental-health science, broad-spectrum.** Kevin's preference: *one show that covers a
spectrum of the mind/brain* rather than single-condition shows. Personally relevant
threads to weight toward (but not silo into):
- **Bipolar / mood disorders** (Kevin's own diagnosis)
- **Autism / ASD** (people in his life)
- **ADHD / attention / executive function**
- Plus the surrounding science: neuroscience, psychology, anxiety, trauma, sleep, medication.

Goal: "bite-sized, science-based understanding" of the terms and conditions thrown
around in mental-health conversation — not a deep book-length dive.

---

## Vetting Rubric (apply to every candidate before recommending)

Score each candidate; only recommend shows that clearly clear the backbone bar.

| Criterion | What to check |
|-----------|---------------|
| **Reporting backbone** | Named fact-checker? Outlet with standards? Sources/studies cited on air or in notes? *Pass/fail gate.* |
| **Production quality** | Tight editing, scripted/structured, not meandering. |
| **Format fit** | <25 min explainer OR 25–55 min doc-style. Not a rambly interview show. |
| **Topic fit** | Covers mental-health / brain science, ideally broad-spectrum. |
| **Currency & status** | Still active or has a finished, evergreen archive? Note if defunct. |
| **The "credulity" check** | Does it push back on guests / acknowledge uncertainty, or just nod along? |

For each recommendation, record: **why it fits, the backbone evidence, format/length,
a representative episode to start with, and any caveat.** Always disclose when a show
is borderline or defunct rather than overselling.

---

## Output Convention

- Vetted picks live in `podcasts/recommendations.md` (and later `books/`, `tv/`, `movies/`).
- Keep a short "Considered but rejected" section — the rejections are as useful as the picks,
  and they encode the taste profile for next time.
- Cite sources for claims about a show (fact-checking policy, host credentials, etc.) — practice
  what we preach.

## Calibration Log

- **2026-05-31 (first podcast pass):** Kevin had *already listened to every recommendation* —
  Short Wave, Science Vs, Radiolab, Hidden Brain, Speaking of Psychology, Science of Happiness,
  Loudest Girl in the World, Lost Patients, Invisibilia, etc. **Takeaway: the taste targeting is
  correct, but the canonical/famous tier is exhausted.** Next round must dig for *deeper cuts* —
  newer (2024–2026) limited series, smaller/independent reported shows, international (BBC/ABC/CBC)
  production, and adjacent-but-non-obvious shows — explicitly screening OUT anything already in
  `podcasts/library.md` or the prior recommendations. Novelty is now a hard requirement alongside rigor.

## Future Categories

Same taste profile, adapted:
- **Books** — rigorous, well-sourced, not pop-psych fluff; Kevin wants *less* time investment than books usually demand, so favor the genuinely worth-it.
- **TV / Movies** — taste profile TBD; interview Kevin when we get there.

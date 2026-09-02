#!/usr/bin/env python3
"""Sponsor detection: who paid for this episode, and which mentions are the ad read.

WHY THIS EXISTS. On 2026-08-23 the extraction filters dropped every candidate for one
episode; the next day's re-run "recovered" by storing two sponsor reads (Blitzy,
HyperAgent) as editorial mentions. Dropping ads at extraction time had made the DB
*look* clean while the ads the model missed sat inside it untagged and uncapped — Blitzy
carries 77 mentions today, almost all of them ad reads. Kevin's call (2026-09-01): ads
are kept, tagged, and weight-capped, never deleted. "Sometimes the ads are helpful… we
shouldn't have them overweight by mentions." This module is the deterministic half of
that: a script decides what a sponsor read is; the model only supplies the residue.

THREE SOURCES OF EVIDENCE, in descending order of trust — this ordering IS the
precedence rule in classify_sponsor(), and the winning source is stored on the mention
as `sponsor_source` so a row can answer "how do we know" without a re-derivation:

  1. 'roster'  The publisher's own "Brought to you by:" block in the episode's Taddy
               show notes. Structured data straight from the source; the only signal
               that is a declaration rather than an inference.
  2. 'phrase'  The mention's context sits inside a sponsor-read window in the
               transcript AND the entity is named inside that window. Ours, but
               deterministic, re-derivable, and auditable. Both halves are required:
               an ad break interrupts the episode, so merely being near one proves
               adjacency, not advertising.
  3. 'model'   The extractor returned is_editorial=false. Least reliable (2026-08-24
               is what it looks like when it misses), but free, and the only signal
               available for a show with neither a roster nor a cue phrase.

WHAT THE LIVE DATA SAID (verified against Neon 2026-09-02, 1,074 AI Daily episodes):
  - `episodes.raw_content` is a TEXT column holding JSON (not JSONB), and for SOP/TAL
    it holds plain text that is not JSON at all — every reader here tolerates both.
  - 558 AI Daily episodes contain "brought to you by"; 522 as a <strong> header. Hard
    Fork and PCHH: zero. The block's markup drifts a lot across two years (see
    parse_sponsor_roster) and the URLs are padded with U+2060 WORD JOINER characters.
  - Culture Gabfest has 117 episodes whose description contains "brought to you by"
    and NOT ONE is a sponsor block — they are article titles being discussed ("The
    Second Trump Presidency, Brought to You by YouTubers"). A substring match would
    have invented a sponsor roster for a show that has none, so the header must end
    its own block to count. Same trap in transcripts: see SPONSOR_CUES.

THE BIAS IS DELIBERATE. Where the two errors trade off, this module prefers MISSING an
ad to mis-tagging an editorial mention. A missed ad costs a few points of ranking weight
on one entity; a mis-tagged editorial mention puts a wrong "Sponsor" checkbox on a
product Kevin actually cares about and quietly caps its real coverage out of the
rollup. That is why the cue list is small, the windows are narrow, and a phrase verdict
has to name its entity.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence


@dataclass(frozen=True)
class Sponsor:
    """One entry from an episode's "Brought to you by:" block."""

    name: str
    url: Optional[str] = None  # NULL, never a guessed URL — the block often omits it


@dataclass(frozen=True)
class SponsorVerdict:
    """Why (or why not) a mention counts as a sponsor read.

    `source` is the strongest evidence that fired, and becomes ai_mentions.sponsor_source
    (NULL for editorial). `matched` is the specific roster name or cue phrase, so a
    surprising verdict is one string away from being explained.
    """

    is_sponsor: bool
    source: Optional[str] = None  # 'roster' | 'phrase' | 'model' | None
    matched: Optional[str] = None


SPONSOR_SOURCES = ("roster", "phrase", "model")

# Characters the feed inserts inside anchor text and around names. U+2060 WORD JOINER
# arrives in runs of 20+ inside every AI Daily sponsor link; U+00A0/U+200B/U+FEFF show
# up in older entries. They are invisible, so leaving them in makes "Blitzy" and
# "Blitzy" compare unequal for reasons no one can see in a terminal.
_INVISIBLE_CHARS = dict.fromkeys(
    map(
        ord,
        "⁠"  # WORD JOINER — runs of 20+ inside every AI Daily sponsor link
        "​‌‍"  # zero-width space / non-joiner / joiner
        "﻿"  # BOM as zero-width no-break space
        "­",  # soft hyphen
    ),
    None,
)


def normalize_text_for_matching(text: str) -> str:
    """Lowercase, de-invisible, whitespace-collapsed text — the coordinate space that
    sponsor_windows() offsets and context-snippet lookups both live in.

    Extraction stores context_snippet through extract_entities.normalize_text(), which
    collapses runs of whitespace. Locating that snippet in a raw transcript therefore
    fails on any line break the model swallowed. Both sides go through this function so
    the comparison is apples to apples; offsets returned by sponsor_windows() index into
    the *normalized* string, and callers must locate snippets in the same string.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.translate(_INVISIBLE_CHARS))
    return re.sub(r"\s+", " ", text).strip().lower()


def squash_name(name: str) -> str:
    """A name reduced to comparable form: lowercase alphanumerics, nothing else.

    Kills the differences that are spelling rather than identity — "Robots & Pencils" vs
    "Robots and Pencils", "Super Intelligent" vs "Superintelligent", "Blitzy.com" vs
    "Blitzy", trailing "Inc"/"AI". Deliberately drops spaces so a transcript's spelled-out
    two-word rendering matches a roster's one-word brand.
    """
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name.translate(_INVISIBLE_CHARS)).lower()
    name = name.replace("&", " and ")
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def _tokens(name: str) -> list[str]:
    """Lowercase alphanumeric tokens — used for the phrase-name containment rule."""
    name = unicodedata.normalize("NFKD", (name or "").translate(_INVISIBLE_CHARS)).lower()
    name = name.replace("&", " and ")
    return [t for t in re.split(r"[^a-z0-9]+", name) if t]


def bounded_edit_distance(a: str, b: str, max_distance: int) -> int:
    """Levenshtein distance, giving up (returning max_distance + 1) once it exceeds the
    budget. Hand-rolled rather than pulling in python-Levenshtein: the inputs are brand
    names of a few characters, the budget is 1-2, and a dependency-free version is one
    thing fewer to debug on a bad Tuesday (docs/principles.md).
    """
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,          # deletion
                    current[j - 1] + 1,       # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        if min(current) > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


def names_match(candidate: str, roster_name: str) -> bool:
    """Does an extracted entity name refer to this roster sponsor?

    Four rules, tightest first. Each earns its place against a real row in Neon:

    1. Squashed equality — "Robots & Pencils" == "Robots and Pencils".
    2. Near-miss spelling, edit distance 1 on names of 5+ squashed characters (2 on
       9+): the transcript renders Blitzy as "Blitzi" and the model stored it as its
       own entity. The length floor keeps short names from colliding ("Rovo"/"Robo"
       are two real, different entities in this database — both are 4 characters, so
       neither rule 2 nor rule 3 can merge them).
    3. Token containment — the roster entry is often a phrase around the brand: "The
       Agent Readiness Audit from Superintelligent" must match the entity
       "Superintelligent". Requires the shorter side to be a contiguous token run of
       the longer, and the shorter side to be 5+ squashed characters, so a one-word
       filler token can never carry a match.
    4. Squashed PREFIX for a single-token candidate ("AssemblyAI" opening the entity
       "Assembly AI voice agent API"), same 5-character floor. Prefix, not substring:
       a plain substring test matched "Intel" inside "…from superINTELligent" and
       "Vanta" inside "Trump Can Keep America's AI adVANTAge", labelling two real
       editorial mentions as advertising. Both were caught by hand-checking all 65
       roster/window disagreements on 2026-09-02; the tests pin them.
    """
    a, b = squash_name(candidate), squash_name(roster_name)
    if not a or not b:
        return False
    if a == b:
        return True

    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 5:
        budget = 2 if len(shorter) >= 9 else 1
        if bounded_edit_distance(a, b, budget) <= budget:
            return True

    ta, tb = _tokens(candidate), _tokens(roster_name)
    if ta and tb and len(shorter) >= 5:
        short_t, long_t = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        n = len(short_t)
        for i in range(len(long_t) - n + 1):
            if long_t[i : i + n] == short_t:
                return True
        # 4: one distinctive token OPENING a compound brand string.
        if len(short_t) == 1 and len(short_t[0]) >= 5 and "".join(long_t).startswith(short_t[0]):
            return True
    return False


# ---------------------------------------------------------------------------
# The roster: parsing the publisher's own "Brought to you by:" block
# ---------------------------------------------------------------------------

# The header, wherever it sits. Matched loosely here and then *verified* to end its own
# block by _header_ends_block — the Gabfest false positives are all mid-sentence.
_HEADER_RE = re.compile(r"brought to you by\s*:?", re.IGNORECASE)

# Paragraphs that mean the sponsor block is over. Every AI Daily description ends with
# some combination of these; without a stop condition the roster would swallow the
# show's own newsletter/Discord/Patreon links and turn them into "sponsors".
_ROSTER_TERMINATORS = (
    "the ai daily brief helps",
    "the ai breakdown helps",
    "about the ai breakdown",
    "subscribe to",
    "join our",
    "join the community",
    "interested in sponsoring",
    "be the first to learn",
    "find our guests",
    "learn more:",
    "hosted on acast",
    "see acast.com/privacy",
    "learn more about your ad choices",
    "privacy policy",
    "advertising inquiries",
)

# Separators between a sponsor's name and its pitch, in observed order of ambiguity.
# The en dash and em dash are the common ones; a bare hyphen also appears, sometimes
# glued to the name ("Rackspace Technology-").
_NAME_SEPARATORS = ("–", "—", " - ", " -", "- ", "-", ":", "|")

# Block boundaries. The newline matters: the 2024 descriptions are PLAIN TEXT with no
# markup at all ("Today's Episode Brought to You By:\nPlumb - Build, test…\nABOUT THE AI
# BREAKDOWN"), and without it five AI Daily episodes parsed to an empty roster while
# visibly naming their sponsor.
_BLOCK_SPLIT_RE = re.compile(r"</p\s*>|<p[^>]*>|<br\s*/?>|\n", re.IGNORECASE)

# Leading verbs of a call to action. Some roster entries are nothing but linked CTA text
# ("Visit AGNTCY.org", "Visit Outshift Internet of Agents"), so the brand only survives
# if the verb is stripped. Stripping beats rejecting: AGNTCY and Outshift really are the
# sponsors of those episodes.
_CTA_LEAD_WORDS = frozenset(
    {
        "visit", "try", "go", "get", "check", "learn", "claim", "download", "head",
        "sign", "use", "start", "discover", "listen", "build", "explore", "see",
        "request", "book", "join", "read", "watch", "tune", "shop", "save",
    }
)

# A roster name is a brand, not a pitch. The longest legitimate one observed across
# 1,074 episodes is "The Agent Readiness Audit from Superintelligent" (6 tokens, 46
# chars), so these caps admit every real name and reject the prose that otherwise leaks
# in — e.g. "Is your enterprise ready for the future of agentic AI?", which would
# token-match a real entity called "Agentic AI" and silently label it an advertisement.
_MAX_ROSTER_NAME_TOKENS = 6
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)

# A roster name is a brand, not a paragraph. Anything longer is prose that slipped past
# the separator split, and admitting it would poison names_match with generic tokens.
_MAX_ROSTER_NAME_CHARS = 70


def _visible(fragment: str) -> str:
    """Tag-stripped, entity-decoded, invisible-char-free text of an HTML fragment."""
    text = html.unescape(_TAG_RE.sub(" ", fragment or ""))
    return re.sub(r"\s+", " ", text.translate(_INVISIBLE_CHARS)).strip()


def _header_ends_block(description: str, match_end: int) -> bool:
    """True when the header phrase is the LAST visible thing in its block.

    This one predicate is the whole defence against Culture Gabfest's 117 false
    positives: "…Bloomberg, 'The Second Trump Presidency, Brought to You by YouTubers.'"
    continues with visible text on the same line and is rejected; AI Daily's
    "<strong>Brought to you by:</strong></p>" (and the older "<br>Brought to you by:</p>")
    is followed only by closing tags and is accepted.
    """
    tail = description[match_end : match_end + 400]
    boundary = _BLOCK_SPLIT_RE.search(tail)
    remainder = tail[: boundary.start()] if boundary else tail
    return _visible(remainder).strip(" :–—-") == ""


def _split_entry_name(visible_text: str, strong_text: Optional[str]) -> Optional[str]:
    """The sponsor's name from one roster paragraph.

    The bolded run is authoritative when the entry has one (the 2026 markup bolds
    exactly the brand — "<strong>KPMG</strong> – Research from…"), including the forms
    that trap the separator inside the bold ("<strong>Blitzy - </strong>",
    "<strong>Rackspace Technology-</strong>"). Entries from 2024-2025 have no bold at
    all, so fall back to the text before the first separator.
    """
    for raw in (strong_text, visible_text):
        if not raw:
            continue
        name = raw.strip()
        for sep in _NAME_SEPARATORS:
            idx = name.find(sep)
            if idx > 0:
                name = name[:idx]
                break
        # A URL glued to the pitch ("…with Notion 3.0 https://ntn.so/nlw") is never part
        # of the name; cut there before the length rules judge it.
        name = re.split(r"\bhttps?\b", name, maxsplit=1)[0]
        name = name.strip().strip("-–—:,. ").strip()
        # A question is a pitch, not a brand ("Is your enterprise ready for…?").
        if "?" in name:
            continue
        words = name.split()
        if words and words[0].lower().strip(",.") in _CTA_LEAD_WORDS and len(words) > 1:
            name = " ".join(words[1:])
        name = name.strip().strip("-–—:,. ").strip()
        if not name or not any(c.isalnum() for c in name):
            continue
        if len(name) > _MAX_ROSTER_NAME_CHARS or len(_tokens(name)) > _MAX_ROSTER_NAME_TOKENS:
            continue
        return name
    return None


def parse_sponsor_roster(description_html: Optional[str]) -> list[Sponsor]:
    """Sponsors declared in an episode's "Brought to you by:" block, in listed order.

    Returns [] for a description with no block — which is the honest answer for Hard
    Fork, PCHH and every Gabfest episode, and is why an absent roster is never treated
    as a parse failure. Tolerates: bold and unbold entries, en/em/plain dashes inside or
    outside the bold, missing URLs, HTML entities, U+2060 padding, and the 2024 form
    where the whole block shares one <p> separated by <br>.
    """
    if not description_html:
        return []
    for header in _HEADER_RE.finditer(description_html):
        if not _header_ends_block(description_html, header.end()):
            continue  # a title being discussed, not a sponsor block (see module docstring)
        return _parse_roster_body(description_html[header.end() :])
    return []


def _parse_roster_body(body: str) -> list[Sponsor]:
    sponsors: list[Sponsor] = []
    seen: set[str] = set()
    for fragment in _BLOCK_SPLIT_RE.split(body):
        visible = _visible(fragment)
        if not visible:
            continue
        lowered = visible.lower()
        if any(lowered.startswith(t) for t in _ROSTER_TERMINATORS):
            break
        strong = re.search(r"<(strong|b)[^>]*>(.*?)</\1>", fragment, re.IGNORECASE | re.DOTALL)
        name = _split_entry_name(visible, _visible(strong.group(2)) if strong else None)
        if not name:
            continue
        key = squash_name(name)
        if not key or key in seen:
            continue
        href = _HREF_RE.search(fragment)
        url = href.group(1) if href else None
        if url is None:
            bare = _BARE_URL_RE.search(_visible(fragment))
            url = bare.group(0) if bare else None
        seen.add(key)
        # Prefer NULL over a plausible-looking guess (docs/principles.md): a sponsor
        # whose block carried no link should read as "no URL", not as a wrong one.
        sponsors.append(Sponsor(name=name, url=url.strip() if url else None))
    return sponsors


def roster_from_raw_content(raw_content: Optional[str]) -> list[Sponsor]:
    """Roster from an `episodes.raw_content` value.

    raw_content is a TEXT column, not JSONB, and holds a JSON payload only for the shows
    with store_raw_content=True — SOP and TAL store plain scraped text there, which
    json.loads rejects. Both are normal, so neither raises.
    """
    if not raw_content:
        return []
    try:
        payload = json.loads(raw_content)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    return parse_sponsor_roster(payload.get("description"))


# ---------------------------------------------------------------------------
# The phrase detector: where in the transcript the ad reads are
# ---------------------------------------------------------------------------

# Cues that open a sponsor read. THIS LIST IS THE SPONSOR FORMULA AND NOTHING ELSE —
# the fixed phrases a host says to hand the microphone to an advertiser. Matched against
# normalized (lowercased, whitespace-collapsed) transcript text.
#
# Three call-to-action cues were tried and REMOVED on 2026-09-02 after a dry run over all
# 16,460 stored mentions: ".com slash" produced 459 of 747 phrase verdicts, "dot com
# slash" 28, and "use code" 52 — and hand-checking them found they fire on ordinary
# speech and on the show's OWN promos ("go to patreon.com slash ai-dailybrief"), whose
# windows then swallowed the editorial mentions beside them. That is how an entity like
# Opus 4.5 got tagged from the sentence "In the last two weeks we've gotten GPT-51 …
# Gemini 3 … Opus 4.5". A cue that fires on editorial speech is worse than a missing
# cue: the roster already catches the sponsors that matter, and a mis-tagged editorial
# mention is a visibly wrong Sponsor checkbox in Notion.
SPONSOR_CUES: tuple[str, ...] = (
    # The canonical opener, in the two phrasings this host uses.
    "brought to you by",
    # "thank you to today's sponsors, KPMG, Blitzy, Robots and Pencils, and HyperAgent"
    "today's sponsor",
    "todays sponsor",
    # "thanks to our sponsors" / "a word from our sponsor"
    "our sponsor",
    "sponsored by",
    "this episode is sponsored",
    # The NPR underwriting formula, which is how PCHH marks its reads.
    "support for this show comes from",
    "support for npr",
)

# How far a window reaches from its cue, in normalized characters. Forward-heavy because
# an ad read runs on after its opener; the small lead-in catches a brand named in the
# same breath just before the cue ("…Vanta, today's sponsor…").
#
# The trail was 900 (roughly a 60-second read) and is now 200 — about 32 words, the core
# of a read rather than the whole ad break. Chosen by sweeping it against the labelled
# set on 2026-09-02: recall on known ad mentions is FLAT at 86.2% for every trail from
# 150 to 600, while phrase verdicts grow from 46 to 164 across the same range. Width
# therefore buys no recall and only costs precision, because what actually decides a
# phrase verdict is the named_in_window rule below, not how far the window reaches.
# 200 sits just above the floor, leaving a little room for a read that names its product
# a beat after the cue.
SPONSOR_WINDOW_LEAD_CHARS = 120
SPONSOR_WINDOW_TRAIL_CHARS = 200


def sponsor_windows(
    transcript_text: Optional[str],
    cues: Sequence[str] = SPONSOR_CUES,
    lead: int = SPONSOR_WINDOW_LEAD_CHARS,
    trail: int = SPONSOR_WINDOW_TRAIL_CHARS,
) -> list[tuple[int, int]]:
    """Character spans of the transcript that read as sponsor copy, merged and sorted.

    Offsets index into normalize_text_for_matching(transcript_text), NOT the raw string —
    the raw string's line breaks are exactly what stops a stored context_snippet from
    being found in it. Callers locate snippets in the same normalized text.
    """
    normalized = normalize_text_for_matching(transcript_text)
    if not normalized:
        return []
    spans: list[tuple[int, int]] = []
    for cue in cues:
        start = 0
        while True:
            idx = normalized.find(cue, start)
            if idx < 0:
                break
            spans.append((max(0, idx - lead), min(len(normalized), idx + len(cue) + trail)))
            start = idx + len(cue)
    return _merge_spans(spans)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# A snippet shorter than this can land inside a sponsor window by coincidence ("OpenAI"
# appears in ad copy too), so a phrase verdict needs a snippet with enough text to be
# about one thing.
_MIN_SNIPPET_CHARS_FOR_WINDOW = 25

# A name shorter than this is too generic to prove anything by appearing in ad copy
# ("AI", "ML", "GPT"), so it cannot satisfy the named-in-window rule on its own. Four is
# the floor rather than five because real short sponsors exist (Bolt, Plum, Vanta) and
# the window is only ~320 characters, so a coincidental four-letter hit is rare.
_MIN_NAME_CHARS_FOR_WINDOW = 4


def named_in_window(
    candidate_names: Sequence[str], normalized_transcript: str, window: tuple[int, int]
) -> bool:
    """Is this entity actually NAMED inside the sponsor read?

    Sitting inside a window is not the same as being advertised. An ad break interrupts
    the episode, so the sentences immediately around it are ordinary news that happens to
    be adjacent — that is how "the United States issued an export control directive to
    suspend access to Fable 5" ended up tagged as advertising. A real sponsor read says
    the product's name, usually several times, so requiring the name to appear in the
    window separates "in the ad" from "next to the ad" without any model call.

    Matching is done on squashed text (lowercase alphanumerics, no spaces) so a
    transcript that renders "Robots and Pencils" for "Robots & Pencils", or splits
    "AssemblyAI" into two words, still matches.
    """
    start, end = window
    squashed_window = squash_name(normalized_transcript[start:end])
    if not squashed_window:
        return False
    for name in candidate_names:
        squashed = squash_name(name)
        if len(squashed) >= _MIN_NAME_CHARS_FOR_WINDOW and squashed in squashed_window:
            return True
    return False


def locate_snippet(normalized_transcript: str, snippet: Optional[str]) -> Optional[int]:
    """Start offset of a stored context_snippet inside the normalized transcript.

    Falls back to the snippet's first 60 characters, because the model sometimes elides
    the middle of a long quote with an ellipsis while keeping the opening verbatim.
    Returns None when neither is found — an unlocatable snippet yields no phrase
    evidence rather than a guessed one.
    """
    normalized_snippet = normalize_text_for_matching(snippet)
    if not normalized_snippet or not normalized_transcript:
        return None
    idx = normalized_transcript.find(normalized_snippet)
    if idx >= 0:
        return idx
    if len(normalized_snippet) > 60:
        idx = normalized_transcript.find(normalized_snippet[:60])
        if idx >= 0:
            return idx
    return None


def _containing_window(
    position: int, windows: Sequence[tuple[int, int]]
) -> Optional[tuple[int, int]]:
    """The sponsor window this position falls in, or None."""
    for window in windows:
        if window[0] <= position < window[1]:
            return window
    return None


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------

def classify_sponsor(
    mention: dict[str, Any],
    roster: Sequence[Sponsor],
    windows: Sequence[tuple[int, int]],
    transcript_text: Optional[str] = None,
    normalized_transcript: Optional[str] = None,
) -> SponsorVerdict:
    """Decide whether one mention is a sponsor read, and say which evidence decided it.

    Precedence is roster → phrase → model (the module docstring explains the ordering).
    The first source that fires wins and is recorded; later sources are not consulted,
    so `sponsor_source` always names the strongest available evidence rather than the
    last one checked.

    A ROSTER MATCH IS ENOUGH — it does not have to be corroborated by a sponsor window.
    That was measured, not assumed. The stricter "roster AND window" variant was built
    first and run over all 16,239 stored mentions on 2026-09-02; it disagreed with
    roster-alone on 65 mentions, and hand-labelling every one of them found 51 ads
    against 12 editorial (2 more were a matcher bug, now fixed — see names_match). The
    reason is structural: this host reads mid-roll ads with no verbal marker at all
    ("Blitzy is driving over 5x engineering velocity for large-scale enterprises…"
    sits 13,936 characters from the nearest cue phrase), so demanding a nearby cue
    throws away four true ads for every editorial mention it saves.

    KNOWN AND ACCEPTED (2026-09-02): a company that sponsors an episode AND is cited
    editorially in that same episode has the editorial citation counted as an ad — KPMG
    publishes the AI-amplifiers study the hosts genuinely discuss, and 2 of its 7
    mentions are that. The cost is bounded and the trade is deliberate: the entity-level
    Sponsor flag is TRUE either way (KPMG really is a sponsor), the rollup caps ad
    mentions at 5 rather than discarding them, and both counts are published side by
    side, so the distortion is visible instead of silent. Kevin's rule is that ads are
    kept, tagged, and weight-capped — never deleted — and none of that is broken by
    labelling one citation generously.

    Pass either transcript_text or a pre-normalized transcript; the latter avoids
    re-normalizing a megabyte of text once per mention in the retag script.
    """
    if normalized_transcript is None:
        normalized_transcript = normalize_text_for_matching(transcript_text)

    candidate_names = [
        n for n in (mention.get("canonical_name"), mention.get("mention_text")) if n
    ]
    for sponsor in roster:
        if any(names_match(name, sponsor.name) for name in candidate_names):
            return SponsorVerdict(True, "roster", sponsor.name)

    snippet = mention.get("context_snippet") or ""
    position = locate_snippet(normalized_transcript, snippet) if normalized_transcript else None
    long_enough = len(normalize_text_for_matching(snippet)) >= _MIN_SNIPPET_CHARS_FOR_WINDOW
    if position is not None and long_enough:
        window = _containing_window(position, windows)
        if window is not None and named_in_window(
            candidate_names, normalized_transcript, window
        ):
            return SponsorVerdict(
                True, "phrase", _cue_for_position(normalized_transcript, position)
            )

    if mention.get("is_editorial") is False:
        return SponsorVerdict(True, "model", None)

    return SponsorVerdict(False, None, None)


def _cue_for_position(normalized_transcript: str, position: int) -> Optional[str]:
    """The cue phrase nearest a matched snippet — what goes in `matched`, so a phrase
    verdict names its own reason instead of just asserting one.

    Searches in BOTH directions. A backwards-only search returned None for the most
    common shape there is: the snippet opens the sentence that contains the cue
    ("Today's episode is brought to you by HyperAgent…"), putting the cue a few
    characters AFTER the snippet's start even though the window plainly fired on it.
    """
    best: Optional[str] = None
    best_distance = None
    for cue in SPONSOR_CUES:
        start = 0
        while True:
            idx = normalized_transcript.find(cue, start)
            if idx < 0:
                break
            distance = abs(idx - position)
            if best_distance is None or distance < best_distance:
                best, best_distance = cue, distance
            start = idx + len(cue)
    return best


def apply_sponsor_verdict(mention: dict[str, Any], verdict: SponsorVerdict) -> dict[str, Any]:
    """Stamp a verdict onto a mention dict in place, and return it.

    Editorial is the absence of evidence: is_editorial stays True and sponsor_source
    stays None (which becomes SQL NULL), rather than a placeholder string. Prefer NULL
    over a fake value — docs/principles.md.
    """
    mention["is_editorial"] = not verdict.is_sponsor
    mention["sponsor_source"] = verdict.source if verdict.is_sponsor else None
    return mention

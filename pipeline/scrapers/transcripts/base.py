"""The contract every publisher-transcript reader keeps, and the guards they share.

A reader takes one feed item and answers with exactly one of three things:

  Transcript  the episode's full text, normalised to plain paragraphs, with provenance.
  NotYet      nothing usable today; ask again on a later run (not published yet, the
              page is for a different episode, or the text failed a sanity check).
  Failed      the reader cannot work for this item at all (no story id in the link),
              so retrying will not help and the caller should say so.

The split matters to the orchestrator: NotYet is the normal state of a new episode
for its first hours or days, and the pipeline already waits for it
(run_new_episodes.TRANSCRIPT_GRACE_DAYS). Failed is a fact about the configuration.

PUBLIC REPO: the text in a Transcript is the publisher's. It goes to Neon and nowhere
else: never into a log line, a step summary, an artifact or a test fixture. Callers log
counts, ids, titles and ratios only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Union

from thefuzz import fuzz

# Words per minute of audio a real, complete transcript lands in. Measured 2026-11-23
# on the publishers' own pages: AI Daily 187 to 213, PCHH 182 to 214, Machine Gods about
# 200. Below the floor the page is a summary or a partial; above the ceiling the
# duration is wrong or the page carries two episodes. Either way, not something to store.
MIN_WORDS_PER_MINUTE = 120
MAX_WORDS_PER_MINUTE = 260

# A page whose own title is this similar to the feed's is the same episode. Needed only
# where the lookup is by DATE (AI Daily): the probe that found the source matched the
# previous day's episode when a date slipped, and a promo item shared a date with the
# main episode on 2026-10-26.
MIN_TITLE_SIMILARITY = 80

# Below this many words, a page is a stub whatever its duration says.
MIN_WORDS = 300


@dataclass(frozen=True)
class Transcript:
    text: str
    source_type: str
    model: str
    is_generated: bool
    source_url: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True)
class NotYet:
    reason: str
    # The publisher said "slow down" (HTTP 429). Callers stop asking that source for the
    # rest of the run instead of trying every remaining episode.
    rate_limited: bool = False


@dataclass(frozen=True)
class Failed:
    reason: str


Result = Union[Transcript, NotYet, Failed]


def normalize_title(title: Optional[str]) -> str:
    """Casefolded, accents and curly quotes flattened, punctuation reduced to spaces."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", title)
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-"}))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w']+", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(a: Optional[str], b: Optional[str]) -> int:
    """0 to 100. token_set_ratio, so a page title that adds or drops a subtitle still
    reads as the same episode while a different episode does not."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0
    return fuzz.token_set_ratio(na, nb)


def words_per_minute(words: int, duration_seconds: Optional[int]) -> Optional[float]:
    if not duration_seconds or duration_seconds <= 0:
        return None
    return words / (duration_seconds / 60)


def sanity_check(text: str, duration_seconds: Optional[int]) -> Optional[str]:
    """None when the text looks like a complete transcript, else the reason it doesn't.

    With no duration in the feed, only the absolute floor applies.
    """
    words = len(text.split())
    if words < MIN_WORDS:
        return f"only {words} words"
    wpm = words_per_minute(words, duration_seconds)
    if wpm is not None and not (MIN_WORDS_PER_MINUTE <= wpm <= MAX_WORDS_PER_MINUTE):
        return f"{wpm:.0f} words per audio minute (expected {MIN_WORDS_PER_MINUTE}-{MAX_WORDS_PER_MINUTE})"
    return None


_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def join_paragraphs(paragraphs: list[str]) -> str:
    """The shape extraction was tuned on: plain paragraphs separated by a blank line,
    which is how the Taddy importer joined its PARAGRAPH-style items. Zero-width
    characters go (AI Daily's pages end with a paragraph that is only one)."""
    cleaned = [re.sub(r"\s+", " ", p.translate(_INVISIBLE)).strip() for p in paragraphs]
    return "\n\n".join(p for p in cleaned if p)

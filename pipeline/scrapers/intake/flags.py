"""Whole-document facts the judge is told but never asked to infer.

The rubric (docs/intake-rubric.md) reads the first ~3,000 words and treats these as
facts computed elsewhere — "a flag is strong evidence for the shape it belongs to and
never decides on its own." Everything here is a regex over the full text: the model
sees an excerpt; the script sees the whole page. No flag is a verdict.
"""

from __future__ import annotations

import re

# Kevin's employer sells western wear to consumers. A customer story about a company in
# one of these industries is peer evidence for a deck (rubric S12) rather than someone
# else's marketing. Keyword match on the text, deliberately narrow: a false "yes" turns a
# vendor case study into a save at 0.6, so the list names industries, not products.
PEER_INDUSTRY_TERMS = (
    "retail", "retailer", "apparel", "fashion", "footwear", "consumer brand", "consumer goods",
    "e-commerce", "ecommerce", "direct-to-consumer", "dtc brand", "omnichannel", "merchandis",
    "shopify merchant", "department store", "specialty store", "cpg",
)
CUSTOMER_STORY_TERMS = (
    "customer story", "case study", "customer spotlight", "learn how", "get started with",
    "contact sales", "talk to sales", "read the full story", "start building",
)
_PERCENT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%|\bpercent\b|\bpercentage points?\b", re.I)
_SAMPLE = re.compile(
    r"\b(?:n\s?=\s?\d[\d,]*|randomi[sz]ed|control group|we surveyed|survey of \d|sample of \d|"
    r"more than \d[\d,]* (?:students|firms|companies|workers|users|respondents|organizations|employees)|"
    r"\d[\d,]* (?:respondents|participants)|largest study)\b", re.I)
_PRICE = re.compile(r"(?:\$\s?\d[\d,.]*\s*(?:per|/)\s*(?:1m|million|1k|thousand)?\s*(?:input |output )?tokens?)|"
                    r"(?:per[- ]million[- ]tokens?)|(?:\$\d[\d.,]*\s*/\s*(?:seat|user|month|mo)\b)", re.I)
_TABLE = re.compile(r"^\s*\|.+\|\s*$\n^\s*\|[\s:|-]+\|\s*$", re.M)


def compute_flags(text: str) -> dict[str, bool]:
    low = (text or "").lower()
    return {
        "HAS_PERCENT": bool(_PERCENT.search(text or "")),
        "HAS_SAMPLE": bool(_SAMPLE.search(text or "")),
        "HAS_PRICE": bool(_PRICE.search(text or "")),
        "HAS_TABLE": bool(_TABLE.search(text or "")),
        "CUSTOMER_STORY": any(t in low for t in CUSTOMER_STORY_TERMS),
        "PEER_INDUSTRY": any(t in low for t in PEER_INDUSTRY_TERMS),
    }


def render_flags(flags: dict[str, bool]) -> str:
    return " ".join(f"{k}: {'yes' if v else 'no'}" for k, v in flags.items())

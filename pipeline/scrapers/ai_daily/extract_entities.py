#!/usr/bin/env python3
"""
AI Daily Brief entity extraction test runner (batch mode).

This script reads cached transcripts and uses OpenAI chat completion to produce
structured mention candidates for schema validation.

Key goals:
1. Use locked taxonomy so types do not drift over time.
2. Mark uncertain rows for review (human-in-the-loop).
3. Run in small batches (default 5 episodes) so later episodes stay "fresh"
   for future prompt/schema iterations.

Usage examples:
    python extract_entities.py --limit 5 --offset 0
    python extract_entities.py --limit 5 --offset 5
    python extract_entities.py --episodes 1339,1342,1343,1344,1349
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import requests
from dotenv import load_dotenv

# Sponsor detection lives beside this file. Two import paths because this module has
# two lives: the orchestrator runs it as a SCRIPT (no package, relative import fails)
# and pytest imports it as pipeline.scrapers.ai_daily.extract_entities. Trying the
# package form first keeps a single module identity under test — importing it twice
# under two names would give the tests a different Sponsor class than the code uses.
try:  # pragma: no cover - exercised by whichever entry point is in use
    from .sponsors import (
        Sponsor,
        apply_sponsor_verdict,
        classify_sponsor,
        normalize_text_for_matching,
        sponsor_windows,
    )
except ImportError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sponsors import (  # type: ignore[no-redef]
        Sponsor,
        apply_sponsor_verdict,
        classify_sponsor,
        normalize_text_for_matching,
        sponsor_windows,
    )


OPENAI_CHAT_COMPLETIONS_ENDPOINT = "https://api.openai.com/v1/chat/completions"

LOCKED_TYPES = [
    "software_product",
    "model",
    "benchmark",
    "report",
    "survey",
    "paper",
    "account",
    "social_post",
    "blog_post",
    "organization",
    "person",
    "other",
]

SENTIMENTS = ["positive", "negative", "neutral", "mixed", "unknown"]
CORE_TYPES = {
    "software_product",
    "model",
    "benchmark",
    "report",
    "survey",
    "paper",
    "account",
    "social_post",
    "blog_post",
}

# --- Media extraction taxonomy (Workstream D: PCHH + Culture Gabfest) ---
MEDIA_TYPES = [
    "movie",
    "tv_series",
    "book",
    "music_album",
    "music_track",
    "game",
    "podcast_series",
    "theater_production",
    "social_account",
    "artist_profile",
    "visual_media_other",
    "other",
]
MEDIA_CORE_TYPES = {
    "movie",
    "tv_series",
    "book",
    "music_album",
    "music_track",
    "game",
    "podcast_series",
    "theater_production",
}
REQUEST_TIMEOUT_SECONDS = 180
OPENAI_MAX_RETRIES = 6
OPENAI_INITIAL_BACKOFF_SECONDS = 2.0
OPENAI_MAX_BACKOFF_SECONDS = 60.0
OPENAI_RETRY_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_CONFIDENCE_REVIEW_THRESHOLD = 0.78
MODEL_PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    # Pricing reference: OpenAI API pricing for GPT-4.1 mini.
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
}
SURVEY_TERMS = {"survey", "poll", "barometer", "census", "questionnaire"}
MEDIA_OUTLET_TERMS = {
    "wall street journal",
    "wsj",
    "new york times",
    "nyt",
    "bloomberg",
    "reuters",
    "the information",
}


@dataclass
class EpisodeInput:
    episode_id: int
    publish_date: str
    title: str
    episode_url: str
    transcript_path: Path


@dataclass
class UsageInfo:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_input_cost_usd: Optional[float]
    estimated_output_cost_usd: Optional[float]
    estimated_total_cost_usd: Optional[float]


def load_environment(repo_root: Path) -> None:
    """Load env vars from common project locations."""
    load_dotenv(os.path.expanduser("~/.env"))
    load_dotenv(repo_root / ".env.local")
    load_dotenv(repo_root / "pipeline" / ".env.local")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract AI Daily entities from cached transcripts")
    parser.add_argument("--limit", type=int, default=5, help="Episodes per batch (default: 5)")
    parser.add_argument("--offset", type=int, default=0, help="Offset into episode list (default: 0)")
    parser.add_argument(
        "--episodes",
        type=str,
        default="",
        help="Comma-separated explicit episode IDs (overrides limit/offset)",
    )
    parser.add_argument("--model", type=str, default=DEFAULT_MODEL, help="OpenAI chat model")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=50000,
        help="Max transcript characters to send (default: 50000)",
    )
    parser.add_argument(
        "--confidence-review-threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_REVIEW_THRESHOLD,
        help="Mentions below this confidence are flagged for review",
    )
    parser.add_argument(
        "--episodes-csv",
        type=str,
        default="codex-notes/2026-02-06-ai-daily-25-episodes.csv",
        help="Episode metadata CSV path (default from codex-notes)",
    )
    parser.add_argument(
        "--transcripts-dir",
        type=str,
        default="pipeline/_cache/ai_daily/transcripts",
        help="Transcript txt cache directory",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="codex-notes/ai-daily-entity-extraction",
        help="Output directory root",
    )
    parser.add_argument(
        "--batch-name",
        type=str,
        default="",
        help="Optional batch name (default auto-generated)",
    )
    parser.add_argument(
        "--focus-core-types",
        dest="focus_core_types",
        action="store_true",
        default=True,
        help="Keep only core taxonomy types (plus unresolved other) for cleaner outputs",
    )
    parser.add_argument(
        "--no-focus-core-types",
        dest="focus_core_types",
        action="store_false",
        help="Keep all extracted types (including organization/person-heavy output)",
    )
    parser.add_argument(
        "--drop-sponsor-mentions",
        action="store_true",
        default=False,
        help="Discard sponsor/ad mentions instead of keeping them tagged (not the "
        "pipeline default; ads are kept, tagged and weight-capped downstream)",
    )
    parser.add_argument(
        "--sponsor-roster-json",
        type=str,
        default="",
        help="Path to the per-episode sponsor roster sidecar written by "
        "run_new_episodes.prepare_extraction_inputs. Optional: without it the roster "
        "signal is unavailable and only transcript cues + the model's own flag apply.",
    )
    parser.add_argument(
        "--extraction-type",
        type=str,
        default="entity_extraction",
        help="Extraction profile: entity_extraction (tech, default) or media_extraction",
    )
    return parser.parse_args()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse json text, including markdown fenced fallback."""
    raw = raw.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        data = json.loads(fence.group(1))
        if isinstance(data, dict):
            return data

    raise ValueError("Model response was not valid JSON object")


def parse_retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


@dataclass(frozen=True)
class ExtractionProfile:
    """One extraction profile = one taxonomy + system prompt + post-processing policy.

    Tech (AI Daily, Hard Fork) and media (PCHH, Culture Gabfest) share the user-prompt
    JSON shape and the same extractor; they differ only in the locked types, the system
    prompt, and whether the tech-specific reclassification heuristics run.
    """

    name: str
    types: list[str]
    core_types: frozenset[str]
    system_prompt: str
    apply_tech_heuristics: bool


def _tech_system_prompt(type_list: str) -> str:
    return (
        "You extract structured references from podcast transcripts for a curated database. "
        "Follow the locked taxonomy exactly and be conservative.\n\n"
        f"Locked entity types: {type_list}.\n"
        "Important rules:\n"
        "1) Never invent URLs. If unknown, set source_url=null.\n"
        "2) If type is unclear, use type='other' and set needs_review=true.\n"
        # The ad read is DATA now, not noise to be skipped: a mention flagged false is
        # kept, tagged and weight-capped downstream, so telling the model to extract
        # sponsor reads rather than ignore them is what makes the tag reachable.
        "3) Set is_editorial=true for normal host commentary/news analysis; set false for explicit sponsor/ad "
        "reads. DO extract products named in a sponsor read — mark them false rather than omitting them.\n"
        "4) Keep only meaningful references, not every generic noun.\n"
        "5) For account mentions, use platform when clear (x/linkedin/youtube/etc).\n"
        "6) Prioritize these types: software_product, model, benchmark, report, survey, paper, account, social_post, blog_post.\n"
        "7) Include organization/person only when central to the specific claim OR needed to attribute a post/report.\n"
        "8) Media outlets (Wall Street Journal, Bloomberg, etc.) are organization, not report.\n"
        "9) If a social post is mentioned, include quoted_text and account when possible.\n"
        "10) Return valid JSON only.\n"
        "11) Do not include duplicate mention rows with identical mention_text + context.\n"
        "12) Aim for quality over quantity; cap at 40 mentions per episode."
    )


def _media_system_prompt(type_list: str) -> str:
    return (
        "You extract MEDIA RECOMMENDATIONS from culture and entertainment podcast transcripts "
        "for a curated database. Capture the movies, shows, books, music, games, podcasts, and live "
        "performances that hosts and guests recommend, endorse, or discuss engaging with.\n\n"
        f"Locked entity types: {type_list}.\n"
        "Important rules:\n"
        "1) Map each cultural work to the closest locked type. People (directors, authors, artists) belong "
        "in that work's facts.creators, NOT as their own mention — unless the person themselves is the "
        "recommendation (then use artist_profile or social_account).\n"
        "2) Pay special attention to endorsement segments — Pop Culture Happy Hour's \"What's Making Me Happy "
        "This Week\" and Culture Gabfest's \"endorsements\" — where hosts explicitly recommend something. Set "
        "facts.explicit_endorsement=true for those.\n"
        "3) In facts, capture when stated: creators (list of {role, name}, e.g. director/author/artist/"
        "showrunner/host), release_year (int), platform (where to watch/read/stream/play), explicit_endorsement "
        "(bool), caveats (reservations a host voiced), comparison_to (similar works named).\n"
        "4) Never invent URLs or facts. If unknown, omit the fact or set source_url=null.\n"
        "5) sentiment_label reflects the hosts' take: positive=recommend/love, negative=pan, mixed=liked-with-"
        "reservations, neutral=mentioned-without-judgment.\n"
        "6) Set is_editorial=true for normal host discussion; false for explicit sponsor/ad reads. DO extract "
        "works named in a sponsor read — mark them false rather than omitting them.\n"
        "7) If a work's type is genuinely unclear, use type='other' and set needs_review=true.\n"
        "8) Keep meaningful recommendations, not every passing pop-culture reference.\n"
        "9) Return valid JSON only. No duplicate rows with identical mention_text + context.\n"
        "10) Aim for quality over quantity; cap at 40 mentions per episode."
    )


def get_profile(extraction_type: Optional[str]) -> ExtractionProfile:
    """Select the extraction profile for a show's extraction_type.

    Defaults to the tech taxonomy for anything that isn't explicitly media — song_extraction
    shows (SOP/TAL) don't use this extractor at all, so 'tech' is a safe, behavior-preserving
    default for any non-media caller.
    """
    if extraction_type == "media_extraction":
        return ExtractionProfile(
            name="media",
            types=MEDIA_TYPES,
            core_types=frozenset(MEDIA_CORE_TYPES),
            system_prompt=_media_system_prompt(", ".join(MEDIA_TYPES)),
            apply_tech_heuristics=False,
        )
    return ExtractionProfile(
        name="tech",
        types=LOCKED_TYPES,
        core_types=frozenset(CORE_TYPES),
        system_prompt=_tech_system_prompt(", ".join(LOCKED_TYPES)),
        apply_tech_heuristics=True,
    )


def openai_extract(
    api_key: str,
    model: str,
    episode: EpisodeInput,
    transcript_text: str,
    profile: ExtractionProfile,
) -> tuple[dict[str, Any], UsageInfo]:
    system_prompt = profile.system_prompt

    user_prompt = (
        "Extract mention candidates from this episode.\n\n"
        f"episode_id: {episode.episode_id}\n"
        f"title: {episode.title}\n"
        f"publish_date: {episode.publish_date}\n"
        f"episode_url: {episode.episode_url}\n\n"
        "Return this JSON shape exactly:\n"
        "{\n"
        '  "episode_id": <int>,\n'
        '  "mentions": [\n'
        "    {\n"
        '      "mention_text": <string>,\n'
        '      "canonical_name": <string>,\n'
        '      "entity_type": <one locked type>,\n'
        '      "platform": <string or null>,\n'
        '      "source_url": <string or null>,\n'
        '      "quoted_text": <string or null>,\n'
        '      "context_snippet": <string>,\n'
        '      "sentiment_label": <positive|negative|neutral|mixed|unknown>,\n'
        '      "is_editorial": <boolean>,\n'
        '      "confidence": <0..1>,\n'
        '      "needs_review": <boolean>,\n'
        '      "review_reason": <string or null>,\n'
        '      "facts": [\n'
        "        {\n"
        '          "fact_key": <string>,\n'
        '          "fact_value": <json scalar or object>,\n'
        '          "confidence": <0..1>\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "new_type_candidates": [\n'
        "    {\n"
        '      "proposed_type": <string>,\n'
        '      "reason": <string>,\n'
        '      "example_mention": <string>\n'
        "    }\n"
        "  ],\n"
        '  "notes": [<string>]\n'
        "}\n\n"
        "Transcript:\n"
        f"{transcript_text}\n"
    )

    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    resp = None
    for attempt in range(1, OPENAI_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                OPENAI_CHAT_COMPLETIONS_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            if attempt >= OPENAI_MAX_RETRIES:
                raise RuntimeError(
                    f"OpenAI request failed after {attempt} attempts: {exc}"
                ) from exc
            sleep_s = min(
                OPENAI_MAX_BACKOFF_SECONDS,
                OPENAI_INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1.0),
            )
            print(
                f"OpenAI request error (attempt {attempt}/{OPENAI_MAX_RETRIES}); "
                f"retrying in {sleep_s:.1f}s: {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(sleep_s)
            continue

        if resp.status_code < 400:
            break

        if resp.status_code in OPENAI_RETRY_STATUS_CODES and attempt < OPENAI_MAX_RETRIES:
            retry_after = parse_retry_after_seconds(resp.headers.get("Retry-After"))
            backoff = min(
                OPENAI_MAX_BACKOFF_SECONDS,
                OPENAI_INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1.0),
            )
            sleep_s = retry_after if retry_after is not None else backoff
            print(
                f"OpenAI transient error {resp.status_code} (attempt {attempt}/{OPENAI_MAX_RETRIES}); "
                f"retrying in {sleep_s:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(sleep_s)
            continue

        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:800]}")

    if resp is None:
        raise RuntimeError("OpenAI request failed with no response")

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    usage_raw = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
    completion_tokens = int(usage_raw.get("completion_tokens") or 0)
    total_tokens = int(usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens))

    rates = MODEL_PRICING_USD_PER_M_TOKENS.get(model)
    input_cost: Optional[float] = None
    output_cost: Optional[float] = None
    total_cost: Optional[float] = None
    if rates:
        input_cost = (prompt_tokens / 1_000_000.0) * rates["input"]
        output_cost = (completion_tokens / 1_000_000.0) * rates["output"]
        total_cost = input_cost + output_cost

    usage = UsageInfo(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_input_cost_usd=input_cost,
        estimated_output_cost_usd=output_cost,
        estimated_total_cost_usd=total_cost,
    )

    return parse_json_object(content), usage


def sanitize_fact(fact: Any) -> Optional[dict[str, Any]]:
    if not isinstance(fact, dict):
        return None
    key = normalize_text(str(fact.get("fact_key", "")))
    if not key:
        return None
    conf = fact.get("confidence", 0.5)
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return {
        "fact_key": key,
        "fact_value": fact.get("fact_value"),
        "confidence": conf,
    }


def sanitize_mention(
    mention: Any,
    episode_id: int,
    confidence_review_threshold: float,
    valid_types: list[str] = LOCKED_TYPES,
) -> Optional[dict[str, Any]]:
    if not isinstance(mention, dict):
        return None

    mention_text = normalize_text(str(mention.get("mention_text", "")))
    canonical_name = normalize_text(str(mention.get("canonical_name", "")))
    context_snippet = normalize_text(str(mention.get("context_snippet", "")))
    if not mention_text or not canonical_name or not context_snippet:
        return None

    entity_type = normalize_text(str(mention.get("entity_type", "other"))).lower()
    needs_review = bool(mention.get("needs_review", False))
    review_reason = mention.get("review_reason")
    if review_reason is not None:
        review_reason = normalize_text(str(review_reason))
        if review_reason == "":
            review_reason = None

    if entity_type not in valid_types:
        entity_type = "other"
        needs_review = True
        review_reason = review_reason or "model_proposed_unknown_type"

    sentiment = normalize_text(str(mention.get("sentiment_label", "unknown"))).lower()
    if sentiment not in SENTIMENTS:
        sentiment = "unknown"

    confidence = mention.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    if confidence < confidence_review_threshold:
        needs_review = True
        if review_reason is None:
            review_reason = "low_confidence"

    platform = mention.get("platform")
    if platform is not None:
        platform = normalize_text(str(platform))
        if platform == "":
            platform = None

    source_url = mention.get("source_url")
    if source_url is not None:
        source_url = normalize_text(str(source_url))
        if source_url == "":
            source_url = None

    quoted_text = mention.get("quoted_text")
    if quoted_text is not None:
        quoted_text = normalize_text(str(quoted_text))
        if quoted_text == "":
            quoted_text = None

    is_editorial = bool(mention.get("is_editorial", True))
    facts_raw = mention.get("facts", [])
    facts: list[dict[str, Any]] = []
    if isinstance(facts_raw, list):
        for f in facts_raw:
            normalized = sanitize_fact(f)
            if normalized is not None:
                facts.append(normalized)

    return {
        "episode_id": episode_id,
        "mention_text": mention_text,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "platform": platform,
        "source_url": source_url,
        "quoted_text": quoted_text,
        "context_snippet": context_snippet,
        "sentiment_label": sentiment,
        "is_editorial": is_editorial,
        # Placeholder until classify_sponsor rules on it; NULL/None is "editorial",
        # never a stand-in value. process_episode_mentions_with_stats always overwrites.
        "sponsor_source": None,
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "facts": facts,
    }


def postprocess_mention_types(
    mention: dict[str, Any],
    valid_types: list[str] = LOCKED_TYPES,
    apply_tech_heuristics: bool = True,
) -> dict[str, Any]:
    """
    Light normalization layer to improve consistency after model extraction.

    The reclassification heuristics below (media-outlet→org, survey/benchmark recovery,
    posting→account) are tech-taxonomy specific, so they run only when apply_tech_heuristics
    is True. For the media profile, skip them and just gate the type against the media
    taxonomy — otherwise a media "book" whose context mentions "survey" would be retyped.
    """
    if not apply_tech_heuristics:
        if mention["entity_type"] not in valid_types:
            mention["entity_type"] = "other"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "postprocess_unknown_type"
        if mention["entity_type"] == "other":
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "other_type_needs_review"
        return mention

    text_blob = " ".join(
        [
            mention.get("mention_text", ""),
            mention.get("canonical_name", ""),
            mention.get("context_snippet", ""),
            mention.get("quoted_text") or "",
        ]
    ).lower()

    # If media outlet was classified as report, normalize to organization.
    if mention["entity_type"] == "report":
        if any(x in text_blob for x in ["journal", "times", "bloomberg", "reuters"]):
            mention["entity_type"] = "organization"
            mention["review_reason"] = mention["review_reason"] or "media_outlet_not_report"
            mention["needs_review"] = True

    # Clean survey naming so org names become "X survey" when needed.
    if mention["entity_type"] == "survey":
        canonical = mention.get("canonical_name", "")
        canonical_l = canonical.lower()
        context_l = mention.get("context_snippet", "").lower()

        if canonical_l in {"ai daily brief", "the ai daily brief"} and "pulse survey" in text_blob:
            mention["canonical_name"] = "AI Usage Pulse Survey"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "survey_name_normalized"
        elif any(term in canonical_l for term in MEDIA_OUTLET_TERMS):
            mention["entity_type"] = "organization"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "media_outlet_not_survey"
        elif not any(term in canonical_l for term in SURVEY_TERMS):
            if "survey" in context_l:
                mention["canonical_name"] = f"{canonical} survey".strip()
                mention["needs_review"] = True
                mention["review_reason"] = mention["review_reason"] or "survey_suffix_added"
            elif "poll" in context_l:
                mention["canonical_name"] = f"{canonical} poll".strip()
                mention["needs_review"] = True
                mention["review_reason"] = mention["review_reason"] or "survey_suffix_added"

    # Recover surveys when strong lexical cue exists.
    if mention["entity_type"] in {"other", "organization", "person"}:
        if "survey" in text_blob:
            mention["entity_type"] = "survey"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "survey_retyped_from_context"

    # Recover benchmarks only when explicit benchmark names appear.
    # Avoid generic "benchmark" context, which can misclassify people/orgs.
    if mention["entity_type"] in {"other", "organization", "person"}:
        if any(
            x in text_blob
            for x in [
                "gpqa",
                "mmlu",
                "swe-bench",
                "swebench",
                "lm arena",
                "lmarena",
                "livecodebench",
                "terminal bench",
                "humanitys last exam",
                "hellaswag",
            ]
        ):
            mention["entity_type"] = "benchmark"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "benchmark_retyped_from_context"

    # Convert person/org to account when clearly discussed as posting.
    if mention["entity_type"] in {"person", "organization"}:
        if any(x in text_blob for x in ["tweeted", "posted", "reposted", "thread on x", "on x"]):
            mention["entity_type"] = "account"
            if not mention.get("platform"):
                mention["platform"] = "x"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "posting_context_retyped_to_account"

    if mention["entity_type"] not in valid_types:
        mention["entity_type"] = "other"
        mention["needs_review"] = True
        mention["review_reason"] = mention["review_reason"] or "postprocess_unknown_type"

    if mention["entity_type"] == "other":
        mention["needs_review"] = True
        mention["review_reason"] = mention["review_reason"] or "other_type_needs_review"

    return mention


# Per-episode counters describing what happened to the model's candidates.
# `sponsor_tagged` counts the mentions KEPT and marked as ads — it is a subset of
# `kept`, not a drop, and it exists so an episode whose only content was a sponsor read
# says so out loud instead of looking like an empty extraction.
# `non_editorial_dropped` now only moves under --drop-sponsor-mentions; the pipeline
# keeps ads (Kevin, 2026-09-01), so a nonzero value in a production batch means someone
# passed that flag.
FILTER_STAT_KEYS = (
    "raw",
    "sanitize_dropped",
    "non_editorial_dropped",
    "non_core_type_dropped",
    "sponsor_tagged",
    "kept",
)

# Columns of mentions.csv, in order — the file the loader reads. Same lockstep contract
# as EPISODE_SUMMARY_FIELDS below, and pinned by the same test: csv.DictWriter refuses a
# row carrying a key this list lacks, which is exactly how PR #23 broke 64/64 batches.
MENTION_CSV_FIELDS = [
    "episode_id",
    "entity_type",
    "canonical_name",
    "mention_text",
    "platform",
    "source_url",
    "sentiment_label",
    "is_editorial",
    "sponsor_source",
    "confidence",
    "needs_review",
    "review_reason",
    "context_snippet",
    "quoted_text",
    "facts_json",
]

REVIEW_CSV_FIELDS = [
    "episode_id",
    "entity_type",
    "canonical_name",
    "mention_text",
    "confidence",
    "review_reason",
    "platform",
    "source_url",
    "context_snippet",
]

# Columns of episode_summary.csv, in order. episode_summary_row() builds rows in this
# exact order, and tests/test_extract_entities.py pins the two together, because
# csv.DictWriter refuses a row carrying a key the column list lacks. On 2026-09-01
# PR #23 added four filter counters to the row and not to this list, and every batch
# of the media backfill failed with "dict contains fields not in fieldnames" (the
# daily entities run would have followed the next day).
EPISODE_SUMMARY_FIELDS = [
    "episode_id",
    "publish_date",
    "title",
    "episode_url",
    "transcript_path",
    "mention_count",
    "raw_mention_count",
    "sponsor_mention_count",
    "dropped_non_editorial",
    "dropped_non_core_type",
    "dropped_invalid",
    "review_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated_total_cost_usd",
]


def episode_summary_row(
    episode: EpisodeInput,
    sanitized_mentions: list[dict[str, Any]],
    filter_stats: dict[str, int],
    usage: UsageInfo,
) -> dict[str, Any]:
    """One episode_summary.csv row; keys are EPISODE_SUMMARY_FIELDS in order."""
    return {
        "episode_id": episode.episode_id,
        "publish_date": episode.publish_date,
        "title": episode.title,
        "episode_url": episode.episode_url,
        "transcript_path": str(episode.transcript_path),
        "mention_count": len(sanitized_mentions),
        "raw_mention_count": filter_stats["raw"],
        "sponsor_mention_count": filter_stats["sponsor_tagged"],
        "dropped_non_editorial": filter_stats["non_editorial_dropped"],
        "dropped_non_core_type": filter_stats["non_core_type_dropped"],
        "dropped_invalid": filter_stats["sanitize_dropped"],
        "review_count": sum(1 for m in sanitized_mentions if m["needs_review"]),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "estimated_total_cost_usd": (
            f"{usage.estimated_total_cost_usd:.8f}" if usage.estimated_total_cost_usd is not None else ""
        ),
    }


def process_episode_mentions_with_stats(
    raw: dict[str, Any],
    episode_id: int,
    profile: ExtractionProfile,
    confidence_review_threshold: float = DEFAULT_CONFIDENCE_REVIEW_THRESHOLD,
    drop_sponsor_mentions: bool = False,
    focus_core_types: bool = True,
    roster: Sequence[Sponsor] | None = None,
    transcript_text: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Turn a raw LLM response into the mentions that actually get loaded — and say
    what happened to the rest.

    This is the single per-episode pipeline — sanitize -> postprocess types -> classify
    -> filter — used by BOTH main() and the eval harness, so "what production extracts"
    has one definition that can't drift. The defaults mirror the production CLI
    defaults (keep ads tagged, core types only), which is what the orchestrator runs.

    ADS ARE TAGGED, NOT DROPPED (Kevin, 2026-09-01). This function used to discard every
    mention with is_editorial=false, which is why the database contains only the ads the
    model MISSED, counted at full weight — 229 of them that the detector can prove
    (retag_sponsor_mentions.py --dry-run, 2026-09-02), 73 of Blitzy's 77 mentions among them. Each surviving mention now gets a sponsor verdict from the deterministic
    detector (roster → phrase → model, see sponsors.classify_sponsor) that OVERRIDES the
    model's own flag in both directions, and carries `sponsor_source` so the row says
    where the verdict came from. The rollup caps ad weight; nothing is deleted.

    The stats exist because of 2026-08-23: the model returned ~5,000 tokens of
    candidates for episode 8429 and these filters removed every one, but nothing
    recorded that — the batch just looked like "the model found nothing", the loader
    raised on the empty file, and the only surviving evidence was a token count in an
    expiring CI log. `raw` vs `kept`, with the per-filter drops, is what makes an empty
    result explainable instead of mysterious. `sponsor_tagged` extends that: an episode
    whose only mentions were ads is NOT an empty extraction, and now it can prove it.
    """
    stats = {key: 0 for key in FILTER_STAT_KEYS}
    mentions_raw = raw.get("mentions", [])
    if not isinstance(mentions_raw, list):
        return [], stats
    stats["raw"] = len(mentions_raw)

    roster = list(roster or [])
    # Normalize the transcript once per episode, not once per mention: these are
    # 50k-character strings and an episode can carry 40 mentions.
    normalized_transcript = normalize_text_for_matching(transcript_text or "")
    windows = sponsor_windows(transcript_text) if transcript_text else []

    out: list[dict[str, Any]] = []
    for mention in mentions_raw:
        normalized = sanitize_mention(
            mention=mention,
            episode_id=episode_id,
            confidence_review_threshold=confidence_review_threshold,
            valid_types=profile.types,
        )
        if normalized is None:
            stats["sanitize_dropped"] += 1
            continue
        normalized = postprocess_mention_types(
            normalized,
            valid_types=profile.types,
            apply_tech_heuristics=profile.apply_tech_heuristics,
        )
        verdict = classify_sponsor(
            normalized, roster, windows, normalized_transcript=normalized_transcript
        )
        apply_sponsor_verdict(normalized, verdict)
        if drop_sponsor_mentions and verdict.is_sponsor:
            stats["non_editorial_dropped"] += 1
            continue
        if (
            focus_core_types
            and normalized["entity_type"] not in profile.core_types
            and normalized["entity_type"] != "other"
        ):
            stats["non_core_type_dropped"] += 1
            continue
        if verdict.is_sponsor:
            stats["sponsor_tagged"] += 1
        out.append(normalized)
    stats["kept"] = len(out)
    return out, stats


def process_episode_mentions(
    raw: dict[str, Any],
    episode_id: int,
    profile: ExtractionProfile,
    confidence_review_threshold: float = DEFAULT_CONFIDENCE_REVIEW_THRESHOLD,
    drop_sponsor_mentions: bool = False,
    focus_core_types: bool = True,
    roster: Sequence[Sponsor] | None = None,
    transcript_text: str | None = None,
) -> list[dict[str, Any]]:
    """The mentions that actually get loaded (see process_episode_mentions_with_stats)."""
    mentions, _stats = process_episode_mentions_with_stats(
        raw,
        episode_id,
        profile,
        confidence_review_threshold=confidence_review_threshold,
        drop_sponsor_mentions=drop_sponsor_mentions,
        focus_core_types=focus_core_types,
        roster=roster,
        transcript_text=transcript_text,
    )
    return mentions


def read_episode_inputs(
    episodes_csv: Path,
    transcripts_dir: Path,
    explicit_ids: list[int],
    offset: int,
    limit: int,
) -> list[EpisodeInput]:
    rows: list[dict[str, str]] = []
    with episodes_csv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    by_id = {int(r["episode_id"]): r for r in rows}
    selected_rows: list[dict[str, str]] = []
    if explicit_ids:
        for ep_id in explicit_ids:
            row = by_id.get(ep_id)
            if row:
                selected_rows.append(row)
    else:
        selected_rows = rows[offset : offset + limit]

    episodes: list[EpisodeInput] = []
    for row in selected_rows:
        episode_id = int(row["episode_id"])
        matches = sorted(transcripts_dir.glob(f"{episode_id}-*.txt"))
        if not matches:
            raise FileNotFoundError(f"Missing transcript file for episode_id={episode_id}")
        episodes.append(
            EpisodeInput(
                episode_id=episode_id,
                publish_date=row["publish_date"],
                title=row["title"],
                episode_url=row["episode_url"],
                transcript_path=matches[0],
            )
        )
    return episodes


def read_sponsor_roster_sidecar(path: str | None) -> dict[int, list[Sponsor]]:
    """Load the per-episode sponsor roster written by prepare_extraction_inputs.

    Shape: {"<episode_id>": [{"name": ..., "url": ...}, ...]}. A sidecar is optional —
    a hand-run batch has none, and an episode absent from it simply has no roster
    signal. Missing evidence degrades the verdict to phrase + model rather than
    failing the batch, which is the difference between a weaker answer and no answer.
    """
    if not path:
        return {}
    file = Path(path).expanduser()
    if not file.exists():
        print(f"  WARNING: sponsor roster sidecar not found: {file} — roster signal unavailable")
        return {}
    raw = json.loads(file.read_text(encoding="utf-8"))
    rosters: dict[int, list[Sponsor]] = {}
    for episode_id, entries in raw.items():
        rosters[int(episode_id)] = [
            Sponsor(name=e["name"], url=e.get("url")) for e in entries if e.get("name")
        ]
    return rosters


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def summarize_mentions(all_mentions: list[dict[str, Any]]) -> dict[str, Any]:
    counts_by_type = Counter()
    counts_by_entity = Counter()
    review_count = 0
    facts_by_key = Counter()
    for mention in all_mentions:
        counts_by_type[mention["entity_type"]] += 1
        counts_by_entity[(mention["entity_type"], mention["canonical_name"])] += 1
        if mention["needs_review"]:
            review_count += 1
        for fact in mention["facts"]:
            facts_by_key[fact["fact_key"]] += 1

    top_entities = []
    for (entity_type, canonical_name), count in counts_by_entity.most_common(50):
        top_entities.append(
            {"entity_type": entity_type, "canonical_name": canonical_name, "mention_count": count}
        )

    return {
        "mention_count": len(all_mentions),
        "review_count": review_count,
        "counts_by_type": dict(counts_by_type),
        "top_entities": top_entities,
        "facts_by_key": dict(facts_by_key),
    }


def build_summary_markdown(
    batch_name: str,
    model: str,
    episodes: list[EpisodeInput],
    summary: dict[str, Any],
    usage_summary: dict[str, Any],
    output_dir: Path,
) -> str:
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    lines.append(f"# AI Daily Entity Extraction - {batch_name}")
    lines.append("")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Model: `{model}`")
    lines.append(f"- Episodes processed: {len(episodes)}")
    lines.append(f"- Total mentions: {summary['mention_count']}")
    lines.append(f"- Mentions flagged for review: {summary['review_count']}")
    lines.append(f"- Prompt tokens: {usage_summary.get('prompt_tokens', 0)}")
    lines.append(f"- Completion tokens: {usage_summary.get('completion_tokens', 0)}")
    lines.append(f"- Total tokens: {usage_summary.get('total_tokens', 0)}")
    estimated_cost = usage_summary.get("estimated_total_cost_usd")
    if estimated_cost is not None:
        lines.append(f"- Estimated OpenAI API cost: `${estimated_cost:.6f}`")
    else:
        lines.append("- Estimated OpenAI API cost: n/a (model pricing not configured)")
    lines.append("")
    lines.append("## Episodes")
    lines.append("")
    for ep in episodes:
        lines.append(
            f"- `{ep.episode_id}` ({ep.publish_date}) - {ep.title} "
            f"- transcript: `{ep.transcript_path}`"
        )
    lines.append("")
    lines.append("## Mention Counts by Type")
    lines.append("")
    for key, count in sorted(summary["counts_by_type"].items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- `{key}`: {count}")
    lines.append("")
    lines.append("## Top Entities")
    lines.append("")
    for item in summary["top_entities"][:20]:
        lines.append(
            f"- `{item['entity_type']}` - {item['canonical_name']}: {item['mention_count']}"
        )
    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    lines.append(f"- Batch manifest: `{output_dir / 'batch_manifest.json'}`")
    lines.append(f"- All mentions CSV: `{output_dir / 'mentions.csv'}`")
    lines.append(f"- Review queue CSV: `{output_dir / 'review_queue.csv'}`")
    lines.append(f"- Per-episode JSON: `{output_dir / 'episodes'}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    profile = get_profile(args.extraction_type)
    repo_root = Path(__file__).resolve().parents[3]
    load_environment(repo_root)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    explicit_ids: list[int] = []
    if args.episodes.strip():
        explicit_ids = [int(x.strip()) for x in args.episodes.split(",") if x.strip()]

    episodes_csv = (repo_root / args.episodes_csv).resolve()
    transcripts_dir = (repo_root / args.transcripts_dir).resolve()
    output_root = (repo_root / args.output_dir).resolve()

    if not episodes_csv.exists():
        raise FileNotFoundError(f"Episodes CSV not found: {episodes_csv}")
    if not transcripts_dir.exists():
        raise FileNotFoundError(f"Transcript dir not found: {transcripts_dir}")

    episodes = read_episode_inputs(
        episodes_csv=episodes_csv,
        transcripts_dir=transcripts_dir,
        explicit_ids=explicit_ids,
        offset=args.offset,
        limit=args.limit,
    )
    if not episodes:
        raise RuntimeError("No episodes selected")

    batch_name = args.batch_name.strip()
    if not batch_name:
        if explicit_ids:
            batch_name = f"batch-explicit-{len(explicit_ids)}"
        else:
            start = args.offset + 1
            end = args.offset + len(episodes)
            batch_name = f"batch-{start:02d}-to-{end:02d}"

    output_dir = output_root / batch_name
    episodes_dir = output_dir / "episodes"
    output_dir.mkdir(parents=True, exist_ok=True)
    episodes_dir.mkdir(parents=True, exist_ok=True)

    sponsor_rosters = read_sponsor_roster_sidecar(args.sponsor_roster_json)

    print(f"Running extraction for {len(episodes)} episodes into {output_dir}", flush=True)
    with_roster = sum(1 for e in episodes if sponsor_rosters.get(e.episode_id))
    print(
        f"Sponsor rosters: {with_roster}/{len(episodes)} episode(s) declare sponsors in "
        f"their show notes",
        flush=True,
    )

    all_mentions: list[dict[str, Any]] = []
    per_episode_results: list[dict[str, Any]] = []
    all_new_type_candidates: list[dict[str, Any]] = []
    all_notes: list[str] = []
    filter_totals = {key: 0 for key in FILTER_STAT_KEYS}
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_input_cost_usd = 0.0
    total_output_cost_usd = 0.0
    has_cost_estimate = False

    for idx, episode in enumerate(episodes, start=1):
        transcript_text = episode.transcript_path.read_text(encoding="utf-8", errors="ignore")
        if len(transcript_text) > args.max_chars:
            transcript_text = transcript_text[: args.max_chars]

        print(
            f"[{idx}/{len(episodes)}] episode_id={episode.episode_id} chars={len(transcript_text)}",
            flush=True,
        )

        raw, usage = openai_extract(
            api_key=api_key,
            model=args.model,
            episode=episode,
            transcript_text=transcript_text,
            profile=profile,
        )
        total_prompt_tokens += usage.prompt_tokens
        total_completion_tokens += usage.completion_tokens
        total_tokens += usage.total_tokens
        if usage.estimated_input_cost_usd is not None and usage.estimated_output_cost_usd is not None:
            has_cost_estimate = True
            total_input_cost_usd += usage.estimated_input_cost_usd
            total_output_cost_usd += usage.estimated_output_cost_usd

        sanitized_mentions, filter_stats = process_episode_mentions_with_stats(
            raw,
            episode.episode_id,
            profile,
            confidence_review_threshold=args.confidence_review_threshold,
            drop_sponsor_mentions=args.drop_sponsor_mentions,
            focus_core_types=args.focus_core_types,
            roster=sponsor_rosters.get(episode.episode_id),
            # The same text the model saw, so a window offset and a context_snippet
            # refer to the same string. Passing the untruncated file would put windows
            # past --max-chars, where no snippet can ever land.
            transcript_text=transcript_text,
        )
        for key in FILTER_STAT_KEYS:
            filter_totals[key] += filter_stats[key]
        if filter_stats["raw"] and not filter_stats["kept"]:
            # Say it where the run summary will show it: the model spoke, the filters
            # removed everything. Not an error — the loader records it as a declared
            # empty result so the episode is not re-queued every day. Since ads are now
            # kept rather than dropped, an episode whose only content was a sponsor read
            # can no longer reach this line (it has mentions); that is the point.
            print(
                f"  episode {episode.episode_id}: {filter_stats['raw']} candidate(s) from the model, "
                f"0 kept (non-editorial {filter_stats['non_editorial_dropped']}, non-core type "
                f"{filter_stats['non_core_type_dropped']}, invalid {filter_stats['sanitize_dropped']}) "
                f"— declared empty"
            )
        elif filter_stats["sponsor_tagged"]:
            print(
                f"  episode {episode.episode_id}: {filter_stats['sponsor_tagged']} of "
                f"{filter_stats['kept']} kept mention(s) tagged as sponsor reads"
            )

        new_type_candidates = raw.get("new_type_candidates", [])
        if not isinstance(new_type_candidates, list):
            new_type_candidates = []
        normalized_new_type_candidates = []
        for c in new_type_candidates:
            if isinstance(c, dict):
                proposed_type = normalize_text(str(c.get("proposed_type", ""))).lower()
                if not proposed_type:
                    continue
                if proposed_type in profile.types:
                    # Ignore suggestions that are already in the locked taxonomy.
                    continue
                normalized_new_type_candidates.append(
                    {
                        "episode_id": episode.episode_id,
                        "proposed_type": proposed_type,
                        "reason": normalize_text(str(c.get("reason", ""))),
                        "example_mention": normalize_text(str(c.get("example_mention", ""))),
                    }
                )

        notes = raw.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        notes = [normalize_text(str(n)) for n in notes if normalize_text(str(n))]

        episode_payload = {
            "episode_id": episode.episode_id,
            "publish_date": episode.publish_date,
            "title": episode.title,
            "episode_url": episode.episode_url,
            "transcript_path": str(episode.transcript_path),
            "model": args.model,
            "mention_count": len(sanitized_mentions),
            "raw_mention_count": filter_stats["raw"],
            "sponsor_mention_count": filter_stats["sponsor_tagged"],
            "dropped": {
                "non_editorial": filter_stats["non_editorial_dropped"],
                "non_core_type": filter_stats["non_core_type_dropped"],
                "invalid": filter_stats["sanitize_dropped"],
            },
            "mentions": sanitized_mentions,
            "new_type_candidates": normalized_new_type_candidates,
            "notes": notes,
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "estimated_input_cost_usd": usage.estimated_input_cost_usd,
                "estimated_output_cost_usd": usage.estimated_output_cost_usd,
                "estimated_total_cost_usd": usage.estimated_total_cost_usd,
            },
        }
        write_json(episodes_dir / f"{episode.episode_id}.json", episode_payload)

        all_mentions.extend(sanitized_mentions)
        all_new_type_candidates.extend(normalized_new_type_candidates)
        all_notes.extend(notes)
        per_episode_results.append(episode_summary_row(episode, sanitized_mentions, filter_stats, usage))

    summary = summarize_mentions(all_mentions)

    mention_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    for mention in all_mentions:
        row = {
            "episode_id": mention["episode_id"],
            "entity_type": mention["entity_type"],
            "canonical_name": mention["canonical_name"],
            "mention_text": mention["mention_text"],
            "platform": mention["platform"] or "",
            "source_url": mention["source_url"] or "",
            "sentiment_label": mention["sentiment_label"],
            "is_editorial": str(mention["is_editorial"]).lower(),
            # Empty cell, not the string "none" — the loader turns "" into SQL NULL.
            "sponsor_source": mention.get("sponsor_source") or "",
            "confidence": f"{mention['confidence']:.4f}",
            "needs_review": str(mention["needs_review"]).lower(),
            "review_reason": mention["review_reason"] or "",
            "context_snippet": mention["context_snippet"],
            "quoted_text": mention["quoted_text"] or "",
            "facts_json": json.dumps(mention["facts"], ensure_ascii=True),
        }
        mention_rows.append(row)
        if mention["needs_review"]:
            review_rows.append(
                {
                    "episode_id": mention["episode_id"],
                    "entity_type": mention["entity_type"],
                    "canonical_name": mention["canonical_name"],
                    "mention_text": mention["mention_text"],
                    "confidence": f"{mention['confidence']:.4f}",
                    "review_reason": mention["review_reason"] or "",
                    "platform": mention["platform"] or "",
                    "source_url": mention["source_url"] or "",
                    "context_snippet": mention["context_snippet"],
                }
            )

    write_csv(output_dir / "mentions.csv", mention_rows, MENTION_CSV_FIELDS)
    write_csv(output_dir / "review_queue.csv", review_rows, REVIEW_CSV_FIELDS)
    write_csv(output_dir / "episode_summary.csv", per_episode_results, EPISODE_SUMMARY_FIELDS)

    usage_summary = {
        "model": args.model,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
        "estimated_input_cost_usd": total_input_cost_usd if has_cost_estimate else None,
        "estimated_output_cost_usd": total_output_cost_usd if has_cost_estimate else None,
        "estimated_total_cost_usd": (
            (total_input_cost_usd + total_output_cost_usd) if has_cost_estimate else None
        ),
        "pricing_usd_per_m_tokens": MODEL_PRICING_USD_PER_M_TOKENS.get(args.model),
    }

    batch_manifest = {
        "batch_name": batch_name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "input": {
            "episodes_csv": str(episodes_csv),
            "transcripts_dir": str(transcripts_dir),
            "limit": args.limit,
            "offset": args.offset,
                "explicit_ids": explicit_ids,
                "max_chars": args.max_chars,
                "confidence_review_threshold": args.confidence_review_threshold,
                "focus_core_types": args.focus_core_types,
                "drop_sponsor_mentions": args.drop_sponsor_mentions,
                "sponsor_roster_episodes": sorted(
                    eid for eid, r in sponsor_rosters.items() if r
                ),
            },
        "episodes": per_episode_results,
        "summary": summary,
        # raw vs kept across the batch, with the per-filter drops. The loader reads this
        # to record an all-filtered batch as a declared empty result.
        "filter_summary": filter_totals,
        "usage_summary": usage_summary,
        "new_type_candidates": all_new_type_candidates,
        "notes": all_notes,
        "outputs": {
            "episode_json_dir": str(episodes_dir),
            "mentions_csv": str(output_dir / "mentions.csv"),
            "review_queue_csv": str(output_dir / "review_queue.csv"),
            "episode_summary_csv": str(output_dir / "episode_summary.csv"),
        },
    }
    write_json(output_dir / "batch_manifest.json", batch_manifest)

    summary_markdown = build_summary_markdown(
        batch_name=batch_name,
        model=args.model,
        episodes=episodes,
        summary=summary,
        usage_summary=usage_summary,
        output_dir=output_dir,
    )
    (output_dir / "summary.md").write_text(summary_markdown, encoding="utf-8")

    print("", flush=True)
    print("Done.", flush=True)
    print(f"Batch: {batch_name}", flush=True)
    print(f"Mentions: {summary['mention_count']}", flush=True)
    print(
        f"  of which sponsor reads: {filter_totals['sponsor_tagged']} "
        f"(kept and tagged, weight-capped at rollup)",
        flush=True,
    )
    print(f"Needs review: {summary['review_count']}", flush=True)
    print(f"Prompt tokens: {usage_summary['prompt_tokens']}", flush=True)
    print(f"Completion tokens: {usage_summary['completion_tokens']}", flush=True)
    print(f"Total tokens: {usage_summary['total_tokens']}", flush=True)
    if usage_summary["estimated_total_cost_usd"] is not None:
        print(f"Estimated API cost (USD): {usage_summary['estimated_total_cost_usd']:.6f}", flush=True)
    else:
        print("Estimated API cost (USD): n/a", flush=True)
    print(f"Output: {output_dir}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.RequestException as exc:
        print(f"Network error calling OpenAI API: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

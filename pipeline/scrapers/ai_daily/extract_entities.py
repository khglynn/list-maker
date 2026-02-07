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
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv


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
REQUEST_TIMEOUT_SECONDS = 180
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_CONFIDENCE_REVIEW_THRESHOLD = 0.78


@dataclass
class EpisodeInput:
    episode_id: int
    publish_date: str
    title: str
    episode_url: str
    transcript_path: Path


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
        "--include-non-editorial",
        action="store_true",
        default=False,
        help="Include sponsor/ad mentions; default is to exclude them",
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


def openai_extract(
    api_key: str,
    model: str,
    episode: EpisodeInput,
    transcript_text: str,
) -> dict[str, Any]:
    type_list = ", ".join(LOCKED_TYPES)

    system_prompt = (
        "You extract structured references from podcast transcripts for a curated database. "
        "Follow the locked taxonomy exactly and be conservative.\n\n"
        f"Locked entity types: {type_list}.\n"
        "Important rules:\n"
        "1) Never invent URLs. If unknown, set source_url=null.\n"
        "2) If type is unclear, use type='other' and set needs_review=true.\n"
        "3) Set is_editorial=true for normal host commentary/news analysis; set false only for explicit sponsor/ad reads.\n"
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

    resp = requests.post(
        OPENAI_CHAT_COMPLETIONS_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:800]}")

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return parse_json_object(content)


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

    if entity_type not in LOCKED_TYPES:
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
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "facts": facts,
    }


def postprocess_mention_types(mention: dict[str, Any]) -> dict[str, Any]:
    """
    Light normalization layer to improve consistency after model extraction.
    """
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

    # Recover surveys when strong lexical cue exists.
    if mention["entity_type"] in {"other", "organization", "person"}:
        if "survey" in text_blob:
            mention["entity_type"] = "survey"
            mention["needs_review"] = True
            mention["review_reason"] = mention["review_reason"] or "survey_retyped_from_context"

    # Recover benchmarks from common benchmark keywords.
    if mention["entity_type"] in {"other", "organization", "person"}:
        if any(x in text_blob for x in ["benchmark", "gpqa", "mmlu", "swe-bench", "lmarena", "lm arena", "livecodebench"]):
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

    if mention["entity_type"] not in LOCKED_TYPES:
        mention["entity_type"] = "other"
        mention["needs_review"] = True
        mention["review_reason"] = mention["review_reason"] or "postprocess_unknown_type"

    return mention


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

    print(f"Running extraction for {len(episodes)} episodes into {output_dir}", flush=True)

    all_mentions: list[dict[str, Any]] = []
    per_episode_results: list[dict[str, Any]] = []
    all_new_type_candidates: list[dict[str, Any]] = []
    all_notes: list[str] = []

    for idx, episode in enumerate(episodes, start=1):
        transcript_text = episode.transcript_path.read_text(encoding="utf-8", errors="ignore")
        if len(transcript_text) > args.max_chars:
            transcript_text = transcript_text[: args.max_chars]

        print(
            f"[{idx}/{len(episodes)}] episode_id={episode.episode_id} chars={len(transcript_text)}",
            flush=True,
        )

        raw = openai_extract(
            api_key=api_key,
            model=args.model,
            episode=episode,
            transcript_text=transcript_text,
        )

        mentions_raw = raw.get("mentions", [])
        sanitized_mentions: list[dict[str, Any]] = []
        if isinstance(mentions_raw, list):
            for mention in mentions_raw:
                normalized = sanitize_mention(
                    mention=mention,
                    episode_id=episode.episode_id,
                    confidence_review_threshold=args.confidence_review_threshold,
                )
                if normalized is not None:
                    normalized = postprocess_mention_types(normalized)
                    sanitized_mentions.append(normalized)

        # Optional filters for cleaner, user-facing review batches.
        filtered_mentions: list[dict[str, Any]] = []
        for mention in sanitized_mentions:
            if not args.include_non_editorial and not mention["is_editorial"]:
                continue
            if args.focus_core_types and mention["entity_type"] not in CORE_TYPES and mention["entity_type"] != "other":
                continue
            filtered_mentions.append(mention)
        sanitized_mentions = filtered_mentions

        new_type_candidates = raw.get("new_type_candidates", [])
        if not isinstance(new_type_candidates, list):
            new_type_candidates = []
        normalized_new_type_candidates = []
        for c in new_type_candidates:
            if isinstance(c, dict):
                proposed_type = normalize_text(str(c.get("proposed_type", ""))).lower()
                if not proposed_type:
                    continue
                if proposed_type in LOCKED_TYPES:
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
            "mentions": sanitized_mentions,
            "new_type_candidates": normalized_new_type_candidates,
            "notes": notes,
        }
        write_json(episodes_dir / f"{episode.episode_id}.json", episode_payload)

        all_mentions.extend(sanitized_mentions)
        all_new_type_candidates.extend(normalized_new_type_candidates)
        all_notes.extend(notes)
        per_episode_results.append(
            {
                "episode_id": episode.episode_id,
                "publish_date": episode.publish_date,
                "title": episode.title,
                "episode_url": episode.episode_url,
                "transcript_path": str(episode.transcript_path),
                "mention_count": len(sanitized_mentions),
                "review_count": sum(1 for m in sanitized_mentions if m["needs_review"]),
            }
        )

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

    write_csv(
        output_dir / "mentions.csv",
        mention_rows,
        [
            "episode_id",
            "entity_type",
            "canonical_name",
            "mention_text",
            "platform",
            "source_url",
            "sentiment_label",
            "is_editorial",
            "confidence",
            "needs_review",
            "review_reason",
            "context_snippet",
            "quoted_text",
            "facts_json",
        ],
    )
    write_csv(
        output_dir / "review_queue.csv",
        review_rows,
        [
            "episode_id",
            "entity_type",
            "canonical_name",
            "mention_text",
            "confidence",
            "review_reason",
            "platform",
            "source_url",
            "context_snippet",
        ],
    )
    write_csv(
        output_dir / "episode_summary.csv",
        per_episode_results,
        [
            "episode_id",
            "publish_date",
            "title",
            "episode_url",
            "transcript_path",
            "mention_count",
            "review_count",
        ],
    )

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
                "include_non_editorial": args.include_non_editorial,
            },
        "episodes": per_episode_results,
        "summary": summary,
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
        output_dir=output_dir,
    )
    (output_dir / "summary.md").write_text(summary_markdown, encoding="utf-8")

    print("", flush=True)
    print("Done.", flush=True)
    print(f"Batch: {batch_name}", flush=True)
    print(f"Mentions: {summary['mention_count']}", flush=True)
    print(f"Needs review: {summary['review_count']}", flush=True)
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

"""The intake judge: deterministic pre-checks first, then two cheap models, then one rule.

Shape (arc plan, "The judge"):
  1. precheck()  — a script decides the structural cases: already ingested, thin
     scrape, PDF, dead link. The model never sees these.
  2. judge_once() — one model reads the rubric + the post and answers
     {verdict, confidence, reason} as strict JSON. Called for BOTH models on every
     candidate: at ~$0.002 a verdict, always asking twice is cheaper than deciding
     when to ask twice.
  3. decide()    — agree → that verdict. Disagree → SAVE, marked disputed. Recall
     first: the expensive error is the usage report Kevin needed and never saw; a
     disputed save is a visible row in Notion and a line in the weekly Slack post.

Models are pinned by id (a swap re-runs evals/intake). They run through OpenRouter
(key: repo secret OPENROUTER_API_KEY, "Listmaker", $15/week cap, expires 2027-09-02)
so the ids match eachie's config and are portable across providers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Ordered fallback lists — first available wins; the exact id used is stored on the row.
JUDGE_MODELS = ("google/gemini-3.7-flash", "google/gemini-3.5-flash")
CHECKER_MODELS = ("openai/gpt-5.6-luna", "openai/gpt-5.4-mini")
REQUEST_TIMEOUT_SECONDS = 60
MAX_TEXT_CHARS = 12_000          # ~3,000 words: enough to judge, cheap enough to ask twice
THIN_WORDS = 200                 # below this a scrape is a paywall/JS shell, not an article (import_blog's rule)
RUBRIC_PATH = Path(__file__).resolve().parents[3] / "docs" / "intake-rubric.md"


@dataclass
class Verdict:
    verdict: str                 # save | skip
    confidence: float            # 0..1
    reason: str
    model: str
    raw: str = ""


@dataclass
class Decision:
    verdict: str
    confidence: float
    reason: str
    judge: Verdict
    checker: Optional[Verdict]
    disputed: bool
    prompt_version: str


@dataclass
class Precheck:
    """The script's verdict before any model runs. `skip_reason` None = proceed."""
    skip_reason: Optional[str]   # duplicate | thin | pdf | dead
    status: str = "skipped"      # skipped, or 'held' for pdf


# ── 1. pre-checks ────────────────────────────────────────────────────────────

def precheck(url: str, *, already_ingested: bool, words: Optional[int], scrape_error: Optional[str]) -> Precheck:
    if already_ingested:
        return Precheck("duplicate")
    if url.lower().split("?")[0].endswith(".pdf"):
        # PDFs live as files in the Obsidian research folder (save_item's rule) — a
        # local-only step, so the row is HELD and the weekly line names it.
        return Precheck("pdf", status="held")
    if scrape_error:
        return Precheck("dead")
    if words is not None and words < THIN_WORDS:
        return Precheck("thin")
    return Precheck(None, status="judged")


# ── 2. one model, one verdict ───────────────────────────────────────────────

def load_rubric(path: Path = RUBRIC_PATH) -> tuple[str, str]:
    """(rubric text, prompt_version). The version is a hash prefix of the rubric, so
    an edit to the rubric is visibly a new prompt on every row it judged."""
    text = path.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def build_messages(rubric: str, *, title: str, source: str, published_on: str, category: list[str],
                   words: Optional[int], links_out: Optional[int], found_via: str, text: str) -> list[dict]:
    body = text[:MAX_TEXT_CHARS]
    truncated = len(text) > MAX_TEXT_CHARS
    user = (
        f"TITLE: {title}\nSOURCE: {source}\nPUBLISHED: {published_on or 'unknown'}\n"
        f"CATEGORY: {', '.join(category) or 'none'}\nWORDS: {words if words is not None else 'unknown'}"
        f"\nLINKS OUT: {links_out if links_out is not None else 'unknown'}\nFOUND VIA: {found_via}\n\n"
        f"TEXT{' (first part)' if truncated else ''}:\n{body}\n\n"
        'Answer with ONE JSON object and nothing else: {"verdict": "save" | "skip", '
        '"confidence": <0..1>, "reason": "<one line, specific to this post>"}'
    )
    return [{"role": "system", "content": rubric}, {"role": "user", "content": user}]


def parse_verdict(raw: str, model: str) -> Verdict:
    """Strict on shape, tolerant on wrapping (code fences, prose around the object)."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError(f"{model}: no JSON object in reply: {raw[:200]!r}")
    data = json.loads(match.group(0))
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("save", "skip"):
        raise ValueError(f"{model}: verdict must be save|skip, got {verdict!r}")
    confidence = float(data.get("confidence", 0))
    confidence = min(1.0, max(0.0, confidence))
    reason = str(data.get("reason", "")).strip()[:500]
    return Verdict(verdict, confidence, reason, model, raw)


def judge_once(messages: list[dict], models: tuple[str, ...], api_key: str,
               client: Optional[httpx.Client] = None) -> Verdict:
    """Ask the first model in `models` that answers; fall through on a provider error."""
    own = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    last_error: Optional[Exception] = None
    try:
        for model in models:
            try:
                resp = client.post(
                    OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {api_key}",
                             "HTTP-Referer": "https://github.com/khglynn/list-maker",
                             "X-Title": "list-maker intake judge"},
                    json={"model": model, "messages": messages, "temperature": 0,
                          "response_format": {"type": "json_object"}, "max_tokens": 300},
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return parse_verdict(content, model)
            except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
        raise RuntimeError(f"no judge model answered ({models}): {last_error}")
    finally:
        if own:
            client.close()


# ── 3. the rule ─────────────────────────────────────────────────────────────

def decide(judge: Verdict, checker: Optional[Verdict], prompt_version: str) -> Decision:
    if checker is None or checker.verdict == judge.verdict:
        conf = judge.confidence if checker is None else (judge.confidence + checker.confidence) / 2
        return Decision(judge.verdict, round(conf, 3), judge.reason, judge, checker, False, prompt_version)
    # Disagreement → save, disputed. The reason shown is the SAVE side's, so the
    # Notion row explains why it came in; the skip side's reason rides in the log.
    saver = judge if judge.verdict == "save" else checker
    return Decision("save", round(saver.confidence, 3), saver.reason, judge, checker, True, prompt_version)


def judge_candidate(*, title: str, source: str, published_on: str, category: list[str],
                    words: Optional[int], links_out: Optional[int], found_via: str, text: str,
                    api_key: Optional[str] = None, rubric_path: Path = RUBRIC_PATH,
                    client: Optional[httpx.Client] = None) -> Decision:
    """Both models, one Decision. Network + files; the pure parts above are what tests pin."""
    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is required to judge candidates")
    rubric, version = load_rubric(rubric_path)
    messages = build_messages(rubric, title=title, source=source, published_on=published_on, category=category,
                              words=words, links_out=links_out, found_via=found_via, text=text)
    first = judge_once(messages, JUDGE_MODELS, api_key, client)
    second = judge_once(messages, CHECKER_MODELS, api_key, client)
    return decide(first, second, version)

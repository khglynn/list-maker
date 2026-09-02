#!/usr/bin/env python3
"""Freeze the labeled set: a candidate pool (metadata + scraped text) + Kevin's labels →
fixtures/labeled_candidates.json (metadata, label, text sha) and the gitignored text cache.

    ./pipeline/venv/bin/python evals/intake/build_fixture.py --pool <pool.json> --texts <dir> --labels <labels.json>

pool.json: [{id, url, source, title, date, category, words, links_out, ...}]; <dir>/<id>.json
holds {"text": ...} per candidate; labels.json: [{id, label: save|skip, note?}]. Candidates
without a label are left out (an unlabeled row is not ground truth). Re-run only on an
intentional re-baseline — the fixture is what the floors are calibrated against.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.intake.run_eval import FIXTURE, FLOORS, TEXT_CACHE, sha  # noqa: E402


def build(pool: list[dict], texts: dict[str, str], labels: dict[str, dict], labeled_by: str) -> dict:
    out = []
    for c in pool:
        lab = labels.get(c["id"])
        if not lab or lab.get("label") not in ("save", "skip"):
            continue
        text = texts.get(c["id"], "")
        out.append({
            "id": c["id"], "url": c["url"], "source": c["source"], "title": c.get("scraped_title") or c["title"],
            "published_on": c.get("date"), "category": c.get("category") or [],
            "words": c.get("words"), "links_out": c.get("links_out"), "found_via": c["source"],
            "text_sha256": sha(text), "label": lab["label"], "note": lab.get("note", ""),
        })
    out.sort(key=lambda r: (r["source"], r["id"]))
    return {"_meta": {"built": date.today().isoformat(), "labeled_by": labeled_by, "n": len(out),
                      "n_save": sum(1 for r in out if r["label"] == "save"), "floors": FLOORS},
            "candidates": out}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pool", required=True)
    p.add_argument("--texts", required=True, help="dir of <id>.json with a 'text' field")
    p.add_argument("--labels", required=True)
    p.add_argument("--labeled-by", default="kevin")
    p.add_argument("--out", default=str(FIXTURE))
    args = p.parse_args()
    pool = json.loads(Path(args.pool).read_text())
    texts = {f.stem: json.loads(f.read_text()).get("text", "") for f in Path(args.texts).glob("*.json")}
    labels = {l["id"]: l for l in json.loads(Path(args.labels).read_text())}
    fixture = build(pool, texts, labels, args.labeled_by)
    TEXT_CACHE.mkdir(parents=True, exist_ok=True)
    for c in fixture["candidates"]:
        (TEXT_CACHE / f"{c['id']}.txt").write_text(texts[c["id"]], encoding="utf-8")
    Path(args.out).write_text(json.dumps(fixture, indent=1), encoding="utf-8")
    m = fixture["_meta"]
    print(f"fixture: {m['n']} labeled ({m['n_save']} save / {m['n'] - m['n_save']} skip) → {args.out}; texts cached in {TEXT_CACHE}")


if __name__ == "__main__":
    main()

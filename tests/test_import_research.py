"""Pure-function tests for the research importer (no DB, no vault)."""

from datetime import date
from pathlib import Path

from pipeline.scrapers.research.import_research import doc_date, doc_title, obsidian_uri


def test_obsidian_uri_is_stable_and_encoded() -> None:
    uri = obsidian_uri(Path("0.2 Clips + Social + AI/Agentic Research/runs/doc.md"))
    assert uri.startswith("obsidian://open?vault=HG%20Main&file=")
    assert "Agentic%20Research" in uri
    assert obsidian_uri(Path("a/b.md")) == obsidian_uri(Path("a/b.md"))


def test_doc_title_prefers_h1_then_stem() -> None:
    assert doc_title(Path("x.md"), "intro\n# Memory Systems Rollup\nbody") == "Memory Systems Rollup"
    assert doc_title(Path("2026-06-03-guide.md"), "no heading here") == "2026-06-03-guide"


def test_doc_date_from_filename_then_frontmatter() -> None:
    assert doc_date(Path("2026-06-03-dependency-hygiene.md"), "") == date(2026, 6, 3)
    assert doc_date(Path("doc.md"), "---\ndate: 2026-05-22\n---\n") == date(2026, 5, 22)
    assert doc_date(Path("doc.md"), "no date anywhere") is None

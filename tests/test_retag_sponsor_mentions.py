"""The one-time pass that reclassifies already-stored mentions as sponsor reads.

plan_changes() is pure given its rows, so the whole decision surface is testable with
fixtures and no database. Hermetic: no DB, no network.
"""

import json

from pipeline.scrapers.ai_daily.retag_sponsor_mentions import (
    plan_changes,
    render_summary,
    summarize_by_entity,
)

ROSTER_HTML = json.dumps(
    {
        "provider": "taddy",
        "description": (
            "<p>Today's news.</p><p><strong>Brought to you by:</strong></p>"
            '<p><strong>Blitzy - </strong>Accelerate development <a href="https://blitzy.com/">x</a></p>'
            '<p><strong>KPMG</strong> – Research on AI adoption <a href="https://kpmg.com/">x</a></p>'
            "<p>The AI Daily Brief helps you understand the most important news in AI.</p>"
        ),
    }
)

# The order matters. The Blitzy read opens the episode with NO cue phrase anywhere near
# it — the mid-roll shape that made a cue-corroboration rule wrong 51 times out of 63 on
# live data. GEMINI_SNIPPET sits at the very end, far past the window trail, so it is
# genuinely outside every window rather than accidentally inside one.
GEMINI_SNIPPET = "Gemini models were a distant second for coding this year."
TRANSCRIPT = (
    "Blitzy is driving over 5x engineering velocity for large-scale enterprises. "
    + ("Editorial analysis of the funding round. " * 30)
    + "Today's sponsor is Vanta, which simplifies compliance for fast-moving teams. "
    + ("More editorial coverage of model releases. " * 60)
    + GEMINI_SNIPPET
)


def _row(**overrides):
    base = {
        "id": 1,
        "episode_id": 8844,
        "entity_id": 28,
        "canonical_name": "Blitzy",
        "mention_text": "Blitzy",
        "context_snippet": "Blitzy is driving over 5x engineering velocity for large-scale enterprises.",
        "is_editorial": True,
        "sponsor_source": None,
        "show_slug": "ai-daily-brief",
        "publish_date": "2026-08-31",
        "episode_title": "An episode",
        "raw_content": ROSTER_HTML,
        "source_text": TRANSCRIPT,
    }
    base.update(overrides)
    return base


def test_a_roster_sponsor_stored_as_editorial_is_planned_for_retag() -> None:
    changes, stats = plan_changes([_row()])

    assert stats["would_tag"] == 1
    assert stats["episodes_with_roster"] == 1
    (change,) = changes
    assert change["from"] == {"is_editorial": True, "sponsor_source": None}
    assert change["to"] == {"is_editorial": False, "sponsor_source": "roster"}
    assert change["matched"] == "Blitzy"


def test_a_cue_window_catches_a_sponsor_that_is_not_on_the_roster() -> None:
    changes, stats = plan_changes(
        [
            _row(
                id=2,
                canonical_name="Vanta",
                mention_text="Vanta",
                context_snippet="Today's sponsor is Vanta, which simplifies compliance for fast-moving teams.",
            )
        ]
    )
    assert stats["would_tag"] == 1
    assert changes[0]["to"]["sponsor_source"] == "phrase"


def test_editorial_mentions_are_left_alone() -> None:
    changes, stats = plan_changes(
        [
            _row(
                id=3,
                entity_id=99,
                canonical_name="Gemini",
                mention_text="Gemini",
                context_snippet=GEMINI_SNIPPET,
            )
        ]
    )
    assert changes == []
    assert stats["unchanged"] == 1 and stats["would_tag"] == 0


def test_an_already_tagged_mention_is_not_replanned() -> None:
    """The detector is deterministic, so a second run must be a no-op — otherwise every
    dry run would report the same changes forever."""
    changes, stats = plan_changes([_row(is_editorial=False, sponsor_source="roster")])
    assert changes == []
    assert stats["already_tagged"] == 1


def test_a_verdict_that_reverses_is_surfaced_not_silently_kept() -> None:
    """Only reachable after a detector change. Un-tagging is exactly what a review needs
    to see, so it is reported rather than skipped.

    This also pins the non-circularity fix: the row's is_editorial=False was written by
    a PREVIOUS retag, so feeding it back to the classifier as the model's opinion would
    make every tagged row re-confirm itself forever and no detector fix could ever undo
    a mistake.
    """
    changes, stats = plan_changes(
        [
            _row(
                id=4,
                canonical_name="Gemini",
                mention_text="Gemini",
                context_snippet=GEMINI_SNIPPET,
                is_editorial=False,
                sponsor_source="phrase",
            )
        ]
    )
    assert stats["would_untag"] == 1
    assert changes[0]["to"] == {"is_editorial": True, "sponsor_source": None}


def test_a_model_sourced_tag_is_not_reversed_by_the_non_circularity_fix() -> None:
    """The one case where the stored False really is the model's own opinion: that
    verdict came from the flag in the first place, so it must survive a re-run."""
    from pipeline.scrapers.ai_daily.retag_sponsor_mentions import original_model_flag

    assert original_model_flag({"is_editorial": False, "sponsor_source": "model"}) is False
    assert original_model_flag({"is_editorial": False, "sponsor_source": "roster"}) is True
    assert original_model_flag({"is_editorial": False, "sponsor_source": None}) is False
    assert original_model_flag({"is_editorial": True, "sponsor_source": None}) is True

    changes, stats = plan_changes(
        [
            _row(
                id=5,
                canonical_name="Gemini",
                mention_text="Gemini",
                context_snippet=GEMINI_SNIPPET,
                is_editorial=False,
                sponsor_source="model",
            )
        ]
    )
    assert changes == [] and stats["already_tagged"] == 1


def test_a_show_without_a_roster_block_still_classifies_by_cue() -> None:
    """Hard Fork has no "Brought to you by" block at all; its ads live only in speech."""
    changes, stats = plan_changes(
        [
            _row(
                id=5,
                show_slug="hard-fork",
                raw_content=None,
                canonical_name="Vanta",
                mention_text="Vanta",
                context_snippet="Today's sponsor is Vanta, which simplifies compliance for fast-moving teams.",
            )
        ]
    )
    assert stats["episodes_with_roster"] == 0
    assert changes[0]["to"]["sponsor_source"] == "phrase"


def test_non_json_raw_content_does_not_break_the_plan() -> None:
    """SOP and TAL store plain scraped text in raw_content — a TEXT column, not JSONB."""
    changes, stats = plan_changes([_row(id=6, raw_content="Rich Rolls scraped page text")])
    assert stats["examined"] == 1  # no exception; the roster is simply unavailable


def test_episode_work_is_done_once_per_episode_not_once_per_mention() -> None:
    """Parsing a roster and normalizing a 50k-character transcript per mention would
    make a 16,000-row retag quadratic in the wrong place."""
    calls = {"n": 0}
    import pipeline.scrapers.ai_daily.retag_sponsor_mentions as rt

    original = rt.roster_from_raw_content

    def counting(raw):
        calls["n"] += 1
        return original(raw)

    rt.roster_from_raw_content = counting
    try:
        plan_changes([_row(id=i) for i in range(10)])
    finally:
        rt.roster_from_raw_content = original
    assert calls["n"] == 1


def test_summary_groups_by_entity_with_its_sources() -> None:
    changes, _ = plan_changes([_row(id=1), _row(id=2), _row(id=3)])
    (entry,) = summarize_by_entity(changes)

    assert entry["canonical_name"] == "Blitzy"
    assert entry["count"] == 3
    assert entry["sources"] == {"roster": 3}
    assert entry["shows"] == ["ai-daily-brief"]
    assert entry["first_date"] == "2026-08-31"


def test_summary_is_ordered_by_volume() -> None:
    rows = [_row(id=1)]
    rows += [
        _row(
            id=10 + i,
            entity_id=1113,
            canonical_name="Vanta",
            mention_text="Vanta",
            context_snippet="Today's sponsor is Vanta, which simplifies compliance for fast-moving teams.",
        )
        for i in range(3)
    ]
    changes, _ = plan_changes(rows)
    names = [e["canonical_name"] for e in summarize_by_entity(changes)]
    assert names == ["Vanta", "Blitzy"]


def test_render_summary_names_the_counts_and_the_entities() -> None:
    changes, stats = plan_changes([_row(id=1), _row(id=2)])
    text = render_summary(stats, summarize_by_entity(changes))
    assert "would tag as ads : 2" in text
    assert "Blitzy" in text and "roster=2" in text

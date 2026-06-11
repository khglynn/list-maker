"""Pure-function tests for the clip-highlight pipeline (no ffmpeg/Whisper/Notion)."""

from pipeline.highlight_clips import (
    anchor_url,
    castro_clip_id,
    fmt_duration,
    locate_span,
    match_slug,
    normalize,
    quote_from_span,
)


def test_castro_clip_id_handles_duplicate_exports() -> None:
    assert castro_clip_id("castro-clip-449762921244.MOV") == "449762921244"
    assert castro_clip_id("castro-clip-449762921244 2.MOV") == "449762921244"
    assert castro_clip_id("v12044gd0000d81kd9nog65kaulgaha0.MOV") is None  # tiktok stray


def test_match_slug_covers_old_and_new_show_names() -> None:
    assert match_slug("The AI Daily Brief (Formerly The AI Breakdown): AI News") == "ai-daily-brief"
    assert match_slug("Hard Fork") == "hard-fork"
    assert match_slug("The Vergecast: Ad-Free Edition") is None


def test_locate_span_finds_head_despite_punctuation_drift() -> None:
    transcript = (
        "Welcome back to the show. " * 20
        + "OpenAI just released a new report breaking down the six core ways "
        + "businesses are using AI today, they call them use case primitives. "
        + "More filler content here. " * 20
    )
    clip = ("OpenAI just released a new report breaking down the six core ways "
            "businesses are using AI today. They call them use case primitives.")
    span = locate_span(clip, transcript)
    assert span is not None
    assert span["head"].startswith("openai just released")
    assert span["tail"] is not None


def test_locate_span_refuses_unrelated_text() -> None:
    assert locate_span("totally unrelated words about gardening tulips daffodils "
                       "watering cans and sunshine every single day", "x " * 500) is None
    assert locate_span("too short", "whatever transcript") is None


def test_quote_caps_long_clips_with_ellipsis() -> None:
    quote = quote_from_span("word " * 300)
    assert "[…]" in quote and len(quote) < 600
    assert quote_from_span("short clip text") == "short clip text"


def test_anchor_url_strips_dashes() -> None:
    url = anchor_url("3780501e-f950-81c9-a3e3-eca7f1162c9d", "37c0501e-f950-8119-ab65-e0bfddf093f5")
    assert url == ("https://www.notion.so/3780501ef95081c9a3e3eca7f1162c9d"
                   "#37c0501ef9508119ab65e0bfddf093f5")


def test_normalize_and_duration() -> None:
    assert normalize("Hello,   World! It's——fine.") == "hello world it s fine"
    assert fmt_duration(58) == "0:58"
    assert fmt_duration(153) == "2:33"

"""Sponsor detection: roster parsing, cue windows, and the precedence rule.

Every HTML fixture here is the real markup shape of a stored `episodes.raw_content`
description, copied from Neon on 2026-09-02 and trimmed. The AI Daily block changed
form several times across two years and each variant broke a naive parser, so they are
pinned individually rather than represented by one tidy example.

Hermetic: no database, no network.
"""

import json

import pytest

from pipeline.scrapers.ai_daily.sponsors import (
    SPONSOR_CUES,
    SPONSOR_WINDOW_TRAIL_CHARS,
    SponsorWindow,
    named_in_window,
    Sponsor,
    SponsorVerdict,
    apply_sponsor_verdict,
    bounded_edit_distance,
    classify_sponsor,
    locate_snippet,
    names_match,
    normalize_text_for_matching,
    parse_sponsor_roster,
    roster_from_raw_content,
    sponsor_windows,
    squash_name,
)

# The 2026 form: each sponsor bolded, separators drifting in and out of the <strong>,
# anchor text padded with U+2060 WORD JOINER runs.
MODERN_BLOCK = (
    "<p>In the headlines: data center politics.</p>"
    "<p><strong>NEXT COHORT - Executive Agent Leadership - </strong>Returns in September -- "
    '<a href="https://training.besuper.ai/">⁠⁠https://training.besuper.ai/⁠⁠</a></p>'
    "<p><strong>Brought to you by:</strong></p>"
    "<p><strong>KPMG</strong> – Research from KPMG and UT Austin shows the highest-impact "
    'AI users treat AI like a reasoning partner. <a href="https://kpmg.com/us/Sophisticated">'
    "⁠⁠https://kpmg.com/us/Sophisticated⁠⁠</a></p>"
    "<p><strong>Harbor - </strong>Invest in the AI ecosystem. "
    '<a href="https://www.harborcapital.com/aidaily">⁠x⁠</a></p>'
    "<p><strong>Hyperagent </strong>-<strong> </strong>Hire a fleet of always-on agents. "
    '<a href="https://hyperagent.com/aidailybrief">⁠x⁠</a></p>'
    "<p><strong>Rackspace Technology-</strong> One accountable partner "
    '<a href="https://www.rackspace.com/">⁠x⁠</a></p>'
    "<p><strong>Robots &amp; Pencils</strong> - Cloud-native AI solutions "
    '<a href="https://robotsandpencils.com/">⁠x⁠</a></p>'
    "<p>The AI Daily Brief helps you understand the most important news in AI.</p>"
    "<p>Subscribe to the newsletter: https://aidailybrief.beehiiv.com/</p>"
)

# The 2025 form: no <strong> anywhere, an en dash on one entry and a hyphen on the next,
# and one sponsor named by a whole phrase rather than a brand.
UNBOLDED_BLOCK = (
    "<p>Today's news.</p><p><strong>Brought to you by:</strong></p>"
    '<p>KPMG – Discover how AI is transforming possibility. <a href="https://www.kpmg.us/AIpodcasts">x</a></p>'
    '<p>Blitzy.com - Go to <a href="https://blitzy.com/">x</a> to build enterprise software</p>'
    "<p>Vanta - Simplify compliance - ⁠⁠https://vanta.com/nlw</p>"
    '<p>The Agent Readiness Audit from Superintelligent - Go to <a href="https://besuper.ai/ ">x</a></p>'
    "<p>The AI Daily Brief helps you understand the most important news in AI.</p>"
)

# The 2024 form: PLAIN TEXT, no markup at all, newline-delimited, header on its own line.
PLAINTEXT_BLOCK = (
    "This episode delves into the escalating tech war.\n"
    "Today's Episode Brought to You By:\n"
    "Plumb - Build, test, and deploy AI features with confidence - https://useplumb.com/\n"
    "ABOUT THE AI BREAKDOWN\n"
    "The AI Breakdown helps you understand the most important news in AI.\n"
    "Join the community: bit.ly/aibreakdown\n"
)

# Culture Gabfest: the phrase inside an article title the panel is discussing. There are
# 117 such episodes and NOT ONE is a sponsor block — a substring match invents a roster
# for a show that has none.
GABFEST_FALSE_POSITIVE = (
    "<p>The trio dissected a deftly reported package from Bloomberg, “The Second Trump "
    "Presidency, Brought to You by YouTubers.” Also, we’re looking for a new Production "
    "Assistant! Endorsements: Dan: Playworld by Adam Ross.</p>"
)


# --------------------------------------------------------------------------
# parse_sponsor_roster
# --------------------------------------------------------------------------

def test_parses_the_modern_bolded_block() -> None:
    roster = parse_sponsor_roster(MODERN_BLOCK)
    assert [s.name for s in roster] == [
        "KPMG", "Harbor", "Hyperagent", "Rackspace Technology", "Robots & Pencils",
    ]
    assert roster[0].url == "https://kpmg.com/us/Sophisticated"
    # The word-joiner padding lives in the anchor TEXT; the href is clean, so take it.
    assert "⁠" not in (roster[2].url or "")


def test_the_host_promo_above_the_header_is_not_a_sponsor() -> None:
    """"NEXT COHORT - Executive Agent Leadership" sits in its own paragraph BEFORE the
    header. Only what follows the header is the roster."""
    assert not any("Executive Agent" in s.name for s in parse_sponsor_roster(MODERN_BLOCK))


def test_the_boilerplate_tail_is_not_a_sponsor() -> None:
    names = {s.name for s in parse_sponsor_roster(MODERN_BLOCK)}
    assert not any(n.lower().startswith(("the ai daily brief", "subscribe")) for n in names)


def test_parses_the_unbolded_2025_block() -> None:
    roster = parse_sponsor_roster(UNBOLDED_BLOCK)
    assert [s.name for s in roster] == [
        "KPMG", "Blitzy.com", "Vanta", "The Agent Readiness Audit from Superintelligent",
    ]
    # Vanta's entry has a bare URL rather than an anchor.
    assert roster[2].url == "https://vanta.com/nlw"


def test_parses_the_plaintext_2024_block() -> None:
    """Five AI Daily episodes are plain text with newline-delimited entries; without the
    newline as a block boundary they parsed to an empty roster while naming a sponsor."""
    roster = parse_sponsor_roster(PLAINTEXT_BLOCK)
    assert [s.name for s in roster] == ["Plumb"]
    assert roster[0].url == "https://useplumb.com/"


def test_a_title_containing_the_phrase_is_not_a_roster() -> None:
    """The Gabfest guard: the header only counts when it ENDS its own block."""
    assert parse_sponsor_roster(GABFEST_FALSE_POSITIVE) == []


def test_a_description_without_the_block_has_no_roster() -> None:
    assert parse_sponsor_roster("<p>Hard Fork talks about AI.</p>") == []
    assert parse_sponsor_roster("") == []
    assert parse_sponsor_roster(None) == []


def test_call_to_action_verbs_are_stripped_not_kept() -> None:
    """Some entries are nothing but linked CTA text; the brand only survives the strip."""
    block = (
        "<p><strong>Brought to you by:</strong></p>"
        '<p><a href="https://agntcy.org/">Visit AGNTCY.org</a></p>'
        '<p><a href="https://outshift.cisco.com/">Visit Outshift Internet of Agents</a></p>'
    )
    assert [s.name for s in parse_sponsor_roster(block)] == [
        "AGNTCY.org", "Outshift Internet of Agents",
    ]


def test_prose_is_rejected_as_a_sponsor_name() -> None:
    """A pitch paragraph with no separator would otherwise become a roster name, and
    "Is your enterprise ready for the future of agentic AI?" token-matches a real entity
    called "Agentic AI" — labelling genuine coverage as advertising."""
    block = (
        "<p><strong>Brought to you by:</strong></p>"
        "<p>Is your enterprise ready for the future of agentic AI?</p>"
        '<p>Section - Workforce transformation <a href="https://sectionai.com/">x</a></p>'
    )
    assert [s.name for s in parse_sponsor_roster(block)] == ["Section"]


def test_a_sponsor_without_a_link_gets_a_null_url_not_a_guess() -> None:
    block = "<p><strong>Brought to you by:</strong></p><p><strong>Plumb</strong> - No link here</p>"
    roster = parse_sponsor_roster(block)
    assert roster == [Sponsor(name="Plumb", url=None)]


def test_roster_from_raw_content_reads_the_taddy_payload() -> None:
    raw = json.dumps({"provider": "taddy", "description": MODERN_BLOCK})
    assert [s.name for s in roster_from_raw_content(raw)][0] == "KPMG"


def test_roster_from_raw_content_tolerates_non_json() -> None:
    """raw_content is TEXT, not JSONB: SOP and TAL store plain scraped text in it, so
    json.loads must be allowed to fail without taking the run with it."""
    assert roster_from_raw_content("Rich Rolls scraped page text") == []
    assert roster_from_raw_content(None) == []
    assert roster_from_raw_content(json.dumps(["not", "a", "dict"])) == []


# --------------------------------------------------------------------------
# names_match
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "candidate,roster_name",
    [
        ("Blitzy", "Blitzy"),
        ("Robots and Pencils", "Robots & Pencils"),          # ampersand spelled out
        ("Blitzi", "Blitzy"),                                 # transcription near-miss
        ("Super Intelligent", "Superintelligent"),            # spacing
        ("Blitzy", "Blitzy.com"),                             # domain suffix
        ("Superintelligent", "The Agent Readiness Audit from Superintelligent"),
        ("Assembly AI voice agent API", "AssemblyAI"),        # brand opens the phrase
    ],
)
def test_names_that_should_match(candidate, roster_name) -> None:
    assert names_match(candidate, roster_name)


@pytest.mark.parametrize(
    "candidate,roster_name",
    [
        # Both real, distinct entities in this database — 4 characters, so neither the
        # edit-distance nor the containment rule may merge them.
        ("Rovo", "Robo"),
        # Found by hand-checking the live disagreements: a substring rule matched
        # "Intel" inside "superINTELligent" and tagged an Intel news mention as an ad.
        ("Intel", "The Agent Readiness Audit from Superintelligent"),
        # And "Vanta" inside "adVANTAge".
        ("Trump Can Keep America's AI Advantage", "Vanta"),
        ("OpenAI", "Blitzy"),
        ("", "Blitzy"),
        ("Blitzy", ""),
    ],
)
def test_names_that_must_not_match(candidate, roster_name) -> None:
    assert not names_match(candidate, roster_name)


def test_squash_name_reduces_to_comparable_form() -> None:
    assert squash_name("Robots & Pencils") == "robotsandpencils"
    assert squash_name("Rackspace Technology-") == "rackspacetechnology"
    assert squash_name("") == ""


def test_bounded_edit_distance_gives_up_past_the_budget() -> None:
    assert bounded_edit_distance("blitzy", "blitzi", 1) == 1
    assert bounded_edit_distance("blitzy", "openai", 1) == 2  # budget + 1
    assert bounded_edit_distance("abc", "abc", 0) == 0


# --------------------------------------------------------------------------
# sponsor_windows
# --------------------------------------------------------------------------

TRANSCRIPT = (
    "Welcome back to the AI Daily Brief. " + ("Editorial news about model releases. " * 40)
    + "Today's episode is brought to you by HyperAgent, where you run fleets of agents. "
    "Claim your $1,000 in inference at hyperagent.com/slash AI Daily Brief. "
    + ("More editorial analysis of the funding round. " * 40)
)


def test_windows_cover_the_cue_and_the_read_that_follows() -> None:
    normalized = normalize_text_for_matching(TRANSCRIPT)
    windows = sponsor_windows(TRANSCRIPT)
    assert windows
    cue_at = normalized.find("brought to you by")
    assert any(w.start <= cue_at < w.end for w in windows)
    # The ad copy after the cue is inside the window; the editorial run before it is not.
    assert any(w.start <= normalized.find("hyperagent.com") < w.end for w in windows)
    assert not any(w.start <= 0 < w.end for w in windows)


def test_windows_are_merged_and_sorted() -> None:
    windows = sponsor_windows(TRANSCRIPT)
    assert windows == sorted(windows)
    for previous, following in zip(windows, windows[1:]):
        assert previous.end < following.start  # overlapping spans merged, not repeated


def test_windows_offsets_index_the_normalized_text() -> None:
    """Offsets must index normalize_text_for_matching(text), not the raw string — a
    stored context_snippet has already had its line breaks collapsed, so locating it in
    raw text fails on every multi-line quote."""
    raw = "Intro.\n\n  Today's sponsor   is\nVanta.  " + ("filler. " * 50)
    normalized = normalize_text_for_matching(raw)
    (window,) = sponsor_windows(raw)
    assert "today's sponsor is vanta." in normalized[window.start:window.end]


def test_no_windows_without_a_cue() -> None:
    assert sponsor_windows("Just an ordinary episode about model releases.") == []
    assert sponsor_windows("") == []
    assert sponsor_windows(None) == []


def test_every_cue_is_lowercase_and_matchable() -> None:
    """normalize_text_for_matching lowercases, so an uppercase cue could never fire."""
    for cue in SPONSOR_CUES:
        assert cue == cue.lower(), cue


# --------------------------------------------------------------------------
# locate_snippet
# --------------------------------------------------------------------------

def test_locate_snippet_survives_collapsed_whitespace() -> None:
    normalized = normalize_text_for_matching("Line one.\n\n   Line   two about Vanta.")
    assert locate_snippet(normalized, "Line two about Vanta.") is not None


def test_locate_snippet_falls_back_to_the_opening_of_a_long_quote() -> None:
    normalized = normalize_text_for_matching("A" * 10 + "Blitzy orchestrates thousands of agents that reason across your code base.")
    snippet = "Blitzy orchestrates thousands of agents that reason across your … elided tail"
    assert locate_snippet(normalized, snippet) is not None


def test_locate_snippet_returns_none_rather_than_guessing() -> None:
    assert locate_snippet("some transcript text", "a quote that is not present here") is None
    assert locate_snippet("", "anything") is None
    assert locate_snippet("text", None) is None


# --------------------------------------------------------------------------
# classify_sponsor — precedence
# --------------------------------------------------------------------------

def _mention(**overrides):
    base = {
        "canonical_name": "HyperAgent",
        "mention_text": "HyperAgent",
        "context_snippet": "Today's episode is brought to you by HyperAgent, where you run fleets of agents.",
        "is_editorial": True,
    }
    base.update(overrides)
    return base


def test_roster_beats_phrase_and_model() -> None:
    roster = [Sponsor("HyperAgent", "https://hyperagent.com/")]
    verdict = classify_sponsor(
        _mention(), roster, sponsor_windows(TRANSCRIPT), transcript_text=TRANSCRIPT
    )
    assert verdict == SponsorVerdict(True, "roster", "HyperAgent")


def test_a_roster_match_needs_no_cue_nearby() -> None:
    """Measured 2026-09-02: requiring a nearby cue was wrong 51 times out of 63, because
    this host reads mid-roll ads with no verbal marker ("Blitzy is driving over 5x
    engineering velocity…" sits 13,936 characters from the nearest cue)."""
    transcript = "Blitzy is driving over 5x engineering velocity for large-scale enterprises."
    verdict = classify_sponsor(
        _mention(canonical_name="Blitzy", mention_text="Blitzy", context_snippet=transcript),
        [Sponsor("Blitzy", None)],
        sponsor_windows(transcript),
        transcript_text=transcript,
    )
    assert verdict.is_sponsor and verdict.source == "roster"


def test_phrase_beats_model_when_no_roster_matches() -> None:
    verdict = classify_sponsor(
        _mention(), [Sponsor("SomeoneElse", None)], sponsor_windows(TRANSCRIPT),
        transcript_text=TRANSCRIPT,
    )
    assert verdict.is_sponsor and verdict.source == "phrase"
    assert verdict.matched in SPONSOR_CUES


def test_model_flag_is_the_last_resort() -> None:
    """The only signal available for a show with neither a roster nor a cue."""
    verdict = classify_sponsor(
        _mention(context_snippet="A completely unrelated quote.", is_editorial=False),
        [], [], transcript_text="Nothing sponsor-shaped in here at all.",
    )
    assert verdict == SponsorVerdict(True, "model", None)


def test_editorial_stays_editorial() -> None:
    verdict = classify_sponsor(
        _mention(canonical_name="Gemini", mention_text="Gemini",
                 context_snippet="Gemini models were a distant second for coding this year."),
        [Sponsor("Blitzy", None)], [], transcript_text="Gemini models were a distant second.",
    )
    assert verdict == SponsorVerdict(False, None, None)


def test_a_short_snippet_cannot_ride_a_window() -> None:
    """"OpenAI" appears in ad copy too; a snippet needs enough text to be about one
    thing before its position counts as evidence."""
    verdict = classify_sponsor(
        _mention(canonical_name="OpenAI", mention_text="OpenAI", context_snippet="HyperAgent"),
        [], sponsor_windows(TRANSCRIPT), transcript_text=TRANSCRIPT,
    )
    assert not verdict.is_sponsor


def test_verdicts_apply_to_a_mention_with_null_for_editorial() -> None:
    editorial = apply_sponsor_verdict({}, SponsorVerdict(False, None, None))
    assert editorial == {"is_editorial": True, "sponsor_source": None}

    ad = apply_sponsor_verdict({}, SponsorVerdict(True, "roster", "Blitzy"))
    assert ad == {"is_editorial": False, "sponsor_source": "roster"}


def test_classification_works_without_any_transcript() -> None:
    """Show-notes extractions have no transcript; the roster still decides."""
    verdict = classify_sponsor(_mention(), [Sponsor("HyperAgent", None)], [])
    assert verdict.is_sponsor and verdict.source == "roster"


# --------------------------------------------------------------------------
# precision rules: the cue list and the named-in-window requirement
# --------------------------------------------------------------------------

def test_call_to_action_cues_were_removed_deliberately() -> None:
    """A dry run over all 16,460 stored mentions on 2026-09-02 traced 459 of 747 phrase
    verdicts to ".com slash" and 52 to "use code". Both fire on ordinary speech and on
    the show's OWN promos ("go to patreon.com slash ai-dailybrief"), and the window then
    swallowed the editorial mentions beside them. The cue list is the sponsor formula.
    """
    for banned in (".com slash", "dot com slash", "use code", "promo code"):
        assert banned not in SPONSOR_CUES


def test_news_after_the_ad_break_is_not_advertising() -> None:
    """The real "Fable 5" case, and what the narrowed trail fixes.

    An ad break interrupts the episode, so the news that FOLLOWS it is adjacent, not
    advertised. With the old 900-character trail this sentence sat inside the sponsor
    window and was tagged as advertising; at 200 it falls outside, which is the whole
    reason the sweep chose the narrow end.
    """
    transcript = (
        "Today's sponsor is Vanta, which gets you audit-ready fast and keeps you secure. "
        + ("The panel returns to the main story of the day. " * 6)
        + "The United States issued an export control directive to suspend access to Fable 5."
    )
    verdict = classify_sponsor(
        {
            "canonical_name": "Fable 5",
            "mention_text": "Fable 5",
            "context_snippet": "The United States issued an export control directive to suspend access to Fable 5.",
            "is_editorial": True,
        },
        [],
        sponsor_windows(transcript),
        transcript_text=transcript,
    )
    assert verdict == SponsorVerdict(False, None, None)


def test_being_inside_a_window_is_not_enough_without_the_name() -> None:
    """The other precision rule, isolated: a mention squarely inside the read but whose
    entity is never named in it is not part of the advertisement."""
    transcript = (
        "Today's sponsor is Vanta, which gets you audit-ready fast. "
        "Rumors swirled that OpenAI's latest model, codenamed Astro, was being prepared."
    )
    windows = sponsor_windows(transcript)
    normalized = normalize_text_for_matching(transcript)
    snippet = "Rumors swirled that OpenAI's latest model, codenamed Astro, was being prepared."
    # It really is inside the window — the name is the only thing keeping it editorial.
    assert any(w.start <= locate_snippet(normalized, snippet) < w.end for w in windows)
    verdict = classify_sponsor(
        {
            # The extractor canonicalized the spoken "Astro" to the product "Astra",
            # which the ad copy never says — so nothing in the read is about this entity.
            "canonical_name": "Astra",
            "mention_text": "Astra",
            "context_snippet": snippet,
            "is_editorial": True,
        },
        [],
        windows,
        transcript_text=transcript,
    )
    assert verdict == SponsorVerdict(False, None, None)


def test_the_advertised_product_in_the_same_window_is_still_tagged() -> None:
    """The other half of the same rule: the sponsor named IN the read must survive it."""
    transcript = (
        "Today's sponsor is Vanta, which gets you audit-ready fast and keeps you secure. "
        "The United States issued an export control directive to suspend access to Fable 5."
    )
    verdict = classify_sponsor(
        {
            "canonical_name": "Vanta",
            "mention_text": "Vanta",
            "context_snippet": "Today's sponsor is Vanta, which gets you audit-ready fast and keeps you secure.",
            "is_editorial": True,
        },
        [],
        sponsor_windows(transcript),
        transcript_text=transcript,
    )
    assert verdict.is_sponsor and verdict.source == "phrase"


def test_named_in_window_matches_across_spelling_differences() -> None:
    normalized = normalize_text_for_matching(
        "today's sponsor is robots and pencils, cloud-native ai solutions that power results"
    )
    window = (0, len(normalized))
    assert named_in_window(["Robots & Pencils"], normalized, window)
    assert named_in_window(["AssemblyAI"], normalize_text_for_matching(
        "brought to you by assembly ai, the best way to build voice ai apps"
    ), (0, 70))
    assert not named_in_window(["Fable 5"], normalized, window)


def test_named_in_window_ignores_names_too_short_to_prove_anything() -> None:
    """"AI" appearing in ad copy proves nothing about an entity called AI."""
    normalized = normalize_text_for_matching("today's sponsor is vanta, the ai compliance platform")
    assert not named_in_window(["AI"], normalized, (0, len(normalized)))


def test_the_window_trail_is_the_core_of_a_read_not_the_whole_break() -> None:
    """Swept against the labelled set on 2026-09-02: recall was FLAT at 86.2% for every
    trail from 150 to 600 while phrase verdicts grew from 46 to 164. Width buys no
    recall and only costs precision, so it sits just above the floor."""
    assert SPONSOR_WINDOW_TRAIL_CHARS <= 250


def test_named_in_window_will_not_match_mid_word() -> None:
    """The phrase path must hold the same line names_match rule 4 holds.

    A bare substring test over squashed text finds "Intel" inside "superINTELligent" and
    "Vanta" inside "adVANTAge" — the exact collisions that labelled two real editorial
    mentions as advertising when the roster matcher had this bug. The two matchers must
    not drift apart.
    """
    normalized = normalize_text_for_matching(
        "today's sponsor is the agent readiness audit from superintelligent, "
        "and separately trump can keep america's ai advantage"
    )
    window = (0, len(normalized))
    assert not named_in_window(["Intel"], normalized, window)
    assert not named_in_window(["Vanta"], normalized, window)
    # …while the spelling tolerance the other tests pin is untouched.
    assert named_in_window(["Superintelligent"], normalized, window)


# --------------------------------------------------------------------------
# the naming test is anchored to the cue (independent review, 2026-09-02)
# --------------------------------------------------------------------------

def test_a_name_only_in_the_lead_is_the_previous_sentence_not_the_ad() -> None:
    """The lead exists so a snippet opening a few words before the cue still lands in
    the window. It must not let the PREVIOUS sentence's subject satisfy the naming test —
    that is how Slack and Telegram were pulled in from the copy before an ad break.
    """
    transcript = (
        "The team shipped a Telegram integration this week and it works well. "
        "Today's sponsor is Vanta, which gets you audit-ready fast."
    )
    windows = sponsor_windows(transcript)
    verdict = classify_sponsor(
        {
            "canonical_name": "Telegram",
            "mention_text": "Telegram",
            "context_snippet": "The team shipped a Telegram integration this week and it works well.",
            "is_editorial": True,
        },
        [], windows, transcript_text=transcript,
    )
    assert verdict == SponsorVerdict(False, None, None)


def test_a_bill_sponsored_by_a_senator_is_not_an_advertisement() -> None:
    """"sponsored by" has a non-advertising sense. Anchoring the naming test to the cue
    is what separates them: the bill's name precedes the phrase, the sponsor's follows."""
    transcript = (
        "The Bipartisan Framework for US AI Act was sponsored by Richard Blumenthal "
        "and Josh Hawley, and it lays out a licensing regime."
    )
    verdict = classify_sponsor(
        {
            "canonical_name": "Bipartisan Framework for US AI Act",
            "mention_text": "Bipartisan Framework for US AI Act",
            "context_snippet": (
                "The Bipartisan Framework for US AI Act was sponsored by Richard "
                "Blumenthal and Josh Hawley, and it lays out a licensing regime."
            ),
            "is_editorial": True,
        },
        [], sponsor_windows(transcript), transcript_text=transcript,
    )
    assert verdict == SponsorVerdict(False, None, None)


def test_a_cue_inside_a_quoted_headline_opens_no_window() -> None:
    """Culture Gabfest's panellists discuss articles BY TITLE. This is the real ep-3737
    text; "Brought to You by YouTubers" is a headline being read aloud, not a handoff to
    an advertiser. The transcript-side twin of the roster's _header_ends_block guard.
    """
    text = (
        "The trio dissected a deftly reported package from Bloomberg, \u201cThe Second "
        "Trump Presidency, Brought to You by YouTubers.\u201d Also, we are looking for a "
        "new Production Assistant."
    )
    assert sponsor_windows(text) == []


def test_an_apostrophe_does_not_open_a_quoted_span() -> None:
    """Only double quotes count. Half the cues contain "today's" — treating an
    apostrophe as a quote delimiter would make them unmatchable."""
    text = "Today's sponsor is Vanta, and it's the platform that's built for teams."
    assert sponsor_windows(text)


def test_window_carries_the_cue_boundary_and_merging_keeps_the_earliest() -> None:
    windows = sponsor_windows(TRANSCRIPT)
    for w in windows:
        assert isinstance(w, SponsorWindow)
        assert w.start <= w.name_from <= w.end
        assert w.cue in SPONSOR_CUES

    merged = sponsor_windows(
        "Today's sponsor is Vanta. It is brought to you by Vanta as well."
    )
    assert len(merged) == 1
    # The naming region of a merged window starts after its FIRST cue.
    assert merged[0].cue == "today's sponsor"

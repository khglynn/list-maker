# AI Daily Type Taxonomy (V1, Plain English)

*Created: 2026-02-06*

This replaces the ambiguous "tool vs product" split and adds broader account/content coverage.

## 1) Recommended Locked Types (Launch Set)

Use these exact types at launch:

1. `software_product`
Meaning: a software/app/platform used by people.
Examples from transcripts: Claude Code, Claude Cowork, Codex app, Cursor, Xcode, ChatGPT Health.

2. `model`
Meaning: an AI model family/version.
Examples: Gemini, Sora, GPT 5.5, Opus 4.5, DeepSeek.

3. `benchmark`
Meaning: eval benchmark name or evaluation framework.
Examples: GPQA, LM Arena context (if discussed as eval framework).

4. `report`
Meaning: named report or analysis publication.
Examples: "AI as a Healthcare Ally..."

5. `survey`
Meaning: survey instrument or survey result set.
Examples: AI Usage Pulse Survey, PwC CEO survey.

6. `paper`
Meaning: research paper or preprint.
Examples: (future episodes likely include arXiv papers).

7. `account`
Meaning: a creator/account identity on any platform (not just X).
Examples: Sam Altman (when referenced as posting), named analysts/commentators.
Notes: this should hold platform in metadata (`x`, `linkedin`, `youtube`, etc.).

8. `social_post`
Meaning: a specific post/thread/message from an account.
Examples: quoted "tweeted..." statements from episodes.

9. `blog_post`
Meaning: article/post on web/newsletter/blog.
Examples: "wrote a piece called ...", "blog post called ..."

10. `organization`
Meaning: company, lab, university, media org, nonprofit.
Examples: OpenAI, Anthropic, Google, Apple, LM Arena.

11. `person`
Meaning: individual people.
Examples: Jensen Huang, Fiji Simo, Andrej Karpathy.

12. `other`
Meaning: unresolved/unknown bucket pending review.

## 2) Why `software_product` (not tool + product)

- In practice, transcript language mixes these concepts.
- Splitting them early causes inconsistent tagging and extra review work.
- We can add finer subtypes later using facts (for example: coding_tool, healthcare_app, workflow_platform).

## 3) Facts/Flags Dictionary (Locked at Launch)

Use these as standardized fact keys:

- `modality`: `llm`, `image`, `video`, `audio`, `multimodal`
- `contains_survey_questions`: `true|false`
- `platform`: `x`, `linkedin`, `youtube`, `substack`, `github`, `web`, etc.
- `is_editorial`: `true|false` (sponsor/ad filtering)
- `source_quality`: `auto_verified|human_verified|unverified|rejected`

## 4) New Type / New Fact Governance

When cron ingests new episodes:

1. Try to map to existing type + existing entity.
2. If uncertain, store as candidate with status `needs_review`.
3. If not mappable, store as `other` + candidate proposal.
4. Human review approves:
   - alias to existing entity
   - new entity under existing type
   - (rare) new type creation

Rule: new **types** should require repeated evidence + manual approval (not auto-created by model).

## 5) URL Policy (Corrected)

Do not assume episode pages contain useful reference links.

For each important mention (report/social_post/blog_post):

1. Extract quote/context from transcript.
2. Run targeted lookup for candidate URLs.
3. Save URL with verification status.
4. Treat only `human_verified`/`auto_verified` links as query-ready.

## 6) What We Saw in the 25-Episode Probe

A quick heuristic pass across 25 transcripts found strong signal for:

- `software_product`: Claude Code, Codex app, Cursor, Xcode, ChatGPT Health
- `model`: Gemini, Opus 4.5, DeepSeek, Sora
- `organization`: OpenAI, Anthropic, Google, Apple
- `survey` and `report`: AI Usage Pulse Survey, AI as a Healthcare Ally
- `benchmark`: GPQA, LM Arena references

It also produced lots of noisy account/person candidates, which confirms we need:
- strict type definitions
- confidence thresholds
- review queue before finalizing uncertain entities.

## 7) Test-Run Plan (Before Final Schema Lock)

1. Run extraction on 25 transcripts in 5-episode batches using this locked taxonomy.
2. Measure:
   - auto-link rate to existing entities
   - unknown rate (`other`)
   - review queue size
   - false positive rate in sampled QA
3. After each 5-episode batch, adjust prompt/rules, then run next fresh batch.
4. Adjust only after review (not ad hoc during extraction).

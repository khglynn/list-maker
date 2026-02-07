# Codex Quick Reference (Plain English)

*Created: 2026-02-06*

## 1) Context Compaction

- In the Codex app, compaction is automatic.
- You do not need to manually trigger it in normal use.
- When context gets large, Codex compresses older parts of the conversation and keeps working.
- In Codex CLI docs, there is a `/compact` slash command. In the app command list, this is not shown.

## 2) "How do you remember my preferences?"

Codex does **not** automatically remember everything across all chats forever.

What works reliably:
- Put preferences in repo instruction files (`AGENTS.md`, `CLAUDE.md`).
- Keep a project memory note (this folder) with stable preferences and current plan.
- At the start of a new chat, remind Codex once: "Use help me mode from CLAUDE.md."

We already added this preference:
- `Communication Default` in `/Users/kevinhalladay-glynn/DevKev/personal/pod-lists/CLAUDE.md`

## 3) Codex vs Claude Code (practical differences)

- Both tools can edit files, run commands, and help with planning.
- Codex app has built-in context meter + automatic compaction.
- Codex app can isolate tasks using Worktrees (safer parallel work).
- Codex lets you approve reusable command prefixes for smoother repeated setup.
- Claude Code is often favored for command-heavy terminal workflows and subagent-style delegation.
- In both tools, the most reliable cross-session memory is project files (instructions + notes), not chat history alone.

## 4) What "parallel work" means in Codex

People usually mean one of these:

1. Parallel inside one project:
- Use separate Worktrees so tasks run side-by-side without touching the same files.
- Example: one thread handles transcript ingestion while another designs query tables.

2. Parallel in the cloud:
- Start a cloud task for longer work while you continue local work in another thread.

Why this matters for solo projects:
- Less risk of breaking your stable work.
- Faster progress when tasks are independent.

## 5) Why use Codex for this project (non-dev version)

- Better safety while experimenting (isolated workspaces/branches).
- Easier "draft vs stable" workflow before merging to `main`.
- Good fit for recurring tasks/automations once pipeline steps stabilize.
- Works well with shared tools (like Neon + Firecrawl MCP) across app/CLI/IDE.

## 6) Best workflow for this project

- Keep one short "current plan" note in `codex-notes/`.
- Keep one short "what changed today" note after each major session.
- Store secrets only in local env files (already done), never in committed docs.

## 7) What to do when context gets high

1. Ask for a checkpoint summary.
2. Let Codex auto-compact.
3. Start the next prompt with: "Continue from codex-notes + latest checkpoint."

## Sources checked (2026-02-06)

- Codex app command docs: https://developers.openai.com/codex/app/commands
- Codex CLI prompting docs (`/compact`): https://developers.openai.com/codex/prompting
- Codex app features: https://developers.openai.com/codex/app/features/
- Codex app worktrees: https://developers.openai.com/codex/app/worktrees

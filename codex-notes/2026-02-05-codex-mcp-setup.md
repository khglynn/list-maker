# Codex MCP Setup (Neon + Firecrawl)

*Created: 2026-02-05*
*Last updated: 2026-02-05*

Use these commands in your terminal to enable the same MCP capabilities in Codex that you use in Claude.

## 1) Verify required env vars exist

```bash
for k in FIRECRAWL_API_KEY NEON_API_KEY; do
  if [ -n "${(P)k}" ]; then
    echo "$k is set"
  else
    echo "$k is missing"
  fi
done
```

## 2) Add Firecrawl MCP

```bash
codex mcp add firecrawl --env FIRECRAWL_API_KEY="$FIRECRAWL_API_KEY" -- npx -y firecrawl-mcp
```

## 3) Add Neon MCP

```bash
codex mcp add neon --env NEON_API_KEY="$NEON_API_KEY" -- npx -y @neondatabase/mcp-server-neon start "$NEON_API_KEY"
```

## 4) Confirm

```bash
codex mcp list
```

Expected: `playwright`, `firecrawl`, and `neon` show as configured/enabled.

## 5) Important for this project

- Keep project DB vars available to app/scripts too:
  - `DATABASE_URL`
  - `NEON_DATABASE_URL` (set same value for now to avoid script mismatch)

# MCP Health and Permission Review

## Capability-based MCP parity

Atlas parity is capability-based, not server-count-based.  
Different clients may use different MCP inventories as long as required
capabilities are present with equivalent security boundaries.

Primary Atlas target servers are healthy:

- codebase-memory
- github
- playwright
- context7

## Permission classes

| Server | Classifications | Notes |
| --- | --- | --- |
| codebase-memory | `READ_ONLY`, `REPO_READ`, `NETWORK` | code intelligence queries/indexing against local workspace |
| github | `AUTHENTICATED`, `NETWORK`, `EXTERNAL_WRITE` (potential by toolset) | token is injected at runtime by wrapper; no token in tracked config |
| playwright | `NETWORK`, `BROWSER_AUTOMATION`, `LOCAL_FILE_READ` | configured headless, localhost default |
| context7 | `NETWORK`, `READ_ONLY` | external documentation retrieval |
| filesystem (Kimi-only pre-existing) | `REPO_WRITE`, `READ_ONLY` depends on tool call | pre-existing scope may be broad; keep roots constrained to Atlas development paths per platform |
| memory (Kimi-only pre-existing) | `LOCAL_PERSISTENCE` | non-canonical convenience memory |

## Least-privilege notes

- Copilot/VS Code/Cursor configs were kept to the four Atlas-target MCPs only.
- Filesystem MCP was not added to those clients because native workspace
  tooling is already sufficient.
- Pre-existing Kimi filesystem server is broader than Atlas least-privilege
  guidance; recommended follow-up is narrowing roots to Atlas-specific paths.

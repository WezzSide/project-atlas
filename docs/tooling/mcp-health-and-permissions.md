# MCP Health and Permission Review

## Enumerated MCP servers

Total configured servers detected across user configs: `17`  
Healthy (command-resolvable): `17`

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
| filesystem (Kimi-only pre-existing) | `REPO_WRITE`, `READ_ONLY` depends on tool call | pre-existing scope is broad (`/home/gebruiker`), not changed by this bootstrap |
| memory (Kimi-only pre-existing) | `LOCAL_PERSISTENCE` | non-canonical convenience memory |

## Least-privilege notes

- Copilot/VS Code/Cursor configs were kept to the four Atlas-target MCPs only.
- Filesystem MCP was not added to those clients because native workspace
  tooling is already sufficient.
- Pre-existing Kimi filesystem server is broader than Atlas least-privilege
  guidance; recommended follow-up is narrowing roots to Atlas-specific paths.

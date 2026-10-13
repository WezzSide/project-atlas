# Atlas Development Environment Bootstrap (Local Workstation)

Date: 2026-09-06  
Target repository: `B0LK13/project-atlas`

## Scope and safety

- Production Atlas runtime behavior was not modified.
- No secrets were written into tracked files.
- MCP configs use runtime token indirection (`gh auth token`) through a local wrapper.
- Backups were created before MCP/client configuration changes:
  - `/home/gebruiker/.copilot/session-state/c3462b7b-03f8-4687-be90-7ae98cf949a6/files/backup-20260906T183505Z/`

## Installation decision manifest

| Tool | Current state | Source | Decision | Why Atlas needs it |
|---|---|---|---|---|
| codebase-memory-mcp | present (`0.10.8`) | npm/local bin | ALREADY_PRESENT | persistent code intelligence and call-graph discovery |
| GitHub MCP server | present (`1.12.0`) | local binary | ALREADY_PRESENT | repository truth (PRs/issues/CI/metadata) |
| Playwright MCP | missing | npm `@playwright/mcp` | INSTALL | browser verification and UI evidence workflows |
| Context7 MCP | missing | npm `@upstash/context7-mcp` | INSTALL | current external API/documentation lookup |
| semgrep | missing | pipx (`semgrep`) | INSTALL | static security checks and unsafe-pattern detection |
| gitleaks | missing | official GitHub release | INSTALL | local secret scanning |
| trivy | missing | official GitHub release | INSTALL | dependency/container vulnerability scanning |
| syft | missing | official GitHub release | INSTALL | SBOM generation |
| grype | missing | official GitHub release | INSTALL | SBOM vulnerability scanning |
| actionlint | missing | Go install | INSTALL | GitHub Actions workflow validation |
| markdownlint-cli2 | missing | npm | INSTALL | docs/markdown quality checks |
| taplo | missing | official GitHub release | INSTALL | TOML lint/format validation |
| hyperfine | missing | apt (requires sudo) | SKIP | non-blocking optimization tool; needs owner install |

## MCP configuration applied

Configured files:

- `~/.copilot/mcp-config.json`
- `~/.config/Code/User/mcp.json`
- `~/.cursor/mcp.json`

Configured servers:

1. `codebase-memory` → `codebase-memory-mcp`
2. `github` → `/home/gebruiker/.local/bin/github-mcp-wrapper`
3. `playwright` → `npx -y @playwright/mcp@0.0.80 --headless`
4. `context7` → `npx -y @upstash/context7-mcp@4.0.5`

Notes:

- Filesystem MCP was not added to Copilot/VS Code/Cursor because native workspace file tooling is already available.
- Existing Kimi filesystem MCP remains configured outside this bootstrap and is not treated as Atlas canonical policy.

## Toolchain health snapshot

- Python: `3.14.4`
- uv: `0.12.10`
- Node: `v22.23.2`
- npm: `10.9.8`
- pnpm: `11.25.0`
- GitHub CLI: `2.46.0`
- Docker: `29.8.0`

Security/static-analysis:

- semgrep `1.176.1`
- gitleaks `8.30.1`
- trivy `0.74.0`
- syft `1.51.1`
- grype `0.118.0`
- actionlint `1.7.12`

Documentation/config lint:

- markdownlint-cli2 `0.23.2`
- taplo `0.10.0`

## Manual auth and owner decisions remaining

- GitHub MCP runtime auth requires a valid `gh auth login` session.
- Codebase Memory auto-installer failed to mutate all detected clients due local binary activation path constraints; manual per-client config is applied instead.
- `hyperfine` requires privileged package installation if desired.
- Merge protection/ruleset changes are intentionally out of scope and were not changed.

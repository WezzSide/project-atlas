# Windows Development Bootstrap (Tooling Lane)

Status: non-production developer tooling guidance for Windows hosts.

## Scope and boundaries

- Tooling lane only. No production Atlas runtime mutation.
- Use Python 3.12 for Windows CI-equivalent Atlas work.
- MCP tokens must be runtime-injected and never stored literally in tracked files.
- Codebase Memory is derived developer intelligence, not canonical merge authority.
- Governance skills remain single-source and cross-platform; do not duplicate
  role policy per platform.
- Role boundaries are platform-invariant (Ubuntu and Windows use the same
  authority model).

## Canonical paths (Windows)

- Copilot MCP config: `$env:USERPROFILE\.copilot\mcp-config.json`
- VS Code MCP config: `$env:APPDATA\Code\User\mcp.json`
- Cursor MCP config: `$env:USERPROFILE\.cursor\mcp.json`
- Preferred worktrees: `D:\atlas-worktrees\...`
- Temporary artifacts: `$env:TEMP\atlas-*`

## Bootstrap commands

```powershell
pwsh -File scripts/bootstrap-dev-tooling.ps1 -Check
pwsh -File scripts/bootstrap-dev-tooling.ps1 -DryRun
pwsh -File scripts/bootstrap-dev-tooling.ps1 -Install
pwsh -File scripts/check-windows-dev-health.ps1
```

`check-windows-dev-health.ps1` is a read-only probe across core platform tools,
required pinned security/dependency tools, and MCP bootstrap prerequisites. It
does not execute MCP tool calls and is not a substitute for native MCP E2E
validation.

The script is idempotent: it checks command presence before install attempts and
does not force upgrades.

Exit contract:

- `0`: healthy (`-Check`/`-DryRun`) or successful install (`-Install`)
- `3`: required tooling/config missing in `-Check`/`-DryRun`
- `4`: install attempted but one or more required items failed
- `10`: script hard failure

## Install policy

Preferred order:

1. Existing trusted installation
2. winget user-scope install
3. official installer where winget is unavailable
4. npm (for MCP npm packages)
5. pipx (for Python-based developer tools)

Avoid opaque bootstrap scripts and `curl|iex` patterns.

## Core MCP set for Windows parity

- codebase-memory
- github (runtime `gh auth token` indirection)
- playwright
- context7

Each selected server must pass startup plus one real tool call in the target
client. Equivalent capability coverage is accepted without requiring exact MCP
server-count parity across clients.

`context7` is optional in the manifest (`required=false`), so absence is
reported as optional and does not fail required parity.

`pip-audit` remains exact-pinned to `2.10.1` in manifest and bootstrap.

## Worktree collision checks (Codebase Memory)

Validate per-worktree key behavior across:

- main checkout path
- at least one alternate worktree path

Do not assume path-equivalent indexes across drive-letter case or junction paths.
If keys collide, require explicit per-worktree project configuration.

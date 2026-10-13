# PR705 Post-Merge Activation Checklist

Status: design/operations checklist for tooling activation after merge.

## Copilot instructions

- Repository instructions are read from `.github/copilot-instructions.md`.
- Keep `AGENTS.md` and `CLAUDE.md` as primary governance contracts.

## Atlas skills

- Role profiles live in `docs/agent-skills/`.
- Use these as lane contracts, not merge authorization receipts.

## Bootstrap script

- Run `scripts/bootstrap-dev-tooling.sh --check` first.
- Use `--dry-run` to preview deltas.
- Use `--install` only when explicit installation is intended.

## MCP clients

- Local configs are required for each client:
  - `~/.copilot/mcp-config.json`
  - `~/.config/Code/User/mcp.json`
  - `~/.cursor/mcp.json`
- GitHub MCP must continue runtime token injection (`gh auth token` wrapper), never literal tokens in config.

## Codebase Memory

- Index keying is path-derived; worktree collision risk was validated as low.
- Re-index each distinct checkout path after major branch/shape changes.
- Treat graph results as derived intelligence, never canonical governance evidence.

## Agents SDK lab

- Lab remains experimental at `experiments/agents_sdk/`.
- No production runtime authority, no merge authority, no secret material.

## Figma

- Integration status: manual auth required.
- Setup is tooling-only and must not resume paused design lanes.

## Kimi filesystem MCP

- Current root scope is broad (`/home/gebruiker`).
- Recommended least-privilege roots:
  - `/home/gebruiker/project-atlas`
  - `/home/gebruiker/project-atlas-worktrees`

## Merge guardian status

- Merge guardian artifacts in this PR are **design only**.
- Production merge enforcement remains owner-controlled and not activated by this PR.

# OpenAI Agents SDK Lab Preparation (Non-Production)

This repository bootstrap does not change production Atlas architecture.  
This file defines a safe experimental lane for future agent-orchestration work.

## Intended topology

`OWNER -> GOVERNOR -> {IMPLEMENTER, VERIFIER, RESEARCHER, DESIGNER} -> EVIDENCE_BROKER -> MERGE_GUARD`

## Boundaries

- Prototype only; no merge-policy authority.
- Keep all experiments isolated from production command paths.
- Use explicit fixture inputs and evidence receipts.

## Suggested setup

1. Create a dedicated experimental branch/worktree.
2. Create `experiments/agents_sdk/` for prototype code.
3. Install SDK only inside a local venv for that experiment.
4. Require explicit handoff receipts between prototype roles.

## Status

- `AGENTS_SDK_ENVIRONMENT = READY` (isolated lab path: `experiments/agents_sdk/`)
- Production integration remains owner-gated.

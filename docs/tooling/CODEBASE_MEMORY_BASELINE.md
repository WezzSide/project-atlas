# Codebase Memory Baseline — Project Atlas

Project key: `home-gebruiker-project-atlas`  
Status: `indexed` / `ready`

## INDEX_STATUS

- nodes: `22896`
- edges: `123694`
- parse-partial files: `1` (`WORKLOG.md` range `5174-5174`)
- intentionally not indexed directories include: `.git`, `docs`, `fixtures`, `tests/integration`, selected large subtrees

## ARCHITECTURE_QUERY

Top-level architecture snapshot reports:

- dominant language: Python (`982` files)
- secondary language: TypeScript (`44` files)
- key edge families: `DEFINES`, `CALLS`, `USAGE`, `WRITES`, `TESTS`, `IMPORTS`
- detected CLI entrypoint: `src/project_atlas/cli.py` (`project_atlas.cli.main`)

## CALL_GRAPH_QUERY

Discovery queries returned:

- CLI entrypoint: `src/project_atlas/cli.py` and orchestration CLI path.
- Ingestion flow anchors: `src/project_atlas/ingestion.py` (`_ingest`, `ingest`, manifest merge and source identity assertions).
- Protected-region handling: `src/project_atlas/protected_regions.py` (`validate_protected_markers`, human-region span logic).
- Control-plane entrypoints: `atlas-vault-documentation/agent_control/{doctor,preflight,postflight}.py`.
- Web Time Machine anchors: `apps/web/src/pages/production/TimeMachinePage.tsx`, `apps/web/src/hooks/useLiveTimeMachine.ts`.

## IMPACT_QUERY

Governance-related signals include:

- merge-sequence gate implementation path:
  `src/project_atlas/orchestration/sdk/merge_sequence_gate.py`
- related governance tests in:
  `tests/unit/test_merge_sequence_gate_d138.py`
  and additional orchestration governance suites.

## KNOWN_LIMITATIONS

- Codebase Memory output is derived developer intelligence, not canonical evidence.
- Ignore rules exclude intentionally sensitive/noisy areas by design.
- Partial parse on long operational logs can hide some symbols; verify claims in source before certifying.

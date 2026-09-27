# ADR-033 — Atlas Runner Fabric 001 (ephemeral GitHub Actions runner fabric)

**Status:** accepted for documentation / implementation complete, DEPLOYMENT PENDING (VPS-02 unreachable)
**Date:** 2026-09-26
**Work package:** AS-RUNNER-FABRIC-001
**Baseline:** MAIN `57a0a61d7c1b2410c96b5c26c507279d5f82c48d` / TREE `44e654882fc58c3ce59f11f9368a3a848170ba7a`
**Depends on:** GOVERNANCE.md (roles / exact-hash / stop boundaries), ADR-006 (repository governance baseline)

## Context

Project Atlas needs an execution fabric for bounded agent/CI workloads with
hard separation between execution and certification. Existing fleet docs
treat VPS-02 as an independent-verifier host; this milestone repurposes
VPS-02 as an execution node and moves independent verification to
GitHub-hosted runners for v1. Constraints: no inbound ports should be
required, jobs must be untrusted by default, evidence must be independent of
the executor's claims, and everything must remain within the repo's
governance vocabulary (`EXECUTOR_SUCCESS != VERIFIED`,
`PASS != MERGE AUTHORIZATION`, `NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`).

Live deployment could not be validated: VPS-02 is unreachable from the
build network at the TCP level (all three documented public IPs and the
Tailscale 100.x address time out). Preflight is therefore NOT_RUN; all
capacity figures in the operations docs are marked placeholders pending
real-host measurement.

## Decision

1. **Stdlib-only controller as a sibling deliverable under `infra/`.**
   `infra/atlas-runner/controller/` is Python 3.12 standard library only;
   it is not part of the `project_atlas` package and does not enter the
   main ruff/mypy scope (it has its own `pyproject.toml`). It reuses the
   `atlas_contracts` path guards via `PYTHONPATH`, matching the deploy
   wrapper's contract resolution.
2. **Filesystem/GitHub-poll trigger model — no inbound ports.** The
   controller polls the GitHub API for queued runs whose labels are a
   subset of `[self-hosted, linux, x64, atlas, executor]`. Admission
   control (label subset + capacity + dedup) decides; a queued run is a
   request, never an instruction. No webhook listener exists.
3. **Ephemeral JIT runner: one job, one worker.** Each admitted job gets
   exactly one worker container running actions/runner 2.337.0, hardened
   (`--cap-drop=ALL`, `no-new-privileges`, no host network, no
   `docker.sock`, forbidden mount prefixes, resource limits). The runner
   auto-deregisters after its single job and the container is destroyed.
   `max_concurrent_jobs = 1` bounds the blast radius on a small shared host.
4. **SQLite WAL state DB is the source of truth** for idempotency and
   crash recovery: dedup key (run, attempt), a fixed 15-state lifecycle
   graph with absorbing terminal states (unlisted transitions fail closed),
   and startup reconcile against Docker reality.
5. **Independent verification on a GitHub-hosted runner in v1.**
   `atlas-runner-verify.yml` runs on `ubuntu-latest` — a host-level trust
   boundary from VPS-02 — recomputing artifact SHA-256, validating evidence
   fields against the schema, checking runner identity, and enforcing
   `complete`-claim semantics before emitting a VERIFIED/REJECTED verdict.
   Same-host executor/verifier separation is explicitly rejected as NOT
   host-level independence.
6. **Privileged workflows are `workflow_dispatch`-only with a
   default-branch gate.** `atlas-agent-execute.yml` (agent execution) and
   `atlas-runner-deploy.yml` (deploy) have no `pull_request` /
   `pull_request_target` triggers. Deploy additionally enforces a
   job-level default-branch gate, a step-level re-assertion, a 40-hex
   ancestor-only revision check with env indirection, an `atlas-vps02`
   environment (required reviewers = human gate), and pinned-host-key SSH.
   Feature branches and PRs can never reach the deploy SSH key (§25-style
   protection). Executor-only paths are annotated
   `EXECUTOR_SUCCESS != VERIFIED`.
7. **Evidence schema as a new subsystem schema following repo conventions.**
   `infra/atlas-runner/schemas/execution-evidence.schema.json` (JSON Schema
   2020-12, `additionalProperties: false`) defines the 21-field per-
   execution record; controller config and worker task schemas sit beside
   it. Host-side evidence is VPS-02-produced and self-reported by
   construction; only the GitHub-hosted verifier's verdict carries
   independence weight.
8. **The Atlas adapter is schema + documentation, not code.** The runner
   fabric is an execution backend of Atlas, not a parallel control plane:
   Atlas supplies task bindings validated against
   `schemas/atlas-task-binding.schema.json` and owns all authority; the
   fabric owns isolation/execution/cleanup and returns evidence. The
   mapping to Atlas dispatch-record statuses is documented in
   `infra/atlas-runner/docs/ATLAS-INTERFACE.md`. No Atlas code changes are
   required in this milestone.

## Consequences

- `DEPLOYED = NO`, `LIVE E2E NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY` (network
  reachability, GitHub secret provisioning, Anthropic auth are all
  external). Capacity defaults in OPERATIONS.md are placeholders PENDING
  preflight.
- The independent verifier moved off VPS-02, creating documented tension
  with existing fleet docs; this ADR records the v1 choice, it does not
  resolve fleet policy.
- Every third-party workflow action is pinned to an immutable commit SHA;
  Anthropic auth uses the `ANTHROPIC_API_KEY` secret path of
  `anthropics/claude-code-action` (OIDC workload-identity federation is the
  documented upgrade, requiring Anthropic Console provisioning).
- Agent execution proposes changes only on a dedicated
  `atlas/agent-<run_id>-<attempt>` branch; no workflow merges anything.

## Non-decisions

- **Kubernetes / Actions Runner Controller / multi-host fleet:** out of
  scope for v1; the fabric is one controller on one host by design.
- **Windows deployment:** out of scope; the schemas are label-agnostic and
  forward-compatible only — no commitment is made.
- **GitHub App token provider:** not implemented; the fine-grained PAT is
  documented with exact minimal permissions, and the App swap is an
  internal `controller/github.py` upgrade with no schema/workflow change.

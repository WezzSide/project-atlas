# Atlas ↔ Runner Fabric Interface (AS-RUNNER-FABRIC-001)

The machine-consumable boundary between Project Atlas and the runner fabric.
Contract vocabulary: `EXECUTOR_SUCCESS != VERIFIED`, `EVIDENCE != AUTHORITY`,
`PASS != MERGE AUTHORIZATION`, `MODEL OUTPUT != AUTHORITY`.

## Positioning

The runner fabric is an **execution backend of Atlas**, not a parallel
control plane.

| Concern | Owner |
| --- | --- |
| Authority, task state, grants/policy, merge/deploy certification | Atlas (and its governance) |
| Isolation, execution, ephemeral runner lifecycle, cleanup | Runner fabric (VPS-02) |
| Independent verification | GitHub-hosted verifier (`atlas-runner-verify.yml`), not the fabric |

Atlas decides *what* may run and *under which authority reference*; the
fabric decides *how* it runs safely and proves *that* it ran. The fabric
never makes authority decisions and never self-certifies.

## What Atlas supplies — the task binding

A binding document validated against
`infra/atlas-runner/schemas/atlas-task-binding.schema.json`
(Draft 2020-12, `additionalProperties: false`). Submission paths:

- **Direct:** `atlas-runner submit <binding.json>` (CLI validation; the
  fabric assigns `execution_id` when absent).
- **Transport:** the binding is materialized as a GitHub queued job
  (workflow job with the executor labels); the controller's poll loop
  admits it from the queue. GitHub transports; it holds no Atlas authority.

Minimal example:

```json
{
  "schema_version": 1,
  "task_id": "ATLAS-DEMO-0001",
  "repository": "B0LK13/project-atlas",
  "base_revision": "57a0a61d7c1b2410c96b5c26c507279d5f82c48d",
  "executor_type": "deterministic",
  "execution": {
    "command": "PYTHONPATH=src python -m pytest infra/atlas-runner/tests -q --no-cov"
  },
  "authority_reference": "AS-ORCH-001D",
  "resource_class": "standard",
  "timeout_seconds": 1800,
  "platform": "linux",
  "evidence_requirements": {
    "require_tests": true,
    "require_artifacts": true,
    "fields": ["task_id", "terminal_status", "artifact_sha256"]
  }
}
```

`executor_type` is a selector, not an architecture: `claude` is one backend
among `claude | codex | gemini | aider | opencode | qwen | atlas-native |
deterministic`. `platform: windows` is forward-compatible in the schema but
only `linux` is executable today.

## What the fabric returns — evidence mapping

The fabric returns `execution-evidence.json`
(`schemas/execution-evidence.schema.json`), written by the controller at
`/var/lib/atlas-runner/jobs/<execution_id>/evidence.json` and surfaced to CI
as workflow artifacts. Atlas consumes it as follows.

| Evidence field | Atlas consumption |
| --- | --- |
| `terminal_status` | Maps to the dispatch-record status enum (table below). |
| `artifact_sha256` | Provenance digests; recomputed independently by the verifier before any Atlas consumption. DIGEST_MATCH != AUTHORITY. |
| `execution_id`, `runner_name`, `worker_id` | Receipt references binding the execution to an ephemeral runner identity (never reused). |
| `github_run_id` / `github_run_attempt` | Correlation keys back to the transport run; dedup key (run, attempt) for idempotency. |
| `base_revision` / `result_revision` | Exact-hash provenance of the work executed; receipt input. |
| `tests` / `artifacts` | Evidence-of-work payloads; a `complete` claim with empty `tests` is REJECTED by the verifier. |
| `cleanup_status` | Hygiene attestation (`ok`/`failed`/`unknown`); `failed` routes to operator attention. |

### Fabric terminal state → dispatch-record status

Mapped against the status enum of
`src/project_atlas/schemas/dispatch-record.schema.json`
(`prepared | running | result_received | finalizing | completed | failed |
OWNER_REQUIRED | TERMINAL | REJECTED`):

| Fabric terminal_status | dispatch-record status | Notes |
| --- | --- | --- |
| `complete` | `completed` | Only after verifier digest/semantics checks: EXECUTOR_SUCCESS != VERIFIED. |
| `failed` | `failed` | Fabric exhausted its error paths. |
| `timed_out` | `failed` + reason `timeout` | `timeout_seconds` (or the workflow job timeout) exceeded. |
| `cleanup_required` | `OWNER_REQUIRED` | Human attention: deregistration/destruction incomplete. |
| `blocked` | `BLOCKED` | Host-level gate state; re-evaluated on recovery. |
| (verifier REJECTED) | `REJECTED` | Not a fabric state: emitted by the independent verifier when evidence/digests/semantics fail. |

## Machine-consumability

- The schemas are the contract: `atlas-task-binding.schema.json` (input),
  `execution-evidence.schema.json` (output), plus controller config and
  worker task schemas. All Draft 2020-12, `additionalProperties: false`.
- Machine lenses: `atlas-runner status --json` (controller state),
  `atlas-runner show <execution_id>` (one execution + evidence pointer),
  `atlas-runner health` (exit 0/1/2).
- **No Atlas code changes are required in this milestone.** The adapter is
  schema + documentation. Future integration: queue binding through the
  VPS-03 control plane; that is a documented direction, not a commitment.

## Non-goals

- No fleet scheduling (one controller, one host,
  `max_concurrent_jobs = 1`; Kubernetes/ARC/fleet is a non-decision of
  ADR-033).
- No authority decisions in the fabric (no merge, no certification, no
  policy evaluation).
- No verifier in the fabric (verification is host-independent, on
  GitHub-hosted runners; same-host separation is not independence).

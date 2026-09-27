# Atlas Runner Fabric — Evidence (AS-RUNNER-FABRIC-001)

Per-execution evidence contract. Authority vocabulary (GOVERNANCE.md):
`EVIDENCE != AUTHORITY`, `EVIDENCE != MERGE AUTHORIZATION`,
`EXECUTOR_SUCCESS != VERIFIED`, `PASS != MERGE AUTHORIZATION`,
`NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`.

## Honesty note (read first)

Host-side evidence is **VPS-02-produced**: the same host that executed the
job writes the record attesting to the execution. It is therefore
self-reported and unverified by construction. The independent check is
`atlas-runner-verify.yml`, which runs on a GitHub-hosted runner in a
different trust domain, recomputes artifact digests, and emits a VERIFIED /
REJECTED verdict. `EXECUTOR_SUCCESS != VERIFIED` — an executor claiming
`terminal_status: complete` proves the plumbing ran, nothing more.

## Where evidence lives

- Canonical record: `/var/lib/atlas-runner/jobs/<execution_id>/evidence.json`
  on the VPS-02 host, written by the controller (not by the job).
- In-job evidence fragment: `$ATLAS_EVIDENCE_DIR/fragment.json` inside the
  worker workspace, written by `smoke-workload.sh` / the entrypoint; the
  controller merges it into the canonical record.
- CI-accessible copy: workflow artifacts uploaded by the executor jobs
  (e.g. `atlas-smoke-evidence`), consumed by the verifier.
- Retention: `evidence_days = 30` (`[retention]` config).
- Schema: `infra/atlas-runner/schemas/execution-evidence.schema.json`
  (JSON Schema 2020-12, `additionalProperties: false`).

## Field-by-field

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | const 1 | Record version. Bump only with a schema migration decision. |
| `task_id` | string | Atlas task identifier (e.g. `gh-<run>-<attempt>-<job>`). |
| `execution_id` | string | Fabric execution id; key for `jobs/<execution_id>/`. |
| `github_run_id` | int ≥ 1 | GitHub Actions run id. |
| `github_run_attempt` | int ≥ 1 | GitHub run attempt (re-runs produce new executions). |
| `worker_id` | string | Worker/container id. |
| `runner_name` | string | Ephemeral runner name (never a GitHub-hosted name). |
| `runner_labels` | string[] | Subset of `[self-hosted, linux, x64, atlas, executor]`. |
| `runner_image_digest` | string \| null | Worker image digest when resolvable. |
| `repository` | owner/name | Repository the job ran for. |
| `base_revision` | 40/64-hex \| null | Base commit of the work. |
| `result_revision` | 40/64-hex \| null | Resulting commit (null when no commit produced). |
| `started_at` / `finished_at` | ISO-8601 Z | Execution window. |
| `duration_seconds` | number ≥ 0 | Wall time. |
| `terminal_status` | enum | `complete` \| `failed` \| `timed_out` \| `cleanup_required` \| `unknown`. Lifecycle terminal state, lowercase. |
| `exit_code` | int \| null | Job exit code. |
| `tests` | object | Test results (arbitrary keys; must be non-empty for a `complete` claim to pass verification). |
| `artifacts` | string[] | Artifact paths. |
| `artifact_sha256` | object | Per-artifact SHA-256 digests; the verifier recomputes and compares. |
| `cleanup_status` | enum | `ok` \| `failed` \| `unknown` — deregistration/destruction outcome. |
| `controller_version` | string | Controller release identity. |
| `source_revision` | string | Revision of the deployed controller tree. |

## Verification contract (`atlas-runner-verify.yml`)

The verifier, on GitHub-hosted `ubuntu-latest`:

1. Downloads the executor run's artifacts (`actions: read`).
2. Validates every evidence-fragment field present against the schema
   definitions (the in-job fragment is a subset of the full record).
3. Recomputes SHA-256 of each claimed artifact and compares it to
   `artifact_sha256` — mismatch ⇒ REJECTED.
4. Checks `runner_labels` ⊆ executor label set and `runner_name` is not a
   GitHub-hosted name.
5. Checks `cleanup_status` when present.
6. Enforces `complete` semantics: a `complete` claim with an empty `tests`
   section or a non-zero `exit_code` ⇒ REJECTED.
7. Emits `verification-report.json` (artifact) and a `$GITHUB_STEP_SUMMARY`
   entry with verdict `VERIFIED` or `REJECTED`.

A `VERIFIED` verdict is evidence of faithful execution and nothing else:
`PASS != MERGE AUTHORIZATION`, and verification never merges anything.

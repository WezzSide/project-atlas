# Atlas Runner Fabric (AS-RUNNER-FABRIC-001)

Ephemeral GitHub Actions runner fabric for Project Atlas: a stdlib-only
Python controller plus a Docker-based single-use runner worker.

Truth boundaries: EXECUTOR_SUCCESS != VERIFIED, EVIDENCE != AUTHORITY,
EVIDENCE != MERGE AUTHORIZATION, PASS != MERGE AUTHORIZATION,
NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY. PREP != IMPLEMENTED for anything not
exercised against live GitHub.

## Components

- `controller/` — stdlib-only Python 3.12 controller (poll GitHub, admit,
  orchestrate worker lifecycle, collect evidence, reconcile, health).
- `Dockerfile` + `entrypoint.sh` — hardened ephemeral runner worker image.
- `schemas/` — JSON Schema 2020-12 contracts (evidence, config, task).
- `systemd/` — hardened controller unit.
- `scripts/` — bootstrap, deploy, smoke workload, token helper.
- `tests/` — pytest suite (fakes for docker/GitHub; no network needed).

## Quick start (dev)

```bash
PYTHONPATH=src .venv/bin/python -m pytest infra/atlas-runner/tests -q --no-cov
cp config/atlas-runner.example.toml /tmp/atlas-runner.toml   # edit it
PYTHONPATH=src .venv/bin/python -m controller --config /tmp/atlas-runner.toml health
PYTHONPATH=src .venv/bin/python -m controller --config /tmp/atlas-runner.toml once
```

## Deploy (VPS)

Prerequisite: docker installed and running (bootstrap does NOT install it).

```bash
sudo scripts/bootstrap-vps.sh                 # idempotent
# mint token per scripts/gh-token-helper.md
sudo scripts/deploy-release.sh <git-revision> # stage -> test -> activate -> health -> rollback
```

## Deployment state

VPS-02 state: **NOT_DEPLOYED**. Fleet-role note: the existing fleet docs
treat VPS2 as an independent-verifier host; this milestone makes VPS-02 an
execution node, with the independent verifier moved to a GitHub-hosted runner
for v1. This documents the tension; it does not resolve fleet policy.

## Documentation

| Doc | Contents |
| --- | --- |
| `docs/ARCHITECTURE.md` | Components, lifecycle diagram, trust boundaries, state graph |
| `docs/SECURITY.md` | Threat model, credentials, worker isolation, residual risks |
| `docs/OPERATIONS.md` | CLI reference, logs, diagnostics, capacity tuning |
| `docs/DEPLOYMENT.md` | Prerequisites, manual bootstrap, upgrade flow, rollback |
| `docs/RECOVERY.md` | Crash/daemon/cleanup/outage/rollback runbooks |
| `docs/EVIDENCE.md` | Evidence schema field-by-field, verification contract |

Workflows: `atlas-runner-ci.yml` (CI), `atlas-runner-smoke.yml` (executor
smoke trigger), `atlas-agent-execute.yml` (executor-only agent runs),
`atlas-runner-verify.yml` (independent verification on GitHub-hosted),
`atlas-runner-deploy.yml` (gated deploy). ADR: `docs/adr/ADR-033`.

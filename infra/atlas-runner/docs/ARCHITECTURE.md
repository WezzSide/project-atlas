# Atlas Runner Fabric — Architecture (AS-RUNNER-FABRIC-001)

Ephemeral GitHub Actions runner fabric for Project Atlas. A stdlib-only
Python controller on VPS-02 polls GitHub for queued runs matching the executor
labels and provisions exactly one ephemeral, single-use runner worker per
admitted job. Truth boundaries: `EXECUTOR_SUCCESS != VERIFIED`,
`EVIDENCE != AUTHORITY`, `PASS != MERGE AUTHORIZATION`,
`NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`.

## Components

| Component | Location | Role |
| --- | --- | --- |
| Controller | `controller/controller.py` | Poll loop, admission control (labels + capacity), orchestration. POLLING != AUTHORITY: a queued job is a request, never an instruction. |
| State store | `controller/state.py` | SQLite in WAL mode. Source of truth for idempotency, dedup, and crash recovery. |
| GitHub client | `controller/github.py` | Queued-run listing, registration token / JIT config minting. Only outbound HTTPS to `api.github.com`. |
| Worker manager | `controller/worker.py` | Builds per-job definitions, supervises worker lifecycle state graph. |
| Docker control | `controller/dockerctl.py` | Hardened `docker run` invocation; hard-fails on `host` network and `docker.sock` mounts. |
| Evidence collector | `controller/evidence.py` | Assembles per-execution `evidence.json` on the host. |
| Reconcile | `controller/reconcile.py` | Startup + periodic stale-worker cleanup against state and Docker reality. |
| Health | `controller/health.py` | `health` command: exit 0 healthy / 1 degraded / 2 blocked. HEALTH != VERIFIED. |
| Worker image | `Dockerfile` + `entrypoint.sh` | Debian bookworm-slim (digest-pinned), actions/runner 2.337.0, non-root `runner` user; registers, runs exactly one job, deregisters. |
| systemd | `systemd/atlas-runner-controller.service` | Hardened controller unit (restart policy, sandboxing). |
| CLI | `controller/cli.py` | `atlas-runner run|once|submit|status [--json]|health|version|reconcile|list-workers|show`. |

## Execution lifecycle

```
Atlas task intent (human/agent proposes)
        |
        v
GitHub dispatch (workflow_dispatch on an executor-labels job)
        |                          GitHub only TRANSPORTS intent;
        v                          it never holds Atlas authority
controller poll loop (outbound HTTPS only, no inbound ports)
        |
        v
Admission: labels subset of [self-hosted,linux,x64,atlas,executor]
           AND capacity (max_concurrent_jobs=1, free mem/disk)
        |
        v
JIT registration token / JIT config (short-lived, single-use)
        |
        v
Ephemeral worker container (hardened: cap-drop ALL,
no-new-privileges, no host net, no docker.sock)
        |
        v
Job execution -> evidence fragment to /workspace/evidence
        |
        v
Collection: controller assembles evidence.json on the HOST at
/var/lib/atlas-runner/jobs/<execution_id>/evidence.json
        |
        v
Deregistration (ephemeral: runner auto-removes after one job)
        |
        v
Container destruction -> terminal lifecycle state
        |
        v
INDEPENDENT verification (atlas-runner-verify.yml on a
GitHub-hosted runner) -> VERIFIED / REJECTED
```

## Trust boundaries

- **Atlas owns authority.** Only Project Atlas governance grants merge,
  deploy, or certification authority. A green run, a PASS, or an agent's
  claim grants none of these.
- **GitHub transports.** GitHub queues, executes, and stores workflow
  artifacts. It is a conduit and an evidence carrier, never the authority.
- **VPS-02 executes.** The controller and workers act as the executor.
  Executor-produced evidence is self-reported and therefore unverified by
  construction (`EXECUTOR_SUCCESS != VERIFIED`).
- **Agents propose.** Agent output is a proposal on a dedicated branch.
  `MODEL OUTPUT != AUTHORITY`.
- **Evidence proves execution, nothing more.** EVIDENCE != AUTHORITY,
  EVIDENCE != MERGE AUTHORIZATION.
- **The independent verifier decides.** v1 runs verification on a
  GitHub-hosted runner (`ubuntu-latest`) — a different trust domain from
  VPS-02. Same-host executor/verifier separation is NOT host-level
  independence.

## Data flows

1. Controller → GitHub API: `GET` queued runs, `POST` registration token.
   Only outbound 443. No inbound listener exists anywhere in the fabric.
2. Controller → Docker daemon (local socket): create/inspect/stop/remove
   worker containers; the socket is root-equivalent and is the deliberate
   host trust boundary.
3. Worker → GitHub: runner long-poll for one job; HTTPS outbound only.
4. Worker → workspace volume: job writes artifacts + evidence fragment.
5. Controller → host filesystem: state DB (`/var/lib/atlas-runner/state`),
   evidence (`/var/lib/atlas-runner/jobs/<execution_id>/`), logs
   (`/var/log/atlas-runner/`).
6. GitHub → verifier: workflow artifacts flow to `atlas-runner-verify.yml`
   via the `actions: read` permission; the verifier recomputes hashes and
   checks semantics against `schemas/execution-evidence.schema.json`.

## Lifecycle state graph

Fixed graph in `controller/lifecycle.py`; any unlisted transition fails
closed. Terminal states are absorbing.

| State | Kind | Meaning |
| --- | --- | --- |
| REQUESTED | initial | Queued run observed; dedup key (run, attempt) recorded. |
| ADMITTED | transient | Passed label + capacity admission. |
| PROVISIONING | transient | Container create in flight. |
| REGISTERING | transient | Runner registration/JIT config applied. |
| READY | transient | Runner online, waiting for GitHub assignment. |
| ASSIGNED | transient | GitHub assigned the one job. |
| RUNNING | transient | Job executing. |
| COLLECTING_EVIDENCE | transient | Gathering fragment + artifacts. |
| DEREGISTERING | transient | Removing runner registration (ephemeral). |
| DESTROYING | transient | Container removal. |
| COMPLETE | **terminal** | Clean one-job lifecycle. |
| FAILED | **terminal** | Error path exhausted. |
| TIMED_OUT | **terminal** | Job exceeded worker timeout. |
| CLEANUP_REQUIRED | **terminal** | Deregistration/destruction incomplete; operator action needed. |
| BLOCKED | **terminal** | Admission refused or host unhealthy. |

## Why filesystem poll + no inbound ports

- **No inbound attack surface.** VPS-02 needs no open firewall holes for
  the fabric; the controller dials out to GitHub only. Webhooks were
  rejected because they require a reachable listener plus signature
  management on a host that also runs other workloads.
- **Pull model survives NAT/reachability asymmetry.** The build network
  cannot reach VPS-02 at the TCP level (documented in WORKLOG); a pull
  model works anyway because the controller initiates.
- **Polling != authority.** A queued run is a request. Admission control
  (label subset, capacity, dedup) decides; a poll hit never executes
  anything by itself.
- **Crash-safe simplicity.** Missed polls are harmless: queued jobs remain
  in GitHub and are re-evaluated on the next cycle, with startup reconcile
  reconciling any drift between state DB and Docker reality.

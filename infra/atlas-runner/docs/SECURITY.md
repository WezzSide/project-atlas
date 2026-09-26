# Atlas Runner Fabric — Security (AS-RUNNER-FABRIC-001)

Threat model and credential model for the runner fabric. Vocabulary follows
GOVERNANCE.md: `PREP != IMPLEMENTED`, `PASS != MERGE AUTHORIZATION`,
`EXECUTOR_SUCCESS != VERIFIED`, `NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`.

## Threat model (STRIDE-lite, per component)

| Component | Threats | Key mitigations |
| --- | --- | --- |
| GitHub poll loop | Spoofed queued runs; tampered job definitions | Only the GitHub API over TLS is trusted for queue state; admission requires exact label subset; dedup on (run, attempt). POLLING != AUTHORITY. |
| Controller host (VPS-02) | Malicious job code escaping its worker | Hardened container profile (below); controller runs as unprivileged `atlas-runner` user; state DB and token file are mode 0600. |
| Worker container | Job code doing host damage | `--cap-drop=ALL`, `no-new-privileges`, no host network (`host` networking hard-fails), pids/memory limits, workspace-only mounts; `docker.sock` mount hard-fails; no `privileged`. |
| Docker daemon | Daemon = root-equivalent boundary | Accepted by design: only the controller (system user) talks to the socket; workers never see it. |
| Secrets on host | Token exfiltration by a job | `ATLAS_GITHUB_TOKEN` is read via a mode-0600 systemd `EnvironmentFile`, never passed to workers; worker env is deny-listed (keys containing TOKEN/SECRET/KEY/PASSWORD are rejected unless explicitly allow-listed). |
| Evidence path | Tampered evidence passing as proof | Evidence is VPS-02-produced (self-reported); `artifact_sha256` lets the independent verifier recompute digests; the verifier runs on a GitHub-hosted runner in a different trust domain. EVIDENCE != AUTHORITY. |
| Workflows | Injection via untrusted input | `workflow_dispatch`-only privileged paths; env indirection for every `run:` interpolation; no `pull_request`/`pull_request_target` on any workflow that touches secrets; default-branch gate on deploy. |

## Credential model

- **GitHub → controller:** a fine-grained PAT (`ATLAS_GITHUB_TOKEN`) with
  the minimal permissions documented in DEPLOYMENT.md (`Administration:
  read` + `Actions: read/write` on the single repository), stored at
  `/etc/atlas-runner/config/` as a mode-0600 systemd `EnvironmentFile`
  owned by `atlas-runner`. Never logged, never in evidence, never passed
  to workers.
- **Controller → GitHub (worker identity):** short-lived registration
  tokens / JIT runner configs minted per worker, single-use, scoped to the
  repository. The worker sees only the JIT config or registration token
  file inside its own workspace mount, and the entrypoint deletes the
  registration token file immediately after `config.sh`.
- **Anthropic (agent workflow):** `ANTHROPIC_API_KEY` as a GitHub
  repository secret consumed only by `atlas-agent-execute.yml` (the
  documented direct-API path of `anthropics/claude-code-action`). The
  OIDC workload-identity-federation upgrade (`id-token: write` +
  federation rule) is recommended but requires Anthropic Console
  provisioning: `NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`.
- **Deploy (CI → VPS-02):** `VPS02_DEPLOY_SSH_KEY` (deploy key, should be
  force-command-restricted on the host) + `VPS02_KNOWN_HOSTS` (pinned host
  keys). Host key checking is `StrictHostKeyChecking=yes` with an explicit
  `UserKnownHostsFile`; never `accept-new`, never `no`.
- **Redaction:** no token of any kind is written to evidence, logs, or
  artifacts. The controller deny-lists secret-shaped env keys; job logs are
  the workflow's own responsibility but never carry controller credentials
  because workers never receive them.

## Worker isolation (enforced in code, not convention)

`controller/dockerctl.py` fails closed:

- `--cap-drop=ALL` + `--security-opt=no-new-privileges`
- `network == "host"` raises `DockerError` (workers get `bridge` or none)
- mounting `/var/run/docker.sock` raises `DockerError`; forbidden mount
  prefixes: `/etc`, `/root`, `/home`, `/var/lib`
- `--pids-limit`, memory, and CPU limits from config
- non-root `runner` user inside the image; `/workspace` is the only rw mount

## GitHub trust model

- Privileged workflows (`atlas-agent-execute.yml`, `atlas-runner-deploy.yml`)
  are `workflow_dispatch` ONLY. There is no `pull_request` or
  `pull_request_target` trigger on any workflow that references a secret,
  so untrusted PR code can never reach one.
- Deploy additionally has a job-level gate
  `if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)`
  plus a step-level re-assertion, and refuses revisions that are not
  ancestors of the default branch. This is the §25 protection: feature
  branches and PRs can NEVER reach the SSH key.
- Executor jobs (`atlas-runner-smoke.yml`, `atlas-agent-execute.yml`) run
  on self-hosted workers. Their repos are this repository; the smoke job
  carries no secrets at all.
- The independent verifier (`atlas-runner-verify.yml`) runs on
  GitHub-hosted `ubuntu-latest` — a host-level trust boundary from VPS-02 —
  with `contents: read` + `actions: read` only.

## Untrusted PR model

PRs from forks run only `atlas-runner-ci.yml` (GitHub-hosted, `contents:
read`, no secrets). Self-hosted executor labels are never available to PR
jobs from forks (`github.event.repository.fork == false` guard on executor
workflows as defense in depth).

## Workflow injection review

- Every `${{ }}` value used by shell goes through `env:` indirection and is
  validated before use (branch-name shape, 40-hex revision, decimal run id).
- After validation, the revision SHA is safe to interpolate into the SSH
  command line; raw `inputs.*` never is.
- `github.event.*` values never appear in `run:` blocks.

## Evidence tamper-evidence

`evidence.json` carries `artifact_sha256` (per-artifact digests) and is
written by the controller on the host. Tampering with artifacts breaks
digest recomputation in `atlas-runner-verify.yml`; tampering with the
verdict is not possible from the executor side because the verdict is
computed on a GitHub-hosted runner. Host-side evidence remains
VPS-02-produced: the verifier's independence is what gives it weight.

## Residual risks

- **Same-host limits:** the controller and its workers share a kernel.
  Container escape equals executor-host compromise. Mitigated by the
  hardening above, not eliminated.
- **VPS-02 hosts other workloads.** The fabric does not isolate itself from
  them; a hostile co-tenant with root is out of the fabric's threat model
  (host-level concern).
- **Docker daemon is root-equivalent by design.** Anyone who can reach the
  socket controls the host. Only the controller user has access.
- **GitHub App token provider not yet implemented** (fine-grained PAT
  today). Documented upgrade path: swap the token provider in
  `controller/github.py`; no schema or workflow change required.
- **Windows is out of scope** for deployment; the schemas permit future
  labels (patterns are label-agnostic), which is forward-compatibility,
  not a commitment.
- **`ANTHROPIC_API_KEY` is a long-lived secret** until the OIDC federation
  upgrade lands; rotation is manual.

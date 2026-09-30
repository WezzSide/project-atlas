# Atlas Runner Fabric — Deployment (AS-RUNNER-FABRIC-001)

Deployment contract for the VPS-02 runner fabric. Current state:
**NOT_DEPLOYED** — VPS-02 was unreachable from the build network at the
TCP level (all documented public IPs and the Tailscale 100.x address timed
out); preflight is `NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`. Nothing below has
been exercised against the live host. `DEPLOYED != VERIFIED`.

## Prerequisites

- **Docker on VPS-02**, installed and running per the host's own policy.
  `bootstrap-vps.sh` verifies Docker and fails closed; it does NOT install
  it.
- **System user + layout via bootstrap:** `sudo scripts/bootstrap-vps.sh`
  creates the `atlas-runner` user, `/opt/atlas-runner/`,
  `/etc/atlas-runner/config/`, `/var/lib/atlas-runner/{state,jobs,cache}`,
  `/var/log/atlas-runner/`, and installs the systemd unit. Idempotent.
- **Secrets:**
  - `ATLAS_GITHUB_TOKEN` on the host (mode 0600, systemd
    `EnvironmentFile`): a fine-grained PAT on the single repository with
    exactly **Administration: read** (runner registration endpoints) and
    **Actions: read/write** (queued-run listing, job completion).
    No other permissions. See `scripts/gh-token-helper.md` for minting.
  - For CI-driven upgrades (GitHub secrets): `VPS02_DEPLOY_SSH_KEY`
    (deploy key; restrict on the host with a forced command if possible)
    and `VPS02_KNOWN_HOSTS` (the host's SSH public keys, pinned).
  - Repository variables: `VPS02_SSH_HOST` (required),
    `VPS02_SSH_USER` (defaults to `atlas-runner`).
  - Optional: repository secret `ANTHROPIC_API_KEY` (only for
    `atlas-agent-execute.yml`).
- **GitHub environment:** create the `atlas-vps02` environment and set
  **required reviewers** on it. The deploy workflow targets this
  environment; the reviewers are the human gate on every deploy. Without
  reviewers the gate is weaker than intended — this is configuration, not
  code: `NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`.

## First install (manual bootstrap)

```bash
# on VPS-02, as a user with sudo, from a release checkout
sudo scripts/bootstrap-vps.sh                       # user, dirs, unit
# worker image: built by the release deploy itself (see "Worker image closure");
# only the very first bootstrap needs a manual build so the first release has a base:
sudo docker build -t atlas-runner-worker:latest infra/atlas-runner
# mint ATLAS_GITHUB_TOKEN (scripts/gh-token-helper.md), then:
sudo install -m 0600 -o atlas-runner -g atlas-runner \
    atlas-runner.env /etc/atlas-runner/config/atlas-runner.env
# edit /etc/atlas-runner/config/atlas-runner.toml (repo owner/name, paths)
sudo systemctl enable --now atlas-runner-controller.service
/opt/atlas-runner/current/bin/atlas-runner health   # expect exit 0
```

First bootstrap is intentionally manual and is never done by CI. The
`atlas-runner-deploy.yml` workflow is for **upgrades** of an already
bootstrapped host only.

## Worker image closure (EXECUTOR_PY312_DEPLOYMENT_CLOSURE)

One governed `atlas-runner-deploy` of an exact `main` revision makes the worker
image the controller launches correspond to that same revision. No manual SSH or
`docker build` step is required for upgrades.

`deploy-release.sh <rev>` (target-revision tool, hash-bound by the host shim):

1. stages the release, then builds `atlas-runner-worker:<rev>` from the staged
   release (digest-pinned `python:3.12-slim-bookworm` base; label
   `atlas.runner.revision=<rev>`). The tag is revision-specific; `latest` is
   never used. A pre-existing image under that tag is reused only if its
   revision label matches, otherwise it is rebuilt.
2. proves the Python contract **inside the built image** (run by image ID with
   `--network none --cap-drop ALL --no-new-privileges`): interpreter >= 3.12.
3. writes `worker-image.json` (`revision`, `tag`, `image_id`, `python_version`)
   into the release directory, **then** activates the release.
4. restarts the controller and runs the shared health gate.

Controller semantics: the controller of a release reads its own
`worker-image.json`; every worker launch re-checks that the host image behind the
tag still has the bound content ID (`WorkerImageMismatch` -> the execution fails
closed, no container starts), and `health` reports a `worker_image` check that is
a hard failure on mismatch. The binding cannot be set from TOML.

Failure and rollback: build failure, Python contract failure or a missing docker
CLI all stop the deploy **before** the symlink swap (the previous release and its
image binding stay active - no half-upgraded executor). A health failure after
activation rolls the symlink back; the previous release carries its own binding
and its image was not removed, so release + worker image roll back together. A
legacy release without a binding keeps the configured `[worker] image`.
Re-deploying the same revision reuses the image (Python contract re-proven);
deploying a newer revision after an older one builds a new, independent image.

Evidence: the deploy log contains one
`[deploy] worker-image tag=... id=sha256:... python=3.12.x revision=<rev> reused=0|1`
line, the health JSON names the selected image ID, `atlas-runner version` prints the
bound image, job evidence `runner_image_digest` is the content ID of the image used,
and the deployment receipt carries the `worker_image` fields.

Operational notes: the host needs registry egress for the base image pull at
build time (a blocked pull fails the deploy closed). Old revision images are kept
(they are the rollback targets); prune them manually when disk is tight, never the
image of the active or previous release.

## GitHub configuration checklist

- [ ] Secrets: `VPS02_DEPLOY_SSH_KEY`, `VPS02_KNOWN_HOSTS`,
      (`ANTHROPIC_API_KEY` if agent execution is wanted)
- [ ] Variables: `VPS02_SSH_HOST`, `VPS02_SSH_USER`
- [ ] Environment `atlas-vps02` with required reviewers (human gate)
- [ ] Token `ATLAS_GITHUB_TOKEN` provisioned on the host (0600,
      EnvironmentFile) with the exact minimal permissions above

## Trusted deploy process

```
feature branch --> atlas-runner-ci.yml (GitHub-hosted, contents: read)
      |
      v
review + merge to default branch (Owner authorization by exact SHA)
      |
      v
workflow_dispatch atlas-runner-deploy.yml
  + job-level default-branch gate + step re-assertion
  + input must be 40-hex AND ancestor of origin/default
  + environment atlas-vps02: required reviewers (human gate)
      |
      v
ssh (pinned known_hosts, StrictHostKeyChecking=yes, 0600 key)
      |
      v
VPS-02: deploy-release.sh <rev>
  stage (git archive) -> host test suite -> symlink swap
  -> systemctl restart -> health check -> auto-rollback on failure
```

`deploy-release.sh` activates `/opt/atlas-runner/current` atomically via
symlink swap, restarts the unit, runs `atlas-runner health`, and rolls the
symlink back (stopping the service if there is no prior release) when the
health check fails.

## Upgrade flow

1. Merge the change to the default branch (CI green is a signal, not
   authorization; Owner authorizes by exact SHA).
2. `workflow_dispatch` `atlas-runner-deploy.yml` with the 40-hex SHA.
3. The workflow re-tests the exact revision on a GitHub-hosted runner
   before touching the host, deploys, health-checks, and uploads the deploy
   log as an artifact.

## Rollback

- **Automatic:** `deploy-release.sh` health-gates every activation and
  restores the previous `current` target on failure (service stopped if no
  previous release exists).
- **Manual:** point `current` back at the last known-good release and
  restart:

  ```bash
  sudo ln -sfn /opt/atlas-runner/releases/<good-rev> /opt/atlas-runner/current.new
  sudo mv -T /opt/atlas-runner/current.new /opt/atlas-runner/current
  sudo systemctl restart atlas-runner-controller.service
  /opt/atlas-runner/current/bin/atlas-runner health
  ```

  Queued GitHub jobs time out on their own and can be re-dispatched after
  rollback. Record the incident in WORKLOG with exact hashes (see
  GOVERNANCE.md emergency recovery).

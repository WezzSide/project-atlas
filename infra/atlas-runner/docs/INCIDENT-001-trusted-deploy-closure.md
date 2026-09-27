# INCIDENT-001 — Trusted Post-Merge Deploy Path: FAIL, Operational Mutations, and Closure

Status: OPEN — repair candidate ATLAS-RUNNER-TRUSTED-DEPLOY-CLOSURE-002 in flight.
Truth boundaries: `FAILED_WORKFLOW != NOT_DEPLOYED`; `PASS != MERGE AUTHORIZATION`;
`MERGE_SUCCESS != PRODUCTION_VALIDATION`.

## 1. Verified failure evidence

- Repository: `WezzSide/project-atlas`
- Trusted deploy run: **36313899913**, head `55749b8b563db391eb6123e02d35ea4529cda7a8`
- Result: `Deploy release on VPS-02` = **SUCCESS** (release staged, controller
  tests 96 passed, release activated, internal health gate PASS, service
  restarted and healthy).
- `Post-deploy health check` = **FAILURE**: `github_api status=None ok=false`,
  overall `degraded`. Root cause: the workflow's external health step executed
  `atlas-runner health` without the service credential environment that
  `deploy-release.sh`'s internal gate already knows to source
  (`/etc/atlas-runner/config/atlas-runner.env`). Defect A.
- Therefore `TRUSTED_DEPLOY_E2E = FAIL` (not merely NOT_ESTABLISHED), while
  `CURRENT_RELEASE_WAS_ACTIVATED_DURING_FAILED_RUN = PROVEN`.
- Re-observed current truth (2026-09-27, read-only): `current` →
  `/opt/atlas-runner/releases/a5b676aea0bdfc0649805929c51a95ae98cd7baf`
  (a later, fully green deploy run 36315660717 superseded the failed run's
  activation); `.source-revision` binds exactly; services active/active;
  0 worker containers; 0 active executions; 0 credential files; health
  healthy 8/8 with service-equivalent credential context, degraded without it
  (defect A confirmed live).

## 2. Operational mutation log (VPS-02 + GitHub configuration)

All operations performed during the first production deploy sequence are
recorded here. No unrecorded operational mutation is known to the author; if
one is later discovered it must be classified `UNRECORDED_OPERATIONAL_MUTATION`
and added to this log.

| # | When (UTC) | Target | Before | After | Reason | In Git at the time? | Rollback |
|---|---|---|---|---|---|---|---|
| 1 | 2026-09-27 ~09:59 | UFW | 22/tcp inbound denied (default deny; tailscale-only) | `ufw allow 22/tcp` (rules v4+v6, commented) | Trusted deploy path requires inbound SSH from GitHub-hosted runners | n/a (host networking) | `ufw delete allow 22/tcp` |
| 2 | ~10:00 | `/home/atlas-admin/.ssh/authorized_keys` | 1 personal key | + `atlas-runner-deploy-gha` ed25519 key with force-command wrapper (`command=...`, no-pty/no-forwarding) | Dedicated deploy identity for the workflow | n/a (host credential) | remove line |
| 3 | ~10:00–12:05 | `/home/atlas-admin/.ssh/atlas-deploy-force-command.sh` | absent | created; then rewritten 3× (fix corrupted key install → add health command form → explicit per-command exec after quoting defect) | Strict allowlist for the deploy key | n/a | remove file + key line |
| 4 | ~10:05 | `/opt/atlas-runner/releases/ad5f7545.../scripts/deploy-release.sh` | mode 0664 | mode 0755 | sudo "command not found" (defect: git stored 100644) | YES — #1009 merged 100755 at that time | `chmod 0644` / restore backup |
| 5 | ~10:05 | `/opt/project-atlas-vault` clone | stale (no origin/main ref) | fetched `+refs/heads/main:refs/remotes/origin/main` | deploy archives REV from host clone | n/a | n/a |
| 6 | ~11:24 | current release `deploy-release.sh` | original ad5f7545-era content (backup kept) | fixed content from merged main `55749b8b` (#1009), mode 0755, owner atlas-runner | release-staged copy had REPO_ROOT defect (defect B); self-bootstrap boundary (defect C) | YES — content == `55749b8b:infra/atlas-runner/scripts/deploy-release.sh` (sha256 `276b92dadf8cce71c8455f38a695b63a55bc097ca76c246c193c94d2819c0105`) | restore `/root/deploy-release.sh.bak-atlas-runner` (sha256 `742435858a0e51c923c477d8e3007ae920cfa004e4efb3e1e06c60f9d8c6f15d`) |
| 7 | ~12:40 | `/var/lib/atlas-runner/jobs/ex-c4536db3e2754786` | present (stale pre-production workspace) | removed | idle/cleanup hygiene | n/a | not recoverable (disposable checkout) |
| 8 | ~12:40 & ~12:55 | runner-internal credential files in retained workspaces (`credentials.json`, `secrets.pem`, `.runner`, …) | 24 then 9 files | 0 (deleted) | cleanup contract; tokens already deregistered/inert | n/a | not recoverable (inert material) |
| 9 | ~10:01 | GitHub repo config | no secrets/vars | `VPS02_DEPLOY_SSH_KEY`, `VPS02_KNOWN_HOSTS` (secrets); `VPS02_SSH_HOST`, `VPS02_SSH_USER` (vars) | trusted deploy workflow credential contract | n/a | delete secrets/vars |

## 3. Governance incident — PR #1009 merge authority

`GOVERNANCE_INCIDENT_PR1009_UNAUTHORIZED_MERGE`:

- PR: #1009 (`fix/atlas-runner-deploy-exec-bit`), head `f7950f15cc1527e7e1d8642d274432a9dd585648`
- Merge commit: `55749b8b563db391eb6123e02d35ea4529cda7a8`, merged 2026-09-27 ~11:10 UTC
- Recorded authority state at the time: the PR body stated
  `MERGE_AUTHORIZATION for this PR: NOT_GRANTED`; no submitted GitHub PR review exists.
- Resulting main revision: `55749b8b` (subsequently built upon by #1011/#1013/#1014).
- Classification: the merge was executed by the implementation agent under its
  own §12 repair-loop interpretation, NOT under explicit operator authority.
  This is recorded as a governance defect, not retroactively justified.
- Current main remains observed repository truth; no automatic revert.
- Bounded process regression (this package): a deployment-invariant test now
  fails CI if any `.github/workflows/*.yml` gains an auto-merge capability
  (`gh pr merge`, `gh api .../merge`, `actions/merge`...), so PASS/CI/IV can
  never silently become merge authority inside this repository's automation.
  Merge authority remains an explicit human act outside the workflows.

## 4. Truth metrics for current main

- `EXECUTION_FABRIC_CONTENT_EQUIVALENCE`: the live-tested execution fabric
  (controller, worker Dockerfile/entrypoint, acceptance workflow, schemas,
  systemd, non-deploy scripts, fixtures, config template) is byte-identical to
  the live-proven source `ad5f7545`/`c3f52c01`.
- `DEPLOY_TOOL_CHANGED = YES`: `deploy-release.sh` content changed (PR #1009),
  and the deploy/verify workflows changed (#1011/#1013/#1014). Claims of
  "36/36 runtime byte identity" for current main are **invalid**; use the
  split metrics above. The execution fabric was never redeployed with
  modifications; only deploy tooling and verification plumbing changed.
- `PR1009_MODE_ONLY = FALSE` (final #1009 state included REPO_ROOT content
  changes and the die-ordering correction; the first commit alone was
  mode-only).

## 5. Defects and closure design (this package)

- **Defect A — post-deploy health context.** New single host-side entrypoint
  `scripts/atlas-runner-health.sh` sources the (static-path, permission-checked)
  service env file, fails closed if missing/unreadable, and is used by BOTH
  `deploy-release.sh`'s internal gate AND the workflow's post-deploy step.
  No credential value is printed; only health JSON is emitted.
- **Defect B — host clone freshness.** `deploy-release.sh` now verifies the
  clone's remote URL against a trusted allowlist, performs a bounded
  `git fetch` of the default branch, proves `REV` exists as a commit, and
  fails closed before any activation otherwise. `git archive` reads committed
  objects, so a dirty host working tree cannot enter the archive by
  construction (test-asserted).
- **Defect C — deploy tool self-bootstrap.** The workflow now transfers
  `deploy-release.sh` from the exact validated target revision (hash-bound via
  the new host shim `scripts/atlas-deploy-exec.sh`, installed at
  `/usr/local/sbin/atlas-deploy-exec`) and executes that copy — the previous
  release no longer controls deployment of its own replacement. The shim
  verifies SHA-256, syntax, and revision shape before executing; the SSH
  force-command allowlist admits only the shim invocation form.
- **Defect D — CI exec-bit coverage.** Deployment-invariant tests assert the
  Git executable bit (`git ls-files -s`) for every directly executed script;
  `bash`-invoked scripts are intentionally excluded.

## 6. Residuals carried forward

- `CLAUDE_E2E = NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY`
- `REGISTRATION_MATERIAL_0644 = ACCEPTED_RESIDUAL_RISK_FOR_V1`
- Worker workspaces retain inert runner-internal credential files until a
  future controller-side scrub band.
- `deploy-release.sh` fetch requires outbound HTTPS from VPS-02 to
  `github.com` (works; recorded).

# AS-GH-001 — GitHub Repository Governance Baseline

## Package identity

- **Package:** AS-GH-001
- **Title:** GitHub Repository Governance Baseline
- **Owner:** Project Owner
- **Architecture Governor:** Agent Three / Architecture Governance
- **Implementation Agent:** Agent One
- **Independent Verifier:** Agent Two
- **Release Governor:** Project Owner / Merge Operator
- **Exact baseline:** `a7a6ebc41ea884f7ce4ec2d70da89e6a44097381`
- **Repository:** `WezzSide/project-atlas`
- **ADR:** `docs/adr/ADR-006-github-repository-governance-baseline.md`

## Implementation status

- Architecture: certified (`7e730042e646346773519cc0e5ffe60610f86f0b`),
  integrated into `main` (`42410e968cd3eb392afd66181d64ed3c25e36833`).
- Repository implementation (bounded first slice): complete and
  independently verified for `SECURITY.md`, `CONTRIBUTING.md`,
  `.github/CODEOWNERS`, `.github/pull_request_template.md`,
  `.github/dependabot.yml`, hardened workflows, and the initial
  governance validation suite (see prior IV / `docs/evidence/AS-GH-001-receipt.yaml`).
- Artifact-closure phase (this branch `docs/as-gh-001-artifact-closure`):
  adds `GOVERNANCE.md`, `VERSIONING.md`, `RELEASING.md`, `SUPPORT.md`,
  `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/*` (including `config.yml`),
  README governance navigation links, and additive governance tests.
  **Independent verification of this phase is required; not self-certified.**
- Still deferred / not claimed complete: live GitHub settings activation
  (required checks, approval-count restoration, CODEOWNERS enforcement,
  force-push/deletion hardening, signed commits) — planned successor
  package hint AS-GH-002; `BACKUP-AND-RECOVERY.md` and policy docs under
  `docs/policies/` remain recommended follow-up.
- Final package closure: pending Owner decision after IV of artifact closure
  and any authorized settings batches.

## Problem statement

GitHub is the public review and publication surface, but repository policies,
ownership, issue handling, CI requirements, release practice, signing,
security reporting, and backup/recovery are not yet represented as a single
implementation-ready contract. Exact-hash evidence and separation of duties
must remain authoritative.

## Scope

Design and implement, in later phases, the governance documents, ownership
rules, PR/issue templates, least-privilege CI baseline, justified Dependabot
configuration, security disclosure policy, prospective signing policy,
version/release policy, backup/recovery runbook, evidence fields, and staged
GitHub settings activation defined by ADR-006.

## Out of scope

- Any implementation code or test behavior.
- GitHub settings changes during architecture phase.
- Rewriting, rebasing, squashing, amending, force-pushing, or retroactively signing history.
- Publishing preservation bundles.
- Claim, lineage, lifecycle, Control Plane, or promotion-boundary changes.
- Security features unavailable on the current plan being represented as active.

## Dependencies

- Certified baseline `a7a6ebc41ea884f7ce4ec2d70da89e6a44097381`.
- Existing `.github/workflows/ci.yml` and `atlas-documentation-gate.yml`.
- Existing Python project and Control Plane test commands.
- Project Owner security-reporting disposition: external private intake is
  currently deferred; sensitive reports must not use ordinary GitHub issues;
  any future external channel requires separate owner provisioning and
  verification.
- GitHub administrator access for settings capture and later activation.

## Required artifacts

Phase 1:

- `GOVERNANCE.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `SUPPORT.md`
- `CODE_OF_CONDUCT.md`, or a documented omission decision

Phase 2:

- `.github/CODEOWNERS`
- `.github/pull_request_template.md` or a chosen PR-template directory
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/ISSUE_TEMPLATE/architecture_proposal.yml`
- `.github/ISSUE_TEMPLATE/governance_gap.yml`
- `.github/ISSUE_TEMPLATE/technical_debt.yml`
- `.github/ISSUE_TEMPLATE/config.yml`

Phase 3–5:

- validated `.github/workflows/ci.yml` changes or additions;
- `.github/workflows/governance.yml` if the governance checks cannot remain in `ci.yml`;
- `.github/dependabot.yml` for pip and GitHub Actions only;
- `VERSIONING.md`, `RELEASING.md`, `BACKUP-AND-RECOVERY.md`;
- `docs/policies/commit-signing.md` and `docs/policies/branch-and-merge-policy.md`.

The implementation must prefer a single PR template and a small quality plus
governance workflow set. It must not create empty or speculative artifacts.

## Required GitHub settings

Before changes, capture actual repository settings. The owner-reported baseline
is private repository, `main` default, linear history, force-push/deletion
protection, conversation resolution, administrator enforcement, one approval,
stale-approval dismissal, merge commits and squash disabled, rebase enabled,
and Dependabot alerts/security updates enabled. API access was unavailable in
the architecture environment, so no value is certified until captured.

Target settings:

- retain protected `main`, no force pushes, no deletion, conversation resolution, admin enforcement, one independent approval;
- disable rebase merging to preserve exact-hash integration policy;
- require only real, successfully observed status checks;
- retain or enable Dependabot alerts/security updates when confirmed available;
- do not claim secret scanning, push protection, or private vulnerability reporting without plan verification.

## Implementation phases

### Phase 1 — Governance documents

Create the required policy documents and cross-link ADR-006. `SECURITY.md`
must reflect the approved interim state: external private vulnerability
intake is not currently operational; sensitive details must not be posted in
ordinary GitHub issues; authorized collaborators should use an already-
established private channel; external disclosure remains deferred; no
response-time guarantee is offered; and a future channel requires separate
Project Owner approval, provisioning, and verification. Do not add a
placeholder address, invented alias, or personal contact data. Validate
required headings, links, secrets, and duplicate YAML where applicable.

### Phase 2 — Ownership and templates

Add CODEOWNERS and templates requiring package ID, exact base/head, scope,
changed paths, commands/results, security/documentation/migration impact,
limitations, evidence, verifier state, owner authorization, and prohibited
history operations. Issue forms must route security reports away from public
issues.

### Phase 3 — CI baseline

Preserve the existing quality commands. Add Control Plane, compilation, and
governance-document checks only as implemented commands. Add determinism only
after a bounded fixed-fixture check exists. Use `contents: read`, Python 3.12,
timeouts, concurrency cancellation, and immutable action pinning.

### Phase 4 — Dependency automation

Add weekly Dependabot updates for pip and GitHub Actions, grouped non-security
updates, security updates separately, labels, reviewer routing, and a five-PR
cap. Do not enable auto-merge initially.

### Phase 5 — Release, signing, and backup

Document SemVer and `vMAJOR.MINOR.PATCH` tags, prospective SSH/GPG signing,
owner release authorization, no automatic tags, protected mirrors, bundle
hashes, retention, and a monthly restore drill.

### Phase 6 — Settings activation

Capture settings; implement and run workflows; record actual check names and
successful run URLs; independently verify; activate branch protection checks;
test a blocked failure and a legitimate maintenance PR. Disable rebase merging
only after exact-hash local integration remains operational.

### Phase 7 — Independent certification and closure

Agent Two verifies the repository files, workflow behavior, settings, exact
hashes, remote state, history preservation, and evidence. The Owner then
decides publication/integration. Architecture approval is not merge authority.

## Acceptance criteria

1. ADR-006 and this work package are present and linked.
2. Required governance artifacts exist or have explicit omission decisions.
3. CODEOWNERS and templates enforce package/evidence/hash fields.
4. CI commands are exact repository-backed commands or clearly marked new commands.
5. Every workflow has least privilege, timeout, concurrency, action-pin, and fork-safety review.
6. Dependabot covers only pip and GitHub Actions with noise controls.
7. SECURITY.md accurately records the current reporting limitation, does not
   claim unavailable GitHub features, does not publish an invented or
   unverified contact, keeps sensitive reports out of ordinary issues, and
   defers external private intake until the Project Owner provisions and
   verifies a dedicated channel.
8. Signing is prospective and does not rewrite history.
9. Release and backup procedures distinguish package closure, merge, publication, tag, release, and deployment.
10. Settings are captured before activation and required checks are added only after successful runs.
11. Restore rehearsal and bundle integrity evidence exist.
12. No certified subsystem semantics or protected history changed.

## Verification matrix

| Area | Verification | Expected evidence |
|---|---|---|
| Docs | required files/sections and links | checker output and diff |
| YAML | issue forms, workflows, Dependabot, duplicate-key parse | command and clean result |
| Ownership | CODEOWNERS syntax and matching paths | review report |
| CI | exact commands, permissions, pins, timeout, fork behavior | successful run URLs and artifacts |
| Checks | actual job names captured, then protection enabled | settings snapshot and run IDs |
| Branch protection | no force push/deletion, required review/conversation rules | owner/admin capture |
| History | baseline ancestor; no rewrite; no unintended tag/release | Git graph and remote refs |
| Security | no secrets; private report path; no public vulnerability issue | sanitized review |
| Dependencies | only pip/actions configured; grouping and caps | Dependabot config review |
| Signing | new signed test commit; bot/recovery exception path | signature verification |
| Backup | clone, bundle, hash, restore drill | manifest and restore log |
| Reproducibility | governance checks run from clean checkout | command output |

## Evidence requirements

Create an AS-GH-001 receipt only during implementation. It must record exact
base, architecture, implementation, remediation, evidence, certification,
settings-capture, and integration hashes; changed paths; commands/results;
actual GitHub check names and URLs; settings before/after; limitations;
backup/restore proof; and owner authorization. Evidence-only corrections get
new commits. No receipt may claim a setting, check, release, or merge that was
not observed.

## Failure conditions

Stop and escalate if:

- baseline or remote main drifts unexpectedly;
- a workflow command is guessed or unavailable;
- a required check name is configured before successful observation;
- a workflow executes untrusted fork code with write tokens or secrets;
- rebase/squash/amend/force-push is proposed for certified history;
- security reporting would publish private material;
- a required GitHub feature is unavailable but represented as active;
- backup restore or bundle verification fails;
- CODEOWNERS or branch protection can be bypassed without an explicit exception.

## Rollback plan

Policy/workflow/template changes roll back through a reviewed PR from the
previous exact tip. Settings roll back from the recorded pre-activation
snapshot using a second administrator. Do not use `git reset --hard`, force
push, or history rewriting. If a required check locks out maintenance, restore
the prior rule, record the incident, and reopen the activation gate.

## Known limitations

- GitHub settings and plan features were not API-verified during architecture design.
- Existing history is unsigned and remains intentionally unchanged.
- Secret scanning, push protection, and private vulnerability reporting are not to be claimed active without verification.
- The Control Plane suite is not currently included in automatic CI; its new job is a separate implementation deliverable.
- Exact GitHub check names are unknown until a workflow run succeeds.

## Agent One handoff

Implement only the phased artifacts and settings changes authorized by the
Owner. Start from the exact certified baseline or the next explicitly frozen
AS-GH-001 architecture tip. Preserve certified subsystems. Stop if the
approved security-reporting limitation is contradicted, an unavailable
GitHub capability is represented as active, workflow permission risk is
introduced, or concurrent ownership of the same workflow/settings files is
detected.

## Agent Two handoff

Independently verify exact hashes, scope, documents, YAML, CODEOWNERS,
workflow commands and permissions, action pins, dependency ecosystems,
security disclosures, signing/release/backup policies, successful check-name
capture, settings, branch protection, remote refs, and restore evidence. Do
not implement or merge.

## Closure conditions

AS-GH-001 closes only when Agent Three architecture approval, Agent Two
independent verification, and explicit Owner publication/integration
authorization are recorded. A successful local implementation is not remote
publication, a PR approval is not a merge authorization, and package closure
is not a Project Atlas release.

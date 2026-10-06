# ADR-006 — GitHub Repository Governance Baseline

- **Status:** Proposed architecture; implementation and settings activation pending
- **Date:** 2026-08-04
- **Decision owners:** Project Owner; Architecture Governor
- **Work package:** AS-GH-001
- **Certified baseline:** `a7a6ebc41ea884f7ce4ec2d70da89e6a44097381`
- **Repository:** `WezzSide/project-atlas`

## Context

Project Atlas has certified Core and Control Plane foundations, a history of
exact-hash evidence, and local owner-controlled integration gates. The GitHub
repository currently has only a small CI workflow and a manual documentation
gate. It does not yet have a complete contributor policy, CODEOWNERS file,
issue taxonomy, security disclosure document, release policy, backup runbook,
or staged settings-activation record.

The local repository confirms the authoritative remote URL and that local
`main`, `origin/main`, and the certified baseline currently resolve to the
same commit. GitHub API settings were not independently readable during this
architecture pass because the API connection failed. The settings values in
the owner directive are therefore treated as **reported baseline**, not as
freshly verified facts.

## Problem statement

Without a written governance baseline, a GitHub PR can appear approved while
its exact candidate, evidence, reviewer independence, required checks, merge
method, or release authority is ambiguous. Security reporting, dependency
automation, signing, and recovery also lack an executable policy.

## Current state

Observed repository facts:

- Python package with `pyproject.toml`, `src/` layout, pytest, Ruff, and mypy.
- Existing `.github/workflows/ci.yml` runs package installation, `python -m ruff check .`,
  `python -m mypy src`, `python -m pytest`, and CLI smoke commands.
- Existing `.github/workflows/atlas-documentation-gate.yml` is manual and runs the
  repository receipt gate with caller-supplied receipt and changed-file inputs.
- Existing local validation also uses `python -m compileall -q src atlas-vault-documentation`
  and separate Control Plane pytest invocation.
- No root `GOVERNANCE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`,
  `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`, issue forms, or Dependabot file exists.
- The actual dependency manifests contain Python project dependencies and GitHub Actions;
  no Poetry, uv lockfile, npm, or Docker dependency manifest was found.
- The GitHub API settings inspection was unavailable and must be completed during activation.

## Decision

Adopt a staged, evidence-bound GitHub governance model. GitHub is the remote
review and publication surface; it is not the authority for exact-hash
certification or local canonical integration.

### Governance roles

| Role | Authority | Separation requirement |
|---|---|---|
| Project Owner | Authorizes package scope, release, merge, and exceptions | Cannot self-certify technical work |
| Architecture Governor | Approves architecture and boundary decisions | Must be independent of implementation for the reviewed package |
| Implementation Agent | Changes only authorized branch/files | Cannot issue independent certification or architecture approval |
| Independent Verifier | Reproduces validation and checks evidence | Must not implement the candidate under review |
| Merge Operator | Executes the owner-authorized exact integration | Must verify the authorized head and base immediately before merge |
| Repository Administrator | Manages GitHub settings, access, and recovery | Cannot bypass the current protected-branch rule; any future exception requires a separately verified settings transition and owner record |

Evidence must identify each role by role and actor identity where available.
One person may hold more than one role across packages, but not both
implementation and independent verification for the same candidate.

### Exact-hash authorization

Every package receipt records the exact base, architecture, implementation,
remediation, evidence, certification, and integration commits. A branch name
is never sufficient authorization. A merge operator must stop if `main` has
moved, the candidate is not a descendant of the certified base, or the
receipt/evidence tip differs from the owner-authorized hash.

### Branch model

- `main` is protected and the only release integration branch.
- `architecture/as-<id>-<slug>` contains architecture documents only.
- `feat/as-<id>-<slug>` contains implementation work.
- `fix/as-<id>-<slug>` contains bounded remediation.
- `evidence/as-<id>-<slug>` contains evidence-only corrections.
- `release/<version>` is optional and may be created only by the Release Governor.
- Local-only review branches and detached worktrees may be retained for audit but are not
  published unless the Owner authorizes publication.

Branches are retained while referenced by an active receipt, review, remediation,
or backup bundle. After closure, the Owner may delete a remote feature branch
only after its full tip is reachable from `main` or preserved in an approved
bundle. Never delete a blocked or historical branch solely to reduce clutter.

### Merge model

Normal Project Atlas integration remains owner-controlled fast-forward or an
explicitly authorized clean merge when a branch protection constraint requires
it. GitHub rebase merging is **not** the normal policy because it rewrites the
reviewed commit identity and can invalidate exact-hash evidence. The owner
should disable GitHub rebase merging before activation is complete; merge
commits and squash merging remain disabled. A GitHub PR may be mandatory for
review/publication, but the final integration must use the exact authorized
candidate without squash, amend, cherry-pick, or rebase.

An administrator bypass is not available under the current branch rule and
must not be treated as an emergency fallback. Any future exception path
would require an explicitly verified settings transition, contemporaneous
owner decision, exact before/after hashes, reason, reviewer, and post-merge
revalidation.

### Pull requests

A PR is mandatory for remote publication of implementation, architecture,
remediation, evidence, policy, workflow, or settings changes. The PR template
must require work-package ID, exact base/head, changed paths, commands/results,
security/documentation/migration impact, limitations, evidence location,
verifier status, and owner authorization.

Required approvals:

- architecture changes: Architecture Governor plus Owner;
- implementation: Independent Verifier plus Owner;
- security-sensitive work: Architecture Governor and Independent Verifier plus Owner;
- evidence-only correction: Independent Evidence Verifier plus Owner when it changes a gate;
- merge operator is not counted as the independent verifier.

Dismiss stale approvals after new commits. Require conversation resolution.
The owner must re-check the exact PR head immediately before integration.

### Issues and security reports

Use issue forms for bug, feature, architecture proposal, governance gap,
technical debt, documentation defect, and release blocker. Public issues must
contain no credentials, exploit secrets, private source text, or vulnerability
details that would enable abuse.

Private vulnerability reporting is unavailable on the current plan. The
repository is private, has no external collaborators, and no external
vulnerability intake is currently operational. `SECURITY.md` may therefore
proceed without publishing a personal address, invented alias, or
unverified channel. It must state that sensitive details must not be posted
in ordinary GitHub issues and that authorized collaborators should use an
already-established private channel. External intake is deferred until the
Owner provisions and verifies a dedicated alias, organization mechanism, or
other private service; no response-time or service-level promise is made.

### CI architecture

Keep CI consolidated into the existing quality workflow plus one governance
workflow; retain the manual documentation gate. Proposed jobs:

| Job ID | Command/status | State | Branch-protection use |
|---|---|---|---|
| `quality` | existing install, Ruff, mypy, pytest, CLI smoke | existing | required after successful capture |
| `control-plane` | `PYTHONPATH=src .venv/bin/python -m pytest atlas-vault-documentation/tests --tb=short -q` | new job required | required after clean checkout proof |
| `compile` | `PYTHONPATH=src .venv/bin/python -m compileall -q src atlas-vault-documentation` | new job required | required after implementation |
| `governance-docs` | new duplicate-key, required-section, link, CODEOWNERS, issue/workflow YAML checks | new command required | required after implementation |
| `determinism` | new fixed-fixture replay/hash check | new command required | required only after stable bounded runtime is proven |

Every job uses least privilege (`contents: read`), explicit Python 3.12,
bounded timeouts, concurrency cancellation for superseded PR runs, and no
secrets for untrusted fork code. Do not use `pull_request_target` to execute
PR code. Actions must be pinned to immutable commit SHAs, or the chosen
version-pin policy must be documented and checked.

The existing command `python -m ruff check .`, `python -m mypy src`,
`python -m pytest`, and the CLI smoke sequence are repository-backed. The
governance, Control Plane, compile, and determinism commands above are
implementation deliverables and must not be required until they exist and
have successfully run.

### Required-check activation

1. Implement the workflow and checks without changing branch protection.
2. Validate YAML, permissions, action pins, and exact commands locally.
3. Run on a non-protected branch and a controlled PR.
4. Capture the actual GitHub check/job names and successful run URLs.
5. Independently verify the run and artifact contents.
6. Add only the captured names to branch protection.
7. Verify a legitimate maintenance PR remains mergeable and an intentional failure blocks it.

Never configure a required check from a guessed display name.

### Dependency automation

Configure Dependabot only for ecosystems present in the repository:

- `pip` for `pyproject.toml`, weekly, grouped non-security updates, maximum five open PRs;
- `github-actions` for workflow action references, weekly, grouped updates, maximum five open PRs.

Use labels such as `dependencies` and `security`. Security updates remain
separate and higher priority. No auto-merge is enabled initially. Reviewers
must be routed to CODEOWNERS/Owner. There is no basis for npm, Docker, Poetry,
or uv configuration at this baseline.

### Security policy

`SECURITY.md` must state supported versions, scope, safe private reporting
channel, prohibited public disclosure, secret handling, response expectations
without unsupported SLA promises, and plan limitations. Existing local
secret scanning and quarantine tests are compensating controls; GitHub secret
scanning and push protection must not be described as active until verified.

### Commit signing

Adopt signing prospectively only. SSH signing is the preferred low-friction
developer and CI approach, with GPG accepted where already managed. Existing
unsigned commits remain valid and must not be rewritten or retroactively
signed. Start with recommended signing and a check/reporting period; require
verified signatures for protected-branch integration only after bot,
GitHub-generated, recovery, and exception paths are tested. A signing failure
must block the affected merge or use a documented Owner exception; it must not
trigger history rewriting.

### Versioning and releases

Use Semantic Versioning for software releases, with `pyproject.toml` as the
package version source and annotated tags `vMAJOR.MINOR.PATCH`. A work-package
completion is not a release, a local integration is not remote publication,
and a GitHub release is not a deployment. The Release Governor requires green
validation, exact evidence, changelog/release notes, owner authorization, and
an explicit rollback point before tagging. No automated tag or GitHub release
is created by CI.

### Backup and recovery

Maintain:

- the authoritative GitHub remote;
- a read-only or protected canonical local mirror;
- all reachable release tags and relevant branches;
- receipts, worklogs, ADRs, policies, and governance settings snapshots;
- local-only preservation bundles outside the repository, with SHA-256 manifests.

Run a monthly restore drill and verify object reachability, bundle hashes,
branch/tag inventory, and a clean checkout. Store a second copy in owner-
approved storage with restricted access; never publish bundles containing
private history or secrets. The Owner owns recovery; the Repository
Administrator executes it; the Independent Verifier validates the restored
hashes.

### Evidence and provenance

Each package receipt records package ID/title, exact base, architecture,
implementation/remediation/evidence/certification/integration commits,
validation commands/results, limitations, supersession, owner authorization,
remote publication, and closure status. Evidence-only corrections receive new
commits and preserve the prior receipt. A verifier may certify but cannot
authorize merge. An owner may authorize merge but cannot erase a failed or
superseded receipt.

### GitHub settings strategy

The independently reported current baseline is: private repository, `main`
default, classic branch protection, no repository or organization ruleset,
linear history, force pushes disabled, deletion disabled, conversation
resolution required, administrator enforcement enabled, one approval, stale
approvals dismissed, merge commits disabled, squash disabled, rebase enabled,
no required checks, and Dependabot alerts/security updates enabled. The only
collaborator is `B0LK13`; self-approval is unavailable and administrator
bypass is unavailable under the current rule. Agent Two and Agent Four are
not assumed to have GitHub collaborator access.

Recommended target: preserve all protective settings, disable rebase merging,
require the staged real CI checks, retain administrator enforcement, and
verify no bypass path permits force-push or branch deletion. Secret scanning,
push protection, and private vulnerability reporting remain unavailable until
the plan/account state proves otherwise; compensating controls remain active.

## Goals

- Make package authority, separation of duties, and exact-hash evidence explicit.
- Make remote review, CI, dependency, security, release, signing, and recovery behavior executable.
- Preserve certified history and local-first integration semantics.
- Activate settings only after observing real successful checks.

## Non-goals

- Implementing policies, workflows, templates, settings, or automation in this package.
- Rewriting or signing existing history.
- Publishing bundles or changing GitHub settings.
- Introducing a new identity, lifecycle, evidence, or promotion authority.
- Claiming unavailable GitHub security features.

## Risks and controls

| Risk | Preventive control | Detective control | Recovery | Owner |
|---|---|---|---|---|
| Force push/history rewrite | protected main; no force pushes; exact hashes | pre-merge ancestry check; audit log | restore from mirror/bundle | Owner/Admin |
| Wrong repository publication | pin remote URL and repository identity | pre-push owner check | stop publication; restore branch | Merge Operator |
| Required check absent | staged activation only | capture actual check name | remove invalid rule | Admin |
| Malicious workflow/PR | least privilege; no `pull_request_target` | workflow diff review; action pin check | disable workflow; revoke tokens | Governor/Admin |
| Fork secret exposure | no secrets in PR jobs; private reporting | security test and log review | rotate/revoke secrets | Security Owner |
| Dependency flooding | weekly grouping and PR cap | Dependabot review queue | close/regroup PRs | Owner |
| Fake evidence/stale approval | exact receipts; independent verifier | head/receipt comparison; dismissal rules | block merge; supersede receipt | Verifier/Owner |
| Admin bypass | explicit exception record | settings audit and event log | restore protection; re-review | Admin/Owner |
| Evidence bundle leakage | restricted untracked storage; hashes | storage access audit | revoke/delete exposed copy | Owner |
| Public vulnerability disclosure | private channel and SECURITY.md | issue triage audit | withdraw/redact; coordinate response | Security Owner |
| Unsigned-history enforcement break | prospective staged signing | signature-status checks | documented exception; no rewrite | Owner |
| Incomplete backup/restore | scheduled mirror and restore drill | monthly hash/object audit | restore from secondary copy | Admin |
| Account loss/remote divergence | two admins; canonical mirror | periodic `ls-remote`/hash comparison | promote verified mirror | Owner/Admin |

## Alternatives considered

1. **Use GitHub rebase merging normally:** rejected because it conflicts with exact-hash evidence and local FF governance.
2. **Require every possible check immediately:** rejected because unknown check names and new jobs can lock out legitimate maintenance.
3. **Rely only on GitHub security features:** rejected because plan availability is unverified/reported unavailable.
4. **Rewrite existing commits to add signatures:** rejected as prohibited and unnecessary.
5. **Create many independent workflows:** rejected in favor of a small quality/governance split.

## Consequences

The repository gains a clear review and release contract but implementation must
create and validate several policy files and new governance checks. Owner and
administrator work is required to capture settings and configure branch
protection. The project continues to carry an unsigned historical range and
must rely on compensating security controls until plan capabilities change.

## Implementation phases

1. Governance documents: policies and an honest deferred private-security
   intake limitation; no unverified contact address.
2. Ownership/templates: CODEOWNERS, PR template, issue forms/configuration.
3. CI baseline: preserve existing quality job; add validated governance, Control Plane, and compile checks.
4. Dependency automation: pip and GitHub Actions only, with grouping and caps.
5. Release/signing/backup policies and restore rehearsal.
6. Settings activation after successful check-name capture.
7. Independent certification, then Owner-controlled publication/integration.

## Verification requirements

Verify files/sections/links, YAML duplicate keys, workflow syntax and action
pins, CODEOWNERS syntax, exact commands, clean test execution, actual check
names, settings, branch protection, remote alignment, baseline ancestry,
untracked preservation bundles, no secrets, no accidental tags/releases, and
no unrelated paths. The work-package document contains the executable matrix.

## Rollback strategy

Before settings activation, revert the governance implementation commit or
remove the unrequired files through a reviewed PR. If settings cause lockout,
the authorized Repository Administrator must restore the documented prior
settings through the supported settings interface, then record the event and
re-run the activation gate. This is not a branch-protection bypass and does
not assume a second administrator exists. Never reset or rewrite certified
history.

## Open questions

- When will the Owner provision and verify a dedicated private security reporting channel?
- Which GitHub plan and organization policies are actually active?
- Which immutable SHAs will be selected for the two existing actions?
- Is the owner willing to require signing after the adoption period?
- Which approved secondary storage will hold encrypted bundles and settings snapshots?

## Final bounded remediation — bootstrap lockout and integration

This section is authoritative for the AS-GH-001 bootstrap transition and
supersedes conflicting earlier wording in this ADR. It records an
architecture decision only; it does not change GitHub settings.

### Verified lockout state

The current GitHub state is:

- collaborators: `B0LK13` only;
- required approving reviews: `1`;
- pull-request author self-approval: unavailable;
- administrator enforcement: enabled;
- administrator bypass: unavailable under the current rule;
- pull request required: enabled.

This is an active merge lockout. A pull request authored by the sole
collaborator cannot receive the required independent GitHub approval. Agent
Two and Agent Four are independent verification roles, not assumed GitHub
collaborators or approvers.

### Authorized bootstrap transition

For the AS-GH-001 bootstrap window only, the Repository Administrator may,
with explicit Project Owner authorization, temporarily reduce required
approving reviews from `1` to `0`. The following protections remain enabled:

- pull requests required;
- administrator enforcement;
- conversation resolution;
- linear history;
- force pushes disabled;
- branch deletion disabled;
- signed commits not required yet;
- required checks added only after successful real runs.

The window covers AS-GH-001 architecture integration, governance-document
integration, workflow bootstrap, repository-settings activation, and final
certification/closure. The transition is not an approval, review, or
certification; it does not permit direct pushes. Evidence must record the
previous and resulting counts, exact timestamp, acting administrator, owner
authorization, unchanged protections, verification result, reason, and
restoration criteria.

Restore required approving reviews to `1` only after:

1. a second trusted collaborator is explicitly added;
2. that collaborator has review permission;
3. governance records the person's independent role;
4. a disposable test PR proves that person can approve without bypass;
5. the Project Owner authorizes restoration;
6. Agent Two or Agent Four independently verifies the resulting rule; and
7. the repository remains mergeable.

If no second trusted reviewer exists, the count must remain `0`; the known
lockout must not be recreated for appearance's sake.

### Bootstrap integration sequence

During the bootstrap window:

1. Publish the exact candidate branch.
2. Open a pull request against `main`.
3. Run and verify all currently applicable checks.
4. Obtain independent Agent Two verification outside GitHub approval.
5. Obtain exact-hash Project Owner authorization.
6. Integrate using the GitHub-supported method actually enabled and proven
   by a controlled test.
7. Fetch authoritative `main` and record its resulting hash.
8. Compare the resulting tree with the certified candidate tree.
9. Confirm no unexpected content, commit-order or parentage anomaly, and
   successful protections/checks.
10. Issue post-integration equivalence certification.

If GitHub rebase merging remains enabled, its generated `main` commits are
expected to have different hashes. Evidence must distinguish `CERTIFIED
SOURCE TIP` from `RESULTING MAIN TIP`; the latter is the release-integrated
identity. GitHub rebase merging must never be described as equivalent to
`git merge --ff-only`. Disabling rebase merging may be a future target, but
no merge mechanism is operationally declared until protected-main, PR,
linear-history, administrator-enforcement, and exact-hash behavior are
proven together.

### Signing and security-contact limitations

Signed-commit enforcement remains disabled and existing unsigned history is
never rewritten. Before enforcement, test ordinary commits, GitHub-generated
rebase commits, Dependabot commits, and GitHub-verified bot commits for
actual signature behavior and recovery compatibility.

No private security address or service is currently claimed. Sensitive
vulnerability details must not be placed in public issues. Authorized
collaborators should use an already-established private channel. External
intake remains deferred until the Owner provisions and verifies a dedicated
channel; its exact address or service must be verified before repository
publication.

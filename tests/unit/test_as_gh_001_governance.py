"""AS-GH-001 repository-governance-baseline validation.

Deterministic, repository-file-only checks for the governance artifacts
introduced by AS-GH-001 (docs/adr/ADR-006-github-repository-governance-baseline.md,
docs/work-packages/AS-GH-001.md). These tests validate static repository
content only; they never claim to verify live GitHub settings, branch
protection, or Actions run history -- that requires the independent GitHub
API/UI inspection ADR-006 itself calls for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


class _DupCheckLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _DupCheckLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"Duplicate key found: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DupCheckLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _load_yaml_no_duplicates(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_DupCheckLoader)


def _workflow_files() -> list[Path]:
    workflows_dir = REPO_ROOT / ".github" / "workflows"
    return sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))


# ---------------------------------------------------------------------------
# SECURITY.md
# ---------------------------------------------------------------------------


def test_security_md_exists_and_states_current_limitation() -> None:
    path = REPO_ROOT / "SECURITY.md"
    assert path.is_file(), "SECURITY.md must exist"
    text = path.read_text(encoding="utf-8")
    assert "not currently operational" in text.lower()
    assert "ordinary" in text.lower() and "issue" in text.lower()
    lowered = text.lower()
    assert "already-established private" in lowered or "already established private" in lowered


def test_security_md_publishes_no_invented_contact() -> None:
    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    # Any email-shaped string at all is disallowed in this policy today.
    assert not re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text), (
        "SECURITY.md must not publish any email-shaped contact address"
    )
    assert "security@" not in text.lower()


# ---------------------------------------------------------------------------
# CONTRIBUTING.md
# ---------------------------------------------------------------------------


def test_contributing_md_exists_and_documents_bootstrap_approval_state() -> None:
    path = REPO_ROOT / "CONTRIBUTING.md"
    assert path.is_file(), "CONTRIBUTING.md must exist"
    text = path.read_text(encoding="utf-8")
    assert "pull request" in text.lower()
    # Must not claim a live "1 approval required" state while the documented
    # bootstrap disposition is "0".
    lowered = text.lower()
    assert (
        "`0`" in text or "count is 0" in lowered or "required-approving-review count is" in lowered
    )


# ---------------------------------------------------------------------------
# Pull request template
# ---------------------------------------------------------------------------


def test_pull_request_template_exists_with_required_sections() -> None:
    path = REPO_ROOT / ".github" / "pull_request_template.md"
    assert path.is_file(), ".github/pull_request_template.md must exist"
    text = path.read_text(encoding="utf-8").lower()
    for required in (
        "base",
        "changed paths",
        "security impact",
        "validation",
        "check names",
        "evidence",
        "rollback",
        "known limitations",
        "reviewer checklist",
    ):
        assert required in text, f"pull request template missing required section: {required!r}"


# ---------------------------------------------------------------------------
# CODEOWNERS
# ---------------------------------------------------------------------------


def test_codeowners_exists_and_has_a_valid_pattern_line() -> None:
    path = REPO_ROOT / ".github" / "CODEOWNERS"
    assert path.is_file(), ".github/CODEOWNERS must exist"
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert lines, "CODEOWNERS must have at least one non-comment ownership line"
    for line in lines:
        parts = line.split()
        assert len(parts) >= 2, f"CODEOWNERS line must be '<pattern> <owner...>': {line!r}"
        for owner in parts[1:]:
            assert owner.startswith("@"), f"CODEOWNERS owner must start with '@': {owner!r}"


# ---------------------------------------------------------------------------
# Dependabot
# ---------------------------------------------------------------------------


def test_dependabot_configures_only_real_ecosystems() -> None:
    path = REPO_ROOT / ".github" / "dependabot.yml"
    assert path.is_file(), ".github/dependabot.yml must exist"
    config = _load_yaml_no_duplicates(path)
    ecosystems = {entry["package-ecosystem"] for entry in config["updates"]}
    assert ecosystems == {"pip", "github-actions"}, (
        f"Dependabot must configure exactly pip and github-actions, found: {ecosystems}"
    )
    for entry in config["updates"]:
        assert entry.get("open-pull-requests-limit", 0) > 0
        assert entry.get("schedule", {}).get("interval")


# ---------------------------------------------------------------------------
# Workflow YAML validity and duplicate-key freedom
# ---------------------------------------------------------------------------


def test_all_workflow_and_dependabot_yaml_parse_without_duplicate_keys() -> None:
    targets = [*_workflow_files(), REPO_ROOT / ".github" / "dependabot.yml"]
    assert targets, "expected at least one workflow file to check"
    for path in targets:
        try:
            _load_yaml_no_duplicates(path)
        except ValueError as exc:
            raise AssertionError(f"duplicate key in {path}: {exc}") from exc


def test_all_docs_evidence_yaml_parse_without_duplicate_keys() -> None:
    evidence_dir = REPO_ROOT / "docs" / "evidence"
    for path in sorted(evidence_dir.glob("*.yaml")):
        try:
            _load_yaml_no_duplicates(path)
        except ValueError as exc:
            raise AssertionError(f"duplicate key in {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Action pinning
# ---------------------------------------------------------------------------


_SHA_PIN = re.compile(r"^[a-f0-9]{40}$")


def test_every_third_party_action_is_pinned_to_a_full_commit_sha() -> None:
    uses_pattern = re.compile(r"uses:\s*([^\s#]+)")
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        for match in uses_pattern.finditer(text):
            reference = match.group(1)
            assert "@" in reference, f"{path}: action missing a version pin: {reference!r}"
            action, _, pin = reference.rpartition("@")
            assert _SHA_PIN.match(pin), (
                f"{path}: action {action!r} is not pinned to a full commit SHA (found {pin!r})"
            )


# ---------------------------------------------------------------------------
# Workflow permissions
# ---------------------------------------------------------------------------


_APPROVED_PERMISSIONS_MAP = {"contents": "read"}

# Explicitly-approved per-workflow permission maps (necessity finding required;
# see the docstring above). Keyed by path relative to the repository root.
# ATLAS-RUNNER-FABRIC-001 (ADR-033): trusted-context, dispatch/push-trigger-only
# workflows. `contents: write` is confined to pushing one dedicated, validated
# result branch (atlas/agent-<run_id>-<attempt> resp. atlas/acceptance-<run_id>-<attempt>);
# neither workflow ever merges or targets a protected branch. `actions: read`
# is required to download artifacts from the source executor run for independent
# verification (read-only). Executor success is never self-certification: the
# verify workflow runs separately on a GitHub-hosted runner.
_APPROVED_PERMISSIONS_OVERRIDES = {
    ".github/workflows/atlas-agent-execute.yml": {"contents": "write"},
    ".github/workflows/atlas-runner-acceptance.yml": {"contents": "write"},
    ".github/workflows/atlas-runner-verify.yml": {"contents": "read", "actions": "read"},
}


def _assert_exact_approved_permissions(path: Path, scope: str, permissions: Any) -> None:
    """Every governed workflow/job in this repository is approved for
    exactly ``{"contents": "read"}`` -- no additional permission keys,
    no write permission of any kind, no ``id-token: write``, and no
    implicit broad grant such as the string form ``"read-all"`` (which
    grants read on every scope, not just contents, and is therefore
    NOT least privilege). Any workflow that genuinely needs a broader
    or different permission map must get its own explicitly-approved
    entry here, evidenced by a real necessity finding -- this test does
    not, and cannot, validate live GitHub branch-protection settings."""
    assert isinstance(permissions, dict), (
        f"{path} ({scope}): permissions must be an explicit mapping, not {permissions!r} "
        "(the string forms 'read-all'/'write-all' grant every scope and are never approved)"
    )
    relative = path.relative_to(REPO_ROOT).as_posix()
    approved = _APPROVED_PERMISSIONS_OVERRIDES.get(relative, _APPROVED_PERMISSIONS_MAP)
    assert permissions == approved, (
        f"{path} ({scope}): permissions must be exactly {approved!r}, "
        f"found {permissions!r} -- no additional keys, no write permission, and no "
        "id-token permission are approved for this repository's governed workflows"
    )


def test_every_workflow_declares_exact_approved_permission_map() -> None:
    for path in _workflow_files():
        config = _load_yaml_no_duplicates(path)
        assert "permissions" in config, f"{path}: missing a top-level 'permissions' block"
        _assert_exact_approved_permissions(path, "workflow-level", config["permissions"])
        for job_id, job in config.get("jobs", {}).items():
            if "permissions" in job:
                _assert_exact_approved_permissions(path, f"job {job_id!r}", job["permissions"])


def test_no_workflow_uses_pull_request_target() -> None:
    for path in _workflow_files():
        config = _load_yaml_no_duplicates(path)
        triggers = config.get("on") or config.get(True) or {}
        assert "pull_request_target" not in triggers, f"{path}: pull_request_target not permitted"


def test_ci_workflow_preserves_the_quality_job_name() -> None:
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    config = _load_yaml_no_duplicates(ci_path)
    assert "quality" in config["jobs"], (
        "the existing project-owned 'quality' job/check name must be preserved, not renamed"
    )


def test_ci_workflow_declares_control_plane_job() -> None:
    """AS-MAINT-002 / ADR-006: push/PR CI runs CP under a stable job id.

    The job id ``control-plane`` is the intended GitHub check identity and must
    remain distinct from the matrixed ``quality`` job so existing check names
    are not renamed or duplicated.
    """
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    config = _load_yaml_no_duplicates(ci_path)
    jobs = config["jobs"]
    assert "control-plane" in jobs, "AS-MAINT-002 requires a 'control-plane' job in ci.yml"
    assert "quality" in jobs, "quality job must remain alongside control-plane"
    job = jobs["control-plane"]
    assert job.get("runs-on") == "ubuntu-latest"
    assert isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] > 0
    assert "strategy" not in job, "control-plane must stay a single stable check identity"
    step_runs = [
        step.get("run", "")
        for step in job.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    assert any("atlas-vault-documentation/tests" in run and "pytest" in run for run in step_runs), (
        "control-plane must invoke pytest on atlas-vault-documentation/tests directly"
    )


def test_atlas_documentation_gate_remains_workflow_dispatch_only() -> None:
    path = REPO_ROOT / ".github" / "workflows" / "atlas-documentation-gate.yml"
    config = _load_yaml_no_duplicates(path)
    triggers = config.get("on") or config.get(True) or {}
    assert set(triggers) == {"workflow_dispatch"}, (
        "atlas-documentation-gate.yml must remain workflow_dispatch-only, "
        f"found triggers: {set(triggers)}"
    )


# ---------------------------------------------------------------------------
# AS-GH-001 artifact-closure additions (repository files only)
# ---------------------------------------------------------------------------

_REQUIRED_GOVERNANCE_DOCS = (
    "GOVERNANCE.md",
    "VERSIONING.md",
    "RELEASING.md",
    "SUPPORT.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
)

_REQUIRED_ISSUE_TEMPLATES = (
    "bug_report.yml",
    "feature_request.yml",
    "documentation.yml",
    "architecture_proposal.yml",
    "governance_gap.yml",
    "technical_debt.yml",
    "config.yml",
)

_PLACEHOLDER_CONTACT_PATTERNS = (
    re.compile(r"\bTODO@\S+", re.IGNORECASE),
    re.compile(r"\bFIXME@\S+", re.IGNORECASE),
    re.compile(r"\bexample\.com\b", re.IGNORECASE),
    re.compile(r"\bchangeme\b", re.IGNORECASE),
    re.compile(r"\breplace[_\s-]?me\b", re.IGNORECASE),
    re.compile(r"\byour[_\s-]?email\b", re.IGNORECASE),
    re.compile(r"\bsecurity@example\b", re.IGNORECASE),
    re.compile(r"\binsert[_\s-]?contact\b", re.IGNORECASE),
)

_EMAIL_SHAPE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}")

_SECRET_SOLICIT_PATTERNS = (
    # Affirmative asks only — warnings like "do not include secrets" are allowed.
    re.compile(
        r"\b(please\s+)?(paste|provide|attach|upload|enter|type)\s+your\s+"
        r"(api[_ -]?key|token|password|secret|credential|private[_ -]?key)\b",
        re.I,
    ),
    re.compile(
        r"\b(api[_ -]?key|password|private[_ -]?key|access[_ -]?token)\s*:\s*$",
        re.I | re.M,
    ),
)


def _governance_doc_paths() -> list[Path]:
    return [REPO_ROOT / name for name in _REQUIRED_GOVERNANCE_DOCS]


def _issue_template_paths() -> list[Path]:
    base = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    return [base / name for name in _REQUIRED_ISSUE_TEMPLATES]


def test_required_governance_artifacts_exist() -> None:
    missing = [str(path.name) for path in _governance_doc_paths() if not path.is_file()]
    assert not missing, f"missing required governance docs: {missing}"


def test_required_issue_templates_exist() -> None:
    missing = [path.name for path in _issue_template_paths() if not path.is_file()]
    assert not missing, f"missing required issue templates: {missing}"


def test_issue_template_yaml_parses_without_duplicate_keys() -> None:
    for path in _issue_template_paths():
        try:
            _load_yaml_no_duplicates(path)
        except ValueError as exc:
            raise AssertionError(f"duplicate key in {path}: {exc}") from exc


def test_governance_docs_have_no_placeholder_or_invented_email_contacts() -> None:
    """Closure artifacts must not invent contacts; email shapes are disallowed
    except the literal domain string inside Contributor Covenant attribution
    URLs is not an email. Any ``user@host`` form is rejected in these docs."""
    for path in [*_governance_doc_paths(), REPO_ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for pattern in _PLACEHOLDER_CONTACT_PATTERNS:
            assert not pattern.search(text), (
                f"{path.name}: placeholder contact pattern {pattern.pattern!r}"
            )
        assert not _EMAIL_SHAPE.search(text), (
            f"{path.name}: must not publish email-shaped contacts "
            "(use SECURITY.md private-channel language instead)"
        )


def test_issue_templates_do_not_solicit_secrets() -> None:
    for path in _issue_template_paths():
        text = path.read_text(encoding="utf-8")
        for pattern in _SECRET_SOLICIT_PATTERNS:
            assert not pattern.search(text), (
                f"{path.name}: must not solicit secrets ({pattern.pattern!r})"
            )
        lowered = text.lower()
        # Positive steer: templates should warn against secrets / point security away.
        if path.name != "config.yml":
            assert "secret" in lowered or "credential" in lowered, (
                f"{path.name}: expected an explicit no-secrets / credentials warning"
            )


def test_issue_templates_route_security_to_security_md() -> None:
    config = _load_yaml_no_duplicates(REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml")
    assert config.get("blank_issues_enabled") is False
    links = config.get("contact_links") or []
    assert links, "config.yml must provide contact_links including SECURITY.md routing"
    joined = " ".join(
        f"{link.get('name', '')} {link.get('url', '')} {link.get('about', '')}" for link in links
    ).lower()
    assert "security.md" in joined
    assert "not currently operational" in joined or "vulnerability" in joined

    for path in _issue_template_paths():
        if path.name == "config.yml":
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert "security.md" in text, f"{path.name}: must reference SECURITY.md"


def test_readme_links_required_governance_artifacts() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in (
        "GOVERNANCE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "SUPPORT.md",
        "CODE_OF_CONDUCT.md",
        "VERSIONING.md",
        "RELEASING.md",
    ):
        assert name in text, f"README.md must navigate to {name}"


def test_governance_md_documents_lifecycle_and_separation_of_duties() -> None:
    text = (REPO_ROOT / "GOVERNANCE.md").read_text(encoding="utf-8").lower()
    for term in (
        "project owner",
        "implementation",
        "independent verifier",
        "certif",
        "merge",
        "baseline",
        "stop",
        "emergency",
        "deferred",
    ):
        assert term in text, f"GOVERNANCE.md missing lifecycle/governance term: {term!r}"
    assert "must not both" in text or ("must not" in text and "implement" in text)


def test_releasing_md_requires_exact_identity_and_forbids_certified_squash() -> None:
    text = (REPO_ROOT / "RELEASING.md").read_text(encoding="utf-8").lower()
    for term in ("implement", "certif", "owner", "merge", "post-merge", "baseline", "sha"):
        assert term in text, f"RELEASING.md missing release-identity term: {term!r}"
    assert "squash" in text
    assert "exact" in text


def test_versioning_md_is_honest_1_0_without_fake_automation() -> None:
    raw = (REPO_ROOT / "VERSIONING.md").read_text(encoding="utf-8")
    text = raw.lower()
    assert "1.0.0" in raw
    assert "release certified" in text
    assert "pyproject.toml" in text
    assert "semver" in text or "semantic versioning" in text
    assert "automat" in text  # documents absence / non-claim of automation
    assert "do not invent" in text or "does not exist" in text


def test_support_and_conduct_document_limitations_without_invented_intake() -> None:
    support = (REPO_ROOT / "SUPPORT.md").read_text(encoding="utf-8").lower()
    conduct = (REPO_ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8").lower()
    support_limit = "sla" in support or "guaranteed" in support or "response time" in support
    assert "no" in support and support_limit
    assert "security.md" in support
    conduct_limit = "email" in conduct or "portal" in conduct or "moderator" in conduct
    assert "no" in conduct and conduct_limit
    assert "limitation" in conduct or "not" in conduct


def test_closure_docs_do_not_claim_live_settings_activated() -> None:
    """Static docs may describe deferred activation; they must not assert that
    required checks / approval restoration / CODEOWNERS enforcement are live."""
    forbidden_claims = (
        re.compile(r"required approving reviews?\s+(are|is)\s+1\b", re.I),
        re.compile(r"require_code_owner_reviews\s*=\s*true", re.I),
        re.compile(
            r"\blive\b.{0,40}\b(required checks?|branch protection)"
            r".{0,40}\b(active|enabled|enforced)\b",
            re.I,
        ),
        re.compile(
            r"\b(required checks?|branch protection)\b.{0,40}"
            r"\b(are|is)\s+now\s+(active|enabled|enforced)\b",
            re.I,
        ),
    )
    paths = [
        *_governance_doc_paths(),
        REPO_ROOT / "README.md",
        REPO_ROOT / "CONTRIBUTING.md",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_claims:
            assert not pattern.search(text), (
                f"{path.name}: false or premature live-settings claim matched {pattern.pattern!r}"
            )
    # Positive: GOVERNANCE must still say settings activation is deferred.
    gov = (REPO_ROOT / "GOVERNANCE.md").read_text(encoding="utf-8").lower()
    assert "deferred" in gov

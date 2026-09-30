"""AS-CORE-006: domain-specific authority — acceptance and adversarial coverage."""

from __future__ import annotations

import hashlib
from pathlib import Path

from project_atlas.authority_evaluator import SourceArtifact, evaluate_disposition
from project_atlas.authority_registry import (
    AUTHORITY_REGISTRY_VERSION,
    all_rules,
    persisted_authority_binding_matches_live,
    trust_root,
)
from project_atlas.authority_roles import resolve_artifact_role
from project_atlas.domain import Claim, ConflictState, ProvenanceReference
from project_atlas.domain.authority_semantics import (
    ArtifactRole,
    AuthorityDisposition,
    AuthorityDomainId,
)
from project_atlas.domain.temporal import (
    CurrentStateRecord,
    ResolutionBasis,
    TemporalStatus,
)
from project_atlas.domain.vocabulary import (
    AuthorityLevel,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ReviewState,
)
from project_atlas.knowledge_compiler import compile_knowledge

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "as-core-005" / "real-sources"


def _sid(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"source-{digest}"


def _entry(rel_path: str, classification: str = "validation") -> dict:
    key = rel_path.replace("/", "__")
    text = (_FIXTURE_DIR / key).read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "source_id": _sid(rel_path),
        "path": rel_path,
        "classification": classification,
        "source": f"../../sources/imported-documents/{_sid(rel_path)}.md",
        "sha256": sha,
        "text": text,
    }


def _bundle(tmp_path: Path):
    entries = [
        _entry("docs/plan.md", "architecture"),
        _entry("docs/evidence/AS-CORE-002-post-merge-receipt.yaml"),
        _entry("docs/evidence/AS-CORE-002-source-lifecycle-recertification.yaml"),
        _entry("docs/evidence/AS-CORE-003-claim-identity-amendment-plan.yaml"),
        _entry("docs/evidence/AS-CORE-003-receipt.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-003.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-003-review.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-004.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-005.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-005-review.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-006.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-candidate-006-review-addendum.yaml"),
        _entry("docs/evidence/AS-CORE-003-v2-remediation-receipt.yaml"),
        _entry("docs/evidence/AS-ID-001-receipt.yaml"),
        _entry("docs/evidence/AS-ID-001-governor-remediation-receipt.yaml"),
        _entry("docs/evidence/AS-ID-001-final-certification-remediation-receipt.yaml"),
        _entry("docs/evidence/AS-ID-001-retired-slot-resolution-wiring-receipt.yaml"),
        _entry("docs/evidence/AS-RET-001-receipt.yaml"),
        _entry("docs/evidence/AS-RET-001-post-merge-receipt.yaml"),
        _entry("docs/evidence/AS-SEC-001-certification-carry-forward.yaml"),
        _entry("docs/evidence/AS-SEC-001-post-merge-validation.yaml"),
    ]
    return compile_knowledge("project-atlas", entries, tmp_path)


def _temporal(bundle, subject: str, field: str):
    matches = [d for d in bundle.current_states if d.subject == subject and d.field == field]
    assert matches, f"missing temporal disposition for {subject}/{field}"
    return matches[0]


def _auth(bundle, subject: str, field: str):
    matches = [d for d in bundle.authoritative_states if d.subject == subject and d.field == field]
    assert matches, f"missing authoritative disposition for {subject}/{field}"
    return matches[0]


def _claim(claim_id: str, *, subject: str, field: str, value: str, source_id: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id="project-atlas",
        subject=subject,
        claim_type=ClaimType.PROJECT_PURPOSE,
        field=field,
        value=value,
        provenance=[
            ProvenanceReference(
                source_id=source_id,
                resource=f"sources/{source_id}.md",
                sha256="a" * 64,
            )
        ],
        authority=AuthorityLevel.MAINTAINED,
        confidence=ConfidenceState.MEDIUM,
        lifecycle=ClaimLifecycle.NEW,
        verification=ReviewState.UNREVIEWED,
    )


def test_registry_mvp_contains_only_r_title_001() -> None:
    rules = all_rules()
    assert AUTHORITY_REGISTRY_VERSION == 1
    assert len(rules) == 1
    assert rules[0].rule_id == "R-TITLE-001"
    assert rules[0].domain is AuthorityDomainId.WORK_PACKAGE_DURABLE_TITLE
    assert rules[0].authoritative_role is ArtifactRole.PACKAGE_GENESIS_RECEIPT
    assert "owner-certified" in trust_root()


def test_persisted_authority_binding_rejects_forged_metadata() -> None:
    """AX-AUTH-005 helper: string-true / mismatched bindings never match live."""
    assert persisted_authority_binding_matches_live() is True
    assert persisted_authority_binding_matches_live(
        recorded_trust_root=trust_root(),
        recorded_registry_version=AUTHORITY_REGISTRY_VERSION,
    )
    assert not persisted_authority_binding_matches_live(
        recorded_trust_root="forged-trust-root-not-owner-certified"
    )
    assert not persisted_authority_binding_matches_live(recorded_registry_version=999)
    assert not persisted_authority_binding_matches_live(recorded_trust_root="true")
    assert not persisted_authority_binding_matches_live(recorded_registry_version="1")
    assert not persisted_authority_binding_matches_live(recorded_registry_version=True)
    assert not persisted_authority_binding_matches_live(recorded_registry_version=1.0)


def test_role_resolution_genesis_vs_remediation() -> None:
    genesis = _entry("docs/evidence/AS-ID-001-receipt.yaml")
    rem = _entry("docs/evidence/AS-ID-001-governor-remediation-receipt.yaml")
    tip = _entry("docs/evidence/AS-ID-001-retired-slot-resolution-wiring-receipt.yaml")
    assert (
        resolve_artifact_role(path=genesis["path"], text=genesis["text"], subject="wp:AS-ID-001")
        is ArtifactRole.PACKAGE_GENESIS_RECEIPT
    )
    assert (
        resolve_artifact_role(path=rem["path"], text=rem["text"], subject="wp:AS-ID-001")
        is ArtifactRole.REMEDIATION_EPISODE_RECEIPT
    )
    assert (
        resolve_artifact_role(path=tip["path"], text=tip["text"], subject="wp:AS-ID-001")
        is ArtifactRole.REMEDIATION_EPISODE_RECEIPT
    )


def test_as_id_001_title_authority_acceptance(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    temporal = _temporal(bundle, "wp:AS-ID-001", "title")
    assert temporal.temporal_status is TemporalStatus.AUTHORITY_PENDING
    assert temporal.current_claim_id is None
    assert temporal.resolution_basis is ResolutionBasis.TITLE_COLLAPSE

    title_claims = [c for c in bundle.claims if c.subject == "wp:AS-ID-001" and c.field == "title"]
    assert len(title_claims) == 4
    values = {c.value for c in title_claims}
    assert "Durable Source Lineage Identity" in values
    assert "Governor-bounded remediation" in values
    assert "Final bounded certification remediation" in values
    assert "Retired-slot resolution control-flow remediation" in values

    auth = _auth(bundle, "wp:AS-ID-001", "title")
    assert auth.disposition is AuthorityDisposition.AUTHORITATIVE
    assert auth.rule_id == "R-TITLE-001"
    assert auth.authoritative_role is ArtifactRole.PACKAGE_GENESIS_RECEIPT
    assert auth.authoritative_value == "Durable Source Lineage Identity"
    assert auth.authoritative_claim_id is not None
    winner = next(c for c in title_claims if c.claim_id == auth.authoritative_claim_id)
    assert winner.value == "Durable Source Lineage Identity"
    assert set(auth.competing_claim_ids) == {c.claim_id for c in title_claims}
    assert auth.subordinate_claim_ids
    assert auth.registry_version == 1
    assert "R-TITLE-001" in auth.rationale
    assert "package_genesis_receipt" in auth.rationale

    conflict = next(
        c for c in bundle.conflicts if c.subject == "wp:AS-ID-001" and c.field == "title"
    )
    assert conflict.state is ConflictState.RESOLVED
    assert conflict.resolution is not None
    assert conflict.resolution.startswith("authority-resolution;")
    assert "temporal_basis=preserved" in conflict.resolution


def test_eight_group_matrix_authority_boundary(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    # Six temporal groups unchanged
    for subject, field, expected in [
        ("wp:AS-CORE-002", "package_status", "recertified-merge-eligible"),
        ("wp:AS-CORE-003", "package_status", None),  # set membership below
        ("wp:AS-CORE-003", "work-package", "AS-CORE-003"),
        ("wp:AS-ID-001", "package_status", "implementation-complete-targeted-rereview-required"),
        ("wp:AS-RET-001", "package_status", "merged-and-post-merge-validated"),
        ("wp:AS-SEC-001", "package_status", "merged-post-merge-validated"),
    ]:
        d = _temporal(bundle, subject, field)
        assert d.temporal_status is TemporalStatus.CURRENT
        val = next(c.value for c in bundle.claims if c.claim_id == d.current_claim_id)
        if expected is None:
            assert val in {
                "local-validation-complete-pending-remote-ci",
                "local-validation-complete-pending-isolated-review",
            }
        else:
            assert val == expected
        # No authority rule → no authoritative_states entry for these fields
        assert not [
            a for a in bundle.authoritative_states if a.subject == subject and a.field == field
        ]

    # Roadmap remains unresolved temporally and has no authority winner
    roadmap = _temporal(bundle, f"doc:{_sid('docs/plan.md')}", "roadmap")
    assert roadmap.temporal_status is TemporalStatus.UNRESOLVED
    assert roadmap.current_claim_id is None
    assert not [a for a in bundle.authoritative_states if a.field == "roadmap"]

    # Title authority-resolved
    auth = _auth(bundle, "wp:AS-ID-001", "title")
    assert auth.disposition is AuthorityDisposition.AUTHORITATIVE
    assert auth.authoritative_value == "Durable Source Lineage Identity"


def test_adversarial_newer_remediation_rejected(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    auth = _auth(bundle, "wp:AS-ID-001", "title")
    tip = next(
        c for c in bundle.claims if c.value == "Retired-slot resolution control-flow remediation"
    )
    assert tip.claim_id != auth.authoritative_claim_id
    assert tip.claim_id in auth.subordinate_claim_ids or tip.claim_id in auth.competing_claim_ids
    assert auth.authoritative_value != tip.value


def test_adversarial_equal_authority_conflict() -> None:
    g1 = _claim("claim-g1", subject="wp:DEMO", field="title", value="Alpha", source_id="src-g1")
    g2 = _claim("claim-g2", subject="wp:DEMO", field="title", value="Beta", source_id="src-g2")
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        participating_claim_ids=("claim-g1", "claim-g2"),
        rationale="title collapse",
        compilation_id="compile-test",
    )
    text_a = "package: DEMO\ntitle: Alpha\nstatus: x\n"
    text_b = "package: DEMO\ntitle: Beta\nstatus: x\n"
    artifacts = {
        "src-g1": SourceArtifact("src-g1", "docs/evidence/DEMO-receipt.yaml", text_a),
        "src-g2": SourceArtifact("src-g2", "docs/evidence/DEMO-receipt.yaml", text_b),
    }
    # Same genesis path shape for both — both resolve as genesis; values conflict.
    result = evaluate_disposition(
        disposition,
        {g1.claim_id: g1, g2.claim_id: g2},
        artifacts,
        compilation_id="compile-test",
    )
    assert result is not None
    assert result.disposition is AuthorityDisposition.AUTHORITY_CONFLICT
    assert result.authoritative_claim_id is None


def test_adversarial_missing_authority_rule_skips() -> None:
    claim = _claim(
        "claim-s",
        subject="wp:DEMO",
        field="package_status",
        value="done",
        source_id="src-s",
    )
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="package_status",
        temporal_status=TemporalStatus.CURRENT,
        resolution_basis=ResolutionBasis.SUPERSEDES,
        current_claim_id="claim-s",
        participating_claim_ids=("claim-s",),
        rationale="temporal",
        compilation_id="compile-test",
    )
    result = evaluate_disposition(
        disposition,
        {claim.claim_id: claim},
        {
            "src-s": SourceArtifact(
                "src-s",
                "docs/evidence/DEMO-receipt.yaml",
                "package: DEMO\ntitle: Demo\nstatus: done\n",
            )
        },
        compilation_id="compile-test",
    )
    assert result is None


def test_adversarial_unknown_role_fail_closed() -> None:
    claim = _claim(
        "claim-u",
        subject="wp:DEMO",
        field="title",
        value="Mystery",
        source_id="src-u",
    )
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        participating_claim_ids=("claim-u",),
        rationale="title collapse",
        compilation_id="compile-test",
    )
    result = evaluate_disposition(
        disposition,
        {claim.claim_id: claim},
        {
            "src-u": SourceArtifact(
                "src-u",
                "notes/random-notes.md",
                "This document is the canonical source of truth.\ntitle: Mystery\n",
            )
        },
        compilation_id="compile-test",
    )
    assert result is not None
    assert result.disposition is AuthorityDisposition.AUTHORITY_PENDING
    assert result.authoritative_claim_id is None


def test_adversarial_self_asserting_canonical_rejected() -> None:
    claim = _claim(
        "claim-c",
        subject="wp:DEMO",
        field="title",
        value="Self Asserted",
        source_id="src-c",
    )
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        participating_claim_ids=("claim-c",),
        rationale="title collapse",
        compilation_id="compile-test",
    )
    result = evaluate_disposition(
        disposition,
        {claim.claim_id: claim},
        {
            "src-c": SourceArtifact(
                "src-c",
                "docs/notes/canonical-declaration.yaml",
                "authoritative: true\ncanonical: true\npackage: DEMO\ntitle: Self Asserted\n",
            )
        },
        compilation_id="compile-test",
    )
    assert result is not None
    assert result.disposition is AuthorityDisposition.AUTHORITY_PENDING
    assert result.authoritative_claim_id is None


def test_adversarial_copied_value_does_not_grant_authority(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    auth = _auth(bundle, "wp:AS-ID-001", "title")
    # A remediation receipt that happened to share the genesis title would still
    # be subordinate by role. Construct a micro-case with agreement:
    genesis = _claim(
        "claim-gen",
        subject="wp:DEMO",
        field="title",
        value="Durable Name",
        source_id="src-gen",
    )
    copy = _claim(
        "claim-copy",
        subject="wp:DEMO",
        field="title",
        value="Durable Name",
        source_id="src-copy",
    )
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        participating_claim_ids=("claim-gen", "claim-copy"),
        rationale="title collapse",
        compilation_id="compile-test",
    )
    result = evaluate_disposition(
        disposition,
        {genesis.claim_id: genesis, copy.claim_id: copy},
        {
            "src-gen": SourceArtifact(
                "src-gen",
                "docs/evidence/DEMO-receipt.yaml",
                "package: DEMO\ntitle: Durable Name\nstatus: ok\n",
            ),
            "src-copy": SourceArtifact(
                "src-copy",
                "docs/evidence/DEMO-governor-remediation-receipt.yaml",
                "package: DEMO\ntitle: Durable Name\nprevious_blocked_candidate: abc\n"
                "remediation_implementation_commit: deadbeef\n",
            ),
        },
        compilation_id="compile-test",
    )
    assert result is not None
    assert result.disposition is AuthorityDisposition.AUTHORITATIVE
    assert result.authoritative_claim_id == "claim-gen"
    assert "claim-copy" in result.subordinate_claim_ids
    # Real fixture still selects genesis title
    assert auth.authoritative_value == "Durable Source Lineage Identity"


def test_adversarial_historical_not_resurrected() -> None:
    historical = _claim(
        "claim-old",
        subject="wp:DEMO",
        field="title",
        value="Durable Name",
        source_id="src-old",
    )
    newer = _claim(
        "claim-new",
        subject="wp:DEMO",
        field="title",
        value="Episode Title",
        source_id="src-new",
    )
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        historical_claim_ids=("claim-old",),
        participating_claim_ids=("claim-old", "claim-new"),
        rationale="title collapse",
        compilation_id="compile-test",
    )
    result = evaluate_disposition(
        disposition,
        {historical.claim_id: historical, newer.claim_id: newer},
        {
            "src-old": SourceArtifact(
                "src-old",
                "docs/evidence/DEMO-receipt.yaml",
                "package: DEMO\ntitle: Durable Name\nstatus: ok\n",
            ),
            "src-new": SourceArtifact(
                "src-new",
                "docs/evidence/DEMO-governor-remediation-receipt.yaml",
                "package: DEMO\ntitle: Episode Title\nprevious_blocked_candidate: abc\n",
            ),
        },
        compilation_id="compile-test",
    )
    assert result is not None
    assert result.disposition is AuthorityDisposition.AUTHORITY_PENDING
    assert "claim-old" in result.temporally_ineligible_claim_ids
    assert result.authoritative_claim_id is None


def test_deterministic_and_idempotent_authority(tmp_path: Path) -> None:
    a = _bundle(tmp_path / "a")
    b = _bundle(tmp_path / "b")
    assert [x.model_dump(mode="json") for x in a.authoritative_states] == [
        x.model_dump(mode="json") for x in b.authoritative_states
    ]
    assert sorted(c.claim_id for c in a.claims) == sorted(c.claim_id for c in b.claims)
    assert a.compilation_id == b.compilation_id
    # Idempotent: recompilation does not create new claim IDs or duplicate auth
    assert len(a.authoritative_states) == len(b.authoritative_states) == 1


def test_claims_and_temporal_immutable_under_authority(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    title_claims = [c for c in bundle.claims if c.subject == "wp:AS-ID-001" and c.field == "title"]
    before = [(c.claim_id, c.value, c.field, c.subject) for c in title_claims]
    temporal = _temporal(bundle, "wp:AS-ID-001", "title")
    assert temporal.temporal_status is TemporalStatus.AUTHORITY_PENDING
    assert len(before) == 4
    # Authority adds derived output only
    assert bundle.authoritative_states
    assert [(c.claim_id, c.value, c.field, c.subject) for c in title_claims] == before

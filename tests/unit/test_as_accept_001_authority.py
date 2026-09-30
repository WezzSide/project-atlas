"""AS-ACCEPT-001 Wave-A authority cases (AX-AUTH-*).

Oracles: INV-003, INV-004 — no cross-field/domain laundering, no forged trust
root as truth, no recency/lexical tie-break after equal-genesis conflict.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from project_atlas.authority_evaluator import SourceArtifact, evaluate_disposition
from project_atlas.authority_registry import AUTHORITY_REGISTRY_VERSION, trust_root
from project_atlas.domain import Claim, ProvenanceReference
from project_atlas.domain.authority_semantics import AuthorityDisposition
from project_atlas.domain.knowledge_query import AnswerStatus
from project_atlas.domain.temporal import CurrentStateRecord, ResolutionBasis, TemporalStatus
from project_atlas.domain.vocabulary import (
    AuthorityLevel,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ReviewState,
)
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import KnowledgeQueryError, query_knowledge

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "as-core-005" / "real-sources"


def _sid(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"source-{digest}"


def _entry(rel_path: str, classification: str = "validation") -> dict[str, Any]:
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


def _entries() -> list[dict[str, Any]]:
    return [
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


def _materialize_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for rel in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# scaffold\n", encoding="utf-8")
    bundle = compile_knowledge("project-atlas", _entries(), tmp_path / "compile")
    for rel, content in render_bundle(bundle, "project-atlas").items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return vault


def _claim(
    claim_id: str,
    *,
    subject: str,
    field: str,
    value: str,
    source_id: str,
) -> Claim:
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


def test_ax_auth_003_malformed_amends_field_never_cross_field_launders() -> None:
    """AX-AUTH-003: malformed amends_field must not launder across fields.

    INV-003 / INV-004 — pending or skip; never package_status authority from a
    title-shaped amendment that declares amends_field=package_status.
    """
    title_claim = _claim(
        "claim-amend",
        subject="wp:DEMO",
        field="title",
        value="Amended Title",
        source_id="src-amend",
    )
    status_claim = _claim(
        "claim-status",
        subject="wp:DEMO",
        field="package_status",
        value="forged-via-amend",
        source_id="src-amend",
    )
    text = (
        "package: DEMO\n"
        "title: Amended Title\n"
        "amends_field: package_status\n"
        "status: forged-via-amend\n"
    )
    title_disp = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        participating_claim_ids=("claim-amend",),
        rationale="title collapse",
        compilation_id="compile-ax-auth-003",
    )
    status_disp = CurrentStateRecord(
        subject="wp:DEMO",
        field="package_status",
        temporal_status=TemporalStatus.CURRENT,
        resolution_basis=ResolutionBasis.REOBSERVATION,
        current_claim_id="claim-status",
        participating_claim_ids=("claim-status",),
        rationale="temporal current",
        compilation_id="compile-ax-auth-003",
    )
    artifacts = {
        "src-amend": SourceArtifact(
            "src-amend",
            "docs/evidence/DEMO-title-amendment.yaml",
            text,
        )
    }
    title_result = evaluate_disposition(
        title_disp,
        {title_claim.claim_id: title_claim},
        artifacts,
        compilation_id="compile-ax-auth-003",
    )
    status_result = evaluate_disposition(
        status_disp,
        {status_claim.claim_id: status_claim},
        artifacts,
        compilation_id="compile-ax-auth-003",
    )
    # package_status has no MVP rule → skip (None); never authoritative laundering.
    assert status_result is None
    # Title path is not a genesis receipt shape → pending / no silent win from amends_field.
    assert title_result is not None
    assert title_result.disposition is AuthorityDisposition.AUTHORITY_PENDING
    assert title_result.authoritative_claim_id is None
    assert title_result.authoritative_value is None


def test_ax_auth_004_cross_domain_title_does_not_force_package_status(
    tmp_path: Path,
) -> None:
    """AX-AUTH-004: authoritative title must not force package_status winner.

    INV-003 — domains isolated; status remains non-authoritative under MVP.
    """
    vault = _materialize_vault(tmp_path)
    title = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    status = query_knowledge(
        vault, "project-atlas", "wp:AS-ID-001", "package_status", kind="authoritative"
    )
    assert title.status is AnswerStatus.OK
    assert title.value == "Durable Source Lineage Identity"
    assert status.value is None
    assert status.status is AnswerStatus.NOT_FOUND
    assert status.authority_disposition is None


def test_ax_auth_005_forged_trust_root_fail_closed_or_regenerate(
    tmp_path: Path,
) -> None:
    """AX-AUTH-005: forged trust root / registry version must not stick as truth.

    Expected: query/validate fail-closed on consume; compile regenerate ignores
    forgery (INV-003 / INV-004 / INV-008).
    """
    vault = _materialize_vault(tmp_path)
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["authority_registry_version"] = 999
    for item in raw["authoritative_states"]:
        item["trust_root"] = "forged-trust-root-not-owner-certified"
        item["registry_version"] = 999
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    consume_fail_closed = False
    try:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    except KnowledgeQueryError:
        consume_fail_closed = True

    # Compile ignores forgery on regenerate (always required).
    bundle = compile_knowledge("project-atlas", _entries(), tmp_path / "recompile")
    regenerated = render_bundle(bundle, "project-atlas")
    auth_fresh = json.loads(regenerated["state/authoritative-state/project-atlas.json"])
    assert auth_fresh["authority_registry_version"] == AUTHORITY_REGISTRY_VERSION
    for item in auth_fresh["authoritative_states"]:
        assert item["trust_root"] == trust_root()
        assert item["registry_version"] == AUTHORITY_REGISTRY_VERSION
        assert item["trust_root"] != "forged-trust-root-not-owner-certified"

    assert consume_fail_closed, (
        "AX-AUTH-005 consume must fail-closed on forged trust_root/registry_version"
    )


def test_ax_auth_009_equal_genesis_conflict_no_lexical_tiebreak() -> None:
    """AX-AUTH-009: equal genesis conflict remains conflict; no lexical/recency pick.

    INV-003 / INV-004.
    """
    g1 = _claim("claim-zzz", subject="wp:DEMO", field="title", value="Alpha", source_id="src-g1")
    g2 = _claim("claim-aaa", subject="wp:DEMO", field="title", value="Beta", source_id="src-g2")
    disposition = CurrentStateRecord(
        subject="wp:DEMO",
        field="title",
        temporal_status=TemporalStatus.AUTHORITY_PENDING,
        resolution_basis=ResolutionBasis.TITLE_COLLAPSE,
        current_claim_id=None,
        participating_claim_ids=("claim-zzz", "claim-aaa"),
        rationale="title collapse",
        compilation_id="compile-ax-auth-009",
    )
    artifacts = {
        "src-g1": SourceArtifact(
            "src-g1", "docs/evidence/DEMO-receipt.yaml", "package: DEMO\ntitle: Alpha\n"
        ),
        "src-g2": SourceArtifact(
            "src-g2", "docs/evidence/DEMO-receipt.yaml", "package: DEMO\ntitle: Beta\n"
        ),
    }
    # Order temptation: evaluate with claim map keys sorted both ways via reverse insert.
    for claims_by_id in (
        {g1.claim_id: g1, g2.claim_id: g2},
        {g2.claim_id: g2, g1.claim_id: g1},
    ):
        result = evaluate_disposition(
            disposition,
            claims_by_id,
            artifacts,
            compilation_id="compile-ax-auth-009",
        )
        assert result is not None
        assert result.disposition is AuthorityDisposition.AUTHORITY_CONFLICT
        assert result.authoritative_claim_id is None
        assert result.authoritative_value is None
        assert set(result.competing_claim_ids) == {"claim-zzz", "claim-aaa"}

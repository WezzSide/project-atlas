"""AS-2.0-DEP-001 — evidence-backed project dependencies."""

from __future__ import annotations

import pytest

from project_atlas.domain import (
    AuthorityLevel,
    Claim,
    ClaimLifecycle,
    ClaimType,
    ConfidenceState,
    ProvenanceReference,
    ReviewState,
)
from project_atlas.intelligence import DEPENDENCY_IS_INFERRED
from project_atlas.intelligence.dependencies import (
    ProjectDependencyClass,
    detect_project_dependencies,
)
from project_atlas.intelligence.types import AssessableClaim

HASH_A = "a" * 64


def _claim(
    claim_id: str,
    *,
    field: str,
    value: str,
    claim_type: ClaimType = ClaimType.ARCHITECTURE,
    source_id: str = "src-a",
    resource: str = "docs/src-a.md",
    lifecycle: ClaimLifecycle = ClaimLifecycle.NEW,
    project_id: str = "harbor-api",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id=project_id,
        subject=f"project:{project_id}",
        claim_type=claim_type,
        field=field,
        value=value,
        provenance=[ProvenanceReference(source_id=source_id, resource=resource, sha256=HASH_A)],
        authority=AuthorityLevel.PRIMARY,
        confidence=ConfidenceState.HIGH,
        lifecycle=lifecycle,
        verification=ReviewState.UNREVIEWED,
    )


def test_inferred_flag_is_no() -> None:
    assert DEPENDENCY_IS_INFERRED == "NO"


def test_explicit_depends_on_is_an_edge() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [
            _claim(
                "dep-1",
                field="depends_on",
                value="lighthouse",
                claim_type=ClaimType.RUNTIME_DEPENDENCY,
            )
        ],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert len(found) == 1
    assert found[0].dep_class is ProjectDependencyClass.EXPLICIT
    assert found[0].inferred is False
    assert found[0].target_project_id == "lighthouse"
    assert found[0].claim_id == "dep-1"
    assert found[0].evidence_refs
    assert found[0].authority_note == "dependency-not-inferred"


def test_explicit_candidate_is_not_inferred() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [
            AssessableClaim(
                claim_id="cand-1",
                project_id="harbor-api",
                subject="project:harbor-api",
                field="dependency_candidate",
                value="lighthouse",
                claim_type="dependency-candidate",
                provenance=(
                    ProvenanceReference(source_id="src-a", resource="docs/src-a.md", sha256=HASH_A),
                ),
            )
        ],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert found
    assert found[0].dep_class is ProjectDependencyClass.EXPLICIT_CANDIDATE
    assert found[0].inferred is False


def test_unknown_target_is_unresolved_not_invented() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [
            _claim(
                "dep-x",
                field="depends_on",
                value="missing-service",
                claim_type=ClaimType.RUNTIME_DEPENDENCY,
            )
        ],
        known_project_ids=("harbor-api",),
    )
    assert found
    assert found[0].dep_class is ProjectDependencyClass.UNRESOLVED_TARGET
    assert found[0].target_project_id is None
    assert found[0].target_name == "missing-service"


def test_shared_words_are_not_a_dependency() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [_claim("h1", field="purpose", value="harbor api service")],
        known_project_ids=("harbor-api", "harbor-ui"),
    )
    assert found == ()


def test_shared_datastore_is_not_a_dependency() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [_claim("h1", field="datastore", value="PostgreSQL 16")],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert found == ()


def test_shared_file_is_not_a_dependency() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [_claim("h1", field="readme", value="docs/shared.md", resource="docs/shared.md")],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert found == ()


def test_same_source_owner_is_not_a_dependency() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [_claim("h1", field="owner", value="platform-team", source_id="src-shared")],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert found == ()


def test_simultaneous_change_is_not_a_dependency() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [
            _claim(
                "h1",
                field="datastore",
                value="PostgreSQL 16",
                lifecycle=ClaimLifecycle.UPDATED,
            )
        ],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert found == ()


def test_foreign_project_claims_are_ignored() -> None:
    found = detect_project_dependencies(
        "harbor-api",
        [
            _claim(
                "other",
                field="depends_on",
                value="lighthouse",
                claim_type=ClaimType.RUNTIME_DEPENDENCY,
                project_id="other-api",
            )
        ],
        known_project_ids=("harbor-api", "lighthouse"),
    )
    assert found == ()


def test_empty_project_id_fails_closed() -> None:
    with pytest.raises(ValueError):
        detect_project_dependencies("  ", [])

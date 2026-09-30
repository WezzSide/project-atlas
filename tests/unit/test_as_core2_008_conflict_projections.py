"""AS-CORE2-008 — duplicate-source facets + review-queue honesty (C8-FR-001…015)."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.conflict_projections import (
    DUPLICATE_SOURCE_KIND,
    conflict_index_companions,
    conflict_markdown_line,
    conflict_review_reason,
    duplicate_source_facet,
    harden_conflict_reviews,
    review_index_companions,
)
from project_atlas.domain import (
    ConflictingClaim,
    ConflictRecord,
    ConflictState,
    ProvenanceReference,
    ReviewCategory,
    ReviewEntry,
)
from project_atlas.domain.authority_semantics import (
    AuthoritativeStateRecord,
    AuthorityDisposition,
    AuthorityDomainId,
)
from project_atlas.domain.vocabulary import ConflictType
from project_atlas.indexes import build_indexes, canonical_index_payloads
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.schema import validate_record

HASH_A = "a" * 64
HASH_B = "b" * 64


def _entry(
    source_id: str,
    path: str,
    text: str,
    sha256: str,
    classification: str,
    *,
    source_lineage_id: str | None = None,
) -> dict[str, str]:
    if not text.startswith("# "):
        text = f"# Overview\n{text}"
    payload: dict[str, str] = {
        "source_id": source_id,
        "path": path,
        "classification": classification,
        "source": f"../../sources/imported-documents/{source_id}.md",
        "sha256": sha256,
        "text": text,
    }
    if source_lineage_id is not None:
        payload["source_lineage_id"] = source_lineage_id
    return payload


def _conflict(
    *,
    conflict_id: str = "conflict-demo",
    project_id: str = "project-1",
    sides: list[tuple[str, str | None, str]] | None = None,
) -> ConflictRecord:
    if sides is None:
        sides = [
            ("source-a", "sline-aaa", "port 8000"),
            ("source-b", "sline-bbb", "port 9000"),
        ]
    return ConflictRecord(
        conflict_id=conflict_id,
        project_id=project_id,
        subject="doc:deployment-target",
        field="deployment",
        claims=[
            ConflictingClaim(source_id=sid, source_lineage_id=sline, claim=value)
            for sid, sline, value in sides
        ],
        claim_ids=[f"claim-{i}" for i in range(len(sides))],
        source_lineage_ids=sorted({sline for _sid, sline, _value in sides if sline is not None}),
        conflict_type=ConflictType.MATERIALLY_INCOMPATIBLE,
        provenance=[
            ProvenanceReference(
                source_id=sid,
                project_id=project_id,
                resource=f"sources/{sid}.md",
                sha256=HASH_A,
                source_lineage_id=sline,
            )
            for sid, sline, _value in sides
        ],
        state=ConflictState.UNRESOLVED,
    )


def test_c8_fr001_helper_module_exports_projection_api() -> None:
    assert callable(duplicate_source_facet)
    assert callable(conflict_review_reason)
    assert callable(conflict_index_companions)
    assert callable(review_index_companions)


def test_c8_fr002_duplicate_source_facet_when_multiple_source_ids() -> None:
    conflict = _conflict()
    facet = duplicate_source_facet(conflict)
    assert facet is not None
    assert facet["kind"] == DUPLICATE_SOURCE_KIND
    assert facet["source_ids"] == ["source-a", "source-b"]
    assert facet["source_lineage_ids"] == ["sline-aaa", "sline-bbb"]


def test_c8_fr002_same_source_omits_facet_temporal_plane() -> None:
    """Same-source multi-value is TEMPORAL's plane — omit duplicate-source facet."""
    conflict = _conflict(
        sides=[
            ("source-a", "sline-aaa", "port 8000"),
            ("source-a", "sline-aaa", "port 9000"),
        ]
    )
    assert duplicate_source_facet(conflict) is None


def test_c8_fr002_facet_replay_stable() -> None:
    conflict = _conflict(
        sides=[
            ("source-b", "sline-bbb", "b"),
            ("source-a", "sline-aaa", "a"),
            ("source-c", None, "c"),
        ]
    )
    first = duplicate_source_facet(conflict)
    second = duplicate_source_facet(conflict)
    assert first == second
    assert first is not None
    assert first["source_ids"] == ["source-a", "source-b", "source-c"]


def test_c8_fr003_conflict_index_companions() -> None:
    conflict = _conflict()
    records = [conflict.model_dump(mode="json")]
    companions = conflict_index_companions(records)
    assert "source-a" in companions["by_source_id"]
    assert "source-b" in companions["by_source_id"]
    assert conflict.conflict_id in companions["by_source_id"]["source-a"]
    assert "sline-aaa" in companions["by_source_lineage_id"]
    assert companions["by_project_id"]["project-1"] == [conflict.conflict_id]


def test_c8_fr003_indexes_emit_additive_conflict_keys(tmp_path: Path) -> None:
    shared = "semantic_subject: deployment-target\nsemantic_kind: doc\n"
    entries = [
        _entry(
            "source-a",
            "ARCHITECTURE.md",
            shared + "Deployment: port 8000",
            HASH_A,
            "architecture",
            source_lineage_id="sline-aaa",
        ),
        _entry(
            "source-b",
            "OPERATIONS.md",
            shared + "Deployment: port 9000",
            HASH_B,
            "operations",
            source_lineage_id="sline-bbb",
        ),
    ]
    bundle = compile_knowledge("project-1", entries, tmp_path)
    rendered = render_bundle(bundle, "project-1")
    for relative, content in rendered.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    payloads = canonical_index_payloads(tmp_path)
    conflicts_path = tmp_path / "generated" / "indexes" / "conflicts.json"
    assert conflicts_path in payloads
    index = json.loads(payloads[conflicts_path].decode("utf-8"))
    assert "by_conflict_id" in index
    assert "by_claim_pair" in index
    assert "by_source_id" in index
    assert "by_source_lineage_id" in index
    assert "by_project_id" in index
    assert "source-a" in index["by_source_id"]
    assert "project-1" in index["by_project_id"]


def test_c8_fr004_review_reason_includes_duplicate_source_and_authority() -> None:
    conflict = _conflict()
    auth = AuthoritativeStateRecord(
        project_id="project-1",
        subject="doc:deployment-target",
        field="deployment",
        authority_domain=AuthorityDomainId.WORK_PACKAGE_DURABLE_TITLE,
        disposition=AuthorityDisposition.AUTHORITY_CONFLICT,
        competing_claim_ids=("claim-0", "claim-1"),
        rationale="equal authority; no winner invented",
        compilation_id="compile-demo",
        registry_version=1,
        trust_root="registry",
    )
    reason = conflict_review_reason(conflict, (auth,))
    assert DUPLICATE_SOURCE_KIND in reason
    assert "sources=source-a,source-b" in reason
    assert "authority_disposition=authority-conflict" in reason
    assert "trust_score" not in reason
    assert "trust_score" not in reason.lower()


def test_c8_fr004_compile_hardens_conflict_review_queue(tmp_path: Path) -> None:
    shared = "semantic_subject: deployment-target\nsemantic_kind: doc\n"
    bundle = compile_knowledge(
        "project-1",
        [
            _entry(
                "source-a",
                "ARCHITECTURE.md",
                shared + "Deployment: port 8000",
                HASH_A,
                "architecture",
            ),
            _entry(
                "source-b",
                "OPERATIONS.md",
                shared + "Deployment: port 9000",
                HASH_B,
                "operations",
            ),
        ],
        tmp_path,
    )
    conflict_reviews = [item for item in bundle.reviews if item.category is ReviewCategory.CONFLICT]
    assert conflict_reviews
    assert all(DUPLICATE_SOURCE_KIND in item.reason for item in conflict_reviews)
    for item in conflict_reviews:
        validate_record(item, "review-entry")


def test_c8_fr005_reviews_json_companion_not_second_queue_root(tmp_path: Path) -> None:
    shared = "semantic_subject: deployment-target\nsemantic_kind: doc\n"
    bundle = compile_knowledge(
        "project-1",
        [
            _entry(
                "source-a",
                "ARCHITECTURE.md",
                shared + "Deployment: port 8000",
                HASH_A,
                "architecture",
            ),
            _entry(
                "source-b",
                "OPERATIONS.md",
                shared + "Deployment: port 9000",
                HASH_B,
                "operations",
            ),
        ],
        tmp_path,
    )
    for relative, content in render_bundle(bundle, "project-1").items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    build_indexes(tmp_path)
    reviews_index = json.loads(
        (tmp_path / "generated" / "indexes" / "reviews.json").read_text(encoding="utf-8")
    )
    assert reviews_index["schema_version"] == 1
    assert "by_review_id" in reviews_index
    assert "by_category" in reviews_index
    assert "conflict" in reviews_index["by_category"]
    # Durable queue root remains review/pending — companion is indexes only.
    assert (tmp_path / "review" / "pending" / "project-1.json").is_file()
    assert not (tmp_path / "review" / "reviews").exists()
    assert not (tmp_path / "review" / "queue").exists()


def test_c8_fr006_consume_existing_conflict_spine_no_reidentity(tmp_path: Path) -> None:
    shared = "semantic_subject: deployment-target\nsemantic_kind: doc\n"
    entries = [
        _entry(
            "source-a",
            "ARCHITECTURE.md",
            shared + "Deployment: port 8000",
            HASH_A,
            "architecture",
        ),
        _entry(
            "source-b",
            "OPERATIONS.md",
            shared + "Deployment: port 9000",
            HASH_B,
            "operations",
        ),
    ]
    first = compile_knowledge("project-1", entries, tmp_path)
    second = compile_knowledge("project-1", entries, tmp_path)
    assert first.conflicts[0].conflict_id == second.conflicts[0].conflict_id
    assert first.conflicts[0].conflict_type is ConflictType.MATERIALLY_INCOMPATIBLE


def test_c8_fr007_conflict_type_not_expanded() -> None:
    assert list(ConflictType) == [ConflictType.MATERIALLY_INCOMPATIBLE]
    conflict = _conflict()
    validate_record(conflict, "conflict-record")


def test_c8_fr008_no_graph_invent_api() -> None:
    import project_atlas.conflict_projections as mod

    forbidden = [
        name for name in dir(mod) if "graph" in name.lower() or name.lower().startswith("from_edge")
    ]
    assert forbidden == []


def test_c8_fr009_no_trust_score_fields_in_facet() -> None:
    facet = duplicate_source_facet(_conflict())
    assert facet is not None
    assert "trust" not in json.dumps(facet).lower()
    assert "score" not in json.dumps(facet).lower()


def test_c8_fr012_deterministic_json_sort_keys(tmp_path: Path) -> None:
    companions = conflict_index_companions([_conflict().model_dump(mode="json")])
    encoded = json.dumps(companions, indent=2, sort_keys=True) + "\n"
    assert encoded == json.dumps(json.loads(encoded), indent=2, sort_keys=True) + "\n"
    reviews = review_index_companions(
        [
            ReviewEntry(
                review_id="review-1",
                project_id="project-1",
                category=ReviewCategory.CONFLICT,
                subject_id="conflict-demo",
                reason="materially incompatible source-backed claims",
                source_ids=["source-a", "source-b"],
            ).model_dump(mode="json")
        ]
    )
    encoded_reviews = json.dumps(reviews, indent=2, sort_keys=True) + "\n"
    assert encoded_reviews == (
        json.dumps(json.loads(encoded_reviews), indent=2, sort_keys=True) + "\n"
    )


def test_c8_markdown_honesty_shows_duplicate_source() -> None:
    line = conflict_markdown_line(_conflict())
    assert DUPLICATE_SOURCE_KIND in line
    assert "source-a" in line and "source-b" in line
    same = conflict_markdown_line(
        _conflict(
            sides=[
                ("source-a", "sline-aaa", "x"),
                ("source-a", "sline-aaa", "y"),
            ]
        )
    )
    assert DUPLICATE_SOURCE_KIND not in same


def test_c8_harden_conflict_reviews_helper() -> None:
    conflict = _conflict()
    entry = ReviewEntry(
        review_id="review-1",
        project_id="project-1",
        category=ReviewCategory.CONFLICT,
        subject_id=conflict.conflict_id,
        reason="materially incompatible source-backed claims",
        source_ids=["source-a", "source-b"],
    )
    other = ReviewEntry(
        review_id="review-2",
        project_id="project-1",
        category=ReviewCategory.PENDING_CLAIM,
        subject_id="claim-0",
        reason="claim requires human verification",
        source_ids=["source-a"],
    )
    hardened = harden_conflict_reviews([entry, other], [conflict], ())
    assert DUPLICATE_SOURCE_KIND in hardened[0].reason
    assert hardened[1].reason == other.reason


def test_c8_compile_render_conflicts_md_honesty(tmp_path: Path) -> None:
    shared = "semantic_subject: deployment-target\nsemantic_kind: doc\n"
    bundle = compile_knowledge(
        "project-1",
        [
            _entry(
                "source-a",
                "ARCHITECTURE.md",
                shared + "Deployment: port 8000",
                HASH_A,
                "architecture",
            ),
            _entry(
                "source-b",
                "OPERATIONS.md",
                shared + "Deployment: port 9000",
                HASH_B,
                "operations",
            ),
        ],
        tmp_path,
    )
    rendered = render_bundle(bundle, "project-1")
    md = rendered["projects/project-1/conflicts.md"]
    assert DUPLICATE_SOURCE_KIND in md
    for conflict in bundle.conflicts:
        validate_record(conflict, "conflict-record")

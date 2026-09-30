"""AS-CORE2-008 adversarial / invariant defenses (C8-ADV / C8-INV)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from project_atlas.conflict_projections import (
    conflict_review_reason,
    duplicate_source_facet,
)
from project_atlas.domain import ConflictingClaim, ConflictRecord
from project_atlas.domain.conflicts import ConflictState

_ROOT = Path(__file__).resolve().parents[2]
_HELPER = _ROOT / "src" / "project_atlas" / "conflict_projections.py"
_COMPILER = _ROOT / "src" / "project_atlas" / "knowledge_compiler.py"
_INDEXES = _ROOT / "src" / "project_atlas" / "indexes.py"
_TEMPORAL = _ROOT / "src" / "project_atlas" / "temporal_evaluator.py"
_XPROJ_INDEXES = _ROOT / "src" / "project_atlas" / "xproj_indexes.py"
_GRAPH_PROJ = _ROOT / "src" / "project_atlas" / "graph_projections.py"


def _conflict_same_source() -> ConflictRecord:
    return ConflictRecord(
        conflict_id="conflict-same-001",
        project_id="proj-demo",
        subject="project:demo",
        field="status",
        claims=[
            ConflictingClaim(source_id="source-a", claim="alpha", source_lineage_id="sline-aaa"),
            ConflictingClaim(source_id="source-a", claim="beta", source_lineage_id="sline-aaa"),
        ],
        claim_ids=["claim-1", "claim-2"],
        source_lineage_ids=["sline-aaa"],
        state=ConflictState.UNRESOLVED,
    )


def test_c8_adv001_no_graph_invent_imports() -> None:
    src = _HELPER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "graph_" not in node.module
            assert node.module != "project_atlas.graph_projections"
            assert node.module != "project_atlas.graph_relationships"
            assert node.module != "project_atlas.graph_quarantine"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "graph_" not in alias.name
    assert "edge→claim" not in src
    assert "synthesize" not in src.lower() or "never invent" in src.lower()


def test_c8_adv002_no_trust_score_fields() -> None:
    src = _HELPER.read_text(encoding="utf-8").lower()
    assert "trust_score" not in src
    assert "confidence_score" not in src
    conflict = _conflict_same_source()
    reason = conflict_review_reason(conflict)
    assert "trust_score" not in reason
    assert "confidence_score" not in reason


def test_c8_adv003_no_dual_own_xproj_obs_incr_paths() -> None:
    src = _HELPER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {
        "project_atlas.xproj_indexes",
        "project_atlas.xproj_duplicates",
        "project_atlas.ops_health",
        "project_atlas.ops_events",
        "project_atlas.ops_report",
        "project_atlas.compile_cache",
        "project_atlas.temporal_evaluator",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden
    assert "duplicate-candidates" not in src
    assert "compile-cache" not in src
    assert "generated/ops" not in src
    assert "UNRESOLVED_SAME_SOURCE_MULTI" not in src


def test_c8_adv004_model_composition_not_reopened() -> None:
    helper = _HELPER.read_text(encoding="utf-8")
    for marker in (
        "concept_maturity",
        "capability_emission",
        "granularity_rules",
        "MODEL-001",
    ):
        assert marker not in helper
    indexes_src = _INDEXES.read_text(encoding="utf-8")
    assert "conflict_index_companions" in indexes_src
    assert "review_index_companions" in indexes_src


def test_c8_adv005_core2_009_promote_not_touched() -> None:
    src = _HELPER.read_text(encoding="utf-8")
    assert "_promote" not in src
    assert "interrupted-write" not in src
    assert "atomic promote" not in src.lower()


def test_c8_adv006_same_source_facet_omitted_not_temporal_rewrite() -> None:
    """Same-source multi-value is TEMPORAL's plane — omit facet; no writer dual-own."""
    conflict = _conflict_same_source()
    assert duplicate_source_facet(conflict) is None
    temporal_src = _TEMPORAL.read_text(encoding="utf-8")
    assert "UNRESOLVED_SAME_SOURCE_MULTI" in temporal_src
    helper = _HELPER.read_text(encoding="utf-8")
    assert "temporal_evaluator" not in helper


def test_c8_adv007_no_second_review_queue_root() -> None:
    helper = _HELPER.read_text(encoding="utf-8")
    assert "review/queue" not in helper
    assert "not invent a second" in helper.lower()
    indexes = _INDEXES.read_text(encoding="utf-8")
    assert 'vault / "review" / "pending"' in indexes
    assert 'vault / "review" / "queue"' not in indexes


def test_c8_adv008_conflict_type_enum_not_expanded() -> None:
    vocab = (_ROOT / "src" / "project_atlas" / "domain" / "vocabulary.py").read_text(
        encoding="utf-8"
    )
    # Closed ConflictType surface — CORE2-008 must not grow it.
    assert 'MATERIALLY_INCOMPATIBLE = "materially-incompatible"' in vocab
    helper = _HELPER.read_text(encoding="utf-8")
    assert "ConflictType" not in helper


def test_c8_adv009_foreign_product_writers_still_present_untouched() -> None:
    assert _XPROJ_INDEXES.is_file()
    assert _GRAPH_PROJ.is_file()
    assert _TEMPORAL.is_file()
    xproj = _XPROJ_INDEXES.read_text(encoding="utf-8")
    assert "xproj" in xproj.lower()


def test_c8_adv010_knowledge_compiler_hooks_are_minimal() -> None:
    compiler = _COMPILER.read_text(encoding="utf-8")
    assert "conflict_projections" in compiler
    assert "conflict_review_reason" in compiler
    assert "conflict_markdown_line" in compiler
    assert "duplicate_source_facet" in compiler
    tree = ast.parse(compiler)
    imported_helpers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "project_atlas.conflict_projections":
            imported_helpers.update(alias.name for alias in node.names)
    assert "conflict_review_reason" in imported_helpers
    assert "conflict_markdown_line" in imported_helpers
    assert "duplicate_source_facet" in imported_helpers


@pytest.mark.parametrize(
    "forbidden",
    [
        "trust_score",
        "confidence_score",
        "graph_edge_conflict",
        "invent_conflict_from_edge",
    ],
)
def test_c8_adv011_forbidden_tokens_absent_from_helper(forbidden: str) -> None:
    src = _HELPER.read_text(encoding="utf-8")
    assert forbidden not in src

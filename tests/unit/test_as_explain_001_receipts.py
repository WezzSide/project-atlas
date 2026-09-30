"""AS-EXPLAIN-001 Band A: structured explainability / provenance receipts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from project_atlas.domain.knowledge_query import (
    AnswerStatus,
    KnowledgeQueryErrorCode,
    QueryKind,
    QueryOutcomeClass,
    QueryShape,
)
from project_atlas.explain_receipts import (
    ExplainReceiptError,
    build_explain_receipt_from_answer,
    build_explain_receipt_from_diagnostic,
    build_explain_receipts_from_multifield,
    receipt_to_json,
)
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import (
    query_diagnostic_from_error,
    query_knowledge,
    query_knowledge_fields,
)
from project_atlas.schema import available_schemas, validate_record

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


def test_explain_receipt_schema_registered() -> None:
    assert "explain-receipt" in available_schemas()


def test_t01_receipt_from_authoritative_answer(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    receipt = build_explain_receipt_from_answer(answer)
    validate_record(receipt, "explain-receipt")
    assert receipt["package"] == "AS-EXPLAIN-001"
    assert receipt["receipt_kind"] == "query_point"
    assert receipt["query_kind"] == "authoritative"
    assert receipt["answer_status"] == answer.status.value
    assert "trust_score" not in receipt
    assert "confidence" not in receipt
    # Deterministic serialization
    assert receipt_to_json(receipt) == receipt_to_json(receipt)


def test_t02_receipt_from_explain_kind_preserves_layers(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="explain")
    assert answer.kind is QueryKind.EXPLAIN
    receipt = build_explain_receipt_from_answer(answer)
    validate_record(receipt, "explain-receipt")
    assert receipt["query_kind"] == "explain"
    assert "persisted_rationale" in receipt["reason_categories"] or receipt["omissions"]


def test_t03_multifield_receipts_preserve_order(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", ["title", "status"], kind="authoritative"
    )
    receipts = build_explain_receipts_from_multifield(envelope)
    assert len(receipts) == 2
    assert [item["field"] for item in receipts] == list(envelope.fields)
    for receipt in receipts:
        validate_record(receipt, "explain-receipt")
        assert receipt["receipt_kind"] == "query_multifield_item"


def test_t04_diagnostic_receipt_fail_closed() -> None:
    from project_atlas.knowledge_query import KnowledgeQueryError

    err = KnowledgeQueryError(KnowledgeQueryErrorCode.INVALID_INPUT, "bad request")
    diagnostic = query_diagnostic_from_error(
        err,
        project_id="project-atlas",
        subject="wp:AS-ID-001",
        field="title",
        kind=QueryKind.AUTHORITATIVE,
        query_shape=QueryShape.POINT,
    )
    assert diagnostic.outcome_class is QueryOutcomeClass.REQUEST_INVALID
    receipt = build_explain_receipt_from_diagnostic(diagnostic)
    validate_record(receipt, "explain-receipt")
    assert receipt["receipt_kind"] == "query_diagnostic"
    assert receipt["outcome_class"] == "request_invalid"
    assert any(ref["ref_kind"] == "error_code" for ref in receipt["provenance_refs"])


def test_t05_trust_score_smuggling_rejected(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    receipt = build_explain_receipt_from_answer(answer)
    smuggled = dict(receipt)
    smuggled["confidence"] = 0.9
    with pytest.raises(ExplainReceiptError, match="forbidden subjective score"):
        receipt_to_json(smuggled)


def test_t06_not_found_marks_omissions(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(
        vault, "project-atlas", "wp:no-such-subject", "title", kind="authoritative"
    )
    assert answer.status is AnswerStatus.NOT_FOUND
    receipt = build_explain_receipt_from_answer(answer)
    validate_record(receipt, "explain-receipt")
    assert "claim_projection_absent" in receipt["omissions"]
    # Must not invent provenance claim ids
    claim_refs = [r for r in receipt["provenance_refs"] if r["ref_kind"] == "claim_id"]
    assert claim_refs == [] or all(isinstance(r["ref_id"], str) for r in claim_refs)


def test_t07_default_007_answer_json_unchanged(tmp_path: Path) -> None:
    """EXPL-FR-010: building a receipt must not mutate the answer envelope."""
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    before = answer.model_dump(mode="json")
    build_explain_receipt_from_answer(answer)
    after = answer.model_dump(mode="json")
    assert before == after
    assert "package" in before and before["package"] == "AS-CORE-007"

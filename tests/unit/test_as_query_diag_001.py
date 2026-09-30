"""AS-QUERY-DIAG-001: Structured Query Outcome Diagnostics (T01-T12)."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.cli import main as cli_main
from project_atlas.domain.knowledge_query import (
    AnswerStatus,
    KnowledgeQueryErrorCode,
    QueryOutcomeClass,
    QueryShape,
)
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    answer_to_json,
    classify_query_outcome,
    diagnostic_to_json,
    query_diagnostic_from_answer,
    query_diagnostic_from_error,
    query_knowledge,
    query_knowledge_fields,
)
from project_atlas.schema import validate_record

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


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# --- T01 / T02 success-path classifiers; success JSON stable -------------------


def test_t01_point_ok_classifier_and_success_json_unchanged(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    success_json = answer_to_json(answer)
    diagnostic = query_diagnostic_from_answer(answer)
    assert diagnostic.outcome_class is QueryOutcomeClass.ANSWER
    assert diagnostic.package == "AS-QUERY-DIAG-001"
    assert diagnostic.query_shape is QueryShape.POINT
    assert diagnostic.error_code is None
    assert answer_to_json(answer) == success_json
    validate_record(diagnostic, "query-diagnostic")


def test_t02_point_nonanswers_not_integrity(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(
        vault, "project-atlas", "wp:DOES-NOT-EXIST", "title", kind="authoritative"
    )
    assert answer.status is AnswerStatus.NOT_FOUND
    diagnostic = query_diagnostic_from_answer(answer)
    assert diagnostic.outcome_class is QueryOutcomeClass.NONANSWER
    assert diagnostic.answer_status is AnswerStatus.NOT_FOUND
    assert classify_query_outcome(AnswerStatus.AUTHORITY_PENDING) is QueryOutcomeClass.NONANSWER
    assert classify_query_outcome(AnswerStatus.AUTHORITY_CONFLICT) is QueryOutcomeClass.NONANSWER
    assert classify_query_outcome(AnswerStatus.UNRESOLVED) is QueryOutcomeClass.NONANSWER
    assert (
        classify_query_outcome(AnswerStatus.TEMPORAL_STATE_MISSING) is QueryOutcomeClass.NONANSWER
    )


# --- T03 / T04 integrity failures + CLI stdout ---------------------------------


def test_t03_compilation_mismatch_cli_stdout_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["compilation_id"] = "compile-drifted-xxxxxxxx"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    lib_diag = query_diagnostic_from_error(
        exc.value,
        project_id="project-atlas",
        subject="wp:AS-ID-001",
        field="title",
        query_shape=QueryShape.POINT,
        kind="authoritative",
    )
    assert lib_diag.outcome_class is QueryOutcomeClass.INTEGRITY_FAILURE
    assert lib_diag.error_code is KnowledgeQueryErrorCode.COMPILATION_MISMATCH
    validate_record(lib_diag, "query-diagnostic")

    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:AS-ID-001",
            "--field",
            "title",
        ]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["package"] == "AS-QUERY-DIAG-001"
    assert payload["outcome_class"] == "integrity_failure"
    assert payload["error_code"] == "compilation_mismatch"
    assert "value" not in payload


def test_t04_state_corrupt_integrity(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT
    diagnostic = query_diagnostic_from_error(exc.value, query_shape=QueryShape.POINT)
    assert diagnostic.outcome_class is QueryOutcomeClass.INTEGRITY_FAILURE


# --- T05 request_invalid -------------------------------------------------------


def test_t05_request_invalid_empty_and_unsupported_kind(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "", kind="authoritative")
    assert exc.value.code is KnowledgeQueryErrorCode.INVALID_INPUT
    assert query_diagnostic_from_error(exc.value).outcome_class is QueryOutcomeClass.REQUEST_INVALID

    with pytest.raises(KnowledgeQueryError) as exc2:
        query_knowledge(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            "title",
            kind="not-a-kind",  # type: ignore[arg-type]
        )
    assert exc2.value.code is KnowledgeQueryErrorCode.UNSUPPORTED_KIND
    assert classify_query_outcome(exc2.value.code) is QueryOutcomeClass.REQUEST_INVALID


# --- T06 multi-field item classes; no rollup value -----------------------------


def test_t06_multifield_mixed_item_classes_no_rollup_value(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "nonexistent_field"],
        kind="authoritative",
    )
    diagnostic = query_diagnostic_from_answer(envelope)
    assert diagnostic.query_shape is QueryShape.MULTIFIELD
    assert diagnostic.fields == ("title", "nonexistent_field")
    assert diagnostic.item_outcome_classes == (
        QueryOutcomeClass.ANSWER,
        QueryOutcomeClass.NONANSWER,
    )
    assert diagnostic.outcome_class is QueryOutcomeClass.NONANSWER
    dumped = diagnostic.model_dump(mode="json")
    assert "value" not in dumped
    assert dumped.get("fields") == ["title", "nonexistent_field"]


# --- T07 determinism -----------------------------------------------------------


def test_t07_diagnostic_dumps_deterministic(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    a = diagnostic_to_json(
        query_diagnostic_from_error(
            exc.value,
            project_id="project-atlas",
            subject="wp:AS-ID-001",
            field="title",
            query_shape=QueryShape.POINT,
        )
    )
    b = diagnostic_to_json(
        query_diagnostic_from_error(
            exc.value,
            project_id="project-atlas",
            subject="wp:AS-ID-001",
            field="title",
            query_shape=QueryShape.POINT,
        )
    )
    assert a == b


# --- T08 read-only -------------------------------------------------------------


def test_t08_diagnostic_path_read_only(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    before = _hash_tree(vault)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    query_diagnostic_from_answer(answer)
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "", "wp:AS-ID-001", "title")
    query_diagnostic_from_error(exc.value)
    assert _hash_tree(vault) == before


# --- T09 007/008 suites remain separately green (smoke here) -------------------


def test_t09_success_path_answer_to_json_still_core_packages(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    point = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    multi = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", ["title"], kind="authoritative"
    )
    point_payload = json.loads(answer_to_json(point))
    multi_payload = json.loads(answer_to_json(multi))
    assert point_payload["package"] == "AS-CORE-007"
    assert multi_payload["package"] == "AS-CORE-008"


# --- T10 no AS-RET on diagnostic path ------------------------------------------


def test_t10_no_retrieval_import_on_diagnostic_path() -> None:
    module = importlib.import_module("project_atlas.knowledge_query")
    source = Path(module.__file__ or "")
    text = source.read_text(encoding="utf-8")
    import_block = "\n".join(
        line for line in text.splitlines() if line.startswith(("import ", "from "))
    )
    assert "retrieval" not in import_block
    assert "project_atlas.retrieval" not in text


# --- T11 secret redaction ------------------------------------------------------


def test_t11_secret_shaped_message_redacted() -> None:
    exc = KnowledgeQueryError(
        KnowledgeQueryErrorCode.STATE_CORRUPT,
        "leak api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    )
    diagnostic = query_diagnostic_from_error(exc)
    assert diagnostic.message is not None
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in diagnostic.message
    assert "api_key=" not in diagnostic.message
    assert "redacted" in diagnostic.message
    assert diagnostic.error_code is KnowledgeQueryErrorCode.STATE_CORRUPT


# --- T12 argparse usage exit 2; no forged integrity diagnostic -----------------


def test_t12_cli_argparse_missing_flags_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["query"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "AS-QUERY-DIAG-001" not in captured.out
    assert "integrity_failure" not in captured.out


def test_cli_success_stdout_unchanged_vs_library(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _materialize_vault(tmp_path)
    lib = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:AS-ID-001",
            "--field",
            "title",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == lib

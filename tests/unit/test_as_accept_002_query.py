"""AS-ACCEPT-002 Band A — QOK / QFL combined DIAG external oracles.

Wave-A2 P0: AX2-QOK-001, AX2-QOK-002, AX2-QFL-001, AX2-QFL-004.
INV-A01 / INV-A02 / INV-A03 / INV-A10.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit._as_accept_002_helpers import (
    QUERY_DIAGNOSTIC_KEYS,
    hash_tree,
    materialize_knowledge_vault,
)

from project_atlas.cli import main as cli_main
from project_atlas.domain.knowledge_query import (
    AnswerStatus,
    KnowledgeQueryErrorCode,
    QueryOutcomeClass,
    QueryShape,
)
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    answer_to_json,
    diagnostic_to_json,
    query_diagnostic_from_answer,
    query_diagnostic_from_error,
    query_knowledge,
)
from project_atlas.schema import validate_record


def test_ax2_qok_001_point_success_quiet_no_diag_failure_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AX2-QOK-001: authoritative success → 007 answer JSON; no DIAG failure envelope.

    INV-A01 — exit 0; stdout is answer JSON; package AS-CORE-007; classifier may
    return answer off the success stdout path.
    """
    vault = materialize_knowledge_vault(tmp_path)
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
            "--kind",
            "authoritative",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out == lib
    payload = json.loads(out)
    assert payload["package"] == "AS-CORE-007"
    assert payload["status"] == "ok"
    assert "value" in payload
    assert payload.get("package") != "AS-QUERY-DIAG-001"
    assert "outcome_class" not in payload
    assert "error_code" not in payload
    # Library classifier is opt-in / off success stdout.
    diagnostic = query_diagnostic_from_answer(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    assert diagnostic.outcome_class is QueryOutcomeClass.ANSWER
    assert diagnostic.package == "AS-QUERY-DIAG-001"


def test_ax2_qok_002_honest_nonanswer_quiet_not_integrity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AX2-QOK-002: honest nonanswer stays exit 0; not integrity_failure.

    INV-A01 / INV-A03 — certified nonanswer envelope; library class nonanswer.
    """
    vault = materialize_knowledge_vault(tmp_path)
    answer = query_knowledge(
        vault, "project-atlas", "wp:DOES-NOT-EXIST", "title", kind="authoritative"
    )
    assert answer.status is AnswerStatus.NOT_FOUND
    diagnostic = query_diagnostic_from_answer(answer)
    assert diagnostic.outcome_class is QueryOutcomeClass.NONANSWER
    assert diagnostic.outcome_class is not QueryOutcomeClass.INTEGRITY_FAILURE
    assert diagnostic.error_code is None

    lib = answer_to_json(answer)
    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:DOES-NOT-EXIST",
            "--field",
            "title",
            "--kind",
            "authoritative",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out == lib
    payload = json.loads(out)
    assert payload["package"] == "AS-CORE-007"
    assert payload["status"] == "not_found"
    assert "outcome_class" not in payload
    assert payload.get("package") != "AS-QUERY-DIAG-001"


def test_ax2_qfl_001_compilation_mismatch_bounded_cli_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AX2-QFL-001: compilation_mismatch → exit 1; bounded QueryDiagnostic.

    INV-A02 — outcome_class=integrity_failure; closed error_code; value absent.
    """
    vault = materialize_knowledge_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["compilation_id"] = "compile-drifted-xxxxxxxx"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.COMPILATION_MISMATCH
    lib_diag = query_diagnostic_from_error(
        exc.value,
        project_id="project-atlas",
        subject="wp:AS-ID-001",
        field="title",
        query_shape=QueryShape.POINT,
        kind="authoritative",
    )
    assert lib_diag.outcome_class is QueryOutcomeClass.INTEGRITY_FAILURE
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
    assert "answer" not in payload


def test_ax2_qfl_004_failure_diagnostic_keys_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    """AX2-QFL-004: failure diagnostic JSON is bounded; sort_keys; no wall-clock.

    INV-A02 / INV-A10 / NFR-004 — contracted keys only; message metadata-safe.
    """
    vault = materialize_knowledge_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    path.write_text("{not-json", encoding="utf-8")
    before = hash_tree(vault)

    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT
    diagnostic = query_diagnostic_from_error(
        exc.value,
        project_id="project-atlas",
        subject="wp:AS-ID-001",
        field="title",
        query_shape=QueryShape.POINT,
        kind="authoritative",
    )
    dumped = json.loads(diagnostic_to_json(diagnostic))
    assert set(dumped.keys()) <= QUERY_DIAGNOSTIC_KEYS
    assert dumped["outcome_class"] in {"integrity_failure", "request_invalid"}
    assert "value" not in dumped
    assert "generated.at" not in dumped
    assert "stack" not in dumped
    assert "traceback" not in dumped
    a = diagnostic_to_json(diagnostic)
    b = diagnostic_to_json(diagnostic)
    assert a == b
    # Secret-shaped message must not pass through (NFR-004 spirit).
    leak = KnowledgeQueryError(
        KnowledgeQueryErrorCode.STATE_CORRUPT,
        "leak api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    )
    redacted = query_diagnostic_from_error(leak)
    assert redacted.message is not None
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in redacted.message
    assert hash_tree(vault) == before

"""AS-QUERY-001: kind-scoped query --list discoverability."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.cli import main as cli_main
from project_atlas.domain.knowledge_query import QueryKind, QueryOutcomeClass
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    KnowledgeQueryErrorCode,
    list_authoritative,
    list_temporal,
)

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


def test_t01_list_temporal_deterministic(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    first = list_temporal(vault, "project-atlas")
    second = list_temporal(vault, "project-atlas")
    assert first
    assert all(item.kind is QueryKind.TEMPORAL for item in first)
    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]
    keys = [(item.subject, item.field) for item in first]
    assert keys == sorted(keys)


def test_t02_list_authoritative_still_works(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answers = list_authoritative(vault, "project-atlas")
    assert answers
    assert all(item.kind is QueryKind.AUTHORITATIVE for item in answers)


def test_t03_cli_list_temporal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _materialize_vault(tmp_path)
    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--kind",
            "temporal",
            "--list",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload
    assert all(item["kind"] == "temporal" for item in payload)


def test_t04_cli_list_explain_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _materialize_vault(tmp_path)
    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--kind",
            "explain",
            "--list",
        ]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome_class"] == QueryOutcomeClass.REQUEST_INVALID.value
    assert payload["error_code"] == KnowledgeQueryErrorCode.UNSUPPORTED_KIND.value


def test_t05_list_temporal_missing_state(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    (vault / "state" / "claims").mkdir(parents=True)
    (vault / "state" / "claims" / "project-atlas.json").write_text(
        json.dumps({"claims": [], "compilation_id": "c1"}, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(KnowledgeQueryError) as excinfo:
        list_temporal(vault, "project-atlas")
    assert excinfo.value.code is KnowledgeQueryErrorCode.STATE_MISSING


def test_t06_no_trust_score_fields(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    for item in list_temporal(vault, "project-atlas"):
        dumped = item.model_dump(mode="json")
        assert "trust_score" not in dumped
        assert "confidence" not in dumped
        assert "confidence_score" not in dumped

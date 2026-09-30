"""AS-CORE-007: Knowledge Query Contract — FR/INV focused suite."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.authority_registry import AUTHORITY_REGISTRY_VERSION, trust_root
from project_atlas.cli import main as cli_main
from project_atlas.domain.knowledge_query import AnswerStatus, QueryKind
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    KnowledgeQueryErrorCode,
    answer_to_json,
    list_authoritative,
    query_knowledge,
)
from project_atlas.schema import validate_record
from project_atlas.validation import validate

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
    # Minimal scaffold files for validate()
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


# --- FR-003 acceptance fixture -------------------------------------------------


def test_as_id_001_title_authoritative_value(tmp_path: Path) -> None:
    """AS-CORE-007-FR-003 / INV-005 — value from persisted authority, not recomputation."""
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert answer.status is AnswerStatus.OK
    assert answer.kind is QueryKind.AUTHORITATIVE
    assert answer.authority_disposition == "authoritative"
    assert answer.value == "Durable Source Lineage Identity"
    assert answer.rule_id == "R-TITLE-001"
    assert answer.claim_id is not None
    assert answer.claim is not None
    assert answer.claim.value == "Durable Source Lineage Identity"
    assert answer.claim.source_id is not None
    assert answer.trust_root
    assert answer.registry_version == 1
    # Temporal layer distinct (INV-004)
    assert answer.temporal_status == "authority-pending"
    assert answer.temporal_current_claim_id is None
    assert answer.value != answer.temporal_current_claim_id
    validate_record(answer, "knowledge-answer")


# --- FR-001 / FR-002 / FR-012 --------------------------------------------------


def test_authoritative_answer_envelope_fields(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert answer.competing_claim_ids or answer.subordinate_claim_ids
    assert answer.authority_rationale
    assert "state/authoritative-state/project-atlas.json" in answer.inspected_artifacts
    assert answer.temporal_status is not None
    assert answer.temporal_resolution_basis is not None


def test_temporal_context_distinct_from_authority(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    auth = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    temporal = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="temporal")
    assert temporal.kind is QueryKind.TEMPORAL
    assert temporal.value is None  # INV-005 / INV-004
    assert temporal.authority_disposition is None
    assert temporal.temporal_status == "authority-pending"
    assert auth.value == "Durable Source Lineage Identity"
    assert auth.temporal_status == temporal.temporal_status


def test_explain_joins_layers_without_new_narrative(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="explain")
    assert answer.kind is QueryKind.EXPLAIN
    assert answer.value == "Durable Source Lineage Identity"
    assert answer.authority_rationale
    assert answer.temporal_rationale
    assert any("no inference" in note.lower() for note in answer.notes)


# --- FR-004 fail-closed non-answers -------------------------------------------


def test_pending_package_status_emits_null_value(tmp_path: Path) -> None:
    """Domains without authoritative records must not invent values."""
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(
        vault,
        "project-atlas",
        "wp:AS-CORE-002",
        "package_status",
        kind="authoritative",
    )
    assert answer.status is AnswerStatus.NOT_FOUND
    assert answer.value is None
    assert answer.reason_code == "not_found"
    # Temporal may still exist
    assert answer.temporal_status in {
        None,
        "current",
        "historical",
        "unresolved",
        "authority-pending",
    }


def test_synthetic_pending_and_conflict_emit_null_value(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    # Clone first record into pending + conflict synthetic rows
    base = raw["authoritative_states"][0]
    pending = {
        **base,
        "subject": "wp:SYNTH-PENDING",
        "field": "title",
        "disposition": "authority-pending",
        "authoritative_claim_id": None,
        "authoritative_value": None,
        "authoritative_role": None,
        "rule_id": "R-TITLE-001",
        "rationale": "synthetic pending for AS-CORE-007 tests",
        "competing_claim_ids": [],
        "subordinate_claim_ids": [],
        "temporally_ineligible_claim_ids": [],
        "evidence": [],
    }
    conflict = {
        **pending,
        "subject": "wp:SYNTH-CONFLICT",
        "disposition": "authority-conflict",
        "rationale": "synthetic conflict for AS-CORE-007 tests",
        "competing_claim_ids": ["claim-a", "claim-b"],
    }
    raw["authoritative_states"].extend([pending, conflict])
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    pending_ans = query_knowledge(
        vault, "project-atlas", "wp:SYNTH-PENDING", "title", kind="authoritative"
    )
    conflict_ans = query_knowledge(
        vault, "project-atlas", "wp:SYNTH-CONFLICT", "title", kind="authoritative"
    )
    assert pending_ans.status is AnswerStatus.AUTHORITY_PENDING
    assert pending_ans.value is None
    assert conflict_ans.status is AnswerStatus.AUTHORITY_CONFLICT
    assert conflict_ans.value is None
    assert list(conflict_ans.competing_claim_ids) == ["claim-a", "claim-b"]


# --- FR-005 integrity ---------------------------------------------------------


def test_missing_state_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.STATE_MISSING


def test_corrupt_json_fails_closed(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT


def test_compilation_id_mismatch_fails_closed(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["compilation_id"] = "compile-drifted-xxxxxxxx"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.COMPILATION_MISMATCH


def test_provenance_mismatch_fails_closed(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for item in raw["authoritative_states"]:
        if item["subject"] == "wp:AS-ID-001" and item["field"] == "title":
            item["authoritative_claim_id"] = "claim-does-not-exist"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code is KnowledgeQueryErrorCode.PROVENANCE_MISMATCH


def test_inconsistent_truncated_state_fails_closed(tmp_path: Path) -> None:
    """Mid-compile / race: truncated authoritative-state → fail closed."""
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    path.write_text('{"schema_version": 1, "authoritative_states": [', encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code in {
        KnowledgeQueryErrorCode.STATE_CORRUPT,
        KnowledgeQueryErrorCode.STATE_RACE,
    }


# --- FR-006 / FR-007 / INV-001..003 -------------------------------------------


def test_query_kind_not_overloading_ret_authority() -> None:
    assert {k.value for k in QueryKind} == {"authoritative", "temporal", "explain"}
    assert "authority" not in {k.value for k in QueryKind}


def test_query_does_not_call_evaluators_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _materialize_vault(tmp_path)
    before = _hash_tree(vault)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("evaluator must not be called")

    monkeypatch.setattr(
        "project_atlas.authority_evaluator.evaluate_authority", _boom, raising=False
    )
    monkeypatch.setattr("project_atlas.temporal_evaluator.evaluate_conflicts", _boom, raising=False)
    # Import path used by knowledge_compiler — ensure query module never imports them at call
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="temporal")
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="explain")
    with pytest.raises(KnowledgeQueryError):
        query_knowledge(vault, "missing", "wp:AS-ID-001", "title")
    after = _hash_tree(vault)
    assert before == after


def test_no_mutation_on_success_and_failure_queries(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    before = _hash_tree(vault)
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="temporal")
    query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="explain")
    query_knowledge(vault, "project-atlas", "wp:DOES-NOT-EXIST", "title", kind="authoritative")
    assert _hash_tree(vault) == before


# --- FR-008 / FR-009 / FR-010 CLI ---------------------------------------------


def test_cli_query_json_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _materialize_vault(tmp_path)
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
    payload = json.loads(capsys.readouterr().out)
    assert payload["value"] == "Durable Source Lineage Identity"

    code = cli_main(["query", "--vault", str(tmp_path / "missing"), "--project", "p"])
    assert code == 1


def test_query_replay_byte_identical(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    a = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    b = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    assert a == b


def test_list_authoritative_deterministic_order(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    first = list_authoritative(vault, "project-atlas")
    second = list_authoritative(vault, "project-atlas")
    assert answer_to_json(first) == answer_to_json(second)
    keys = [(item.subject, item.field) for item in first]
    assert keys == sorted(keys)
    assert any(
        item.subject == "wp:AS-ID-001" and item.value == "Durable Source Lineage Identity"
        for item in first
    )


def test_cli_list_authoritative(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = _materialize_vault(tmp_path)
    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--kind",
            "authoritative",
            "--list",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload


# --- FR-011 validate glue -----------------------------------------------------


def test_validate_accepts_legacy_without_005_006(tmp_path: Path) -> None:
    vault = tmp_path / "legacy"
    for rel in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# x\n", encoding="utf-8")
    result = validate(vault)
    assert result["ok"] is True


def test_validate_checks_present_state_files(tmp_path: Path) -> None:
    """FR-011: when 005/006 files exist, malformed records fail validate."""
    vault = tmp_path / "validate-005-006"
    for rel in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# x\n", encoding="utf-8")
    current = vault / "state" / "current-state" / "project-atlas.json"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "project-atlas",
                "compilation_id": "compile-test",
                "current_states": [],
                "temporal_relations": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    auth = vault / "state" / "authoritative-state" / "project-atlas.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "project-atlas",
                "compilation_id": "compile-test",
                "authority_registry_version": 1,
                "authoritative_states": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = validate(vault)
    assert result["ok"] is True
    auth.write_text(
        '{"schema_version":1,"authoritative_states":[{"bad":true}]}',
        encoding="utf-8",
    )
    result = validate(vault)
    assert result["ok"] is False
    assert any("authoritative-state" in err for err in result["errors"])


# --- Mismatched inputs --------------------------------------------------------


def test_mismatched_subject_field_not_found(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(
        vault, "project-atlas", "wp:AS-ID-001", "nonexistent_field", kind="authoritative"
    )
    assert answer.status is AnswerStatus.NOT_FOUND
    assert answer.value is None


def _forge_authoritative_binding(
    vault: Path,
    *,
    trust: str | None = "forged-trust-root-not-owner-certified",
    registry_version: int | None = 999,
    file_registry_version: int | None = 999,
) -> None:
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    if file_registry_version is not None:
        raw["authority_registry_version"] = file_registry_version
    for item in raw.get("authoritative_states", []):
        if trust is not None:
            item["trust_root"] = trust
        if registry_version is not None:
            item["registry_version"] = registry_version
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_forged_trust_root_query_fail_closed(tmp_path: Path) -> None:
    """AX-AUTH-005 / AS-CORE-007 consume: forged trust_root is not echoed."""
    vault = _materialize_vault(tmp_path)
    _forge_authoritative_binding(vault)
    with pytest.raises(KnowledgeQueryError) as excinfo:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert excinfo.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT
    assert "forged-trust-root" not in str(excinfo.value).lower()


def test_forged_registry_version_only_fail_closed(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    _forge_authoritative_binding(
        vault,
        trust=trust_root(),
        registry_version=999,
        file_registry_version=AUTHORITY_REGISTRY_VERSION,
    )
    with pytest.raises(KnowledgeQueryError) as excinfo:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert excinfo.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT


def test_forged_file_level_registry_version_fail_closed(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    _forge_authoritative_binding(
        vault,
        trust=trust_root(),
        registry_version=AUTHORITY_REGISTRY_VERSION,
        file_registry_version=999,
    )
    with pytest.raises(KnowledgeQueryError) as excinfo:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert excinfo.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT


def test_bool_registry_version_on_record_fail_closed(tmp_path: Path) -> None:
    """JSON true must not coerce to live registry version 1."""
    vault = _materialize_vault(tmp_path)
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["authority_registry_version"] = AUTHORITY_REGISTRY_VERSION
    for item in raw.get("authoritative_states", []):
        item["trust_root"] = trust_root()
        item["registry_version"] = True
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as excinfo:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert excinfo.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT


def test_forged_evidence_trust_root_fail_closed(tmp_path: Path) -> None:
    """Record-level live binding must not launder forged evidence bindings."""
    vault = _materialize_vault(tmp_path)
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["authority_registry_version"] = AUTHORITY_REGISTRY_VERSION
    for item in raw.get("authoritative_states", []):
        item["trust_root"] = trust_root()
        item["registry_version"] = AUTHORITY_REGISTRY_VERSION
        for ev in item.get("evidence") or []:
            ev["trust_root"] = "forged-trust-root-not-owner-certified"
            ev["registry_version"] = 999
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as excinfo:
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert excinfo.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT
    assert "forged-trust-root" not in str(excinfo.value).lower()


def test_legitimate_binding_still_queries(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert answer.status is AnswerStatus.OK
    assert answer.trust_root == trust_root()
    assert answer.registry_version == AUTHORITY_REGISTRY_VERSION


def test_validate_forged_trust_root_fail_closed(tmp_path: Path) -> None:
    """FR-011 consume: forged trust_root on present 006 state fails validate."""
    vault = tmp_path / "validate-forged-binding"
    for rel in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# x\n", encoding="utf-8")
    auth = vault / "state" / "authoritative-state" / "project-atlas.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "subject": "wp:AS-ID-001",
        "field": "title",
        "authority_domain": "work_package.durable_title",
        "disposition": "authoritative",
        "rationale": "fixture",
        "compilation_id": "compile-test",
        "registry_version": 999,
        "trust_root": "forged-trust-root-not-owner-certified",
    }
    auth.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "project-atlas",
                "compilation_id": "compile-test",
                "authority_registry_version": 999,
                "authoritative_states": [record],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = validate(vault)
    assert result["ok"] is False
    assert any("trust binding" in err or "registry version" in err for err in result["errors"])


def test_validate_forged_evidence_binding_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "validate-forged-evidence"
    for rel in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# x\n", encoding="utf-8")
    auth = vault / "state" / "authoritative-state" / "project-atlas.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "subject": "wp:AS-ID-001",
        "field": "title",
        "authority_domain": "work_package.durable_title",
        "disposition": "authoritative",
        "rationale": "fixture",
        "compilation_id": "compile-test",
        "registry_version": AUTHORITY_REGISTRY_VERSION,
        "trust_root": trust_root(),
        "evidence": [
            {
                "schema_version": 1,
                "rule_id": "R-TITLE-001",
                "trust_root": "forged-trust-root-not-owner-certified",
                "registry_version": 999,
                "artifact_role": "package_genesis_receipt",
                "claim_id": "claim-aaaaaaaaaaaaaaaa",
                "source_id": "source-aaaaaaaaaaaaaaaa",
                "source_path": "docs/evidence/AS-ID-001-receipt.yaml",
                "temporal_status": "current",
            }
        ],
    }
    auth.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "project-atlas",
                "compilation_id": "compile-test",
                "authority_registry_version": AUTHORITY_REGISTRY_VERSION,
                "authoritative_states": [record],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = validate(vault)
    assert result["ok"] is False
    assert any("evidence trust binding" in err for err in result["errors"])


def test_cli_library_semantic_parity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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

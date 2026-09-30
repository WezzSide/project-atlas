"""AS-CORE-008: Subject Multi-Field Knowledge Query — FR/INV focused suite."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.cli import main as cli_main
from project_atlas.domain.knowledge_query import AnswerStatus, QueryKind
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    KnowledgeQueryErrorCode,
    answer_to_json,
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


def _inject_auth_rows(vault: Path, rows: list[dict[str, Any]]) -> None:
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["authoritative_states"].extend(rows)
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- T01 / FR-001..005 / R-TITLE-001 ------------------------------------------


def test_t01_two_field_mixed_authoritative_and_not_found(tmp_path: Path) -> None:
    """T01: title OK + package_status not_found; shared compilation_id."""
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "package_status"],
        kind="authoritative",
    )
    assert envelope.package == "AS-CORE-008"
    assert envelope.schema_version == 1
    assert envelope.fields == ("title", "package_status")
    assert len(envelope.results) == 2
    title, status = envelope.results
    assert title.package == "AS-CORE-007"
    assert title.status is AnswerStatus.OK
    assert title.value == "Durable Source Lineage Identity"
    assert title.rule_id == "R-TITLE-001"
    assert status.status is AnswerStatus.NOT_FOUND
    assert status.value is None
    assert envelope.compilation_id is not None
    assert title.compilation_id == envelope.compilation_id
    assert status.compilation_id == envelope.compilation_id
    # No request-level value attribute / invented rollup
    assert not hasattr(envelope, "value") or "value" not in envelope.model_fields_set
    validate_record(envelope, "knowledge-multifield-answer")


def test_three_field_success_mixed_statuses(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    base = json.loads(
        (vault / "state" / "authoritative-state" / "project-atlas.json").read_text(encoding="utf-8")
    )["authoritative_states"][0]
    _inject_auth_rows(
        vault,
        [
            {
                **base,
                "subject": "wp:AS-ID-001",
                "field": "synth_pending",
                "disposition": "authority-pending",
                "authoritative_claim_id": None,
                "authoritative_value": None,
                "authoritative_role": None,
                "rationale": "synthetic pending",
                "competing_claim_ids": [],
                "subordinate_claim_ids": [],
                "temporally_ineligible_claim_ids": [],
                "evidence": [],
            },
            {
                **base,
                "subject": "wp:AS-ID-001",
                "field": "synth_conflict",
                "disposition": "authority-conflict",
                "authoritative_claim_id": None,
                "authoritative_value": None,
                "authoritative_role": None,
                "rationale": "synthetic conflict",
                "competing_claim_ids": ["claim-a", "claim-b"],
                "subordinate_claim_ids": [],
                "temporally_ineligible_claim_ids": [],
                "evidence": [],
            },
        ],
    )
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "synth_pending", "synth_conflict"],
        kind="authoritative",
    )
    assert [item.status for item in envelope.results] == [
        AnswerStatus.OK,
        AnswerStatus.AUTHORITY_PENDING,
        AnswerStatus.AUTHORITY_CONFLICT,
    ]
    assert envelope.results[0].value == "Durable Source Lineage Identity"
    assert envelope.results[1].value is None
    assert envelope.results[2].value is None


# --- T02 / FR-006 / INV-007 ordering ------------------------------------------


def test_t02_caller_field_order_preserved(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    # Deliberately anti-lexicographic vs sorted(["package_status", "title"]).
    ordered = ["title", "package_status"]
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ordered,
        kind="authoritative",
    )
    assert envelope.fields == ("title", "package_status")
    assert [item.field for item in envelope.results] == ordered
    assert envelope.fields != tuple(sorted(envelope.fields))


# --- T03 / T04 / FR-007 / FR-008 request invalid ------------------------------


def test_t03_duplicate_fields_request_invalid(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge_fields(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            ["title", "title"],
            kind="authoritative",
        )
    assert exc.value.code is KnowledgeQueryErrorCode.INVALID_INPUT


def test_t04_empty_fields_request_invalid(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", [], kind="authoritative")
    assert exc.value.code is KnowledgeQueryErrorCode.INVALID_INPUT


def test_empty_project_and_invalid_subject_request_invalid(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge_fields(vault, "", "wp:AS-ID-001", ["title"])
    assert exc.value.code is KnowledgeQueryErrorCode.INVALID_INPUT
    with pytest.raises(KnowledgeQueryError) as exc2:
        query_knowledge_fields(vault, "project-atlas", "", ["title"])
    assert exc2.value.code is KnowledgeQueryErrorCode.INVALID_INPUT
    with pytest.raises(KnowledgeQueryError) as exc3:
        query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title", "  "])
    assert exc3.value.code is KnowledgeQueryErrorCode.INVALID_INPUT
    with pytest.raises(KnowledgeQueryError) as exc4:
        query_knowledge_fields(vault, "project-atlas", "wp:BAD SUBJECT WITH SPACES", ["title"])
    assert exc4.value.code is KnowledgeQueryErrorCode.INVALID_INPUT


# --- T05 unknown field item non-answer ----------------------------------------


def test_t05_unknown_field_not_found_siblings_unaffected(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "nonexistent_field"],
        kind="authoritative",
    )
    assert envelope.results[0].status is AnswerStatus.OK
    assert envelope.results[0].value == "Durable Source Lineage Identity"
    assert envelope.results[1].status is AnswerStatus.NOT_FOUND
    assert envelope.results[1].value is None


# --- T06 temporal / T07 explain -----------------------------------------------


def test_t06_temporal_multifield_no_authoritative_values(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "package_status"],
        kind="temporal",
    )
    assert envelope.kind is QueryKind.TEMPORAL
    for item in envelope.results:
        assert item.kind is QueryKind.TEMPORAL
        assert item.value is None
        assert item.authority_disposition is None


def test_t07_explain_multifield_per_item_rationales_only(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "package_status"],
        kind="explain",
    )
    assert envelope.kind is QueryKind.EXPLAIN
    title = envelope.results[0]
    assert title.kind is QueryKind.EXPLAIN
    assert title.authority_rationale
    assert title.temporal_rationale
    assert any("no inference" in note.lower() for note in title.notes)
    # No batch narrative field on envelope
    dumped = envelope.model_dump(mode="json")
    assert "batch_narrative" not in dumped
    assert "overall_rationale" not in dumped


# --- T08 / T09 shared-state fatal ---------------------------------------------


def test_t08_compilation_mismatch_request_fatal_no_partial(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["compilation_id"] = "compile-drifted-xxxxxxxx"
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge_fields(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            ["title", "package_status"],
            kind="authoritative",
        )
    assert exc.value.code is KnowledgeQueryErrorCode.COMPILATION_MISMATCH


def test_t09_missing_authoritative_state_fatal(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    auth = vault / "state" / "authoritative-state" / "project-atlas.json"
    auth.unlink()
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge_fields(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            ["title", "package_status"],
            kind="authoritative",
        )
    assert exc.value.code is KnowledgeQueryErrorCode.STATE_MISSING


def test_snapshot_missing_claims_corrupt_and_mid_compile(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    claims = vault / "state" / "claims" / "project-atlas.json"
    claims.unlink()
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title"])
    assert exc.value.code is KnowledgeQueryErrorCode.STATE_MISSING

    vault2 = _materialize_vault(tmp_path / "corrupt")
    auth = vault2 / "state" / "authoritative-state" / "project-atlas.json"
    auth.write_text("{not-json", encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc2:
        query_knowledge_fields(vault2, "project-atlas", "wp:AS-ID-001", ["title"])
    assert exc2.value.code is KnowledgeQueryErrorCode.STATE_CORRUPT

    vault3 = _materialize_vault(tmp_path / "mid")
    auth3 = vault3 / "state" / "authoritative-state" / "project-atlas.json"
    auth3.write_text('{"schema_version": 1, "authoritative_states": [', encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc3:
        query_knowledge_fields(vault3, "project-atlas", "wp:AS-ID-001", ["title"])
    assert exc3.value.code in {
        KnowledgeQueryErrorCode.STATE_CORRUPT,
        KnowledgeQueryErrorCode.STATE_RACE,
    }


# --- T10 determinism / T11 read-only ------------------------------------------


def test_t10_replay_determinism_byte_identical(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    a = answer_to_json(
        query_knowledge_fields(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            ["package_status", "title"],
            kind="authoritative",
        )
    )
    b = answer_to_json(
        query_knowledge_fields(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            ["package_status", "title"],
            kind="authoritative",
        )
    )
    assert a == b
    assert '"package": "AS-CORE-008"' in a


def test_t11_zero_mutation_hash_proof(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    before = _hash_tree(vault)
    query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title", "package_status"])
    query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title"], kind="temporal")
    query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title"], kind="explain")
    with pytest.raises(KnowledgeQueryError):
        query_knowledge_fields(vault, "missing", "wp:AS-ID-001", ["title"])
    assert _hash_tree(vault) == before


# --- T12 / T13 point compatibility + semantic equivalence ---------------------


def test_t13_item_semantic_equivalence_to_point_query(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    fields = ["title", "package_status", "nonexistent_field"]
    envelope = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", fields, kind="authoritative"
    )
    for field_name, item in zip(fields, envelope.results, strict=True):
        point = query_knowledge(
            vault, "project-atlas", "wp:AS-ID-001", field_name, kind="authoritative"
        )
        assert answer_to_json(item) == answer_to_json(point)


def test_t12_point_query_path_unchanged(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    point = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    assert point.package == "AS-CORE-007"
    assert point.value == "Durable Source Lineage Identity"


# --- T14 CLI / library parity -------------------------------------------------


def test_t14_cli_repeat_field_and_fields_csv_parity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _materialize_vault(tmp_path)
    lib = answer_to_json(
        query_knowledge_fields(
            vault,
            "project-atlas",
            "wp:AS-ID-001",
            ["title", "package_status"],
            kind="authoritative",
        )
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
            "--field",
            "package_status",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == lib

    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:AS-ID-001",
            "--fields",
            "title,package_status",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == lib


def test_cli_single_field_preserves_point_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = _materialize_vault(tmp_path)
    lib = answer_to_json(query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title"))
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
    out = capsys.readouterr().out
    assert out == lib
    payload = json.loads(out)
    assert payload["package"] == "AS-CORE-007"


def test_cli_list_excludes_multifield_flags(
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
            "--list",
            "--field",
            "title",
        ]
    )
    assert code == 1
    capsys.readouterr()


def test_cli_duplicate_fields_exit_error(
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
            "--subject",
            "wp:AS-ID-001",
            "--field",
            "title",
            "--field",
            "title",
        ]
    )
    assert code == 1
    capsys.readouterr()


# --- T15 no AS-RET / T16 title / INV-003 no recompute -------------------------


def test_t15_as_ret_not_consulted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _materialize_vault(tmp_path)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("AS-RET must not be consulted")

    monkeypatch.setattr("project_atlas.retrieval.VaultRetriever", _boom, raising=False)
    monkeypatch.setattr("project_atlas.retrieval.retrieve", _boom, raising=False)
    envelope = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", ["title", "package_status"]
    )
    assert envelope.results[0].value == "Durable Source Lineage Identity"


def test_t16_canonical_title_r_title_001_in_multifield(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", ["package_status", "title"]
    )
    title = envelope.results[1]
    assert title.value == "Durable Source Lineage Identity"
    assert title.rule_id == "R-TITLE-001"


def test_no_authority_or_temporal_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _materialize_vault(tmp_path)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("evaluator must not be called")

    monkeypatch.setattr(
        "project_atlas.authority_evaluator.evaluate_authority", _boom, raising=False
    )
    monkeypatch.setattr("project_atlas.temporal_evaluator.evaluate_conflicts", _boom, raising=False)
    query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title", "package_status"])


def test_provenance_retained_on_authoritative_item(tmp_path: Path) -> None:
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", ["title", "package_status"]
    )
    claim = envelope.results[0].claim
    assert claim is not None
    assert claim.source_id is not None
    assert claim.sha256 is not None
    assert envelope.results[1].claim is None


def test_no_cross_field_authority_laundering(tmp_path: Path) -> None:
    """INV-010: sibling authoritative title must not fill package_status."""
    vault = _materialize_vault(tmp_path)
    envelope = query_knowledge_fields(
        vault, "project-atlas", "wp:AS-ID-001", ["title", "package_status"]
    )
    assert envelope.results[0].value == "Durable Source Lineage Identity"
    assert envelope.results[1].value is None
    assert envelope.results[1].status is AnswerStatus.NOT_FOUND


def test_single_snapshot_load_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _materialize_vault(tmp_path)
    calls = {"n": 0}
    original = __import__(
        "project_atlas.knowledge_query", fromlist=["_load_snapshot"]
    )._load_snapshot

    def _counted(vault_path: Path, project_id: str) -> Any:
        calls["n"] += 1
        return original(vault_path, project_id)

    monkeypatch.setattr("project_atlas.knowledge_query._load_snapshot", _counted)
    query_knowledge_fields(vault, "project-atlas", "wp:AS-ID-001", ["title", "package_status", "x"])
    assert calls["n"] == 1

"""AS-ACCEPT-001 Wave-A query cases (AX-QRY-*).

Oracles: INV-001, INV-003, INV-004, INV-005 — single-snapshot coherence,
cross-project reject, no RET kind fill, no request-level rollup value.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from project_atlas.cli import main as cli_main
from project_atlas.domain.knowledge_query import (
    AnswerStatus,
    KnowledgeAnswer,
    KnowledgeMultiFieldAnswer,
    QueryKind,
)
from project_atlas.knowledge_compiler import compile_knowledge, render_bundle
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    KnowledgeQueryErrorCode,
    answer_to_json,
    query_knowledge,
    query_knowledge_fields,
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


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_ax_qry_001_multifield_single_snapshot_no_mixed_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AX-QRY-001: mid-query vault mutation must not yield mixed compilation_id.

    INV-001 / INV-005 — 008 loads one snapshot; all items share compilation_id.
    """
    vault = _materialize_vault(tmp_path)
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    before_claims = (vault / "state" / "claims" / "project-atlas.json").read_bytes()

    load_count = {"n": 0}
    original = query_knowledge_fields.__globals__["_load_snapshot"]

    def _counting_load(vault_path: Path, project_id: str) -> Any:
        load_count["n"] += 1
        snap = original(vault_path, project_id)
        # Mutate on-disk state after the single snapshot is already loaded.
        if load_count["n"] == 1:
            raw = json.loads(auth_path.read_text(encoding="utf-8"))
            raw["compilation_id"] = "compile-mutated-after-snapshot-load"
            auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return snap

    monkeypatch.setattr(
        "project_atlas.knowledge_query._load_snapshot", _counting_load, raising=True
    )
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "package_status"],
        kind="authoritative",
    )
    assert load_count["n"] == 1
    assert envelope.compilation_id is not None
    assert envelope.compilation_id != "compile-mutated-after-snapshot-load"
    ids = {item.compilation_id for item in envelope.results}
    assert ids == {envelope.compilation_id}
    # Claims bytes unchanged by query consume (INV-001).
    assert (vault / "state" / "claims" / "project-atlas.json").read_bytes() == before_claims

    # Point path: truncated mid-read → fail-closed (STATE_RACE / STATE_CORRUPT).
    vault2 = _materialize_vault(tmp_path / "race")
    path = vault2 / "state" / "authoritative-state" / "project-atlas.json"
    path.write_text('{"schema_version": 1, "authoritative_states": [', encoding="utf-8")
    with pytest.raises(KnowledgeQueryError) as exc:
        query_knowledge(vault2, "project-atlas", "wp:AS-ID-001", "title")
    assert exc.value.code in {
        KnowledgeQueryErrorCode.STATE_CORRUPT,
        KnowledgeQueryErrorCode.STATE_RACE,
    }


def test_ax_qry_002_cross_project_request_invalid() -> None:
    """AX-QRY-002: cross-project multi-field composition is REQUEST INVALID.

    AS-CORE-008-FR-002 — envelope items must share one project_id.
    """
    title = KnowledgeAnswer(
        status=AnswerStatus.OK,
        kind=QueryKind.AUTHORITATIVE,
        project_id="project-a",
        subject="wp:AS-ID-001",
        field="title",
        compilation_id="compile-shared",
        authority_disposition="authoritative",
        value="Durable Source Lineage Identity",
    )
    status = KnowledgeAnswer(
        status=AnswerStatus.NOT_FOUND,
        kind=QueryKind.AUTHORITATIVE,
        project_id="project-b",
        subject="wp:AS-ID-001",
        field="package_status",
        compilation_id="compile-shared",
        reason_code="not_found",
    )
    with pytest.raises(ValidationError, match="project_id"):
        KnowledgeMultiFieldAnswer(
            project_id="project-a",
            subject="wp:AS-ID-001",
            kind=QueryKind.AUTHORITATIVE,
            compilation_id="compile-shared",
            fields=("title", "package_status"),
            results=(title, status),
        )


def test_ax_qry_004_ret_kind_confusion_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AX-QRY-004: AS-RET kinds must not fill knowledge-query answers.

    INV-003 — reject / separate surface; never RET-fill certified answer.
    """
    vault = _materialize_vault(tmp_path)

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("AS-RET retrieval must not be invoked")

    monkeypatch.setattr("project_atlas.retrieval.retrieve", _boom, raising=False)
    monkeypatch.setattr("project_atlas.retrieval.lookup", _boom, raising=False)

    for kind in ("exact", "prefix", "authority", "ret"):
        with pytest.raises(KnowledgeQueryError) as exc:
            query_knowledge(
                vault,
                "project-atlas",
                "wp:AS-ID-001",
                "title",
                kind=kind,  # type: ignore[arg-type]
            )
        assert exc.value.code is KnowledgeQueryErrorCode.UNSUPPORTED_KIND

        with pytest.raises(KnowledgeQueryError) as exc2:
            query_knowledge_fields(
                vault,
                "project-atlas",
                "wp:AS-ID-001",
                ["title"],
                kind=kind,  # type: ignore[arg-type]
            )
        assert exc2.value.code is KnowledgeQueryErrorCode.UNSUPPORTED_KIND

    # CLI argparse choices exclude RET kinds (exit 2 usage).
    with pytest.raises(SystemExit) as exited:
        cli_main(
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
                "exact",
            ]
        )
    assert exited.value.code == 2


def test_ax_qry_008_multifield_envelope_has_no_request_level_value(
    tmp_path: Path,
) -> None:
    """AX-QRY-008: multi-field envelope must not invent an overall/request value.

    AS-CORE-008-FR-005 / INV-003 — per-item answers only; assert absence.
    """
    vault = _materialize_vault(tmp_path)
    before = _hash_tree(vault)
    envelope = query_knowledge_fields(
        vault,
        "project-atlas",
        "wp:AS-ID-001",
        ["title", "package_status"],
        kind="authoritative",
    )
    dumped = json.loads(answer_to_json(envelope))
    assert "value" not in dumped
    assert not hasattr(envelope, "value") or "value" not in envelope.model_fields_set
    assert envelope.results[0].value == "Durable Source Lineage Identity"
    assert envelope.results[1].value is None
    assert _hash_tree(vault) == before

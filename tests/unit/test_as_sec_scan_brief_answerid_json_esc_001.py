"""AS-SEC-SCAN-BRIEF-ANSWERID-JSON-ESC-001 — decoded answer ids must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.project_brief import materialize_project_briefs
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_answer_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    plant = vault / "generated" / "answers" / "ans-overview-harbor-api.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "answer_id": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '  "summary": "Harbor API overview",\n'
        '  "inspected_artifacts": ["\\u0041KIAAAAAAAAAAAAAAAAA"],\n'
        '  "project_id": "harbor-api",\n'
        '  "status": "ok"\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_project_briefs(vault, project_ids=["harbor-api"], refresh=False)
    written = (vault / "generated" / "ops" / "project-brief-harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_overview_summary_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    plant = vault / "generated" / "answers" / "ans-overview-harbor-api.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "answer_id": "ans-overview-harbor-api",\n'
        '  "summary": "We will adopt \\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '  "project_id": "harbor-api",\n'
        '  "status": "ok"\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_project_briefs(vault, project_ids=["harbor-api"], refresh=False)
    written = (vault / "generated" / "ops" / "project-brief-harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_architecture_evidence_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    plant = vault / "generated" / "answers" / "ans-architecture-harbor-api.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "answer_id": "ans-architecture-harbor-api",\n'
        '  "summary": "Harbor architecture",\n'
        '  "evidence": ["\\u0041KIAAAAAAAAAAAAAAAAA"],\n'
        '  "project_id": "harbor-api",\n'
        '  "status": "ok"\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_project_briefs(vault, project_ids=["harbor-api"], refresh=False)
    written = (vault / "generated" / "ops" / "project-brief-harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_knowledge_answer_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    plant = vault / "generated" / "answers" / "ans-custom-harbor-api.json"
    plant.parent.mkdir(parents=True)
    raw = (
        '{\n  "answer_id": "\\u0041KIAAAAAAAAAAAAAAAAA",\n'
        '  "subject": "harbor-api",\n'
        '  "summary": "custom"\n}\n'
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_project_briefs(vault, project_ids=["harbor-api"], refresh=False)
    written = (vault / "generated" / "ops" / "project-brief-harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []

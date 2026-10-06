"""AS-SEC-SCAN-OBS-PROJ-SOURCE-JSON-ESC-001 — decoded source paths must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.obsidian_projection import materialize_obsidian_projection
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def _living(vault: Path) -> str:
    return (
        vault / "generated" / "obsidian" / "projects" / "harbor-api" / "project-living.md"
    ).read_text(encoding="utf-8")


def _assert_no_token(written: str) -> None:
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_compilation_source_path_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    plant = vault / "state" / "compilation-outcomes" / "harbor-api.json"
    plant.parent.mkdir(parents=True)
    raw = (
        "{\n"
        '  "candidates": [{\n'
        f'    "source_path": "{ESC}",\n'
        '    "source_id": "src-compile",\n'
        '    "outcome": "FAILED"\n'
        "  }]\n"
        "}\n"
    )
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_obsidian_projection(vault, project_id="harbor-api", refresh_brief=False)
    _assert_no_token(_living(vault))


def test_json_unicode_escape_source_manifest_path_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    raw = (
        "{\n"
        '  "sources": [{\n'
        f'    "path": "{ESC}",\n'
        '    "source_id": "src-d",\n'
        '    "likely_project": "harbor-api",\n'
        '    "exclusion_reason": "configured-exclusion"\n'
        "  }]\n"
        "}\n"
    )
    plant = vault / "sources" / "manifests" / "source-manifest.json"
    plant.parent.mkdir(parents=True)
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_obsidian_projection(vault, project_id="harbor-api", refresh_brief=False)
    _assert_no_token(_living(vault))


def test_json_unicode_escape_secret_findings_path_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    manifest = vault / "sources" / "manifests" / "source-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        (
            "{\n"
            '  "sources": [{\n'
            '    "path": "docs/a.md",\n'
            '    "source_id": "src-s",\n'
            '    "likely_project": "harbor-api"\n'
            "  }]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    raw = (
        "{\n"
        '  "findings": [{\n'
        f'    "path": "{ESC}",\n'
        '    "source_id": "src-s",\n'
        '    "pattern": "cloud-access-key"\n'
        "  }]\n"
        "}\n"
    )
    plant = vault / "generated" / "reports" / "secret-findings.json"
    plant.parent.mkdir(parents=True)
    plant.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_obsidian_projection(vault, project_id="harbor-api", refresh_brief=False)
    _assert_no_token(_living(vault))

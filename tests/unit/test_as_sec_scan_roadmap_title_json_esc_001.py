"""AS-SEC-SCAN-ROADMAP-TITLE-JSON-ESC-001 — decoded roadmap titles must not persist."""

from __future__ import annotations

from pathlib import Path

from project_atlas.project_roadmap import materialize_roadmap_lenses
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def _write_item(vault: Path, item_json: str) -> str:
    proj = vault / "projects" / "harbor-api"
    proj.mkdir(parents=True)
    raw = (
        "# Roadmap\n\n## Roadmap record\n```json\n"
        "{\n"
        '  "items": [' + item_json + "]\n"
        "}\n"
        "```\n"
    )
    (proj / "roadmap.md").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_roadmap_lenses(vault, project_ids=["harbor-api"])
    return (vault / "generated" / "answers" / "ans-roadmap-harbor-api.json").read_text(
        encoding="utf-8"
    )


def test_json_unicode_escape_roadmap_title_is_not_persisted(tmp_path: Path) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            '    "id": "wp-1",\n'
            f'    "title": "{ESC}",\n'
            '    "status": "IN_PROGRESS"\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_roadmap_item_id_is_not_persisted(tmp_path: Path) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            f'    "id": "{ESC}",\n'
            '    "title": "safe title",\n'
            '    "status": "IN_PROGRESS"\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_roadmap_milestone_is_not_persisted(tmp_path: Path) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            '    "id": "wp-1",\n'
            '    "title": "safe title",\n'
            '    "status": "IN_PROGRESS",\n'
            f'    "milestone": "{ESC}"\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_roadmap_blocker_reason_is_not_persisted(tmp_path: Path) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            '    "id": "wp-1",\n'
            '    "title": "safe title",\n'
            '    "status": "BLOCKED",\n'
            f'    "blockers": [{{"reason": "{ESC}"}}]\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_roadmap_notes_are_not_persisted(tmp_path: Path) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            '    "id": "wp-1",\n'
            '    "title": "safe title",\n'
            '    "status": "IN_PROGRESS",\n'
            f'    "notes": ["{ESC}"]\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_roadmap_status_flag_is_not_persisted(tmp_path: Path) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            '    "id": "wp-1",\n'
            '    "title": "safe title",\n'
            f'    "status": "{ESC}"\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []
    assert "unrecognized_status:" + TOKEN not in written


def test_json_unicode_escape_roadmap_lifecycle_flag_is_not_persisted(
    tmp_path: Path,
) -> None:
    written = _write_item(
        tmp_path / "vault",
        (
            "{\n"
            '    "id": "wp-1",\n'
            '    "title": "safe title",\n'
            '    "status": "IN_PROGRESS",\n'
            f'    "lifecycle": "{ESC}"\n'
            "  }"
        ),
    )
    assert TOKEN not in written
    assert scan_text(written) == []
    assert "unrecognized_lifecycle:" + TOKEN not in written


def test_json_unicode_escape_milestone_status_flag_is_not_persisted(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    proj = vault / "projects" / "harbor-api"
    proj.mkdir(parents=True)
    raw = (
        "# Roadmap\n\n## Roadmap record\n```json\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "id": "wp-1",\n'
        '      "title": "safe title",\n'
        '      "status": "VERIFIED_COMPLETION",\n'
        '      "evidence": ["projects/harbor-api/roadmap.md"]\n'
        "    }\n"
        "  ],\n"
        '  "milestones": [\n'
        "    {\n"
        '      "id": "ms-1",\n'
        '      "title": "safe milestone",\n'
        f'      "status": "{ESC}"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
    )
    (proj / "roadmap.md").write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    materialize_roadmap_lenses(vault, project_ids=["harbor-api"])
    written = (vault / "generated" / "answers" / "ans-roadmap-harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_state_lens_summary_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    proj = vault / "projects" / "harbor-api"
    proj.mkdir(parents=True)
    roadmap = (
        "# Roadmap\n\n## Roadmap record\n```json\n"
        '{"items": []}\n'
        "```\n"
    )
    (proj / "roadmap.md").write_text(roadmap, encoding="utf-8")
    answers = vault / "generated" / "answers"
    answers.mkdir(parents=True)
    state = '{\n  "summary": "' + ESC + '",\n  "rollup": "IN_PROGRESS"\n}\n'
    (answers / "ans-state-harbor-api.json").write_text(state, encoding="utf-8")
    assert scan_text(roadmap) == []
    assert scan_text(state) == []
    materialize_roadmap_lenses(vault, project_ids=["harbor-api"])
    written = (vault / "generated" / "answers" / "ans-roadmap-harbor-api.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []

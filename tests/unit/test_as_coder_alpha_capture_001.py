"""AS-CODER-ALPHA-CAPTURE-001 coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.agent_handoff import (
    AgentHandoffError,
    create_handoff,
    export_agent_context,
    resume_handoff,
)
from project_atlas.cli import EXIT_OK, main
from project_atlas.connect import connect_project
from project_atlas.session_capture import (
    SessionCaptureError,
    capture_session,
    list_captures,
)


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Capture Fixture\n\nSession memory seed.\n\n## Stack\n\nPython.\n",
        encoding="utf-8",
    )
    return root


def test_explicit_capture_and_context_surface(tmp_path: Path) -> None:
    project = _seed(tmp_path / "capture-fixture")
    vault = Path(connect_project(project)["vault"])
    report = capture_session(
        vault,
        "capture-fixture",
        summary="Wired capture defaults",
        kind="milestone",
        decisions=["Keep UNKNOWN honest"],
        changes=["Added atlas capture record"],
        next_work=["Dogfood connect+capture+handoff"],
        unknowns=["Windows ADV not required for this package"],
    )
    assert report["status"] == "ok"
    assert report["capture_id"].startswith("capture-")
    assert (vault / report["path"]).is_file()
    assert (vault / report["latest_path"]).is_file()
    listed = list_captures(vault, project_id="capture-fixture")
    assert listed and listed[0]["capture_id"] == report["capture_id"]

    ctx = export_agent_context(vault, "capture-fixture", refresh_brief=False)
    md = (vault / ctx["markdown_path"]).read_text(encoding="utf-8")
    assert "## Session memory (captures)" in md
    assert "Wired capture defaults" in md
    assert "Keep UNKNOWN honest" in md
    assert "generated_at" not in json.loads((vault / report["path"]).read_text(encoding="utf-8"))


def test_handoff_semi_auto_capture(tmp_path: Path) -> None:
    project = _seed(tmp_path / "semi-auto")
    vault = Path(connect_project(project)["vault"])
    created = create_handoff(
        vault,
        "semi-auto",
        note="overnight checkpoint",
        refresh_brief=False,
        auto_capture=True,
    )
    capture = created.get("session_capture") or {}
    assert capture.get("capture_id", "").startswith("capture-")
    assert capture.get("source") == "handoff-auto"
    md = (vault / created["context_markdown"]).read_text(encoding="utf-8")
    assert "overnight checkpoint" in md

    skipped = create_handoff(
        vault,
        "semi-auto",
        note="no auto",
        refresh_brief=False,
        auto_capture=False,
    )
    assert skipped.get("session_capture") is None


def test_cli_capture_record_and_list(tmp_path: Path) -> None:
    project = _seed(tmp_path / "cli-capture")
    vault = Path(connect_project(project)["vault"])
    assert (
        main(
            [
                "capture",
                "record",
                "--vault",
                str(vault),
                "--project",
                "cli-capture",
                "--summary",
                "CLI capture works",
                "--decision",
                "Use ops receipts",
                "--change",
                "Added capture CLI",
                "--json",
            ]
        )
        == EXIT_OK
    )
    assert (
        main(
            [
                "capture",
                "list",
                "--vault",
                str(vault),
                "--project",
                "cli-capture",
                "--json",
            ]
        )
        == EXIT_OK
    )


def test_capture_fail_closed_inputs(tmp_path: Path) -> None:
    project = _seed(tmp_path / "capture-safety")
    vault = Path(connect_project(project)["vault"])
    with pytest.raises(SessionCaptureError):
        capture_session(vault, "capture-safety", summary="   ")
    with pytest.raises(SessionCaptureError):
        capture_session(vault, "capture-safety", summary="ok", kind="invented")
    with pytest.raises(SessionCaptureError):
        capture_session(vault, "../escape", summary="ok")
    with pytest.raises(SessionCaptureError):
        capture_session(tmp_path / "missing-vault", "capture-safety", summary="ok")


def test_resume_handoff_rejects_traversal_pointer(tmp_path: Path) -> None:
    project = _seed(tmp_path / "pointer-fixture")
    vault = Path(connect_project(project)["vault"])
    create_handoff(
        vault,
        "pointer-fixture",
        note="safe",
        refresh_brief=False,
        auto_capture=False,
    )
    outside = tmp_path / "outside-pack.json"
    outside.write_text(
        json.dumps({"handoff_id": "evil", "project_id": "pointer-fixture"}),
        encoding="utf-8",
    )
    latest = vault / "generated" / "ops" / "handoffs" / "latest.json"
    for bad_path in (
        "../../outside-pack.json",
        str(outside),
        "generated\\ops\\handoffs\\x.json",
    ):
        latest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "handoff_id": "evil",
                    "path": bad_path,
                    "project_id": "pointer-fixture",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        with pytest.raises(AgentHandoffError):
            resume_handoff(vault)

"""AS-CODER-ALPHA-CONNECT-001 — unit coverage for ``atlas connect``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.connect import (
    BIND_RELATIVE,
    DEFAULT_VAULT_DIRNAME,
    ConnectError,
    connect_project,
    project_slug_from_dirname,
    resolve_vault_path,
)
from project_atlas.discovery import discover


def _seed_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text(
        "# Coder Alpha Fixture\n\nPersistent brain dogfood seed.\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir()
    (root / "docs" / "DECISIONS.md").write_text(
        "# Decisions\n\nUse Atlas as the persistent project brain.\n",
        encoding="utf-8",
    )
    return root


def test_resolve_vault_defaults_to_project_atlas_vault(tmp_path: Path) -> None:
    project = _seed_project(tmp_path / "proj")
    assert resolve_vault_path(project, None) == (project / DEFAULT_VAULT_DIRNAME).resolve()


def test_resolve_vault_prefers_explicit_and_bind(tmp_path: Path) -> None:
    project = _seed_project(tmp_path / "proj")
    explicit = tmp_path / "explicit-vault"
    assert resolve_vault_path(project, explicit) == explicit.resolve()
    bind = project / BIND_RELATIVE
    bind.parent.mkdir(parents=True)
    bind.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_root": project.resolve().as_posix(),
                "vault": "../bound-vault",
                "vault_id": "atlas-main",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    assert resolve_vault_path(project, None) == (tmp_path / "bound-vault").resolve()


def test_connect_refuses_home_and_filesystem_root(tmp_path: Path) -> None:
    with pytest.raises(ConnectError, match="home directory"):
        connect_project(Path.home(), dry_run=True)
    with pytest.raises(ConnectError, match="filesystem root"):
        connect_project(Path("/"), dry_run=True)


def test_connect_dry_run_reports_plan_without_writes(tmp_path: Path) -> None:
    project = _seed_project(tmp_path / "proj")
    report = connect_project(project, dry_run=True)
    assert report["status"] == "dry_run"
    assert report["package"] == "AS-CODER-ALPHA-CONNECT-001"
    assert report["honesty"]["atlas_opt_wake_gate"] == "CLOSED"
    assert not (project / DEFAULT_VAULT_DIRNAME).exists()
    assert not (project / BIND_RELATIVE).exists()
    assert not (project / ".atlas-project.yaml").exists()


def test_connect_compiles_project_and_is_idempotent(tmp_path: Path) -> None:
    project = _seed_project(tmp_path / "my-cool-app")
    expected_id = project_slug_from_dirname(project.name, project_root=project)
    first = connect_project(project)
    assert first["status"] == "connected"
    assert first["documents_ingested"] >= 1
    assert first["documents_discovered"] >= 1
    assert first["projects"] == [expected_id]
    assert "unknown-project" not in first["projects"]
    vault = Path(first["vault"])
    assert (vault / "index.md").is_file()
    assert (project / BIND_RELATIVE).is_file()
    assert (vault / "generated" / "ops" / "connect-receipt.json").is_file()
    assert (vault / "projects" / expected_id / "project.md").is_file()
    assert any("ans-roadmap-" in path for path in first.get("roadmap_answers") or [])
    assert (vault / "generated" / "answers" / f"ans-roadmap-{expected_id}.json").is_file()
    marker = (project / ".atlas-project.yaml").read_text(encoding="utf-8")
    assert f"id: {expected_id}" in marker

    # Rediscover must not treat in-tree vault files as active sources.
    rediscovered = discover(project)
    active = [row["path"] for row in rediscovered["sources"] if not row.get("exclusion_reason")]
    assert all(".atlas-vault/" not in path for path in active)
    assert all(not path.startswith(".atlas/") for path in active)
    assert first["documents_discovered"] == len(active)

    second = connect_project(project)
    assert second["status"] == "connected"
    assert second["vault_created"] is False
    assert second["projects"] == first["projects"]
    bind = json.loads((project / BIND_RELATIVE).read_text(encoding="utf-8"))
    assert bind["vault"] == DEFAULT_VAULT_DIRNAME
    assert bind["generated"]["by"] == "atlas-coder-alpha-connect-001"
    # No wall-clock timestamps in bind/receipt (NFR-001).
    assert "generated_at" not in bind
    assert "at" not in bind.get("generated", {})


def test_project_slug_from_dirname_is_safe() -> None:
    assert project_slug_from_dirname("My Cool App") == "my-cool-app"
    assert project_slug_from_dirname("123-start") == "p-123-start"
    assert project_slug_from_dirname("@@@").startswith("project-")
    assert project_slug_from_dirname("文档一") != project_slug_from_dirname("文档二")


def test_cli_connect_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["connect", "--help"])
    assert excinfo.value.code == 0


def test_cli_connect_end_to_end(tmp_path: Path) -> None:
    project = _seed_project(tmp_path / "cli-proj")
    assert (
        main(
            [
                "connect",
                str(project),
                "--json",
            ]
        )
        == EXIT_OK
    )
    vault = project / DEFAULT_VAULT_DIRNAME
    assert (vault / ".atlas" / "vault.json").is_file()
    assert main(["connect", str(Path.home())]) == EXIT_ERROR

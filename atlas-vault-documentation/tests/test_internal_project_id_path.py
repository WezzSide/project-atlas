"""AS-CTRL-INTERNAL-PID-F1 — project_id must not escape internal vault paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from internal import graph_validation, ingestion_validation, routing_state
from internal.safe_project import require_project_id


def test_ingestion_validate_rejects_traversal(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "ingestion" / "state").mkdir(parents=True)
    (outside / "secret-inv.json").write_text(
        json.dumps(
            {
                "project_id": "x",
                "documents": [{"document_id": "x:1", "processing": {"state": "discovered"}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe project id"):
        ingestion_validation.validate(vault, "../../../outside/secret-inv")


def test_graph_validate_rejects_traversal(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "relationships" / "receipts").mkdir(parents=True)
    with pytest.raises(ValueError, match="unsafe project id"):
        graph_validation.validate(vault, "../../../outside/graph-state")


def test_routing_state_rejects_traversal(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "routing" / "state").mkdir(parents=True)
    (outside / "rs-secret.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": "x",
                "routed_events": {},
                "work_packages": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe project id"):
        routing_state.load_state(vault / "routing" / "state", "../../../outside/rs-secret")


def test_graph_scripts_reject_traversal_project_id(tmp_path: Path) -> None:
    """AS-CTRL-INTERNAL-PID-F1 residual: CLI --project-id fails closed before path join."""
    from scripts.ingest_graphify import main as ingest_main
    from scripts.rebuild_relationships import main as rebuild_main

    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "evil-project.json").write_text("{}", encoding="utf-8")

    for bad in (
        "../../../outside/evil-project",
        "..\\..\\outside\\evil-project",
        "..",
        "",
    ):
        with pytest.raises(ValueError, match="unsafe project id"):
            require_project_id(bad)

    rc = ingest_main(
        ["--vault", str(vault), "--project-id", "../../../outside/evil-project"]
    )
    assert rc == 3

    rc = rebuild_main(
        ["--vault", str(vault), "--project-id", "../../../outside/evil-project"]
    )
    assert rc == 3

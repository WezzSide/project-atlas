"""AS-GRAPH-001 — Graphify artifact acceptance (derived-only)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from project_atlas.config import AtlasConfig, GraphifyConfig, load_config
from project_atlas.graph_acceptance import (
    AUTHORITY_LEVEL,
    TRUTH_BOUNDARY,
    GraphAcceptanceError,
    accept_graphify_artifacts,
    classify_graphify_document,
    inspect_acceptance,
)
from project_atlas.ingestion import _classify
from project_atlas.schema import available_schemas, validate_record
from project_atlas.validation import validate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "graphify-present"
GRAPHIFY_OUT = FIXTURE / "graphify-out"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_for(root: Path) -> dict[str, object]:
    sources: list[dict[str, object]] = []
    for path in sorted((root / "graphify-out").iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        sources.append(
            {
                "source_id": f"source-{path.stem}",
                "path": relative,
                "media_type": "application/json",
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "classification_state": "unclassified",
                "authority": {"level": "derived"},
            }
        )
    return {"schema_version": 1, "project_id": "graphify-present", "sources": sources}


def test_graph_schemas_registered() -> None:
    kinds = available_schemas()
    for kind in (
        "graphify-envelope",
        "graphify-node",
        "graphify-edge",
        "graphify-metadata",
        "graph-acceptance-receipt",
    ):
        assert kind in kinds


def test_semantic_ingestion_defaults_false() -> None:
    config = load_config()
    assert config.graphify.enabled is True
    assert config.graphify.semantic_ingestion is False


def test_classify_graphify_basename_before_keywords() -> None:
    assert classify_graphify_document("graphify-out/graph.json") == "graphify-output"
    label, method = _classify("graphify-out/graph.json", '{"architecture":"design"}')
    assert label == "graphify-output"
    assert method == "deterministic-graphify-basename"


def test_golden_graphify_present_accepts_four_derived_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    manifest = _manifest_for(root)
    receipt = accept_graphify_artifacts(
        project_root=root,
        manifest=manifest,
        config=AtlasConfig(),
        strict=True,
    )
    assert receipt.accepted_count == 4
    assert receipt.rejected_count == 0
    assert receipt.semantic_status == "disabled"
    assert all(item.authority_level == AUTHORITY_LEVEL for item in receipt.artifacts)
    metadata = next(item for item in receipt.artifacts if item.family == "metadata")
    assert metadata.node_count == 0
    assert metadata.edge_count == 0
    assert receipt.node_count > 0
    assert receipt.edge_count > 0
    payload = receipt.as_dict()
    validate_record(payload, "graph-acceptance-receipt")
    assert payload["truth_boundary"] == TRUTH_BOUNDARY
    summary = inspect_acceptance(receipt)
    assert summary["authority_level"] == "derived"
    assert summary["semantic_ingestion"] == "disabled"
    assert len(summary["artifact_ids"]) == 4


def test_metadata_no_emit_even_when_alone(tmp_path: Path) -> None:
    root = tmp_path / "meta-only"
    out = root / "graphify-out"
    out.mkdir(parents=True)
    meta = out / "metadata.json"
    meta.write_text('{"schema_version":1,"generator":"fixture"}\n', encoding="utf-8")
    manifest = {
        "project_id": "meta-only",
        "sources": [
            {
                "source_id": "src-meta",
                "path": "graphify-out/metadata.json",
                "sha256": _sha256(meta),
                "size_bytes": meta.stat().st_size,
                "media_type": "application/json",
                "classification_state": "unclassified",
            }
        ],
    }
    receipt = accept_graphify_artifacts(project_root=root, manifest=manifest, strict=True)
    assert receipt.accepted_count == 1
    assert receipt.node_count == 0
    assert receipt.edge_count == 0


def test_hash_mismatch_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    manifest = _manifest_for(root)
    for source in manifest["sources"]:  # type: ignore[index]
        assert isinstance(source, dict)
        if source["path"] == "graphify-out/graph.json":
            source["sha256"] = "0" * 64
    with pytest.raises(GraphAcceptanceError, match="hash-mismatch"):
        accept_graphify_artifacts(project_root=root, manifest=manifest, strict=True)


def test_path_escape_rejected(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "graph.json").write_text(
        '{"schema_version":1,"nodes":[{"id":"a"}],"edges":[]}\n', encoding="utf-8"
    )
    digest = _sha256(root / "graph.json")
    manifest = {
        "project_id": "proj",
        "sources": [
            {
                "source_id": "src-escape",
                "path": "../graph.json",
                "sha256": digest,
                "size_bytes": 1,
                "media_type": "application/json",
                "classification_state": "unclassified",
            }
        ],
    }
    with pytest.raises(GraphAcceptanceError, match="path-escape"):
        accept_graphify_artifacts(project_root=root, manifest=manifest, strict=True)


def test_unknown_schema_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    out = root / "graphify-out"
    out.mkdir(parents=True)
    path = out / "graph.json"
    path.write_text('{"schema_version":"graphify-99.0","nodes":[],"edges":[]}\n', encoding="utf-8")
    manifest = {
        "project_id": "proj",
        "sources": [
            {
                "source_id": "src-unknown",
                "path": "graphify-out/graph.json",
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
                "media_type": "application/json",
                "classification_state": "unclassified",
            }
        ],
    }
    with pytest.raises(GraphAcceptanceError):
        accept_graphify_artifacts(project_root=root, manifest=manifest, strict=True)


def test_semantic_flag_true_fails_closed() -> None:
    with pytest.raises(GraphAcceptanceError, match="semantic_ingestion_unsupported"):
        accept_graphify_artifacts(
            project_root=FIXTURE,
            manifest={"project_id": "x", "sources": []},
            config=GraphifyConfig(semantic_ingestion=True),
            strict=True,
        )


def test_determinism_replay(tmp_path: Path) -> None:
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    manifest = _manifest_for(root)
    first = accept_graphify_artifacts(project_root=root, manifest=manifest).to_json()
    second = accept_graphify_artifacts(project_root=root, manifest=manifest).to_json()
    assert first == second


def test_no_truth_layer_mutation(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for relative in (
        "state/current-state/keep.json",
        "state/authoritative-state/keep.json",
        "state/claims/keep.json",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"sentinel":true}\n', encoding="utf-8")
    before = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    accept_graphify_artifacts(project_root=root, manifest=_manifest_for(root), strict=True)
    after = {path: path.read_bytes() for path in vault.rglob("*") if path.is_file()}
    assert before == after
    assert not (vault / "relationships").exists()


def test_legacy_vault_validate_without_graph_receipts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for relative in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")
    # Minimal vault will still fail knowledge/portfolio checks; assert graph
    # acceptance does not add errors when the directory is absent.
    result = validate(vault)
    assert all("graph acceptance" not in err for err in result["errors"])


def test_optional_validate_accepts_derived_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    for relative in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# ok\n", encoding="utf-8")
    root = tmp_path / "graphify-present"
    shutil.copytree(FIXTURE, root)
    receipt = accept_graphify_artifacts(project_root=root, manifest=_manifest_for(root))
    out = vault / "generated" / "graph" / "acceptance" / "graphify-present.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(receipt.to_json(), encoding="utf-8")
    result = validate(vault)
    assert all("graph acceptance" not in err for err in result["errors"])

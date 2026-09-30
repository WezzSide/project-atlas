"""AS-GRAPH-005 — Human-readable derived graph projections (G5 matrix).

Truth boundary: GRAPH PROJECTION ≠ AUTOMATIC AUTHORITY.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import project_atlas.graph_projections as gp
from project_atlas.graph_projections import (
    AUTHORITY_LEVEL,
    DERIVED_LABEL,
    INTELLIGENCE_LABEL,
    PACKAGE_ID,
    TRUTH_BOUNDARY,
    GraphProjectionError,
    inspect_projections,
    load_health_from_vault,
    load_relationships_from_vault,
    materialize_projections,
    materialize_projections_from_vault,
    promote_projection_path_forbidden,
    promote_projection_to_authority_forbidden,
    promote_projection_to_claim_forbidden,
    render_graph_health_markdown,
    render_relationships_markdown,
    write_projection_outputs,
)
from project_atlas.graph_quarantine import GraphHealthSnapshot
from project_atlas.graph_relationships import RelationshipRecord


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relationship(
    relationship_id: str,
    *,
    rel_type: str = "depends-on",
    source: str = "demo:a",
    target: str = "demo:b",
    quality: str = "inferred",
) -> RelationshipRecord:
    return RelationshipRecord(
        project_id="demo",
        relationship_id=relationship_id,
        relationship_type=rel_type,
        source_entity_id=source,
        target_entity_id=target,
        source_graphify_id="a",
        target_graphify_id="b",
        link_quality=quality,  # type: ignore[arg-type]
        relationship_fingerprint="c" * 64,
        provenance={
            "graphify_artifact_refs": [
                {"relative_path": "graphify-out/graph.json", "sha256": "a" * 64}
            ]
        },
    )


def _health(
    *,
    retained: int = 1,
    quarantined: int = 0,
    state: str = "healthy",
) -> GraphHealthSnapshot:
    return GraphHealthSnapshot(
        project_id="demo",
        retained_count=retained,
        quarantined_count=quarantined,
        category_counts={"orphaned-endpoint": quarantined} if quarantined else {},
        link_quality_histogram={"inferred": retained} if retained else {},
        health_state=state,  # type: ignore[arg-type]
        input_content_hash="d" * 64,
    )


def test_package_constants() -> None:
    assert PACKAGE_ID == "AS-GRAPH-005"
    assert AUTHORITY_LEVEL == "derived"
    assert "PROJECTION" in TRUTH_BOUNDARY
    assert "derived" in DERIVED_LABEL
    assert "Layer A" in INTELLIGENCE_LABEL


def test_render_relationships_deterministic_order_and_labels() -> None:
    records = (
        _relationship("rel-b", rel_type="part-of", source="demo:z", target="demo:y"),
        _relationship("rel-a", rel_type="depends-on", source="demo:a", target="demo:b"),
    )
    first = render_relationships_markdown(records, project_id="demo")
    second = render_relationships_markdown(records, project_id="demo")
    assert first == second
    assert DERIVED_LABEL in first
    assert INTELLIGENCE_LABEL in first
    assert TRUTH_BOUNDARY in first
    assert "authority: `derived`" in first
    # depends-on before part-of
    assert first.index("depends-on") < first.index("part-of")
    assert "<!-- atlas:generated:start -->" in first
    assert "<!-- BEGIN HUMAN: notes -->" in first


def test_render_absent_graph_state_no_speculation() -> None:
    md = render_relationships_markdown((), project_id="demo")
    assert "No speculative relationship content" in md
    assert 'source_state: "absent"' in md
    health_md = render_graph_health_markdown(None, project_id="demo")
    assert "No speculative health content" in health_md
    assert 'source_state: "absent"' in health_md


def test_render_health_metadata_only() -> None:
    md = render_graph_health_markdown(
        _health(retained=2, quarantined=3, state="degraded"),
        project_id="demo",
    )
    assert "degraded" in md
    assert "orphaned-endpoint" in md
    assert "trust score" not in md.lower() or "not trust" in md.lower()
    assert "GRAPH HEALTH ≠ PROJECT AUTHORITY" in md


def test_materialize_bundle_and_inspect() -> None:
    bundle = materialize_projections(
        project_id="demo",
        relationships=(_relationship("rel-1"),),
        health=_health(),
    )
    assert bundle.relationship_count == 1
    assert bundle.health_present is True
    assert bundle.source_state == "present"
    info = inspect_projections(bundle)
    assert info["package_id"] == PACKAGE_ID
    assert info["relationship_count"] == 1


def test_write_projections_and_preserve_human_region(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    bundle = materialize_projections(
        project_id="demo",
        relationships=(_relationship("rel-1"),),
        health=_health(),
    )
    planned = write_projection_outputs(bundle, vault=vault)
    assert planned == [
        "generated/graph/projections/demo/graph-health.md",
        "generated/graph/projections/demo/relationships.md",
    ]
    rel_path = vault / "generated/graph/projections/demo/relationships.md"
    health_path = vault / "generated/graph/projections/demo/graph-health.md"
    assert rel_path.is_file()
    assert health_path.is_file()

    human_block = (
        "<!-- BEGIN HUMAN: notes -->\n"
        "Operator note: keep this byte-stable.\n"
        "<!-- END HUMAN: notes -->\n"
    )
    text = rel_path.read_text(encoding="utf-8")
    text = text.replace(
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->\n",
        human_block,
    )
    rel_path.write_text(text, encoding="utf-8")
    prior_digest = _sha256(rel_path)

    updated = materialize_projections(
        project_id="demo",
        relationships=(
            _relationship("rel-1"),
            _relationship("rel-2", source="demo:c", target="demo:d"),
        ),
        health=_health(retained=2),
    )
    write_projection_outputs(updated, vault=vault)
    refreshed = rel_path.read_text(encoding="utf-8")
    assert "Operator note: keep this byte-stable." in refreshed
    assert "rel-2" in refreshed
    # Human region bytes preserved; whole file digest may change from generated body.
    assert human_block.strip() in refreshed
    assert prior_digest != _sha256(rel_path)


def test_malformed_protected_markers_fail_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    bundle = materialize_projections(project_id="demo", relationships=(), health=None)
    write_projection_outputs(bundle, vault=vault)
    rel_path = vault / "generated/graph/projections/demo/relationships.md"
    rel_path.write_text(
        rel_path.read_text(encoding="utf-8") + "<!-- BEGIN HUMAN: broken -->\n",
        encoding="utf-8",
    )
    with pytest.raises(GraphProjectionError, match="malformed-protected-markers"):
        write_projection_outputs(bundle, vault=vault)


def test_promote_failure_leaves_prior_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    bundle = materialize_projections(
        project_id="demo",
        relationships=(_relationship("rel-1"),),
        health=_health(),
    )
    write_projection_outputs(bundle, vault=vault)
    rel_path = vault / "generated/graph/projections/demo/relationships.md"
    prior = rel_path.read_bytes()
    prior_digest = _sha256(rel_path)

    calls = {"n": 0}
    real_replace = gp._replace_path

    def boom(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("injected-promote-failure")
        real_replace(src, dst)

    monkeypatch.setattr(gp, "_replace_path", boom)
    updated = materialize_projections(
        project_id="demo",
        relationships=(_relationship("rel-1"), _relationship("rel-2")),
        health=_health(retained=2),
    )
    with pytest.raises(GraphProjectionError, match="promotion-failed-prior-state-intact"):
        write_projection_outputs(updated, vault=vault)
    assert rel_path.read_bytes() == prior
    assert _sha256(rel_path) == prior_digest


def test_forbidden_write_prefixes() -> None:
    for relative in (
        "relationships/nodes.md",
        "claims/x.md",
        "generated/graph/relationships/demo/x.json",
        "generated/graph/health/demo/health.json",
        "generated/graph/quarantine/demo/q.json",
        "../escape.md",
    ):
        with pytest.raises(GraphProjectionError):
            promote_projection_path_forbidden(relative)


def test_authority_and_claim_elevation_forbidden() -> None:
    with pytest.raises(GraphProjectionError, match="authority-elevation-forbidden"):
        promote_projection_to_authority_forbidden()
    with pytest.raises(GraphProjectionError, match="claim-synthesis-forbidden"):
        promote_projection_to_claim_forbidden()


def test_load_from_vault_consume_only(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    rel_dir = vault / "generated/graph/relationships/demo"
    health_dir = vault / "generated/graph/health/demo"
    rel_dir.mkdir(parents=True)
    health_dir.mkdir(parents=True)
    record = _relationship("rel-1")
    rel_bytes = record.to_json().encode("utf-8")
    (rel_dir / "rel-1.json").write_bytes(rel_bytes)
    health = _health(quarantined=1, state="degraded")
    health_bytes = health.to_json().encode("utf-8")
    (health_dir / "health.json").write_bytes(health_bytes)
    rel_digest = hashlib.sha256(rel_bytes).hexdigest()
    health_digest = hashlib.sha256(health_bytes).hexdigest()

    loaded = load_relationships_from_vault(vault, project_id="demo")
    assert len(loaded) == 1
    assert loaded[0].relationship_id == "rel-1"
    loaded_health = load_health_from_vault(vault, project_id="demo")
    assert loaded_health is not None
    assert loaded_health.health_state == "degraded"

    # Consume-only: machine stores unchanged.
    assert _sha256(rel_dir / "rel-1.json") == rel_digest
    assert _sha256(health_dir / "health.json") == health_digest

    bundle = materialize_projections_from_vault(vault, project_id="demo")
    write_projection_outputs(bundle, vault=vault)
    assert (vault / "generated/graph/projections/demo/relationships.md").is_file()
    # Still unchanged after projection write.
    assert _sha256(rel_dir / "rel-1.json") == rel_digest
    assert _sha256(health_dir / "health.json") == health_digest


def test_secret_shaped_reason_redacted_in_projection() -> None:
    record = _relationship("rel-1")
    # Inject secret-shaped text into entity id path used by renderer redaction.
    dirty = RelationshipRecord(
        project_id="demo",
        relationship_id="rel-secret",
        relationship_type="depends-on",
        source_entity_id="demo:password=hunter2",
        target_entity_id="demo:b",
        source_graphify_id="a",
        target_graphify_id="b",
        link_quality="inferred",
        relationship_fingerprint="e" * 64,
        provenance={},
    )
    md = render_relationships_markdown((dirty,), project_id="demo")
    assert "hunter2" not in md
    assert "redacted-sensitive" in md
    _ = record


def test_project_mismatch_fail_closed() -> None:
    with pytest.raises(GraphProjectionError, match="relationship-project-mismatch"):
        materialize_projections(
            project_id="other",
            relationships=(_relationship("rel-1"),),
        )

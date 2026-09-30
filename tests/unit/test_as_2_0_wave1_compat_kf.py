"""AS-2.0-COMPAT-001 and AS-KF2-* Wave 1 tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.compat_anchor import (
    EXPECTED_FREEZE_HEAD,
    EXPECTED_FREEZE_TREE,
    SNAPSHOT_ID,
    CompatAnchorError,
    load_compatibility_anchor,
    require_compatibility_anchor,
)
from project_atlas.kf2_fabric import (
    Kf2Error,
    register_entity,
    register_namespace,
    register_relationship,
)
from project_atlas.schema import available_schemas, validate_record

ROOT = Path(__file__).resolve().parents[2]


def test_compatibility_anchor_loads_and_pins() -> None:
    anchor = load_compatibility_anchor()
    assert anchor.snapshot_id == SNAPSHOT_ID
    assert anchor.software_freeze_head == EXPECTED_FREEZE_HEAD
    assert anchor.software_freeze_tree == EXPECTED_FREEZE_TREE
    assert anchor.release_certified is True
    assert anchor.one_dot_oh_wins_conflicts is True
    assert anchor.authentic_estate_pilot_passed is False
    validate_record(anchor.as_dict(), "compatibility-anchor")


def test_compatibility_anchor_detects_head_drift(tmp_path: Path) -> None:
    src = ROOT / "docs" / "releases" / "1.0.0" / "compatibility-anchor.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["software_freeze_head"] = "0" * 40
    bad = tmp_path / "compatibility-anchor.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CompatAnchorError, match="head-drift"):
        load_compatibility_anchor(bad)


def test_kf2_namespace_entity_relationship_roundtrip(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    anchor = require_compatibility_anchor()
    ns = register_namespace(
        vault,
        namespace_id="portfolio",
        display_name="Portfolio",
        anchor=anchor,
    )
    assert ns.namespace_id == "portfolio"
    entity_a = register_entity(
        vault,
        entity_id="svc-api",
        namespace_id="portfolio",
        display_name="API Service",
        xproj_global_entity_id="global-api-1",
        anchor=anchor,
    )
    entity_b = register_entity(
        vault,
        entity_id="svc-web",
        namespace_id="portfolio",
        display_name="Web Service",
        anchor=anchor,
    )
    rel = register_relationship(
        vault,
        relationship_id="rel-web-depends-api",
        from_entity_id=entity_b.entity_id,
        to_entity_id=entity_a.entity_id,
        relation_type="depends-on",
        anchor=anchor,
    )
    assert rel.relation_type == "depends-on"
    validate_record(ns.as_dict(), "kf2-namespace")
    validate_record(entity_a.as_dict(), "kf2-entity")
    validate_record(rel.as_dict(), "kf2-relationship")
    assert (vault / "generated" / "kf2" / "namespaces" / "portfolio.json").is_file()
    assert (vault / "generated" / "kf2" / "entities" / "svc-api.json").is_file()
    assert (vault / "generated" / "kf2" / "relationships" / "rel-web-depends-api.json").is_file()


def test_kf2_requires_namespace_before_entity(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(Kf2Error, match="namespace-missing"):
        register_entity(
            vault,
            entity_id="orphan",
            namespace_id="missing",
            display_name="Orphan",
        )


def test_kf2_rejects_self_loop(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    register_namespace(vault, namespace_id="ns", display_name="NS")
    register_entity(vault, entity_id="e1", namespace_id="ns", display_name="E1")
    with pytest.raises(Kf2Error, match="self-loop"):
        register_relationship(
            vault,
            relationship_id="loop",
            from_entity_id="e1",
            to_entity_id="e1",
            relation_type="related-to",
        )


def test_wave1_schemas_registered() -> None:
    kinds = set(available_schemas())
    for kind in (
        "compatibility-anchor",
        "kf2-namespace",
        "kf2-entity",
        "kf2-relationship",
    ):
        assert kind in kinds


def test_package_docs_present() -> None:
    docs = ROOT / "docs"
    assert (docs / "AS-2.0-COMPAT-001.md").is_file()
    assert (docs / "AS-KF2-WAVE1.md").is_file()
    text = (docs / "AS-KF2-WAVE1.md").read_text(encoding="utf-8")
    assert "AS-KF2-ENTITY-001" in text
    assert "AS-KF2-REL-001" in text
    assert "AS-KF2-NS-001" in text
    assert "≠ AUTHORITY" in text or "not authority" in text.lower()

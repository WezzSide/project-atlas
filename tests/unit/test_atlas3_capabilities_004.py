"""AT3-004 semantic capability registry."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from project_atlas.atlas3.capabilities import (
    REGISTRY,
    get_capability,
    list_capabilities,
    register_capability,
)
from project_atlas.atlas3.contracts import Atlas3Error

ROOT = Path(__file__).resolve().parents[2]


def test_surfaces_are_not_capabilities() -> None:
    catalog = list_capabilities()
    assert catalog["surface_is_capability"] is False
    assert catalog["count"] >= 6
    assert get_capability("atlas3.pulse")["semantic_contract"] == "AT3-015"
    assert get_capability("atlas3.chronicle")["maturity"] == "roadmap-horizon"


def test_wrapper_inflation_fails_closed() -> None:
    with pytest.raises(Atlas3Error) as exc:
        register_capability(
            {
                "capability_id": "atlas3.pulse-web",
                "semantic_contract": "AT3-015",
                "truth_dependency": "derived",
                "required_evidence": [],
                "available_surfaces": ["web"],
                "maturity": "implementation-unlocked",
                "demo_required": False,
                "security_class": "read-derived",
            }
        )
    assert exc.value.code == "WRAPPER_INFLATION"
    assert "atlas3.pulse-web" not in REGISTRY


def test_registry_matches_shipped_schema() -> None:
    schema = json.loads(
        (ROOT / "docs/atlas-3/contracts/capability.schema.json").read_text(encoding="utf-8")
    )
    for capability in REGISTRY.values():
        jsonschema.validate(capability, schema)


def test_register_rejects_authority_security_class() -> None:
    """Catalog must not mint authority as a capability security class."""
    with pytest.raises(Atlas3Error) as exc:
        register_capability(
            {
                "capability_id": "atlas3.forged-authority",
                "semantic_contract": "AT3-004-FORGED",
                "truth_dependency": "none",
                "required_evidence": [],
                "available_surfaces": ["python"],
                "maturity": "implementation-unlocked",
                "demo_required": False,
                "security_class": "authority",
            }
        )
    assert exc.value.code == "AUTHORITY_SECURITY_CLASS"
    assert "atlas3.forged-authority" not in REGISTRY


def test_register_rejects_unknown_security_class() -> None:
    with pytest.raises(Atlas3Error) as exc:
        register_capability(
            {
                "capability_id": "atlas3.forged-unknown-class",
                "semantic_contract": "AT3-004-UNKNOWN-CLASS",
                "truth_dependency": "none",
                "required_evidence": [],
                "available_surfaces": ["python"],
                "maturity": "implementation-unlocked",
                "demo_required": False,
                "security_class": "truth-core",
            }
        )
    assert exc.value.code == "UNKNOWN_SECURITY_CLASS"
    assert "atlas3.forged-unknown-class" not in REGISTRY


def test_register_rejects_authority_claim_key() -> None:
    with pytest.raises(Atlas3Error) as exc:
        register_capability(
            {
                "capability_id": "atlas3.forged-authority-flag",
                "semantic_contract": "AT3-004-FORGED-FLAG",
                "truth_dependency": "none",
                "required_evidence": [],
                "available_surfaces": ["python"],
                "maturity": "implementation-unlocked",
                "demo_required": False,
                "security_class": "owner-gated",
                "is_authority": True,
            }
        )
    assert exc.value.code == "AUTHORITY_CLAIM_FORBIDDEN"
    assert "atlas3.forged-authority-flag" not in REGISTRY


def test_register_accepts_closed_security_class() -> None:
    cid = "atlas3.honest-owner-gated"
    try:
        registered = register_capability(
            {
                "capability_id": cid,
                "semantic_contract": "AT3-004-HONEST",
                "truth_dependency": "owner-gate",
                "required_evidence": ["owner_decision"],
                "available_surfaces": ["python"],
                "maturity": "implementation-unlocked",
                "demo_required": False,
                "security_class": "owner-gated",
            }
        )
        assert registered["security_class"] == "owner-gated"
        assert get_capability(cid)["security_class"] == "owner-gated"
    finally:
        REGISTRY.pop(cid, None)

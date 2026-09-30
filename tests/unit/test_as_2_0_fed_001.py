"""AS-2.0-FED-001 federation join inventory tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.federation import (
    FederationError,
    FederationMember,
    build_join_inventory,
)
from project_atlas.schema import available_schemas, validate_record

ROOT = Path(__file__).resolve().parents[2]


def test_federation_join_inventory_happy_path(tmp_path: Path) -> None:
    primary = tmp_path / "vault-a"
    member = tmp_path / "vault-b"
    primary.mkdir()
    member.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    report = build_join_inventory(
        federation_id="estate-alpha",
        members=[
            FederationMember("alpha", str(primary), "primary", "proj-a"),
            FederationMember("beta", str(member), "member"),
        ],
        output_vault=out,
    )
    assert report["status"] == "joined"
    assert report["compat_snapshot_id"] == "atlas-1.0.0-compat"
    validate_record(report, "federation-join-inventory")
    assert (out / "generated" / "federation" / "estate-alpha-join-inventory.json").is_file()


def test_federation_refuses_missing_primary(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    report = build_join_inventory(
        federation_id="estate-beta",
        members=[
            FederationMember("a", str(a), "member"),
            FederationMember("b", str(b), "member"),
        ],
        output_vault=out,
    )
    assert report["status"] == "refused"
    assert report["refusal_reason"] == "federation-primary-count-invalid"


def test_federation_refuses_missing_vault(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    report = build_join_inventory(
        federation_id="estate-gamma",
        members=[
            FederationMember("a", str(a), "primary"),
            FederationMember("missing", str(tmp_path / "nope"), "member"),
        ],
        output_vault=out,
    )
    assert report["status"] == "refused"
    assert "federation-vault-missing" in str(report.get("refusal_reason"))


def test_federation_rejects_duplicate_roots(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(FederationError, match="vault-root-duplicate"):
        build_join_inventory(
            federation_id="estate-dup",
            members=[
                FederationMember("a", str(a), "primary"),
                FederationMember("b", str(a), "member"),
            ],
            output_vault=out,
        )


def test_federation_schema_registered_and_docs() -> None:
    assert "federation-join-inventory" in available_schemas()
    assert (ROOT / "docs" / "AS-2.0-FED-001.md").is_file()

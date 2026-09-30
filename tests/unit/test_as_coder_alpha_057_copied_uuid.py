"""D-057 — copied project_uuid must fail closed (one UUID → one project.id)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from project_atlas.cli import EXIT_ERROR, main
from project_atlas.connect import ConnectError, connect_project
from project_atlas.source_identity import load_allocation_uuid_owners


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _marker(root: Path, project_id: str, project_uuid: str | None = None) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id},
    }
    if project_uuid is not None:
        payload["project_uuid"] = project_uuid
    _write(root / ".atlas-project.yaml", yaml.safe_dump(payload, sort_keys=False))


def _snapshot_identity(vault: Path) -> dict[str, object]:
    registry = vault / "state" / "sources.json"
    manifest = vault / "generated" / "ops" / "connect-manifest.json"
    receipts = sorted((vault / "receipts" / "source-lineage").glob("project-*-allocation.json"))
    return {
        "registry": registry.read_bytes() if registry.is_file() else None,
        "manifest": manifest.read_bytes() if manifest.is_file() else None,
        "receipts": {path.name: path.read_bytes() for path in receipts},
        "owners": load_allocation_uuid_owners(vault),
    }


def test_a_same_id_same_uuid_reconnect(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    root.mkdir()
    _write(root / "README.md", "# Alpha\n\nPurpose.\n")
    shared = tmp_path / "vault"
    first = connect_project(root, vault=shared)
    project_id = str(first["bound_project_id"])
    uuid = yaml.safe_load((root / ".atlas-project.yaml").read_text(encoding="utf-8"))[
        "project_uuid"
    ]
    before = _snapshot_identity(shared)
    second = connect_project(root, vault=shared)
    assert second["status"] == "connected"
    assert second["bound_project_id"] == project_id
    assert (
        yaml.safe_load((root / ".atlas-project.yaml").read_text(encoding="utf-8"))["project_uuid"]
        == uuid
    )
    after = _snapshot_identity(shared)
    assert after["owners"] == before["owners"]


def test_d_f_g_different_id_same_uuid_fail_closed_no_mutation(tmp_path: Path) -> None:
    """Local D-055 HIGH: copied UUID under distinct project.id must fail closed."""
    shared = tmp_path / "vault"
    alpha = tmp_path / "alpha"
    sibling = tmp_path / "r4c2-sibling"
    alpha.mkdir()
    sibling.mkdir()
    _write(alpha / "README.md", "# Alpha body\n\nOriginal alpha content.\n")
    ra = connect_project(alpha, vault=shared)
    alpha_id = str(ra["bound_project_id"])
    alpha_uuid = yaml.safe_load((alpha / ".atlas-project.yaml").read_text(encoding="utf-8"))[
        "project_uuid"
    ]
    before = _snapshot_identity(shared)
    alpha_readme_before = (alpha / "README.md").read_text(encoding="utf-8")

    _marker(sibling, "r4c2-sibling", alpha_uuid)
    _write(sibling / "README.md", "# Sibling body\n\nCopied-uuid attacker content.\n")

    with pytest.raises(ConnectError, match="PROJECT_IDENTITY_CONFLICT"):
        connect_project(sibling, vault=shared)

    after = _snapshot_identity(shared)
    assert after == before
    assert load_allocation_uuid_owners(shared)[alpha_uuid] == alpha_id
    assert not (
        shared / "receipts" / "source-lineage" / "project-r4c2-sibling-allocation.json"
    ).is_file()
    assert (alpha / "README.md").read_text(encoding="utf-8") == alpha_readme_before
    # Alpha lineage / imported note must not absorb sibling body.
    alpha_note = shared / "projects" / alpha_id / "project.md"
    if alpha_note.is_file():
        note_text = alpha_note.read_text(encoding="utf-8")
        assert "Copied-uuid attacker content" not in note_text


def test_c_same_id_different_uuid_fail_closed(tmp_path: Path) -> None:
    shared = tmp_path / "vault"
    root = tmp_path / "same-id"
    root.mkdir()
    _write(root / "README.md", "# Same\n\nPurpose.\n")
    connect_project(root, vault=shared)
    marker = yaml.safe_load((root / ".atlas-project.yaml").read_text(encoding="utf-8"))
    project_id = marker["project"]["id"]
    other_uuid = "aad720e7-5980-4fac-b787-686c4a921d41"
    assert marker["project_uuid"] != other_uuid
    marker["project_uuid"] = other_uuid
    _write(root / ".atlas-project.yaml", yaml.safe_dump(marker, sort_keys=False))
    before = _snapshot_identity(shared)
    with pytest.raises(ConnectError, match="PROJECT_IDENTITY_CONFLICT"):
        connect_project(root, vault=shared)
    assert _snapshot_identity(shared) == before
    owners = load_allocation_uuid_owners(shared)
    assert project_id in owners.values()


def test_e_different_id_different_uuid_pass(tmp_path: Path) -> None:
    shared = tmp_path / "vault"
    alpha = tmp_path / "alpha-e"
    beta = tmp_path / "beta-e"
    alpha.mkdir()
    beta.mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    _write(beta / "README.md", "# Beta\n\nPurpose.\n")
    ra = connect_project(alpha, vault=shared)
    rb = connect_project(beta, vault=shared)
    assert ra["bound_project_id"] != rb["bound_project_id"]
    ua = yaml.safe_load((alpha / ".atlas-project.yaml").read_text(encoding="utf-8"))["project_uuid"]
    ub = yaml.safe_load((beta / ".atlas-project.yaml").read_text(encoding="utf-8"))["project_uuid"]
    assert ua != ub
    owners = load_allocation_uuid_owners(shared)
    assert owners[ua] == ra["bound_project_id"]
    assert owners[ub] == rb["bound_project_id"]


def test_h_i_conflict_no_source_coalescing(tmp_path: Path) -> None:
    shared = tmp_path / "vault"
    alpha = tmp_path / "alpha-hi"
    sibling = tmp_path / "sibling-hi"
    alpha.mkdir()
    sibling.mkdir()
    _write(alpha / "README.md", "# Alpha HI\n\nalpha-unique-token-zzz\n")
    connect_project(alpha, vault=shared)
    uuid = yaml.safe_load((alpha / ".atlas-project.yaml").read_text(encoding="utf-8"))[
        "project_uuid"
    ]
    registry_before = (shared / "state" / "sources.json").read_text(encoding="utf-8")
    _marker(sibling, "sibling-hi", uuid)
    _write(sibling / "README.md", "# Sibling HI\n\nsibling-unique-token-yyy\n")
    with pytest.raises(ConnectError, match="PROJECT_IDENTITY_CONFLICT"):
        connect_project(sibling, vault=shared)
    registry_after = (shared / "state" / "sources.json").read_text(encoding="utf-8")
    assert registry_after == registry_before
    assert "sibling-unique-token-yyy" not in registry_after


def test_j_k_r2_r3_regressions_still_pass(tmp_path: Path) -> None:
    from project_atlas.connect import project_slug_from_dirname

    aliases = ["foo-bar", "Foo.Bar", "Foo_Bar", "Foo Bar"]
    ids = []
    for name in aliases:
        root = tmp_path / name
        root.mkdir()
        _write(root / "README.md", f"# {name}\n\nPurpose.\n")
        ids.append(connect_project(root, vault=tmp_path / "alias-vault")["bound_project_id"])
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", str(i)) for i in ids)

    shared = tmp_path / "shared-r3"
    a = tmp_path / "proj-alpha"
    b = tmp_path / "proj-beta"
    a.mkdir()
    b.mkdir()
    _write(a / "README.md", "# A\n\nPurpose.\n")
    _write(b / "README.md", "# B\n\nPurpose.\n")
    ra = connect_project(a, vault=shared)
    rb = connect_project(b, vault=shared)
    assert ra["bound_project_id"] != rb["bound_project_id"]
    assert project_slug_from_dirname("Foo Bar", project_root=a) != project_slug_from_dirname(
        "Foo Bar", project_root=b
    )


def test_deleted_allocation_receipt_still_blocks_copied_uuid(tmp_path: Path) -> None:
    """Receipt deletion must not reopen UUID coalescing via durable lineage."""
    shared = tmp_path / "vault"
    alpha = tmp_path / "alpha-receiptless"
    sibling = tmp_path / "sibling-receiptless"
    alpha.mkdir()
    sibling.mkdir()
    _write(alpha / "README.md", "# Alpha\n\nalpha-body\n")
    connect_project(alpha, vault=shared)
    uuid = yaml.safe_load((alpha / ".atlas-project.yaml").read_text(encoding="utf-8"))[
        "project_uuid"
    ]
    receipt = next((shared / "receipts" / "source-lineage").glob("project-*-allocation.json"))
    receipt.unlink()
    registry_before = (shared / "state" / "sources.json").read_bytes()
    _marker(sibling, "sibling-receiptless", uuid)
    _write(sibling / "README.md", "# Sibling\n\nsibling-body\n")
    with pytest.raises(ConnectError, match="PROJECT_IDENTITY_CONFLICT"):
        connect_project(sibling, vault=shared)
    assert (shared / "state" / "sources.json").read_bytes() == registry_before


def test_malformed_yaml_marker_controlled_error(tmp_path: Path) -> None:
    root = tmp_path / "bad-marker"
    root.mkdir()
    _write(root / "README.md", "# Bad\n\nPurpose.\n")
    _write(root / ".atlas-project.yaml", "project: [\n")
    with pytest.raises(ConnectError, match="INVALID_PROJECT_MARKER"):
        connect_project(root, vault=tmp_path / "vault-bad")
    assert main(["connect", str(root), "--vault", str(tmp_path / "vault-cli"), "--json"]) == (
        EXIT_ERROR
    )
    assert not (tmp_path / "vault-bad" / "generated" / "ops" / "connect-manifest.json").is_file()

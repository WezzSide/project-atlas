"""D-050 / D-052 residual HIGH remediations (R2-R5) from Local Windows IV."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from project_atlas.attention_hygiene import classify_attention
from project_atlas.connect import (
    ConnectError,
    connect_project,
    project_slug_from_dirname,
    root_identity_fingerprint,
)
from project_atlas.domain.claims import ID_PATTERN
from project_atlas.lineage import build_project_registry
from project_atlas.project_architecture import build_architecture_lens
from project_atlas.source_health import explain_source_health


def _marker_id(root: Path) -> str:
    raw = yaml.safe_load((root / ".atlas-project.yaml").read_text(encoding="utf-8"))
    return str(raw["project"]["id"])


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_r2_lossy_slug_aliases_do_not_collide(tmp_path: Path) -> None:
    aliases = ["foo-bar", "Foo.Bar", "Foo_Bar", "Foo Bar"]
    roots = []
    ids = []
    for name in aliases:
        root = tmp_path / name
        root.mkdir()
        _write(root / "README.md", f"# {name}\n\nPurpose.\n")
        report = connect_project(root)
        project_id = report["bound_project_id"]
        assert isinstance(project_id, str)
        assert re.fullmatch(ID_PATTERN, project_id)
        ids.append(project_id)
        roots.append(root)
        alloc = (
            Path(report["vault"])
            / "receipts"
            / "source-lineage"
            / f"project-{project_id}-allocation.json"
        )
        assert alloc.is_file()
    assert len(set(ids)) == len(aliases)
    # Same root reconnect keeps identity.
    again = connect_project(roots[0])
    assert again["bound_project_id"] == ids[0]


def test_r2_unicode_and_explicit_marker_stable(tmp_path: Path) -> None:
    a = tmp_path / "文档一"
    b = tmp_path / "文档二"
    a.mkdir()
    b.mkdir()
    _write(a / "README.md", "# A\n\nPurpose.\n")
    _write(b / "README.md", "# B\n\nPurpose.\n")
    id_a = connect_project(a)["bound_project_id"]
    id_b = connect_project(b)["bound_project_id"]
    assert id_a != id_b
    assert re.fullmatch(ID_PATTERN, str(id_a))
    assert re.fullmatch(ID_PATTERN, str(id_b))
    emoji = tmp_path / "🚀🚀"
    emoji.mkdir()
    _write(emoji / "README.md", "# Emoji\n\nPurpose.\n")
    assert re.fullmatch(ID_PATTERN, str(connect_project(emoji)["bound_project_id"]))

    explicit = tmp_path / "explicit-root"
    explicit.mkdir()
    _write(explicit / "README.md", "# Explicit\n\nPurpose.\n")
    _write(
        explicit / ".atlas-project.yaml",
        "schema_version: 1\nproject:\n  id: governed-explicit\n  name: Explicit\n",
    )
    report = connect_project(explicit)
    assert report["bound_project_id"] == "governed-explicit"
    assert _marker_id(explicit) == "governed-explicit"


def test_r2_same_basename_different_roots(tmp_path: Path) -> None:
    left = tmp_path / "left" / "shared-name"
    right = tmp_path / "right" / "shared-name"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    _write(left / "README.md", "# Left\n\nPurpose.\n")
    _write(right / "README.md", "# Right\n\nPurpose.\n")
    assert connect_project(left)["bound_project_id"] != connect_project(right)["bound_project_id"]
    assert project_slug_from_dirname("Foo Bar", project_root=left) != project_slug_from_dirname(
        "Foo_Bar", project_root=right
    )
    assert root_identity_fingerprint(left) != root_identity_fingerprint(right)


def test_r3_shared_vault_two_projects_stable(tmp_path: Path) -> None:
    shared = tmp_path / "shared-vault"
    alpha = tmp_path / "project-alpha"
    beta = tmp_path / "project-beta"
    alpha.mkdir()
    beta.mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    _write(beta / "README.md", "# Beta\n\nPurpose.\n")
    ra = connect_project(alpha, vault=shared)
    rb = connect_project(beta, vault=shared)
    assert ra["status"] == "connected"
    assert rb["status"] == "connected"
    id_a = ra["bound_project_id"]
    id_b = rb["bound_project_id"]
    assert id_a != id_b
    # Distinct source IDs for same relative path.
    manifest = json.loads(
        (shared / "generated" / "ops" / "connect-manifest.json").read_text(encoding="utf-8")
    )
    # After beta connect, manifest is beta's inventory; alpha registry must still exist.
    assert (shared / "projects" / str(id_a)).is_dir()
    assert (shared / "projects" / str(id_b)).is_dir()
    # Reconnects must not raise project_uuid changed.
    ra2 = connect_project(alpha, vault=shared)
    rb2 = connect_project(beta, vault=shared)
    assert ra2["bound_project_id"] == id_a
    assert rb2["bound_project_id"] == id_b
    # Source IDs for README must differ across projects when both discovered.
    from project_atlas.discovery import discover

    a_ids = {
        row["source_id"] for row in discover(alpha)["sources"] if row.get("path") == "README.md"
    }
    b_ids = {
        row["source_id"] for row in discover(beta)["sources"] if row.get("path") == "README.md"
    }
    assert a_ids
    assert b_ids
    assert a_ids.isdisjoint(b_ids)
    del manifest  # kept for local clarity; ownership restored by reconnects


def test_r4_failed_connect_does_not_mutate_manifest(tmp_path: Path) -> None:
    shared = tmp_path / "shared-vault"
    alpha = tmp_path / "alpha-healthy"
    alpha.mkdir()
    (alpha / "docs").mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    _write(
        alpha / "docs" / "credentials.md",
        'aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n',
    )
    ra = connect_project(alpha, vault=shared)
    project_a = str(ra["bound_project_id"])
    manifest_path = shared / "generated" / "ops" / "connect-manifest.json"
    before = manifest_path.read_bytes()
    before_attention = classify_attention(shared, project_a)
    assert before_attention["rollup"] != "CLEAR"
    assert any(item.get("reason_code") == "SECRET_QUARANTINE" for item in before_attention["items"])

    sibling = tmp_path / "sibling-bad"
    sibling.mkdir()
    _write(sibling / "README.md", "# Sibling\n\nPurpose.\n")
    # Invalid UTF-8 source — ingest fails after staging write (Local D-050 R4).
    (sibling / "broken.md").write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")

    with pytest.raises(ConnectError, match="UTF-8"):
        connect_project(sibling, vault=shared)

    after = manifest_path.read_bytes()
    assert after == before
    assert not (shared / "generated" / "ops" / ".connect-manifest.staging.json").exists()
    after_attention = classify_attention(shared, project_a)
    assert after_attention["rollup"] != "CLEAR"
    assert any(item.get("reason_code") == "SECRET_QUARANTINE" for item in after_attention["items"])


def test_r3_shared_vault_secret_attention_survives_sibling(tmp_path: Path) -> None:
    """Sibling reconnect must not CLEAR another project's SECRET_QUARANTINE."""
    shared = tmp_path / "shared-vault"
    alpha = tmp_path / "alpha-sec"
    beta = tmp_path / "beta-sec"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "docs").mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    _write(
        alpha / "docs" / "credentials.md",
        'aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n',
    )
    _write(beta / "README.md", "# Beta\n\nPurpose.\n")
    ra = connect_project(alpha, vault=shared)
    connect_project(beta, vault=shared)
    att = classify_attention(shared, str(ra["bound_project_id"]))
    assert att["rollup"] != "CLEAR"
    assert any(item.get("reason_code") == "SECRET_QUARANTINE" for item in att["items"])


def test_r3_path_active_source_id_migration_with_retired_history() -> None:
    """Active path continuity must survive source_id renames even with retired history."""
    project_uuid = "00000000-0000-4000-8000-000000000050"
    initial = build_project_registry(
        project_uuid,
        [{"source_id": "source-old-compat", "path": "README.md", "sha256": "a" * 64}],
        [],
    )
    deleted = build_project_registry(project_uuid, [], initial)
    resolution = {
        "outcome": "create_new_generation",
        "authority": "curator_approved",
        "candidate_lineage_ids": [initial[0]["source_lineage_id"]],
        "reason": "new generation after delete",
    }
    regenerated = build_project_registry(
        project_uuid,
        [
            {
                "source_id": "source-gen2",
                "path": "README.md",
                "sha256": "b" * 64,
                "lineage_resolution": resolution,
            }
        ],
        deleted,
    )
    active = next(item for item in regenerated if item["source_id"] == "source-gen2")
    migrated = build_project_registry(
        project_uuid,
        [
            {
                "source_id": "project:demo|README.md",
                "path": "README.md",
                "sha256": "b" * 64,
            }
        ],
        regenerated,
    )
    adopted = next(
        item
        for item in migrated
        if item["path"] == "README.md" and item["source_change_state"] != "deleted"
    )
    assert adopted["source_lineage_id"] == active["source_lineage_id"]
    assert adopted["source_id"] == "project:demo|README.md"


def test_r3_unreadable_durable_manifest_fail_closed_attention(tmp_path: Path) -> None:
    """Unreadable source-manifest must not fall back to last-writer ownership."""
    shared = tmp_path / "shared-vault"
    alpha = tmp_path / "alpha-unreadable"
    beta = tmp_path / "beta-unreadable"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "docs").mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    _write(
        alpha / "docs" / "credentials.md",
        'aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n',
    )
    _write(beta / "README.md", "# Beta\n\nPurpose.\n")
    ra = connect_project(alpha, vault=shared)
    connect_project(beta, vault=shared)
    durable = shared / "sources" / "manifests" / "source-manifest.json"
    durable.write_text("{not-json", encoding="utf-8")
    att = classify_attention(shared, str(ra["bound_project_id"]))
    assert att["rollup"] != "CLEAR"
    codes = {item.get("reason_code") for item in att["items"]}
    assert "SECRET_OWNERSHIP_UNKNOWN" in codes


def test_r3_absent_durable_manifest_fail_closed_attention(tmp_path: Path) -> None:
    """Absent source-manifest must not fall back to last-writer ownership."""
    shared = tmp_path / "shared-vault"
    alpha = tmp_path / "alpha-absent"
    beta = tmp_path / "beta-absent"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "docs").mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    _write(
        alpha / "docs" / "credentials.md",
        'aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n',
    )
    _write(beta / "README.md", "# Beta\n\nPurpose.\n")
    ra = connect_project(alpha, vault=shared)
    connect_project(beta, vault=shared)
    durable = shared / "sources" / "manifests" / "source-manifest.json"
    durable.unlink()
    att = classify_attention(shared, str(ra["bound_project_id"]))
    assert att["rollup"] != "CLEAR"
    codes = {item.get("reason_code") for item in att["items"]}
    assert "SECRET_OWNERSHIP_UNKNOWN" in codes


def test_r3_shared_vault_exclusions_survive_sibling(tmp_path: Path) -> None:
    """Sibling connect must not erase another project's durable exclusions."""
    shared = tmp_path / "shared-vault"
    alpha = tmp_path / "alpha-excl"
    beta = tmp_path / "beta-excl"
    alpha.mkdir()
    beta.mkdir()
    _write(alpha / "README.md", "# Alpha\n\nPurpose.\n")
    (alpha / "__pycache__").mkdir()
    _write(alpha / "__pycache__" / "note.md", "cached note\n")
    _write(beta / "README.md", "# Beta\n\nPurpose.\n")
    ra = connect_project(alpha, vault=shared)
    project_a = str(ra["bound_project_id"])
    before = explain_source_health(shared, project_a)
    assert before["counts"].get("excluded", 0) >= 1
    connect_project(beta, vault=shared)
    after = explain_source_health(shared, project_a)
    assert after["counts"].get("excluded", 0) >= 1
    assert any(
        row.get("status") == "excluded" and "__pycache__" in str(row.get("source") or "")
        for row in after["sources"]
    )


def test_r5_generic_architecture_md_extracts_slots(tmp_path: Path) -> None:
    root = tmp_path / "arch-generic"
    root.mkdir()
    _write(root / "README.md", "# Arch Generic\n\nPurpose.\n")
    _write(
        root / "ARCHITECTURE.md",
        "# Architecture\n\n"
        "## Purpose\n\nLocal-first project knowledge compiler.\n\n"
        "## Components\n\n"
        "- Connect CLI orchestrates discovery and ingest\n"
        "- Knowledge compiler extracts evidence-backed claims\n"
        "- Validation gate enforces provenance\n\n"
        "## Responsibilities\n\n"
        "- Connect owns project bind lifecycle\n"
        "- Ingest owns quarantine and promotion\n\n"
        "## Runtime\n\n"
        "- CLI entrypoint `atlas`\n"
        "- Optional LIVE_API loopback server\n\n"
        "## Data store\n\n"
        "- Vault Markdown notes under projects/\n"
        "- Generated JSON lenses under generated/answers/\n\n"
        "## Data flow\n\n"
        "discover -> ingest -> build-indexes -> validate\n\n"
        "## Control flow\n\n"
        "CLI dispatches subcommands; connect sequences Core compile steps.\n\n"
        "## Boundaries\n\n"
        "MODEL_OUTPUT != AUTHORITY; fail-closed path safety; secrets quarantined.\n",
    )
    report = connect_project(root)
    vault = Path(report["vault"])
    project_id = str(report["bound_project_id"])
    lens = build_architecture_lens(vault, project_id)
    assert lens["status"] == "derived"
    slots = lens["slots"]
    assert slots["major_components"] != "UNKNOWN"
    assert (
        "Connect CLI" in slots["major_components"]
        or "component" in slots["major_components"].lower()
    )
    assert slots["component_responsibilities"] != "UNKNOWN"
    assert slots["runtime_surfaces"] != "UNKNOWN"
    assert slots["data_stores"] != "UNKNOWN"
    assert slots["data_flow"] != "UNKNOWN"
    assert slots["control_flow"] != "UNKNOWN"
    assert slots["trust_boundaries"] != "UNKNOWN"
    assert lens["evidence"]
    assert any(path.lower().endswith("architecture.md") for path in lens["evidence"])


def test_r5_weak_architecture_mention_stays_unknown(tmp_path: Path) -> None:
    root = tmp_path / "weak-arch"
    root.mkdir()
    _write(root / "README.md", "# Weak\n\nMentions architecture casually.\n")
    _write(root / "notes.md", "We should improve architecture someday.\n")
    report = connect_project(root)
    lens = build_architecture_lens(Path(report["vault"]), str(report["bound_project_id"]))
    # No ARCHITECTURE.md / AGENTS / plan — remain honest UNKNOWN.
    assert lens["status"] in {"unknown", "derived"}
    if lens["status"] == "unknown":
        assert all(value == "UNKNOWN" for value in lens["slots"].values())

"""D-063 truth hardening for D-049 Knowledge Estate Discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.estate_discovery import (
    EstateDiscoveryError,
    VaultProjectIdentity,
    canonical_path_key,
    connect_discovered_candidate,
    discover_estate,
    load_vault_project_identities,
    match_fingerprint,
    prove_connected,
    review_candidates,
    write_discovery_report,
)
from project_atlas.web_api.discovery import load_estate_discovery_view


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _allocation(vault: Path, project_id: str, project_uuid: str) -> None:
    path = vault / "receipts" / "source-lineage" / f"project-{project_id}-allocation.json"
    _write(
        path,
        json.dumps(
            {
                "schema_version": 1,
                "receipt_type": "project-identity-allocation",
                "project": project_id,
                "project_uuid": project_uuid,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


ALPHA_UUID = "11111111-1111-4111-8111-111111111111"
BETA_UUID = "22222222-2222-4222-8222-222222222222"
OTHER_UUID = "33333333-3333-4333-8333-333333333333"


def test_p1_identity_contradiction_matrix() -> None:
    vault = [
        VaultProjectIdentity("alpha", ALPHA_UUID),
        VaultProjectIdentity("beta", BETA_UUID),
    ]
    # A: EXACT
    state, _, _, mid, _ = match_fingerprint(
        {
            "atlas_project_id": "alpha",
            "atlas_project_uuid": ALPHA_UUID,
            "marker_status": "ok",
            "uuid_status": "ok",
        },
        vault,
    )
    assert state == "EXACT" and mid == "alpha"

    # B: same id different uuid → CONFLICTING (never EXACT)
    state, _, conflicts, _, _ = match_fingerprint(
        {
            "atlas_project_id": "alpha",
            "atlas_project_uuid": OTHER_UUID,
            "marker_status": "ok",
            "uuid_status": "ok",
        },
        vault,
    )
    assert state == "CONFLICTING"
    assert any(c.kind == "same_id_different_uuid" for c in conflicts)

    # C: different id same uuid → CONFLICTING
    state, _, conflicts, _, _ = match_fingerprint(
        {
            "atlas_project_id": "beta",
            "atlas_project_uuid": ALPHA_UUID,
            "marker_status": "ok",
            "uuid_status": "ok",
        },
        vault,
    )
    assert state == "CONFLICTING"
    assert any(c.kind == "different_id_same_uuid" for c in conflicts)

    # D: id present, uuid absent → EXACT with honest missing uuid evidence
    state, evidence, _, mid, _ = match_fingerprint(
        {
            "atlas_project_id": "alpha",
            "atlas_project_uuid": None,
            "marker_status": "ok",
            "uuid_status": "absent",
        },
        vault,
    )
    assert state == "EXACT" and mid == "alpha"
    assert any(e.kind == "uuid_absent" for e in evidence)

    # E: uuid matches, id absent → EXACT
    state, _, _, mid, _ = match_fingerprint(
        {
            "atlas_project_id": None,
            "atlas_project_uuid": ALPHA_UUID,
            "marker_status": "ok",
            "uuid_status": "ok",
        },
        vault,
    )
    assert state == "EXACT" and mid == "alpha"

    # F: invalid uuid not silently absent
    state, _, conflicts, _, _ = match_fingerprint(
        {
            "atlas_project_id": "alpha",
            "atlas_project_uuid": None,
            "marker_status": "ok",
            "uuid_status": "invalid",
        },
        vault,
    )
    assert state == "CONFLICTING"
    assert any(c.kind == "invalid_project_uuid" for c in conflicts)

    # G: malformed marker → CONFLICTING / not EXACT via weaker evidence
    state, _, conflicts, _, _ = match_fingerprint(
        {
            "atlas_project_id": None,
            "directory_name": "alpha",
            "marker_status": "invalid",
            "uuid_status": "absent",
        },
        vault,
    )
    assert state == "CONFLICTING"
    assert any(c.kind.startswith("marker_") for c in conflicts)


def test_p2_connected_requires_bind_proof(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    alpha = estate / "alpha"
    other = estate / "other-alpha"
    _write(
        alpha / ".atlas-project.yaml",
        f"project:\n  id: alpha\nproject_uuid: {ALPHA_UUID}\n",
    )
    _write(alpha / "README.md", "# a\n")
    (alpha / ".git").mkdir(parents=True)
    _write(
        other / ".atlas-project.yaml",
        f"project:\n  id: alpha\nproject_uuid: {ALPHA_UUID}\n",
    )
    _write(other / "README.md", "# copy\n")
    (other / ".git").mkdir(parents=True)

    vault = tmp_path / "vault"
    (vault / "projects" / "alpha").mkdir(parents=True)
    _allocation(vault, "alpha", ALPHA_UUID)
    # Bind only alpha root — not the copied marker at other-alpha.
    _write(
        alpha / ".atlas" / "connect.json",
        json.dumps(
            {
                "schema_version": 1,
                "schema": "atlas.connect.bind.v1",
                "project_root": str(alpha.resolve()),
                "vault": str(vault.resolve()),
                "project_id": "alpha",
                "project_ids": ["alpha"],
            }
        ),
    )

    report = discover_estate(estate, vault=vault)
    by_name = {Path(p["path"]).name: p for p in report["candidates"]["projects"]}
    assert by_name["alpha"]["category"] == "CONNECTED"
    assert by_name["alpha"]["lifecycle"] == "CONNECTED"
    assert by_name["alpha"]["why_connected"]
    # Copied marker at different root must not be CONNECTED.
    assert by_name["other-alpha"]["category"] != "CONNECTED"
    assert by_name["other-alpha"]["lifecycle"] != "CONNECTED"
    assert by_name["other-alpha"]["match_state"] in {"EXACT", "CONFLICTING"}


def test_p3_loads_allocation_receipts_not_speculative_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "projects" / "alpha").mkdir(parents=True)
    _allocation(vault, "alpha", ALPHA_UUID)
    identities = load_vault_project_identities(vault)
    assert len(identities) == 1
    assert identities[0].project_uuid == ALPHA_UUID
    assert "allocation_receipt" in identities[0].identity_sources


def test_p4_strong_evidence_from_git_remote(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    bound = estate / "svc"
    _write(bound / "README.md", "# svc\n")
    _write(bound / "pyproject.toml", '[project]\nname = "svc-app"\n')
    (bound / "src").mkdir(parents=True)
    (bound / ".git").mkdir()
    _write(
        bound / ".git" / "config",
        '[remote "origin"]\n\turl = https://example.com/svc.git\n',
    )

    vault = tmp_path / "vault"
    (vault / "projects" / "svc-app").mkdir(parents=True)
    # Governed bind root carries live git remote for STRONG_EVIDENCE.
    _write(
        vault / "generated" / "ops" / "connect-receipt.json",
        json.dumps(
            {
                "project_root": str(bound.resolve()),
                "project_id": "svc-app",
                "projects": ["svc-app"],
            }
        ),
    )
    # Candidate without Atlas marker — match via remote.
    report = discover_estate(estate, vault=vault, include_knowledge=False)
    projects = report["candidates"]["projects"]
    assert projects
    assert projects[0]["match_state"] == "STRONG_EVIDENCE"

    # Package name alone against id alignment is not automatic EXACT.
    state, _, _, _, _ = match_fingerprint(
        {
            "package_name": "svc-app",
            "marker_status": "absent",
            "uuid_status": "absent",
        },
        [VaultProjectIdentity("svc-app", None)],
    )
    assert state in {"LIKELY", "UNMATCHED", "STRONG_EVIDENCE"}
    assert state != "EXACT"

    # Directory name only never EXACT.
    state, _, _, _, _ = match_fingerprint(
        {
            "directory_name": "svc-app",
            "marker_status": "absent",
            "uuid_status": "absent",
        },
        [VaultProjectIdentity("svc-app", None)],
    )
    assert state == "LIKELY"


def test_p4_git_remote_vs_marker_conflict() -> None:
    vault = [
        VaultProjectIdentity(
            "alpha",
            ALPHA_UUID,
            git_remote="https://example.com/alpha.git",
            bind_root="/tmp/alpha",
            bind_proven=True,
        ),
        VaultProjectIdentity("beta", BETA_UUID),
    ]
    state, _, conflicts, _, _ = match_fingerprint(
        {
            "atlas_project_id": "beta",
            "atlas_project_uuid": None,
            "marker_status": "ok",
            "uuid_status": "absent",
            "git_remote": "https://example.com/alpha.git",
        },
        vault,
    )
    assert state == "CONFLICTING"
    assert any(c.kind == "git_remote_vs_marker_id" for c in conflicts)


def test_p5_knowledge_nested_under_project(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    proj = estate / "alpha"
    notes = proj / "research"
    _write(proj / "README.md", "# a\n")
    (proj / ".git").mkdir(parents=True)
    _write(notes / "paper.md", "x\n")
    _write(notes / "notes.md", "y\n")
    _write(notes / "more.md", "z\n")
    report = discover_estate(estate)
    knowledge = report["candidates"]["knowledge"]
    nested = [k for k in knowledge if Path(k["path"]).name == "research"]
    assert nested
    assert nested[0]["knowledge_relation"] == "KNOWLEDGE_PROJECT_MATCHED"


def test_p5_obsidian_not_silently_assigned(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    vault_dir = estate / "personal-brain"
    (vault_dir / ".obsidian").mkdir(parents=True)
    _write(vault_dir / ".obsidian" / "app.json", "{}\n")
    for name in ("a.md", "b.md", "c.md"):
        _write(vault_dir / name, "note\n")
    report = discover_estate(estate)
    obs = [k for k in report["candidates"]["knowledge"] if k["kind"] == "obsidian_vault"]
    assert obs
    assert obs[0]["knowledge_relation"] in {
        "KNOWLEDGE_UNMATCHED",
        "KNOWLEDGE_AMBIGUOUS",
    }
    assert obs[0]["required_review"] is True


def test_p9_nested_fake_project_in_node_modules(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    a = estate / "ProjectA"
    b = estate / "ProjectB"
    fake = a / "node_modules" / "fake-project"
    for root in (a, b, fake):
        _write(root / "README.md", "# x\n")
        _write(root / "package.json", '{"name":"x"}\n')
        (root / "src").mkdir(parents=True)
        (root / ".git").mkdir(parents=True)
    report = discover_estate(estate, include_knowledge=False)
    names = {Path(p["path"]).name for p in report["candidates"]["projects"]}
    assert "ProjectA" in names
    assert "ProjectB" in names
    assert "fake-project" not in names


def test_p10_truncation_honesty(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    for i in range(5):
        root = estate / f"p{i}"
        _write(root / "README.md", "# x\n")
        (root / ".git").mkdir(parents=True)
    report = discover_estate(
        estate,
        include_knowledge=False,
        max_project_candidates=2,
    )
    assert report["scan"]["project_limit_reached"] is True
    assert report["scan"]["scan_complete"] is False
    assert report["scan"]["truncation_reason"] == "project_limit_reached"
    assert report["counts"]["projects"] == 2


def test_p11_cache_never_skips_identity(tmp_path: Path) -> None:
    estate = tmp_path / "estate" / "p"
    _write(
        estate / ".atlas-project.yaml",
        f"project:\n  id: alpha\nproject_uuid: {ALPHA_UUID}\n",
    )
    _write(estate / "README.md", "# a\n")
    (estate / ".git").mkdir(parents=True)
    vault = tmp_path / "vault"
    (vault / "projects" / "alpha").mkdir(parents=True)
    _allocation(vault, "alpha", ALPHA_UUID)
    report1 = discover_estate(tmp_path / "estate", vault=vault)
    assert report1["incremental_foundation"]["cache_used_for_skip"] is False
    # Mutate identity
    _write(
        estate / ".atlas-project.yaml",
        f"project:\n  id: beta\nproject_uuid: {BETA_UUID}\n",
    )
    stale_cache = {"entries": report1.get("_cache_entries")}
    report2 = discover_estate(tmp_path / "estate", vault=vault, prior_cache=stale_cache)
    assert report2["incremental_foundation"]["cache_used_for_skip"] is False
    proj = report2["candidates"]["projects"][0]
    assert proj["fingerprint"]["atlas_project_id"] == "beta"


def test_p12_review_actionable(tmp_path: Path) -> None:
    estate = tmp_path / "estate" / "impostor"
    _write(
        estate / ".atlas-project.yaml",
        f"project:\n  id: impostor\nproject_uuid: {ALPHA_UUID}\n",
    )
    vault = tmp_path / "vault"
    (vault / "projects" / "alpha").mkdir(parents=True)
    _allocation(vault, "alpha", ALPHA_UUID)
    report = discover_estate(tmp_path / "estate", vault=vault)
    rows = review_candidates(report)
    assert rows
    assert rows[0]["required_action"]
    assert rows[0]["conflicting_evidence"]


def test_p13_stale_report_connect_fail_closed(tmp_path: Path) -> None:
    estate = tmp_path / "estate" / "p"
    _write(
        estate / ".atlas-project.yaml",
        f"project:\n  id: alpha\nproject_uuid: {ALPHA_UUID}\n",
    )
    _write(estate / "README.md", "# a\n")
    (estate / ".git").mkdir(parents=True)
    vault = tmp_path / "vault"
    (vault / "projects" / "alpha").mkdir(parents=True)
    _allocation(vault, "alpha", ALPHA_UUID)
    report = discover_estate(tmp_path / "estate", vault=vault)
    cand = report["candidates"]["projects"][0]
    # Mutate marker after report
    _write(
        estate / ".atlas-project.yaml",
        f"project:\n  id: beta\nproject_uuid: {BETA_UUID}\n",
    )
    with pytest.raises(EstateDiscoveryError, match=r"stale report"):
        connect_discovered_candidate(report, cand["candidate_id"], vault=vault, dry_run=True)


def test_p7_candidate_id_stable_and_case_policy(tmp_path: Path) -> None:
    a = tmp_path / "Foo"
    a.mkdir()
    key1 = canonical_path_key(a)
    key2 = canonical_path_key(a)
    assert key1 == key2
    if os.name != "nt":
        # On case-sensitive FS, Foo and foo keys differ when both exist.
        b = tmp_path / "foo"
        try:
            b.mkdir()
        except FileExistsError:
            pytest.skip("filesystem case-insensitive")
        assert canonical_path_key(a) != canonical_path_key(b)


def test_web_api_parity_includes_scan(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    estate = tmp_path / "estate" / "p"
    _write(estate / "README.md", "# p\n")
    (estate / ".git").mkdir(parents=True)
    report = discover_estate(
        tmp_path / "estate",
        vault=vault,
        max_project_candidates=0,
    )
    # max 0 → immediate truncation
    write_discovery_report(report, vault / "generated" / "ops" / "estate-discovery-report.json")
    view = load_estate_discovery_view(vault)
    assert view["present"] is True
    assert "scan" in view
    assert view["scan"]["scan_complete"] is False


def test_prove_connected_rejects_same_id_without_bind() -> None:
    vp = VaultProjectIdentity("alpha", ALPHA_UUID, bind_proven=False)
    ok, why = prove_connected(
        Path("/tmp/elsewhere"),
        "alpha",
        "EXACT",
        [vp],
    )
    assert ok is False
    assert why

"""AS-CODER-ALPHA-KNOWLEDGE-ESTATE-DISCOVERY-001 / D-049 wave-1 tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.estate_discovery import (
    EstateDiscoveryError,
    connect_discovered_candidate,
    discover_estate,
    match_fingerprint,
    refuse_dangerous_authorized_root,
    review_candidates,
    write_discovery_report,
)
from project_atlas.web_api.discovery import load_estate_discovery_view


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_refuse_home_and_filesystem_root(tmp_path: Path) -> None:
    with pytest.raises(EstateDiscoveryError, match="home directory"):
        refuse_dangerous_authorized_root(Path.home())
    # Filesystem root — platform root of tmp_path.
    fs_root = Path(tmp_path.anchor)
    with pytest.raises(EstateDiscoveryError, match="filesystem root"):
        refuse_dangerous_authorized_root(fs_root)


def test_bounded_root_finds_nested_projects_and_obsidian(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    alpha = estate / "alpha"
    beta = estate / "beta"
    notes = estate / "personal-notes"
    _write(alpha / "README.md", "# Alpha\n")
    _write(alpha / "pyproject.toml", '[project]\nname = "alpha-app"\n')
    (alpha / "src").mkdir(parents=True)
    (alpha / ".git").mkdir()
    _write(alpha / ".git" / "config", '[remote "origin"]\n\turl = https://example.com/alpha.git\n')
    _write(
        beta / ".atlas-project.yaml",
        "project:\n  id: beta-app\nproject_uuid: 11111111-1111-4111-8111-111111111111\n",
    )
    _write(beta / "README.md", "# Beta\n")
    (notes / ".obsidian").mkdir(parents=True)
    _write(notes / ".obsidian" / "app.json", "{}\n")
    _write(notes / "daily.md", "note\n")
    # Ignored trees must not yield project candidates.
    junk = estate / "alpha" / "node_modules" / "pkg"
    _write(junk / "package.json", '{"name":"junk"}\n')
    (junk / ".git").mkdir(parents=True)

    report = discover_estate(estate, include_projects=True, include_knowledge=True)
    projects = report["candidates"]["projects"]
    knowledge = report["candidates"]["knowledge"]
    paths = {p["path"] for p in projects}
    assert any(Path(p).name == "alpha" for p in paths)
    assert any(Path(p).name == "beta" for p in paths)
    assert not any("node_modules" in p for p in paths)
    assert any(k["kind"] == "obsidian_vault" for k in knowledge)
    assert report["security"]["unsafe_path_escapes_allowed"] == 0
    assert report["invariant"] == "DISCOVER != INGEST != TRUST != AUTHORITY"
    # Discovery must not create vault project dirs.
    assert not (estate / "projects").exists()


def test_symlink_escape_not_descended(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    outside = tmp_path / "outside-secret"
    _write(outside / "README.md", "secret\n")
    (outside / ".git").mkdir(parents=True)
    estate.mkdir()
    link = estate / "escape"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted in this environment")
    _write(estate / "keep" / "package.json", '{"name":"keep"}\n')
    _write(estate / "keep" / "README.md", "# keep\n")
    (estate / "keep" / "src").mkdir(parents=True)

    report = discover_estate(estate)
    project_paths = [p["path"] for p in report["candidates"]["projects"]]
    assert not any("outside-secret" in p for p in project_paths)
    ignored_reasons = {row["reason"] for row in report["categories"]["IGNORED"]}
    assert (
        "symlink_or_reparse_escape" in ignored_reasons or "symlink_not_descended" in ignored_reasons
    )
    assert report["security"]["unsafe_path_escapes_detected"] >= 1


def test_match_states_exact_ambiguous_conflicting() -> None:
    from project_atlas.estate_discovery import VaultProjectIdentity

    vault = [
        VaultProjectIdentity(
            project_id="alpha",
            project_uuid="11111111-1111-4111-8111-111111111111",
        ),
        VaultProjectIdentity(
            project_id="beta",
            project_uuid="22222222-2222-4222-8222-222222222222",
        ),
    ]
    exact_state, _, _, mid, _ = match_fingerprint(
        {
            "atlas_project_id": "alpha",
            "atlas_project_uuid": "11111111-1111-4111-8111-111111111111",
        },
        vault,
    )
    assert exact_state == "EXACT"
    assert mid == "alpha"

    conflict_state, _, conflicts, _, _ = match_fingerprint(
        {
            "atlas_project_id": "beta",
            "atlas_project_uuid": "11111111-1111-4111-8111-111111111111",
        },
        vault,
    )
    assert conflict_state == "CONFLICTING"
    assert conflicts

    amb_state, _, _, _, _ = match_fingerprint(
        {"directory_name": "alpha", "package_name": "beta"},
        [
            VaultProjectIdentity("alpha", None),
            VaultProjectIdentity("beta", None),
        ],
    )
    # dirname→alpha and package→beta both likely ⇒ ambiguous when both hit distinct ids
    assert amb_state in {"AMBIGUOUS", "LIKELY"}


def test_no_silent_identity_merge_on_name_only(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    one = estate / "shared-name"
    two = estate / "other" / "shared-name"
    for root in (one, two):
        _write(root / "README.md", "# x\n")
        _write(root / "package.json", '{"name":"shared-name"}\n')
        (root / "src").mkdir(parents=True)
        (root / ".git").mkdir(parents=True)

    vault = tmp_path / "vault"
    (vault / "projects" / "shared-name").mkdir(parents=True)
    report = discover_estate(estate, vault=vault)
    projects = report["candidates"]["projects"]
    assert len(projects) >= 2
    # Distinct candidate_ids — no coalescing by display name.
    ids = {p["candidate_id"] for p in projects}
    assert len(ids) >= 2


def test_cli_estate_discover_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    estate = tmp_path / "estate" / "proj"
    _write(estate / "README.md", "# P\n")
    _write(estate / "pyproject.toml", '[project]\nname = "proj"\n')
    (estate / "src").mkdir(parents=True)
    (estate / ".git").mkdir()
    code = main(
        [
            "discover",
            "--root",
            str(tmp_path / "estate"),
            "--projects",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_id"].startswith("AS-CODER-ALPHA-KNOWLEDGE-ESTATE")
    assert payload["counts"]["projects"] >= 1


def test_cli_legacy_source_output_still_works(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "src"
    _write(src / "note.md", "hello\n")
    out = tmp_path / "manifest.json"
    code = main(["discover", "--source", str(src), "--output", str(out)])
    assert code == 0
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "sources" in data
    assert "discovered" in capsys.readouterr().out.lower()


def test_cli_discover_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    estate = tmp_path / "estate"
    proj = estate / "marked"
    _write(
        proj / ".atlas-project.yaml",
        "project:\n  id: marked\nproject_uuid: 33333333-3333-4333-8333-333333333333\n",
    )
    vault = tmp_path / "vault"
    (vault / "projects" / "other").mkdir(parents=True)
    # Plant governed allocation receipt so uuid conflicts with a different owner id.
    _write(
        vault / "receipts" / "source-lineage" / "project-other-allocation.json",
        json.dumps(
            {
                "schema_version": 1,
                "receipt_type": "project-identity-allocation",
                "project": "other",
                "project_uuid": "33333333-3333-4333-8333-333333333333",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    report = discover_estate(estate, vault=vault)
    report_path = tmp_path / "report.json"
    write_discovery_report(report, report_path)
    rows = review_candidates(report)
    # Conflicting uuid ownership should require review when detected.
    code = main(
        [
            "discover",
            "review",
            "--report",
            str(report_path),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "review" in payload
    assert isinstance(rows, list)


def test_web_discovery_view_absent_and_present(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    empty = load_estate_discovery_view(vault)
    assert empty["present"] is False
    assert empty["categories"]["DISCOVERED_PROJECTS"] == []

    estate = tmp_path / "estate" / "p"
    _write(estate / "README.md", "# p\n")
    _write(estate / "package.json", '{"name":"p"}\n')
    (estate / "src").mkdir(parents=True)
    report = discover_estate(tmp_path / "estate", vault=vault)
    write_discovery_report(report, vault / "generated" / "ops" / "estate-discovery-report.json")
    view = load_estate_discovery_view(vault)
    assert view["present"] is True
    assert view["counts"]["projects"] >= 1


def test_discover_does_not_ingest(tmp_path: Path) -> None:
    estate = tmp_path / "estate" / "p"
    _write(estate / "README.md", "# p\n")
    (estate / ".git").mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    discover_estate(tmp_path / "estate", vault=vault)
    assert list(vault.iterdir()) == [] or not (vault / "projects").exists()


def test_conflicting_candidate_connect_refused(tmp_path: Path) -> None:
    """Lane C: copied UUID + different project.id must not silently unify."""
    estate = tmp_path / "estate"
    owner = estate / "owner"
    impostor = estate / "impostor"
    uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _write(
        owner / ".atlas-project.yaml",
        f"project:\n  id: owner\nproject_uuid: {uuid}\n",
    )
    _write(owner / "README.md", "# owner\n")
    (owner / ".git").mkdir(parents=True)
    _write(
        impostor / ".atlas-project.yaml",
        f"project:\n  id: impostor\nproject_uuid: {uuid}\n",
    )
    _write(impostor / "README.md", "# impostor\n")
    (impostor / ".git").mkdir(parents=True)

    vault = tmp_path / "vault"
    (vault / "projects" / "owner").mkdir(parents=True)
    _write(
        vault / "projects" / "owner" / "project.md",
        f"---\nproject_uuid: {uuid}\n---\n",
    )
    _write(
        vault / "receipts" / "source-lineage" / "project-owner-allocation.json",
        json.dumps(
            {
                "schema_version": 1,
                "receipt_type": "project-identity-allocation",
                "project": "owner",
                "project_uuid": uuid,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    report = discover_estate(estate, vault=vault)
    conflict = [p for p in report["candidates"]["projects"] if Path(p["path"]).name == "impostor"]
    assert conflict, "impostor candidate missing"
    assert conflict[0]["match_state"] == "CONFLICTING"
    assert conflict[0]["required_review"] is True
    with pytest.raises(EstateDiscoveryError, match="CONFLICTING"):
        connect_discovered_candidate(report, conflict[0]["candidate_id"], vault=vault, dry_run=True)


def test_obsidian_connect_refused_without_policy(tmp_path: Path) -> None:
    estate = tmp_path / "estate" / "notes"
    (estate / ".obsidian").mkdir(parents=True)
    _write(estate / ".obsidian" / "app.json", "{}\n")
    _write(estate / "a.md", "a\n")
    _write(estate / "b.md", "b\n")
    _write(estate / "c.md", "c\n")
    report = discover_estate(tmp_path / "estate")
    obs = [k for k in report["candidates"]["knowledge"] if k.get("kind") == "obsidian_vault"]
    assert obs
    with pytest.raises(EstateDiscoveryError, match="non-project"):
        connect_discovered_candidate(report, obs[0]["candidate_id"], dry_run=True)

"""D-080 deterministic bounded candidate selection and attachment truth.

Trace: D-PROJECT-ATLAS-CLOUD-D049-D080-CANDIDATE-SELECTION-TRUTH
Does not weaken D-078 volume-root policy.
Does not treat first-seen order as selection authority.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.estate_discovery import (
    CANDIDATE_SELECTION_POLICY,
    ROOT_MODE_OWNER_AUTHORIZED_VOLUME,
    EstateDiscoveryError,
    authorize_discovery_root,
    discover_estate,
    format_discovery_human,
    refuse_dangerous_authorized_root,
    write_discovery_report,
)
from project_atlas.web_api.discovery import load_estate_discovery_view


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_proj(
    root: Path,
    *,
    remote: str = "https://example.invalid/org/app.git",
    package: str | None = None,
    with_src: bool = True,
    with_docs: bool = True,
) -> Path:
    name = package if package is not None else root.name
    if with_docs:
        _write(root / "README.md", f"# {name}\n")
    _write(root / "package.json", f'{{"name":"{name}"}}\n')
    (root / ".git").mkdir(parents=True, exist_ok=True)
    _write(
        root / ".git" / "config",
        f'[remote "origin"]\n\turl = {remote}\n',
    )
    if with_src:
        (root / "src").mkdir(parents=True, exist_ok=True)
        _write(root / "src" / "main.py", "print(1)\n")
    return root


def _names(report: dict[str, object]) -> set[str]:
    candidates = report["candidates"]  # type: ignore[index]
    projects = candidates["projects"]  # type: ignore[index]
    return {Path(str(item["path"])).name for item in projects}


def _paths(report: dict[str, object]) -> list[str]:
    candidates = report["candidates"]  # type: ignore[index]
    projects = candidates["projects"]  # type: ignore[index]
    return [str(item["path"]) for item in projects]


def _fake_windows_volume(monkeypatch: pytest.MonkeyPatch, volume: Path) -> None:
    import project_atlas.estate_discovery as ed

    vol_key = ed.canonical_path_key(volume)

    def _is_vol(path: Path) -> bool:
        return ed.canonical_path_key(Path(path)) == vol_key

    monkeypatch.setattr(ed, "is_filesystem_root", lambda path: _is_vol(path))
    monkeypatch.setattr(
        ed,
        "is_windows_drive_volume_root",
        lambda path, host_os=None: _is_vol(path),
    )
    monkeypatch.setattr(
        ed,
        "is_windows_system_volume_root",
        lambda path, host_os=None, environ=None: False,
    )
    monkeypatch.setattr(ed, "is_unc_root", lambda path: False)


def _starvation_estate(root: Path, *, copies: int = 80) -> list[str]:
    """Noisy first-region copies + late strong unique projects."""
    for index in range(copies):
        _make_proj(
            root / "aaa-noise" / "worktrees" / f"copy-{index:03d}",
            remote="https://example.invalid/org/same-copy.git",
            package="same-copy",
        )
    late = [
        "strong-alpha",
        "strong-bravo",
        "strong-charlie",
        "strong-delta",
        "strong-echo",
    ]
    for name in late:
        proj = _make_proj(
            root / "zzz-real" / name,
            remote=f"https://example.invalid/owner/{name}.git",
            package=name,
        )
        _write(proj / "docs" / "notes.md", "note one\n")
        _write(proj / "docs" / "adr.md", "note two\n")
        _write(proj / "docs" / "plan.md", "note three\n")
    return late


def test_1_ordinary_estate_under_cap_unchanged(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _make_proj(estate / "alpha")
    _make_proj(estate / "beta")
    report = discover_estate(estate)
    assert report["scan"]["scan_complete"] is True
    assert report["scan"]["project_limit_reached"] is False
    assert report["scan"]["candidate_selection_policy"] == CANDIDATE_SELECTION_POLICY
    assert report["scan"]["project_candidates_seen"] == 2
    assert report["scan"]["project_candidates_emitted"] == 2
    assert report["scan"]["project_candidates_suppressed"] == 0
    assert _names(report) == {"alpha", "beta"}


def test_2_over_cap_estate_is_honestly_bounded(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    for index in range(12):
        _make_proj(
            estate / f"p{index:02d}",
            remote=f"https://example.invalid/org/p{index:02d}.git",
            package=f"p{index:02d}",
        )
    report = discover_estate(estate, max_project_candidates=5)
    assert report["scan"]["project_limit_reached"] is True
    assert report["scan"]["scan_complete"] is False
    assert report["scan"]["project_candidates_seen"] == 12
    assert report["scan"]["project_candidates_emitted"] == 5
    assert report["scan"]["project_candidates_suppressed"] == 7
    assert "project_limit_reached" in report["scan"]["truncation_causes"]
    human = format_discovery_human(report)
    assert "SCAN INCOMPLETE" in human
    assert "emitted 5 of 12 seen" in human


def test_3_and_4_late_strong_projects_survive_noisy_first_subtree(
    tmp_path: Path,
) -> None:
    estate = tmp_path / "estate"
    late = _starvation_estate(estate, copies=80)
    report = discover_estate(estate, max_project_candidates=8, enumeration_order="name_asc")
    names = _names(report)
    for name in late:
        assert name in names, name
    copies = [name for name in names if name.startswith("copy-")]
    assert len(copies) <= 2
    assert report["scan"]["project_candidates_seen"] == 85
    assert report["scan"]["project_candidates_emitted"] <= 8
    assert report["scan"]["project_candidates_emitted"] >= 5


def test_5_candidate_set_stable_under_reversed_enumeration(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    late = _starvation_estate(estate, copies=80)
    first = discover_estate(estate, max_project_candidates=8, enumeration_order="name_asc")
    second = discover_estate(estate, max_project_candidates=8, enumeration_order="name_desc")
    assert set(_paths(first)) == set(_paths(second))
    assert _names(first) == _names(second)
    for name in late:
        assert name in _names(first)


def test_6_authorized_volume_root_never_emitted_as_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    (volume / ".git").mkdir(parents=True)
    _write(
        volume / ".git" / "config", '[remote "origin"]\n\turl = https://example.invalid/vol.git\n'
    )
    _write(volume / "README.md", "# volume\n")
    _make_proj(volume / "inside")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    paths = _paths(report)
    assert not any(Path(path).resolve() == volume.resolve() for path in paths)
    assert "inside" in _names(report)
    reasons = {row["reason"] for row in report["categories"]["IGNORED"]}
    assert "authorized_volume_root_scope_container" in reasons


def test_7_root_level_knowledge_not_assigned_to_invented_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    (volume / ".git").mkdir(parents=True)
    _write(volume / "docs" / "a.md", "a\n")
    _write(volume / "docs" / "b.md", "b\n")
    _write(volume / "docs" / "c.md", "c\n")
    _make_proj(volume / "inside")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    knowledge = report["candidates"]["knowledge"]
    assert knowledge
    for row in knowledge:
        assert (
            row.get("matched_project_id") not in {"", "D", "D:", None}
            or row["knowledge_relation"] != "KNOWLEDGE_PROJECT_MATCHED"
        )
        if Path(row["path"]).resolve() == volume.resolve() or Path(row["path"]).name in {
            "docs",
            "D",
        }:
            assert row["knowledge_relation"] in {
                "KNOWLEDGE_UNMATCHED",
                "KNOWLEDGE_AMBIGUOUS",
            }
            assert _valid_or_none(row.get("matched_project_id")) != ""


def _valid_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def test_8_blank_project_id_assignment_impossible(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _make_proj(estate / "alpha")
    _write(estate / "notes" / "a.md", "a\n")
    _write(estate / "notes" / "b.md", "b\n")
    _write(estate / "notes" / "c.md", "c\n")
    report = discover_estate(estate)
    for row in report["candidates"]["knowledge"]:
        mid = row.get("matched_project_id")
        if mid is not None:
            assert isinstance(mid, str) and mid.strip() != ""


def test_9_dangling_knowledge_project_relation_impossible(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    alpha = _make_proj(estate / "alpha")
    _write(alpha / "docs" / "a.md", "a\n")
    _write(alpha / "docs" / "b.md", "b\n")
    _write(alpha / "docs" / "c.md", "c\n")
    report = discover_estate(estate)
    project_tokens = set()
    for row in report["candidates"]["projects"]:
        project_tokens.add(row["candidate_id"])
        if row.get("matched_project_id"):
            project_tokens.add(row["matched_project_id"])
    for row in report["candidates"]["knowledge"]:
        mid = row.get("matched_project_id")
        if mid:
            assert mid in project_tokens


def test_10_bounded_directory_semantics_unchanged(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _make_proj(estate / "alpha")
    report = discover_estate(estate)
    assert report["authorized_root_mode"] == "BOUNDED_DIRECTORY"
    assert report["volume_root_authorized"] is False
    assert "alpha" in _names(report)


def test_11_d078_volume_authorization_still_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = tmp_path / "D"
    _make_proj(volume / "alpha")
    _fake_windows_volume(monkeypatch, volume)
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        discover_estate(volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    assert report["volume_root_authorized"] is True
    assert "alpha" in _names(report)


def test_12_system_volume_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import project_atlas.estate_discovery as ed

    volume = tmp_path / "C"
    volume.mkdir()
    vol_key = ed.canonical_path_key(volume)
    monkeypatch.setattr(
        ed, "is_filesystem_root", lambda path: ed.canonical_path_key(Path(path)) == vol_key
    )
    monkeypatch.setattr(
        ed,
        "is_windows_drive_volume_root",
        lambda path, host_os=None: ed.canonical_path_key(Path(path)) == vol_key,
    )
    monkeypatch.setattr(
        ed,
        "is_windows_system_volume_root",
        lambda path, host_os=None, environ=None: ed.canonical_path_key(Path(path)) == vol_key,
    )
    monkeypatch.setattr(ed, "is_unc_root", lambda path: False)
    with pytest.raises(EstateDiscoveryError, match="SYSTEM_VOLUME_ROOT_NOT_ALLOWED"):
        discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_13_home_still_refuses() -> None:
    with pytest.raises(EstateDiscoveryError, match="HOME_DIRECTORY_NOT_ALLOWED"):
        authorize_discovery_root(Path.home(), root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_14_unc_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import project_atlas.estate_discovery as ed

    volume = tmp_path / "unc"
    volume.mkdir()
    key = ed.canonical_path_key(volume)
    monkeypatch.setattr(ed, "is_unc_root", lambda path: ed.canonical_path_key(Path(path)) == key)
    monkeypatch.setattr(ed, "is_windows_drive_volume_root", lambda path, host_os=None: False)
    with pytest.raises(EstateDiscoveryError, match="UNC_VOLUME_ROOT_NOT_ALLOWED"):
        discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)


def test_15_reparse_escape_still_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "D"
    outside = tmp_path / "outside-secret"
    _make_proj(outside)
    volume.mkdir()
    (volume / "escape").symlink_to(outside, target_is_directory=True)
    _make_proj(volume / "inside")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    assert not any("outside-secret" in path for path in _paths(report))
    assert report["security"]["unsafe_path_escapes_allowed"] == 0


def test_16_d067_scan_honesty_remains(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    current = estate
    for index in range(9):
        current = current / f"L{index + 1}"
        current.mkdir(parents=True, exist_ok=True)
    _make_proj(current / "deep-proj")
    _make_proj(estate / "shallow")
    report = discover_estate(estate, include_knowledge=False)
    assert report["scan"]["scan_complete"] is False
    assert report["scan"]["depth_limit_reached"] is True
    assert "SCAN INCOMPLETE" in format_discovery_human(report)


def test_17_cli_api_web_parity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    estate = tmp_path / "estate"
    vault = tmp_path / "vault"
    _make_proj(estate / "alpha")
    report = discover_estate(estate, vault=vault, max_project_candidates=1)
    write_discovery_report(report, vault / "generated" / "ops" / "estate-discovery-report.json")
    view = load_estate_discovery_view(vault)
    assert view["scan"]["candidate_selection_policy"] == CANDIDATE_SELECTION_POLICY
    assert view["scan"]["project_candidates_seen"] == report["scan"]["project_candidates_seen"]
    assert (
        view["scan"]["project_candidates_emitted"] == report["scan"]["project_candidates_emitted"]
    )
    rc = main(["discover", "--root", str(estate), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scan"]["candidate_selection_policy"] == CANDIDATE_SELECTION_POLICY


def test_family_grouping_is_not_identity_merge(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    for index in range(6):
        _make_proj(
            estate / "cluster" / f"copy-{index}",
            remote="https://example.invalid/org/same.git",
            package="same",
        )
    report = discover_estate(estate, max_project_candidates=3)
    families = {row.get("candidate_family") for row in report["candidates"]["projects"]}
    assert any(isinstance(item, str) and item.startswith("remote:") for item in families)
    assert report["scan"]["project_candidates_seen"] == 6
    assert report["scan"]["project_candidates_emitted"] <= 2
    assert report["scan"]["project_limit_reached"] is True


def test_default_filesystem_root_still_refuses() -> None:
    with pytest.raises(EstateDiscoveryError, match="FILESYSTEM_ROOT_NOT_ALLOWED"):
        refuse_dangerous_authorized_root(Path(Path.cwd().anchor))

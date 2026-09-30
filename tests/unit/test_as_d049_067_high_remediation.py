"""D-067 HIGH remediations: cache/ ignore + depth-bound honesty.

Trace: D-PROJECT-ATLAS-CLOUD-D049-REMEDIATION-067
Invalidates frozen tip 0509287 / tree 728f3af for:
  - cache/ IGNORE POLICY GAP (HIGH 1)
  - DEPTH BOUND FALSE COMPLETENESS (HIGH 2)
Also covers the low-risk quoted git-config URL sanitizer residual.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.cli import main
from project_atlas.estate_discovery import (
    DEFAULT_MAX_DEPTH,
    IGNORE_DIR_NAMES,
    discover_estate,
    format_discovery_human,
    sanitize_git_remote_url,
    write_discovery_report,
)
from project_atlas.web_api.discovery import load_estate_discovery_view


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_proj(root: Path, *, with_src: bool = True) -> Path:
    _write(root / "README.md", f"# {root.name}\n")
    _write(root / "package.json", f'{{"name":"{root.name}"}}\n')
    _write(root / "pyproject.toml", f'[project]\nname = "{root.name}"\n')
    (root / ".git").mkdir(parents=True, exist_ok=True)
    _write(
        root / ".git" / "config",
        '[remote "origin"]\n\turl = https://example.com/repo.git\n',
    )
    if with_src:
        (root / "src").mkdir(parents=True, exist_ok=True)
        _write(root / "src" / "main.py", "print(1)\n")
    return root


def _names(report: dict[str, object]) -> set[str]:
    candidates = report["candidates"]  # type: ignore[index]
    projects = candidates["projects"]  # type: ignore[index]
    return {Path(str(c["path"])).name for c in projects}


def _paths(report: dict[str, object]) -> list[str]:
    candidates = report["candidates"]  # type: ignore[index]
    projects = candidates["projects"]  # type: ignore[index]
    return [str(c["path"]) for c in projects]


# --- HIGH 1: exact ignore policy ------------------------------------------------


def test_ignore_dir_names_includes_cache_and_dot_cache() -> None:
    assert "cache" in IGNORE_DIR_NAMES
    assert ".cache" in IGNORE_DIR_NAMES


def test_cache_tree_fake_project_not_discovered(tmp_path: Path) -> None:
    """HIGH 1: Estate/DecoyHost/cache/fake-proj must not be a candidate."""
    estate = tmp_path / "Estate"
    _make_proj(estate / "RealProject")
    _make_proj(estate / "DecoyHost" / "cache" / "fake-proj")
    report = discover_estate(estate, include_knowledge=False)
    paths = _paths(report)
    assert any(p.endswith("RealProject") for p in paths)
    assert not any("/cache/" in p and "fake-proj" in p for p in paths)
    assert "fake-proj" not in _names(report)


def test_existing_ignore_trees_remain_excluded(tmp_path: Path) -> None:
    estate = tmp_path / "Estate"
    _make_proj(estate / "RealProject")
    hosts = (
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "vendor",
        "target",
        "generated",
        ".atlas-vault",
        ".git",
        ".cache",
        "cache",
    )
    for name in hosts:
        _make_proj(estate / "DecoyHost" / name / "fake-proj")
    report = discover_estate(estate, include_knowledge=False)
    paths = _paths(report)
    for name in hosts:
        assert not any(f"/{name}/" in p and "fake-proj" in p for p in paths), name
    assert "fake-proj" not in _names(report)


def test_cache_substring_names_remain_discoverable(tmp_path: Path) -> None:
    """Exact directory policy — names containing 'cache' are not ignored."""
    estate = tmp_path / "Estate"
    controls = (
        "project-cache",
        "cache-service",
        "cached",
        "cachex",
        "my-cache-project",
    )
    for name in controls:
        _make_proj(estate / name)
    report = discover_estate(estate, include_knowledge=False)
    names = _names(report)
    for name in controls:
        assert name in names, name


# --- HIGH 2: depth honesty -----------------------------------------------------


def test_case_a_shallower_than_bound_is_complete(tmp_path: Path) -> None:
    estate = tmp_path / "Estate"
    _make_proj(estate / "shallow")
    report = discover_estate(estate, include_knowledge=False)
    assert report["scan"]["scan_complete"] is True
    assert report["scan"]["depth_limit_reached"] is False
    assert report["scan"]["truncation_reason"] is None
    assert "shallow" in _names(report)


def test_case_b_exactly_at_bound_no_deeper_content_is_complete(
    tmp_path: Path,
) -> None:
    estate = tmp_path / "Estate"
    current = estate
    for i in range(DEFAULT_MAX_DEPTH):
        current = current / f"L{i + 1}"
    # Leaf project at depth==max_depth; only ignored child (.git), no src/.
    _make_proj(current, with_src=False)
    report = discover_estate(estate, include_knowledge=False)
    assert current.relative_to(estate).parts.__len__() == DEFAULT_MAX_DEPTH
    assert report["scan"]["scan_complete"] is True
    assert report["scan"]["depth_limit_reached"] is False
    assert current.name in _names(report)


def test_case_c_relevant_descendant_beyond_bound_is_incomplete(
    tmp_path: Path,
) -> None:
    estate = tmp_path / "Estate"
    _make_proj(estate / "shallow")
    current = estate
    for i in range(DEFAULT_MAX_DEPTH + 3):
        current = current / f"L{i + 1}"
        current.mkdir(parents=True, exist_ok=True)
    _make_proj(current / "deep-proj")
    report = discover_estate(estate, include_knowledge=False)
    assert "deep-proj" not in _names(report)
    assert report["scan"]["scan_complete"] is False
    assert report["scan"]["depth_limit_reached"] is True
    assert report["scan"]["max_depth"] == DEFAULT_MAX_DEPTH
    reason = str(report["scan"]["truncation_reason"] or "")
    assert "max_depth" in reason
    assert "max_depth_reached" in report["scan"]["truncation_causes"]
    human = format_discovery_human(report)
    assert "SCAN INCOMPLETE" in human
    assert f"max_depth={DEFAULT_MAX_DEPTH}" in human
    assert "Results are not a complete estate inventory." in human


def test_case_d_multiple_deep_branches_deterministic(tmp_path: Path) -> None:
    estate = tmp_path / "Estate"
    for branch in ("A", "B"):
        current = estate / branch
        for i in range(DEFAULT_MAX_DEPTH + 1):
            current = current / f"L{i + 1}"
            current.mkdir(parents=True, exist_ok=True)
        _make_proj(current / "deep")
    first = discover_estate(estate, include_knowledge=False)
    second = discover_estate(estate, include_knowledge=False)
    assert first["scan"] == second["scan"]
    assert first["scan"]["scan_complete"] is False
    assert first["scan"]["depth_limit_reached"] is True
    assert first["scan"]["truncation_causes"] == ["max_depth_reached"]


def test_case_e_candidate_and_depth_limits_both_visible(tmp_path: Path) -> None:
    estate = tmp_path / "Estate"
    for i in range(4):
        _make_proj(estate / f"p{i}", with_src=False)
    current = estate
    for i in range(DEFAULT_MAX_DEPTH + 1):
        current = current / f"D{i + 1}"
        current.mkdir(parents=True, exist_ok=True)
    _make_proj(current / "deep-proj")
    report = discover_estate(estate, include_knowledge=False, max_project_candidates=2)
    assert report["scan"]["scan_complete"] is False
    assert report["scan"]["project_limit_reached"] is True
    assert report["scan"]["depth_limit_reached"] is True
    causes = report["scan"]["truncation_causes"]
    assert "max_depth_reached" in causes
    assert "project_limit_reached" in causes
    assert "max_depth" in str(report["scan"]["truncation_reason"])


def test_case_f_only_ignored_subtree_beyond_depth_stays_complete(
    tmp_path: Path,
) -> None:
    """Ignored descendants are excluded by policy without traversal (Case F)."""
    estate = tmp_path / "Estate"
    _make_proj(estate / "RealProject", with_src=False)
    deep = estate / "node_modules"
    for i in range(DEFAULT_MAX_DEPTH + 2):
        deep = deep / f"L{i + 1}"
        deep.mkdir(parents=True, exist_ok=True)
    _make_proj(deep / "fake-proj")
    report = discover_estate(estate, include_knowledge=False)
    assert "fake-proj" not in _names(report)
    assert report["scan"]["depth_limit_reached"] is False
    assert report["scan"]["scan_complete"] is True
    assert report["scan"]["truncation_reason"] is None


def test_cli_json_scan_state_parity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    estate = tmp_path / "Estate"
    current = estate
    for i in range(DEFAULT_MAX_DEPTH + 1):
        current = current / f"L{i + 1}"
        current.mkdir(parents=True, exist_ok=True)
    _make_proj(current / "deep-proj")
    code = main(["discover", "--json", "--root", str(estate)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    report = discover_estate(estate, include_knowledge=True)
    assert payload["scan"]["scan_complete"] is False
    assert payload["scan"]["depth_limit_reached"] is True
    assert payload["scan"]["scan_complete"] == report["scan"]["scan_complete"]
    assert payload["scan"]["depth_limit_reached"] == report["scan"]["depth_limit_reached"]
    assert payload["scan"]["truncation_reason"] == report["scan"]["truncation_reason"]


def test_api_web_projects_depth_incompleteness(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    estate = tmp_path / "Estate"
    current = estate
    for i in range(DEFAULT_MAX_DEPTH + 1):
        current = current / f"L{i + 1}"
        current.mkdir(parents=True, exist_ok=True)
    _make_proj(current / "deep-proj")
    report = discover_estate(estate, vault=vault)
    write_discovery_report(report, vault / "generated" / "ops" / "estate-discovery-report.json")
    view = load_estate_discovery_view(vault)
    assert view["scan"]["scan_complete"] is False
    assert view["scan"]["depth_limit_reached"] is True
    assert view["scan"]["max_depth"] == DEFAULT_MAX_DEPTH
    assert "max_depth_reached" in view["scan"]["truncation_causes"]
    assert view["scan"]["truncation_reason"] == report["scan"]["truncation_reason"]


def test_discover_help_discloses_root_default_and_max_depth() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["discover", "--help"])
    assert excinfo.value.code == 0


def test_discover_help_text_names_cwd_and_depth_bound(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["discover", "--help"])
    text = capsys.readouterr().out
    assert "current working directory" in text
    assert f"max_depth={DEFAULT_MAX_DEPTH}" in text
    assert "SCAN INCOMPLETE" in text
    assert "owner-authorized-volume" in text


# --- MEDIUM: quoted git-config URL --------------------------------------------


def test_sanitize_quoted_git_remote_strips_userinfo() -> None:
    planted = "D067_PLANTED_SECRET"
    assert (
        sanitize_git_remote_url(f'"https://user:{planted}@example.invalid/org/project.git"')
        == "https://example.invalid/org/project.git"
    )
    assert (
        sanitize_git_remote_url(f'"https://user:p%40ss{planted}@example.invalid/org/project.git"')
        == "https://example.invalid/org/project.git"
    )
    assert (
        sanitize_git_remote_url(f"https://user:{planted}@example.invalid/org/project.git")
        == "https://example.invalid/org/project.git"
    )
    assert (
        sanitize_git_remote_url(f"https://{planted}@example.invalid/org/project.git")
        == "https://example.invalid/org/project.git"
    )
    assert (
        sanitize_git_remote_url("ssh://git@example.invalid/org/project.git")
        == "ssh://example.invalid/org/project.git"
    )


def test_quoted_gitconfig_remote_not_echoed_in_report(tmp_path: Path) -> None:
    planted = "D067_PLANTED_SECRET"
    estate = tmp_path / "Estate"
    proj = estate / "quoted-svc"
    _write(proj / "README.md", "# quoted\n")
    _write(proj / "package.json", '{"name":"quoted-svc"}\n')
    (proj / ".git").mkdir(parents=True)
    _write(
        proj / ".git" / "config",
        f'[remote "origin"]\n\turl = "https://user:{planted}@example.invalid/q.git"\n',
    )
    report = discover_estate(estate, include_knowledge=False)
    blob = json.dumps(report, sort_keys=True)
    assert planted not in blob
    remote = report["candidates"]["projects"][0]["fingerprint"].get("git_remote")
    assert remote == "https://example.invalid/q.git"

"""D-084 hierarchical fair selection + bounded enrichment.

Trace: D-PROJECT-ATLAS-CLOUD-D049-D084-ESTATE-FAIR-SELECTION
Does not weaken D-078 volume-root policy or D-080 attachment truth.
Does not treat first-seen order or raw candidate volume as authority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.estate_discovery import (
    CANDIDATE_SELECTION_POLICY,
    PRESELECT_MULTIPLIER,
    ROOT_MODE_OWNER_AUTHORIZED_VOLUME,
    discover_estate,
    hierarchical_preselect_budget,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_proj(
    root: Path,
    *,
    remote: str = "https://example.invalid/org/app.git",
    package: str | None = None,
    with_git: bool = True,
    docs: int = 1,
) -> Path:
    name = package if package is not None else root.name
    _write(root / "README.md", f"# {name}\n")
    _write(root / "package.json", f'{{"name":"{name}"}}\n')
    if with_git:
        (root / ".git").mkdir(parents=True, exist_ok=True)
        _write(
            root / ".git" / "config",
            f'[remote "origin"]\n\turl = {remote}\n',
        )
    (root / "src").mkdir(parents=True, exist_ok=True)
    _write(root / "src" / "main.py", "print(1)\n")
    for index in range(max(0, docs - 1)):
        _write(root / "docs" / f"note-{index}.md", f"note {index}\n")
    return root


def _names(report: dict[str, object]) -> set[str]:
    candidates = report["candidates"]  # type: ignore[index]
    projects = candidates["projects"]  # type: ignore[index]
    return {Path(str(item["path"])).name for item in projects}


def _paths(report: dict[str, object]) -> set[str]:
    candidates = report["candidates"]  # type: ignore[index]
    projects = candidates["projects"]  # type: ignore[index]
    return {str(item["path"]) for item in projects}


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


def _adversarial_estate(root: Path, *, noise: int = 520) -> dict[str, list[str]]:
    """Synthetic estate larger than a typical emit cap. No owner path names."""
    families = ("alpha-clone", "bravo-clone", "charlie-clone", "delta-clone")
    for index in range(noise):
        family = families[index % len(families)]
        _make_proj(
            root / "aaa-noise" / f"{family}-{index:04d}",
            remote=f"https://example.invalid/flood/{family}.git",
            package=family,
        )
    keepers = ["strong-alpha", "strong-bravo", "strong-charlie", "strong-delta", "strong-echo"]
    for name in keepers:
        _make_proj(
            root / "zzz-keepers" / name,
            remote=f"https://example.invalid/owner/{name}.git",
            package=name,
            docs=3,
        )
    siblings = ["sibling-one", "sibling-two"]
    for name in siblings:
        _make_proj(
            root / "mid-siblings" / name,
            remote=f"https://example.invalid/sib/{name}.git",
            package=name,
        )
    keep_root = _make_proj(
        root / "boundary-root" / "keep-root",
        remote="https://example.invalid/owner/keep-root.git",
        package="keep-root",
    )
    _make_proj(
        keep_root / "rich-nested",
        remote="https://example.invalid/unused/nested.git",
        package="rich-nested",
        with_git=False,
        docs=8,
    )
    parent = _make_proj(
        root / "nested-indep" / "outer-repo",
        remote="https://example.invalid/owner/outer-repo.git",
        package="outer-repo",
    )
    _make_proj(
        parent / "inner-repo",
        remote="https://example.invalid/owner/inner-repo.git",
        package="inner-repo",
    )
    _write(root / "aaa-noise" / "flood-notes" / "a.md", "a\n")
    _write(root / "aaa-noise" / "flood-notes" / "b.md", "b\n")
    _write(root / "aaa-noise" / "flood-notes" / "c.md", "c\n")
    _write(root / "zzz-keepers" / "strong-alpha" / "docs" / "extra.md", "x\n")
    return {
        "keepers": keepers,
        "siblings": siblings,
        "root": ["keep-root"],
        "nested_component": ["rich-nested"],
        "independent_nested": ["outer-repo", "inner-repo"],
    }


def test_policy_bumped_to_hierarchical_fair_v2() -> None:
    assert CANDIDATE_SELECTION_POLICY == "deterministic_hierarchical_fair_v2"
    assert PRESELECT_MULTIPLIER == 3
    assert hierarchical_preselect_budget(11461, 500) == 1500
    assert hierarchical_preselect_budget(12, 500) == 12


def test_1_enumeration_order_independent(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    expected = _adversarial_estate(estate, noise=520)
    first = discover_estate(estate, max_project_candidates=40, enumeration_order="name_asc")
    second = discover_estate(estate, max_project_candidates=40, enumeration_order="name_desc")
    assert _paths(first) == _paths(second)
    for name in expected["keepers"]:
        assert name in _names(first)


def test_2_noisy_region_cannot_monopolize(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    expected = _adversarial_estate(estate, noise=520)
    report = discover_estate(estate, max_project_candidates=40)
    names = _names(report)
    for name in expected["keepers"]:
        assert name in names
    emitted_regions = report["scan"]["region_emitted_counts"]
    seen_regions = report["scan"]["region_candidate_counts"]
    assert seen_regions["aaa-noise"] >= 520
    assert emitted_regions.get("zzz-keepers", 0) == 5
    assert emitted_regions.get("aaa-noise", 0) < 40
    assert emitted_regions.get("aaa-noise", 0) <= 40 - 5


def test_3_project_root_not_displaced_by_rich_nested(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    keep_root = _make_proj(estate / "keep-root")
    _make_proj(keep_root / "rich-nested", with_git=False, docs=8)
    report = discover_estate(estate, max_project_candidates=1)
    names = _names(report)
    assert "keep-root" in names
    assert "rich-nested" not in names


def test_4_independent_nested_repo_remains_eligible(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    parent = _make_proj(estate / "outer-repo")
    _make_proj(parent / "inner-repo")
    report = discover_estate(estate, max_project_candidates=8)
    names = _names(report)
    assert "outer-repo" in names
    assert "inner-repo" in names


def test_5_multiple_valid_siblings_can_be_emitted(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    expected = _adversarial_estate(estate, noise=520)
    report = discover_estate(estate, max_project_candidates=40)
    names = _names(report)
    for name in expected["siblings"]:
        assert name in names


def test_6_cap_honesty_over_five_hundred_candidates(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _adversarial_estate(estate, noise=520)
    report = discover_estate(estate, max_project_candidates=40)
    scan = report["scan"]
    assert scan["project_candidates_seen"] > 500
    assert scan["project_candidates_emitted"] <= 40
    assert scan["project_candidates_emitted"] >= 10
    assert scan["project_candidates_suppressed"] == (
        scan["project_candidates_seen"] - scan["project_candidates_emitted"]
    )
    assert scan["project_limit_reached"] is True
    assert scan["scan_complete"] is False
    assert "project_limit_reached" in scan["truncation_causes"]
    assert scan["candidate_selection_policy"] == CANDIDATE_SELECTION_POLICY


def test_7_volume_root_never_emitted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    volume = tmp_path / "D"
    (volume / ".git").mkdir(parents=True)
    _write(
        volume / ".git" / "config", '[remote "origin"]\n\turl = https://example.invalid/vol.git\n'
    )
    _write(volume / "README.md", "# volume\n")
    _make_proj(volume / "inside")
    _fake_windows_volume(monkeypatch, volume)
    report = discover_estate(volume, root_mode=ROOT_MODE_OWNER_AUTHORIZED_VOLUME)
    assert not any(Path(path).resolve() == volume.resolve() for path in _paths(report))
    assert "inside" in _names(report)


def test_8_knowledge_relations_stay_fail_closed(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    expected = _adversarial_estate(estate, noise=80)
    report = discover_estate(estate, max_project_candidates=12)
    project_tokens = set()
    for row in report["candidates"]["projects"]:
        project_tokens.add(row["candidate_id"])
        if row.get("matched_project_id"):
            project_tokens.add(row["matched_project_id"])
    blank = 0
    dangling = 0
    for row in report["candidates"]["knowledge"]:
        mid = row.get("matched_project_id")
        if mid is None:
            continue
        if not isinstance(mid, str) or mid.strip() == "":
            blank += 1
            continue
        if mid not in project_tokens:
            dangling += 1
    assert blank == 0
    assert dangling == 0
    for name in expected["keepers"][:2]:
        assert name in _names(report)


def test_9_bounded_enrichment_does_not_fingerprint_every_sighting(
    tmp_path: Path,
) -> None:
    estate = tmp_path / "estate"
    _adversarial_estate(estate, noise=520)
    report = discover_estate(estate, max_project_candidates=40)
    scan = report["scan"]
    seen = scan["project_candidates_seen"]
    enriched = scan["project_candidates_enriched"]
    preselected = scan["project_candidates_preselected"]
    assert seen > 500
    assert enriched < seen
    assert preselected <= 40 * PRESELECT_MULTIPLIER
    assert enriched <= 40 * PRESELECT_MULTIPLIER
    assert enriched == preselected


def test_10_under_cap_still_enriches_emitted(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _make_proj(estate / "alpha")
    _make_proj(estate / "beta")
    report = discover_estate(estate)
    assert report["scan"]["project_candidates_seen"] == 2
    assert report["scan"]["project_candidates_enriched"] == 2
    assert report["scan"]["project_candidates_emitted"] == 2
    assert report["scan"]["scan_complete"] is True

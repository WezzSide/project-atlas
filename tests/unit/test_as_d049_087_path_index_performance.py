"""D-087 in-memory path index + resolved-path reuse.

Trace: D-PROJECT-ATLAS-CLOUD-D049-D087-PATH-INDEX-PERFORMANCE

Does not redesign D-084 selection semantics.
Does not weaken path safety, D-078 policy, or bounded enrichment.
Wall-clock is recorded outside normal CI assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.estate_discovery import (
    CANDIDATE_SELECTION_POLICY,
    DiscoveryCandidate,
    _associate_knowledge,
    _under_authorized,
    canonical_path_key,
    discover_estate,
    reset_discovery_perf,
    select_bounded_knowledge_sightings,
)
from project_atlas.estate_path_index import (
    ancestor_items_from_index,
    canonical_key_from_resolved_text,
    current_discovery_perf,
    has_selected_project_ancestor,
    is_canonical_descendant,
    is_canonical_under,
    parent_canonical_key,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_proj(root: Path, *, remote: str, package: str) -> Path:
    _write(root / "README.md", f"# {package}\n")
    _write(root / "package.json", f'{{"name":"{package}"}}\n')
    (root / ".git").mkdir(parents=True, exist_ok=True)
    _write(root / ".git" / "config", f'[remote "origin"]\n\turl = {remote}\n')
    (root / "src").mkdir(parents=True, exist_ok=True)
    _write(root / "src" / "main.py", "print(1)\n")
    return root


def _candidate(
    path: Path,
    *,
    candidate_id: str,
    path_key: str | None = None,
) -> DiscoveryCandidate:
    key = path_key if path_key is not None else canonical_path_key(path)
    return DiscoveryCandidate(
        candidate_id=candidate_id,
        kind="project",
        path=path.as_posix(),
        display_name=path.name,
        lifecycle="CLASSIFIED",
        match_state="UNMATCHED",
        category="DISCOVERED_PROJECTS",
        fingerprint={"path_key": key, "canonical_path": path.as_posix()},
    )


def _knowledge_sighting(path: Path, *, is_obsidian: bool = False):
    from project_atlas.estate_discovery import KnowledgeSighting

    resolved = path.expanduser().resolve(strict=False)
    return KnowledgeSighting(
        path=resolved,
        path_key=canonical_path_key(resolved),
        depth=len(resolved.parts),
        region=resolved.parts[0] if resolved.parts else "_root",
        signals=["knowledge_dir:docs"],
        is_obsidian=is_obsidian,
        names=set(),
    )


def test_component_aware_prefix_rejects_sibling_collision() -> None:
    """D:\\foo is not an ancestor of D:\\foobar."""
    assert is_canonical_descendant("d:/foobar", "d:/foo") is False
    assert is_canonical_descendant("d:/foo/bar", "d:/foo") is True
    assert is_canonical_under("d:/foobar", "d:/foo") is False
    assert is_canonical_under("d:/foo", "d:/foo") is True
    assert is_canonical_under("d:/foo/bar", "d:/") is True
    assert is_canonical_under("d:/foo/bar", "d:") is True
    assert has_selected_project_ancestor("d:/foobar/docs", {"d:/foo"}) is False
    assert has_selected_project_ancestor("d:/foo/docs", {"d:/foo"}) is True


def test_unicode_and_windows_case_keys_are_deterministic() -> None:
    nfc = canonical_key_from_resolved_text("D:/Cafe\u0301/docs")
    composed = canonical_key_from_resolved_text("D:/Café/docs")
    assert nfc == composed
    folded_a = canonical_key_from_resolved_text("D:/Foo")
    folded_b = canonical_key_from_resolved_text("d:/foo")
    if folded_a != folded_b:
        # Linux CI: case-preserving keys stay distinct.
        assert folded_a.endswith("Foo") or folded_a.endswith("foo")
    parent = parent_canonical_key("d:/tmp/alpha/docs")
    assert parent == "d:/tmp/alpha"
    assert parent_canonical_key("d:/tmp") == "d:"
    assert parent_canonical_key("d:") == ""
    assert parent_canonical_key("/workspace") == "/"
    assert parent_canonical_key("/") == ""


def test_naive_startswith_is_not_used_for_ancestry() -> None:
    """Guard the forbidden 'D:/foo'.startswith style bug."""
    child = "d:/foobar"
    ancestor = "d:/foo"
    assert child.startswith(ancestor) is True
    assert is_canonical_descendant(child, ancestor) is False


def test_knowledge_index_matches_linear_d084_semantics(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    outer = _make_proj(
        estate / "outer",
        remote="https://example.invalid/org/outer.git",
        package="outer",
    )
    inner = _make_proj(
        estate / "outer" / "inner",
        remote="https://example.invalid/org/inner.git",
        package="inner",
    )
    notes = estate / "outer" / "inner" / "docs"
    _write(notes / "a.md", "a\n")
    _write(notes / "b.md", "b\n")
    _write(notes / "c.md", "c\n")
    sibling = estate / "foobar-notes"
    _write(sibling / "a.md", "a\n")
    _write(sibling / "b.md", "b\n")
    _write(sibling / "c.md", "c\n")
    foo = _make_proj(
        estate / "foo",
        remote="https://example.invalid/org/foo.git",
        package="foo",
    )

    projects = [
        _candidate(outer, candidate_id="project-outer"),
        _candidate(inner, candidate_id="project-inner"),
        _candidate(foo, candidate_id="project-foo"),
    ]
    by_key = {str(item.fingerprint["path_key"]): item for item in projects}
    nested = _knowledge_sighting(notes)
    collision = _knowledge_sighting(sibling)

    index_parents = ancestor_items_from_index(nested.path_key, by_key)
    linear_parents = [
        item
        for item in projects
        if _under_authorized(nested.path, Path(item.path))
        and canonical_path_key(nested.path) != str(item.fingerprint["path_key"])
    ]
    assert {p.candidate_id for p in index_parents} == {p.candidate_id for p in linear_parents}
    assert {p.candidate_id for p in index_parents} == {
        "project-outer",
        "project-inner",
    }

    relation, state, _, token = _associate_knowledge(
        nested.path,
        projects,
        is_obsidian=False,
        vault_projects=[],
        knowledge_path_key=nested.path_key,
    )
    assert relation == "KNOWLEDGE_AMBIGUOUS"
    assert state == "AMBIGUOUS"
    assert token is None

    assert has_selected_project_ancestor(collision.path_key, set(by_key)) is False
    rel2, _, _, _ = _associate_knowledge(
        collision.path,
        projects,
        is_obsidian=False,
        vault_projects=[],
        knowledge_path_key=collision.path_key,
    )
    assert rel2 in {"KNOWLEDGE_DISCOVERED", "KNOWLEDGE_UNMATCHED"}


def test_in_memory_selection_has_no_resolve_and_sub_quadratic_checks() -> None:
    """P=500, K=2000: ancestry checks << K*P; no resolve during selection."""
    projects = []
    knowledge = []
    for index in range(500):
        path = Path(f"/synthetic/estate/region{index:03d}/proj{index:03d}")
        key = f"/synthetic/estate/region{index:03d}/proj{index:03d}"
        projects.append(_candidate(path, candidate_id=f"project-{index:03d}", path_key=key))
        for kid in range(4):
            kpath = path / "docs" / f"cluster-{kid}"
            kkey = f"{key}/docs/cluster-{kid}"
            from project_atlas.estate_discovery import KnowledgeSighting

            knowledge.append(
                KnowledgeSighting(
                    path=kpath,
                    path_key=kkey,
                    depth=6,
                    region=f"region{index:03d}",
                    signals=["knowledge_dir:docs"],
                    is_obsidian=False,
                    names=set(),
                )
            )

    kx_p = len(knowledge) * len(projects)
    assert kx_p == 1_000_000
    reset_discovery_perf()
    chosen, suppressed = select_bounded_knowledge_sightings(knowledge, 500, projects)
    perf = current_discovery_perf()
    assert perf.path_resolve_calls == 0
    assert perf.under_authorized_calls == 0
    assert perf.knowledge_project_ancestry_checks < kx_p // 20
    assert perf.knowledge_project_ancestry_checks <= len(knowledge) * 8
    assert len(chosen) == 500
    assert len(suppressed) == 1500
    assert all(
        has_selected_project_ancestor(
            item.path_key, {str(p.fingerprint["path_key"]) for p in projects}
        )
        for item in chosen
    )


def test_discover_estate_preserves_d084_policy_and_bounded_enrichment(
    tmp_path: Path,
) -> None:
    estate = tmp_path / "estate"
    for region_i in range(8):
        region = estate / f"region-{region_i:02d}"
        for proj_i in range(6):
            name = f"app-{region_i:02d}-{proj_i:02d}"
            proj = _make_proj(
                region / name,
                remote=f"https://example.invalid/org/{name}.git",
                package=name,
            )
            docs = proj / "docs"
            for note in range(3):
                _write(docs / f"n{note}.md", "note\n")
    report = discover_estate(
        estate,
        max_project_candidates=20,
        max_knowledge_candidates=40,
    )
    scan = report["scan"]
    assert scan["candidate_selection_policy"] == CANDIDATE_SELECTION_POLICY
    assert scan["project_candidates_seen"] == 48
    assert scan["project_candidates_emitted"] == 20
    assert scan["project_candidates_enriched"] <= 20 * 3
    assert scan["project_candidates_preselected"] <= 20 * 3
    counters = scan["operation_counters"]
    kx_p = scan["knowledge_candidates_seen"] * scan["project_candidates_emitted"]
    assert counters["knowledge_project_ancestry_checks"] < max(kx_p, 1)
    assert scan["path_resolve_calls_during_in_memory_selection"] == 0
    assert report["_perf"]["note"].startswith("Diagnostic timings")
    written = tmp_path / "report.json"
    from project_atlas.estate_discovery import write_discovery_report

    write_discovery_report(report, written)
    payload = written.read_text(encoding="utf-8")
    assert "_perf" not in payload
    assert "_cache_entries" not in payload


def test_symlink_escape_still_uses_security_boundary(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    outside = tmp_path / "outside-secret"
    outside.mkdir()
    _write(outside / "secret.md", "nope\n")
    _make_proj(
        estate / "alpha",
        remote="https://example.invalid/org/alpha.git",
        package="alpha",
    )
    link = estate / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted")
    report = discover_estate(estate, include_knowledge=True)
    assert report["security"]["unsafe_path_escapes_allowed"] == 0
    escaped = [
        row
        for row in report["categories"]["IGNORED"]
        if row.get("reason") == "symlink_or_reparse_escape"
    ]
    assert escaped
    paths = {item["path"] for item in report["candidates"]["projects"]}
    assert all("outside-secret" not in path for path in paths)


def test_nested_component_and_volume_root_not_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import project_atlas.estate_discovery as ed

    volume = tmp_path / "D"
    volume.mkdir()
    vol_key = ed.canonical_path_key(volume)

    def _is_vol(path: Path) -> bool:
        return ed.canonical_path_key(Path(path)) == vol_key

    monkeypatch.setattr(ed, "is_filesystem_root", _is_vol)
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
    _write(volume / "README.md", "# volume\n")
    _write(volume / "package.json", '{"name":"volume"}\n')
    (volume / ".git").mkdir()
    (volume / "src").mkdir()
    nested = _make_proj(
        volume / "real-app",
        remote="https://example.invalid/org/real.git",
        package="real-app",
    )
    _write(nested / "docs" / "a.md", "a\n")
    _write(nested / "docs" / "b.md", "b\n")
    _write(nested / "docs" / "c.md", "c\n")
    report = discover_estate(
        volume,
        root_mode=ed.ROOT_MODE_OWNER_AUTHORIZED_VOLUME,
        host_os="nt",
        environ={"SystemDrive": "C:"},
    )
    names = {Path(item["path"]).name for item in report["candidates"]["projects"]}
    assert "real-app" in names
    assert volume.name not in names
    assert report["scan"]["candidate_selection_policy"] == CANDIDATE_SELECTION_POLICY


def test_single_parent_knowledge_still_matches(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    proj = _make_proj(
        estate / "solo",
        remote="https://example.invalid/org/solo.git",
        package="solo",
    )
    notes = proj / "research"
    for name in ("a.md", "b.md", "c.md"):
        _write(notes / name, "n\n")
    report = discover_estate(estate)
    nested = [
        item for item in report["candidates"]["knowledge"] if Path(item["path"]).name == "research"
    ]
    assert nested
    assert nested[0]["knowledge_relation"] == "KNOWLEDGE_PROJECT_MATCHED"
    assert nested[0]["matched_project_id"]

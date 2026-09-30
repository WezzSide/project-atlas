"""AS-CODER-ALPHA-SOURCE-DRIFT-SCOPE-001 — CC-P1-002 / CC-P2-002 guards."""

from __future__ import annotations

from pathlib import Path

from project_atlas.source_drift_scope import (
    PACKAGE_ID,
    live_path_contained,
    manifest_row_matches_scoped_project,
    manifest_row_owner,
)


def test_unowned_row_does_not_match_scoped_project() -> None:
    row = {"path": "shared/x.md", "sha256": "abc"}
    assert manifest_row_owner(row) is None
    assert manifest_row_matches_scoped_project(row, "harbor-api") is False


def test_unknown_project_row_does_not_match_scoped_project() -> None:
    row = {"path": "orphan.md", "likely_project": "unknown-project"}
    assert manifest_row_matches_scoped_project(row, "harbor-api") is False


def test_sibling_owner_does_not_match_scoped_project() -> None:
    row = {"path": "portal/README.md", "likely_project": "harbor-portal"}
    assert manifest_row_matches_scoped_project(row, "harbor-api") is False


def test_matching_owner_is_in_scope() -> None:
    row = {"path": "api/README.md", "likely_project": "harbor-api"}
    assert manifest_row_matches_scoped_project(row, "harbor-api") is True


def test_project_id_alias_is_accepted() -> None:
    row = {"path": "api/README.md", "project_id": "harbor-api"}
    assert manifest_row_matches_scoped_project(row, "harbor-api") is True


def test_empty_scope_never_matches() -> None:
    row = {"path": "api/README.md", "likely_project": "harbor-api"}
    assert manifest_row_matches_scoped_project(row, "") is False


def test_cc_p1_002_unowned_edit_must_not_stale_unrelated_project(
    tmp_path: Path,
) -> None:
    """Reproduction of CLOUD-031-C CC-P1-002 against the shared filter.

    Manifest: portal owned + shared unowned. Scoped harbor-api must not
    import the unowned row. Editing shared/x.md therefore cannot mark
    harbor-api in-scope.
    """
    sources = [
        {"path": "portal/README.md", "likely_project": "harbor-portal", "sha256": "1"},
        {"path": "shared/x.md", "sha256": "2"},
    ]
    in_scope = [item for item in sources if manifest_row_matches_scoped_project(item, "harbor-api")]
    assert in_scope == []
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "x.md").write_text("changed\n", encoding="utf-8")
    assert not any(manifest_row_matches_scoped_project(item, "harbor-api") for item in sources)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("do-not-echo\n", encoding="utf-8")
    escape = root / "escape.md"
    escape.symlink_to(outside)
    assert live_path_contained(root, "escape.md") is None
    assert live_path_contained(root, "../secret.txt") is None
    assert live_path_contained(root, "/etc/passwd") is None


def test_contained_relative_file_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    target = root / "docs" / "arch.md"
    target.parent.mkdir()
    target.write_text("ok\n", encoding="utf-8")
    live = live_path_contained(root, "docs/arch.md")
    assert live == target.resolve()
    assert PACKAGE_ID == "AS-CODER-ALPHA-SOURCE-DRIFT-SCOPE-001"

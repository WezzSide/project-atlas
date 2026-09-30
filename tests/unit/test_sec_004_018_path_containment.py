"""CODEX-SEC-004 / SEC-014 / SEC-017 / SEC-018 path containment regressions.

Canonical helpers live in ``atlas_contracts.paths`` and are re-exported from
``atlas_contracts.identity``. Windows drive-relative joins, reserved device
names, symlink/reparse escapes, and separator traversal must fail closed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from atlas_contracts.identity import (
    ensure_under_root,
    join_under_root,
    resolve_under_root,
    safe_relative_component,
    safe_relative_path,
)
from atlas_contracts.paths import safe_relative_component as paths_safe_relative_component
from project_atlas.backup import BackupError, _assert_inside
from project_atlas.cli import EXIT_ERROR, EXIT_OK, main
from project_atlas.ingestion import _inside, _source_path


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "../x",
        "a/b",
        "a\\b",
        "/abs",
        "C:\\abs",
        "C:/abs",
        "C:foo",
        "c:bar",
        "\\\\server\\share",
        "//server/share",
        "foo:bar",
        "file:stream",
        "CON",
        "con",
        "NUL",
        "nul.txt",
        "COM1",
        "lpt9",
        "file.",
        "file ",
        "foo\x00bar",
        "foo\nbar",
    ],
)
def test_safe_relative_component_rejects_windows_and_traversal(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        safe_relative_component(value, label="component")
    with pytest.raises(ValueError, match="unsafe"):
        paths_safe_relative_component(value, label="component")


@pytest.mark.parametrize("value", ["ok", "project-id", "AE-001", ".hidden-ok", "file_name"])
def test_safe_relative_component_allows_safe_ids(value: str) -> None:
    assert safe_relative_component(value, label="component") == value


@pytest.mark.parametrize(
    "value",
    [
        "../escape",
        "..\\escape",
        "/absolute",
        "C:\\windows",
        "C:foo/bar",
        "a/../../b",
        "a/C:foo",
        "foo:bar/baz",
        "x/CON",
        "x/nul.txt",
    ],
)
def test_safe_relative_path_rejects_escapes(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        safe_relative_path(value, label="rel")


def test_safe_relative_path_accepts_posix_relative() -> None:
    assert safe_relative_path("docs/readme.md", label="rel") == ("docs", "readme.md")


def test_resolve_under_root_blocks_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        resolve_under_root(root, "../outside", label="path")


def test_resolve_under_root_blocks_windows_drive_relative(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    # Path concatenation of C:foo discards the vault root on Windows.
    with pytest.raises(ValueError, match="unsafe"):
        resolve_under_root(root, "C:foo", label="path")
    with pytest.raises(ValueError, match="unsafe"):
        join_under_root(root, "projects", "C:foo", label="path")


def test_windows_joinpath_drive_relative_is_rejected(tmp_path: Path) -> None:
    """SEC-018: drive-relative components must never be joinable under a root.

    On Windows, ``root / 'C:foo'`` either discards the root (other drive) or
    silently rewrites to ``root/foo`` (same drive). Both are unsafe; the
    canonical helper must reject the component before join.
    """
    root = (tmp_path / "vault").resolve()
    root.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        safe_relative_component("C:foo", label="path")
    with pytest.raises(ValueError, match="unsafe"):
        join_under_root(root, "projects", "C:foo", label="path")
    # Even if a caller bypasses component checks and joins first, containment
    # still fails closed when the join discarded the approved root.
    joined = root / "projects" / "C:foo"
    if sys.platform == "win32" and not str(joined).lower().startswith(str(root).lower()):
        with pytest.raises(ValueError):
            ensure_under_root(root, joined, label="path")


def test_ensure_under_root_blocks_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("leak", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - environment without symlink privilege
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(ValueError, match="escapes root"):
        ensure_under_root(root, root / "escape" / "secret.txt", label="path")


def test_ensure_under_root_allows_descendant(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    target = root / "projects" / "demo" / "note.md"
    target.parent.mkdir(parents=True)
    target.write_text("ok", encoding="utf-8")
    resolved = ensure_under_root(root, target, label="path")
    assert resolved == Path(os.path.realpath(target))


def test_ingestion_inside_and_source_path_use_shared_helper(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    ok = _inside(root, root / "projects" / "demo")
    assert ok.is_relative_to(Path(os.path.realpath(root)))
    with pytest.raises(ValueError, match=r"escapes|unsafe"):
        _inside(root, tmp_path / "outside")
    with pytest.raises(ValueError, match="unsafe"):
        _source_path(root, "../outside.md")
    with pytest.raises(ValueError, match="unsafe"):
        _source_path(root, "C:foo")
    with pytest.raises(ValueError, match="unsafe"):
        _source_path(root, "a/../../b.md")
    nested = _source_path(root, "docs/readme.md")
    assert nested == Path(os.path.realpath(root / "docs" / "readme.md"))


def test_backup_assert_inside_uses_shared_helper(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    _assert_inside(root, root / "MANIFEST.json")
    with pytest.raises(BackupError, match="outside root"):
        _assert_inside(root, tmp_path / "elsewhere" / "x")


def test_backup_assert_inside_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.bin").write_bytes(b"x")
    link = root / "domains"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(BackupError, match="outside root"):
        _assert_inside(root, root / "domains" / "x.bin")


@pytest.mark.parametrize(
    "likely_project",
    ["C:foo", "CON", "foo:bar", "..", "../x", "a/b", "nul.txt"],
)
def test_ingest_rejects_unsafe_likely_project_ids(tmp_path: Path, likely_project: str) -> None:
    """Public ingest boundary: unsafe project ids never create paths (SEC-014)."""
    from project_atlas.source_identity import canonical_source_sha256_bytes

    source_root = tmp_path / "source"
    vault = tmp_path / "vault"
    source_root.mkdir()
    document = source_root / "README.md"
    payload = b"# Controlled source\n"
    document.write_bytes(payload)
    manifest = tmp_path / "crafted-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_root": str(source_root.resolve()),
                "sources": [
                    {
                        "source_id": "source-safe",
                        "path": "README.md",
                        "media_type": "text/markdown",
                        "sha256": canonical_source_sha256_bytes(payload, relative_path="README.md"),
                        "size_bytes": len(payload),
                        "modified_at": "2026-08-01T00:00:00Z",
                        "likely_project": likely_project,
                        "classification_state": "unclassified",
                        "exclusion_reason": None,
                    }
                ],
                "duplicates": {},
                "inventory_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    assert (
        main(
            [
                "ingest",
                "--manifest",
                str(manifest),
                "--vault",
                str(vault),
                "--source",
                str(source_root),
            ]
        )
        == EXIT_ERROR
    )
    # Drive-relative join must not materialize outside the vault tree.
    assert not (tmp_path / "foo").exists()

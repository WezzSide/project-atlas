"""AS-OBSIDIAN-CAPTURE-001-F14 — the remaining raw `OSError` sites in `_promote`.

F11 contained the mkdir failure site in `graph_projections._promote` and named
the two other escapes from the F6 residual register as still open: a read-only
output directory (`staged.write_bytes`) and an existing target that became
unreadable (`path.read_bytes()`). Issue #757 adds a third: a target name too
long for the filesystem (ENAMETOOLONG), which escaped from the existence check
(`path.exists() and not path.is_file()`).

Each must now raise `GraphProjectionError` naming the path, mirroring F11's
``<kind>:<OSError subclass>:<path>`` shape. As in F11, the expected subclass is
measured on this platform by performing the same raw operation, never
hard-coded.
"""

from __future__ import annotations

import os
import pathlib
import stat
import sys
from collections.abc import Callable

import pytest

import project_atlas.graph_projections as gp
from project_atlas.graph_projections import GraphProjectionError

_NEEDS_REAL_PERMISSIONS = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permission bits are not enforced on Windows or for root",
)


def _measured_error_name(operation: Callable[[], object]) -> str:
    """The `OSError` subclass THIS platform raises for THIS fixture."""
    try:
        operation()
    except OSError as exc:
        return type(exc).__name__
    raise AssertionError("fixture is inert: the raw operation succeeded")


def _assert_contained(
    caught: pytest.ExceptionInfo[GraphProjectionError],
    *,
    kind: str,
    expected: str,
    path: pathlib.Path,
) -> None:
    message = str(caught.value)
    assert message.startswith(f"{kind}:"), message
    assert f":{expected}:" in message, f"guard must name the real class, got {message}"
    assert str(path) in message, "the operator needs the path named"
    assert isinstance(caught.value.__cause__, OSError)


@_NEEDS_REAL_PERMISSIONS
def test_f14_read_only_output_directory_stays_inside_the_error_boundary(
    tmp_path: pathlib.Path,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    target = out / "note.md"
    out.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        expected = _measured_error_name(lambda: (out / ".probe").write_bytes(b"x"))
        with pytest.raises(GraphProjectionError) as caught:
            gp._promote({target: b"content"})
        _assert_contained(
            caught, kind="unwritable-canonical-target", expected=expected, path=target
        )
        assert list(out.iterdir()) == [], "no staging residue may be left behind"
    finally:
        out.chmod(stat.S_IRWXU)


@_NEEDS_REAL_PERMISSIONS
def test_f14_unreadable_existing_target_stays_inside_the_error_boundary(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / "note.md"
    target.write_bytes(b"prior")
    target.chmod(0)
    try:
        expected = _measured_error_name(target.read_bytes)
        with pytest.raises(GraphProjectionError) as caught:
            gp._promote({target: b"content"})
        _assert_contained(
            caught, kind="unreadable-canonical-target", expected=expected, path=target
        )
    finally:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert target.read_bytes() == b"prior", "a refused write leaves the prior note"


def test_f14_name_too_long_stays_inside_the_error_boundary(
    tmp_path: pathlib.Path,
) -> None:
    target = tmp_path / ("n" * 4096 + ".md")
    try:
        target.exists()
    except OSError as exc:
        expected: str | None = type(exc).__name__
    else:
        # Platforms whose `exists()` swallows the error (e.g. Windows) fail
        # later, at the staged write; the write must still be contained.
        expected = None
    with pytest.raises(GraphProjectionError) as caught:
        gp._promote({target: b"content"})
    if expected is not None:
        _assert_contained(
            caught, kind="uninspectable-canonical-target", expected=expected, path=target
        )
    else:
        assert str(target) in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_f14_write_failure_is_contained_independent_of_permissions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Portable twin of the read-only test: runs as root and on Windows too."""
    target = tmp_path / "note.md"
    real_write = pathlib.Path.write_bytes

    def refuse_stage(self: pathlib.Path, data: bytes) -> int:
        if self.name.endswith(".atlas-stage"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_write(self, data)

    monkeypatch.setattr(pathlib.Path, "write_bytes", refuse_stage)
    with pytest.raises(GraphProjectionError) as caught:
        gp._promote({target: b"content"})
    _assert_contained(
        caught,
        kind="unwritable-canonical-target",
        expected=PermissionError.__name__,
        path=target,
    )
    assert list(tmp_path.iterdir()) == []


def test_f14_read_failure_is_contained_independent_of_permissions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Portable twin of the unreadable-target test."""
    target = tmp_path / "note.md"
    target.write_bytes(b"prior")

    def refuse_read(self: pathlib.Path) -> bytes:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(pathlib.Path, "read_bytes", refuse_read)
    with pytest.raises(GraphProjectionError) as caught:
        gp._promote({target: b"content"})
    _assert_contained(
        caught,
        kind="unreadable-canonical-target",
        expected=PermissionError.__name__,
        path=target,
    )
    monkeypatch.undo()
    assert target.read_bytes() == b"prior"


def test_f14_directory_at_target_is_still_rejected_as_not_a_file(
    tmp_path: pathlib.Path,
) -> None:
    """Regression control: the pre-existing not-a-file refusal is unchanged."""
    target = tmp_path / "note.md"
    target.mkdir()
    with pytest.raises(GraphProjectionError, match=r"^canonical-target-not-file:"):
        gp._promote({target: b"content"})


def test_f14_normal_writes_and_unchanged_skips_still_work(tmp_path: pathlib.Path) -> None:
    """Positive control: legitimate writes are not refused by the new guards."""
    target = tmp_path / "a" / "note.md"
    gp._promote({target: b"hello"})
    assert target.read_bytes() == b"hello"
    gp._promote({target: b"hello"})
    gp._promote({target: b"world"})
    assert target.read_bytes() == b"world"
    assert sorted(p.name for p in target.parent.iterdir()) == ["note.md"]

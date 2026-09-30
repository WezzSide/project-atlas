"""AS-OBSIDIAN-CAPTURE-001-F11 — the mkdir failure site in each writer.

F6 closed the error boundary for two of the ways an atomic note write can fail:
the read of the prior note, and `os.replace`. Creating the note's parent
directory was left outside the guard in **both** writers, so a blocked or
unwritable parent escaped as a raw `OSError` past `ObsidianProjectionError` /
`GraphProjectionError`. A caller catching only the domain error did not catch
this at all, which is the defect F6 exists to prevent, one step earlier in the
same function.

Both sites are recorded in the F6 residual register
(`docs/evidence/AS-OBSIDIAN-CAPTURE-001-F6-ERROR-BOUNDARY.md`): the obsidian one
as "`_write_atomic`'s `mkdir` is outside the new guard", the graph one as the
first of "three raw `OSError`s still escape `write_projection_outputs` from the
unguarded `_promote`: **an ancestor directory replaced by a file**, a read-only
output directory, and a file becoming unreadable between the plan read and
`_promote`'s own read". An earlier revision of this package claimed the second
site "was recorded nowhere"; that is retracted -- the register names it, eight
lines above the bullet the package quoted. The fix is new; the finding was not.

Reproduced on `main` at both sites before fixing, by a plain file sitting where a
directory must be -- a stale or tampered vault, or a path component an operator
created by hand.

**What this does not close.** The other two `_promote` escapes in that register
entry remain raw at this head, both pre-existing and both measured by
verification: a read-only output directory (`staged.write_bytes`) and an
existing target that became unreadable (`path.read_bytes()`). They are different
sites with different failure modes and are tracked separately; this package is
the mkdir class only. So the F6 property is now true at the mkdir site in both
writers -- not "at all three sites", as an earlier revision of this docstring
said.

Scope: an error-boundary fix, not a policy change. The same writes fail; they now
fail inside the module's own exception type, naming the directory. Nothing that
succeeded before is refused now -- verification confirmed that independently
across 67 legitimate scenarios and 960 concurrent writes, with byte-identical
output trees on base and head.
"""

from __future__ import annotations

import pathlib

import pytest

import project_atlas.graph_projections as gp
import project_atlas.obsidian_projection as op
from project_atlas.graph_projections import GraphProjectionError
from project_atlas.obsidian_projection import ObsidianProjectionError


def _blocked_parent(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A vault where a path component ABOVE the note's parent is a FILE."""
    vault = tmp_path / "vault"
    vault.mkdir()
    blocker = vault / "blocked"
    blocker.write_bytes(b"i am a file, not a directory")
    return vault, blocker / "sub" / "note.md"


def _blocked_immediate_parent(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    """A vault where the note's IMMEDIATE parent is a FILE.

    A materially different condition from `_blocked_parent`: on Linux this
    raises `FileExistsError` (errno 17) where the other raises
    `NotADirectoryError` (errno 20). Measured, not assumed -- see
    `_mkdir_error_name`.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    blocker = vault / "blocked"
    blocker.write_bytes(b"i am a file, not a directory")
    return vault, blocker / "note.md"


def _mkdir_error_name(parent: pathlib.Path) -> str:
    """The `OSError` subclass THIS platform raises for THIS blocked parent.

    Measured, never hard-coded. An earlier revision asserted the literal string
    ``"NotADirectoryError"`` and was red on Windows CI, which raises
    ``FileExistsError`` for the same fixture; and on Linux alone the class
    depends on whether the blocking file is an ancestor or the immediate parent.
    The assertion was platform- and shape-coupled from the start.

    Measuring makes it portable *and* keeps it load-bearing: the guard must name
    the true underlying class, so a guard that reported a generic ``OSError`` --
    or the wrong subclass -- still fails.
    """
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return type(exc).__name__
    raise AssertionError(f"fixture is inert: mkdir({parent}) succeeded, so nothing is pinned here")


def test_f11_projection_mkdir_failure_stays_inside_the_error_boundary(
    tmp_path: pathlib.Path,
) -> None:
    vault, target = _blocked_parent(tmp_path)
    expected = _mkdir_error_name(target.parent)
    with pytest.raises(ObsidianProjectionError) as caught:
        op._write_atomic(target, b"content", vault=vault)
    message = str(caught.value)
    assert message.startswith("unwritable-note-directory:")
    assert f":{expected}:" in message, f"guard must name the real class, got {message}"
    assert str(target.parent) in message, "the operator needs the directory named"


def test_f11_graph_mkdir_failure_stays_inside_the_error_boundary(
    tmp_path: pathlib.Path,
) -> None:
    _, target = _blocked_parent(tmp_path)
    expected = _mkdir_error_name(target.parent)
    with pytest.raises(GraphProjectionError) as caught:
        gp._promote({target: b"content"})
    message = str(caught.value)
    assert message.startswith("unwritable-note-directory:")
    assert f":{expected}:" in message, f"guard must name the real class, got {message}"
    assert str(target.parent) in message, "the operator needs the directory named"


def test_f11_containment_is_not_coupled_to_one_blocked_shape(
    tmp_path: pathlib.Path,
) -> None:
    """A second, materially different mkdir failure, in both writers.

    This is the only test here that uses a different fixture. It exists because
    the two above pin one shape whose `OSError` subclass differs by platform: a
    guard that happened to contain only that shape would pass them both. Here the
    immediate parent is the file, which raises a different errno, and the guard
    must still contain it and still name the class correctly.

    Asserted with `pytest.raises` rather than a try/except that only rejects raw
    `OSError`. An earlier revision used the latter and justified it as stronger,
    on the grounds that a domain-type check "would pass on a writer that raised
    nothing at all". That is backwards, and verification demonstrated it with a
    mutant that swallows the mkdir failure and raises nothing: the try/except
    form passed on that mutant; `pytest.raises` failed it with DID NOT RAISE.
    `pytest.raises` also re-raises a non-matching exception, so it rejects a raw
    `OSError` too -- it is the stronger form on both counts.
    """
    vault, target = _blocked_immediate_parent(tmp_path)
    expected = _mkdir_error_name(target.parent)
    for call, domain_error in (
        (lambda: op._write_atomic(target, b"c", vault=vault), ObsidianProjectionError),
        (lambda: gp._promote({target: b"c"}), GraphProjectionError),
    ):
        with pytest.raises(domain_error) as caught:
            call()
        message = str(caught.value)
        assert message.startswith("unwritable-note-directory:")
        assert f":{expected}:" in message


def test_f11_nothing_is_left_behind_when_the_directory_cannot_be_made(
    tmp_path: pathlib.Path,
) -> None:
    """Fail-closed: a refused write leaves no staging residue of any name."""
    vault, target = _blocked_parent(tmp_path)
    before = {p for p in vault.rglob("*")}
    with pytest.raises(ObsidianProjectionError):
        op._write_atomic(target, b"content", vault=vault)
    with pytest.raises(GraphProjectionError):
        gp._promote({target: b"content"})
    assert {p for p in vault.rglob("*")} == before


def test_f11_a_writable_parent_is_still_created_normally(tmp_path: pathlib.Path) -> None:
    """Positive control: the guard must not stop directories being created."""
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "a" / "b" / "note.md"
    op._write_atomic(target, b"hello", vault=vault)
    assert target.read_bytes() == b"hello"
    assert target.parent.is_dir()

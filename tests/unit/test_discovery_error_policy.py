"""M1 -- one OSError policy for every guard in ``discovery.py``.

``_INACCESSIBLE_SCOPE_ERRNOS`` states the contract: an errno that means "this
scope cannot be read" (ENOENT, ENOTDIR, EBADF, ELOOP, EACCES, EPERM) is
reported or recorded as excluded evidence and the run continues; anything
else -- EIO, ENOMEM, ENFILE -- is a fault and must stay visible. Before this
suite the contract was enforced only by the four agent-event guards; the five
guards on the main walk (metadata probe, listability probe, symlink-target
probe, ``stat()``, digest) caught every ``OSError``, so an injected ``EIO``
produced ``skipped unreadable path: ... (Input/output error)`` and exit 0, and
an ``EIO`` while hashing was *recorded* as an ``unreadable`` source -- a
corruption signal filed as a permission problem.

Two-sided: each injection point is exercised with a fault (must propagate)
and with an inaccessible-scope errno (must keep today's observable
continue-behaviour). Errors are injected rather than provoked with mode bits,
so every case runs on every platform and as root.

Which guard catches first is a ``pathlib`` detail: on 3.12 ``is_file()`` goes
through ``Path.stat``, on 3.13 through ``os.path``. The assertions therefore
pin the contract (propagate vs. continue), not the catching line.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import project_atlas.discovery as discovery_module
from project_atlas.discovery import discover
from project_atlas.source_identity import canonical_source_sha256

Injector = Callable[[pytest.MonkeyPatch, Path, OSError], None]


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("# readme\n", encoding="utf-8")
    (root / "docs" / "a.md").write_text("# a\n", encoding="utf-8")
    return root


def _named(path: object, name: str) -> bool:
    return isinstance(path, (str, os.PathLike)) and Path(path).name == name


def _inject_metadata_probe(monkeypatch: pytest.MonkeyPatch, root: Path, exc: OSError) -> None:
    """`is_symlink()` is the walk's first metadata access for every entry."""
    real = Path.is_symlink

    def fake(self: Path) -> bool:
        if self.name == "a.md":
            raise exc
        return real(self)

    monkeypatch.setattr(Path, "is_symlink", fake)


def _inject_listability_probe(monkeypatch: pytest.MonkeyPatch, root: Path, exc: OSError) -> None:
    """`_is_listable` opens the directory with `os.scandir`."""
    real = os.scandir

    def fake(path: Any = ".", *args: Any, **kwargs: Any) -> Any:
        if _named(path, "docs"):
            raise exc
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", fake)


def _inject_symlink_target_probe(monkeypatch: pytest.MonkeyPatch, root: Path, exc: OSError) -> None:
    """`_uninventoried_symlink_target` probes the resolved target with `exists()`."""
    (root / "alias.md").symlink_to(root / "docs" / "a.md")
    real = Path.exists

    def fake(self: Path, *args: Any, **kwargs: Any) -> bool:
        if self.name == "a.md":
            raise exc
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake)


def _inject_stat(monkeypatch: pytest.MonkeyPatch, root: Path, exc: OSError) -> None:
    real = Path.stat

    def fake(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if self.name == "a.md":
            raise exc
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake)


def _inject_digest(monkeypatch: pytest.MonkeyPatch, root: Path, exc: OSError) -> None:
    def fake(path: Path) -> str:
        if path.name == "a.md":
            raise exc
        return canonical_source_sha256(path)

    monkeypatch.setattr("project_atlas.discovery.canonical_source_sha256", fake)


INJECTORS: dict[str, Injector] = {
    "metadata-probe": _inject_metadata_probe,
    "listability-probe": _inject_listability_probe,
    "symlink-target-probe": _inject_symlink_target_probe,
    "stat": _inject_stat,
    "digest": _inject_digest,
}


def _points() -> Iterator[Any]:
    for name, injector in INJECTORS.items():
        marks = []
        if name == "symlink-target-probe" and os.name == "nt":
            marks.append(pytest.mark.skip(reason="symlink creation needs a privilege on Windows"))
        yield pytest.param(injector, id=name, marks=marks)


@pytest.mark.parametrize("inject", list(_points()))
@pytest.mark.parametrize(
    "fault",
    [errno.EIO, errno.ENOMEM, errno.ENFILE],
    ids=lambda code: errno.errorcode[code],
)
def test_fault_errnos_propagate_from_every_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inject: Injector, fault: int
) -> None:
    """A fault is not an unreadable scope: it must abort visibly, not be skipped."""
    root = _source(tmp_path)
    inject(monkeypatch, root, OSError(fault, "simulated fault"))

    with pytest.raises(OSError) as excinfo:
        discover(root)
    assert excinfo.value.errno == fault, "the original fault must surface unchanged"


@pytest.mark.parametrize("inject", list(_points()))
@pytest.mark.parametrize(
    "inaccessible",
    [errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENAMETOOLONG],
    ids=lambda code: errno.errorcode[code],
)
def test_inaccessible_errnos_still_continue_from_every_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    inject: Injector,
    inaccessible: int,
) -> None:
    """Tightening the guards must not reintroduce the aborts they exist to prevent.

    Every inaccessible-scope errno keeps today's behaviour: the run completes,
    the untouched document is still inventoried, and the skip is observable --
    as a diagnostic naming the path, or as an ``unreadable`` record.
    """
    root = _source(tmp_path)
    inject(monkeypatch, root, OSError(inaccessible, "simulated inaccessible scope"))

    with caplog.at_level("WARNING"):
        manifest = discover(root)

    by_path = {record["path"]: record for record in manifest["sources"]}
    assert "README.md" in by_path, "the run continues past the unreadable entry"
    assert by_path["README.md"]["sha256"], "other documents keep their evidence"
    if inject is _inject_digest:
        # The digest guard records the file without content evidence.
        record = by_path["docs/a.md"]
        assert record["sha256"] is None
        assert record["exclusion_reason"] == "unreadable"
    elif inject is _inject_symlink_target_probe:
        # Only the alias probe was unreadable; the real document is untouched.
        assert by_path["docs/a.md"]["sha256"]
        assert "alias.md" not in by_path
    elif inject is _inject_listability_probe:
        # The scope is reported as inaccessible. Whether its children were
        # still yielded is an artifact of the injection, not of the guard:
        # 3.12's `rglob` shares the patched `os.scandir`, 3.13's binds its
        # own reference at import. A really unlistable directory has no
        # children to yield either way (see the portability suite).
        assert any("inaccessible discovery scope" in m and "docs" in m for m in caplog.messages)
    else:
        assert "docs/a.md" not in by_path, "no record without metadata"
        if inaccessible != errno.ENOENT:
            # An entry that vanished (ENOENT) is not a document and is not
            # reported -- `pathlib` answers is_file()/is_dir() False for it.
            # Every other skip is named in a diagnostic.
            assert any("a.md" in m or "docs" in m for m in caplog.messages), caplog.messages


def _grow_past_path_max(root: Path, *, levels: int = 30, width: int = 200) -> None:
    """A real Linux tree whose absolute path exceeds PATH_MAX (4096 bytes).

    Built and torn down through directory descriptors: once past the limit
    no absolute path can name the entries, so neither `Path.mkdir` nor
    pytest's own `tmp_path` cleanup (`shutil.rmtree`) can reach them.
    """
    name = "d" * width
    fd = os.open(root, os.O_RDONLY)
    try:
        for _ in range(levels):
            os.mkdir(name, dir_fd=fd)
            child = os.open(name, os.O_RDONLY, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open("deep.md", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd)
        with os.fdopen(leaf, "w", encoding="utf-8") as handle:
            handle.write("# deep\n")
    finally:
        os.close(fd)


def _remove_past_path_max(root: Path, *, width: int = 200) -> None:
    def remove(parent_fd: int, entry: str) -> None:
        try:
            child = os.open(entry, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd)
        except NotADirectoryError:
            os.unlink(entry, dir_fd=parent_fd)
            return
        try:
            for grandchild in os.listdir(child):
                remove(child, grandchild)
        finally:
            os.close(child)
        os.rmdir(entry, dir_fd=parent_fd)

    fd = os.open(root, os.O_RDONLY)
    try:
        remove(fd, "d" * width)
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name == "nt", reason="PATH_MAX and dir_fd are POSIX semantics")
def test_path_past_path_max_is_skipped_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ENAMETOOLONG is a property of one path, not a fault (IV finding on M1).

    Linux lets a tree grow past PATH_MAX; every metadata call on such an
    entry fails with ENAMETOOLONG. The base skipped it with a diagnostic; the
    first M1 head aborted the run on it because the errno was outside the
    stated set. It belongs inside: the kernel will not address the entry, and
    nothing about the rest of the tree is in doubt.
    """
    root = _source(tmp_path)
    _grow_past_path_max(root)
    try:
        with caplog.at_level("WARNING"):
            manifest = discover(root)
    finally:
        _remove_past_path_max(root)

    by_path = {record["path"]: record for record in manifest["sources"]}
    assert {"README.md", "docs/a.md"} <= set(by_path), "the rest of the tree is inventoried"
    assert not any("deep.md" in path for path in by_path), "the unaddressable entry is not a record"
    skipped = [m for m in caplog.messages if "skipped unreadable path" in m]
    assert skipped, "the skip is observable"
    assert all("\n" not in m for m in skipped)


def test_fault_errnos_propagate_from_agent_event_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four agent-event guards: behavioural coverage, not only structural.

    R5-F already pins `iterdir()`; this pins the `is_dir()` reachability probe
    and the `inspect_event_package` call site.
    """
    root = _source(tmp_path)
    package = root / ".atlas-inbox" / "agent-events" / "proj-a" / "evt-1"
    package.mkdir(parents=True)
    for name in ("event.md", "event.json", "provenance.json", "receipt.yaml"):
        (package / name).write_text("{}\n", encoding="utf-8")

    real_is_dir = Path.is_dir

    def fake_is_dir(self: Path, *args: Any, **kwargs: Any) -> bool:
        if self.name == "proj-a":
            raise OSError(errno.EIO, "simulated fault")
        return real_is_dir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    with pytest.raises(OSError) as excinfo:
        discover(root)
    assert excinfo.value.errno == errno.EIO
    monkeypatch.undo()

    def fake_inspect(*args: Any, **kwargs: Any) -> Any:
        raise OSError(errno.EIO, "simulated fault")

    monkeypatch.setattr("project_atlas.discovery.inspect_event_package", fake_inspect)
    with pytest.raises(OSError) as excinfo:
        discover(root)
    assert excinfo.value.errno == errno.EIO


def test_digest_fault_is_not_recorded_as_unreadable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-fix failure mode, pinned by name.

    An ``EIO`` half-way through hashing used to yield a manifest row with
    ``exclusion_reason == "unreadable"`` and ``sha256 == None`` -- exit 0, and
    a corruption signal filed under the same reason as a mode-000 file.
    """
    root = _source(tmp_path)
    _inject_digest(monkeypatch, root, OSError(errno.EIO, "simulated I/O error"))

    with pytest.raises(OSError) as excinfo:
        discover(root)
    assert excinfo.value.errno == errno.EIO


def test_every_oserror_guard_in_discovery_filters_by_errno() -> None:
    """Structural pin: no `except OSError` in the module may be a blanket catch.

    Counted against the source so a future guard added without the filter
    fails here rather than silently widening the policy again. The one
    exception is the project-marker reader, which converts *every* read
    failure into a fail-closed ``INVALID_PROJECT_MARKER`` rather than
    continuing -- the opposite of absorbing.
    """
    import inspect

    source = inspect.getsource(discovery_module)
    lines = source.splitlines()
    blanket: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("except") or "OSError" not in stripped:
            continue
        window = "\n".join(lines[index : index + 3])
        if "_is_inaccessible_scope(exc)" in window:
            continue
        if "raise ValueError" in "\n".join(lines[index : index + 5]):
            continue  # fail-closed conversion, not absorption
        blanket.append(index + 1)
    assert blanket == [], f"blanket `except OSError` at lines {blanket}"

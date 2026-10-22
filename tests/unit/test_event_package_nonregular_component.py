"""Event-package components that are not regular files must be refused.

#713: `_sha256` opened every required component by name. A FIFO under a
component name hung the reader indefinitely. A directory or socket raised
a raw `OSError` that escaped `inspect_event_package`, which only catches
`PackageValidationError` / `ValueError`.

The fix is a `lstat` + `S_ISREG` predicate before any open. That is file
*type* safety. It does not close hardlinked regular files (#712) and it
does not own symlink-component policy (#694).
"""

from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

import pytest

from atlas_contracts import event_package
from atlas_contracts.event_package import (
    EVENT_PACKAGE_FILES,
    PackageValidationError,
    _raw_inventory,
    inspect_event_package,
)

PACKAGE_PATH = ".atlas-inbox/agent-events/proj/evt-1"
PROJECT_ID = "proj"
EVENT_ID = "AE-evt-1"


def _package(root: Path, *, relative: str = PACKAGE_PATH) -> Path:
    package = root.joinpath(*relative.split("/"))
    package.mkdir(parents=True)
    return package


def _fill(package: Path, *, skip: str | None = None) -> None:
    for name in sorted(EVENT_PACKAGE_FILES):
        if name != skip:
            (package / name).write_text("{}\n", encoding="utf-8")


def _alarm_timeout(seconds: int) -> None:
    """Fail the test if a hang is not converted into a governed refusal."""
    import signal

    def _handler(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"component reader hung for {seconds}s")

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)


def _clear_alarm() -> None:
    import signal

    signal.alarm(0)


def test_fifo_component_is_refused_not_hung(tmp_path: Path) -> None:
    """The alarm *is* the assertion: without the predicate this never returns."""
    if not hasattr(os, "mkfifo") or not hasattr(__import__("signal"), "SIGALRM"):
        pytest.skip("os.mkfifo / SIGALRM unavailable on this platform")

    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    os.mkfifo(package / "event.md")

    _alarm_timeout(5)
    try:
        with pytest.raises(PackageValidationError, match=r"not a regular file: event.md \(fifo\)"):
            _raw_inventory(root, PACKAGE_PATH)
    finally:
        _clear_alarm()


def test_directory_component_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    (package / "event.md").mkdir()

    with pytest.raises(
        PackageValidationError, match=r"not a regular file: event.md \(directory\)"
    ):
        _raw_inventory(root, PACKAGE_PATH)


def test_socket_component_is_refused(tmp_path: Path) -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX sockets unavailable on this platform")

    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    sock_path = package / "event.md"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(os.fspath(sock_path))
        with pytest.raises(
            PackageValidationError, match=r"not a regular file: event.md \(socket\)"
        ):
            _raw_inventory(root, PACKAGE_PATH)
    except OSError as exc:
        pytest.skip(f"AF_UNIX bind not permitted: {exc}")
    finally:
        server.close()


def test_refusal_is_governed_not_raw_oserror(tmp_path: Path) -> None:
    """inspect_event_package must see a refusal, not an escaping OSError.

    A complete-looking pipeline in event.json would otherwise flip the
    inventory to ``pending``. The type refusal must still win as
    ``invalid`` so the operator sees a governed verdict, not a crash.
    """
    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    (package / "event.md").mkdir()
    (package / "event.json").write_text(
        '{"pipeline":{"captured":true,"normalized":true,"verified":true,"routed":true}}\n',
        encoding="utf-8",
    )

    inventory = inspect_event_package(root, PROJECT_ID, EVENT_ID, PACKAGE_PATH)
    assert inventory.status == "invalid"
    assert inventory.errors
    assert "not a regular file" in inventory.errors[0]


def test_refusal_happens_before_any_component_is_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    (package / "event.md").mkdir()
    hashed: list[Path] = []
    real_sha256 = event_package._sha256

    def _spy(path: Path) -> str:
        hashed.append(Path(path))
        return real_sha256(path)

    monkeypatch.setattr(event_package, "_sha256", _spy)

    with pytest.raises(PackageValidationError):
        _raw_inventory(root, PACKAGE_PATH)

    assert hashed == [], (
        "the type refusal did not precede reading: "
        + f"{[str(path) for path in hashed]} were hashed"
    )


def test_control_ordinary_package_still_loads(tmp_path: Path) -> None:
    root = tmp_path / "source"
    package = _package(root)
    _fill(package)
    _path, hashes = _raw_inventory(root, PACKAGE_PATH)
    assert set(hashes) == EVENT_PACKAGE_FILES
    assert all(len(digest) == 64 for digest in hashes.values())


def test_device_component_is_refused(tmp_path: Path) -> None:
    if not hasattr(os, "mknod"):
        pytest.skip("os.mknod unavailable on this platform")

    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    try:
        os.mknod(package / "event.md", stat.S_IFCHR | 0o644)
    except (OSError, PermissionError, AttributeError) as exc:
        pytest.skip(f"mknod not permitted: {exc}")

    with pytest.raises(
        PackageValidationError,
        match=r"not a regular file: event.md \(character-device\)",
    ):
        _raw_inventory(root, PACKAGE_PATH)


def test_hardlinked_component_is_not_closed_by_this_predicate(tmp_path: Path) -> None:
    """S_ISREG does not reach #712. This control must stay green until #712.

    A hardlink to a regular file *is* a regular file. If a later change
    starts refusing it, that is an identity-policy change and belongs to
    #712, not this file-type package.
    """
    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip="event.md")
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE-BYTES-STILL-ACCEPTED-BY-713\n", encoding="utf-8")
    try:
        os.link(outside, package / "event.md")
    except OSError as exc:
        pytest.skip(f"os.link not permitted: {exc}")

    _path, hashes = _raw_inventory(root, PACKAGE_PATH)
    assert hashes["event.md"] == event_package._sha256(outside)


@pytest.mark.parametrize("component", sorted(EVENT_PACKAGE_FILES))
def test_each_component_directory_is_refused(tmp_path: Path, component: str) -> None:
    root = tmp_path / "source"
    package = _package(root)
    _fill(package, skip=component)
    (package / component).mkdir()

    with pytest.raises(
        PackageValidationError,
        match=rf"not a regular file: {component} \(directory\)",
    ):
        _raw_inventory(root, PACKAGE_PATH)

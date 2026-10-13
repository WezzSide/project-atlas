"""Domain F (atomic I/O) and Domain G (file lock / open-handle semantics).

Every claim here is qualified: "ATOMICITY OBSERVED" (this specific
mechanism, on this filesystem, this run) never "ATOMICITY PROVEN" in
general. Sharing-violation behavior is stated as measured, with the
limitation that Windows sharing flags are not fully under Python's control
noted explicitly where relevant.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path

from windows_conformance.model import ProbeOutcome

_IO_CONTRACT = (
    "Atomic-write code (os.replace-based) must have its atomicity claims backed "
    "by an actual observed experiment on this platform, not carried over from "
    "POSIX rename() semantics -- os.rename() and os.replace() are NOT "
    "interchangeable on Windows the way they are on POSIX."
)
_LOCK_CONTRACT = (
    "Atlas code that replaces/deletes/renames a file another handle may have "
    "open must know the actual Windows sharing-violation behavior it will hit "
    "-- not assume POSIX's 'delete-while-open still works, readers see the old "
    "inode' semantics."
)


def probe_win_io_001_replace_overwrites_existing_target() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-io-"))
    try:
        dst = scratch / "target.txt"
        dst.write_text("old", encoding="utf-8")
        src = scratch / "new.tmp"
        src.write_text("new", encoding="utf-8")
        try:
            os.replace(src, dst)
            ok, detail = True, "succeeded"
        except OSError as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        content = dst.read_text(encoding="utf-8") if dst.is_file() else None
        return ProbeOutcome(
            "WIN-IO-001", "atomic_io", _IO_CONTRACT,
            f"os.replace() onto an existing target: {detail}; final content={content!r}",
            "PASS" if ok and content == "new" else "FAIL", "OBSERVED",
            evidence={"replace_succeeded": ok, "final_content": content},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_io_002_rename_onto_existing_target_differs_from_replace() -> ProbeOutcome:
    """The classic POSIX/Windows divergence: os.rename() onto an existing
    file silently replaces it on POSIX, but raises FileExistsError on
    Windows. Measure the actual exception class, don't hard-code an
    assumption (the F6/F7/F10 lesson from prior Windows regression work)."""
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-io-"))
    try:
        dst = scratch / "target.txt"
        dst.write_text("old", encoding="utf-8")
        src = scratch / "new.tmp"
        src.write_text("new", encoding="utf-8")
        error_class = None
        try:
            os.rename(src, dst)
            succeeded = True
        except OSError as exc:
            succeeded = False
            error_class = type(exc).__name__
        return ProbeOutcome(
            "WIN-IO-002", "atomic_io", _IO_CONTRACT,
            (
                f"os.rename() onto an existing target: succeeded={succeeded}"
                + (f", raised {error_class}" if error_class else "")
            ),
            "PASS", "OBSERVED",
            evidence={"rename_succeeded": succeeded, "error_class": error_class},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_io_003_replace_read_only_target() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-io-"))
    try:
        dst = scratch / "target.txt"
        dst.write_text("old", encoding="utf-8")
        os.chmod(dst, stat.S_IREAD)
        src = scratch / "new.tmp"
        src.write_text("new", encoding="utf-8")
        try:
            os.replace(src, dst)
            ok, detail = True, "succeeded"
        except OSError as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        finally:
            with __import__("contextlib").suppress(OSError):
                os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
        return ProbeOutcome(
            "WIN-IO-003", "atomic_io", _IO_CONTRACT,
            f"os.replace() onto a read-only target: {detail}",
            "PASS", "OBSERVED",
            evidence={"replace_succeeded": ok, "detail": detail},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_io_004_replace_missing_parent() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-io-"))
    try:
        src = scratch / "src.tmp"
        src.write_text("x", encoding="utf-8")
        dst = scratch / "no_such_dir" / "target.txt"
        error_class = None
        try:
            os.replace(src, dst)
            succeeded = True
        except OSError as exc:
            succeeded = False
            error_class = type(exc).__name__
        return ProbeOutcome(
            "WIN-IO-004", "atomic_io", _IO_CONTRACT,
            f"os.replace() with a missing parent directory: succeeded={succeeded}, raised={error_class}",
            "PASS" if not succeeded and error_class == "FileNotFoundError" else "FAIL",
            "OBSERVED",
            evidence={"succeeded": succeeded, "error_class": error_class},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_io_005_file_vs_directory_collision() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-io-"))
    try:
        dst_dir = scratch / "target"
        dst_dir.mkdir()
        src_file = scratch / "src.tmp"
        src_file.write_text("x", encoding="utf-8")
        error_class = None
        try:
            os.replace(src_file, dst_dir)
            succeeded = True
        except OSError as exc:
            succeeded = False
            error_class = type(exc).__name__
        return ProbeOutcome(
            "WIN-IO-005", "atomic_io", _IO_CONTRACT,
            f"os.replace(file, existing_directory): succeeded={succeeded}, raised={error_class}",
            "PASS" if not succeeded else "FAIL",
            "OBSERVED",
            evidence={"succeeded": succeeded, "error_class": error_class},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_io_006_concurrent_replace_race() -> ProbeOutcome:
    """Two threads race os.replace() onto the same destination. Requires
    only that the final state is exactly one of the two writers' content
    (no corruption/interleaving/partial write) -- not a specific winner."""
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-io-"))
    try:
        dst = scratch / "target.txt"
        dst.write_text("initial", encoding="utf-8")
        payload_a = "A" * 4096
        payload_b = "B" * 4096
        errors: list[str] = []

        def _writer(payload: str, tag: str) -> None:
            tmp = scratch / f"tmp_{tag}.txt"
            tmp.write_text(payload, encoding="utf-8")
            try:
                os.replace(tmp, dst)
            except OSError as exc:  # pragma: no cover -- recorded, not asserted away
                errors.append(f"{tag}: {type(exc).__name__}: {exc}")

        t1 = threading.Thread(target=_writer, args=(payload_a, "a"))
        t2 = threading.Thread(target=_writer, args=(payload_b, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        final = dst.read_text(encoding="utf-8")
        clean_win = final in (payload_a, payload_b)
        return ProbeOutcome(
            "WIN-IO-006", "atomic_io", _IO_CONTRACT,
            (
                f"concurrent os.replace() race: final content is "
                f"{'exactly one writer payload (no corruption)' if clean_win else 'CORRUPTED/mixed'}; "
                f"writer errors={errors}"
            ),
            "PASS" if clean_win and not errors else "FAIL", "OBSERVED",
            evidence={"final_is_clean_single_writer": clean_win, "writer_errors": errors, "final_len": len(final)},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_lock_001_replace_while_target_open_for_read() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-lock-"))
    try:
        dst = scratch / "target.txt"
        dst.write_text("old", encoding="utf-8")
        src = scratch / "new.tmp"
        src.write_text("new", encoding="utf-8")
        handle = open(dst, "rb")  # noqa: SIM115 -- deliberately held open across the probe
        try:
            try:
                os.replace(src, dst)
                succeeded = True
                error_class = None
            except OSError as exc:
                succeeded = False
                error_class = type(exc).__name__
        finally:
            handle.close()
        return ProbeOutcome(
            "WIN-LOCK-001", "file_lock_semantics", _LOCK_CONTRACT,
            (
                f"os.replace() while destination open for read (via a plain "
                f"open() handle, no explicit share-flags control): succeeded={succeeded}"
                + (f", raised={error_class}" if error_class else "")
                + ". Limitation: Python's open() on Windows requests default "
                "sharing (FILE_SHARE_READ|WRITE|DELETE) -- this does not "
                "characterize every possible sharing-mode combination."
            ),
            "PASS", "OBSERVED",
            evidence={"replace_succeeded": succeeded, "error_class": locals().get("error_class")},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_lock_002_delete_while_open() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-lock-"))
    try:
        target = scratch / "target.txt"
        target.write_text("x", encoding="utf-8")
        handle = open(target, "rb")  # noqa: SIM115
        try:
            try:
                target.unlink()
                succeeded = True
                error_class = None
            except OSError as exc:
                succeeded = False
                error_class = type(exc).__name__
        finally:
            handle.close()
        return ProbeOutcome(
            "WIN-LOCK-002", "file_lock_semantics", _LOCK_CONTRACT,
            f"unlink() while open for read: succeeded={succeeded}, raised={error_class}",
            "PASS", "OBSERVED",
            evidence={"unlink_succeeded": succeeded, "error_class": error_class},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_lock_003_rename_while_open() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-lock-"))
    try:
        target = scratch / "target.txt"
        target.write_text("x", encoding="utf-8")
        new_name = scratch / "renamed.txt"
        handle = open(target, "rb")  # noqa: SIM115
        try:
            try:
                target.rename(new_name)
                succeeded = True
                error_class = None
            except OSError as exc:
                succeeded = False
                error_class = type(exc).__name__
        finally:
            handle.close()
        return ProbeOutcome(
            "WIN-LOCK-003", "file_lock_semantics", _LOCK_CONTRACT,
            f"rename() while open for read: succeeded={succeeded}, raised={error_class}",
            "PASS", "OBSERVED",
            evidence={"rename_succeeded": succeeded, "error_class": error_class},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


PROBES = [
    probe_win_io_001_replace_overwrites_existing_target,
    probe_win_io_002_rename_onto_existing_target_differs_from_replace,
    probe_win_io_003_replace_read_only_target,
    probe_win_io_004_replace_missing_parent,
    probe_win_io_005_file_vs_directory_collision,
    probe_win_io_006_concurrent_replace_race,
    probe_win_lock_001_replace_while_target_open_for_read,
    probe_win_lock_002_delete_while_open,
    probe_win_lock_003_rename_while_open,
]

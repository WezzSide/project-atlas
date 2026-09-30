"""Domain E: links / reparse points.

No administrator privileges required. Where a capability cannot be
created under the current account (e.g. symlinks without Developer Mode /
SeCreateSymbolicLinkPrivilege), the probe reports NOT_TESTED/UNKNOWN --
never PASS.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from windows_conformance import procutil
from windows_conformance.model import ProbeOutcome

_CONTRACT = (
    "Atlas code that reasons about symlinks/junctions/hardlinks on Windows must "
    "use what Path.resolve()/is_symlink()/stat() actually expose on this host, "
    "not POSIX-derived assumptions about a single 'link' concept."
)


def probe_win_link_001_file_symlink() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-link-"))
    try:
        target = scratch / "target.txt"
        target.write_text("real", encoding="utf-8")
        link = scratch / "link.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            return ProbeOutcome(
                "WIN-LINK-001", "links_reparse_points", _CONTRACT,
                f"symlink_to() failed (likely no SeCreateSymbolicLinkPrivilege): {type(exc).__name__}: {exc}",
                "NOT_TESTED", "UNKNOWN",
                evidence={"creation_error": str(exc)},
            )
        is_symlink = link.is_symlink()
        resolves_to_target = link.resolve() == target.resolve()
        same_file = link.samefile(target)
        return ProbeOutcome(
            "WIN-LINK-001", "links_reparse_points", _CONTRACT,
            f"file symlink: is_symlink={is_symlink}, resolve()->target={resolves_to_target}, samefile={same_file}",
            "PASS", "OBSERVED",
            evidence={"is_symlink": is_symlink, "resolve_matches_target": resolves_to_target, "samefile": same_file},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_link_002_directory_symlink() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-link-"))
    try:
        target_dir = scratch / "target_dir"
        target_dir.mkdir()
        (target_dir / "f.txt").write_text("x", encoding="utf-8")
        link_dir = scratch / "link_dir"
        try:
            link_dir.symlink_to(target_dir, target_is_directory=True)
        except OSError as exc:
            return ProbeOutcome(
                "WIN-LINK-002", "links_reparse_points", _CONTRACT,
                f"directory symlink_to() failed: {type(exc).__name__}: {exc}",
                "NOT_TESTED", "UNKNOWN",
                evidence={"creation_error": str(exc)},
            )
        is_symlink = link_dir.is_symlink()
        is_dir = link_dir.is_dir()
        file_visible = (link_dir / "f.txt").is_file()
        return ProbeOutcome(
            "WIN-LINK-002", "links_reparse_points", _CONTRACT,
            f"dir symlink: is_symlink={is_symlink}, is_dir={is_dir}, child_visible_through_link={file_visible}",
            "PASS", "OBSERVED",
            evidence={"is_symlink": is_symlink, "is_dir": is_dir, "child_visible_through_link": file_visible},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_link_003_ntfs_junction() -> ProbeOutcome:
    """NTFS junctions (mklink /J) need no elevated privilege, unlike
    symlinks -- and Path.is_symlink() historically does NOT report True for
    them prior to Python 3.12's reparse-point awareness; observe directly
    rather than assume either way."""
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-LINK-003", "links_reparse_points", _CONTRACT, "not on Windows", "NOT_APPLICABLE", "OBSERVED"
        )
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-link-"))
    try:
        target_dir = scratch / "jtarget"
        target_dir.mkdir()
        (target_dir / "f.txt").write_text("x", encoding="utf-8")
        junction = scratch / "jlink"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(target_dir)],
            capture_output=True, text=True, check=False,
            creationflags=procutil.NO_WINDOW,
        )
        if result.returncode != 0 or not junction.exists():
            return ProbeOutcome(
                "WIN-LINK-003", "links_reparse_points", _CONTRACT,
                f"mklink /J failed: {(result.stderr or result.stdout).strip()}",
                "NOT_TESTED", "UNKNOWN",
                evidence={"mklink_stderr": result.stderr, "mklink_stdout": result.stdout},
            )
        is_symlink = junction.is_symlink()
        is_junction = junction.is_junction() if hasattr(junction, "is_junction") else None
        is_dir = junction.is_dir()
        file_visible = (junction / "f.txt").is_file()
        return ProbeOutcome(
            "WIN-LINK-003", "links_reparse_points", _CONTRACT,
            (
                f"NTFS junction: is_symlink()={is_symlink}, "
                f"is_junction()={'n/a (Python<3.12)' if is_junction is None else is_junction}, "
                f"is_dir={is_dir}, child_visible={file_visible}"
            ),
            "PASS", "OBSERVED",
            evidence={
                "is_symlink": is_symlink,
                "is_junction": is_junction,
                "is_dir": is_dir,
                "child_visible_through_junction": file_visible,
            },
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_link_004_hardlink() -> ProbeOutcome:
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-link-"))
    try:
        target = scratch / "hltarget.txt"
        target.write_text("real", encoding="utf-8")
        hardlink = scratch / "hardlink.txt"
        try:
            os.link(target, hardlink)
        except OSError as exc:
            return ProbeOutcome(
                "WIN-LINK-004", "links_reparse_points", _CONTRACT,
                f"os.link() failed: {type(exc).__name__}: {exc}",
                "NOT_TESTED", "UNKNOWN",
                evidence={"creation_error": str(exc)},
            )
        is_symlink = hardlink.is_symlink()
        same_file = hardlink.samefile(target)
        target.write_text("modified via original", encoding="utf-8")
        content_shared = hardlink.read_text(encoding="utf-8") == "modified via original"
        return ProbeOutcome(
            "WIN-LINK-004", "links_reparse_points", _CONTRACT,
            f"hardlink: is_symlink={is_symlink} (must be False), samefile={same_file}, content_shared={content_shared}",
            "PASS" if not is_symlink and same_file and content_shared else "FAIL",
            "OBSERVED",
            evidence={"is_symlink": is_symlink, "samefile": same_file, "content_shared": content_shared},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


PROBES = [
    probe_win_link_001_file_symlink,
    probe_win_link_002_directory_symlink,
    probe_win_link_003_ntfs_junction,
    probe_win_link_004_hardlink,
]

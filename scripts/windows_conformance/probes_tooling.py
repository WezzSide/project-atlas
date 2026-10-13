"""Domain H (executable resolution) and Domain I (import provenance)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from windows_conformance import procutil
from windows_conformance.model import ProbeOutcome

_TOOL_CONTRACT = (
    "A bare executable name resolved via PATH must not be assumed to be the "
    "one belonging to the current worktree/venv -- Atlas tooling that needs "
    "'the current checkout's own CLI' must resolve it explicitly, never rely "
    "on PATH order alone (the exact class of defect previously observed: a "
    "bare `atlas` on PATH silently executing a stale editable install)."
)
_IMPORT_CONTRACT = (
    "project_atlas.__file__ must resolve under the intended worktree in both "
    "the parent process and any subprocess it spawns -- never assumed from a "
    "successful `pip install -e` alone."
)


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def probe_win_tool_001_sys_executable_identity() -> ProbeOutcome:
    exe = Path(sys.executable)
    return ProbeOutcome(
        "WIN-TOOL-001", "tool_resolution", _TOOL_CONTRACT,
        f"sys.executable resolved: exists={exe.is_file()}, digest16={_digest(exe)}",
        "PASS" if exe.is_file() else "FAIL", "OBSERVED",
        evidence={"exists": exe.is_file(), "digest16": _digest(exe), "name": exe.name},
    )


def probe_win_tool_002_python_on_path_vs_sys_executable() -> ProbeOutcome:
    on_path = shutil.which("python")
    same = on_path is not None and Path(on_path).resolve() == Path(sys.executable).resolve()
    return ProbeOutcome(
        "WIN-TOOL-002", "tool_resolution", _TOOL_CONTRACT,
        (
            f"`python` on PATH resolves to {on_path!r}; "
            f"{'SAME as' if same else 'DIFFERENT from'} sys.executable "
            f"({sys.executable!r}) -- code must not assume they match"
        ),
        "PASS", "OBSERVED",
        evidence={"path_python_matches_sys_executable": same, "on_path": bool(on_path)},
    )


def probe_win_tool_003_atlas_cli_provenance() -> ProbeOutcome:
    venv_scripts = Path(sys.executable).parent
    expected_atlas = venv_scripts / "atlas.exe"
    on_path = shutil.which("atlas")
    matches_this_venv = (
        on_path is not None and Path(on_path).resolve() == expected_atlas.resolve()
    )
    return ProbeOutcome(
        "WIN-TOOL-003", "tool_resolution", _TOOL_CONTRACT,
        (
            f"`atlas` on PATH resolves to {on_path!r}; this venv's own atlas.exe "
            f"is {expected_atlas!r} -- {'SAME' if matches_this_venv else 'DIFFERENT (PATH shadowing risk if code relies on bare `atlas`)'}"
        ),
        "PASS", "OBSERVED",
        evidence={
            "path_atlas_matches_this_venv": matches_this_venv,
            "on_path_found": bool(on_path),
            "this_venv_atlas_exists": expected_atlas.is_file(),
        },
    )


def probe_win_tool_004_git_pwsh_resolution() -> ProbeOutcome:
    findings = {}
    for tool in ("git", "pwsh", "powershell"):
        where = shutil.which(tool)
        version = None
        if where:
            try:
                result = subprocess.run(
                    [where, "--version"], capture_output=True, text=True, timeout=10, check=False,
                    creationflags=procutil.NO_WINDOW,
                )
                version = (result.stdout or result.stderr or "").strip().splitlines()[0] if (result.stdout or result.stderr) else None
            except (OSError, subprocess.TimeoutExpired, IndexError):
                version = None
        findings[tool] = {"resolved": where, "version": version}
    all_resolved = all(v["resolved"] for v in findings.values())
    return ProbeOutcome(
        "WIN-TOOL-004", "tool_resolution", _TOOL_CONTRACT,
        f"git/pwsh/powershell resolution: {[(k, bool(v['resolved'])) for k, v in findings.items()]}",
        "PASS" if all_resolved else "NOT_TESTED",
        "OBSERVED" if all_resolved else "UNKNOWN",
        evidence=findings,
    )


def probe_win_import_001_parent_provenance() -> ProbeOutcome:
    import project_atlas  # local import: this is the whole point of the probe

    resolved = Path(project_atlas.__file__).resolve()
    here = Path(__file__).resolve()
    # scripts/windows_conformance/probes_tooling.py -> repo root is 2 parents up from scripts/
    repo_root = here.parents[2]
    under_repo = str(resolved).lower().startswith(str(repo_root).lower())
    return ProbeOutcome(
        "WIN-IMPORT-001", "import_provenance", _IMPORT_CONTRACT,
        f"parent process project_atlas.__file__ under this worktree: {under_repo}",
        "PASS" if under_repo else "FAIL", "OBSERVED",
        evidence={"under_repo": under_repo},
    )


def probe_win_import_002_child_process_provenance() -> ProbeOutcome:
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "import project_atlas,pathlib;print(pathlib.Path(project_atlas.__file__).resolve())"],
        capture_output=True, text=True, timeout=30, check=False,
        creationflags=procutil.NO_WINDOW,
    )
    child_path = (result.stdout or "").strip()
    under_repo = child_path.lower().startswith(str(repo_root).lower())
    return ProbeOutcome(
        "WIN-IMPORT-002", "import_provenance", _IMPORT_CONTRACT,
        f"subprocess project_atlas.__file__ under this worktree: {under_repo} (returncode={result.returncode})",
        "PASS" if under_repo and result.returncode == 0 else "FAIL", "OBSERVED",
        evidence={"under_repo": under_repo, "returncode": result.returncode, "stderr_tail": (result.stderr or "")[-300:]},
    )


def probe_win_import_003_contamination_negative_control() -> ProbeOutcome:
    """Deliberate negative control: point a subprocess's PYTHONPATH at a
    scratch fixture package (never the user's global environment) placed
    *before* this worktree's src on the path, and confirm the provenance
    check the way WIN-IMPORT-002 does it would actually notice -- proving
    the guard can detect contamination rather than always reporting PASS."""
    import os

    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-import-"))
    try:
        fake_pkg = scratch / "project_atlas"
        fake_pkg.mkdir()
        (fake_pkg / "__init__.py").write_text("__file__ # contamination fixture\n", encoding="utf-8")
        here = Path(__file__).resolve()
        repo_root = here.parents[2]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(scratch) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", "import project_atlas,pathlib;print(pathlib.Path(project_atlas.__file__).resolve())"],
            capture_output=True, text=True, timeout=30, check=False, env=env,
            creationflags=procutil.NO_WINDOW,
        )
        child_path = (result.stdout or "").strip()
        under_repo = child_path.lower().startswith(str(repo_root).lower())
        under_scratch = child_path.lower().startswith(str(scratch).lower())
        guard_detected_contamination = under_scratch and not under_repo
        return ProbeOutcome(
            "WIN-IMPORT-003", "import_provenance", _IMPORT_CONTRACT,
            (
                f"negative control: with a contaminating PYTHONPATH entry prepended, "
                f"the child resolved project_atlas to the scratch fixture (not this "
                f"worktree): {guard_detected_contamination} -- proves the same "
                f"under_repo check used by WIN-IMPORT-001/002 is load-bearing, not "
                f"trivially always-true"
            ),
            "PASS" if guard_detected_contamination else "FAIL", "OBSERVED",
            evidence={"under_scratch": under_scratch, "under_repo": under_repo},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


PROBES = [
    probe_win_tool_001_sys_executable_identity,
    probe_win_tool_002_python_on_path_vs_sys_executable,
    probe_win_tool_003_atlas_cli_provenance,
    probe_win_tool_004_git_pwsh_resolution,
    probe_win_import_001_parent_provenance,
    probe_win_import_002_child_process_provenance,
    probe_win_import_003_contamination_negative_control,
]

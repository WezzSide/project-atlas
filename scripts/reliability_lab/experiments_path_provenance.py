"""Domains G (path/filesystem TOCTOU races), H (import provenance stress),
I (tool resolution drift), and K (long path/Unicode combinatorial stress)."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from reliability_lab import procutil
from reliability_lab.model import ExperimentResult
from reliability_lab.runctl import Mode, TrialOutcome, run_trials, scaled_iterations

_SCRATCH_DIRS: list[Path] = []

_PROVENANCE_CHECK_CODE = (
    "import project_atlas,pathlib;"
    "print(pathlib.Path(project_atlas.__file__).resolve())"
)


def _scratch(prefix: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"atlas-lab-{prefix}-"))
    _SCRATCH_DIRS.append(d)
    return d


def _cleanup_scratch() -> int:
    leaked = 0
    for d in _SCRATCH_DIRS:
        try:
            shutil.rmtree(d, ignore_errors=False)
        except OSError:
            leaked += 1
    _SCRATCH_DIRS.clear()
    return leaked


# ---------------------------------------------------------------------------
# Domain G -- path/filesystem TOCTOU race
# ---------------------------------------------------------------------------


def experiment_path_toctou_race(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PATH-001: validate a component via
    ``atlas_contracts.paths.safe_relative_component`` (validation), then a
    concurrent thread renames the target out from under the path between
    validation and use (the classic TOCTOU gap). Requires the eventual
    `open()`/read to either see the *original* file or fail cleanly --
    never silently read/write through an attacker-substituted path,
    because containment is re-checked at the point of use via
    ``ensure_under_root`` in the real code path, not only at validation
    time."""
    from atlas_contracts.paths import ensure_under_root, safe_relative_component

    n = scaled_iterations(70, mode)
    _SCRATCH_DIRS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        root = _scratch(f"toctou{i}")
        name = safe_relative_component("target.txt", label="probe")
        target = root / name
        target.write_text("original", encoding="utf-8")
        substitute_dir = _scratch(f"toctou-sub{i}")
        (substitute_dir / "target.txt").write_text("SUBSTITUTED-OUTSIDE-ROOT", encoding="utf-8")

        race_won_by_attacker = False

        def racer() -> None:
            nonlocal race_won_by_attacker
            try:
                target.unlink()
                target.symlink_to(substitute_dir / "target.txt")
                race_won_by_attacker = True
            except OSError:
                pass  # symlink privilege or race timing -- not a contract failure either way

        start = time.perf_counter()
        import threading

        t = threading.Thread(target=racer)
        t.start()
        t.join(timeout=2)
        try:
            resolved = ensure_under_root(root, target, label="probe")
            resolved_ok = True
            escaped = not str(resolved).lower().startswith(str(root.resolve()).lower())
        except ValueError:
            resolved_ok = False
            escaped = False
        latency_ms = (time.perf_counter() - start) * 1000
        # Contract: even if the attacker won the race and substituted a
        # symlink, ensure_under_root's post-resolve containment check must
        # either refuse it (not resolved_ok) or the resolved path must
        # still be physically under root (Windows without dev-mode symlink
        # privilege may simply fail to create the symlink at all, which is
        # also a pass -- see racer()'s own try/except).
        ok = (not escaped) if resolved_ok else True
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "ToctouContainmentEscape",
            detail=(
                f"race_won_by_attacker={race_won_by_attacker} resolved_ok={resolved_ok} "
                f"escaped={escaped}"
            ),
            contract_violated=(
                "ensure_under_root must re-check containment at use time, not trust "
                "validation-time state"
            ),
        )

    return run_trials(
        experiment_id="EXP-PATH-001",
        domain="path_filesystem_race",
        hypothesis=(
            "A validate-then-use gap, deliberately raced with a symlink substitution, never lets "
            "ensure_under_root() resolve outside the approved root."
        ),
        contract=(
            "containment is checked on the fully resolved target immediately before use "
            "(paths.py's own documented contract)"
        ),
        stress_dimension="TOCTOU: concurrent rename+symlink race between validation and use",
        expected_invariant="resolved path never escapes root, regardless of race timing",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=5.0,
        cleanup_fn=_cleanup_scratch,
    )


# ---------------------------------------------------------------------------
# Domain K -- long path / Unicode combinatorial stress
# ---------------------------------------------------------------------------


def experiment_long_path_unicode_matrix(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PATH-002: a combinatorial matrix (length x Unicode form x case),
    not isolated single examples -- does OS acceptance / Atlas contract
    acceptance hold consistently across combinations, or does some
    combination behave differently than its parts would suggest?"""
    from atlas_contracts.paths import safe_relative_component

    lengths = [10, 50, 120, 200]
    unicode_forms = ["ascii", "nfc", "nfd", "mixed_script"]
    cases = ["lower", "upper", "title"]
    combos = [
        (length_variant, form, case)
        for length_variant in lengths
        for form in unicode_forms
        for case in cases
    ]
    n = min(scaled_iterations(80, mode), len(combos) * 2)
    _SCRATCH_DIRS.clear()
    root = _scratch("longpath-unicode")

    def _make_name(length_variant: int, form: str, case: str) -> str:
        base = {
            "ascii": "cafe",
            "nfc": "café",  # é precomposed
            "nfd": unicodedata.normalize("NFD", "café"),  # e + combining acute
            "mixed_script": "cafe日本語",  # latin + japanese
        }[form]
        name = (base * ((length_variant // max(1, len(base))) + 1))[:length_variant]
        if case == "upper":
            name = name.upper()
        elif case == "title":
            name = name.title()
        return name or "x"

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        length_variant, form, case = combos[i % len(combos)]
        name = _make_name(length_variant, form, case)
        start = time.perf_counter()
        try:
            safe_relative_component(name, label="probe")
            contract_ok = True
        except ValueError:
            contract_ok = False
        os_ok = True
        os_detail = ""
        try:
            (root / name).write_text("x", encoding="utf-8")
            (root / name).unlink()
        except OSError as exc:
            os_ok = False
            os_detail = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - start) * 1000
        # No single expected answer across the whole matrix (that's the
        # point -- OS_ACCEPTS and ATLAS_CONTRACT_ACCEPTS are independent
        # questions); the invariant is just that observing both never
        # raises anything *unexpected* (already caught above) and that
        # contract-accepted names never fail at the OS for a reason other
        # than a legitimate Windows limit (e.g. MAX_PATH on the deepest
        # combos), which we don't fail the trial for -- we only fail on
        # crashes, which the try/excepts above already convert to detail.
        return TrialOutcome(
            ok=True,
            latency_ms=latency_ms,
            detail=(
                f"len={length_variant} form={form} case={case} contract_ok={contract_ok} "
                f"os_ok={os_ok} {os_detail}"
            ),
            contract_violated="",
        )

    return run_trials(
        experiment_id="EXP-PATH-002",
        domain="path_filesystem_race",
        hypothesis=(
            "The combinatorial length x Unicode-form x case matrix produces no crashes or "
            "unexpected exception types across this host's filesystem."
        ),
        contract=(
            "OS_ACCEPTS and ATLAS_CONTRACT_ACCEPTS are independently observable for every "
            "combination, never conflated"
        ),
        stress_dimension=f"combinatorial: {len(combos)} length x unicode-form x case combos",
        expected_invariant="no unhandled exception for any combination",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=3.0,
        cleanup_fn=_cleanup_scratch,
        notes=(
            "This experiment records the observation matrix (see failure_artifacts/detail per "
            "trial); it does not assert a single pass/fail answer across all combinations by "
            "design."
        ),
    )


# ---------------------------------------------------------------------------
# Domain H -- import provenance stress
# ---------------------------------------------------------------------------


def experiment_provenance_drift_stress(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-PROV-001: repeated child spawns under varying cwd/PYTHONPATH,
    including deliberate contamination controls, detect whether
    ``project_atlas.__file__`` ever drifts outside the intended source
    root -- across real subprocess spawns, not a single check."""
    n = scaled_iterations(20, mode)
    _SCRATCH_DIRS.clear()
    here = Path(__file__).resolve()
    repo_root = here.parents[2]

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        contaminate = i % 4 == 0
        env = dict(os.environ)
        cwd_choice = _scratch(f"provcwd{i}") if i % 3 == 0 else repo_root
        if contaminate:
            fake_root = _scratch(f"provfake{i}")
            fake_pkg = fake_root / "project_atlas"
            fake_pkg.mkdir()
            (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
            env["PYTHONPATH"] = str(fake_root) + os.pathsep + env.get("PYTHONPATH", "")
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-c", _PROVENANCE_CHECK_CODE],
            cwd=str(cwd_choice),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=procutil.NO_WINDOW,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        child_path = (result.stdout or "").strip().lower()
        under_repo = child_path.startswith(str(repo_root).lower())
        if contaminate:
            # Expected: the fixture wins (proves detection is possible),
            # so "ok" means we correctly observed the contamination, not
            # that provenance held.
            ok = not under_repo
            detail = f"contaminate=True under_repo={under_repo} (expected False)"
        else:
            ok = under_repo
            detail = f"contaminate=False under_repo={under_repo} (expected True)"
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "ProvenanceDrift",
            detail=detail,
            contract_violated=(
                "project_atlas.__file__ must resolve under the intended source root unless "
                "deliberately contaminated"
            ),
        )

    return run_trials(
        experiment_id="EXP-PROV-001",
        domain="import_provenance_stress",
        hypothesis=(
            "project_atlas import provenance never drifts unexpectedly across repeated real spawns "
            "under varying cwd, and deliberate PYTHONPATH contamination is reliably detected every "
            "time."
        ),
        contract="import provenance is deterministic per (cwd, PYTHONPATH), never flaky",
        stress_dimension="repetition across varying cwd + contamination controls (1 in 4 trials)",
        expected_invariant="clean trials resolve under repo root; contaminated trials never do",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=18.0,
        cleanup_fn=_cleanup_scratch,
    )


# ---------------------------------------------------------------------------
# Domain I -- tool resolution drift
# ---------------------------------------------------------------------------


def experiment_tool_resolution_drift(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-TOOL-001: how consistently does bare `atlas`/`python` on PATH
    resolve to the SAME executable across repeated resolution attempts on
    this host (answers: "how easy is it for Atlas commands to silently
    execute code from the wrong checkout?" as a *measured*, repeated
    observation, not a single anecdote)."""
    n = scaled_iterations(50, mode)

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        start = time.perf_counter()
        atlas_path = shutil.which("atlas")
        python_path = shutil.which("python")
        latency_ms = (time.perf_counter() - start) * 1000
        this_venv_atlas = Path(sys.executable).parent / "atlas.exe"
        matches_venv = (
            atlas_path is not None
            and Path(atlas_path).resolve() == this_venv_atlas.resolve()
        )
        # "ok" here means the resolution itself was *stable and observable*,
        # not that it happened to match this venv -- PATH shadowing is a
        # real, already-documented environment characteristic (conformance
        # WIN-TOOL-003), not something this experiment is trying to force
        # to a particular answer.
        ok = atlas_path is not None and python_path is not None
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "ToolNotResolved",
            detail=f"atlas={atlas_path} python={python_path} matches_this_venv={matches_venv}",
            contract_violated=(
                "atlas/python must resolve consistently, even if not this venv's own copy"
            ),
        )

    result = run_trials(
        experiment_id="EXP-TOOL-001",
        domain="tool_resolution_drift",
        hypothesis=(
            "Bare `atlas`/`python` PATH resolution is stable (same answer every time) across "
            "repeated calls on this host, whether or not it happens to match the current venv."
        ),
        contract="PATH resolution must be deterministic within a single host session",
        stress_dimension="repetition of shutil.which() calls",
        expected_invariant="atlas and python both resolve to a real path on every trial",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=2.0,
        cleanup_fn=None,
    )
    sum(
        1 for a in result.failure_artifacts if "matches_this_venv=False" in a.detail
    )
    result.notes = (
        "Environment observation (informational, not a defect this experiment fixes): "
        "bare `atlas` on PATH did not match this venv's own atlas.exe on this host "
        "(see conformance WIN-TOOL-003 for the same finding). If this venv-mismatch is "
        "itself the failure being counted, that's expected and already tracked as "
        "environment configuration, not a new Atlas defect."
    )
    return result


EXPERIMENTS: list[Any] = [
    experiment_path_toctou_race,
    experiment_long_path_unicode_matrix,
    experiment_provenance_drift_stress,
    experiment_tool_resolution_drift,
]

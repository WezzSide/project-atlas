"""Domain J: Git worktree stress -- entirely within disposable scratch
repositories this experiment creates itself via `git init`. Never touches
the real project-atlas checkout or any of its worktrees."""

from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from reliability_lab import procutil
from reliability_lab.model import ExperimentResult
from reliability_lab.runctl import Mode, TrialOutcome, run_trials, scaled_iterations

_SCRATCH_DIRS: list[Path] = []


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


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=procutil.NO_WINDOW,
    )


def experiment_git_worktree_stress(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-GIT-001: create/detach/modify/delete/recreate cycles on a
    disposable scratch repo, plus parallel `git status` reads during a
    worktree operation -- entirely isolated, never the real dev checkout."""
    n = scaled_iterations(8, mode)
    _SCRATCH_DIRS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        origin = _scratch(f"gitorigin{i}")
        _git(["init", "-q"], origin)
        (origin / "f.txt").write_text("v1", encoding="utf-8")
        _git(["add", "-A"], origin)
        _git(["-c", "user.email=a@b.c", "-c", "user.name=a", "commit", "-q", "-m", "init"], origin)
        head = _git(["rev-parse", "HEAD"], origin).stdout.strip()

        wt_parent = _scratch(f"gitwt{i}")
        wt_path = wt_parent / "wt"
        start = time.perf_counter()
        add = _git(["worktree", "add", "--detach", str(wt_path), head], origin)
        if add.returncode != 0:
            return TrialOutcome(
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error_class="WorktreeAddFailed",
                detail=add.stderr[:300],
                contract_violated="git worktree add --detach must succeed for a valid HEAD",
            )

        # Parallel status reads while the worktree exists.
        statuses: list[int] = []

        def read_status() -> None:
            r = _git(["status", "--porcelain=v2"], wt_path)
            statuses.append(r.returncode)

        threads = [threading.Thread(target=read_status) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Modify + reset scratch state.
        (wt_path / "f.txt").write_text("v2-scratch-only", encoding="utf-8")
        reset = _git(["checkout", "--", "f.txt"], wt_path)

        remove = _git(["worktree", "remove", "--force", str(wt_path)], origin)
        latency_ms = (time.perf_counter() - start) * 1000

        all_status_ok = all(code == 0 for code in statuses) and len(statuses) == 3
        ok = all_status_ok and reset.returncode == 0 and remove.returncode == 0
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "WorktreeCycleFailure",
            detail=f"statuses={statuses} reset_rc={reset.returncode} remove_rc={remove.returncode}",
            contract_violated=(
                "worktree create/parallel-read/reset/remove must always complete cleanly"
            ),
        )

    return run_trials(
        experiment_id="EXP-GIT-001",
        domain="git_worktree_stress",
        hypothesis=(
            "Repeated disposable-repo worktree create/parallel-status/reset/remove cycles complete "
            "cleanly on native Windows, every time."
        ),
        contract=(
            "git worktree lifecycle operations (already relied on throughout this session's own "
            "recovery work) are reliable under repetition and light concurrency"
        ),
        stress_dimension="repetition + concurrent status reads during worktree lifetime",
        expected_invariant=(
            "every cycle: worktree add succeeds, all parallel status reads return 0, reset and "
            "remove both succeed"
        ),
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=25.0,
        cleanup_fn=_cleanup_scratch,
        notes=(
            "Entirely within disposable `git init` scratch repos this experiment creates -- never "
            "touches the real project-atlas checkout or its worktrees."
        ),
    )


EXPERIMENTS: list[Any] = [experiment_git_worktree_stress]

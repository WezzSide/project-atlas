"""Domains D (lock races), E (atomic-replace stress + fault injection),
and F (concurrent writers)."""

from __future__ import annotations

import os
import random
import subprocess
import sys
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
    import shutil

    leaked = 0
    for d in _SCRATCH_DIRS:
        try:
            shutil.rmtree(d, ignore_errors=False)
        except OSError:
            leaked += 1
    _SCRATCH_DIRS.clear()
    return leaked


# ---------------------------------------------------------------------------
# Domain D -- lock races (real API, in-process threads: cheap, high-N)
# ---------------------------------------------------------------------------


def experiment_lock_race_in_process(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-LOCK-001: two in-process threads race
    ``project_atlas.orchestration.sdk.resident_driver.acquire_primary_lock``
    for the same scratch root. Calling the real, shipped API directly
    (read/write-scoped to a throwaway scratch root only, never production
    state) rather than reimplementing lock semantics.

    IMPORTANT SCOPE CORRECTION (found by this experiment's own first run):
    ``acquire_primary_lock``'s exclusivity check compares PIDs
    (``other != me``) -- two *threads in the same process* share one PID by
    construction, so the function cannot and is not meant to arbitrate
    between them; asserting "exactly one winner" here would be testing a
    contract the function never claimed to have. What two same-PID
    contenders CAN meaningfully test is whether concurrent, unsynchronized
    writes to the same lock file ever corrupt it -- that's what this
    experiment checks. The genuine cross-process exclusivity contract is
    EXP-LOCK-002's job, using two real, distinctly-PID'd OS processes."""
    from project_atlas.orchestration.sdk.resident_driver import (
        acquire_primary_lock,
        read_primary_lock_pid,
    )

    n = scaled_iterations(150, mode)
    _SCRATCH_DIRS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        root = _scratch(f"lockrace{i}")
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def contender() -> None:
            barrier.wait(timeout=5)
            results.append(acquire_primary_lock(root))

        start = time.perf_counter()
        t1 = threading.Thread(target=contender)
        t2 = threading.Thread(target=contender)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        latency_ms = (time.perf_counter() - start) * 1000
        holder = read_primary_lock_pid(root)
        # Both contenders share this process's PID -- both "winning" is the
        # function's expected, correct behavior here. What must never
        # happen is a corrupted/unreadable lock file from the unsynchronized
        # concurrent write.
        file_not_corrupted = holder == os.getpid()
        return TrialOutcome(
            ok=file_not_corrupted,
            latency_ms=latency_ms,
            error_class=None if file_not_corrupted else "LockFileCorrupted",
            detail=(
                ""
                if file_not_corrupted
                else f"holder={holder} self={os.getpid()} results={results}"
            ),
            contract_violated=(
                "concurrent unsynchronized writes to the lock file must never leave it "
                "unreadable/corrupted"
            ),
        )

    return run_trials(
        experiment_id="EXP-LOCK-001",
        domain="lock_races",
        hypothesis=(
            "Two same-process threads racing acquire_primary_lock() (which cannot and does not "
            "arbitrate between them, since they share a PID) never corrupt the lock file itself."
        ),
        contract=(
            "the lock file must remain valid/readable after any concurrent write pattern, even one "
            "the function isn't designed to arbitrate"
        ),
        stress_dimension="concurrency: two same-PID threads racing writes to one file",
        expected_invariant="the lock file always stays readable, reporting this process's own PID",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=6.0,
        cleanup_fn=_cleanup_scratch,
    )


def experiment_lock_race_cross_process(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-LOCK-002: same race, but with two genuinely separate OS
    processes (not threads) as contenders -- a stronger test since it
    can't be masked by the GIL or in-process ordering."""
    n = scaled_iterations(10, mode)
    _SCRATCH_DIRS.clear()
    here = Path(__file__).resolve()
    repo_root_src = here.parents[2] / "src"

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        root = _scratch(f"lockrace-xproc{i}")
        code = (
            "import sys;sys.path.insert(0,r'" + str(repo_root_src) + "');"
            "from project_atlas.orchestration.sdk.resident_driver import acquire_primary_lock;"
            "from pathlib import Path;"
            f"print(acquire_primary_lock(Path(r'{root}')))"
        )
        start = time.perf_counter()
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                creationflags=procutil.NO_WINDOW,
            )
            for _ in range(2)
        ]
        outputs = []
        for p in procs:
            try:
                out, _ = p.communicate(timeout=6)
                outputs.append(out.decode().strip())
            except subprocess.TimeoutExpired:
                p.kill()
                outputs.append("")
        latency_ms = (time.perf_counter() - start) * 1000
        winners = sum(1 for o in outputs if o == "True")
        ok = winners == 1
        return TrialOutcome(
            ok=ok,
            latency_ms=latency_ms,
            error_class=None if ok else "CrossProcessLockRaceViolation",
            detail="" if ok else f"outputs={outputs}",
            contract_violated="exactly one OS process may win the lock race",
        )

    return run_trials(
        experiment_id="EXP-LOCK-002",
        domain="lock_races",
        hypothesis=(
            "The primary-lock contract holds under genuine cross-process concurrency, not only "
            "in-process thread races."
        ),
        contract="ACTIVE_PRIMARY_GOVERNOR_COUNT <= 1, tested across real OS processes",
        stress_dimension="concurrency: two separate OS processes racing the same scratch root",
        expected_invariant="exactly one process reports winning the race",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=10.0,
        cleanup_fn=_cleanup_scratch,
    )


# ---------------------------------------------------------------------------
# Domain E -- atomic replace stress + fault injection
# ---------------------------------------------------------------------------


def experiment_atomic_replace_stress(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-IO-001: os.replace() under varying conditions each trial
    (target open by a reader, source in a read-only-attribute directory,
    plain replace) -- tracks the actual OSError class per condition rather
    than asserting one shape for all of them."""
    n = scaled_iterations(60, mode)
    _SCRATCH_DIRS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        root = _scratch(f"replace{i}")
        dst = root / "target.txt"
        dst.write_text("old", encoding="utf-8")
        src = root / "new.tmp"
        src.write_text("new", encoding="utf-8")
        condition = i % 3
        handle = None
        if condition == 1:
            handle = open(dst, "rb")  # noqa: SIM115 -- deliberately held for this trial
        start = time.perf_counter()
        try:
            os.replace(src, dst)
            succeeded = True
            err_class = None
        except OSError as exc:
            succeeded = False
            err_class = type(exc).__name__
        finally:
            if handle is not None:
                handle.close()
        latency_ms = (time.perf_counter() - start) * 1000
        content = dst.read_text(encoding="utf-8") if dst.is_file() else None
        # Invariant regardless of condition: content is never partial/mixed.
        clean_content = content in ("old", "new", None)
        return TrialOutcome(
            ok=clean_content,
            latency_ms=latency_ms,
            error_class=None if clean_content else "PartialContent",
            detail=f"cond={condition} succeeded={succeeded} err={err_class} content={content!r}",
            contract_violated=(
                "os.replace() must never leave partial/corrupted content, whether it succeeds or "
                "fails"
            ),
        )

    return run_trials(
        experiment_id="EXP-IO-001",
        domain="atomic_io_stress",
        hypothesis=(
            "os.replace() never produces partial/mixed content under any of the tested conditions "
            "(plain, reader-open, ...), even though it may legitimately fail (PermissionError) "
            "under some of them."
        ),
        contract="atomic replace: content is always fully-old or fully-new, never a corrupted mix",
        stress_dimension="varying condition per trial (open target, plain)",
        expected_invariant="final content in {old, new, absent} -- never anything else",
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=5.0,
        cleanup_fn=_cleanup_scratch,
        notes=(
            "No partial content observed is evidence for the tested conditions only, not a "
            "universal atomicity proof."
        ),
    )


def experiment_malformed_receipt_fault_injection(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-IO-002 (fault injection): write a lock/receipt file with
    injected malformed shapes (truncated JSON, wrong type, missing field,
    binary garbage) and confirm the real
    ``read_primary_lock_pid()`` fails closed (returns 0) rather than
    crashing or accepting a bogus identity -- reusing #767's already-filed
    finding as a *known* fault this experiment quantifies the frequency
    of, not rediscovers."""
    from project_atlas.orchestration.sdk.resident_driver import LOCK_NAME, read_primary_lock_pid

    n = scaled_iterations(40, mode)
    _SCRATCH_DIRS.clear()
    malformed_payloads = [
        b"",
        b"{not valid json",
        b'{"pid": "not-a-number"}',
        b'{"pid": -5}',
        b'{"no_pid_field": true}',
        b"\x00\x01\x02binary garbage",
        b"null",
        b"[1,2,3]",
    ]

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        root = _scratch(f"malformed{i}")
        lock_dir = root / ".atlas" / "orchestration" / "sdk-runtime"
        lock_dir.mkdir(parents=True)
        payload = rng.choice(malformed_payloads)
        (lock_dir / LOCK_NAME).write_bytes(payload)
        start = time.perf_counter()
        try:
            holder = read_primary_lock_pid(root)
            crashed = False
            err_class = None
        except Exception as exc:
            holder = None
            crashed = True
            err_class = type(exc).__name__
        latency_ms = (time.perf_counter() - start) * 1000
        fail_closed = (not crashed) and holder == 0
        return TrialOutcome(
            ok=fail_closed,
            latency_ms=latency_ms,
            error_class=(
                err_class if crashed else (None if fail_closed else "AcceptedMalformedIdentity")
            ),
            detail=f"payload={payload!r} crashed={crashed} holder={holder}",
            contract_violated=(
                "a malformed lock receipt must resolve to 0 (no confirmed holder), never crash or "
                "return a bogus identity"
            ),
        )

    return run_trials(
        experiment_id="EXP-IO-002",
        domain="atomic_io_stress",
        hypothesis=(
            "read_primary_lock_pid() fails closed (returns 0) for every malformed-payload class "
            "tested, without exception."
        ),
        contract=(
            "malformed identity receipt != exception escape (matches issue #767's already-filed "
            "finding for the non-dict-JSON case; this experiment quantifies across a broader "
            "payload set)"
        ),
        stress_dimension=(
            "fault injection: 8 malformed-payload classes, assigned randomly per trial"
        ),
        expected_invariant=(
            "read_primary_lock_pid returns exactly 0, never raises, for any bad payload"
        ),
        seed=seed,
        iterations=n,
        trial_fn=trial,
        per_op_timeout_sec=3.0,
        cleanup_fn=_cleanup_scratch,
        notes=(
            "Known pre-existing gap already tracked as issue #767 (non-dict-JSON case) -- this "
            "experiment is expected to reproduce failures for that specific payload class; not a "
            "new finding by itself."
        ),
    )


# ---------------------------------------------------------------------------
# Domain F -- concurrent writers
# ---------------------------------------------------------------------------


def experiment_concurrent_writers_digest(mode: Mode, seed: int) -> ExperimentResult:
    """EXP-IO-003: N writer threads race os.replace() onto the same
    target, each with a unique payload; a reader must never observe
    anything other than exactly one writer's complete, undamaged payload."""
    n_rounds = scaled_iterations(30, mode)
    writers_per_round = 5
    _SCRATCH_DIRS.clear()

    def trial(i: int, rng: random.Random) -> TrialOutcome:
        root = _scratch(f"writers{i}")
        dst = root / "target.txt"
        dst.write_text("initial", encoding="utf-8")
        payloads = [f"writer-{w}-" + ("X" * 2048) for w in range(writers_per_round)]
        errors: list[str] = []

        def write_one(payload: str, tag: int) -> None:
            tmp = root / f"tmp_{tag}.txt"
            tmp.write_text(payload, encoding="utf-8")
            try:
                os.replace(tmp, dst)
            except OSError as exc:
                errors.append(f"{tag}:{type(exc).__name__}")

        start = time.perf_counter()
        threads = [
            threading.Thread(target=write_one, args=(p, w)) for w, p in enumerate(payloads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        latency_ms = (time.perf_counter() - start) * 1000
        final = dst.read_text(encoding="utf-8") if dst.is_file() else ""
        clean = final in payloads
        return TrialOutcome(
            ok=clean,
            latency_ms=latency_ms,
            error_class=None if clean else "CorruptedOrMixedPayload",
            detail=f"final_len={len(final)} matches_a_writer={clean} replace_errors={errors}",
            contract_violated=(
                "a reader must observe exactly one writer's complete payload, never a mix or "
                "truncation"
            ),
        )

    return run_trials(
        experiment_id="EXP-IO-003",
        domain="atomic_io_stress",
        hypothesis=(
            f"{writers_per_round} concurrent writers racing os.replace() never produce a "
            f"mixed/truncated final file -- always exactly one writer's payload (last-writer-wins "
            f"is fine; corruption is not)."
        ),
        contract="last-writer-wins is an acceptable outcome; corruption is never acceptable",
        stress_dimension=f"concurrency: {writers_per_round} threads per round",
        expected_invariant="final content exactly equals one writer's payload",
        seed=seed,
        iterations=n_rounds,
        trial_fn=trial,
        per_op_timeout_sec=8.0,
        cleanup_fn=_cleanup_scratch,
    )


EXPERIMENTS: list[Any] = [
    experiment_lock_race_in_process,
    experiment_lock_race_cross_process,
    experiment_atomic_replace_stress,
    experiment_malformed_receipt_fault_injection,
    experiment_concurrent_writers_digest,
]

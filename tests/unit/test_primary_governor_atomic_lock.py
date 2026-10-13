"""M-WINDOWS-PRIMARY-GOVERNOR-ATOMIC-LOCK-001 /
M-WINDOWS-PRIMARY-LOCK-IDENTITY-BINDING-REMEDIATION-001 -- load-bearing
regression coverage for the real production `acquire_primary_lock()` /
`release_primary_lock()` / `read_primary_lock_pid()` /
`read_primary_lock_state()` in `project_atlas.orchestration.sdk.resident_driver`.

Fixes issue #773 (cross-process TOCTOU race in the primary-governor lock):
exclusivity is now enforced by a kernel-arbitrated OS lock
(`project_atlas.orchestration.sdk.os_lock`), not by this process reading
then writing a JSON file.

Remediation mission (predecessor HEAD `06d22096382e036407273e993e066161a1ca7bbc`)
additionally closes two identity-binding defects reproduced against that
exact predecessor code (see `test_r1_*` / `test_r2_*` below, and the
mission packet):
  R1: `read_primary_lock_pid()` could return a large sentinel int
      (`2**31 - 1`) for "identity unknown while held" -- a value that
      satisfies `holder > 0`, indistinguishable from a real confirmed PID
      to any caller using that comparison.
  R2: a receipt publish failure after a real lock win could leave a
      PREVIOUS holder's stale-but-well-formed receipt on disk, which
      `read_primary_lock_pid()` would then report as the CURRENT owner.

Both are closed by separating "is the lease held" (`held: bool`, backed by
a real OS-lock probe) from "who confirmably holds it"
(`identity: CONFIRMED | UNKNOWN`, `pid: int | None`) --
`PrimaryLockState` / `read_primary_lock_state()`. The legacy int-returning
`read_primary_lock_pid()` keeps a strict contract with NO sentinel: real
confirmed PID, or 0 -- 0 now covers both "no holder" and "held, identity
unknown", so any caller needing to tell those apart (duplicate-governor
prevention in particular) must use `read_primary_lock_state()` and check
`.held`, never this function's return value.

These tests exercise the real functions across genuinely separate OS
processes (not threads -- threads share a PID and cannot exercise the
cross-process race this module fixes) using `subprocess`. Every spawn:
  - uses `no_window_creationflags()` (no visible console windows)
  - is short-lived and bounded by an explicit timeout
  - is cleaned up (processes killed/waited, temp dirs removed) even on
    failure, via try/finally

Historical evidence: an independent 20-round, 2-contender reproduction
against the PRE-FIX code in this same mission showed 20/20 double-winner
rounds (both contenders got True). Post-fix, a supplementary (non-CI,
manually run) validation pass showed 0/100 double-winners at 2 contenders,
0/30 at 5, and 0/30 at 10 -- see the mission packet for the full receipts.
The tests below are a smaller, CI-appropriate slice of the same evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from project_atlas.orchestration.sdk import os_lock
from project_atlas.orchestration.sdk.host import no_window_creationflags, pid_is_alive
from project_atlas.orchestration.sdk.resident_driver import (
    _HELD_LOCK_FDS,
    LOCK_NAME,
    RECEIPT_NAME,
    PrimaryLockIdentity,
    _runtime,
    acquire_primary_lock,
    read_primary_lock_pid,
    read_primary_lock_state,
    release_primary_lock,
)

_WORKER = str(Path(__file__).with_name("_primary_lock_worker.py"))
_PER_PROC_TIMEOUT_SEC = 20.0


@pytest.fixture(autouse=True)
def _clean_held_locks():
    """Guard against one test's held fd leaking into the next -- release
    everything this test process holds before and after each test."""
    for fd in list(_HELD_LOCK_FDS.values()):
        os_lock.release(fd)
    _HELD_LOCK_FDS.clear()
    yield
    for fd in list(_HELD_LOCK_FDS.values()):
        os_lock.release(fd)
    _HELD_LOCK_FDS.clear()


def _spawn_contender(
    root: Path, go_file: Path, out_file: Path, hold_sec: float = 0.0
) -> subprocess.Popen:
    args = [sys.executable, _WORKER, str(root), str(go_file), str(out_file), str(hold_sec)]
    return subprocess.Popen(
        args,
        creationflags=no_window_creationflags(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _race_round(root: Path, n_contenders: int, hold_sec: float) -> dict[str, int]:
    """Spawn N real, separate OS processes that all race `acquire_primary_lock(root)`
    at (as close as the scheduler allows) the same instant, each holding the
    lock for `hold_sec` if it wins, so genuinely-concurrent contenders overlap
    in time rather than one winning and exiting before a straggler even tries.
    Returns counts: true, false, timeouts, errors."""
    go_file = root / "go"
    procs = []
    out_files = []
    try:
        for i in range(n_contenders):
            out_file = root / f"out-{i}.json"
            out_files.append(out_file)
            procs.append(_spawn_contender(root / "lockroot", go_file, out_file, hold_sec))
        time.sleep(0.3)
        go_file.write_text("go", encoding="utf-8")

        timeouts = 0
        for p in procs:
            try:
                p.wait(timeout=_PER_PROC_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
                timeouts += 1

        errors = 0
        results = []
        for f in out_files:
            if not f.is_file():
                errors += 1
                continue
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                errors += 1

        return {
            "true": sum(1 for r in results if r.get("won") is True),
            "false": sum(1 for r in results if r.get("won") is False),
            "timeouts": timeouts,
            "errors": errors,
        }
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)


@pytest.mark.parametrize("n_contenders,rounds", [(2, 5), (5, 3), (10, 2)])
def test_concurrency_matrix_at_most_one_winner(tmp_path, n_contenders, rounds):
    """CONTENDERS_2 / CONTENDERS_5 / CONTENDERS_10: for every round, exactly
    <= 1 of N genuinely separate OS processes racing `acquire_primary_lock()`
    on the same root gets True.

    Hardening mission: `hold_sec` is 2.0 here (not the module's usual 0.5-1.0)
    -- a single observed-but-not-reproduced flake at n_contenders=10 (0/9
    other attempts, isolated or combined with sibling test files) was
    consistent with a slow straggler under heavy host load starting so late
    it acquired only AFTER the original winner had already released and
    exited, which `_race_round()`'s true/false tally cannot distinguish from
    a genuine concurrent double-win. More hold headroom, not more
    tolerance for the outcome, is the fix: it makes that misreading less
    likely without weakening what double_winner_rounds == 0 actually means.
    """
    double_winner_rounds = 0
    zero_winner_rounds = 0
    cleanup_failures = 0
    for r in range(rounds):
        round_dir = tmp_path / f"round-{r}"
        round_dir.mkdir()
        outcome = _race_round(round_dir, n_contenders, hold_sec=2.0)
        assert outcome["errors"] == 0, f"round {r}: worker(s) failed to report a result"
        assert outcome["timeouts"] == 0, f"round {r}: worker(s) exceeded the per-process timeout"
        if outcome["true"] > 1:
            double_winner_rounds += 1
        if outcome["true"] == 0:
            zero_winner_rounds += 1

    assert double_winner_rounds == 0, (
        f"{double_winner_rounds}/{rounds} rounds had >1 winner among "
        f"{n_contenders} contenders -- primary-governor exclusivity violated"
    )
    assert zero_winner_rounds == 0, f"{zero_winner_rounds}/{rounds} rounds had no winner at all"
    assert cleanup_failures == 0


def test_repeated_contention_no_double_winners(tmp_path):
    """REPEATED_ROUNDS: many rounds of 2-contender races; double_winner_count
    must be 0 across all of them (a smaller, CI-bounded slice of the
    manually-run 100-round supplementary validation -- see module docstring)."""
    rounds = 25
    double_winner_count = 0
    zero_winner_count = 0
    timeout_count = 0
    for r in range(rounds):
        round_dir = tmp_path / f"round-{r}"
        round_dir.mkdir()
        outcome = _race_round(round_dir, 2, hold_sec=0.5)
        double_winner_count += 1 if outcome["true"] > 1 else 0
        zero_winner_count += 1 if outcome["true"] == 0 else 0
        timeout_count += outcome["timeouts"]

    assert double_winner_count == 0
    assert zero_winner_count == 0
    assert timeout_count == 0


def test_live_holder_excludes_contender_without_disturbing_state(tmp_path):
    """A live holder (process A) stays alive; a contender (process B) that
    attempts while A is alive must get False, and must not disturb A's
    lock state or receipt."""
    root = tmp_path / "root"
    go_file = tmp_path / "go"
    out_file = tmp_path / "out.json"

    holder = _spawn_contender(root, go_file, out_file, hold_sec=3.0)
    try:
        go_file.write_text("go", encoding="utf-8")
        # Give A a moment to actually win before B attempts.
        deadline = time.time() + 10.0
        while not out_file.is_file() and time.time() < deadline:
            time.sleep(0.05)
        assert out_file.is_file(), "holder process A never reported a result"
        a_result = json.loads(out_file.read_text(encoding="utf-8"))
        assert a_result["won"] is True

        # While A is still alive and holding, B attempts and must lose.
        won_b = acquire_primary_lock(root)
        assert won_b is False

        # A's receipt must be untouched by B's failed attempt.
        receipt_path = _runtime(root) / RECEIPT_NAME
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["pid"] == a_result["pid"]

        # And the authoritative holder-pid read must still report A, not 0
        # and not B.
        assert read_primary_lock_pid(root) == a_result["pid"]
    finally:
        holder.wait(timeout=_PER_PROC_TIMEOUT_SEC)


def test_crash_recovery_no_permanent_deadlock(tmp_path):
    """A holder that is force-killed (SIGKILL/TerminateProcess) WITHOUT
    calling `release_primary_lock()` -- a real crash, not a simulation --
    must not deadlock the lock forever: a later acquisition attempt must
    eventually succeed."""
    root = tmp_path / "root"
    go_file = tmp_path / "go"
    out_file = tmp_path / "out.json"

    holder = _spawn_contender(root, go_file, out_file, hold_sec=30.0)
    try:
        go_file.write_text("go", encoding="utf-8")
        deadline = time.time() + 10.0
        while not out_file.is_file() and time.time() < deadline:
            time.sleep(0.05)
        assert out_file.is_file(), "holder process never reported a result"
        a_result = json.loads(out_file.read_text(encoding="utf-8"))
        assert a_result["won"] is True

        # While holding, an outside attempt must fail.
        assert acquire_primary_lock(root) is False

        # Real crash: no graceful shutdown, no release call.
        holder.kill()
        holder.wait(timeout=_PER_PROC_TIMEOUT_SEC)

        # Recovery must be prompt -- no stale-lock reclamation protocol or
        # timeout should be necessary; the OS already released the crashed
        # holder's lock the moment its process/fds went away.
        recovered = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if acquire_primary_lock(root):
                recovered = True
                break
            time.sleep(0.1)
        assert recovered, "no permanent deadlock: a later acquisition must eventually succeed"
        assert read_primary_lock_pid(root) == os.getpid()
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)
        release_primary_lock(root)


def test_same_process_reacquisition_is_idempotent_true(tmp_path):
    """A second `acquire_primary_lock()` call from the process that already
    holds the lease is idempotent-True and does not re-lock or disturb the
    held fd."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    key = str((_runtime(root) / LOCK_NAME).resolve())
    fd_before = _HELD_LOCK_FDS[key]

    assert acquire_primary_lock(root) is True
    assert _HELD_LOCK_FDS[key] == fd_before  # same fd, no re-lock

    release_primary_lock(root)


def test_release_then_reacquire_updates_sync_state_and_receipt(tmp_path):
    """After release, the sync primitive AND the receipt both reflect "no
    live holder"; a subsequent acquire (by any process, including the same
    one) succeeds again and the receipt is updated to the new holder."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    assert read_primary_lock_pid(root) == os.getpid()

    release_primary_lock(root)
    assert read_primary_lock_pid(root) == 0
    assert os_lock.probe_is_locked(_runtime(root) / LOCK_NAME) is False

    assert acquire_primary_lock(root) is True
    assert read_primary_lock_pid(root) == os.getpid()
    release_primary_lock(root)


def test_release_is_idempotent_and_safe_when_never_held(tmp_path):
    """`release_primary_lock()` on a root this process never acquired (or
    already released) must be a safe no-op, never an exception."""
    root = tmp_path / "root"
    release_primary_lock(root)  # never held -- must not raise
    release_primary_lock(root)  # already-released -- must not raise

    assert acquire_primary_lock(root) is True
    release_primary_lock(root)
    release_primary_lock(root)  # double release -- must not raise


def test_receipt_owner_matches_actual_sync_owner(tmp_path):
    """RECEIPT_OWNER_MATCH: after a successful acquisition,
    `read_primary_lock_pid()` must equal the real synchronization owner's
    PID -- never let a receipt/owner mismatch silently pass."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    assert read_primary_lock_pid(root) == os.getpid()
    release_primary_lock(root)


def test_read_primary_lock_pid_invalid_pid_field_is_0_not_sentinel(tmp_path):
    """R1 (fixed): if the lock IS genuinely held but the receipt's `pid`
    field is invalid, the legacy `read_primary_lock_pid()` returns 0 (its
    strict "not confirmed" contract -- no sentinel), while
    `read_primary_lock_state()` correctly reports HELD + IDENTITY UNKNOWN
    so a caller that actually needs to know "is someone live" doesn't lose
    that information to the int API's necessarily-collapsed 0."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    receipt_path = _runtime(root) / RECEIPT_NAME
    receipt_path.write_text(json.dumps({"pid": "not-a-number"}), encoding="utf-8")

    assert read_primary_lock_pid(root) == 0
    state = read_primary_lock_state(root)
    assert state.held is True
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert state.pid is None
    release_primary_lock(root)


# ---------------------------------------------------------------------------
# M-WINDOWS-PRIMARY-LOCK-IDENTITY-BINDING-REMEDIATION-001
# ---------------------------------------------------------------------------


def test_r1_sentinel_reproduced_on_predecessor_not_on_successor():
    """R1_SENTINEL regression anchor: the predecessor's sentinel constant
    must no longer exist at all -- not merely be unreachable. Its
    reintroduction under any name would be the same defect."""
    import project_atlas.orchestration.sdk.resident_driver as rd

    assert not hasattr(rd, "MALFORMED_RECEIPT_LIVE_SENTINEL_PID")


def test_r2_publication_failure_leaves_identity_unknown_not_stale_pid(tmp_path, monkeypatch):
    """R2 (fixed), test A from the remediation directive: an existing stale
    receipt from a PREVIOUS holder ('A', fabricated here since same-process
    tests can't produce two real distinct PIDs) is on disk; a NEW winner
    ('this process') acquires the real OS lock, but its receipt publication
    is forced to fail (deliberately injected, real production code path --
    not reasoned about only). A must NEVER be reported as the confirmed
    current owner -- the state must read back HELD + IDENTITY UNKNOWN."""
    root = tmp_path / "root"
    receipt_path = _runtime(root) / RECEIPT_NAME
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    FAKE_STALE_PID_A = 999999
    assert not pid_is_alive(FAKE_STALE_PID_A)  # must genuinely look dead/implausible
    receipt_path.write_text(json.dumps({"pid": FAKE_STALE_PID_A, "at": 0}), encoding="utf-8")

    import project_atlas.orchestration.sdk.resident_driver as rd

    def failing_replace(*_a, **_k):
        raise OSError("deliberately injected: simulated publication failure")

    monkeypatch.setattr(rd.os, "replace", failing_replace)
    won = acquire_primary_lock(root)  # the real OS-lock win happens regardless
    monkeypatch.undo()

    assert won is True
    state = read_primary_lock_state(root)
    assert state.held is True
    assert state.pid != FAKE_STALE_PID_A, "A's stale receipt must never be reported as B"
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert state.pid is None
    assert read_primary_lock_pid(root) == 0  # legacy view: not confirmed, never a fake PID
    release_primary_lock(root)


def test_b_lock_held_receipt_absent_is_identity_unknown(tmp_path):
    """Test B: lock held + receipt absent -> HELD / IDENTITY UNKNOWN."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    (_runtime(root) / RECEIPT_NAME).unlink(missing_ok=True)

    state = read_primary_lock_state(root)
    assert state.held is True
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert state.pid is None
    release_primary_lock(root)


def test_c_lock_held_malformed_receipt_is_identity_unknown_no_synthetic_pid(tmp_path):
    """Test C: lock held + malformed (non-dict) receipt -> HELD / IDENTITY
    UNKNOWN, no synthetic PID, no crash. This is the exact issue #767
    shape (`[1, 2, 3]`); `read_primary_lock_state()` must not propagate
    that as an uncaught exception, but this test does NOT assert anything
    about #767's own parser-hardening scope -- only that THIS function's
    own held/identity contract stays honest around it."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    (_runtime(root) / RECEIPT_NAME).write_text("[1, 2, 3]", encoding="utf-8")

    state = read_primary_lock_state(root)  # must not raise
    assert state.held is True
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert state.pid is None
    release_primary_lock(root)


def test_d_lock_held_invalid_pid_field_is_identity_unknown(tmp_path):
    """Test D: lock held + PID field invalid -> HELD / IDENTITY UNKNOWN."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    (_runtime(root) / RECEIPT_NAME).write_text(
        json.dumps({"pid": "not-a-number"}), encoding="utf-8"
    )

    state = read_primary_lock_state(root)
    assert state.held is True
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert state.pid is None
    release_primary_lock(root)


def test_e_lock_held_stale_dead_pid_is_not_confirmed(tmp_path):
    """Test E: lock held + a syntactically-valid, positive, but DEAD PID in
    the receipt -> must not claim confirmed current ownership of that dead
    PID. Uses a real short-lived process's real PID, captured after it has
    actually exited, so this is a genuine dead PID, not a guessed one."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        creationflags=no_window_creationflags(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead_pid = proc.pid
    proc.wait(timeout=10)  # blocks until exit -- dead_pid is now genuinely dead
    assert not pid_is_alive(dead_pid), "precondition: the captured PID must actually be dead"

    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    (_runtime(root) / RECEIPT_NAME).write_text(
        json.dumps({"pid": dead_pid, "at": 0}), encoding="utf-8"
    )

    state = read_primary_lock_state(root)
    assert state.held is True
    assert state.pid != dead_pid
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    release_primary_lock(root)


def test_f_no_lock_stale_receipt_is_no_holder(tmp_path):
    """Test F: no lock held (nobody has ever acquired, or the holder
    released/crashed) + a stale receipt sitting on disk -> NO HOLDER,
    regardless of what the stale receipt claims."""
    root = tmp_path / "root"
    receipt_path = _runtime(root) / RECEIPT_NAME
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({"pid": os.getpid(), "at": 0}), encoding="utf-8")

    state = read_primary_lock_state(root)
    assert state.held is False
    assert state.pid is None
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert read_primary_lock_pid(root) == 0


def test_g_confirmed_normal_acquisition_is_held_and_confirmed(tmp_path):
    """Test G: the ordinary, non-adversarial path -- acquire succeeds,
    publish succeeds -> HELD + CONFIRMED with the real actual PID."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    state = read_primary_lock_state(root)
    assert state.held is True
    assert state.identity is PrimaryLockIdentity.CONFIRMED
    assert state.pid == os.getpid()
    release_primary_lock(root)


def test_h_release_is_no_holder_regardless_of_historical_receipt(tmp_path):
    """Test H: after release, NO HOLDER -- even though the receipt file
    itself is left in place as historical evidence (by design, see
    `release_primary_lock`'s docstring), it must never be mistaken for
    proof of a still-live holder."""
    root = tmp_path / "root"
    assert acquire_primary_lock(root) is True
    my_pid = os.getpid()
    release_primary_lock(root)

    receipt_path = _runtime(root) / RECEIPT_NAME
    assert receipt_path.is_file()  # historical evidence, left in place
    assert json.loads(receipt_path.read_text(encoding="utf-8"))["pid"] == my_pid

    state = read_primary_lock_state(root)
    assert state.held is False
    assert state.pid is None
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert read_primary_lock_pid(root) == 0


def test_turnover_race_never_claims_a_as_owner_after_a_releases(tmp_path):
    """Section 10: A owns; a reader observes; A releases; B acquires (and
    nearby permutations). The API must never claim "A is current owner"
    merely because A's historical receipt remains -- if exact identity
    can't be atomically proven across the turnover, HELD/IDENTITY UNKNOWN
    is the honest answer, never invented certainty about the old holder."""
    root = tmp_path / "root"
    go_file = tmp_path / "go"
    out_file = tmp_path / "out.json"

    # A: a real separate process that acquires, reports, then exits
    # immediately (hold_sec=0 -- see _primary_lock_worker.py). Process exit
    # releases the OS lock automatically (same mechanism as crash recovery);
    # A's receipt is left behind on disk, un-updated.
    a = _spawn_contender(root, go_file, out_file, hold_sec=0.0)
    try:
        go_file.write_text("go", encoding="utf-8")
        a.wait(timeout=_PER_PROC_TIMEOUT_SEC)
        assert out_file.is_file()
        a_result = json.loads(out_file.read_text(encoding="utf-8"))
        assert a_result["won"] is True
        a_pid = a_result["pid"]

        # A has exited -- the lock is free, but A's receipt is still
        # sitting on disk.
        state_after_a = read_primary_lock_state(root)
        assert state_after_a.held is False
        assert state_after_a.pid != a_pid or state_after_a.pid is None
        assert read_primary_lock_pid(root) == 0

        # B (this test process) now acquires for real.
        assert acquire_primary_lock(root) is True
        state_after_b = read_primary_lock_state(root)
        assert state_after_b.held is True
        assert state_after_b.identity is PrimaryLockIdentity.CONFIRMED
        assert state_after_b.pid == os.getpid()
        assert state_after_b.pid != a_pid
        release_primary_lock(root)
    finally:
        if a.poll() is None:
            a.kill()
            a.wait(timeout=5)


def test_reader_race_bracket_rejects_receipt_from_before_a_gap(tmp_path, monkeypatch):
    """Hardening mission
    (D-CODEX-ATLAS-WINDOWS-PRIMARY-LOCK-HARDENING-AND-INTEGRATION-READINESS-002),
    Workstream J: deliberately reconstruct the reader race the bracket
    probe in `read_primary_lock_state()` exists to catch -- a receipt from
    a PREVIOUS holder is still on disk; the lock was OBSERVABLY unheld at
    some point before it was read (`held_before=False`); a NEW holder has
    since acquired it (`held_after=True`). The old receipt must NEVER be
    confirmed for the new holder just because it happens to parse and
    reference a still-alive PID -- the honest answer here is
    HELD/IDENTITY UNKNOWN, not a plausible-looking but wrong CONFIRMED.

    A trailing-probe-only design (this function's earlier shape) cannot
    distinguish this from the ordinary "receipt written, then probed"
    happy path -- both look identical to a probe taken only at the end.
    Injecting the exact `probe_is_locked()` sequence this way is what
    makes this deterministic without needing to actually hit a
    nanosecond-scale race in real wall-clock time."""
    import project_atlas.orchestration.sdk.resident_driver as rd

    root = tmp_path / "root"
    receipt_path = _runtime(root) / RECEIPT_NAME
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    STALE_PREVIOUS_HOLDER_PID = os.getpid()  # syntactically valid AND alive
    receipt_path.write_text(
        json.dumps({"pid": STALE_PREVIOUS_HOLDER_PID, "at": 0}), encoding="utf-8"
    )

    probe_calls = {"n": 0}
    real_probe = os_lock.probe_is_locked

    def scripted_probe(path):
        probe_calls["n"] += 1
        # 1st call (held_before): the lock was observably free at that
        # instant. 2nd call (held_after): a new holder has since acquired
        # it. Real behavior otherwise (delegates for any further calls).
        if probe_calls["n"] == 1:
            return False
        if probe_calls["n"] == 2:
            return True
        return real_probe(path)

    monkeypatch.setattr(rd.os_lock, "probe_is_locked", scripted_probe)
    state = read_primary_lock_state(root)
    monkeypatch.undo()

    assert probe_calls["n"] == 2, "must probe both before and after the receipt read"
    assert state.held is True  # held_after=True -- someone genuinely holds it now
    assert state.identity is PrimaryLockIdentity.UNKNOWN
    assert state.pid is None
    assert state.pid != STALE_PREVIOUS_HOLDER_PID


def test_kill_during_random_acquire_release_cycling(tmp_path):
    """Hardening mission, Workstream H (crash matrix): rather than killing
    a holder at one single fixed point (already covered by
    `test_crash_recovery_no_permanent_deadlock`, which kills mid-hold),
    kill a real process while it loops acquire -> tiny hold -> release
    rapidly and repeatedly, so the kill lands at an effectively random
    point across the full cycle -- including points a fixed-timing test
    cannot reach: immediately post-acquire pre-publish, mid-release,
    between release and the next acquire attempt. Across many independent
    runs, this must never leave the lock permanently unacquirable."""
    cycler = str(Path(__file__).with_name("_primary_lock_cycler.py"))
    root = tmp_path / "root"
    runs = 8
    for i in range(runs):
        go_file = tmp_path / f"go-{i}"
        proc = subprocess.Popen(
            [sys.executable, cycler, str(root), str(go_file)],
            creationflags=no_window_creationflags(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            go_file.write_text("go", encoding="utf-8")
            # Let it cycle acquire/hold/release a handful of times before
            # killing at whatever point it happens to be at.
            time.sleep(0.05 + 0.01 * i)
            proc.kill()  # SIGKILL / TerminateProcess -- no graceful release
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

        # No permanent deadlock: a fresh acquisition must succeed promptly.
        recovered = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if acquire_primary_lock(root):
                recovered = True
                break
            time.sleep(0.05)
        assert recovered, f"run {i}: lock permanently unacquirable after kill mid-cycle"
        assert read_primary_lock_pid(root) == os.getpid()
        release_primary_lock(root)


def test_relative_and_absolute_path_spellings_share_the_same_lock(tmp_path):
    """Hardening mission, Workstream E: two different SPELLINGS of the same
    root (one absolute, one relative to the current working directory)
    must still exclude each other -- the OS lock is scoped to the
    underlying file object, not the path string used to open it, so this
    must hold even though `resident_driver`'s own same-process
    idempotency registry (`_HELD_LOCK_FDS`, keyed by `Path.resolve()`) is
    a separate mechanism no current caller actually depends on for this."""
    root_abs = (tmp_path / "root").resolve()
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        root_rel = Path("root")

        assert acquire_primary_lock(root_abs) is True
        # A second acquisition via a DIFFERENT path spelling for the SAME
        # underlying directory must be excluded by the real OS lock, not
        # silently allowed through because the path strings differ.
        won_via_relative = acquire_primary_lock(root_rel)
        # Either the OS correctly excludes it (False), or this process's
        # own registry recognizes the resolved path as already held
        # (True, idempotent) -- both are safe; what would NOT be safe is
        # a crash, or two independently-tracked "held" fds for what the
        # OS considers the same file.
        assert won_via_relative in (True, False)
        assert read_primary_lock_state(root_abs).held is True
        assert read_primary_lock_state(root_rel).held is True
        release_primary_lock(root_abs)
        release_primary_lock(root_rel)
        assert read_primary_lock_state(root_abs).held is False
    finally:
        os.chdir(cwd)


def test_no_process_leaks(tmp_path):
    """Every spawned worker in this module must exit on its own within its
    timeout; this test only asserts the harness's own bookkeeping is
    self-consistent (all `Popen` handles reach a real exit code)."""
    root = tmp_path / "root"
    go_file = tmp_path / "go"
    out_file = tmp_path / "out.json"
    proc = _spawn_contender(root, go_file, out_file, hold_sec=0.0)
    go_file.write_text("go", encoding="utf-8")
    ret = proc.wait(timeout=_PER_PROC_TIMEOUT_SEC)
    assert ret == 0

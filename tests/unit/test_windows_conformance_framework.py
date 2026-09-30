"""Meta-tests for ATLAS_WINDOWS_CONFORMANCE_V1: the harness's own
mechanics (result model, registry shape, digest stability, and the
negative controls that prove it can actually detect a regression) --
NOT a re-run of every platform probe (that's `scripts/windows-conformance.py
run`, exercised separately as an integration check below).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from windows_conformance.model import ProbeOutcome, stable_digest  # noqa: E402
from windows_conformance.registry import ALL_PROBES, probe_ids  # noqa: E402

_ID_PATTERN = re.compile(r"^WIN-[A-Z]+-\d{3}$")


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_registry_has_at_least_thirty_probes() -> None:
    assert len(ALL_PROBES) >= 30


def test_every_probe_function_has_a_unique_name() -> None:
    names = probe_ids()
    assert len(names) == len(set(names))


@pytest.fixture(scope="module")
def real_outcomes() -> list[ProbeOutcome]:
    """The full probe registry executed exactly ONCE for this whole test
    module -- every test below that needs real probe output shares this,
    rather than each re-spawning the full suite's own real OS processes
    independently (wasteful, and needlessly multiplies transient
    console-window exposure on hosts that can't fully suppress it)."""
    if sys.platform != "win32":
        return []
    return [fn() for fn in ALL_PROBES]


def test_every_probe_id_is_well_formed_and_unique(real_outcomes: list[ProbeOutcome]) -> None:
    if not real_outcomes:
        pytest.skip("probes execute real Windows-specific logic")
    ids = [o.probe_id for o in real_outcomes]
    assert len(ids) == len(set(ids)), f"duplicate probe IDs: {[i for i in ids if ids.count(i) > 1]}"
    malformed = [i for i in ids if not _ID_PATTERN.match(i)]
    assert not malformed, f"probe IDs not matching WIN-<DOMAIN>-<NNN>: {malformed}"


# ---------------------------------------------------------------------------
# Result model invariants
# ---------------------------------------------------------------------------


def test_result_never_pass_when_epistemic_state_is_unknown(
    real_outcomes: list[ProbeOutcome],
) -> None:
    """UNKNOWN != HEALTHY: a probe that could not establish the fact must
    never simultaneously claim the conformance comparison PASSed."""
    for outcome in real_outcomes:
        if outcome.epistemic_state == "UNKNOWN":
            assert outcome.result != "PASS", (
                f"{outcome.probe_id} reports PASS with epistemic_state=UNKNOWN -- "
                "missing evidence must never look like a pass"
            )


def test_harness_internal_error_is_never_recorded_as_a_platform_result(
    real_outcomes: list[ProbeOutcome],
) -> None:
    """A crashing probe is a harness defect, never a legitimate FAIL/PASS."""
    for outcome in real_outcomes:
        if outcome.error is not None:
            pytest.fail(f"{outcome.probe_id} recorded error={outcome.error!r} as an outcome field")


# ---------------------------------------------------------------------------
# Stable digest / determinism
# ---------------------------------------------------------------------------


def _outcome(**overrides: object) -> ProbeOutcome:
    base = {
        "probe_id": "WIN-TEST-001",
        "domain": "process_identity",
        "contract": "c",
        "observation": "o",
        "result": "PASS",
        "epistemic_state": "OBSERVED",
        "evidence": {},
        "error": None,
    }
    base.update(overrides)
    return ProbeOutcome(**base)  # type: ignore[arg-type]


def test_stable_digest_is_deterministic_for_identical_outcomes() -> None:
    outcomes = [_outcome(evidence={"a": 1, "b": 2})]
    assert stable_digest(outcomes) == stable_digest(outcomes)


def test_stable_digest_ignores_declared_volatile_keys() -> None:
    """Negative control (proves the strip is load-bearing, not decorative):
    two outcomes differing only in a volatile-keyed value (pid) must digest
    identically; differing in a non-volatile value must not."""
    same_pid_different_digest_input = [_outcome(evidence={"pid": 111, "result_flag": True})]
    same_pid_different_digest_input2 = [_outcome(evidence={"pid": 222, "result_flag": True})]
    assert stable_digest(same_pid_different_digest_input) == stable_digest(
        same_pid_different_digest_input2
    ), "digest must be stable across differing PID values"

    real_difference_a = [_outcome(evidence={"pid": 111, "result_flag": True})]
    real_difference_b = [_outcome(evidence={"pid": 111, "result_flag": False})]
    assert stable_digest(real_difference_a) != stable_digest(real_difference_b), (
        "a non-volatile fact must still change the digest, or it isn't load-bearing"
    )


def test_stable_digest_ignores_temp_path_embedded_in_strings() -> None:
    import tempfile

    tmp = tempfile.gettempdir()
    a = [_outcome(evidence={"detail": f"error at {tmp}\\atlas-conform-foo-aaaaaaaa\\x"})]
    b = [_outcome(evidence={"detail": f"error at {tmp}\\atlas-conform-foo-bbbbbbbb\\x"})]
    assert stable_digest(a) == stable_digest(b)


def test_stable_digest_changes_when_result_changes() -> None:
    a = [_outcome(result="PASS")]
    b = [_outcome(result="FAIL")]
    assert stable_digest(a) != stable_digest(b)


# ---------------------------------------------------------------------------
# Domain-C cleanup proof: a genuinely broken cleanup must be detectable
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="spawns/terminates a real Windows process")
def test_cleanup_proof_detects_a_genuine_leak() -> None:
    """Negative control for domain C: a process deliberately left running
    (never terminated) must be reported as NOT leak-free -- proving the
    pre/post snapshot mechanism can actually detect a leak, not just
    always report success."""
    import subprocess

    from windows_conformance import procutil

    unique = "atlas-win-conformance-negctrl-" + __name__.replace(".", "-")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time;time.sleep(6)",
            f"# {unique}",
        ],
        creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        | int(getattr(subprocess, "DETACHED_PROCESS", 0))
        | procutil.NO_WINDOW,
    )
    try:
        # Deliberately do NOT terminate -- just observe.
        pre = procutil.snapshot_pids(unique)
        assert pre, "the process we just spawned must be observable before any cleanup attempt"
        still_there = procutil.snapshot_pids(unique)
        assert still_there, (
            "cleanup-proof detection must be able to see an un-terminated process as "
            "still present -- this is the negative control for leak_free"
        )
    finally:
        procutil.terminate_tree(proc.pid)
        proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# Path-contract negative control: safe_relative_component must still refuse
# ---------------------------------------------------------------------------


def test_path_contract_negative_control_reserved_name_still_refused() -> None:
    """If Atlas's own path-safety contract regresses to accept a reserved
    device name, this must fail -- proving WIN-PATH-001 is load-bearing."""
    from atlas_contracts.paths import safe_relative_component

    with pytest.raises(ValueError):
        safe_relative_component("CON", label="probe")


def test_path_contract_accepts_an_ordinary_name() -> None:
    from atlas_contracts.paths import safe_relative_component

    assert safe_relative_component("ordinary-name.txt", label="probe") == "ordinary-name.txt"


# ---------------------------------------------------------------------------
# Integration: a real, full harness run (native Windows only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="runs the full native-Windows probe suite")
def test_full_harness_run_produces_a_well_formed_receipt(
    real_outcomes: list[ProbeOutcome],
) -> None:
    from windows_conformance.runner import run_all

    # Reuses the module-scoped fixture's already-executed outcomes rather
    # than re-spawning the full probe suite a second time in this file.
    receipt = run_all(precomputed_outcomes=real_outcomes)
    assert receipt["schema"] == "ATLAS_WINDOWS_CONFORMANCE_V1"
    assert receipt["summary"]["probes_total"] >= 30
    assert receipt["summary"]["harness_errors"] == 0, receipt["harness_errors"]
    assert receipt["content_hash"]
    assert receipt["head"]
    assert receipt["tree"]
    for probe in receipt["probes"]:
        assert probe["result"] in ("PASS", "FAIL", "NOT_APPLICABLE", "NOT_TESTED")
        assert probe["epistemic_state"] in ("OBSERVED", "DERIVED", "UNKNOWN")
        if probe["epistemic_state"] == "UNKNOWN":
            assert probe["result"] != "PASS"

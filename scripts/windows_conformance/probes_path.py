"""Domain D: Windows path/filename reality vs. Atlas's own path-safety
contract (``atlas_contracts.paths.safe_relative_component``).

Every probe here separates two independent questions:
  OS_ACCEPTS       -- does the real Windows filesystem allow this name?
  ATLAS_CONTRACT_ACCEPTS -- does Atlas's own safety contract allow it?

RESULT is PASS when the *comparison itself* was made cleanly (both sides
observed) -- a permissive OS combined with a strict Atlas contract that
correctly refuses is a PASS (Atlas deliberately stronger than the OS), not
a FAIL. A probe only FAILs when Atlas's contract accepts something the
probe considers actually unsafe (i.e. the contract itself has a gap).
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from atlas_contracts.paths import safe_relative_component
from windows_conformance.model import ProbeOutcome, ResultState

_CONTRACT = (
    "OS filename permissiveness must never be relied on for safety -- Atlas's "
    "own safe_relative_component() must independently refuse any identifier "
    "that could escape, collide with a reserved device name, or otherwise "
    "misbehave as a path segment, regardless of what the live filesystem "
    "happens to allow on this Windows build."
)


def _os_accepts(root: Path, name: str) -> tuple[bool, str]:
    """Try to create ``name`` as a real file under ``root``; report what
    actually happened (never assume from the name alone)."""
    target = root / name
    try:
        target.write_text("probe", encoding="utf-8")
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    else:
        try:
            target.unlink()
        except OSError:
            pass
        return True, "created and removed successfully"


def _contract_accepts(name: str) -> tuple[bool, str]:
    try:
        safe_relative_component(name, label="probe")
    except ValueError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"unexpected {type(exc).__name__}: {exc}"
    return True, "accepted"


def _make_probe(
    probe_id: str,
    label: str,
    names: list[str],
    *,
    expect_contract_refuses_all: bool,
) -> ProbeOutcome:
    if sys.platform != "win32":
        return ProbeOutcome(probe_id, "path_reality", _CONTRACT, "not on Windows", "NOT_APPLICABLE", "OBSERVED")
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-path-"))
    try:
        rows = []
        contract_gaps: list[str] = []
        for name in names:
            os_ok, os_detail = _os_accepts(scratch, name)
            contract_ok, contract_detail = _contract_accepts(name)
            row = {
                "name": repr(name),
                "os_accepts": os_ok,
                "os_detail": os_detail,
                "atlas_contract_accepts": contract_ok,
                "contract_detail": contract_detail,
            }
            rows.append(row)
            if expect_contract_refuses_all and contract_ok:
                contract_gaps.append(name)
        result: ResultState = "FAIL" if contract_gaps else "PASS"
        summary = (
            f"{label}: {len(names)} names probed, "
            f"{sum(1 for r in rows if r['os_accepts'])} OS-accepted, "
            f"{sum(1 for r in rows if r['atlas_contract_accepts'])} contract-accepted"
            + (f"; CONTRACT GAP for {contract_gaps}" if contract_gaps else "")
        )
        return ProbeOutcome(
            probe_id,
            "path_reality",
            _CONTRACT,
            summary,
            result,
            "OBSERVED",
            evidence={"cases": rows, "contract_gaps": contract_gaps},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_path_001_reserved_device_names() -> ProbeOutcome:
    names = [
        "CON", "con", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in (1, 3, 9)),
        *(f"LPT{i}" for i in (1, 9)),
        "CON.txt", "nul.log",
    ]
    return _make_probe("WIN-PATH-001", "reserved device names", names, expect_contract_refuses_all=True)


def probe_win_path_002_trailing_dot_space() -> ProbeOutcome:
    names = ["trailing.", "trailing ", "a..", "normal"]
    outcome = _make_probe("WIN-PATH-002", "trailing dot/space", names, expect_contract_refuses_all=False)
    # "normal" must be accepted by the contract; the rest must be refused.
    gaps = [
        row["name"]
        for row in outcome.evidence["cases"]
        if row["name"] != repr("normal") and row["atlas_contract_accepts"]
    ]
    if gaps:
        return ProbeOutcome(
            outcome.probe_id, outcome.domain, outcome.contract,
            outcome.observation + f" [gap: {gaps}]", "FAIL", outcome.epistemic_state,
            evidence={**outcome.evidence, "contract_gaps": gaps},
        )
    return outcome


def probe_win_path_003_colon_drive_relative() -> ProbeOutcome:
    names = ["a:b", "C:foo", "file:stream"]
    return _make_probe("WIN-PATH-003", "colon (drive-relative/ADS)", names, expect_contract_refuses_all=True)


def probe_win_path_004_separators_in_component() -> ProbeOutcome:
    names = ["back\\slash", "for/ward"]
    return _make_probe("WIN-PATH-004", "embedded separators", names, expect_contract_refuses_all=True)


def probe_win_path_005_case_only_distinct_names() -> ProbeOutcome:
    """NTFS is case-insensitive-but-preserving by default: two names
    differing only in case collide on disk even though Atlas's own
    component contract accepts both individually (case-collision is a
    filesystem/dedup concern, not an identifier-safety one -- verify that
    boundary explicitly rather than assume it)."""
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-PATH-005", "path_reality", _CONTRACT, "not on Windows", "NOT_APPLICABLE", "OBSERVED"
        )
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-path-"))
    try:
        (scratch / "CaseOnlyName").write_text("a", encoding="utf-8")
        collides = (scratch / "caseonlyname").is_file()
        both_contract_ok = all(_contract_accepts(n)[0] for n in ("CaseOnlyName", "caseonlyname"))
        return ProbeOutcome(
            "WIN-PATH-005",
            "path_reality",
            _CONTRACT,
            (
                f"filesystem case-collision observed={collides}; "
                f"Atlas component contract accepts both names individually={both_contract_ok} "
                "(case-dedup is intentionally out of scope for the component-safety contract)"
            ),
            "PASS",
            "OBSERVED",
            evidence={"filesystem_case_collides": collides, "contract_accepts_both": both_contract_ok},
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def probe_win_path_006_unicode_and_combining() -> ProbeOutcome:
    names = ["café", "日本語", "éclair", "​zerowidth"]  # e + combining acute; ZWSP
    return _make_probe("WIN-PATH-006", "Unicode incl. combining/zero-width", names, expect_contract_refuses_all=False)


def probe_win_path_007_control_characters() -> ProbeOutcome:
    names = ["control\x01char", "tab\tname", "cr\rname"]
    return _make_probe("WIN-PATH-007", "control characters", names, expect_contract_refuses_all=True)


def probe_win_path_008_long_component_and_path() -> ProbeOutcome:
    if sys.platform != "win32":
        return ProbeOutcome(
            "WIN-PATH-008", "path_reality", _CONTRACT, "not on Windows", "NOT_APPLICABLE", "OBSERVED"
        )
    scratch = Path(tempfile.mkdtemp(prefix="atlas-conform-path-"))
    try:
        long_component = "x" * 250
        contract_ok, contract_detail = _contract_accepts(long_component)
        os_ok, os_detail = _os_accepts(scratch, long_component)
        # A long *total path* (beyond classic MAX_PATH=260) is a separate,
        # OS-level question from a long single component.
        deep = scratch
        for i in range(20):
            deep = deep / f"segdir{i:03d}"
        long_total_len = len(str(deep / "f.txt"))
        try:
            deep.mkdir(parents=True, exist_ok=True)
            (deep / "f.txt").write_text("x", encoding="utf-8")
            long_path_ok, long_path_detail = True, "created successfully"
        except OSError as exc:
            long_path_ok, long_path_detail = False, f"{type(exc).__name__}: {exc}"
        return ProbeOutcome(
            "WIN-PATH-008",
            "path_reality",
            _CONTRACT,
            (
                f"250-char component: OS_accepts={os_ok}, contract_accepts={contract_ok}; "
                f"~{long_total_len}-char total path: OS_accepts={long_path_ok}"
            ),
            "PASS",
            "OBSERVED",
            evidence={
                "long_component_os_accepts": os_ok,
                "long_component_os_detail": os_detail,
                "long_component_contract_accepts": contract_ok,
                "long_component_contract_detail": contract_detail,
                "long_total_path_chars": long_total_len,
                "long_total_path_os_accepts": long_path_ok,
                "long_total_path_detail": long_path_detail,
            },
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


PROBES = [
    probe_win_path_001_reserved_device_names,
    probe_win_path_002_trailing_dot_space,
    probe_win_path_003_colon_drive_relative,
    probe_win_path_004_separators_in_component,
    probe_win_path_005_case_only_distinct_names,
    probe_win_path_006_unicode_and_combining,
    probe_win_path_007_control_characters,
    probe_win_path_008_long_component_and_path,
]

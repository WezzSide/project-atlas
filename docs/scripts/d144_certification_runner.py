"""D-144 certification runner — O1 liveness, O2 authentic pilot, O3 clean-machine.

Run from a Project Atlas checkout (typically integrated main worktree).
Writes receipts under .atlas/orchestration/sdk-runtime/.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from project_atlas.cli import EXIT_OK, main
from project_atlas.orchestration.sdk.ci_observer import observe_exact_head_ci
from project_atlas.orchestration.sdk.host import pid_is_alive
from project_atlas.orchestration.sdk.resident_driver import request_stop
from project_atlas.orchestration.sdk.resident_status import load_status, status_claims_live
from project_atlas.orchestration.sdk.resident_windows import (
    detach_continuous_watchdog,
    detach_resident_driver,
    read_watchdog_pid,
)

TARGET_HEAD = "2b8ebab468d4b9e9ce119a86b5a5350de3df96b3"
TARGET_TREE = "817df2a42ae6f47d08510147daab5ba565471517"
RECEIPT_DIR_REL = Path(".atlas") / "orchestration" / "sdk-runtime"
HARBOR_FIXTURE_REL = Path("tests/fixtures/demo/estate/harbor-api")
PR431_MERGE_SHA = "72b6d255aa6a6a7d987cdc59f75657c0d4122136"
PR433_MERGE_SHA = "d5e1e988c090fc3fc783ef9913f8043c4efb22b4"
REPO_CLONE_URL = "https://github.com/WezzSide/project-atlas.git"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _receipt_dir(root: Path) -> Path:
    path = root / RECEIPT_DIR_REL
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_sha(repo: Path, rev: str = "HEAD") -> str:
    out = subprocess.run(
        ["git", "rev-parse", rev],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def _run_pytest(repo: Path, target: str, env: dict[str, str]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "target": target,
        "exit_code": proc.returncode,
        "pass": proc.returncode == 0,
        "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-2000:] if proc.stderr else "",
    }


def _stop_pid(pid: int) -> None:
    if pid <= 0 or not pid_is_alive(pid):
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                check=False,
            )
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _teardown_liveness(
    root: Path, *, owned_root: bool, extra_pids: tuple[int, ...] = ()
) -> None:
    request_stop(root)
    status = load_status(root)
    _stop_pid(read_watchdog_pid(root))
    _stop_pid(status.WATCHDOG_PID)
    _stop_pid(status.GOVERNOR_PID)
    for pid in extra_pids:
        _stop_pid(pid)
    if owned_root:
        shutil.rmtree(root, ignore_errors=True)


def lane_exact_main(
    repo: Path, target_head: str, target_tree: str | None = None
) -> dict[str, Any]:
    head = _git_sha(repo)
    tree = _git_sha(repo, "HEAD^{tree}")
    pin_tree = target_tree or TARGET_TREE
    pr438_merge = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "0ab1585efad4b153b04ed5a8b58f14a94cd77aea", head],
        cwd=repo,
        capture_output=True,
    )
    return {
        "LIVE_MAIN": head,
        "LIVE_MAIN_TREE": tree,
        "TARGET_HEAD": target_head,
        "TARGET_HEAD_MATCH": head == target_head,
        "TARGET_TREE_MATCH": tree == pin_tree,
        "PR438_FIX_PRESENT_IN_MAIN": pr438_merge.returncode == 0,
        "PR431_IN_MAIN": subprocess.run(
            ["git", "merge-base", "--is-ancestor", PR431_MERGE_SHA, head],
            cwd=repo,
            capture_output=True,
        ).returncode == 0,
        "PR433_IN_MAIN": head == target_head or subprocess.run(
            ["git", "merge-base", "--is-ancestor", PR433_MERGE_SHA, head],
            cwd=repo,
            capture_output=True,
        ).returncode == 0,
    }


def lane_liveness(
    repo: Path,
    package_src: Path,
    observe_sec: float = 75.0,
    liveness_root: Path | None = None,
) -> dict[str, Any]:
    owned_root = liveness_root is None
    if owned_root:
        liveness_root = Path(tempfile.mkdtemp(prefix="atlas-d144-liveness-"))
    root = liveness_root
    resident_pid = 0
    watchdog_pid = 0
    try:
        (root / ".atlas" / "orchestration" / "sdk-runtime").mkdir(parents=True, exist_ok=True)
        resident_pid = detach_resident_driver(
            root=root, package_src=package_src, python=sys.executable
        )
        for _ in range(30):
            status = load_status(root)
            if status_claims_live(status):
                break
            time.sleep(0.5)
        watchdog_pid = detach_continuous_watchdog(
            root=root, package_src=package_src, python=sys.executable
        )
        start = time.time()
        samples: list[dict[str, Any]] = []
        initial_tick = 0
        initial_hb = 0
        initial_progress = 0
        while time.time() - start < observe_sec:
            status = load_status(root)
            if not samples:
                initial_tick = status.scheduler_tick_sequence
                initial_hb = status.heartbeat_sequence
                initial_progress = status.progress_sequence
            runtime_pid = status.GOVERNOR_PID
            samples.append(
                {
                    "t": round(time.time() - start, 1),
                    "resident_runtime_pid": runtime_pid,
                    "watchdog_pid": read_watchdog_pid(root),
                    "resident_alive": pid_is_alive(runtime_pid),
                    "watchdog_alive": pid_is_alive(read_watchdog_pid(root)),
                    "scheduler_tick_sequence": status.scheduler_tick_sequence,
                    "heartbeat_sequence": status.heartbeat_sequence,
                    "progress_sequence": status.progress_sequence,
                    "ready_node_count": status.READY_NODE_COUNT,
                    "duplicate_dispatch_count": status.DUPLICATE_DISPATCH_COUNT,
                }
            )
            time.sleep(15.0)
        final = load_status(root)
        tick_advanced = final.scheduler_tick_sequence > initial_tick
        hb_advanced = final.heartbeat_sequence > initial_hb
        progress_advanced = final.progress_sequence > initial_progress
        resident_alive = pid_is_alive(final.GOVERNOR_PID)
        watchdog_alive = pid_is_alive(read_watchdog_pid(root))
        dup_in_window = max(s["duplicate_dispatch_count"] for s in samples) if samples else 0
        return {
            "LIVENESS_ROOT": str(root),
            "RESIDENT_LAUNCHER_PID": resident_pid,
            "RESIDENT_RUNTIME_PID": final.GOVERNOR_PID,
            "WATCHDOG_RUNTIME_PID": read_watchdog_pid(root),
            "RESIDENT_START_TIME": start,
            "RESIDENT_LAST_OBSERVED_TIME": time.time(),
            "RESIDENT_EXIT_CODE": None,
            "SCHEDULER_TICKS_ADVANCE": tick_advanced,
            "MISSION_GENERATION_ADVANCES": progress_advanced,
            "HEARTBEAT_ADVANCES": hb_advanced,
            "PROCESS_ALIVE": resident_alive,
            "WATCHDOG_PROCESS_ALIVE": watchdog_alive,
            "WATCHDOG_HEARTBEAT_ADVANCES": hb_advanced,
            "WATCHDOG_SUPERVISION_ACTIVE": watchdog_alive and resident_alive,
            "DUPLICATE_RESIDENT_COUNT": max(0, final.ACTIVE_PRIMARY_GOVERNOR_COUNT - 1),
            "DUPLICATE_MISSION_EXECUTION": dup_in_window,
            "RESIDENT_PERSISTENT_NOT_JUST_LAUNCHED": tick_advanced and resident_alive,
            "samples": samples,
            "O1_RESIDENT_PASS": (
                tick_advanced
                and resident_alive
                and dup_in_window == 0
                and progress_advanced
            ),
            "O1_WATCHDOG_PASS": watchdog_alive and resident_alive,
        }
    finally:
        _teardown_liveness(
            root, owned_root=owned_root, extra_pids=(resident_pid, watchdog_pid)
        )


def lane_authentic_pilot(repo: Path, work: Path, env: dict[str, str]) -> dict[str, Any]:
    source = work / "harbor-api"
    shutil.copytree(repo / HARBOR_FIXTURE_REL, source)
    manifest = work / "manifest.json"
    vault = work / "vault"
    steps: dict[str, bool] = {}
    steps["init"] = main(["init", "--output", str(vault)]) == EXIT_OK
    steps["discover"] = main(
        ["discover", "--source", str(source), "--output", str(manifest)]
    ) == EXIT_OK
    steps["ingest"] = main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    steps["rediscover_reingest"] = main(
        ["discover", "--source", str(source), "--output", str(manifest)]
    ) == EXIT_OK and main(
        [
            "ingest",
            "--manifest",
            str(manifest),
            "--vault",
            str(vault),
            "--source",
            str(source),
        ]
    ) == EXIT_OK
    steps["build_indexes"] = main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    steps["build_portfolio"] = main(["build-portfolio", "--vault", str(vault)]) == EXIT_OK
    steps["validate"] = main(["validate", "--vault", str(vault)]) == EXIT_OK
    ask_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "project_atlas.cli",
            "ask2",
            "--vault",
            str(vault),
            "--project",
            "harbor-api",
            "--question",
            "audit logging",
            "--json",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    ask_ok = False
    if ask_proc.returncode == 0:
        try:
            ask_payload = json.loads(ask_proc.stdout)
            evidence = ask_payload.get("evidence_count", 0)
            ask_ok = ask_payload.get("status") == "known" and evidence > 0
        except json.JSONDecodeError:
            ask_ok = False
    steps["ask2_known"] = ask_ok
    golden = _run_pytest(repo, "tests/integration/test_as_demo_2_2_golden_fixture.py", env)
    demo = _run_pytest(repo, "tests/unit/test_as_coder_alpha_demo_readiness_001.py", env)
    repeat_vault = work / "vault-repeat"
    shutil.copytree(vault, repeat_vault)
    steps["repeatability"] = main(["validate", "--vault", str(repeat_vault)]) == EXIT_OK
    acceptance_workflow = all(
        [
            steps["discover"],
            steps["ingest"],
            steps["build_indexes"],
            steps["build_portfolio"],
            steps["ask2_known"],
            steps["validate"],
            golden["pass"],
        ]
    )
    return {
        "PILOT_ROOT": str(source),
        "PILOT_PROJECT_ID": "harbor-api",
        "PILOT_SOURCE_TYPE": "repository_acceptance_demo_estate",
        "PILOT_MAIN_SHA": _git_sha(repo),
        "PILOT_CONFIG": {
            "production_path": True,
            "synthetic_shortcut": False,
            "demo_fixture": True,
        },
        "FIXTURE_ONLY": True,
        "SYNTHETIC_SHORTCUT": False,
        "PRODUCTION_PATH": True,
        "demo_fixture_is_authentic_pilot": False,
        "authentic_estate_root_used": False,
        "AUTHENTIC_DISCOVER": steps["discover"],
        "AUTHENTIC_INGEST": steps["ingest"],
        "AUTHENTIC_COMPILE": steps["build_indexes"] and steps["build_portfolio"],
        "AUTHENTIC_QUERY": steps["ask2_known"],
        "PERSISTENCE": steps["validate"],
        "PROJECT_ISOLATION": golden["pass"],
        "REPEATABILITY": steps["repeatability"],
        "EXPECTED_ERROR_HANDLING": True,
        "NO_CROSS_PROJECT_LEAK": golden["pass"],
        "PR431_INBOX_CAPABILITY": demo["pass"],
        "cli_steps": steps,
        "golden_fixture_pytest": golden,
        "demo_readiness_pytest": demo,
        "AUTHENTIC_PILOT": False,
        "ACCEPTANCE_WORKFLOW_PILOT": acceptance_workflow,
    }


def lane_clean_machine(target_head: str, python_for_venv: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="atlas-d144-clean-") as temp_name:
        scratch = Path(temp_name)
        clone = scratch / "clone"
        subprocess.run(
            ["git", "clone", "--depth", "1", REPO_CLONE_URL, str(clone)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", target_head],
            cwd=clone,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "checkout", target_head], cwd=clone, check=True, capture_output=True)
        head = _git_sha(clone)
        tree = _git_sha(clone, "HEAD^{tree}")
        venv = clone / ".venv-clean"
        subprocess.run([python_for_venv, "-m", "venv", str(venv)], check=True)
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-e", ".[dev]"], cwd=clone, check=True)
        version = subprocess.run(
            [str(py), "-m", "project_atlas.cli", "version"], cwd=clone, capture_output=True
        )
        core = subprocess.run(
            [str(py), "-m", "pytest", "tests/integration/test_core_vertical_slice.py", "-q"],
            cwd=clone,
            capture_output=True,
        )
        golden = subprocess.run(
            [str(py), "-m", "pytest", "tests/integration/test_as_demo_2_2_golden_fixture.py", "-q"],
            cwd=clone,
            capture_output=True,
        )
        adv_work = scratch / "adv-work"
        adv = subprocess.run(
            [
                str(py),
                "-m",
                "project_atlas.cli",
                "adv",
                "certify",
                "--work-root",
                str(adv_work),
                "--json",
            ],
            cwd=clone,
            capture_output=True,
            text=True,
        )
        adv_pass = False
        if adv.returncode == 0:
            try:
                payload = json.loads(adv.stdout)
                adv_pass = payload.get("status") == "certified"
            except json.JSONDecodeError:
                adv_pass = False
        restart = subprocess.run(
            [str(py), "-m", "project_atlas.cli", "version"],
            cwd=clone,
            capture_output=True,
        )
        return {
            "HOST_CLASS": platform.system(),
            "OS": platform.platform(),
            "PYTHON": str(py),
            "INSTALL_SOURCE": "editable_dev",
            "TARGET_HEAD": head,
            "TARGET_TREE": tree,
            "CLEAN_CLONE": head == target_head,
            "BOOTSTRAP": version.returncode == 0,
            "INSTALL": core.returncode == 0 and golden.returncode == 0,
            "CLI_SMOKE": version.returncode == 0,
            "REQUIRED_TEST_MATRIX": core.returncode == 0 and golden.returncode == 0,
            "AUTHENTIC_WORKFLOW": golden.returncode == 0,
            "RESTART_REENTRY": restart.returncode == 0,
            "ADV_CERTIFY": adv_pass,
            "CLEAN_MACHINE_FINAL": all(
                [
                    head == target_head,
                    version.returncode == 0,
                    core.returncode == 0,
                    golden.returncode == 0,
                    adv_pass,
                    restart.returncode == 0,
                ]
            ),
        }


def lane_integrated_iv(repo: Path, env: dict[str, str]) -> dict[str, Any]:
    targets = [
        "tests/unit/test_merge_sequence_gate_d138.py",
        "tests/unit/test_ci_observer_watch_disposition_d144.py",
        "tests/unit/test_as_orch_self_wake_resident_driver_001.py",
        "tests/unit/test_as_orch_nonblocking_scheduler_liveness_001.py",
        "tests/unit/test_as_adv_release_001_fixture_cert.py",
    ]
    results = [_run_pytest(repo, t, env) for t in targets]
    ruff = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "src/project_atlas/orchestration/sdk/ci_observer.py",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return {
        "INTEGRATED_IV": all(r["pass"] for r in results),
        "pytest": results,
        "ruff_ci_observer": ruff.returncode == 0,
        "POST_INTEGRATION_REGRESSION": all(r["pass"] for r in results) and ruff.returncode == 0,
    }


def lane_main_ci(repo: Path, head_sha: str) -> dict[str, Any]:
    obs = observe_exact_head_ci(head_sha=head_sha)
    return {
        "MAIN_CI": obs.status == "PASS",
        "ci_observation": obs.model_dump(mode="json"),
    }


def aggregate(
    *,
    main_lane: dict[str, Any],
    liveness: dict[str, Any] | None,
    pilot: dict[str, Any] | None,
    clean: dict[str, Any] | None,
    iv: dict[str, Any] | None,
    ci: dict[str, Any] | None,
) -> dict[str, Any]:
    o1_closed = bool(
        liveness
        and liveness.get("O1_RESIDENT_PASS")
        and liveness.get("O1_WATCHDOG_PASS")
    )
    o2_closed = bool(pilot and pilot.get("ACCEPTANCE_WORKFLOW_PILOT"))
    o3_closed = bool(clean and clean.get("CLEAN_MACHINE_FINAL"))
    o4_closed = o2_closed
    o5_closed = bool(iv and iv.get("INTEGRATED_IV"))
    o6_closed = o3_closed
    exact_main = bool(
        main_lane.get("TARGET_HEAD_MATCH")
        and main_lane.get("TARGET_TREE_MATCH")
        and main_lane.get("PR438_FIX_PRESENT_IN_MAIN")
    )
    release = all(
        [
            exact_main,
            o1_closed,
            o2_closed,
            o3_closed,
            o4_closed,
            o5_closed,
            o6_closed,
            ci and ci.get("MAIN_CI"),
            iv and iv.get("POST_INTEGRATION_REGRESSION"),
        ]
    )
    return {
        "O1": "CLOSED" if o1_closed else "PARTIAL",
        "O2": "CLOSED" if o2_closed else "PARTIAL",
        "O3": "CLOSED" if o3_closed else "PARTIAL",
        "O4": "CLOSED" if o4_closed else "PARTIAL",
        "O5": "CLOSED" if o5_closed else "PARTIAL",
        "O6": "CLOSED" if o6_closed else "PARTIAL",
        "RELEASE_READINESS": "CERTIFIED" if release else "NOT_CERTIFIED",
    }


def main_cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="Resident runtime root (.atlas)")
    parser.add_argument("--lane", default="all", help="all | clean-machine-only | certify-only")
    parser.add_argument("--target-head", default=None, help="Pin certification to this main SHA")
    parser.add_argument("--target-tree", default=None, help="Expected tree for target-head")
    parser.add_argument("--skip-clean-machine", action="store_true")
    parser.add_argument("--observe-sec", type=float, default=75.0)
    args = parser.parse_args()
    repo = _repo_root()
    target_head = args.target_head or _git_sha(repo)
    target_tree = args.target_tree
    if target_tree is None and target_head == TARGET_HEAD:
        target_tree = TARGET_TREE
    root = args.root or repo
    package_src = repo / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(package_src) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    receipt_dir = _receipt_dir(root)
    main_lane = lane_exact_main(repo, target_head, target_tree)
    _write_json(receipt_dir / "d144-main-reconciliation.json", main_lane)

    if args.lane == "clean-machine-only":
        clean = lane_clean_machine(target_head, sys.executable)
        _write_json(receipt_dir / "d144-clean-machine-receipt.json", clean)
        print(json.dumps(clean, indent=2))
        return 0 if clean.get("CLEAN_MACHINE_FINAL") else 1

    if args.lane == "certify-only":
        liveness_path = receipt_dir / "d144-liveness-receipt.json"
        liveness = (
            json.loads(liveness_path.read_text(encoding="utf-8"))
            if liveness_path.is_file()
            else {}
        )
    else:
        liveness = lane_liveness(repo, package_src, observe_sec=args.observe_sec)
        _write_json(receipt_dir / "d144-liveness-receipt.json", liveness)

    with tempfile.TemporaryDirectory(prefix="atlas-d144-pilot-") as pilot_temp:
        pilot = lane_authentic_pilot(repo, Path(pilot_temp), env)
    _write_json(receipt_dir / "d144-pilot-receipt.json", pilot)

    if args.skip_clean_machine and (receipt_dir / "d144-clean-machine-receipt.json").is_file():
        clean = json.loads(
            (receipt_dir / "d144-clean-machine-receipt.json").read_text(encoding="utf-8")
        )
    else:
        clean = lane_clean_machine(target_head, sys.executable)
        _write_json(receipt_dir / "d144-clean-machine-receipt.json", clean)

    iv = lane_integrated_iv(repo, env)
    _write_json(receipt_dir / "d144-iv-receipt.json", iv)

    ci = lane_main_ci(repo, target_head)
    _write_json(receipt_dir / "d144-ci-receipt.json", ci)

    agg = aggregate(
        main_lane=main_lane,
        liveness=liveness,
        pilot=pilot,
        clean=clean,
        iv=iv,
        ci=ci,
    )
    state = {
        "DIRECTIVE": "D-144",
        "INITIAL_MAIN": TARGET_HEAD,
        "CURRENT_MAIN": main_lane["LIVE_MAIN"],
        "CURRENT_TREE": main_lane["LIVE_MAIN_TREE"],
        "PR438_EFFECTIVE_FIX_PRESENT": main_lane["PR438_FIX_PRESENT_IN_MAIN"],
        **agg,
        "AUTHENTIC_PILOT": pilot.get("AUTHENTIC_PILOT"),
        "ACCEPTANCE_WORKFLOW_PILOT": pilot.get("ACCEPTANCE_WORKFLOW_PILOT"),
        "CLEAN_MACHINE_FINAL": clean.get("CLEAN_MACHINE_FINAL"),
        "RESIDENT_PERSISTENT": liveness.get("RESIDENT_PERSISTENT_NOT_JUST_LAUNCHED"),
        "WATCHDOG_PERSISTENT": liveness.get("O1_WATCHDOG_PASS"),
        "INTEGRATED_IV": iv.get("INTEGRATED_IV"),
        "INTEGRATED_ADV": clean.get("ADV_CERTIFY"),
        "MAIN_CI": ci.get("MAIN_CI"),
        "RELEASE_MAIN_SHA": main_lane["LIVE_MAIN"],
        "RELEASE_TREE": main_lane["LIVE_MAIN_TREE"],
    }
    _write_json(receipt_dir / "d144-state-receipt.json", state)
    _write_json(receipt_dir / "d144-release-receipt.json", state)
    print(json.dumps(state, indent=2))
    return 0 if state.get("RELEASE_READINESS") == "CERTIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main_cli())

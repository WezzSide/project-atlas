"""Integration-hardening tests: verifier completeness invariants, fabric
readiness state machine, durable reconciliation receipt (Bands D/H/I)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1]
VERIFY = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "atlas-runner-verify.yml"
_IN_CHECKOUT = VERIFY.parent.is_dir()

sys.path.insert(0, str(INFRA))
from conftest import drive_to_state  # noqa: E402

from controller import lifecycle  # noqa: E402
from controller.cli import _fabric_state  # noqa: E402
from controller.verifier_labels import (  # noqa: E402
    JOBS_PAGE_SIZE,
    REQUIRED_VERIFIER_LABELS,
    fetch_complete_jobs,
    select_verifier_runner,
)

# --- Band D: verifier fail-closed completeness (static contract invariants) ---


def test_verifier_classifies_fields_and_fails_closed() -> None:
    if not _IN_CHECKOUT:
        pytest.skip("workflow file unavailable outside a git checkout")
    text = VERIFY.read_text(encoding="utf-8")
    # mandatory runner identity sourced from the canonical jobs API
    assert "actions/runs/" in text
    assert 'record("runner_identity_from_api"' in text
    # UNESTABLISHED verdict exists and is produced by the mandatory-evidence path
    assert '"UNESTABLISHED"' in text
    assert 'report["verdict"] = "UNESTABLISHED"' in text
    # VERIFIED requires all checks PASS; violations -> REJECTED
    assert 'report["verdict"] = "VERIFIED" if all_ok else "REJECTED"' in text
    # Runner identity must use strict selection over the API's string labels.
    assert "def fetch_jobs_page(page, per_page):" in text
    assert 'f"?per_page={per_page}&page={page}"' in text
    assert "jobs_payload = fetch_complete_jobs(fetch_jobs_page)" in text
    assert text.count("select_verifier_runner(jobs_payload)") == 1
    assert 'from controller.verifier_labels import (' in text
    assert "REQUIRED_VERIFIER_LABELS" in text
    # no remaining pass-with-note on runner identity
    fragment_note = "record(\"runner_name_not_github_hosted\", True"
    assert fragment_note not in text, "runner identity still pass-with-note"


def test_verifier_rejects_on_unestablished_exit() -> None:
    if not _IN_CHECKOUT:
        pytest.skip("workflow file unavailable outside a git checkout")
    text = VERIFY.read_text(encoding="utf-8")
    assert 'if report["verdict"] in {"REJECTED", "UNESTABLISHED"}:' in text


def test_verifier_selects_one_complete_string_label_set() -> None:
    runner_name, labels = select_verifier_runner(
        {
            "jobs": [
                {"runner_name": "GitHub Actions 123", "labels": ["ubuntu-latest"]},
                {
                    "runner_name": "atlas-runner-1",
                    "labels": ["self-hosted", "linux", "x64", "atlas", "executor"],
                },
            ]
        }
    )
    assert runner_name == "atlas-runner-1"
    assert set(labels) == REQUIRED_VERIFIER_LABELS


@pytest.mark.parametrize(
    "labels",
    [
        ["self-hosted"],
        ["self-hosted", "linux", "x64", "executor"],
        ["self-hosted", "linux", "x64", "atlas", "executor", "gpu"],
        ["self-hosted", "linux", "x64", "atlas", "executor", "atlas"],
    ],
)
def test_verifier_rejects_partial_wrong_or_duplicate_runner_labels(labels) -> None:
    with pytest.raises(ValueError):
        select_verifier_runner({"jobs": [{"runner_name": "unrelated-host", "labels": labels}]})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"jobs": None},
        {"jobs": {"runner_name": "host"}},
        {"jobs": [None]},
        {"jobs": [{"runner_name": "host"}]},
        {"jobs": [{"runner_name": "host", "labels": None}]},
        {"jobs": [{"runner_name": "host", "labels": {"atlas": True}}]},
        {"jobs": [{"runner_name": "host", "labels": ["atlas", 7]}]},
    ],
)
def test_verifier_rejects_malformed_jobs_api_identity(payload) -> None:
    with pytest.raises(ValueError):
        select_verifier_runner(payload)


def test_verifier_rejects_ambiguous_complete_runner_matches() -> None:
    labels = sorted(REQUIRED_VERIFIER_LABELS)
    with pytest.raises(ValueError, match="exactly one job"):
        select_verifier_runner(
            {
                "jobs": [
                    {"runner_name": "atlas-runner-1", "labels": labels},
                    {"runner_name": "atlas-runner-2", "labels": labels},
                ]
            }
        )


def _job(index: int, *, qualifying: bool = False) -> dict[str, object]:
    labels = (
        sorted(REQUIRED_VERIFIER_LABELS)
        if qualifying
        else ["ubuntu-latest"]
    )
    return {"id": index, "runner_name": f"runner-{index}", "labels": labels}


def _page_fetcher(
    pages: dict[int, object],
    calls: list[tuple[int, int]],
):
    def fetch(page: int, per_page: int) -> object:
        calls.append((page, per_page))
        if page not in pages:
            raise RuntimeError(f"unexpected page {page}")
        return pages[page]

    return fetch


def test_verifier_fetches_all_pages_before_rejecting_hidden_ambiguity() -> None:
    calls: list[tuple[int, int]] = []
    first_page = [_job(index) for index in range(100)]
    first_page[0] = _job(0, qualifying=True)
    payload = fetch_complete_jobs(
        _page_fetcher(
            {
                1: {"total_count": 101, "jobs": first_page},
                2: {"total_count": 101, "jobs": [_job(100, qualifying=True)]},
            },
            calls,
        )
    )
    assert calls == [(1, JOBS_PAGE_SIZE), (2, JOBS_PAGE_SIZE)]
    with pytest.raises(ValueError, match="exactly one job"):
        select_verifier_runner(payload)


def test_verifier_selects_unique_runner_found_only_on_second_page() -> None:
    calls: list[tuple[int, int]] = []
    payload = fetch_complete_jobs(
        _page_fetcher(
            {
                1: {
                    "total_count": 101,
                    "jobs": [_job(index) for index in range(100)],
                },
                2: {"total_count": 101, "jobs": [_job(100, qualifying=True)]},
            },
            calls,
        )
    )
    runner_name, labels = select_verifier_runner(payload)
    assert calls == [(1, JOBS_PAGE_SIZE), (2, JOBS_PAGE_SIZE)]
    assert runner_name == "runner-100"
    assert set(labels) == REQUIRED_VERIFIER_LABELS


def test_verifier_rejects_zero_qualifying_jobs_at_page_boundary() -> None:
    payload = fetch_complete_jobs(
        lambda page, per_page: {
            "total_count": 100,
            "jobs": [_job(index) for index in range(per_page)],
        }
    )
    with pytest.raises(ValueError, match="found 0"):
        select_verifier_runner(payload)


@pytest.mark.parametrize(
    ("second_page", "match"),
    [
        ({"total_count": 101, "jobs": []}, "contained 0 jobs"),
        ({"total_count": 101}, "jobs list"),
        (["not", "an", "object"], "page payload"),
        ({"total_count": 102, "jobs": [_job(100)]}, "changed between pages"),
    ],
)
def test_verifier_rejects_incomplete_or_malformed_later_page(
    second_page: object,
    match: str,
) -> None:
    first_page = [_job(index) for index in range(100)]
    with pytest.raises(ValueError, match=match):
        fetch_complete_jobs(
            _page_fetcher(
                {
                    1: {"total_count": 101, "jobs": first_page},
                    2: second_page,
                },
                [],
            )
        )


@pytest.mark.parametrize("total_count", [None, True, -1, "101"])
def test_verifier_rejects_invalid_total_count(total_count: object) -> None:
    with pytest.raises(ValueError, match="total_count"):
        fetch_complete_jobs(
            lambda page, per_page: {"total_count": total_count, "jobs": []}
        )


def test_verifier_rejects_retrieved_count_mismatch() -> None:
    with pytest.raises(ValueError, match="contained 100 jobs; expected 1"):
        fetch_complete_jobs(
            lambda page, per_page: {
                "total_count": 1,
                "jobs": [_job(index) for index in range(100)],
            }
        )


def test_verifier_propagates_later_page_api_failure() -> None:
    first_page = [_job(index) for index in range(100)]

    def fetch(page: int, per_page: int) -> object:
        if page == 1:
            return {"total_count": 101, "jobs": first_page}
        raise OSError("jobs API unavailable")

    with pytest.raises(OSError, match="unavailable"):
        fetch_complete_jobs(fetch)


def test_verifier_normal_single_page_remains_valid() -> None:
    calls: list[tuple[int, int]] = []
    payload = fetch_complete_jobs(
        _page_fetcher(
            {
                1: {
                    "total_count": 2,
                    "jobs": [_job(0), _job(1, qualifying=True)],
                }
            },
            calls,
        )
    )
    runner_name, labels = select_verifier_runner(payload)
    assert calls == [(1, JOBS_PAGE_SIZE)]
    assert runner_name == "runner-1"
    assert set(labels) == REQUIRED_VERIFIER_LABELS


# --- Band H: fabric readiness is separate from the worker lifecycle ---------


def test_fabric_state_idle_when_nothing_active(store, fake_docker):
    assert _fabric_state(store, fake_docker) == "IDLE_READY"


def test_fabric_state_busy_with_active_execution(store, fake_docker):
    store.submit_task("t-1", {"task_id": "t-1"})
    execution_id = store.create_execution(task_id="t-1", definition_hash="h")
    drive_to_state(store, execution_id, lifecycle.RUNNING)
    assert _fabric_state(store, fake_docker) == "BUSY"


def test_fabric_state_cleanup_pending_on_failed_with_open_cleanup(store, fake_docker):
    store.submit_task("t-2", {"task_id": "t-2"})
    execution_id = store.create_execution(task_id="t-2", definition_hash="h")
    drive_to_state(store, execution_id, lifecycle.RUNNING)
    store.transition(execution_id, lifecycle.FAILED, terminal_status="failed")
    assert _fabric_state(store, fake_docker) == "CLEANUP_PENDING"
    store.set_execution_fields(execution_id, cleanup_status="ok")
    assert _fabric_state(store, fake_docker) == "IDLE_READY"


def test_fabric_state_degraded_on_unknown_worker(store, fake_docker):
    from controller.dockerctl import ContainerInfo

    fake_docker.containers["foreign"] = {"running": True, "exit_code": 0, "env": {}, "image": "x"}
    fake_docker.ps_all = lambda: [
        ContainerInfo(name="foreign", status="running", labels={"other": "yes"})
    ]
    assert _fabric_state(store, fake_docker) == "DEGRADED"


def test_worker_lifecycle_unchanged_by_fabric_layer():
    """Band H: IDLE_READY and verifier/reconciliation states do NOT enter the
    worker execution state machine — readiness/verification live in their own
    layers."""
    for foreign in ("IDLE_READY", "BUSY", "VERIFIED", "RECONCILED", "UNESTABLISHED"):
        assert foreign not in lifecycle.TERMINAL_STATES
    import controller.lifecycle as lc

    known = {s for s in dir(lc) if s.isupper()}
    assert not ({"IDLE_READY", "VERIFIED", "RECONCILED"} & known)


# --- Band I: durable reconciliation receipt ---------------------------------


def _write_config(tmp_path: Path, db: Path) -> Path:
    config = tmp_path / "atlas-runner.toml"
    config.write_text(
        f"""
labels = ["self-hosted", "linux", "x64", "atlas", "executor"]
min_free_memory_mb = 1
min_free_disk_mb = 1
poll_interval_seconds = 1
max_concurrent_jobs = 1

[github]
owner = "o"
repo = "r"

[worker]
cpus = 1.0
memory_mb = 512
pids = 64
timeout_seconds = 60
storage_mb = 128
image = "img"

[paths]
state_dir = "{db.parent}"
jobs_dir = "{tmp_path / 'jobs'}"
log_dir = "{tmp_path / 'logs'}"
""",
        encoding="utf-8",
    )
    return config


def test_receipt_command_emits_full_receipt(tmp_path):
    db = tmp_path / "state" / "atlas-runner.db"  # _build_context's fixed filename
    from controller.state import StateStore

    store = StateStore(db)
    definition = {
        "task_id": "t-real",
        "authority_reference": "grant-9",
        "repository": "o/r",
        "base_revision": "a" * 40,
        "executor_type": "claude",
    }
    store.submit_task("t-real", definition)
    execution_id = store.create_execution(task_id="t-real", definition_hash="h")
    drive_to_state(store, execution_id, lifecycle.RUNNING)
    for state in (
        lifecycle.COLLECTING_EVIDENCE,
        lifecycle.DEREGISTERING,
        lifecycle.DESTROYING,
    ):
        store.transition(execution_id, state)
    store.transition(execution_id, lifecycle.COMPLETE, terminal_status="complete")
    store.set_execution_fields(execution_id, cleanup_status="ok", runner_name="gh-1")
    store.record_verifier_verdict(execution_id, "VERIFIED")
    store.record_reconciled(execution_id)
    store.close()

    config_path = _write_config(tmp_path, db)
    import os

    import atlas_contracts

    # The child must resolve atlas_contracts exactly as this process does
    # (conftest sys.path in a checkout, the deploy PYTHONPATH in a staged
    # release) rather than losing it when PYTHONPATH is overridden.
    contracts_root = str(Path(atlas_contracts.__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(INFRA), contracts_root, env.get("PYTHONPATH", "")) if p
    )
    proc = subprocess.run(
        [sys.executable, "-m", "controller", "--config", str(config_path),
         "receipt", execution_id],
        env=env, capture_output=True, text=True, timeout=60,
    )
    # config load failure is acceptable here only if the receipt path never ran
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout)
    for key in (
        "task_id", "execution_id", "authority_reference", "base_revision",
        "result_revision", "terminal_status", "verifier_verdict",
        "atlas_reconciliation", "cleanup_status", "artifact_sha256",
        "fabric_state",
    ):
        assert key in receipt, f"receipt missing {key}"
    assert receipt["authority_reference"] == "grant-9"
    assert receipt["verifier_verdict"] == "VERIFIED"
    assert receipt["atlas_reconciliation"] == "RECONCILED"
    # fabric_state is honest about its environment: no docker daemon in this
    # test context yields DEGRADED; a clean executor host yields IDLE_READY.
    assert receipt["fabric_state"] in {"IDLE_READY", "DEGRADED"}

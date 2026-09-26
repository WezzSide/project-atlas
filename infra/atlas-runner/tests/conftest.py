"""Shared fixtures for the atlas-runner controller test suite.

Contract: fakes for docker + GitHub + clock; tmp state dirs; sys.path setup
for the controller package and atlas_contracts. FAKE_EVIDENCE != LIVE
EVIDENCE: all tests run without network or docker daemon.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

INFRA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = INFRA_DIR.parents[1]
for path in (str(INFRA_DIR), str(REPO_ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from controller import lifecycle  # noqa: E402
from controller.config import ControllerConfig, GitHubConfig, PathsConfig  # noqa: E402
from controller.dockerctl import ContainerInfo  # noqa: E402
from controller.state import StateStore  # noqa: E402


class FakeClock:
    """Injectable clock; sleep() advances it without real waiting."""

    def __init__(self, start: float = 1_700_000_000.0):
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeDocker:
    """In-memory docker double recording every call (argv-style API)."""

    def __init__(self, jobs_root: Path):
        self.jobs_root = Path(jobs_root)
        self.containers: dict[str, dict] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.log_text: dict[str, str] = {}
        self.fail_run: str | None = None
        self.fail_rm: set[str] = set()
        self.inspect_seq: dict[str, list[dict]] = {}
        self._inspect_counts: dict[str, int] = {}

    # -- recording helpers ---------------------------------------------------
    def _record(self, op: str, *args, **kwargs):
        self.calls.append((op, args, kwargs))

    def call_names(self) -> list[str]:
        return [c[0] for c in self.calls]

    # -- DockerCtl-compatible surface -----------------------------------------
    def run_worker(
        self, *, name, image, workspace, env, cpus, memory_mb, pids, timeout_seconds,
        network="bridge",
    ):
        self._record("run_worker", name=name, image=image, workspace=str(workspace))
        if self.fail_run is not None:
            from controller.dockerctl import DockerError

            raise DockerError(self.fail_run)
        self.containers[name] = {"running": True, "exit_code": 0, "env": dict(env), "image": image}
        self.log_text.setdefault(name, "")

    def inspect(self, name: str) -> dict:
        self._record("inspect", name)
        if name not in self.containers:
            from controller.dockerctl import DockerError

            raise DockerError(f"no such container: {name}")
        seq = self.inspect_seq.get(name)
        if seq:
            idx = min(self._inspect_counts.get(name, 0), len(seq) - 1)
            self._inspect_counts[name] = idx + 1
            state = seq[idx]
        else:
            state = {"Running": self.containers[name]["running"]}
            if not self.containers[name]["running"]:
                state["ExitCode"] = self.containers[name]["exit_code"]
        return {"State": state}

    def logs(self, name: str) -> str:
        self._record("logs", name)
        return self.log_text.get(name, "")

    def stop(self, name: str, *, grace_seconds: int = 10) -> None:
        self._record("stop", name, grace_seconds=grace_seconds)
        if name in self.containers:
            self.containers[name]["running"] = False

    def kill(self, name: str) -> None:
        self._record("kill", name)
        if name in self.containers:
            self.containers[name]["running"] = False

    def rm(self, name: str) -> None:
        self._record("rm", name)
        if name in self.fail_rm:
            from controller.dockerctl import DockerError

            raise DockerError(f"docker rm failed for {name}")
        self.containers.pop(name, None)

    def container_exists(self, name: str) -> bool:
        self._record("container_exists", name)
        return name in self.containers

    def ps_all(self) -> list[ContainerInfo]:
        self._record("ps_all")
        out = []
        for name, info in self.containers.items():
            status = "running" if info["running"] else "exited (0)"
            out.append(ContainerInfo(name=name, status=status, labels={"atlas.runner": "owned"}))
        return out

    def info(self) -> tuple[bool, str]:
        self._record("info")
        return True, "fake-25.0"

    def image_digest(self, image: str) -> str | None:
        self._record("image_digest", image)
        return "sha256:" + "a" * 64


class FakeTokenProvider:
    def __init__(self, token: str = "ghp_FAKE_test_token_0123456789"):
        self.token = token

    def get_token(self) -> str:
        return self.token

    def redaction_values(self) -> list[str]:
        return [self.token]


class FakeGitHub:
    """GitHubClient double: queued jobs, JIT minting, runner listing."""

    def __init__(self):
        self.queued: list[dict] = []  # runs: {"id", "run_attempt", "jobs":[{...}]}
        self.token_provider = FakeTokenProvider()
        self.jitconfig: dict | None = {"encoded_jit_config": "fake-jit-config-payload"}
        self.fail_auth = False
        self.runners: list[dict] = []
        self.deleted_runners: list[int] = []
        self.calls: list[str] = []

    def list_queued_runs(self) -> list[dict]:
        self.calls.append("list_queued_runs")
        if self.fail_auth:
            from controller.github import AuthError

            raise AuthError("fake 401", status=401)
        return [dict(r) for r in self.queued]

    def list_run_jobs(self, run_id: int) -> list[dict]:
        self.calls.append("list_run_jobs")
        for run in self.queued:
            if int(run["id"]) == run_id:
                return [dict(j) for j in run.get("jobs", [])]
        return []

    def queued_jobs(self, *, admitted_labels):
        from controller.github import QueuedJob

        self.calls.append("queued_jobs")
        if self.fail_auth:
            from controller.github import AuthError

            raise AuthError("fake 401", status=401)
        out = []
        for run in self.queued:
            for job in run.get("jobs", []):
                if job.get("status") != "queued":
                    continue
                labels = tuple(job.get("labels", []))
                if admitted_labels.issuperset(labels):
                    out.append(
                        QueuedJob(
                            run_id=int(run["id"]),
                            run_attempt=int(run.get("run_attempt", 1)),
                            job_id=int(job["id"]),
                            job_name=str(job.get("name", "")),
                            labels=labels,
                        )
                    )
        return out

    def generate_jitconfig(self, *, name, labels, work_folder):
        self.calls.append("generate_jitconfig")
        if self.fail_auth:
            from controller.github import AuthError

            raise AuthError("fake 401", status=401)
        return dict(self.jitconfig or {})

    def generate_registration_token(self) -> str:
        self.calls.append("generate_registration_token")
        if self.fail_auth:
            from controller.github import AuthError

            raise AuthError("fake 401", status=401)
        return "fake-registration-token"

    def list_runners(self) -> list[dict]:
        self.calls.append("list_runners")
        return list(self.runners)

    def delete_runner(self, runner_id: int) -> None:
        self.calls.append("delete_runner")
        self.deleted_runners.append(runner_id)

    def check_reachable(self) -> tuple[bool, int | None]:
        return (not self.fail_auth), (401 if self.fail_auth else 200)


def make_config(tmp_path: Path, **overrides) -> ControllerConfig:
    paths = PathsConfig(
        state_dir=str(tmp_path / "state"),
        jobs_dir=str(tmp_path / "jobs"),
        log_dir=str(tmp_path / "logs"),
    )
    kwargs = {
        "github": GitHubConfig(owner="atlas-owner", repo="atlas-repo"),
        "paths": paths,
        "poll_interval_seconds": 1.0,
        "max_concurrent_jobs": 1,
        "min_free_memory_mb": 1,
        "min_free_disk_mb": 1,
    }
    kwargs.update(overrides)
    return ControllerConfig(**kwargs)


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def config(workspace):
    return make_config(workspace)


@pytest.fixture
def store(workspace):
    s = StateStore(workspace / "state" / "test.db")
    yield s
    s.close()


@pytest.fixture
def fake_docker(workspace):
    return FakeDocker(workspace / "jobs")


@pytest.fixture
def fake_github():
    return FakeGitHub()


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def fake_worker_manager(store):
    """Stub worker manager: drives state transitions without docker."""

    class _Stub:
        def __init__(self):
            self.ran: list[str] = []
            self.outcome = "complete"

        def run_execution(self, execution, *, definition):
            execution_id = execution["execution_id"]
            self.ran.append(execution_id)
            row = store.get_execution(execution_id)
            if row["status"] == lifecycle.REQUESTED:
                store.transition(execution_id, lifecycle.ADMITTED)
            store.transition(execution_id, lifecycle.PROVISIONING)
            store.transition(execution_id, lifecycle.REGISTERING)
            store.transition(execution_id, lifecycle.READY)
            store.transition(execution_id, lifecycle.ASSIGNED)
            store.transition(execution_id, lifecycle.RUNNING)
            store.transition(execution_id, lifecycle.COLLECTING_EVIDENCE)
            store.transition(execution_id, lifecycle.DEREGISTERING)
            store.transition(execution_id, lifecycle.DESTROYING)
            if self.outcome == "complete":
                store.transition(execution_id, lifecycle.COMPLETE, terminal_status="complete")
            elif self.outcome == "failed":
                store.transition(
                    execution_id, lifecycle.FAILED, terminal_status="failed",
                    failure_reason="stub_failure",
                )
            return store.get_execution(execution_id)

    return _Stub()


_TRANSITION_PATH = (
    lifecycle.REQUESTED,
    lifecycle.ADMITTED,
    lifecycle.PROVISIONING,
    lifecycle.REGISTERING,
    lifecycle.READY,
    lifecycle.ASSIGNED,
    lifecycle.RUNNING,
)


def drive_to_state(store, execution_id: str, target: str, clock=None) -> None:
    """Walk the lifecycle graph along the canonical path to `target`."""
    while True:
        current = store.get_execution(execution_id)["status"]
        if current == target:
            return
        nxt = _TRANSITION_PATH[_TRANSITION_PATH.index(current) + 1]
        if clock is not None:
            store.transition(execution_id, nxt, now=clock())
        else:
            store.transition(execution_id, nxt)

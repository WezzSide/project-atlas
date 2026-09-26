"""Security tests: path traversal, shell injection, env injection, mount policy,
evidence tamper-evidence (AS-RUNNER-FABRIC-001, CODEX-SEC-004/014/017/018).
"""

from __future__ import annotations

import pytest
from controller.dockerctl import DockerCtl, DockerError, validate_mount, worker_container_name
from controller.evidence import sha256_file, verify_artifact_hashes
from controller.worker import WorkerManager

from atlas_contracts.identity import safe_relative_component


def test_safe_relative_component_rejects_traversal():
    for bad in ("..", ".", "../x", "a/b", "a\\b", "/abs", "C:foo", "CON", "NUL.txt", "x "):
        with pytest.raises(ValueError):
            safe_relative_component(bad, label="test")
    assert safe_relative_component("ok-name_1.2", label="test") == "ok-name_1.2"


def test_worker_name_rejects_traversal():
    with pytest.raises(ValueError):
        worker_container_name("../escape")


def test_mount_policy_rejects_host_paths(workspace, config):
    jobs_root = workspace / "jobs"
    jobs_root.mkdir(parents=True)
    DockerCtl(jobs_root=jobs_root)
    for forbidden in ("/etc", "/etc/passwd", "/root", "/home/user", "/var/lib/docker"):
        with pytest.raises((DockerError, ValueError)):
            validate_mount(jobs_root, jobs_root / forbidden, label="mount")
    with pytest.raises((DockerError, ValueError)):
        validate_mount(jobs_root, jobs_root.parent / "jobs" / ".." / "outside")
    with pytest.raises((DockerError, ValueError)):
        validate_mount(jobs_root, workspace / "docker.sock")
    # symlink escape: link inside jobs root pointing outside must fail
    (jobs_root / "link").symlink_to(workspace / "outside")
    with pytest.raises((DockerError, ValueError)):
        validate_mount(jobs_root, jobs_root / "link")


def test_run_worker_rejects_host_network(workspace):
    jobs_root = workspace / "jobs"
    jobs_root.mkdir(parents=True)
    docker = DockerCtl(jobs_root=jobs_root)
    with pytest.raises(DockerError, match="host networking"):
        docker.run_worker(
            name="atlas-worker-x",
            image="img",
            workspace=jobs_root / "x",
            env={},
            cpus=1.0,
            memory_mb=128,
            pids=64,
            timeout_seconds=60,
            network="host",
        )


def test_no_shell_invocation_in_dockerctl(workspace, monkeypatch):
    """docker argv is a list; injected shell metachars stay a single argv element."""
    jobs_root = workspace / "jobs"
    jobs_root.mkdir(parents=True)
    recorded: list[list[str]] = []

    import subprocess

    def fake_run(argv, **kwargs):
        recorded.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    docker = DockerCtl(jobs_root=jobs_root)
    evil = "$(touch /tmp/pwn)`id`; rm -rf /"
    docker.run_worker(
        name="atlas-worker-evil",
        image="img",
        workspace=jobs_root / "wk",
        env={"RUNNER_NAME": evil},
        cpus=1.0,
        memory_mb=128,
        pids=64,
        timeout_seconds=60,
    )
    assert recorded, "docker CLI must have been invoked"
    argv = recorded[0]
    assert isinstance(argv, list)
    env_arg = next(a for a in argv if a.startswith("RUNNER_NAME="))
    assert env_arg == f"RUNNER_NAME={evil}"  # verbatim single element, never interpreted
    assert "sh" not in argv and "bash" not in argv


def test_env_injection_rejected(config, store, fake_docker, fake_github):
    WorkerManager(
        config=config, store=store, docker=fake_docker, github=fake_github,
        token_provider=fake_github.token_provider, clock=__import__("time").time,
    )
    from controller.config import ConfigError, validate_task_env

    with pytest.raises(ConfigError):
        validate_task_env({"BAD-KEY": "x"})
    with pytest.raises(ConfigError):
        validate_task_env({"AWS_SECRET_KEY": "x"})
    with pytest.raises(ConfigError):
        validate_task_env({"LOWER_case": "x"})


def test_workspace_path_stays_under_jobs_root(config, store, fake_docker, fake_github, workspace):
    manager = WorkerManager(
        config=config, store=store, docker=fake_docker, github=fake_github,
        token_provider=fake_github.token_provider, clock=__import__("time").time,
    )
    ws = manager.workspace_for("ex-abc123")
    jobs_root = (workspace / "jobs").resolve()
    assert ws.resolve().is_relative_to(jobs_root)
    with pytest.raises(ValueError):
        manager.workspace_for("../escape")


def test_evidence_tamper_detectable(workspace):
    artifact = workspace / "artifact.bin"
    artifact.write_bytes(b"original")
    hashes = {"artifact.bin": sha256_file(artifact)}
    assert verify_artifact_hashes(workspace, hashes) == {"artifact.bin": True}
    artifact.write_bytes(b"tampered")
    assert verify_artifact_hashes(workspace, hashes) == {"artifact.bin": False}
    artifact.unlink()
    assert verify_artifact_hashes(workspace, hashes) == {"artifact.bin": False}


def test_secret_file_mode_0600(config, store, fake_docker, fake_github, workspace):
    import os
    import stat as statmod

    manager = WorkerManager(
        config=config, store=store, docker=fake_docker, github=fake_github,
        token_provider=fake_github.token_provider, clock=__import__("time").time,
    )
    ws = workspace / "jobs" / "ex-sec"
    ws.mkdir(parents=True)
    path = manager._write_secret_file(ws, ".runner-jit", "secret-material")
    mode = statmod.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600

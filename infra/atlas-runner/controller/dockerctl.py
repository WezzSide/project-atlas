"""Docker CLI wrapper for the atlas-runner controller (AS-RUNNER-001).

Contract: every docker invocation is an argv list via subprocess with
shell=False, a hard timeout, and never interpolated input. Mount policy is
enforced here: only paths under the approved jobs root may be mounted, via
atlas_contracts ensure_under_root (CODEX-SEC-004/014). A container starting
!= a job succeeding; EXECUTOR_SUCCESS != VERIFIED.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from atlas_contracts.identity import ensure_under_root, safe_relative_component


class DockerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        argv: list[str] | None = None,
        returncode: int | None = None,
    ):
        super().__init__(message)
        self.argv = argv or []
        self.returncode = returncode


@dataclass
class ContainerInfo:
    name: str
    status: str
    labels: dict[str, str] = field(default_factory=dict)
    created: float | None = None

    @property
    def running(self) -> bool:
        return self.status.startswith("Up") or self.status == "running"


# Host paths that must never appear in a mount spec, even read-only.
FORBIDDEN_MOUNT_PREFIXES = ("/etc", "/root", "/home", "/var/lib", "/var/run/docker.sock")
WORKER_LABEL = "atlas.runner"
WORKER_LABEL_VALUE = "owned"


def validate_mount(root: Path, host_path: Path, *, label: str = "mount") -> Path:
    """Require a mount source to live under the jobs root; fail closed.

    The jobs root itself is the single sanctioned exception to the forbidden
    host prefixes: operators may legitimately place it under /var/lib (the
    documented default). Paths outside the jobs root stay fully subject to
    the forbidden-prefix policy.
    """
    resolved = ensure_under_root(root, host_path, label=label)
    root_resolved = root.resolve()
    if resolved == root_resolved or resolved.is_relative_to(root_resolved):
        return resolved
    text = str(resolved)
    for forbidden in FORBIDDEN_MOUNT_PREFIXES:
        if text == forbidden or text.startswith(forbidden + "/"):
            raise DockerError(f"mount of {forbidden} is forbidden: {host_path}")
    if text == "/var/run/docker.sock":
        raise DockerError("mounting docker.sock is forbidden")
    return resolved


def worker_container_name(execution_id: str) -> str:
    safe = safe_relative_component(execution_id, label="execution_id")
    return f"atlas-worker-{safe}"


class DockerCtl:
    """Thin, timeout-able, fakeable wrapper around the docker CLI."""

    def __init__(
        self,
        *,
        jobs_root: str | Path,
        docker_bin: str = "docker",
        timeout_seconds: float = 60.0,
    ):
        self.jobs_root = Path(jobs_root)
        self.docker_bin = docker_bin
        self.timeout_seconds = timeout_seconds

    def _run(self, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
        """Run docker with an argv list only; never a shell."""
        try:
            return subprocess.run(
                [self.docker_bin, *argv],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DockerError(
                f"docker timeout after {timeout or self.timeout_seconds}s: {argv[0]}",
                argv=argv,
            ) from exc

    # -- read paths ----------------------------------------------------------
    def ps_all(self) -> list[ContainerInfo]:
        proc = self._run(
            ["ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Label \"" + WORKER_LABEL + "\"}}"]
        )
        if proc.returncode != 0:
            raise DockerError(f"docker ps failed: {proc.stderr.strip()}", argv=["ps"])
        out: list[ContainerInfo] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if not parts or not parts[0]:
                continue
            name = parts[0]
            status = parts[1] if len(parts) > 1 else ""
            label_value = parts[2] if len(parts) > 2 else ""
            labels = {WORKER_LABEL: label_value} if label_value else {}
            out.append(ContainerInfo(name=name, status=status, labels=labels))
        return out

    def inspect(self, name: str) -> dict:
        proc = self._run(["inspect", name])
        if proc.returncode != 0:
            raise DockerError(f"docker inspect failed for {name}", argv=["inspect"])
        import json

        data = json.loads(proc.stdout)
        return data[0] if data else {}

    def logs(self, name: str) -> str:
        proc = self._run(["logs", name], timeout=self.timeout_seconds)
        if proc.returncode not in (0, 1):  # 1: container had no log driver output yet
            raise DockerError(
                f"docker logs failed for {name}: {proc.stderr.strip()}",
                argv=["logs"],
            )
        return proc.stdout + proc.stderr

    def info(self) -> tuple[bool, str]:
        proc = self._run(["info", "--format", "{{.ServerVersion}}"])
        return (proc.returncode == 0), proc.stdout.strip()

    def image_digest(self, image: str) -> str | None:
        proc = self._run(
            ["images", "--digests", "--format", "{{.Repository}}:{{.Tag}} {{.Digest}}", image]
        )
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            repo_tag, _, digest = line.partition(" ")
            if repo_tag.startswith(image) and digest.startswith("sha256:"):
                return digest.strip()
        return None

    # -- worker lifecycle ------------------------------------------------------
    def run_worker(
        self,
        *,
        name: str,
        image: str,
        workspace: Path,
        env: dict[str, str],
        cpus: float,
        memory_mb: int,
        pids: int,
        timeout_seconds: int,
        network: str = "bridge",
    ) -> None:
        """Start an ephemeral, hardened worker container (detached)."""
        if network == "host":
            raise DockerError("host networking is forbidden for workers")
        resolved_workspace = validate_mount(self.jobs_root, workspace, label="workspace mount")
        argv = [
            "run", "--rm", "-d",
            "--name", name,
            "--label", f"{WORKER_LABEL}={WORKER_LABEL_VALUE}",
            "--label", f"{WORKER_LABEL}.workspace={resolved_workspace}",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--network", network,
            "--pids-limit", str(pids),
            "--cpus", str(cpus),
            "--memory", f"{memory_mb}m",
            "--memory-swap", f"{memory_mb}m",
            "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m",
            "--stop-timeout", str(min(timeout_seconds, 65535)),
            "--workdir", "/workspace",
            "-v", f"{resolved_workspace}:/workspace:rw",
        ]
        for key, value in sorted(env.items()):
            argv.extend(["-e", f"{key}={value}"])
        argv.append(image)
        proc = self._run(argv, timeout=self.timeout_seconds)
        if proc.returncode != 0:
            raise DockerError(
                f"docker run failed for {name}: {proc.stderr.strip()[:400]}",
                argv=argv,
                returncode=proc.returncode,
            )

    def stop(self, name: str, *, grace_seconds: int = 10) -> None:
        proc = self._run(["stop", "--time", str(grace_seconds), name])
        if proc.returncode != 0:
            raise DockerError(
                f"docker stop failed for {name}: {proc.stderr.strip()}",
                argv=["stop"],
            )

    def kill(self, name: str) -> None:
        proc = self._run(["kill", name])
        if proc.returncode != 0:
            raise DockerError(
                f"docker kill failed for {name}: {proc.stderr.strip()}",
                argv=["kill"],
            )

    def rm(self, name: str) -> None:
        proc = self._run(["rm", "-f", name])
        if proc.returncode != 0:
            raise DockerError(
                f"docker rm failed for {name}: {proc.stderr.strip()}",
                argv=["rm"],
                returncode=proc.returncode,
            )

    def container_exists(self, name: str) -> bool:
        proc = self._run(["ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^{name}$"])
        return proc.returncode == 0 and name in proc.stdout.split()

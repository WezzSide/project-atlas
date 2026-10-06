"""Fleet runtime source (infra/fleet-runtime): it compiles, it carries no private-environment
default, and a missing required value is an explicit configuration failure."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "infra" / "fleet-runtime"
SUPERVISOR = RUNTIME / "node" / "atlas_mission_supervisor.py"
CLIENT = RUNTIME / "node" / "atlas_queue_client.py"
POLICY = RUNTIME / "node" / "atlas_task_policy.py"
QUEUE = RUNTIME / "control" / "queue" / "atlas_queue_service.py"
GATEWAY = RUNTIME / "control" / "gateway" / "atlas_owner_gateway.py"
SOURCES = (SUPERVISOR, CLIENT, POLICY, QUEUE, GATEWAY)

_HOST_PATH = re.compile(r"(?<![A-Za-z0-9_.])/(etc|var|opt|srv|home|root|usr/local)/")
_ADDRESS = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_LOOPBACK_OR_WILDCARD = {"127.0.0.1", "0.0.0.0"}
_PORT_LITERAL = re.compile(r"PORT[^\n]*['\"]\d{2,5}['\"]")
_SECRET_SHAPED = re.compile(r"sk-ant-[A-Za-z0-9]|gh[pousr]_[A-Za-z0-9]{8}|BEGIN [A-Z ]*PRIVATE KEY")

_QUEUE_ENV = {
    "ATLAS_QUEUE_POLICY_FILE": "policy.json",
    "ATLAS_QUEUE_DB": "queue.db",
    "ATLAS_QUEUE_PORT": "18080",
    "ATLAS_QUEUE_TOKENS_FILE": "tokens.json",
}
_GATEWAY_ENV = {
    "ATLAS_GATEWAY_CONFIG": "gateway.json",
    "ATLAS_GATEWAY_STATE": "state.json",
    "ATLAS_GATEWAY_LOG": "events.jsonl",
}
_SUPERVISOR_ENV = {"ATLAS_AUTONOMY_ETC": "etc", "ATLAS_AUTONOMY_VAR": "var"}


def _load(path: Path, env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Execute the module body only (not ``main``) in a child with a controlled environment."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
    clean.update(
        {
            name: str(tmp_path / value) if "PORT" not in name else value
            for name, value in env.items()
        }
    )
    code = "import runpy, sys; runpy.run_path(sys.argv[1], run_name='loaded')"
    return subprocess.run(
        [sys.executable, "-c", code, str(path)],
        capture_output=True,
        text=True,
        env=clean,
        cwd=str(tmp_path),
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_source_compiles(path: Path) -> None:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


@pytest.mark.parametrize("path", [*SOURCES, RUNTIME / "README.md"], ids=lambda p: p.name)
def test_no_private_environment_default_is_published(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not _HOST_PATH.search(text), path.name
    assert not _PORT_LITERAL.search(text), path.name
    assert not _SECRET_SHAPED.search(text), path.name
    assert set(_ADDRESS.findall(text)) <= _LOOPBACK_OR_WILDCARD, path.name


@pytest.mark.parametrize(
    ("path", "env"),
    [(QUEUE, _QUEUE_ENV), (GATEWAY, _GATEWAY_ENV)],
    ids=["queue-service", "owner-gateway"],
)
def test_each_required_value_is_an_explicit_startup_failure(
    path: Path, env: dict[str, str], tmp_path: Path
) -> None:
    assert _load(path, env, tmp_path).returncode == 0
    for missing in env:
        partial = {name: value for name, value in env.items() if name != missing}
        result = _load(path, partial, tmp_path)
        assert result.returncode != 0, missing
        assert f"configuration error: {missing}" in result.stderr, missing


@pytest.mark.skipif(sys.platform == "win32", reason="the supervisor needs a POSIX host (fcntl)")
def test_supervisor_requires_its_directories(tmp_path: Path) -> None:
    assert _load(SUPERVISOR, _SUPERVISOR_ENV, tmp_path).returncode == 0
    for missing in _SUPERVISOR_ENV:
        partial = {k: v for k, v in _SUPERVISOR_ENV.items() if k != missing}
        result = _load(SUPERVISOR, partial, tmp_path)
        assert result.returncode != 0, missing
        assert f"configuration error: {missing}" in result.stderr, missing


def test_queue_client_is_unconfigured_without_operator_values(tmp_path: Path) -> None:
    code = (
        "import runpy, sys; m = runpy.run_path(sys.argv[1], run_name='loaded'); print(m['_cfg']())"
    )
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
    for extra in ({}, {"ATLAS_QUEUE_URL": "http://localhost:1"}):
        result = subprocess.run(
            [sys.executable, "-c", code, str(CLIENT)],
            capture_output=True,
            text=True,
            env={**clean, **extra},
            cwd=str(tmp_path),
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "(None, None)"

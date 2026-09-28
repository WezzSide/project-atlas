"""Integration-hardening tests: repo-owned host policy, deterministic
credential cleanup, deployment receipt semantics (Bands A/B/C)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[1]
POLICY = INFRA / "scripts" / "atlas-runner-host-policy.sh"

_IN_CHECKOUT = (
    Path(__file__).resolve().parents[3] / ".github" / "workflows"
).is_dir()
WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "atlas-runner-deploy.yml"

ACCEPT = {
    "deploy-legacy": "sudo /opt/atlas-runner/current/scripts/deploy-release.sh " + "a" * 40,
    "health-legacy": "sudo /opt/atlas-runner/current/bin/atlas-runner health",
    "health-helper": "sudo /opt/atlas-runner/current/scripts/atlas-runner-health.sh",
    "shim": "sudo /usr/local/sbin/atlas-deploy-exec " + "a" * 40 + " " + "b" * 64,
}
REFUSE = {
    "arbitrary": "id",
    "general-shell": "sudo bash -c id",
    "deploy-extra-arg": (
        "sudo /opt/atlas-runner/current/scripts/deploy-release.sh " + "a" * 40 + " extra"
    ),
    "deploy-nonhex": "sudo /opt/atlas-runner/current/scripts/deploy-release.sh nothex",
    "health-extra-arg": "sudo /opt/atlas-runner/current/bin/atlas-runner health extra",
    "health-alt-path": "sudo /opt/atlas-runner/current/scripts/../scripts/atlas-runner-health.sh",
    "shim-one-arg": "sudo /usr/local/sbin/atlas-deploy-exec " + "a" * 40,
    "shim-three-args": "sudo /usr/local/sbin/atlas-deploy-exec " + "a" * 40 + " " + "b" * 64 + " x",
    "shim-nonhex": "sudo /usr/local/sbin/atlas-deploy-exec " + "Z" * 40 + " " + "b" * 64,
    "shim-separator": "sudo /usr/local/sbin/atlas-deploy-exec aaaa;id " + "b" * 64,
    "shim-env-prefix": (
        "FOO=bar sudo /usr/local/sbin/atlas-deploy-exec " + "a" * 40 + " " + "b" * 64
    ),
    "shim-alt-path": "sudo /usr/local/sbin/../sbin/atlas-deploy-exec " + "a" * 40 + " " + "b" * 64,
    "unrelated-sudo": "sudo systemctl stop atlas-runner-controller",
}


def _run_policy(command: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SSH_ORIGINAL_COMMAND"] = command
    return subprocess.run(
        ["bash", str(POLICY)], env=env, capture_output=True, timeout=30,
    )


def test_policy_accepts_exact_deploy_invocation() -> None:
    proc = _run_policy(ACCEPT["deploy-legacy"])
    # refused at argument validation OR executed-but-script-missing both prove
    # the form was admitted past the dispatcher; here we assert NOT refused.
    assert b"refusing command" not in proc.stderr
    assert proc.returncode in (0, 1, 2)  # downstream failure is fine; refusal shape is not


def test_policy_accepts_exact_health_invocation() -> None:
    proc = _run_policy(ACCEPT["health-helper"])
    assert b"refusing command" not in proc.stderr


def test_policy_rejects_all_noncontract_forms() -> None:
    for name, command in REFUSE.items():
        proc = _run_policy(command)
        assert proc.returncode != 0, f"{name}: unexpectedly accepted"
        assert b"refusing" in proc.stderr or b"Permission" in proc.stderr, (
            f"{name}: no refusal message"
        )


def test_policy_no_wildcards_or_general_shell() -> None:
    text = POLICY.read_text(encoding="utf-8")
    for bad in ("sudo *", "/bin/bash *", "eval ", "exec ${SSH_ORIGINAL_COMMAND"):
        assert bad not in text, f"policy contains general-purpose construct: {bad}"


def test_workflow_deterministic_credential_cleanup() -> None:
    """Band B: both SSH steps must use real mktemp + EXIT trap (no mktemp -u)."""
    if not _IN_CHECKOUT:
        pytest.skip("workflow file unavailable outside a git checkout")
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "mktemp -u" not in text, "predictable mktemp -u remains in deploy workflow"
    assert text.count("trap 'rm -f") >= 2, "EXIT-trap cleanup missing from an SSH step"


def test_cleanup_trap_removes_material_on_failure_path() -> None:
    """Functional: the trap pattern removes key material even when the SSH
    command fails (simulated refusal)."""
    script = """
set -euo pipefail
umask 077
keyfile="$(mktemp /tmp/atlas-test-key.XXXXXX)"
trap 'rm -f "${keyfile}"' EXIT
printf 'secret-material\\n' > "${keyfile}"
ssh() { echo "refusing command" >&2; return 1; }
ssh host "some command" || true
echo "KEYFILE=${keyfile}"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert "refusing command" in proc.stderr  # the simulated SSH failure occurred
    keyfile = next(
        line.split("=", 1)[1] for line in proc.stdout.splitlines() if line.startswith("KEYFILE=")
    )
    assert not Path(keyfile).exists(), "trap did not remove key material on failure path"
    assert "secret-material" not in proc.stdout + proc.stderr


def test_workflow_emits_deployment_receipt() -> None:
    """Band C: receipt step exists and preserves both workflow and reconciliation."""
    if not _IN_CHECKOUT:
        pytest.skip("workflow file unavailable outside a git checkout")
    text = WORKFLOW.read_text(encoding="utf-8")
    for state in (
        "DEPLOY_NOT_ACTIVATED",
        "DEPLOY_ACTIVATED_HEALTH_PASS",
        "DEPLOY_ACTIVATED_POSTCHECK_TRANSPORT_FAILED",
        "DEPLOY_ACTIVATED_HEALTH_FAILED",
        "DEPLOY_ROLLED_BACK",
    ):
        assert state in text, f"receipt state missing: {state}"
    assert "id: health" in text, "health step lacks an id for outcome binding"
    assert "workflow_results" in text

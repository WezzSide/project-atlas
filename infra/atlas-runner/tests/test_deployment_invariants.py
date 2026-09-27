"""Deployment invariant tests (AS-RUNNER-FABRIC-001).

Guards the host-integration contract that only a real systemd launch can
exercise: the unit must pass global CLI flags before the subcommand, and the
generated bin wrapper must put the release directory on PYTHONPATH so
`python -m controller` resolves regardless of the caller's cwd.

DEPLOYED != VERIFIED; these checks catch PRE_DEPLOY breakage.
"""

from __future__ import annotations

from pathlib import Path

RELEASE_ROOT = Path(__file__).resolve().parents[1]


def test_unit_execstart_places_config_before_subcommand() -> None:
    unit = (RELEASE_ROOT / "systemd" / "atlas-runner-controller.service").read_text(
        encoding="utf-8"
    )
    for line in unit.splitlines():
        if line.startswith("ExecStart="):
            command = line.removeprefix("ExecStart=")
            config_at = command.index("--config")
            run_at = command.rindex(" run")
            assert config_at < run_at, (
                "unit must pass --config before the subcommand "
                "(argparse rejects global flags after the subcommand)"
            )
            return
    raise AssertionError("no ExecStart line in unit file")


def test_unit_startlimit_in_unit_section() -> None:
    unit = (RELEASE_ROOT / "systemd" / "atlas-runner-controller.service").read_text(
        encoding="utf-8"
    )
    section = ""
    saw_startlimit_in_unit = False
    for line in unit.splitlines():
        if line.startswith("["):
            section = line
        elif line.startswith("StartLimit"):
            assert section == "[Unit]", (
                f"{line.split('=')[0]} belongs in [Unit] on systemd >= 230; "
                f"found in {section}"
            )
            saw_startlimit_in_unit = True
    assert saw_startlimit_in_unit, "expected StartLimit* directives in [Unit]"


def test_deploy_wrapper_puts_release_on_pythonpath() -> None:
    script = (RELEASE_ROOT / "scripts" / "deploy-release.sh").read_text(encoding="utf-8")
    assert 'PYTHONPATH="${RELEASE}:${CONTRACTS_SRC}"' in script, (
        "wrapper must prepend the release dir so `python -m controller` resolves "
        "from any cwd (systemd WorkingDirectory alone is not enough for direct CLI use)"
    )


# --- INCIDENT-001 (ATLAS-RUNNER-TRUSTED-DEPLOY-CLOSURE-002) ------------------

import os  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _in_git_checkout() -> bool:
    """True when the suite runs from a repository checkout (CI); False when it
    runs from a staged release on the deploy host (deploy-release.sh runs the
    suite against /opt/atlas-runner/releases/<rev>/tests)."""
    return (
        subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
        ).returncode
        == 0
    )


_IN_CHECKOUT = _in_git_checkout()

# Files that are executed via sudo/absolute path by the trusted deploy path.
# Scripts deliberately invoked through `bash <script>` must NOT be listed here
# (their Git mode stays 100644 by design; this is what CI missed before
# INCIDENT-001 defect D).
DIRECTLY_EXECUTED_SCRIPTS = (
    "infra/atlas-runner/scripts/deploy-release.sh",
    "infra/atlas-runner/scripts/atlas-runner-health.sh",
    "infra/atlas-runner/scripts/atlas-deploy-exec.sh",
)


def _git_index_mode(path: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", "-s", "--", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out, f"{path} is not tracked in the Git index"
    return out.split()[0]


def _on_disk_exec(path: Path) -> bool:
    return bool(stat.S_IMODE(os.stat(path).st_mode) & 0o111)


def test_directly_executed_scripts_have_git_exec_bit() -> None:
    """Defect D regression: CI's bash -n / shellcheck do not require +x.

    Checkout context: assert the Git index mode (not os.access()) is 100755.
    Staged-release context (deploy host): assert the on-disk executable bit —
    staged files carry their Git mode through `git archive`, so this is the
    same invariant observed from the release.
    """
    for path in DIRECTLY_EXECUTED_SCRIPTS:
        if _IN_CHECKOUT:
            assert (_REPO_ROOT / path).is_file(), f"{path} missing from checkout"
            mode = _git_index_mode(path)
            assert mode == "100755", f"{path} must be executable in Git (got {mode})"
        else:
            script = RELEASE_ROOT / "scripts" / Path(path).name
            assert script.is_file(), f"{script} missing from staged release"
            assert _on_disk_exec(script), f"{script} must carry the executable bit"


def test_bash_invoked_scripts_are_not_exec_bit_required() -> None:
    """Guard against false-positive exec-bit requirements.

    bootstrap/smoke/acceptance workloads are invoked as `bash <script>`;
    requiring +x there would be noise, so assert they are NOT in the
    directly-executed list while still being present (Git-tracked in a
    checkout, on disk in a staged release).
    """
    for path in (
        "infra/atlas-runner/scripts/bootstrap-vps.sh",
        "infra/atlas-runner/scripts/smoke-workload.sh",
        "infra/atlas-runner/scripts/acceptance-workload.sh",
    ):
        assert path not in DIRECTLY_EXECUTED_SCRIPTS
        if _IN_CHECKOUT:
            _git_index_mode(path)  # tracked
        else:
            assert (RELEASE_ROOT / "scripts" / Path(path).name).is_file()


def test_no_workflow_grants_itself_merge_authority() -> None:
    """Bounded governance regression for GOVERNANCE_INCIDENT_PR1009.

    PASS/CI/IV must never silently become merge authority: no workflow may
    contain an automated merge primitive. Merge remains an explicit human act.
    Only meaningful in a checkout (staged releases carry no .github tree).
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    if not workflows_dir.is_dir():
        pytest.skip("not a git checkout: no .github/workflows to scan")
    forbidden = ("gh pr merge", "gh api -X POST", "pulls/", "/merge\"", "/merge'")
    for path in sorted(workflows_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, (
                f"{path.name}: found '{needle}' — workflows must not carry "
                "merge authority (governance incident PR1009 regression)"
            )


def test_health_entrypoint_parity_between_deploy_and_workflow() -> None:
    """Defect A regression: internal gate and workflow must use the SAME entrypoint.

    The deploy-side half is asserted in every context; the workflow half
    requires a checkout (staged releases carry no .github tree).
    """
    deploy = (RELEASE_ROOT / "scripts" / "deploy-release.sh").read_text(encoding="utf-8")
    helper = "scripts/atlas-runner-health.sh"
    assert helper in deploy, "deploy-release.sh internal gate must use the shared health entrypoint"
    if not _IN_CHECKOUT:
        pytest.skip("not a git checkout: workflow file unavailable for parity check")
    workflow = (_REPO_ROOT / ".github" / "workflows" / "atlas-runner-deploy.yml").read_text(
        encoding="utf-8"
    )
    assert helper in workflow, "workflow post-deploy step must use the shared health entrypoint"

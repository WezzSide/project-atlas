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

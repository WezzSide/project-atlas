"""EXECUTOR_PY312_BOOTSTRAP: Python >= 3.12 is a guarantee of the digest-pinned worker image.

Regression for live run 36754123746: ``actions/setup-python`` (python-version 3.12) cannot
provide an interpreter on the Debian 12 self-hosted executor, so the job died before any agent
execution. The runtime contract now lives in the immutable image and the workflow validates it
fail-closed instead of downloading a toolchain at job time.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "infra/atlas-runner/Dockerfile").read_text(encoding="utf-8")
WORKFLOW_TEXT = (ROOT / ".github/workflows/atlas-agent-execute.yml").read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
STEPS = WORKFLOW["jobs"]["execute"]["steps"]


def _index(pred) -> int:
    for i, step in enumerate(STEPS):
        if pred(step):
            return i
    raise AssertionError("step not found")


def test_base_image_is_a_digest_pinned_python_312_image():
    froms = [ln for ln in DOCKERFILE.splitlines() if ln.startswith("FROM ")]
    assert len(froms) == 1
    assert re.fullmatch(r"FROM python:3\.12[\w.-]*@sha256:[0-9a-f]{64}", froms[0]), froms[0]


def test_python_is_not_taken_from_apt_and_build_asserts_the_version():
    apt_blocks = re.findall(r"apt-get install[^\n]*(?:\\\n[^\n]*)*", DOCKERFILE)
    assert apt_blocks
    for block in apt_blocks:
        assert not re.search(r"\bpython3[\w-]*\b", block), block
    assert "sys.version_info >= (3, 12)" in DOCKERFILE


def test_workflow_no_longer_depends_on_setup_python():
    assert all("setup-python" not in str(step.get("uses", "")) for step in STEPS)


def test_python_contract_is_validated_fail_closed_before_any_agent_execution():
    validate = _index(lambda s: "Verify executor Python toolchain" in s.get("name", ""))
    venv = _index(lambda s: "isolated venv" in s.get("name", ""))
    agent = _index(lambda s: "claude-code-action" in str(s.get("uses", "")))
    branch = _index(lambda s: "dedicated agent branch" in s.get("name", ""))
    assert validate < venv < branch < agent
    body = STEPS[validate]["run"]
    assert "set -euo pipefail" in body
    assert "sys.version_info >= (3, 12)" in body
    assert "pip --version" in body and "venv --help" in body
    # no silent fallback to an older interpreter
    assert "3.11" not in body and "|| true" not in body
    install = STEPS[venv]["run"]
    assert 'pip install --quiet -e ".[dev]"' in install
    assert "GITHUB_PATH" in install  # later steps (agent, pytest) use the venv interpreter


def test_no_privilege_or_permission_widening():
    assert WORKFLOW["permissions"] == {"contents": "write"}
    assert WORKFLOW["jobs"]["execute"]["runs-on"] == [
        "self-hosted",
        "linux",
        "x64",
        "atlas",
        "executor",
    ]
    assert "id-token" not in WORKFLOW_TEXT.split("permissions:")[1].split("concurrency:")[0]


def test_deploy_receipt_correlates_release_revision_and_worker_image():
    deploy = (ROOT / ".github/workflows/atlas-runner-deploy.yml").read_text(encoding="utf-8")
    assert "worker-image" in deploy and '"worker_image"' in deploy
    script = (ROOT / "infra/atlas-runner/scripts/deploy-release.sh").read_text(encoding="utf-8")
    # image is built/validated/bound strictly before the symlink swap
    assert (
        script.index('"${DOCKER}" build')
        < script.index("worker-image.json")
        < script.index("# --- activate: symlink swap")
    )
    assert ":latest" not in script

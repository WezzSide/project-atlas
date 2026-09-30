"""Entrypoint evidence-fragment lifecycle guards.

AS-RUNNER-EVIDENCE-REVISION-001: runner shutdown must not clobber a richer
workflow-produced fragment; fallback fragment is allowed when none exists.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "entrypoint.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_entrypoint(*, tmp_path: Path, preexisting_fragment: dict | None = None) -> dict:
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir(parents=True, exist_ok=True)
    _write_executable(
        runner_dir / "run.sh",
        "#!/usr/bin/env bash\nset -euo pipefail\nexit \"${FAKE_RUN_EXIT_CODE:-0}\"\n",
    )
    workspace = tmp_path / "workspace"
    evidence_dir = workspace / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if preexisting_fragment is not None:
        (evidence_dir / "fragment.json").write_text(
            json.dumps(preexisting_fragment), encoding="utf-8"
        )
    jit_path = tmp_path / "jit.txt"
    jit_path.write_text("fake-jit", encoding="utf-8")
    env = {
        **os.environ,
        "ATLAS_RUNNER_DIR": str(runner_dir),
        "ATLAS_WORK_DIR": str(workspace),
        "ATLAS_EVIDENCE_DIR": str(evidence_dir),
        "ATLAS_TASK_ID": "ATLAS-RUNNER-E2E-001",
        "RUNNER_JIT_CONFIG_FILE": str(jit_path),
        "FAKE_RUN_EXIT_CODE": "0",
    }
    result = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads((evidence_dir / "fragment.json").read_text(encoding="utf-8"))


def test_entrypoint_creates_fallback_fragment_when_missing(tmp_path: Path) -> None:
    fragment = _run_entrypoint(tmp_path=tmp_path)
    assert fragment == {
        "task_id": "ATLAS-RUNNER-E2E-001",
        "runner_exit_code": 0,
        "artifacts": [],
    }


def test_entrypoint_preserves_rich_fragment_fields_on_runner_exit(tmp_path: Path) -> None:
    rich = {
        "task_id": "ATLAS-RUNNER-E2E-001",
        "base_revision": "a" * 40,
        "result_revision": "b" * 40,
        "tests": {"acceptance_fixture_monotonic": True},
        "artifacts": [
            "acceptance.patch",
            "infra/atlas-runner/tests/fixtures/acceptance/ATLAS-RUNNER-E2E-001.json",
        ],
        "artifact_sha256": {
            "acceptance.patch": "c" * 64,
            "infra/atlas-runner/tests/fixtures/acceptance/ATLAS-RUNNER-E2E-001.json": "d" * 64,
        },
    }
    fragment = _run_entrypoint(tmp_path=tmp_path, preexisting_fragment=rich)
    assert fragment["task_id"] == rich["task_id"]
    assert fragment["base_revision"] == rich["base_revision"]
    assert fragment["result_revision"] == rich["result_revision"]
    assert fragment["tests"] == rich["tests"]
    assert fragment["artifacts"] == rich["artifacts"]
    assert fragment["artifact_sha256"] == rich["artifact_sha256"]
    assert fragment["runner_exit_code"] == 0

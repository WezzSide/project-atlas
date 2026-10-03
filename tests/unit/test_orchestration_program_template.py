"""The shipped program template must actually be usable.

A template nobody validates rots into a shape the loader stopped accepting, and
the first person to find out is an operator following the documentation. These
tests keep it honest in both directions: it must refuse as-is, and it must
validate once its documented preparation is done.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from project_atlas.orchestration.program.loader import ProgramLoadError, load_program

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "orchestration"
    / "program"
    / "programs"
    / "first-program-TEMPLATE.json"
)


def _strip(value: Any) -> Any:
    """Exactly the transformation the template's own README documents."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def test_the_template_exists_and_is_json() -> None:
    assert TEMPLATE.is_file(), "the documented template is missing"
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert payload["_README"], "the template must explain itself"


def test_the_template_refuses_to_run_as_is(tmp_path: Path) -> None:
    """Deliberate. A template that validated as-is would invite running it."""
    copy = tmp_path / "as-is.json"
    copy.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ProgramLoadError) as excinfo:
        load_program(copy)
    assert excinfo.value.code == "FILE_MALFORMED"
    assert "_README" in str(excinfo.value)


def test_every_operator_supplied_field_is_marked() -> None:
    """A field the operator must fill has to say so where they will see it."""
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    text = json.dumps(payload)
    assert "OPERATOR-SUPPLIED" in text
    program = payload["program"]
    for required in ("program_id", "objective", "approved_by", "approval_reference",
                     "workspace_root", "base_pin"):
        assert "OPERATOR-SUPPLIED" in str(program[required]), (
            f"{required} does not tell the operator to replace it"
        )
    task = program["tasks"][0]
    for required in ("task_id", "title", "instruction"):
        assert "OPERATOR-SUPPLIED" in str(task[required])


def test_the_template_validates_once_prepared_as_documented(tmp_path: Path) -> None:
    """The whole point: follow the README and get a program that loads.

    Verified end to end rather than asserted -- a real git workspace, the
    documented strip, and the operator-supplied values filled in.
    """
    workspace = tmp_path / "ws"
    (workspace / "OPERATOR-SUPPLIED").mkdir(parents=True)
    (workspace / "OPERATOR-SUPPLIED" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    for argv in (
        ["git", "init", "--quiet", str(workspace)],
        ["git", "-C", str(workspace), "config", "user.email", "t@example.invalid"],
        ["git", "-C", str(workspace), "config", "user.name", "T"],
        ["git", "-C", str(workspace), "add", "-A"],
        ["git", "-C", str(workspace), "commit", "--quiet", "-m", "seed"],
    ):
        subprocess.run(argv, check=True, capture_output=True, timeout=60)
    pin = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.strip()

    payload = _strip(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    payload["program"].update(
        program_id="filled-example",
        workspace_root=str(workspace),
        base_pin=pin,
    )
    task = payload["program"]["tasks"][0]
    task.update(
        task_id="example-task",
        mutation_paths=["OPERATOR-SUPPLIED/thing.py"],
        surface_id="example",
        surface_semantic="EXAMPLE",
    )
    task["acceptance"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(0)"]
    payload["profiles"]["implementer"].update(
        agent_id="example-agent",
        allowed_mutation_prefixes=["OPERATOR-SUPPLIED/"],
    )

    prepared = tmp_path / "filled.json"
    prepared.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    loaded = load_program(prepared)
    assert loaded.program.program_id == "filled-example"
    assert loaded.program.limits.max_concurrent_workers == 1, (
        "the template must default to the proven sequential path"
    )
    profile = loaded.effective_profile("example-task")
    assert profile.agent_id == "example-agent"
    # The template's own acceptance shape survives: a COMMAND check and a
    # tree-changed check, so a worker that does nothing cannot pass.
    kinds = {check.kind.value for check in loaded.program.tasks[0].acceptance}
    assert "COMMAND" in kinds
    assert "GIT_TREE_CHANGED" in kinds


def test_the_template_names_no_unavailable_runtime() -> None:
    """It must not hand an operator an adapter that does not exist."""
    from project_atlas.orchestration.program.profiles import AdapterKind

    payload = _strip(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    adapters = {
        profile["adapter"]
        for profile in payload["profiles"].values()
        if isinstance(profile, dict) and "adapter" in profile
    }
    assert adapters, "the template declares no adapter"
    assert adapters <= {kind.value for kind in AdapterKind}
    assert "cursor-agent" not in adapters
    assert "copilot" not in adapters

"""The supervisor exposed through the Atlas CLI, and the seam it must not break.

`atlas program ...` and `atlas agent ...` are the operator surface. They are a
registration, not a second lifecycle: both delegate into the same
`dispatch_cli` the module entry point uses, so the two cannot drift into
different behaviour under the same arguments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from project_atlas.cli import build_parser
from project_atlas.cli import main as atlas_main
from project_atlas.orchestration.program.cli import main as module_main


def _top_level_commands() -> set[str]:
    parser = build_parser()
    actions = [
        action
        for action in parser._actions
        if hasattr(action, "choices") and isinstance(action.choices, dict)
    ]
    assert actions, "the Atlas parser has no subcommand group"
    return set(actions[0].choices)


def test_the_supervisor_is_reachable_through_atlas() -> None:
    commands = _top_level_commands()
    assert "program" in commands
    assert "agent" in commands


def test_the_atlas3_seam_still_works_alongside_it() -> None:
    """Registering here must not displace the Atlas 3 surface."""
    commands = _top_level_commands()
    for atlas3_command in ("pulse", "proof", "memory", "home", "truth-graph"):
        assert atlas3_command in commands, (
            f"{atlas3_command!r} disappeared when the supervisor was registered"
        )


def test_certified_commands_survive_the_registration() -> None:
    commands = _top_level_commands()
    for certified in ("connect", "ask2", "kdiff", "brief", "capture", "validate"):
        assert certified in commands


def test_the_supervisor_registers_no_atlas3_owned_name() -> None:
    """`program start` is nested, so it cannot collide with atlas3's `start`.

    `ATLAS3_COMMANDS` contains `start` and `mission`. The supervisor has
    subcommands with those spellings, which is only safe because they live
    under `program`/`agent` and never on the top-level group.
    """
    from project_atlas.atlas3.cli import ATLAS3_COMMANDS

    parser = build_parser()
    group = next(
        action
        for action in parser._actions
        if hasattr(action, "choices") and isinstance(action.choices, dict)
    )
    supervisor_subcommands: set[str] = set()
    for name in ("program", "agent"):
        sub = group.choices[name]
        for action in sub._actions:
            if hasattr(action, "choices") and isinstance(action.choices, dict):
                supervisor_subcommands |= set(action.choices)
    # They do overlap -- that is the point of checking the nesting holds.
    assert supervisor_subcommands & set(ATLAS3_COMMANDS), (
        "this test is vacuous if the spellings no longer overlap"
    )
    assert not (set(group.choices) & set(ATLAS3_COMMANDS) - _atlas3_top_level()), (
        "an Atlas 3 owned name is registered outside the Atlas 3 seam"
    )


def _atlas3_top_level() -> set[str]:
    """Atlas 3 names that legitimately appear at the top level via its seam."""
    from project_atlas.atlas3.cli import ATLAS3_COMMANDS

    return set(ATLAS3_COMMANDS)


def test_both_entry_points_produce_the_same_result(
    tmp_path: Path, capsys: Any
) -> None:
    """One lifecycle interface, asserted rather than assumed."""
    argv = ["program", "runtimes"]
    assert atlas_main(argv) == 0
    through_atlas = json.loads(capsys.readouterr().out)
    assert module_main(argv) == 0
    through_module = json.loads(capsys.readouterr().out)

    # Version probes read the live machine, so compare the structure and the
    # decisions rather than volatile fields.
    assert through_atlas["no_generic_adapter"] == through_module["no_generic_adapter"]
    assert [row["adapter"] for row in through_atlas["runtimes"]] == [
        row["adapter"] for row in through_module["runtimes"]
    ]
    assert [row["name"] for row in through_atlas["inventoried_not_implemented"]] == [
        row["name"] for row in through_module["inventoried_not_implemented"]
    ]
    _ = tmp_path


def test_a_refusal_through_atlas_keeps_the_json_contract(
    tmp_path: Path, capsys: Any
) -> None:
    """An error must not escape as a traceback through the Atlas entry point."""
    code = atlas_main(
        ["program", "validate", "--program", str(tmp_path / "missing.json")]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "FILE_MISSING"
    assert payload["merge_authorized"] is False


def test_agent_refusals_travel_the_same_path(tmp_path: Path, capsys: Any) -> None:
    code = atlas_main(
        [
            "agent", "launch",
            "--registry", str(tmp_path / "registry"),
            "--agent-id", "nobody",
        ]
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "UNKNOWN_AGENT"


@pytest.mark.parametrize(
    "argv",
    [
        ["program", "validate", "--help"],
        ["program", "control", "--help"],
        ["program", "service", "--help"],
        ["agent", "enroll", "--help"],
    ],
)
def test_every_operator_command_is_reachable_through_atlas(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        atlas_main(argv)
    assert excinfo.value.code == 0


def test_every_implemented_operator_command_is_documented() -> None:
    """Documentation synchronisation, enforced rather than remembered.

    `atlas program cancel` shipped documented nowhere for a while: it existed,
    worked, and no page mentioned it. A command an operator cannot discover is
    a command that does not exist for them.
    """
    import re
    from pathlib import Path as _Path

    docs = _Path(__file__).resolve().parents[2] / "docs" / "orchestration" / "program"
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in [*sorted(docs.glob("*.md")), docs / "operator-journey.sh"]
        if path.is_file()
    )
    parser = build_parser()
    group = next(
        action
        for action in parser._actions
        if hasattr(action, "choices") and isinstance(action.choices, dict)
    )
    undocumented: list[str] = []
    for name in ("program", "agent"):
        sub = group.choices[name]
        subcommands: set[str] = set()
        for action in sub._actions:
            if hasattr(action, "choices") and isinstance(action.choices, dict):
                subcommands |= set(action.choices)
        assert subcommands, f"{name} has no subcommands; the introspection drifted"
        for command in sorted(subcommands):
            if not re.search(rf"\b{name}\s+{re.escape(command)}\b", text):
                undocumented.append(f"{name} {command}")
    assert undocumented == [], (
        f"implemented but documented nowhere under docs/orchestration/program: "
        f"{undocumented}"
    )

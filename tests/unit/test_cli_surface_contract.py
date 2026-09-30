"""The CLI surface, observed on the parser argparse actually builds.

Six rounds of independent verification defeated six static answers to one
question: *which parser does the operator actually get?* Walk order, name
ownership, the call `main` makes, the module attribute, the entry point, and
finally the module-object write forms -- each fix closed a spelling and left
the class, because that question is a **runtime** property of a language where
any name can be rebound through an attribute, a subscript, `__dict__`,
`vars()`, `object.__setattr__`, or a computed `setattr`.

So these tests do not read `cli.py`. They import it, build the parser, and ask
the parser what it exposes. Every attack found across those six rounds --
seam shadowing in any spelling, parser rebinding, decoy factories, decorated
factories, module-attribute writes, control-flow evasion, a factory returning
an object it did not build, `dest` changes, deleted arguments -- changes what
this observes, by construction rather than by enumeration.

The static checks in `test_atlas3_demo_isolation_001.py` remain as
defence-in-depth against *deletions in a diff*. These are the ones that
establish what the CLI exposes.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import pytest

from project_atlas.atlas3.cli import ATLAS3_COMMANDS
from project_atlas.cli import build_parser, main

#: Certified top-level commands (CLAUDE.md's command list).
CERTIFIED_COMMANDS = ("connect", "ask2", "kdiff", "brief", "capture")

#: Documented flags per certified command, with the attribute each must land
#: on. `dest` is pinned deliberately: renaming it leaves the flag accepted and
#: silently drops its effect, which no "the option exists" check would catch.
CERTIFIED_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    # `brief --project` is repeatable, so its dest is plural. Pinned from the
    # built parser rather than assumed -- this test caught the assumption.
    "brief": (("--vault", "vault"), ("--project", "projects")),
    "ask2": (("--vault", "vault"), ("--project", "project"), ("--question", "question")),
    # CLAUDE.md documents `[--as-of T | --from T1 --to T2]` as kdiff's
    # time-travel interface, so those are part of the certified surface too.
    "kdiff": (
        ("--vault", "vault"),
        ("--project", "project"),
        ("--as-of", "as_of"),
        ("--from", "from_ref"),
        ("--to", "to_ref"),
    ),
    "connect": (("--vault", "vault"),),
}

#: Certified subcommands, whose whole operator interface lives one level down.
CERTIFIED_SUBCOMMAND_OPTIONS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("capture", "record"): (
        ("--vault", "vault"),
        ("--project", "project"),
        ("--summary", "summary"),
    ),
    ("capture", "list"): (("--vault", "vault"),),
}


def _subparser_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    actions = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(actions) == 1, f"expected exactly one subparser group, found {len(actions)}"
    return actions[0]


def _commands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return dict(_subparser_action(parser).choices)


def _options(parser: argparse.ArgumentParser) -> dict[str, str]:
    """option string -> dest, for every action on this parser."""
    return {option: action.dest for action in parser._actions for option in action.option_strings}


@pytest.fixture(scope="module")
def built() -> argparse.ArgumentParser:
    """The real parser, built the way the console script builds it."""
    return build_parser()


@pytest.mark.parametrize("command", CERTIFIED_COMMANDS)
def test_certified_command_is_exposed(command: str, built: argparse.ArgumentParser) -> None:
    """Not "cli.py contains a registration" -- the parser answers to it."""
    assert command in _commands(built), (
        f"certified command {command!r} is not exposed by the built parser"
    )


def test_atlas3_seam_actually_ran(built: argparse.ArgumentParser) -> None:
    """The seam's effect, not its syntax.

    Shadowing `register_atlas3_parsers`, rebinding it through the module
    object by any spelling, or skipping the call with an early `return` all
    leave the source looking correct and produce a parser with no Atlas 3
    commands. This sees the absence.
    """
    exposed = set(_commands(built))
    missing = sorted(set(ATLAS3_COMMANDS) - exposed)
    assert missing == [], f"Atlas 3 seam did not register: {missing}"


def test_no_certified_command_is_demoted(built: argparse.ArgumentParser) -> None:
    """Rebinding the top-level group re-routes registrations under another parser.

    Observed as the certified commands no longer being top-level, whatever
    spelling moved them.
    """
    top_level = set(_commands(built))
    for command in CERTIFIED_COMMANDS:
        assert command in top_level, f"{command!r} is no longer a top-level command"


@pytest.mark.parametrize("command", sorted(CERTIFIED_OPTIONS))
def test_certified_command_keeps_its_documented_options(
    command: str, built: argparse.ArgumentParser
) -> None:
    """Flag *and* `dest`: a renamed dest accepts the flag and drops its effect."""
    options = _options(_commands(built)[command])
    for flag, dest in CERTIFIED_OPTIONS[command]:
        assert flag in options, f"{command} no longer accepts {flag}"
        assert options[flag] == dest, (
            f"{command} {flag} now lands on {options[flag]!r}, not {dest!r}; "
            "the flag would be accepted and silently ignored"
        )


@pytest.mark.parametrize("pair", sorted(CERTIFIED_SUBCOMMAND_OPTIONS))
def test_certified_subcommand_keeps_its_documented_options(
    pair: tuple[str, str], built: argparse.ArgumentParser
) -> None:
    command, subcommand = pair
    parent = _commands(built)[command]
    children = _commands(parent)
    assert subcommand in children, f"{command} no longer exposes {subcommand!r}"
    options = _options(children[subcommand])
    for flag, dest in CERTIFIED_SUBCOMMAND_OPTIONS[pair]:
        assert flag in options, f"{command} {subcommand} no longer accepts {flag}"
        assert options[flag] == dest


def test_certified_commands_parse_a_documented_invocation(
    built: argparse.ArgumentParser,
) -> None:
    """End to end through argparse: the documented invocation still parses.

    Compared as a `Path`, not as a string: `--vault` is `type=Path`, so the
    parsed value is a `WindowsPath` on Windows and its string form uses
    backslashes. Asserting the POSIX spelling passed on Linux and failed the
    Windows CI job.
    """
    parsed = built.parse_args(["brief", "--vault", "/tmp/v", "--project", "p"])
    assert parsed.command == "brief"
    assert parsed.vault == Path("/tmp/v")
    assert parsed.projects == ["p"]


# ---------------------------------------------------------------------------
# Through `main`, not through `build_parser()`.
#
# Asserting on `build_parser()`'s return establishes what that function
# exposes -- which is one layer short of what the operator gets. Verification
# demonstrated three ways to break the CLI while leaving it green: `main`
# building from a different factory, `main` mutating the parser after it is
# returned, and registrations gated on the test process being detectable.
#
# These drive the real entry point instead. `--help` is used deliberately: it
# goes through the parser the operator's invocation goes through, and exits
# before any command does work, so the surface can be asserted without a vault
# or any side effect.
# ---------------------------------------------------------------------------


def _main_help(argv: list[str]) -> tuple[int, str]:
    """Run `main(argv)` expecting argparse's help exit; return (code, stdout)."""
    from contextlib import redirect_stdout
    from io import StringIO

    captured = StringIO()
    with redirect_stdout(captured), pytest.raises(SystemExit) as raised:
        main(argv)
    code = raised.value.code
    return (0 if code is None else int(code)), captured.getvalue()


@pytest.mark.parametrize("command", CERTIFIED_COMMANDS)
def test_main_exposes_each_certified_command(command: str) -> None:
    """The operator's entry point answers to it, not merely `build_parser()`.

    The exit code alone is not enough: with the subparser group removed,
    `atlas brief --help` still exits 0, because the top-level parser consumes
    `--help` and prints its own usage. The usage line must name the
    subcommand, which is what distinguishes "this command exists" from
    "something answered".
    """
    code, help_text = _main_help([command, "--help"])
    assert code == 0, f"`atlas {command} --help` exited {code}; the command is unreachable"
    assert f"atlas {command}" in help_text, (
        f"`atlas {command} --help` printed top-level usage, not {command}'s; "
        "the subcommand is not reachable through main"
    )


@pytest.mark.parametrize("command", sorted(CERTIFIED_OPTIONS))
def test_main_offers_each_documented_option(command: str) -> None:
    """The documented flags reach the operator, via `main`'s own parser."""
    code, help_text = _main_help([command, "--help"])
    assert code == 0
    for flag, _dest in CERTIFIED_OPTIONS[command]:
        assert flag in help_text, f"`atlas {command} --help` does not offer {flag}"


def test_main_exposes_the_atlas3_seam() -> None:
    """The seam's commands are reachable through the entry point.

    Neutralising the seam by any means -- shadowing, a module-object write in
    any spelling, an early return -- leaves this asking for a command the
    parser does not have.
    """
    sample = sorted(ATLAS3_COMMANDS)[:3]
    for command in sample:
        code, _ = _main_help([command, "--help"])
        assert code == 0, f"Atlas 3 command {command!r} is unreachable through main"


def test_certified_optional_flags_are_not_silently_required(
    built: argparse.ArgumentParser,
) -> None:
    """`brief --project p` must parse without `--vault`.

    Making a documented-optional flag `required=True` breaks an invocation the
    docs promise, while every "the option exists" check still passes.
    """
    parsed = built.parse_args(["brief", "--project", "p"])
    assert parsed.command == "brief"
    assert parsed.projects == ["p"]


def test_connect_keeps_its_documented_positional(built: argparse.ArgumentParser) -> None:
    """`atlas connect [source]` -- positionals carry no option strings, so a
    check that reads only `option_strings` cannot see one disappear."""
    parsed = built.parse_args(["connect", "/tmp/src"])
    assert parsed.command == "connect"
    assert Path(parsed.source) == Path("/tmp/src")


# ---------------------------------------------------------------------------
# The parse boundary.
#
# Everything above asserts `dest` on `build_parser()`'s return value. That is
# one object short of the truth: `main` receives that parser and is free to
# mutate it before handing it to argparse. Independent verification of #690
# proved the gap (P04) -- renaming `brief --vault`'s `dest` to `vault_renamed`
# immediately after `build_parser()` returns leaves the help text unchanged,
# every check above green, and `args.vault` absent from the namespace the
# command function is called with.
#
# The only place where "what the operator gets" is a settled fact is the
# instant argparse finishes parsing. These tests observe exactly that: they
# run the real `main(argv)`, intercept `argparse.ArgumentParser.parse_args`,
# and capture both the parser argparse was invoked on and the namespace it
# produced -- then stop, before dispatch and before any side effect.
#
# This subsumes the build-time checks rather than replacing them: a mutation
# anywhere between `build_parser`'s first line and argparse's last is visible
# here, because the observation happens after all of it.
# ---------------------------------------------------------------------------


class _ParseBoundary(BaseException):
    """Stop `main` the moment argparse has parsed, before dispatch.

    Derived from `BaseException`, not `Exception`, so a broad `except
    Exception` anywhere in the entry point cannot swallow the sentinel and
    turn a blocked mutation into a silent pass.
    """

    def __init__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace) -> None:
        super().__init__("parse-boundary")
        self.parser = parser
        self.namespace = namespace


def _sentinel(flag: str) -> str:
    """A per-flag marker value, unique so two flags cannot share a landing site.

    Alphanumeric only: `--vault` is `type=Path`, and a value containing a
    separator comes back as a `WindowsPath` whose string form uses
    backslashes. This round-trips identically on both platforms.
    """
    return "ORACLE" + flag.lstrip("-").replace("-", "").upper()


def _carries(value: object, sentinel: str) -> bool:
    """Did `sentinel` land here? Repeatable flags append, so lists count."""
    if isinstance(value, (list, tuple)):
        return any(_carries(item, sentinel) for item in value)
    return value == sentinel or str(value) == sentinel


def _parse_boundary(
    argv: list[str], entry: Callable[[list[str]], object]
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Run `entry(argv)` and capture argparse's parser and namespace.

    Every way of not reaching the boundary is an `AssertionError`, so a
    mutation that makes the CLI exit early, or never parse at all, blocks
    just as loudly as one that parses into the wrong place.
    """
    real_parse_args = argparse.ArgumentParser.parse_args

    def _spy(
        self: argparse.ArgumentParser,
        args: object = None,
        namespace: object = None,
    ) -> argparse.Namespace:
        parsed = real_parse_args(self, args, namespace)  # type: ignore[arg-type]
        raise _ParseBoundary(self, parsed)

    argparse.ArgumentParser.parse_args = _spy  # type: ignore[method-assign]
    try:
        entry(argv)
    except _ParseBoundary as boundary:
        return boundary.parser, boundary.namespace
    except SystemExit as exc:
        raise AssertionError(
            f"`atlas {' '.join(argv)}` exited {exc.code} instead of parsing; "
            "the documented invocation no longer reaches the parse boundary"
        ) from exc
    finally:
        argparse.ArgumentParser.parse_args = real_parse_args  # type: ignore[method-assign]
    raise AssertionError(
        f"`atlas {' '.join(argv)}` returned without ever calling parse_args; "
        "the entry point does not parse the operator's arguments"
    )


def _descend(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> argparse.ArgumentParser:
    """Walk the captured parser down to a (sub)command, or fail closed."""
    current = parser
    for name in path:
        choices = _commands(current)
        assert name in choices, (
            f"{name!r} is not exposed by the parser argparse actually used "
            f"(available: {sorted(choices)[:12]}...)"
        )
        current = choices[name]
    return current


def _assert_certified_dests(
    entry: Callable[[list[str]], object],
    path: tuple[str, ...],
    options: tuple[tuple[str, str], ...],
) -> None:
    """The certified surface, checked on the parser argparse was handed.

    Two independent observations, because they fail to different attacks:
    the parser's own `option -> dest` map, and whether the value actually
    landed on that attribute in the produced namespace. A mutation that
    edits the action object is caught by both; one that intercepts binding
    without touching the action is caught by the second.
    """
    argv = list(path)
    for flag, _dest in options:
        argv += [flag, _sentinel(flag)]
    parser, namespace = _parse_boundary(argv, entry=entry)

    assert getattr(namespace, "command", None) == path[0], (
        f"`atlas {' '.join(argv)}` routed to "
        f"{getattr(namespace, 'command', None)!r}, not {path[0]!r}"
    )
    if len(path) > 1:
        routed = {str(value) for value in vars(namespace).values()}
        assert path[1] in routed, f"the namespace records no route to subcommand {path[1]!r}"

    exposed = _options(_descend(parser, path))
    for flag, dest in options:
        label = "atlas " + " ".join(path)
        assert flag in exposed, f"`{label}` no longer accepts {flag}"
        assert exposed[flag] == dest, (
            f"`{label}` {flag} lands on {exposed[flag]!r}, not {dest!r}, on the "
            "parser argparse actually used; the flag is accepted and ignored"
        )
        assert hasattr(namespace, dest), (
            f"`{label}` parsed without producing {dest!r}; {flag} was accepted "
            "and its value is not reachable by the command function"
        )
        assert _carries(getattr(namespace, dest), _sentinel(flag)), (
            f"`{label}` {flag}={_sentinel(flag)!r} did not land on {dest!r} "
            f"(found {getattr(namespace, dest)!r})"
        )


@pytest.mark.parametrize("command", sorted(CERTIFIED_OPTIONS))
def test_certified_dests_hold_at_the_parse_boundary(command: str) -> None:
    """P04: the certified `dest`s, observed after `main` has finished with the
    parser rather than before it starts."""
    _assert_certified_dests(main, (command,), CERTIFIED_OPTIONS[command])


@pytest.mark.parametrize("pair", sorted(CERTIFIED_SUBCOMMAND_OPTIONS))
def test_certified_subcommand_dests_hold_at_the_parse_boundary(
    pair: tuple[str, str],
) -> None:
    """The same boundary, one level down, where `capture`'s whole operator
    interface lives."""
    _assert_certified_dests(main, pair, CERTIFIED_SUBCOMMAND_OPTIONS[pair])


def test_parse_boundary_parser_still_exposes_every_certified_command() -> None:
    """Demotion and seam neutralisation, observed on the parser argparse used.

    `build_parser()` returning a healthy parser says nothing if `main` parses
    with a different one.
    """
    parser, _ = _parse_boundary(["brief", "--project", "p"], entry=main)
    exposed = set(_commands(parser))
    missing = sorted(set(CERTIFIED_COMMANDS) - exposed)
    assert missing == [], f"not top-level on the parser main used: {missing}"
    absent = sorted(set(ATLAS3_COMMANDS) - exposed)
    assert absent == [], f"Atlas 3 seam absent from the parser main used: {absent}"


# ---------------------------------------------------------------------------
# Negative controls: P04A-P04E.
#
# A guard that cannot fail proves nothing, and source inspection is not proof
# that it would. Each entry point below is a `main` damaged in one of the ways
# post-build mutation can damage one, driven through the same assertions the
# real tests use. Each must raise `AssertionError` -- BLOCK.
# ---------------------------------------------------------------------------


def _action_for(
    parser: argparse.ArgumentParser, path: tuple[str, ...], flag: str
) -> argparse.Action:
    for action in _descend(parser, path)._actions:
        if flag in action.option_strings:
            return action
    raise AssertionError(f"no {flag} on {' '.join(path)}")


def _p04a(argv: list[str]) -> object:
    """Rename a certified top-level `dest` after `build_parser()` returns.

    Verbatim reproduction of the reported bypass.
    """
    parser = build_parser()
    _action_for(parser, ("brief",), "--vault").dest = "vault_renamed"
    return parser.parse_args(argv)


def _p04b(argv: list[str]) -> object:
    """The same, one level down, on a certified subcommand."""
    parser = build_parser()
    _action_for(parser, ("capture", "record"), "--vault").dest = "vault_renamed"
    return parser.parse_args(argv)


def _p04c(argv: list[str]) -> object:
    """The same damage through a computed attribute write.

    Spelling is what defeated six static rounds; the oracle observes the
    resulting object, so `setattr` with a computed name is not a new case.
    """
    parser = build_parser()
    action = _action_for(parser, ("kdiff",), "--project")
    setattr(action, "".join(["d", "e", "s", "t"]), "project_elsewhere")
    return parser.parse_args(argv)


def _p04d(argv: list[str]) -> object:
    """Delete a certified action after the parser is built."""
    parser = build_parser()
    target = _descend(parser, ("ask2",))
    action = _action_for(parser, ("ask2",), "--question")
    target._actions.remove(action)
    for option in action.option_strings:
        target._option_string_actions.pop(option, None)
    return parser.parse_args(argv)


def _p04e(argv: list[str]) -> object:
    """Build a healthy parser, then parse the operator's arguments with a decoy.

    Nothing observable about `build_parser()` is wrong here. Only the object
    argparse is handed is.
    """
    build_parser()
    decoy = argparse.ArgumentParser(prog="atlas")
    subparsers = decoy.add_subparsers(dest="command")
    for command in CERTIFIED_COMMANDS:
        child = subparsers.add_parser(command)
        for flag, _dest in CERTIFIED_OPTIONS.get(command, ()):
            child.add_argument(flag, dest="swallowed")
    return decoy.parse_args(argv)


#: Each damaged entry point, with the certified surface it must be caught on.
P04_MATRIX: tuple[tuple[str, Callable[[list[str]], object], tuple[str, ...]], ...] = (
    ("P04A-top-level-dest-rename", _p04a, ("brief",)),
    ("P04B-subcommand-dest-rename", _p04b, ("capture", "record")),
    ("P04C-computed-setattr-dest-rename", _p04c, ("kdiff",)),
    ("P04D-post-build-action-deletion", _p04d, ("ask2",)),
    ("P04E-decoy-parser-at-the-boundary", _p04e, ("brief",)),
)


@pytest.mark.parametrize("case", P04_MATRIX, ids=[case[0] for case in P04_MATRIX])
def test_p04_mutation_matrix_is_blocked(
    case: tuple[str, Callable[[list[str]], object], tuple[str, ...]],
) -> None:
    """Every post-build mutation of the certified surface must BLOCK.

    These run against the real `cli.py` parser, not a fixture, so the matrix
    stays honest as the CLI evolves.
    """
    _label, entry, path = case
    options = (
        CERTIFIED_OPTIONS[path[0]]
        if len(path) == 1
        else CERTIFIED_SUBCOMMAND_OPTIONS[(path[0], path[1])]
    )
    with pytest.raises(AssertionError):
        _assert_certified_dests(entry, path, options)


def test_p04_matrix_control_passes_undamaged() -> None:
    """The matrix's own control: the real `main` passes the identical
    assertions the damaged entry points fail. Without this, a checker that
    rejected everything would look like a working guard."""
    for _label, _entry, path in P04_MATRIX:
        options = (
            CERTIFIED_OPTIONS[path[0]]
            if len(path) == 1
            else CERTIFIED_SUBCOMMAND_OPTIONS[(path[0], path[1])]
        )
        _assert_certified_dests(main, path, options)

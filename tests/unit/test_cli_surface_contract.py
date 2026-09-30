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
import copy
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
#: Documented flags per certified command, as
#: ``(flag, dest, nargs, leaf_type, repeatable)``.
#:
#: `dest` alone is not the operator contract. Verification defeated a
#: `dest`-only pin three separate ways: renaming `dest` (P04), changing `nargs`
#: so a single value arrives as a one-element list that `_carries` happily
#: recurses into, and dropping `type=` so a `Path` flag delivers a `str`. Each
#: leaves the flag accepted, the suite green, and the handler receiving
#: something it cannot use -- usually as an unhandled `AttributeError`.
CERTIFIED_OPTIONS: dict[str, tuple[tuple[str, str, object, type, bool], ...]] = {
    # `brief --project` is repeatable, so its dest is plural and its value is a
    # list. Pinned from the built parser rather than assumed -- this test
    # caught the assumption.
    "brief": (
        ("--vault", "vault", None, Path, False),
        ("--project", "projects", None, str, True),
    ),
    "ask2": (
        ("--vault", "vault", None, Path, False),
        ("--project", "project", None, str, False),
        ("--question", "question", None, str, False),
    ),
    # CLAUDE.md documents `[--as-of T | --from T1 --to T2]` as kdiff's
    # time-travel interface, so those are part of the certified surface too.
    "kdiff": (
        ("--vault", "vault", None, Path, False),
        ("--project", "project", None, str, False),
        ("--as-of", "as_of", None, str, False),
        ("--from", "from_ref", None, str, False),
        ("--to", "to_ref", None, str, False),
    ),
    "connect": (("--vault", "vault", None, Path, False),),
}

#: Documented POSITIONAL arguments, identified by their index among a command's
#: positionals rather than by name -- a renamed `dest` is exactly what this
#: pins, so it cannot also be the thing used to find the argument.
#: `atlas connect [source]` is CLAUDE.md's spelling.
#: `nargs` is pinned beside `dest`, because `_carries` recurses into lists:
#: `nargs="*"` turns a single value into a one-element list and satisfies a
#: `dest`-only pin while the handler receives a `list` where it expects a path.
CERTIFIED_POSITIONALS: dict[str, tuple[tuple[int, str, object, type], ...]] = {
    "connect": ((0, "source", "?", Path),),
}

#: Certified subcommands, whose whole operator interface lives one level down.
CERTIFIED_SUBCOMMAND_OPTIONS: dict[
    tuple[str, str], tuple[tuple[str, str, object, type, bool], ...]
] = {
    ("capture", "record"): (
        ("--vault", "vault", None, Path, False),
        ("--project", "project", None, str, False),
        ("--summary", "summary", None, str, False),
    ),
    ("capture", "list"): (("--vault", "vault", None, Path, False),),
}


def _positional_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """The command's positionals, in the order argparse consumes them."""
    return [
        action
        for action in parser._actions
        if not action.option_strings
        and not isinstance(action, argparse._SubParsersAction)
    ]


def _subparser_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    actions = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(actions) == 1, f"expected exactly one subparser group, found {len(actions)}"
    return actions[0]


def _commands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return dict(_subparser_action(parser).choices)


def _routing_dest(parser: argparse.ArgumentParser, path: tuple[str, ...]) -> str:
    """The attribute a subcommand's route lands on, read off the real parser.

    `command` is not the whole route. `atlas capture record` selects its
    handler through `args.capture_command`, and `cli.main` branches on that
    attribute directly. Reading the `dest` from the parser argparse was handed
    -- rather than assuming the `f"{path[0]}_command"` spelling -- keeps a
    deliberate rename of the routing attribute a refactor rather than a
    failure, while damage to its *value* still blocks.
    """
    return _subparser_action(_descend(parser, path[:-1])).dest


def _options(parser: argparse.ArgumentParser) -> dict[str, str]:
    """option string -> dest, for every action on this parser."""
    return {
        option: action.dest
        for action in parser._actions
        for option in action.option_strings
    }


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
    for flag, dest, *_shape in CERTIFIED_OPTIONS[command]:
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
    for flag, dest, *_shape in CERTIFIED_SUBCOMMAND_OPTIONS[pair]:
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
    for flag, *_rest in CERTIFIED_OPTIONS[command]:
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
# So these tests observe the parser at the instant argparse finishes parsing:
# they run the real `main(argv)`, intercept `argparse.ArgumentParser.parse_args`,
# and capture both the parser argparse was invoked on and the namespace it
# produced -- then stop, before dispatch and before any side effect.
#
# That instant is *not* the last word, and an earlier revision of this comment
# wrongly said it was. Production `main` calls `_apply_stranger_defaults(args)`
# after parsing, which rewrites `args.vault`, `args.project` and
# `args.projects`, so the namespace keeps changing after argparse is done. Two
# attacks live in that gap and are caught at the dispatch boundary further
# down, not here.
#
# This supplements the build-time checks: certified action mappings and marker
# values are observed at the first intercepted parse. Mutations that preserve
# those observations, or occur after that parse, need other checks.
# ---------------------------------------------------------------------------


class _ParseBoundary(BaseException):
    """Stop `main` the moment argparse has parsed, before dispatch.

    Derived from `BaseException`, not `Exception`, so a broad `except
    Exception` anywhere in the entry point cannot swallow the sentinel and
    turn a blocked mutation into a silent pass.
    """

    def __init__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace
    ) -> None:
        super().__init__("parse-boundary")
        self.parser = parser
        self.namespace = namespace


def _sentinel(name: str) -> str:
    """A per-flag marker value, unique so two flags cannot share a landing site.

    Deliberately mixed-case and separator-bearing. An earlier version was
    uppercase alphanumeric, to survive `type=Path` round-tripping on Windows
    where a separator comes back as a backslash. Independent verification
    showed that made the marker blind to a whole class: any `type=` that is a
    no-op on `[A-Z0-9]+` -- `lambda v: Path(str(v).replace("/", ""))`, or
    `str.upper` -- mangled the operator's real value while leaving the marker
    intact. The platform problem is solved by comparing as a `Path` instead
    (see `_carries`), which costs nothing and keeps the marker sensitive.
    """
    return "Oracle/" + name.lstrip("-").replace("-", "_") + "-7x"


def _sentinel_alt(name: str) -> str:
    """A second marker of a deliberately different shape.

    One marker can only ever catch the transforms it is not a fixed point of.
    Verification found three that the first marker survives unchanged:
    `lstrip("/")`, truncation to its own length, and stripping spaces. This one
    is absolute, long, and carries a space, a dot and an underscore, so each of
    those changes it -- and so do `replace(".", "")` and `replace("_", "")`,
    which a later round found were identities on both earlier markers.
    Two markers of different shape is not a proof of completeness -- truncation
    to the longest marker's length is invariant against any fixed set of them.
    It is a much smaller target than one.
    """
    return "/Oracle " + name.lstrip("-").replace("-", "_") + ".d/deep_nested-7x"


MARKERS = (_sentinel, _sentinel_alt)


def _leaves(value: object) -> list[object]:
    """Flatten a value to its leaves, at any depth.

    An earlier docstring claimed this flattened one level, "which is all
    argparse ever produces". Both halves were wrong. It recurses to arbitrary
    depth, and argparse produces nested lists routinely -- `action="append"`
    with `nargs="+"` yields `[["a", "b"], ["c"]]`. That inaccuracy was the
    stated justification for not pinning container depth, which
    `_assert_value_shape` now pins separately.
    """
    if isinstance(value, (list, tuple)):
        return [leaf for item in value for leaf in _leaves(item)]
    return [value]


def _assert_value_shape(
    label: str, name: str, value: object, leaf_type: type, repeatable: bool
) -> None:
    """The half of the shape contract that needs no parser, so it holds at the
    dispatch boundary too, where only the namespace is in hand."""
    assert isinstance(value, list) is repeatable, (
        f"`{label}` {name} is {'no longer' if repeatable else 'now'} "
        f"repeatable; the handler receives {type(value).__name__}"
    )
    # Depth, not just the outer container. `_leaves` flattens all the way
    # down, so a custom action appending `[values]` rather than `values`
    # satisfies every other element of this contract while the handler
    # receives a list where it expects a string. Until now that shape blocked
    # only because one legacy test hard-codes its expected value, which is a
    # backstop rather than a pin -- the same insufficiency this file already
    # rejects elsewhere. The next repeatable flag certified would inherit no
    # such luck.
    for item in value if repeatable else [value]:
        assert not isinstance(item, (list, tuple)), (
            f"`{label}` {name} delivered a nested {type(item).__name__} where "
            "a leaf is documented; the handler receives a container it cannot use"
        )
    for leaf in _leaves(value):
        assert isinstance(leaf, leaf_type), (
            f"`{label}` {name} delivered {type(leaf).__name__} where "
            f"{leaf_type.__name__} is documented; the flag is accepted and the "
            "handler receives a value it cannot use"
        )


def _assert_shape(
    label: str,
    name: str,
    action: argparse.Action,
    value: object,
    nargs: object,
    leaf_type: type,
    repeatable: bool,
) -> None:
    """`dest` says where the value lands; this says what lands there.

    Three shape mutations each leave `dest` correct and the flag accepted:
    `nargs` turning one value into a list, `type=` removed so a `Path` slot
    receives a `str`, and a repeatable flag becoming single-valued.

    Two of the three reach the handler as an unhandled `AttributeError` on a
    documented invocation -- `'list' object has no attribute 'strip'` and
    `'str' object has no attribute 'expanduser'`. The third does not crash at
    all: with `--project` no longer repeatable the handler iterates the string
    character by character and exits 0 with a silently wrong answer. That is
    the worse outcome of the three, and the reason this is pinned rather than
    left to a crash to reveal.
    """
    assert action.nargs == nargs, (
        f"`{label}` {name} now takes nargs={action.nargs!r}, not {nargs!r}; "
        "the handler receives a different shape"
    )
    _assert_value_shape(label, name, value, leaf_type, repeatable)


def _carries(value: object, sentinel: str) -> bool:
    """Did `sentinel` land here, unmodified?

    Repeatable flags append, so lists count. `--vault` is `type=Path`, so the
    parsed value is a `WindowsPath` on Windows whose string form uses
    backslashes; comparing as a `Path` makes a separator-bearing marker
    portable, where comparing strings would not be.
    """
    if isinstance(value, (list, tuple)):
        return any(_carries(item, sentinel) for item in value)
    if value == sentinel or str(value) == sentinel:
        return True
    try:
        # `as_posix()`, not `==`: `PurePath` comparison is case-insensitive on
        # Windows, and the marker is case-sensitive by design. This form is
        # exact in both case and separators on every platform.
        return Path(str(value)).as_posix() == Path(sentinel).as_posix()
    except (TypeError, ValueError):
        return False


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


def _descend(
    parser: argparse.ArgumentParser, path: tuple[str, ...]
) -> argparse.ArgumentParser:
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


def _argv_for(
    path: tuple[str, ...],
    options: tuple[tuple[str, str], ...],
    positionals: tuple[tuple[int, str, object, type], ...] = (),
    marker: Callable[[str], str] = _sentinel,
) -> list[str]:
    """A documented invocation carrying a distinct marker on every certified slot."""
    argv = list(path)
    for flag, *_rest in options:
        argv += [flag, marker(flag)]
    argv += [marker(name) for _index, name, _nargs, _leaf in positionals]
    return argv


def _assert_positionals(
    parser: argparse.ArgumentParser,
    namespace: argparse.Namespace,
    path: tuple[str, ...],
    positionals: tuple[tuple[int, str, object, type], ...],
    marker: Callable[[str], str] = _sentinel,
) -> None:
    """Positionals carry no option strings, so an `option -> dest` map is blind
    to them. They are pinned by index instead."""
    label = "atlas " + " ".join(path)
    found = _positional_actions(_descend(parser, path))
    for index, dest, nargs, leaf_type in positionals:
        assert len(found) > index, (
            f"`{label}` no longer takes a positional at index {index}; "
            f"documented as {dest!r}"
        )
        action = found[index]
        assert action.dest == dest, (
            f"`{label}` positional {index} lands on {action.dest!r}, not "
            f"{dest!r}; the argument is accepted and silently ignored"
        )
        assert hasattr(namespace, dest), f"`{label}` parsed without producing {dest!r}"
        _assert_shape(
            label, f"positional {dest!r}", action, getattr(namespace, dest),
            nargs, leaf_type, False,
        )
        assert _carries(getattr(namespace, dest), marker(dest)), (
            f"`{label}` positional {dest!r} did not receive its value "
            f"(found {getattr(namespace, dest)!r})"
        )


def _assert_certified_dests(
    entry: Callable[[list[str]], object],
    path: tuple[str, ...],
    options: tuple[tuple[str, str], ...],
    positionals: tuple[tuple[int, str, object, type], ...] = (),
    marker: Callable[[str], str] = _sentinel,
) -> None:
    """The certified surface, checked on the parser argparse was handed.

    Two independent observations, because they fail to different attacks:
    the parser's own `option -> dest` map, and whether the value actually
    landed on that attribute in the produced namespace. A mutation that
    edits the action object is caught by both; one that intercepts binding
    without touching the action is caught by the second.
    """
    argv = _argv_for(path, options, positionals, marker)
    parser, namespace = _parse_boundary(argv, entry=entry)

    assert getattr(namespace, "command", None) == path[0], (
        f"`atlas {' '.join(argv)}` routed to "
        f"{getattr(namespace, 'command', None)!r}, not {path[0]!r}"
    )
    if len(path) > 1:
        route = _routing_dest(parser, path)
        assert getattr(namespace, route, None) == path[1], (
            f"`atlas {' '.join(argv)}` records its route on {route!r} as "
            f"{getattr(namespace, route, None)!r}, not {path[1]!r}. A scan of "
            "every namespace value for the subcommand name was the previous "
            "check; it passes on any attribute that happens to hold the "
            "string, which is not the same as the route being intact."
        )

    target = _descend(parser, path)
    exposed = _options(target)
    for flag, dest, nargs, leaf_type, repeatable in options:
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
        assert _carries(getattr(namespace, dest), marker(flag)), (
            f"`{label}` {flag}={marker(flag)!r} did not land on {dest!r} "
            f"(found {getattr(namespace, dest)!r})"
        )
        _assert_shape(
            label,
            flag,
            _action_for(parser, path, flag),
            getattr(namespace, dest),
            nargs,
            leaf_type,
            repeatable,
        )
    _assert_positionals(parser, namespace, path, positionals, marker)


@pytest.mark.parametrize("marker", MARKERS, ids=["marker", "marker-alt"])
@pytest.mark.parametrize("command", sorted(CERTIFIED_OPTIONS))
def test_certified_dests_hold_at_the_parse_boundary(
    command: str, marker: Callable[[str], str]
) -> None:
    """P04: the certified `dest`s, observed after `main` has finished with the
    parser rather than before it starts."""
    _assert_certified_dests(
        main,
        (command,),
        CERTIFIED_OPTIONS[command],
        CERTIFIED_POSITIONALS.get(command, ()),
        marker,
    )


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
        for flag, *_rest in CERTIFIED_OPTIONS.get(command, ()):
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


# ---------------------------------------------------------------------------
# The dispatch boundary.
#
# The parse boundary above is not the last word, and an earlier revision of
# this file claimed it was. Independent verification pointed at production
# `main`, which calls `_apply_stranger_defaults(args)` *after* parsing and
# rewrites `args.vault`, `args.project` and `args.projects`. So parsing is not
# the instant the operator's namespace becomes a settled fact, and two attacks
# live in the gap:
#
#   * `main` parses twice -- a healthy decoy parse whose result is discarded,
#     then the real one. A check that stops on the first `parse_args` call
#     never observes the second.
#   * `main` edits `args` after parsing. Nothing about either parser is wrong.
#
# These controls are checked by recording the namespace processed by
# `_apply_stranger_defaults` and stopping at `load_config`. This observes its
# certified marker values at that seam, not arbitrary later edits or namespace
# substitutions. The identity assertion below binds it to a parsed namespace;
# it does not prove which object a later handler will receive.
# ---------------------------------------------------------------------------


class _DispatchBoundary(BaseException):
    """Stop `main` after post-parse processing, before any command runs."""


def _dispatch_boundary(
    argv: list[str], entry: Callable[[list[str]], object]
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Capture the processed namespace at the pre-load_config observation seam.

    The parser argparse was handed comes back with it, because the route's
    landing site is a property of the parser and the route's *value* is a
    property of the namespace; pinning the route needs both.
    """
    from project_atlas import cli as cli_module

    captured: list[argparse.Namespace] = []
    parsed: list[argparse.Namespace] = []
    parsers: list[argparse.ArgumentParser] = []
    real_apply = cli_module._apply_stranger_defaults
    real_load = cli_module.load_config
    real_parse_args = argparse.ArgumentParser.parse_args

    def _parse_recorder(
        self: argparse.ArgumentParser,
        args: object = None,
        namespace: object = None,
    ) -> argparse.Namespace:
        result = real_parse_args(self, args, namespace)  # type: ignore[arg-type]
        parsed.append(result)
        parsers.append(self)
        return result

    def _apply_spy(args: argparse.Namespace) -> None:
        real_apply(args)
        captured.append(args)

    def _load_spy(*_args: object, **_kwargs: object) -> object:
        raise _DispatchBoundary("dispatch-boundary")

    cli_module._apply_stranger_defaults = _apply_spy  # type: ignore[assignment]
    cli_module.load_config = _load_spy  # type: ignore[assignment]
    argparse.ArgumentParser.parse_args = _parse_recorder  # type: ignore[method-assign]
    try:
        entry(argv)
    except _DispatchBoundary:
        pass
    except SystemExit as exc:
        raise AssertionError(
            f"`atlas {' '.join(argv)}` exited {exc.code} before dispatch"
        ) from exc
    except Exception as exc:
        # A documented invocation that cannot reach dispatch is broken, however
        # it fails. `ConnectError` is the common shape: damage that empties
        # `args.vault` sends `_apply_stranger_defaults` looking for a bind that
        # is not there. Real `main` catches that and returns EXIT_ERROR, which
        # arrives here as the `else` branch below; an entry point that lets it
        # escape arrives here.
        raise AssertionError(
            f"`atlas {' '.join(argv)}` raised {type(exc).__name__} before "
            f"dispatch: {exc}"
        ) from exc
    else:
        raise AssertionError(
            f"`atlas {' '.join(argv)}` never reached the dispatch boundary; "
            "the entry point does not process the operator's arguments the "
            "way the CLI does"
        )
    finally:
        cli_module._apply_stranger_defaults = real_apply  # type: ignore[assignment]
        cli_module.load_config = real_load  # type: ignore[assignment]
        argparse.ArgumentParser.parse_args = real_parse_args  # type: ignore[method-assign]

    assert len(captured) == 1, (
        f"expected exactly one namespace at the dispatch boundary, captured "
        f"{len(captured)}. The CLI's post-parse processing is not the shape "
        "this oracle observes: `_apply_stranger_defaults` was called "
        f"{len(captured)} times before `load_config` rather than once. If that "
        "is a deliberate refactor, this helper needs updating with it."
    )
    # Cardinality is not identity, and identity is the property that matters.
    # Handing `_apply_stranger_defaults` a `copy.copy(args)` and dispatching on
    # the original satisfies every value assertion below while the operator's
    # namespace is untouched by any of them. Verification demonstrated exactly
    # that, with full operator damage and the suite green.
    assert any(captured[0] is candidate for candidate in parsed), (
        "the namespace processed before dispatch is not one argparse produced. "
        "The entry point processed a copy, so what this oracle observed is not "
        "what the command will receive"
    )
    return parsers[parsed.index(captured[0])], captured[0]


def _assert_dispatch_dests(
    entry: Callable[[list[str]], object],
    path: tuple[str, ...],
    options: tuple[tuple[str, str], ...],
    positionals: tuple[tuple[int, str, object, type], ...] = (),
    marker: Callable[[str], str] = _sentinel,
) -> None:
    """Check certified marker values on the captured, processed namespace."""
    argv = _argv_for(path, options, positionals, marker)
    parser, namespace = _dispatch_boundary(argv, entry=entry)
    label = "atlas " + " ".join(path)

    assert getattr(namespace, "command", None) == path[0], (
        f"`{label}` dispatches as {getattr(namespace, 'command', None)!r}"
    )
    # `command` alone is not the route. `cli.main` branches on
    # `args.capture_command` to choose between `capture record` and
    # `capture list`, so a post-parse `args.capture_command = "list"` sends
    # the operator's `record` invocation to the wrong handler while every
    # certified flag value on this namespace survives intact -- invisible to
    # every other assertion here. Verified: with this pin absent the whole
    # suite passes under exactly that mutation, and only two unrelated
    # functional tests catch it, which is the incidental backstop this file
    # rejects as a substitute for a pin.
    if len(path) > 1:
        route = _routing_dest(parser, path)
        assert getattr(namespace, route, None) == path[1], (
            f"`{label}` reaches dispatch routed to "
            f"{getattr(namespace, route, None)!r} on {route!r}, not "
            f"{path[1]!r}; the operator's subcommand was changed after parsing"
        )
    for flag, dest, _nargs, leaf_type, repeatable in options:
        assert hasattr(namespace, dest), (
            f"`{label}` reaches dispatch without {dest!r}; {flag} was accepted "
            "and its value is gone by the time the command runs"
        )
        assert _carries(getattr(namespace, dest), marker(flag)), (
            f"`{label}` {flag}={marker(flag)!r} is not on {dest!r} at "
            f"dispatch (found {getattr(namespace, dest)!r}); it was altered "
            "after parsing"
        )
        _assert_value_shape(
            label, flag, getattr(namespace, dest), leaf_type, repeatable
        )
    for _index, dest, _nargs, leaf_type in positionals:
        assert hasattr(namespace, dest), f"`{label}` reaches dispatch without {dest!r}"
        _assert_value_shape(
            label, f"positional {dest!r}", getattr(namespace, dest), leaf_type, False
        )
        assert _carries(getattr(namespace, dest), marker(dest)), (
            f"`{label}` positional {dest!r} is not intact at dispatch "
            f"(found {getattr(namespace, dest)!r})"
        )


@pytest.mark.parametrize("marker", MARKERS, ids=["marker", "marker-alt"])
@pytest.mark.parametrize("command", sorted(CERTIFIED_OPTIONS))
def test_certified_values_survive_to_the_dispatch_boundary(
    command: str, marker: Callable[[str], str]
) -> None:
    """Certified marker values remain intact at the pre-load_config seam.

    Run with two markers of different shape: a transform the CLI applies is
    only visible in a marker it is not a fixed point of.
    """
    _assert_dispatch_dests(
        main,
        (command,),
        CERTIFIED_OPTIONS[command],
        CERTIFIED_POSITIONALS.get(command, ()),
        marker,
    )


@pytest.mark.parametrize("marker", MARKERS, ids=["marker", "marker-alt"])
@pytest.mark.parametrize("pair", sorted(CERTIFIED_SUBCOMMAND_OPTIONS))
def test_certified_subcommand_values_survive_to_dispatch(
    pair: tuple[str, str], marker: Callable[[str], str]
) -> None:
    _assert_dispatch_dests(
        main, pair, CERTIFIED_SUBCOMMAND_OPTIONS[pair], (), marker
    )


# --- Negative controls for the attacks the parse boundary cannot see --------


# These damage `ask2`, not `brief`, on purpose. `brief` is in
# `_apply_stranger_defaults`' resolution set, so emptying its `vault` sends the
# real function looking for a connect bind and it raises `ConnectError` before
# `load_config` is reached. That still blocks, but it blocks for the wrong
# reason -- verification showed these controls passing with the entire value
# assertion deleted, which makes them worthless as controls in CI where no bind
# exists. `ask2` is in none of those sets, so post-parse processing is a no-op
# and the controls reach the assertion they exist to exercise.


def _r_double_parse(argv: list[str]) -> object:
    """Parse twice: a healthy decoy first, the damaged real parse second.

    Defeats any check that stops on the first `parse_args` call.
    """
    from project_atlas import cli as cli_module

    parser = build_parser()
    build_parser().parse_args(argv)
    _action_for(parser, ("ask2",), "--question").dest = "question_renamed"
    args = parser.parse_args(argv)
    cli_module._apply_stranger_defaults(args)
    cli_module.load_config(getattr(args, "config", None))
    return 0


def _r_post_parse_edit(argv: list[str]) -> object:
    """Edit the namespace after parsing. Both parsers are beyond reproach."""
    from project_atlas import cli as cli_module

    parser = build_parser()
    args = parser.parse_args(argv)
    args.question = "replaced-after-parsing"
    cli_module._apply_stranger_defaults(args)
    cli_module.load_config(getattr(args, "config", None))
    return 0


def _r_shadow_namespace(argv: list[str]) -> object:
    """Process a copy, dispatch on the original.

    Every value assertion would pass on the copy. The operator's namespace is
    a different object and carries the damage.
    """
    from project_atlas import cli as cli_module

    parser = build_parser()
    args = parser.parse_args(argv)
    cli_module._apply_stranger_defaults(copy.copy(args))
    args.question = "replaced-after-parsing"
    cli_module.load_config(getattr(args, "config", None))
    return 0


def _r_healthy(argv: list[str]) -> object:
    """The control: `main`'s own shape, undamaged."""
    from project_atlas import cli as cli_module

    parser = build_parser()
    args = parser.parse_args(argv)
    cli_module._apply_stranger_defaults(args)
    cli_module.load_config(getattr(args, "config", None))
    return 0


DISPATCH_MATRIX: tuple[tuple[str, Callable[[list[str]], object]], ...] = (
    ("P04F-second-parse-wins", _r_double_parse),
    ("P04G-post-parse-namespace-edit", _r_post_parse_edit),
    ("P04H-shadow-namespace", _r_shadow_namespace),
)


@pytest.mark.parametrize(
    "case", DISPATCH_MATRIX, ids=[case[0] for case in DISPATCH_MATRIX]
)
def test_dispatch_matrix_is_blocked(
    case: tuple[str, Callable[[list[str]], object]],
) -> None:
    """Damage that is invisible at the parse boundary must block at dispatch."""
    _label, entry = case
    with pytest.raises(AssertionError):
        _assert_dispatch_dests(entry, ("ask2",), CERTIFIED_OPTIONS["ask2"])


def test_dispatch_matrix_control_passes_undamaged() -> None:
    """An undamaged entry point of the same shape passes the same assertions."""
    _assert_dispatch_dests(_r_healthy, ("ask2",), CERTIFIED_OPTIONS["ask2"])


# --- The route is part of the operator's request, not just its values -------


# Every case above damages a *value*. This one leaves all of them intact and
# changes which handler runs. `cli.main` selects between `capture record` and
# `capture list` on `args.capture_command`, so rewriting it after parsing runs
# the operator's `record` invocation as a `list` -- with `command`, `--vault`,
# `--project` and every other certified value still exactly as typed. It was
# invisible to this file until the route was pinned: the whole suite passed
# under it, and the only tests that failed were two unrelated functional
# suites that happen to exercise `capture`. That is the incidental backstop
# this file rejects everywhere else, so the route is pinned directly.


def _r_route_rewrite(argv: list[str]) -> object:
    """Rewrite the subcommand route after parsing. Every value survives."""
    from project_atlas import cli as cli_module

    parser = build_parser()
    args = parser.parse_args(argv)
    args.capture_command = "list"
    cli_module._apply_stranger_defaults(args)
    cli_module.load_config(getattr(args, "config", None))
    return 0


def _r_route_healthy(argv: list[str]) -> object:
    """The control: the same shape, with the route left alone."""
    from project_atlas import cli as cli_module

    parser = build_parser()
    args = parser.parse_args(argv)
    cli_module._apply_stranger_defaults(args)
    cli_module.load_config(getattr(args, "config", None))
    return 0


def test_post_parse_route_rewrite_is_blocked() -> None:
    """A post-parse subcommand-route rewrite must block at dispatch."""
    with pytest.raises(AssertionError):
        _assert_dispatch_dests(
            _r_route_rewrite,
            ("capture", "record"),
            CERTIFIED_SUBCOMMAND_OPTIONS[("capture", "record")],
        )


def test_post_parse_route_control_passes_undamaged() -> None:
    """The control for it: same shape, undamaged, same assertions."""
    _assert_dispatch_dests(
        _r_route_healthy,
        ("capture", "record"),
        CERTIFIED_SUBCOMMAND_OPTIONS[("capture", "record")],
    )


def test_omitted_certified_flags_keep_their_documented_default() -> None:
    """A post-build `set_defaults` supplies a value the operator never gave.

    `brief --vault` is documented optional, and omitting it must leave the
    slot empty for `_apply_stranger_defaults` to resolve from the local bind.
    `parser.set_defaults(vault=Path("/attacker/vault"))` after `build_parser()`
    returns leaves every flag, `dest` and help string correct, and hands the
    command a vault the operator did not name. Observed before
    `_apply_stranger_defaults` runs, which is the only point where the
    documented default is still distinguishable from a resolved one.
    """
    _parser, namespace = _parse_boundary(["brief", "--project", "p"], entry=main)
    assert namespace.vault is None, (
        f"`atlas brief --project p` arrived with vault={namespace.vault!r}; "
        "a value was supplied for a flag the operator omitted"
    )
    assert namespace.projects == ["p"]

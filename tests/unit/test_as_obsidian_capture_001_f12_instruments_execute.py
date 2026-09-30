"""AS-OBSIDIAN-CAPTURE-001-F12 — the committed instruments must actually run.

Three evidence instruments live under ``docs/scripts/``. Each was committed for
the same stated reason: an instrument *described* is not an instrument
*available*, and a seal that cites measurements no clean checkout can reproduce
is citing nothing.

Measured on `a7adce4e`: **none of the three is referenced by any test or CI
file.** `pyproject.toml` sets ``testpaths = ["tests"]``, so pytest never sees
them and `ruff check .` does not lint them either. They can be renamed, broken
by a refactor, or silently invalidated, and nothing notices — which is the same
failure the seals were meant to close, one level up.

This wires them, with deliberately different scope per instrument, because they
do not have the same contract:

* ``f9_diagnostic_parity.py`` has a clean pass/fail contract and touches
  nothing. It is **executed**, and its result asserted.
* ``f8_near_miss_controls.py`` deliberately MUTATES source files and restores
  them in a ``finally``. Running that inside CI would leave a broken tree if it
  were interrupted, so it is **not executed here** — only checked to import
  cleanly and still expose its entry point. Rot is caught; destruction is not
  risked.
* ``seal_retracted_claim_sweep.py`` over-reports by design and needs a human to
  classify its hits, so a pass/fail assertion on its exit code would be wrong.
  It too is **import- and interface-checked** only.

**How the exclusion is enforced, and what it does not reach.** Two layers, after
two weaker revisions. :func:`_fuse` replaces the mutating instrument's ``main``
with a tripwire at load time; that is the load-bearing layer and it is shape
independent **for the call** — every *shape* of call through :func:`_load` raises
— but coverage is exactly what the identity predicate matches, and that has a
stated residue. :func:`_executing_references_to` is the second layer, a static scan for the
primitives that bypass :func:`_load` altogether (``subprocess``, ``runpy``,
``os``, ``importlib``, ``exec``), resolved through import aliases and through
names bound to string literals.

Neither reaches a path that both bypasses :func:`_load` *and* names the script
in a way no constant resolves — a filename assembled from fragments at runtime,
or read from another file. That residue is real. It is recorded rather than
implied away, because the first two revisions of this guard were each defeated
by a shape their author had not imagined, and the honest response is to say
where the third one ends rather than to claim it ends nowhere.

What this does NOT do, stated so nobody reads more into a green suite: it does
not verify the instruments are *correct*, only that they still load and, for the
one with a decidable contract, still report what the record says.

And one concrete limit of the instrument it does execute, worth naming here
because a green line is otherwise reassuring beyond its warrant:
``f9_diagnostic_parity.py`` always passes ``rendered=FRESH``. It varies only the
prior note, so it **cannot observe a rendered-side divergence at all** -- which
is exactly the class F10 had to find with a two-sided sweep. Wiring this up
catches rot and message-parity regressions; it does not make the parity claim
broader than the corpus behind it.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import os
import pathlib
import subprocess
import sys
import types
from collections.abc import Callable

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "scripts"

#: The one instrument that rewrites source files. Never executed from here.
_MUTATING = "f8_near_miss_controls.py"

#: Each instrument's own public entry points, pinned so a rename or a refactor
#: fails here rather than rotting silently.
#:
#: An earlier revision called these "the callable each record names". Measured
#: with word-boundary, case-sensitive matches across ``docs/evidence/*.md`` plus
#: ``WORKLOG.md`` and ``docs/backlog.md`` -- the method is stated because the
#: earlier revision gave a bare figure that no one else could reproduce, which is
#: this lane's own failure mode:
#:
#:     main 682   sweep 32   outcome 30   ANCHOR 5   CLAIMS 5
#:     NAMED 0    FRAGMENTS 0   CONTROLS 0   RETRACTION 0
#:
#: So four of the ten appear nowhere, and the other six appear only as ordinary
#: English prose -- never as a citation of the callable. An earlier revision said
#: "only ``main`` appears at all", which is false and contradicted the very next
#: sentence it wrote; verification caught it. The list is derived from the
#: scripts, not from the records, which still catches rot but is a weaker thing
#: than the original comment claimed.
#:
#: Measured at the same time, and recorded rather than quietly fixed:
#: ``seal_retracted_claim_sweep.py`` is cited by **no** evidence record, backlog
#: line or WORKLOG line anywhere in the repository. It was committed as
#: reproducible evidence and nothing refers to it -- the same rot this module
#: exists to catch, one level further out, and not in this package's scope.
CITED = {
    "f9_diagnostic_parity.py": ("main", "outcome", "NAMED", "FRAGMENTS"),
    "f8_near_miss_controls.py": ("main", "CONTROLS", "ANCHOR"),
    "seal_retracted_claim_sweep.py": ("sweep", "CLAIMS", "RETRACTION"),
}


def _invalidate_cached_bytecode(path: pathlib.Path) -> None:
    """Drop any ``__pycache__`` entry for ``path`` so the next load recompiles.

    Extracted so it can be tested against a throwaway file rather than against a
    committed instrument -- this suite must not mutate those, which is the whole
    subject of the exclusion below.
    """
    cached = pathlib.Path(importlib.util.cache_from_source(str(path)))
    cached.unlink(missing_ok=True)


def _is_the_mutating_instrument(path: pathlib.Path) -> bool:
    """Identity by filename **OR** content hash — the union, not a replacement.

    Two revisions of this predicate each closed one class and opened another,
    because each **replaced** the previous rule instead of joining it:

    ======================================  ============  ============  =========
    candidate                               ``name`` only  hash only     union
    ======================================  ============  ============  =========
    other name, identical bytes             REAL BODY     blocked       blocked
    same name, one byte edited              blocked       REAL BODY     blocked
    ======================================  ============  ============  =========

    Verification measured both columns. The name rule missed a copy or symlink
    under another name; the hash rule then missed a **maintainer editing the
    instrument**, which keeps the filename and changes a byte -- a trailing
    newline, an inserted comment, CRLF endings, a stray ``print``. That is the
    likelier accident of the two, and the guard exists so that the exclusion
    "stays a decision rather than becoming an accident".

    Neither rule is a superset of the other, so the guard takes **both**. A
    false fuse costs nothing: nothing in this suite legitimately loads a file
    named like the mutating instrument, and if one ever does, fusing it is the
    safe direction. Recorded so it is not rediscovered as a surprise: a future
    fixture named ``f8_near_miss_controls.py`` with harmless content, loaded
    through :func:`_load_from` and then called, will raise.

    **What the union still does not reach**, since a table showing only the two
    columns each single rule missed reads as though the pair covers everything:
    a copy that changes **both** -- a different name *and* edited content --
    matches neither half and loads unfused. Measured. The static layer misses
    that source shape too, so it is the intersection of both layers' residues,
    it has been open in every revision of this guard, and it is not closed here.
    Nothing plausible produces it by accident, which is the argument for leaving
    it rather than an argument that it is absent.
    """
    if path.name == _MUTATING:
        return True
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest == _mutating_digest()


def _mutating_digest() -> str:
    return hashlib.sha256((SCRIPTS / _MUTATING).read_bytes()).hexdigest()


def _fuse(module: types.ModuleType) -> None:
    """Replace the mutating instrument's entry point with a tripwire.

    Shape independent **for the call**, which is the half a static check cannot
    do: an alias, a walrus, a tuple target, an annotated assignment,
    ``getattr``, a helper function, a name assembled at runtime -- all covered,
    because the fuse acts on the module object rather than on the syntax.

    It is **not** independent of *which file* is loaded. Coverage is exactly
    what :func:`_is_the_mutating_instrument` matches, and that predicate has a
    stated residue. An earlier revision of this paragraph said "every route ...
    whatever the call looks like" with no such qualifier -- the
    boundary-narrower-than-reality failure this package exists to prevent,
    asserted a few lines from the paragraph that qualifies it.

    **The real callable is deliberately NOT published.** An earlier revision
    stored it on ``tripwire.__wrapped__`` so the interface check could confirm
    it survived. Verification found that this hands out a one-line bypass --
    ``module.main.__wrapped__(root, ref)`` and ``inspect.unwrap(module.main)``
    both reached the real mutating body -- and ``__wrapped__`` is the *standard*
    unwrapping convention, so the bypass is the idiomatic thing for the next
    maintainer to write. It survives only as a boolean marker now; the fuse
    asserts the real entry point was callable at the moment it replaced it,
    which is what the interface check actually needs.
    """
    real = getattr(module, "main", None)
    assert callable(real), f"{_MUTATING} lost its main() entry point"

    def tripwire(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            f"{_MUTATING} was EXECUTED by this suite. It rewrites files under "
            "src/ and restores them in a finally block; a hard interrupt -- "
            "SIGKILL, SIGTERM, os._exit, power loss; `finally` already covers "
            "KeyboardInterrupt -- would leave the tree mutated. The exposure "
            "that matters is a developer's local checkout, since a hosted CI "
            "workspace is discarded either way. Import- and interface-checked "
            "here, never run."
        )

    tripwire.__fused__ = True  # type: ignore[attr-defined]
    module.main = tripwire  # type: ignore[attr-defined]


def _load_from(path: pathlib.Path, *, invalidate: bool = True) -> types.ModuleType:
    """The one loader. Everything that loads an instrument comes through here.

    An earlier revision had two: :func:`_load` and a parameterised ``_load_from``
    added for the bytecode test. Only the first fused, so
    ``_load_from(SCRIPTS / _MUTATING).main(root, ref)`` returned an **unfused**
    module and ran the real mutating body -- structurally the same defect this
    guard exists to close, reintroduced by the fix, one line above the test that
    motivated the helper. Verification caught it. There is now a single load
    path and therefore a single place the fuse can be forgotten.

    ``invalidate`` forces a fresh compile. ``exec_module`` accepts a cached
    ``.pyc`` whose header ``(mtime, size)`` still matches, so an edit that keeps
    the byte length and lands in the same second runs STALE bytecode -- inert in
    CI, live in the local edit-test loop, which is exactly where this lane runs
    its negative controls. It is a parameter only so the hazard can be
    reproduced against a throwaway file.
    """
    if invalidate:
        _invalidate_cached_bytecode(path)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if _is_the_mutating_instrument(path):
        _fuse(module)
    return module


def _load(name: str) -> types.ModuleType:
    return _load_from(SCRIPTS / name)


@pytest.mark.parametrize("name", sorted(CITED))
def test_f12_every_cited_instrument_still_exists(name: str) -> None:
    assert (SCRIPTS / name).is_file(), f"{name} is cited by a sealed record and is missing"


@pytest.mark.parametrize("name", sorted(CITED))
def test_f12_every_cited_instrument_imports_cleanly(name: str) -> None:
    """A broken import is how an uncited, unlinted script rots."""
    _load(name)


@pytest.mark.parametrize("name", sorted(CITED))
def test_f12_every_cited_instrument_keeps_the_interface_its_record_names(name: str) -> None:
    module = _load(name)
    missing = [attr for attr in CITED[name] if not hasattr(module, attr)]
    assert not missing, f"{name} lost {missing}, which its evidence record refers to"


def test_f12_the_parity_instrument_still_reports_what_the_seal_claims() -> None:
    """The one instrument with a decidable contract is actually executed.

    F9's seal cites this script's result. If the two writers ever diverge again,
    or the corpus goes inert, this fails in CI rather than the next time someone
    happens to run it by hand.
    """
    module = _load("f9_diagnostic_parity.py")
    exit_code = module.main()
    assert exit_code == 0, "the two writers diverged, or the corpus stopped exercising them"


def test_f12_the_parity_instrument_runs_as_a_script_too() -> None:
    """Cited as a command in the seal, so the command form is what is checked."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "f9_diagnostic_parity.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "divergences            0" in result.stdout, result.stdout


#: Module roots whose callables start a process or execute code. Matched after
#: resolving import aliases, so ``import subprocess as sp`` is not an escape.
_EXEC_MODULES = frozenset(
    {"subprocess", "runpy", "os", "importlib", "asyncio", "pty", "multiprocessing"}
)

#: Builtins that execute source directly.
_EXEC_BUILTINS = frozenset({"exec", "eval", "compile", "__import__"})


def _script_spellings(script: str) -> tuple[str, ...]:
    """Both ways the script gets named: as a filename and as a module name.

    ``importlib.import_module("f8_near_miss_controls")`` and
    ``runpy.run_module(...)`` name it without the ``.py``. An earlier revision
    matched only the filename, so every module-name form went undetected while
    sitting inside the coverage its docstring claimed.
    """
    return (script, script.removesuffix(".py"))


def _names_naming(tree: ast.AST, script: str, *, seed: set[str] | None = None) -> set[str]:
    """Identifiers ever bound to a value that mentions ``script``.

    Deliberately conservative, and deliberately **not** a scoped constant model.
    An earlier revision substituted the *last* binding of each name anywhere in
    the tree, which verification showed cuts both ways: a later rebinding of a
    name silently hid a real execution path, and an unrelated later binding
    flagged a harmless one. Order- and scope-blindness is unavoidable without a
    real dataflow pass, so this errs toward flagging: if a name is *ever* bound
    to something mentioning the script, uses of that name are suspect.

    Because it looks at the whole assigned subtree rather than a bare literal,
    it also covers the forms that slipped past substitution -- a dict literal, a
    list iterated by a for-loop, a class attribute, a parameter default.
    """
    spellings = _script_spellings(script)
    names: set[str] = set(seed or ())

    def mentions(node: ast.AST) -> bool:
        """True if the value names the script, or a name already suspect.

        The second half makes it transitive, and closes a real miss:
        ``src = Path(_M).read_text()`` followed by ``exec(src, {})`` mentions the
        script only through ``_M``, so a single pass left ``src`` unflagged while
        the call really did execute it. Iterated to a fixpoint below.
        """
        rendered = ast.unparse(node)
        if any(sp in rendered for sp in spellings):
            return True
        used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        return bool(used & names)

    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets, value = [node.target], node.iter
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            slots = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            defaults = list(args.defaults) + [d for d in args.kw_defaults if d]
            # strict=False is correct: defaults align to the TAIL of the slot
            # list, so the two sequences are deliberately different lengths.
            for arg, default in zip(reversed(slots), reversed(defaults), strict=False):
                if mentions(default):
                    names.add(arg.arg)
            continue
        if value is None or not mentions(value):
            continue
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif isinstance(sub, ast.Attribute):
                    names.add(sub.attr)
    return names


def _names_naming_closure(tree: ast.AST, script: str) -> set[str]:
    """:func:`_names_naming` iterated until it stops growing.

    A single pass already resolves *forward* chains, because :func:`_names_naming`
    grows its set during one walk. What needs iteration is a **reverse-ordered**
    chain -- ``exec(z)`` above ``z = v0`` above ``v0 = v1`` ... -- which
    propagates one hop per pass. An earlier revision claimed "one pass could not
    see it" of a forward chain; that was wrong, and the two corpus entries meant
    to demonstrate transitivity both passed under a single pass, so the mechanism
    was unpinned. The corpus now carries a reverse chain that requires it.

    The bound fails **closed**: a file needing more than 64 passes raises rather
    than returning a truncated, falsely-clean result.
    """
    names: set[str] = set()
    for _ in range(64):
        grown = _names_naming(tree, script, seed=names)
        if grown == names:
            return names
        names = grown
    raise AssertionError(
        "the name-resolution fixpoint did not converge; refusing to report a "
        "clean result from a truncated analysis. An earlier revision capped this "
        "at 16 and returned whatever it had, so a long enough reverse-ordered "
        "chain silently produced an empty -- i.e. 'safe' -- verdict. A guard that "
        "truncates must fail closed."
    )


def _exec_roots(tree: ast.AST) -> set[str]:
    """Local names that reach an execution primitive, following import aliases."""
    roots = set(_EXEC_BUILTINS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _EXEC_MODULES:
                    roots.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (
            (node.module or "").split(".")[0] in _EXEC_MODULES
        ):
            roots.update(alias.asname or alias.name for alias in node.names)
    return roots


def _executing_references_to(source: str, script: str) -> list[str]:
    """Call sites in ``source`` that would run ``script`` without the loader.

    The second layer. :func:`_fuse` is the load-bearing one and covers every
    *call shape* through :func:`_load_from`, for the files
    :func:`_is_the_mutating_instrument` matches; this covers the primitives that
    bypass the loader entirely -- ``subprocess``, ``runpy``,
    ``os``, ``importlib``, ``asyncio``, ``exec``/``eval``/``compile`` -- naming
    the script either as a filename or as a module name, directly or through any
    identifier ever bound to something that mentions it.

    **Where it ends**, stated precisely because an earlier revision's boundary
    claim was narrower than its actual misses, which is the over-reading this
    package exists to prevent. It does not see: a name assembled or read at
    runtime and never written literally in this file (from fragments, a file,
    ``os.environ``, ``sys.argv``); a call routed through a helper defined in
    another module; or an execution primitive reached by a root outside
    ``_EXEC_MODULES``. Those are covered by :func:`_fuse` if they go through the
    loader, and by nothing if they do not. That residue is real.
    """
    tree = ast.parse(source)
    roots = _exec_roots(tree)
    suspect = _names_naming_closure(tree, script)
    spellings = _script_spellings(script)

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = ast.unparse(node.func)
        if callee.split(".")[0] not in roots:
            continue
        rendered = ast.unparse(node)
        names_used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names_used |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        if any(sp in rendered for sp in spellings) or (names_used & suspect):
            found.append(f"line {node.lineno}: {callee}(... {script} ...)")
    return found


def test_f12_the_mutating_instrument_is_not_executed_by_this_suite() -> None:
    """Guard the deliberate scope choice above, in two independent layers.

    ``f8_near_miss_controls.py`` rewrites source files and restores them in a
    ``finally``. If a future edit made this suite run it, an interrupted CI job
    would leave a mutated tree. The exclusion has to stay a decision rather than
    becoming an accident.

    **Two earlier revisions of this guard were weaker than they read.**

    The first asserted a substring: ``'f8_near_miss_controls.py", ('`` not in
    ``source.replace(" ", "")``. The needle contains a space and the haystack had
    every space stripped, so it could never match under any edit. Two review bots
    caught it independently.

    The second replaced that with a static AST detector. Better, but verification
    showed 15 of 20 execution paths still slipped past it -- including
    ``_load(_MUTATING).main()``, using the module's own constant, on the very
    line this test writes below. A maintainer copying that line and appending
    ``.main()`` defeated the whole guard. Enumerating call shapes is an arms race
    the guard loses.

    So the load-bearing layer is no longer static. :func:`_fuse` replaces the
    instrument's ``main`` with a tripwire at load time, which is **shape
    independent for the call**: aliasing, a walrus, a tuple target, ``getattr``,
    a helper function, a module-level binding -- every such shape through
    :func:`_load` raises. It is not independent of WHICH FILE is loaded; see
    :func:`_is_the_mutating_instrument` for the residue.
    :func:`_executing_references_to` is now the second layer, covering the
    primitives that bypass :func:`_load` entirely.

    Each layer carries its own positive control, because a guard that cannot be
    shown to fire is indistinguishable from one that is broken.
    """
    source = pathlib.Path(__file__).read_text()

    # Layer 2 control. `f9_diagnostic_parity.py` IS executed here as a script,
    # so a detector reporting nothing for it is broken rather than reassuring.
    control = _executing_references_to(source, "f9_diagnostic_parity.py")
    assert control, (
        "positive control: the parity instrument is executed here via subprocess, "
        "so the detector must find that call; it found nothing, which means the "
        "detector is broken and its silence about the mutating script is worthless"
    )

    running = _executing_references_to(source, _MUTATING)
    assert not running, (
        f"this suite would RUN the source-mutating instrument at: {running}. "
        "It rewrites files under src/ and restores them; an interrupted CI job "
        "would leave the tree mutated."
    )

    # Layer 1, and its control in the same breath: the entry point is still
    # present and callable -- which is what the evidence record depends on --
    # and calling it here raises rather than mutating anything.
    module = _load(_MUTATING)
    assert callable(module.main), "entry point kept, but not invoked here"
    with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
        module.main()
    assert getattr(module.main, "__fused__", False) is True


def test_f12_the_fuse_survives_every_binding_shape() -> None:
    """The shapes that defeated the static detector must all raise.

    Exercised rather than pattern-matched, so this cannot rot into another
    assertion about source text. Note what these do and do not prove: they vary
    how the RESULT of the loader is bound, which is close to tautological
    against a fuse that mutates the returned module. The load-bearing cases are
    the two below them -- the unwrapping routes verification found reaching the
    real body.
    """
    shapes: list[tuple[str, types.ModuleType]] = [
        ("module-level constant", _load(_MUTATING)),
        ("string literal", _load("f8_near_miss_controls.py")),
        ("direct path load", _load_from(SCRIPTS / _MUTATING)),
        ("direct path load, no invalidation", _load_from(SCRIPTS / _MUTATING, invalidate=False)),
    ]
    aliased = _load(_MUTATING)
    shapes.append(("alias", aliased))
    if (walrus := _load(_MUTATING)) is not None:
        shapes.append(("walrus", walrus))
    annotated: types.ModuleType = _load(_MUTATING)
    shapes.append(("annotated assignment", annotated))
    pair, _ = (_load(_MUTATING), None)
    shapes.append(("tuple target", pair))
    assembled = _load("f8_near_miss" + "_controls.py")
    shapes.append(("name assembled at runtime", assembled))

    def via_helper(module: types.ModuleType) -> types.ModuleType:
        return module

    shapes.append(("helper indirection", via_helper(_load(_MUTATING))))

    for label, module in shapes:
        with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
            module.main()
        with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
            # B009 suppressed deliberately: the constant getattr IS the shape
            # under test. It defeated an earlier revision's detector, which only
            # matched `ast.Attribute` callees, so it is exercised literally.
            getattr(module, "main")()  # noqa: B009
        bound = module.main
        with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
            bound()
        assert getattr(module.main, "__fused__", False) is True, label


def test_f12_the_fuse_cannot_be_unwrapped(tmp_path: pathlib.Path) -> None:
    """The two routes verification measured reaching the real mutating body.

    An earlier revision stored the real callable on ``tripwire.__wrapped__`` so
    the interface check could confirm it survived. Both of these then ran the
    real body -- proved by pointing it at an empty root, where it raises
    ``FileNotFoundError`` on its FIRST read, before any write:

        _load(_MUTATING).main.__wrapped__(root, ref)   -> real body entered
        inspect.unwrap(_load(_MUTATING).main)(root, ref) -> real body entered

    ``__wrapped__`` is the standard unwrapping convention, so that bypass was
    the idiomatic thing to write. It is gone. This test asserts it stays gone,
    and does so by attempting the call rather than by inspecting attributes.
    """
    module = _load(_MUTATING)
    assert not hasattr(module.main, "__wrapped__"), (
        "the real entry point is published again; __wrapped__ is a one-line bypass"
    )
    assert inspect.unwrap(module.main) is module.main, "unwrap must not reach past the fuse"

    for label, candidate in (
        ("module.main", module.main),
        ("inspect.unwrap(module.main)", inspect.unwrap(module.main)),
    ):
        with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
            candidate(tmp_path, "origin/main")
        assert label


def test_f12_the_fuse_follows_the_bytes_not_the_filename(tmp_path: pathlib.Path) -> None:
    """Identity is the union of filename and content hash; both halves asserted.

    An earlier revision keyed the fuse on ``path.name == _MUTATING``, so both of
    these loaded UNFUSED through :func:`_load_from` -- the one loader -- and ran
    the real mutating body. Verification measured it; the static layer missed the
    same spellings, so both layers failed together. A second revision then keyed
    on the content hash alone, which opened the opposite case. **Identity is now
    the union of the two**, closing copies, symlinks, hardlinks, renames,
    case-differing names, and edits that keep the name -- and both halves are
    asserted below, because either alone leaves a column open.
    """
    real = SCRIPTS / _MUTATING
    disguises = [("copy", tmp_path / "harmless_helper.py")]
    disguises[0][1].write_bytes(real.read_bytes())
    link = tmp_path / "alias_instrument.py"
    try:
        link.symlink_to(real)
        disguises.append(("symlink", link))
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pass

    for label, path in disguises:
        module = _load_from(path)
        assert getattr(module.main, "__fused__", False) is True, label
        with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
            module.main(tmp_path, "origin/main")

    # The other half of the union: an EDITED copy keeps the filename, so it must
    # still fuse. An earlier revision asserted the opposite here -- that a file
    # merely sharing the name is NOT fused -- on the aesthetic ground that "the
    # tripwire belongs to the code, not the label". That assertion actively
    # pinned a hole: it made every single-byte edit of the instrument load
    # unfused, which is exactly what a maintainer working on it produces.
    mutations: tuple[tuple[str, Callable[[bytes], bytes]], ...] = (
        ("trailing newline", lambda b: b + b"\n"),
        ("inserted comment", lambda b: b"# wip\n" + b),
        ("CRLF endings", lambda b: b.replace(b"\n", b"\r\n")),
    )
    for label, mutate in mutations:
        edited = tmp_path / label.replace(" ", "_") / _MUTATING
        edited.parent.mkdir(parents=True, exist_ok=True)
        edited.write_bytes(mutate(real.read_bytes()))
        module = _load_from(edited)
        assert getattr(module.main, "__fused__", False) is True, label
        with pytest.raises(AssertionError, match="was EXECUTED by this suite"):
            module.main(tmp_path, "origin/main")


def test_f12_the_fixpoint_fails_closed_rather_than_reporting_clean() -> None:
    """A truncated analysis must not read as a clean verdict.

    An earlier revision capped the iteration at 16 and returned whatever it had,
    so a reverse-ordered chain longer than that produced an empty result -- which
    the caller reads as "nothing executes this script". Verification found it.
    """

    def chain(hops: int) -> str:
        return (
            "import runpy\ndef f(): runpy.run_path(z)\n"
            + "".join(f"v{i} = v{i + 1}\n" for i in range(hops))
            + f'v{hops} = "f8_near_miss_controls.py"\nz = v0\n'
        )

    # Well within the bound: resolves, and the old cap of 16 would have missed it.
    assert _executing_references_to(chain(40), _MUTATING)

    # Beyond the bound: must RAISE, not return an empty (i.e. "clean") list.
    with pytest.raises(AssertionError, match="did not converge"):
        _executing_references_to(chain(200), _MUTATING)


def test_f12_the_fuse_leaves_the_other_instruments_alone() -> None:
    """Negative side: only the mutating instrument is fused.

    Without this, a fuse that accidentally wrapped everything would make the
    parity test's real execution indistinguishable from a tripwire, and the
    module would still be green.
    """
    for name in sorted(CITED):
        module = _load(name)
        fused = getattr(getattr(module, "main", None), "__fused__", False)
        assert fused == (name == _MUTATING), f"{name}: fused={fused}"


#: Synthetic sources, each a shape verification measured slipping past the
#: previous revision of the detector. They are exercised against the detector
#: directly rather than through this file, because this file must not itself
#: contain an execution path -- which is precisely why the earlier revisions'
#: "detector is live on this file" control could not reach these shapes.
_MUST_DETECT = {
    "module-level constant": (
        '_M = "f8_near_miss_controls.py"\nimport runpy\ndef f(): runpy.run_path(_M)\n'
    ),
    "aliased subprocess": (
        'import subprocess as sp\ndef f(): sp.run(["py", "f8_near_miss_controls.py"])\n'
    ),
    "from-import of an exec primitive": (
        'from runpy import run_path\ndef f(): run_path("f8_near_miss_controls.py")\n'
    ),
    "exec of the file's text": 'def f(): exec(open("f8_near_miss_controls.py").read())\n',
    "os.system": 'import os\ndef f(): os.system("py f8_near_miss_controls.py")\n',
    "annotated constant": (
        '_M: str = "f8_near_miss_controls.py"\nimport runpy\ndef f(): runpy.run_path(_M)\n'
    ),
    "import_module by MODULE name": (
        '_M = "f8_near_miss_controls"\nimport importlib\ndef f(): importlib.import_module(_M)\n'
    ),
    "__import__ by module name": '_M = "f8_near_miss_controls"\ndef f(): __import__(_M)\n',
    "runpy.run_module by module name": (
        'import runpy\ndef f(): runpy.run_module("f8_near_miss_controls")\n'
    ),
    "dict-literal lookup": (
        'import runpy\nN = {"m": "f8_near_miss_controls.py"}\ndef f(): runpy.run_path(N["m"])\n'
    ),
    "for-loop target": (
        'import runpy\ndef f():\n    for n in ["f8_near_miss_controls.py"]: runpy.run_path(n)\n'
    ),
    "class attribute": (
        'import runpy\nclass C: M = "f8_near_miss_controls.py"\ndef f(): runpy.run_path(C.M)\n'
    ),
    "parameter default": ('import runpy\ndef f(n="f8_near_miss_controls.py"): runpy.run_path(n)\n'),
    "walrus binding": (
        'import runpy\ndef f():\n    if (m := "f8_near_miss_controls.py"): runpy.run_path(m)\n'
    ),
    "read_text then exec (transitive)": (
        'import pathlib\n_M = "f8_near_miss_controls.py"\n'
        "def f():\n    src = pathlib.Path(_M).read_text()\n    exec(src, {})\n"
    ),
    "two-hop transitive": (
        'import pathlib\n_M = "f8_near_miss_controls.py"\n'
        "def f():\n    a = pathlib.Path(_M)\n    b = a.read_text()\n    exec(b, {})\n"
    ),
    "asyncio subprocess": (
        "import asyncio\n"
        'def f(): asyncio.create_subprocess_exec("py", "f8_near_miss_controls.py")\n'
    ),
    "caller's own importlib spec": (
        'import importlib.util as iu\n_M = "f8_near_miss_controls.py"\n'
        'def f(): iu.spec_from_file_location("x", _M)\n'
    ),
    "reverse-ordered chain (requires the fixpoint)": (
        "import runpy\ndef f(): runpy.run_path(z)\n"
        "z = v0\nv0 = v1\nv1 = v2\n"
        'v2 = "f8_near_miss_controls.py"\n'
    ),
    "transitive THROUGH an attribute": (
        "import runpy\nclass C:\n"
        '    M = "f8_near_miss_controls.py"\n'
        "alias = C.M\ndef f(): runpy.run_path(alias)\n"
    ),
    "attribute use (requires attr matching)": (
        'import runpy\nclass C:\n    M = "f8_near_miss_controls.py"\ndef f(): runpy.run_path(C.M)\n'
    ),
    "later rebinding must not hide it": (
        'import runpy\n_M = "f8_near_miss_controls.py"\n'
        'def f(): runpy.run_path(_M)\n_M = "harmless.py"\n'
    ),
}

#: Shapes the detector must NOT flag, so it is not merely returning everything.
_MUST_NOT_DETECT = {
    "a bare mention in a string": '_M = "f8_near_miss_controls.py"\n',
    "a non-executing call": (
        'import pathlib\ndef f(): pathlib.Path("f8_near_miss_controls.py").read_text()\n'
    ),
    "a different script": ('import runpy\ndef f(): runpy.run_path("f9_diagnostic_parity.py")\n'),
    "an unrelated exec": 'def f(): exec("print(1)", {})\n',
    "an unrelated subprocess": 'import subprocess\ndef f(): subprocess.run(["ls"])\n',
}


def test_f12_the_detector_finds_the_shapes_that_defeated_its_predecessor() -> None:
    """Pin the detector's own machinery, not just its verdict on this file.

    Verification found 15 of 20 execution shapes slipping past the previous
    revision. The fixes -- resolving names bound to string literals, and
    following import aliases -- are what closed them. Nothing in this file
    exercises either: `f9_diagnostic_parity.py` is named by a plain literal in a
    plain `subprocess.run`, so removing constant substitution entirely left the
    suite green. That is the mechanism-unpinned failure this lane keeps
    producing, so it is pinned here directly.

    This also makes a *targeted* blinding of the detector fail. Stubbing it to
    return `[]` only for `_MUTATING` passed every other assertion in this module.
    """
    assert len(_MUST_DETECT) >= 22 and len(_MUST_NOT_DETECT) >= 5, (
        "the corpora are the pin; emptying either left the suite green, so their "
        f"size is asserted: detect={len(_MUST_DETECT)} reject={len(_MUST_NOT_DETECT)}"
    )
    missed = [
        label
        for label, source in _MUST_DETECT.items()
        if not _executing_references_to(source, _MUTATING)
    ]
    assert not missed, f"the detector no longer finds: {missed}"

    flagged = [
        label
        for label, source in _MUST_NOT_DETECT.items()
        if _executing_references_to(source, _MUTATING)
    ]
    assert not flagged, f"the detector flags shapes that do not execute: {flagged}"


@pytest.mark.skipif(
    sys.dont_write_bytecode,
    reason=(
        "the negative control needs a .pyc to go stale; with bytecode writing "
        "disabled (PYTHONDONTWRITEBYTECODE=1, python -B, read-only or container "
        "filesystems) the hazard cannot reproduce and the test would fail for "
        "the wrong reason. Verification measured exactly that: 1 failed under "
        "each of the three."
    ),
)
def test_f12_a_stale_pyc_cannot_make_an_instrument_report_the_wrong_thing(
    tmp_path: pathlib.Path,
) -> None:
    """A control that measures a file it is not executing is worse than none.

    `exec_module` accepts a cached `.pyc` whose header `(mtime, size)` still
    matches the source, so an edit that keeps the byte length and lands in the
    same second runs STALE bytecode. Verification found this and I reproduced
    it: the loaded module executed the old code while the file on disk said
    something else. Inert in CI, which always has a fresh checkout -- live in
    the local edit-test loop, which is exactly where this lane runs its negative
    controls, and this lane has already been misled by a control reading a stale
    snapshot.

    Both halves are asserted. Without the invalidation the stale read is
    reproduced here, so this is a measurement rather than a claim; with it the
    module recompiles.
    """
    script = tmp_path / "instrument_under_test.py"
    script.write_text('MARKER = "OLD"\n')
    original = script.read_bytes()
    stat = script.stat()

    def mutate_in_place() -> None:
        """Same byte length, same mtime -- the shape the pyc header cannot see."""
        script.write_bytes(original.replace(b'"OLD"', b'"NEW"'))
        os.utime(script, (stat.st_atime, stat.st_mtime))
        assert script.stat().st_size == stat.st_size
        assert script.stat().st_mtime == stat.st_mtime

    # Negative control: the hazard is real, and reproduced right here.
    _load_from(script, invalidate=False)
    mutate_in_place()
    assert _load_from(script, invalidate=False).MARKER == "OLD", (
        "the stale-bytecode hazard did not reproduce, so the assertion below "
        "would pass for the wrong reason"
    )

    # The fix.
    script.write_bytes(original)
    os.utime(script, (stat.st_atime, stat.st_mtime))
    _load_from(script, invalidate=True)
    mutate_in_place()
    assert _load_from(script, invalidate=True).MARKER == "NEW"

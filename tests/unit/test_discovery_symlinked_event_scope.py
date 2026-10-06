"""M2 -- symlinked event-scope resolution is decided on physical identity.

The reserved routing scope ``.atlas-inbox/agent-events/<project>/<event>/``
is excluded from ``sources`` so package components are not double-counted as
documentation, and ``_discover_agent_events`` inventories the packages. Both
sides compared *nominal* paths. Reached through a symbolic link the two
disagreed, and which way depended on where the link sat:

* ``.atlas-inbox`` a link (direct, relative or multi-hop) to an in-root
  directory, or a project directory a link: the package appeared **both** as
  an ``agent_events`` row and as ``sources`` rows under its real path;
* ``agent-events`` itself a link: ``agent_events`` came back empty with no
  diagnostic and the content was captured as sources instead;
* an event directory a link to an in-root target loaded as a real package,
  although the loader's own contract refuses symlinked packages (its check
  runs on an already-resolved path, so it never fires).

Now: a symbolic link anywhere on the chain from the root to a package
directory is never followed by the inventory. Its physical content, if inside
the root, is inventoried exactly once as ordinary sources under its real path;
if outside, the walk reports the escape (R4-D) and nothing is inventoried.
Every refusal is reported naming the alias and its physical target, and a
symlinked event directory is recorded as an ``invalid`` row -- durable
evidence that reaches quarantine -- with the loader's own error wording.
The walk is unchanged: it already never follows a link, so once the inventory
stops following them the two sides agree by construction.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from _logcapture import DISCOVERY_LOGGER, capturing

import project_atlas.discovery as discovery_module
from project_atlas.discovery import discover

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="symlink creation needs a privilege on Windows"
)

COMPONENTS = ("event.md", "event.json", "provenance.json", "receipt.yaml")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("# readme\n", encoding="utf-8")
    return root


def _package(parent: Path, project: str, event: str) -> Path:
    """A structurally-shaped package: <project>/<event>/ with the four components."""
    package = parent / project / event
    package.mkdir(parents=True)
    for name in COMPONENTS:
        (package / name).write_text("{}\n", encoding="utf-8")
    return package


def _events(manifest: dict[str, object]) -> list[tuple[str, str, str]]:
    rows = manifest["agent_events"]
    assert isinstance(rows, list)
    return [(r["project_id"], r["event_id"], r["status"]) for r in rows]


def _paths(manifest: dict[str, object]) -> list[str]:
    rows = manifest["sources"]
    assert isinstance(rows, list)
    return sorted(str(r["path"]) for r in rows)


def _component_paths(prefix: str) -> list[str]:
    return sorted(f"{prefix}/{name}" for name in COMPONENTS)


def _scope_refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [m for m in caplog.messages if "symbolic link" in m and "agent-event" in m]


# --- in-root aliases: content inventoried once, as sources, with a diagnostic


@pytest.mark.parametrize(
    "shape",
    ["inbox-direct", "scope-direct", "scope-relative", "inbox-multi-hop"],
)
def test_aliased_scope_is_inventoried_exactly_once_as_sources(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, shape: str
) -> None:
    """No duplicate identity caused only by path aliasing, and no silent drop."""
    root = _root(tmp_path)
    real = root / "real-inbox"
    _package(real / "agent-events", "proj", "evt-1")
    if shape == "inbox-direct":
        (root / ".atlas-inbox").symlink_to(real, target_is_directory=True)
        alias, physical = ".atlas-inbox", real
    elif shape == "scope-direct":
        (root / ".atlas-inbox").mkdir()
        (root / ".atlas-inbox" / "agent-events").symlink_to(
            real / "agent-events", target_is_directory=True
        )
        alias, physical = ".atlas-inbox/agent-events", real / "agent-events"
    elif shape == "scope-relative":
        (root / ".atlas-inbox").mkdir()
        os.symlink("../real-inbox/agent-events", root / ".atlas-inbox" / "agent-events")
        alias, physical = ".atlas-inbox/agent-events", real / "agent-events"
    else:
        (root / "hop").symlink_to(real, target_is_directory=True)
        (root / ".atlas-inbox").symlink_to(root / "hop", target_is_directory=True)
        alias, physical = ".atlas-inbox", real  # the *final* hop is named

    with capturing(caplog, DISCOVERY_LOGGER):
        manifest = discover(root)

    assert _events(manifest) == [], "a scope reached through a link is not the reserved scope"
    expected = _component_paths("real-inbox/agent-events/proj/evt-1")
    assert _paths(manifest) == ["README.md", *expected]
    refusals = _scope_refusals(caplog)
    assert len(refusals) == 1, caplog.messages
    assert alias in refusals[0], "the alias is named"
    assert physical.resolve().as_posix() in refusals[0], "the physical target is named"


def test_intermediate_project_link_is_refused_and_content_kept_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A project directory that is a link: nothing beneath it is a package."""
    root = _root(tmp_path)
    holder = root / "holder"
    _package(holder, "x", "evt-1")
    scope = root / ".atlas-inbox" / "agent-events"
    scope.mkdir(parents=True)
    (scope / "proj").symlink_to(holder / "x", target_is_directory=True)
    _package(scope, "real-proj", "evt-2")

    with capturing(caplog, DISCOVERY_LOGGER):
        manifest = discover(root)

    assert _events(manifest) == [("real-proj", "evt-2", "pending")], "the real package still routes"
    assert _paths(manifest) == ["README.md", *_component_paths("holder/x/evt-1")]
    refusals = [m for m in caplog.messages if "packages beneath it not inventoried" in m]
    assert len(refusals) == 1, caplog.messages
    assert ".atlas-inbox/agent-events/proj" in refusals[0]
    assert (holder / "x").resolve().as_posix() in refusals[0]


@pytest.mark.parametrize("where", ["inside", "outside"])
def test_symlinked_event_directory_is_recorded_invalid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, where: str
) -> None:
    """An event directory that is a link is an invalid package, wherever it points.

    The loader's contract says so in as many words; its check just never
    fires because it inspects the resolved path. Recording the row keeps the
    refusal in the manifest, where ingestion quarantines it without loading.
    """
    root = _root(tmp_path)
    target_parent = root / "holder" if where == "inside" else tmp_path / "outside"
    target = _package(target_parent, "any", "target")
    scope = root / ".atlas-inbox" / "agent-events"
    (scope / "proj").mkdir(parents=True)
    (scope / "proj" / "evt-link").symlink_to(target, target_is_directory=True)

    with capturing(caplog, DISCOVERY_LOGGER):
        manifest = discover(root)

    rows = manifest["agent_events"]
    assert isinstance(rows, list)
    assert [(r["project_id"], r["event_id"], r["status"]) for r in rows] == [
        ("proj", "evt-link", "invalid")
    ]
    assert rows[0]["errors"] == ["event package directory is missing or symlinked"]
    assert rows[0]["component_sha256"] == {}, "no content identity for a refused package"
    assert rows[0]["package_path"] == ".atlas-inbox/agent-events/proj/evt-link"
    if where == "inside":
        assert _paths(manifest) == ["README.md", *_component_paths("holder/any/target")]
    else:
        assert _paths(manifest) == ["README.md"], "outside content is never inventoried"
        assert any("outside the source root" in m for m in caplog.messages), "R4-D still fires"
    assert any(
        "symlinked event package recorded as invalid" in m and "evt-link" in m
        for m in caplog.messages
    )


# --- out-of-scope resolution: refused, reported, never followed


@pytest.mark.parametrize("shape", ["inbox", "scope"])
def test_scope_escaping_the_root_is_refused_and_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, shape: str
) -> None:
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    _package(outside / "agent-events", "proj", "evt-1")
    if shape == "inbox":
        (root / ".atlas-inbox").symlink_to(outside, target_is_directory=True)
        alias = ".atlas-inbox"
        physical = outside
    else:
        (root / ".atlas-inbox").mkdir()
        (root / ".atlas-inbox" / "agent-events").symlink_to(
            outside / "agent-events", target_is_directory=True
        )
        alias = ".atlas-inbox/agent-events"
        physical = outside / "agent-events"

    with capturing(caplog, DISCOVERY_LOGGER):
        manifest = discover(root)

    assert _events(manifest) == [], "no package identity is fabricated for outside content"
    assert _paths(manifest) == ["README.md"]
    refusals = _scope_refusals(caplog)
    assert len(refusals) == 1 and alias in refusals[0], caplog.messages
    assert physical.resolve().as_posix() in refusals[0]
    # The walk's own escape diagnostic (R4-D) is independent and still fires.
    assert any("outside the source root" in m and alias in m for m in caplog.messages)


@pytest.mark.parametrize("shape", ["dangling", "file-target"])
@pytest.mark.parametrize("level", ["inbox", "scope"])
def test_unusable_scope_link_is_reported_not_silently_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, shape: str, level: str
) -> None:
    """A link at the reserved scope is named even when it leads nowhere usable.

    Asking `is_dir()` first answers False for a dangling or file-targeted
    link, so the scope was dropped as "no scope at all" with no diagnostic --
    the alias existed and went unmentioned. Testing the chain first is what
    makes it observable.
    """
    root = _root(tmp_path)
    if shape == "file-target":
        target = root / "not-a-directory.md"
        target.write_text("# not a scope\n", encoding="utf-8")
    else:
        target = root / "nowhere"
    if level == "inbox":
        alias = root / ".atlas-inbox"
    else:
        (root / ".atlas-inbox").mkdir()
        alias = root / ".atlas-inbox" / "agent-events"
    alias.symlink_to(target)

    with capturing(caplog, DISCOVERY_LOGGER):
        manifest = discover(root)

    assert _events(manifest) == []
    refusals = _scope_refusals(caplog)
    assert len(refusals) == 1, caplog.messages
    assert alias.relative_to(root).as_posix() in refusals[0]
    assert target.resolve().as_posix() in refusals[0], "the physical target is named"


def test_escaping_scope_link_is_refused_before_any_probe_follows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Order matters: the inventory refuses before anything stats through the link.

    `_reachable_is_dir` resolves, so calling it first performed a metadata
    probe on an out-of-root location before the refusal path ran.

    Scoped to the inventory deliberately. `discover()`'s own walk still asks
    `_reachable_is_dir(event_root)` to decide whether to exclude the reserved
    subtree, and that probe resolves through the alias exactly as it did on
    the base -- pre-existing, unchanged here, and outside this contract.
    """
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    _package(outside / "agent-events", "proj", "evt-1")
    (root / ".atlas-inbox").symlink_to(outside, target_is_directory=True)

    probed: list[str] = []
    real = discovery_module._reachable_is_dir

    def recording(path: Path) -> bool | None:
        probed.append(path.as_posix())
        return real(path)

    monkeypatch.setattr(discovery_module, "_reachable_is_dir", recording)
    with capturing(caplog, DISCOVERY_LOGGER):
        events = discovery_module._discover_agent_events(root)

    assert events == []
    assert probed == [], f"the inventory must not follow the escaping link: {probed}"
    assert len(_scope_refusals(caplog)) == 1


# --- the real scope is unchanged, including next to an alias of itself


def test_real_scope_with_an_alias_beside_it_routes_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Real path + alias path: the package routes once, the alias is quiet."""
    root = _root(tmp_path)
    scope = root / ".atlas-inbox" / "agent-events"
    _package(scope, "proj", "evt-1")
    (root / "mirror").symlink_to(root / ".atlas-inbox", target_is_directory=True)

    with capturing(caplog, DISCOVERY_LOGGER):
        manifest = discover(root)

    assert _events(manifest) == [("proj", "evt-1", "pending")]
    assert _paths(manifest) == ["README.md"], "components are neither sources nor duplicated"
    assert _scope_refusals(caplog) == []
    assert not any("outside the source root" in m for m in caplog.messages)


def test_aliased_scope_manifest_is_deterministic(tmp_path: Path) -> None:
    """The refusal path must not introduce run-to-run drift (NFR-001)."""
    root = _root(tmp_path)
    real = root / "real-inbox"
    _package(real / "agent-events", "proj", "evt-1")
    (root / ".atlas-inbox").symlink_to(real, target_is_directory=True)

    first = json.dumps(discover(root), sort_keys=True)
    second = json.dumps(discover(root), sort_keys=True)
    assert first == second

"""Linux filesystem semantics across the discover -> ingest boundary.

Linux permits filenames the shared portable path contract
(``atlas_contracts.paths``, CODEX-SEC-004/014/017/018) refuses on every
platform -- colons, control characters, Windows reserved basenames -- and
treats a backslash as an ordinary filename character rather than a separator.
It also routinely exposes files whose content the caller cannot read.

None of these may abort a run. Before this suite, ``discover`` emitted such
paths happily and ``ingest`` then failed the whole run closed on them, so a
single legally-named file made the pipeline permanently unrunnable on Linux;
an unreadable file aborted ``discover`` outright. They are now recorded as
excluded evidence -- never silently dropped, never ingested.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
from pathlib import Path

import pytest

from project_atlas.cli import EXIT_OK, main
from project_atlas.discovery import discover
from project_atlas.ingestion import ingest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX-only filenames and permission modes")

# `pytestmark` skips execution, not import: a decorator argument is still
# evaluated at collection time on every platform, and `os.geteuid` does not
# exist on Windows. Resolve it here, guarded, rather than inside the marker.
_IS_ROOT = os.name != "nt" and os.geteuid() == 0

#: Legal on Linux, unrepresentable under the portable path contract.
NON_PORTABLE_NAMES = (
    "co:lon.md",  # Windows drive / alternate-data-stream separator
    "new\nline.md",  # control character
    "back\\slash.md",  # separator under the contract, a name character here
    "aux.md",  # Windows reserved device basename
)


def _write_source(root: Path) -> Path:
    """Build a source tree exercising Linux-only filesystem behaviour."""
    docs = root / "docs"
    docs.mkdir(parents=True)
    (root / ".atlas-project.yaml").write_text(
        "schema_version: 1\nproject:\n  id: fs-portability\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Readme\n", encoding="utf-8")
    for name in NON_PORTABLE_NAMES:
        (docs / name).write_text(f"# {name}\n", encoding="utf-8")
    return root


def _discover(tmp_path: Path, source: Path) -> dict[str, object]:
    manifest = tmp_path / "manifest.json"
    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    return json.loads(manifest.read_text(encoding="utf-8"))


def _by_path(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    sources = manifest["sources"]
    assert isinstance(sources, list)
    return {str(record["path"]): record for record in sources}


def test_non_portable_names_are_excluded_evidence_not_fatal(tmp_path: Path) -> None:
    """Each unrepresentable name is recorded as excluded, with a reason."""
    records = _by_path(_discover(tmp_path, _write_source(tmp_path / "source")))

    for name in NON_PORTABLE_NAMES:
        record = records[f"docs/{name}"]
        assert record["classification_state"] == "excluded", name
        assert record["exclusion_reason"] == "non-portable-path", name

    # Ordinary names are untouched by the guard.
    assert records["README.md"]["exclusion_reason"] is None
    assert records["README.md"]["classification_state"] != "excluded"


def test_pipeline_completes_with_non_portable_names(tmp_path: Path) -> None:
    """The full pipeline runs green; only representable sources are ingested."""
    source = _write_source(tmp_path / "source")
    manifest = tmp_path / "manifest.json"
    vault = tmp_path / "vault"

    assert main(["discover", "--source", str(source), "--output", str(manifest)]) == EXIT_OK
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    assert (
        main(
            [
                "ingest",
                "--manifest",
                str(manifest),
                "--vault",
                str(vault),
                "--source",
                str(source),
            ]
        )
        == EXIT_OK
    )
    assert main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
    assert main(["validate", "--vault", str(vault)]) == EXIT_OK

    ingested = {
        path.read_text(encoding="utf-8")
        for path in (vault / "sources" / "imported-documents").rglob("*.md")
    }
    for name in NON_PORTABLE_NAMES:
        assert not any(f"# {name}" in body for body in ingested), name


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_unreadable_file_is_recorded_not_fatal(tmp_path: Path) -> None:
    """An unreadable file yields real metadata, no digest, and no abort."""
    source = _write_source(tmp_path / "source")
    unreadable = source / "docs" / "unreadable.md"
    unreadable.write_text("# unreadable\n", encoding="utf-8")
    size = unreadable.stat().st_size
    unreadable.chmod(0o000)
    try:
        record = _by_path(_discover(tmp_path, source))["docs/unreadable.md"]
    finally:
        # Leave the tree removable for tmp_path teardown.
        unreadable.chmod(0o644)

    assert record["exclusion_reason"] == "unreadable"
    assert record["classification_state"] == "excluded"
    assert record["sha256"] is None, "content was never read, so it must not be claimed"
    assert record["size_bytes"] == size, "stat metadata is real, not invented"


def test_case_variant_filenames_stay_distinct(tmp_path: Path) -> None:
    """Linux is case-sensitive: variants are separate sources, never merged."""
    source = _write_source(tmp_path / "source")
    (source / "readme.md").write_text("# lower\n", encoding="utf-8")
    (source / "ReadMe.md").write_text("# mixed\n", encoding="utf-8")

    records = _by_path(_discover(tmp_path, source))

    variants = {path for path in records if path.lower() == "readme.md"}
    assert variants == {"README.md", "readme.md", "ReadMe.md"}
    ids = {str(records[path]["source_id"]) for path in variants}
    assert len(ids) == 3, "case variants must not collapse onto one source identity"


def test_symlinks_and_special_files_are_never_sources(tmp_path: Path) -> None:
    """Only regular files are evidence: no symlink, FIFO or device follow."""
    source = _write_source(tmp_path / "source")
    docs = source / "docs"
    (docs / "link-to-readme.md").symlink_to(source / "README.md")
    (docs / "link-outside.md").symlink_to(tmp_path / "outside.md")
    (docs / "dir-loop").symlink_to(source)
    os.mkfifo(docs / "fifo.md")

    records = _by_path(_discover(tmp_path, source))

    for name in ("link-to-readme.md", "link-outside.md", "fifo.md"):
        assert f"docs/{name}" not in records, name
    assert not any(path.startswith("docs/dir-loop") for path in records)


def test_symlinked_source_root_is_refused_with_the_physical_path(tmp_path: Path) -> None:
    """The authorized root stays symlink-free, but says which path to use.

    Refusing a symlinked root is deliberate (CODEX-SEC-001 / SEC-SCAN-A-014:
    resolve() would dereference it and defeat a later check). Symlinked
    project roots are ordinary on Linux, so the refusal must name the
    physical path rather than leave an existing directory unexplained.
    """
    source = _write_source(tmp_path / "source")
    link = tmp_path / "link-source"
    link.symlink_to(source)
    manifest = tmp_path / "manifest.json"
    vault = tmp_path / "vault"
    assert main(["discover", "--source", str(link), "--output", str(manifest)]) == EXIT_OK
    assert main(["init", "--output", str(vault)]) == EXIT_OK

    with pytest.raises(ValueError) as excinfo:
        ingest(manifest, vault, authorized_source_root=link)

    message = str(excinfo.value)
    assert "non-symlink directory" in message
    assert str(source) in message, "the physical path must be named"

    # The named physical path is genuinely accepted.
    assert ingest(manifest, vault, authorized_source_root=source)["documents_ingested"] >= 1


def test_non_utf8_filename_is_reported_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A filename that is not valid UTF-8 is skipped loudly, not fatally.

    Linux filenames are byte strings. Such a name decodes to lone surrogates
    and cannot be encoded into the UTF-8 JSON manifest, which previously
    aborted the entire run with a bare codec error.
    """
    source = _write_source(tmp_path / "source")
    raw_name = os.path.join(os.fsencode(source / "docs"), b"bad-\xff-name.md")
    with open(raw_name, "wb") as handle:
        handle.write(b"# undecodable name\n")

    with caplog.at_level("WARNING"):
        records = _by_path(_discover(tmp_path, source))

    assert "README.md" in records, "the rest of the tree still discovers"
    assert not any("bad-" in path for path in records), "never recorded as a source claim"
    assert any("undecodable filename" in message for message in caplog.messages), (
        "the skip must be reported, not silent"
    )


def test_canonical_normalization_collision_is_reported_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """NFC and NFD spellings of one name are two files on Linux, one identity.

    `canonicalize_project_path` NFC-normalizes deliberately, so identity is
    host-independent (AS-ID-001). Both spellings therefore claim one
    `source_id`, which used to abort the whole ingest at the CODEX-SEC-002
    duplicate-identity guard. The first in deterministic order keeps the
    identity; the collider is reported and never recorded.
    """
    source = _write_source(tmp_path / "source")
    raw = os.fsencode(source)
    with open(os.path.join(raw, b"caf\xc3\xa9.md"), "wb") as handle:  # NFC
        handle.write(b"# nfc\n")
    with open(os.path.join(raw, b"cafe\xcc\x81.md"), "wb") as handle:  # NFD
        handle.write(b"# nfd\n")

    with caplog.at_level("WARNING"):
        records = _by_path(_discover(tmp_path, source))

    cafes = [path for path in records if "caf" in path]
    assert len(cafes) == 1, f"exactly one spelling may hold the identity, got {cafes}"
    # Which spelling wins is part of the contract, not an accident: the first
    # in deterministic sort order. NFD ("cafe\u0301.md") sorts before NFC
    # ("caf\xe9.md") because 'e' precedes U+00E9.
    assert cafes == ["cafe\u0301.md"], f"unexpected collision winner: {cafes}"
    assert any("canonical-path collision" in message for message in caplog.messages)

    # The pipeline must now complete rather than abort at ingest.
    vault = tmp_path / "vault"
    assert main(["init", "--output", str(vault)]) == EXIT_OK
    assert (
        main(
            [
                "ingest",
                "--manifest",
                str(tmp_path / "manifest.json"),
                "--vault",
                str(vault),
                "--source",
                str(source),
            ]
        )
        == EXIT_OK
    )


def test_backslash_name_colliding_with_real_path_is_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A literal backslash name and the real nested path share one canonical form."""
    source = _write_source(tmp_path / "source")
    (source / "docs" / "slash.md").write_text("# real\n", encoding="utf-8")
    (source / "docs\\slash.md").write_text("# literal\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        records = _by_path(_discover(tmp_path, source))

    assert "docs/slash.md" in records
    assert "docs\\slash.md" not in records
    assert any("canonical-path collision" in message for message in caplog.messages)


@pytest.mark.skipif(_IS_ROOT, reason="root traverses regardless of mode bits")
def test_untraversable_directory_does_not_abort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A listable-but-not-traversable directory (0444) must not kill the run.

    This reaches `path.is_file()` -- the loop's *first* metadata access --
    not the later `stat()`, which is why the earlier guard missed it.
    """
    source = _write_source(tmp_path / "source")
    locked = source / "locked"
    locked.mkdir()
    (locked / "a.md").write_text("# locked\n", encoding="utf-8")
    locked.chmod(0o444)
    try:
        if os.access(locked / "a.md", os.R_OK):
            pytest.skip("filesystem does not enforce the missing execute bit")
        with caplog.at_level("WARNING"):
            records = _by_path(_discover(tmp_path, source))
    finally:
        locked.chmod(0o755)

    assert "README.md" in records, "the rest of the tree still discovers"
    assert "locked/a.md" not in records
    assert any("skipped unreadable path" in message for message in caplog.messages)


@pytest.mark.skipif(_IS_ROOT, reason="root reads regardless of mode bits")
def test_unreadable_directory_is_reported_not_silently_lost(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unreadable directory's existence must survive as observable evidence.

    `rglob` swallows the failure, so without an explicit probe the entire
    subtree vanished from the inventory with no record and no diagnostic --
    silent evidence loss, which the evidence contract forbids.
    """
    source = _write_source(tmp_path / "source")
    dark = source / "dark"
    dark.mkdir()
    (dark / "b.md").write_text("# dark\n", encoding="utf-8")
    dark.chmod(0o000)
    try:
        if os.access(dark, os.R_OK):
            pytest.skip("filesystem does not enforce directory mode bits")
        with caplog.at_level("WARNING"):
            records = _by_path(_discover(tmp_path, source))
    finally:
        dark.chmod(0o755)

    assert "dark/b.md" not in records, "contents were never read, so never claimed"
    assert any("inaccessible discovery scope" in message for message in caplog.messages), (
        "the lost scope must be observable, not silent"
    )


def test_inventory_is_deterministic_across_creation_order(tmp_path: Path) -> None:
    """Identical trees must inventory identically regardless of dirent order.

    The ordering key case-folds, so case variants tie; a stable sort then
    inherited directory-entry order and the inventory hash depended on the
    order files happened to be created in -- an NFR-001 determinism defect,
    and the thing "first in deterministic order wins" silently rested on.
    """
    fixed = 1_700_000_000

    def inventory(root: Path, order: list[str]) -> tuple[str, list[str]]:
        root.mkdir(parents=True)
        (root / ".atlas-project.yaml").write_text(
            "schema_version: 1\nproject:\n  id: determinism\n", encoding="utf-8"
        )
        for name in order:
            (root / name).write_text("# same content\n", encoding="utf-8")
        for entry in [*root.rglob("*"), root]:
            os.utime(entry, (fixed, fixed))
        manifest = discover(root)
        return manifest["inventory_sha256"], [s["path"] for s in manifest["sources"]]

    # Same root path for both runs: source_id is root-fingerprinted, so a
    # differing root would mask the ordering question being asked here.
    root = tmp_path / "tree"
    first = inventory(root, ["README.md", "readme.md", "ReadMe.md"])
    shutil.rmtree(root)
    second = inventory(root, ["ReadMe.md", "readme.md", "README.md"])

    assert first[1] == second[1], "manifest order must not follow creation order"
    assert first[0] == second[0], "inventory_sha256 must not follow creation order"


def _valid_package(inbox: Path, project_id: str, event_id: str) -> Path:
    """A structurally-shaped event package: <project-id>/<event-id>/ (AS-INT-001)."""
    package = inbox / project_id / event_id
    package.mkdir(parents=True)
    for component in ("event.md", "event.json", "provenance.json", "receipt.yaml"):
        (package / component).write_text("{}\n", encoding="utf-8")
    return package


def test_unexpected_document_in_reserved_scope_is_not_silently_lost(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R4-A: a real document in the agent-event inbox reaches neither inventory.

    `.atlas-inbox/agent-events/` is reserved routing scope: only
    `<project-id>/<event-id>/` package directories are valid there (AS-INT-001),
    and `discover()` excludes the whole subtree from `sources` so package
    components are not double-counted as documentation. A loose file therefore
    lands in neither `sources` nor `agent_events` -- previously without any
    diagnostic at all, so a real document disappeared from observable output.
    """
    source = _write_source(tmp_path / "source")
    inbox = source / ".atlas-inbox" / "agent-events"
    inbox.mkdir(parents=True)
    (inbox / "loose.md").write_text("# a real document\n", encoding="utf-8")
    _valid_package(inbox, "proj-a", "evt-1")
    (inbox / "proj-a" / "stray.md").write_text("# stray\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        manifest = _discover(tmp_path, source)

    records = _by_path(manifest)
    assert not any(".atlas-inbox" in path for path in records), (
        "reserved scope stays out of sources"
    )

    warned = [m for m in caplog.messages if "reserved agent-event scope" in m]
    assert any("agent-events/loose.md" in m for m in warned), "top-level drop must be observable"
    assert any("proj-a/stray.md" in m for m in warned), "package-level drop must be observable"

    # No fabricated routed evidence: the loose files are not agent events.
    events = manifest["agent_events"]
    assert isinstance(events, list)
    assert [(e["project_id"], e["event_id"]) for e in events] == [("proj-a", "evt-1")]


def test_valid_agent_event_package_still_routes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R4-B: the diagnostic must not disturb valid package routing."""
    source = _write_source(tmp_path / "source")
    inbox = source / ".atlas-inbox" / "agent-events"
    inbox.mkdir(parents=True)
    _valid_package(inbox, "proj-a", "evt-1")
    _valid_package(inbox, "proj-b", "evt-2")

    with caplog.at_level("WARNING"):
        events = _discover(tmp_path, source)["agent_events"]

    assert isinstance(events, list)
    assert sorted((e["project_id"], e["event_id"]) for e in events) == [
        ("proj-a", "evt-1"),
        ("proj-b", "evt-2"),
    ]
    # The diagnostic must never fire for a well-formed package directory.
    assert not [m for m in caplog.messages if "reserved agent-event scope" in m]
    # Scope note: this exercises package *layout* routing, not envelope
    # verification -- these components are structurally present but not
    # cryptographically valid, so status stays "pending".
    # tests/integration/test_agent_event_ingestion.py covers fully-verified
    # packages end to end, and this change cannot affect envelope validation.


def test_ordinary_sources_outside_reserved_scope_are_unchanged(tmp_path: Path) -> None:
    """R4-C: documents outside the inbox keep behaving exactly as before."""
    source = _write_source(tmp_path / "source")
    (source / "docs" / "ordinary.md").write_text("# ordinary\n", encoding="utf-8")
    inbox = source / ".atlas-inbox" / "agent-events"
    inbox.mkdir(parents=True)
    (inbox / "loose.md").write_text("# reserved\n", encoding="utf-8")

    records = _by_path(_discover(tmp_path, source))

    assert records["docs/ordinary.md"]["exclusion_reason"] is None
    assert records["README.md"]["exclusion_reason"] is None


def test_reserved_scope_diagnostic_keeps_inventory_deterministic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R4-E: the new diagnostic must not reintroduce order dependence."""
    fixed = 1_700_000_000

    def inventory(root: Path, order: list[str]) -> tuple[str, list[str], list[str]]:
        inbox = root / ".atlas-inbox" / "agent-events"
        inbox.mkdir(parents=True)
        (root / ".atlas-project.yaml").write_text(
            "schema_version: 1\nproject:\n  id: reserved\n", encoding="utf-8"
        )
        (root / "README.md").write_text("# root\n", encoding="utf-8")
        for name in order:
            (inbox / name).write_text("# loose\n", encoding="utf-8")
        _valid_package(inbox, "proj-a", "evt-1")
        for entry in [*root.rglob("*"), root]:
            os.utime(entry, (fixed, fixed))
        with caplog.at_level("WARNING"):
            caplog.clear()
            manifest = discover(root)
            warnings = [m for m in caplog.messages if "reserved agent-event scope" in m]
        return (
            manifest["inventory_sha256"],
            [s["path"] for s in manifest["sources"]],
            warnings,
        )

    root = tmp_path / "tree"
    first = inventory(root, ["a.md", "b.md", "c.md"])
    shutil.rmtree(root)
    second = inventory(root, ["c.md", "b.md", "a.md"])

    assert first[1] == second[1]
    assert first[0] == second[0], "reserved-scope diagnostics must not perturb the inventory"
    assert first[2] == second[2], "diagnostic order must not follow creation order"


def test_symlink_escaping_the_root_is_reported_not_silently_lost(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R4-D: a real document reachable only through an escaping symlink.

    A symlink is never evidence itself -- following one would duplicate a
    document already inventoried under its real path, or escape the source
    root. That reasoning holds only when the target is *inside* the root.
    When it resolves outside, the content is real, readable and reachable at
    a path under the root, yet `rglob` neither yields it (a file symlink is
    non-regular) nor descends it (a directory symlink is not followed) -- so
    a document, or an entire subtree behind a directory symlink, vanished
    with no record and no diagnostic.
    """
    outside = tmp_path / "outside"
    (outside / "buried").mkdir(parents=True)
    (outside / "handbook.md").write_text("# real, readable, outside\n", encoding="utf-8")
    (outside / "buried" / "deep.md").write_text("# behind a dir symlink\n", encoding="utf-8")

    source = _write_source(tmp_path / "source")
    (source / "escape.md").symlink_to(outside / "handbook.md")
    (source / "mirror").symlink_to(outside / "buried")

    with caplog.at_level("WARNING"):
        records = _by_path(_discover(tmp_path, source))

    # Still excluded -- following the link would escape the approved root.
    assert "escape.md" not in records
    assert not any(path.startswith("mirror") for path in records)

    escaped = [m for m in caplog.messages if "outside the source root" in m]
    assert any("escape.md" in m for m in escaped), "escaping file symlink must be observable"
    assert any("mirror" in m for m in escaped), "escaping directory symlink must be observable"
    # Naming the physical target is half the point: without it the operator
    # knows something was skipped but not what.
    assert any(str(outside / "handbook.md") in m for m in escaped), "target must be named"
    assert any(str(outside / "buried") in m for m in escaped), "target must be named"


def test_non_escaping_symlinks_stay_quiet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """The escape diagnostic must not fire where nothing is actually lost.

    An in-root target is already inventoried under its own real path, a
    broken link has no document behind it, and a FIFO is not a document.
    Warning on those would be noise, not evidence.
    """
    source = _write_source(tmp_path / "source")
    (source / "docs" / "target.md").write_text("# in tree\n", encoding="utf-8")
    (source / "dup.md").symlink_to(source / "docs" / "target.md")
    (source / "mirror_in").symlink_to(source / "docs")
    (source / "broken.md").symlink_to(source / "nowhere.md")
    os.mkfifo(source / "pipe.md")

    with caplog.at_level("WARNING"):
        records = _by_path(_discover(tmp_path, source))

    assert "docs/target.md" in records, "the real document is inventoried under its own path"
    assert not [m for m in caplog.messages if "outside the source root" in m]


def test_diagnostics_cannot_forge_log_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A control character in a reported path must not split the diagnostic.

    A newline in any reported path would end the warning early and forge what
    reads as a second log record. The escaping-symlink diagnostic made this
    obvious by reporting an unconstrained path from outside the root, but an
    in-root name reaches a log too: the undecodable-filename branch logs
    before portability is evaluated, and a `non-portable-path` record is
    still reported if it later collides canonically. Both are covered here.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    hostile = os.path.join(os.fsencode(outside), b"evil\nWARNING forged line.md")
    with open(hostile, "wb") as handle:
        handle.write(b"# external\n")

    source = _write_source(tmp_path / "source")
    os.symlink(hostile, os.path.join(os.fsencode(source), b"link.md"))

    with caplog.at_level("WARNING"):
        _discover(tmp_path, source)

    escaped = [m for m in caplog.messages if "outside the source root" in m]
    assert escaped, "the escaping symlink must still be reported"
    assert all("\n" not in message for message in escaped), "no diagnostic may span lines"
    assert any("\\x0a" in message for message in escaped), "the newline must be escaped"


def test_in_root_control_character_paths_cannot_forge_log_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The forged-line risk is not confined to escaping symlinks.

    An in-root name carrying a newline reaches a diagnostic by two routes:
    the undecodable-filename branch logs before portability is evaluated, and
    a record excluded as `non-portable-path` is still reported when it later
    collides canonically.
    """
    source = _write_source(tmp_path / "source")
    docs = os.fsencode(source / "docs")
    with open(os.path.join(docs, b"ev\x0ail-caf\xc3\xa9.md"), "wb") as handle:
        handle.write(b"# nfc\n")
    with open(os.path.join(docs, b"ev\x0ail-cafe\xcc\x81.md"), "wb") as handle:
        handle.write(b"# nfd\n")
    with open(os.path.join(docs, b"ev\x0ail-\xff-byte.md"), "wb") as handle:
        handle.write(b"# undecodable\n")

    with caplog.at_level("WARNING"):
        _discover(tmp_path, source)

    assert caplog.messages, "these inputs must still be reported"
    assert all("\n" not in message for message in caplog.messages), "no diagnostic may span lines"

    # Pin each route separately: an aggregate assertion is satisfied by
    # either one alone, so it would not notice if a route stopped firing.
    undecodable = [m for m in caplog.messages if "undecodable filename" in m]
    collision = [m for m in caplog.messages if "canonical-path collision" in m]
    assert undecodable, "the undecodable-filename route must fire"
    assert collision, "the canonical-collision route must fire"
    assert all("\\x0a" in m for m in undecodable), "escaped in the undecodable route"
    assert all("\\x0a" in m for m in collision), "escaped in the collision route"


def _inbox_with(root: Path, *projects: str) -> Path:
    """An agent-event inbox holding one valid-shaped package per project."""
    inbox = root / ".atlas-inbox" / "agent-events"
    inbox.mkdir(parents=True, exist_ok=True)
    for index, project in enumerate(projects, start=1):
        _valid_package(inbox, project, f"evt-{index}")
    return inbox


@pytest.mark.skipif(_IS_ROOT, reason="root reads regardless of mode bits")
def test_unreadable_agent_event_project_dir_does_not_abort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R5-A: an unreadable project directory must not kill the run.

    `_discover_agent_events` iterated the inbox with no error handling, so one
    mode-000 directory aborted the whole of `discover()` with `PermissionError`
    -- the exact failure class the main walk had already been taught to
    survive.
    """
    source = _write_source(tmp_path / "source")
    inbox = _inbox_with(source, "proj-a", "proj-ok")
    locked = inbox / "proj-a"
    locked.chmod(0o000)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("filesystem does not enforce directory mode bits")
        with caplog.at_level("WARNING"):
            manifest = _discover(tmp_path, source)
    finally:
        locked.chmod(0o755)

    events = manifest["agent_events"]
    assert isinstance(events, list)
    # R5-E: the accessible project is still inventoried, not lost with it.
    # `_inbox_with` numbers packages across projects, so proj-ok holds evt-2.
    assert [(e["project_id"], e["event_id"]) for e in events] == [("proj-ok", "evt-2")]
    assert not any(e["project_id"] == "proj-a" for e in events), "no fabricated package"
    assert "README.md" in _by_path(manifest), "ordinary sources still inventoried"
    assert any("inaccessible discovery scope" in m for m in caplog.messages)


@pytest.mark.skipif(_IS_ROOT, reason="root reads regardless of mode bits")
def test_unreadable_agent_event_root_does_not_abort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R5-B: an unreadable inbox root degrades to "no events", not a crash."""
    source = _write_source(tmp_path / "source")
    inbox = _inbox_with(source, "proj-a")
    inbox.chmod(0o000)
    try:
        if os.access(inbox, os.R_OK):
            pytest.skip("filesystem does not enforce directory mode bits")
        with caplog.at_level("WARNING"):
            manifest = _discover(tmp_path, source)
    finally:
        inbox.chmod(0o755)

    assert manifest["agent_events"] == []
    assert "README.md" in _by_path(manifest)
    assert any("inaccessible discovery scope" in m for m in caplog.messages)


@pytest.mark.skipif(_IS_ROOT, reason="root reads regardless of mode bits")
def test_unreadable_event_package_dir_does_not_abort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R5-C: this one raises inside `atlas_contracts`, so the call is guarded.

    No inventory row is appended for the unreadable package: a partially-read
    package would be fabricated evidence.
    """
    source = _write_source(tmp_path / "source")
    inbox = _inbox_with(source, "proj-a", "proj-ok")
    package = inbox / "proj-a" / "evt-1"
    package.chmod(0o000)
    try:
        if os.access(package, os.R_OK):
            pytest.skip("filesystem does not enforce directory mode bits")
        with caplog.at_level("WARNING"):
            manifest = _discover(tmp_path, source)
    finally:
        package.chmod(0o755)

    events = manifest["agent_events"]
    assert isinstance(events, list)
    assert [(e["project_id"], e["event_id"]) for e in events] == [("proj-ok", "evt-2")]
    assert any("inaccessible discovery scope" in m for m in caplog.messages)


@pytest.mark.skipif(_IS_ROOT, reason="root reads regardless of mode bits")
def test_event_inventory_reports_unreadable_scope_without_relying_on_the_walk(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The inventory must report for itself, not inherit the walk's diagnostic.

    An earlier version of this fix emitted nothing here, on the theory that
    `discover()`'s walk always reports the same path first. That is unsound:
    with the inbox at mode 0111 the walk cannot *list* it, so it reports only
    `.atlas-inbox` and never reaches the descendants, while this inventory
    needs only `+x` and traverses straight past -- dropping an unreadable
    child with no diagnostic naming it at all. That was silent loss
    introduced by the very change meant to prevent aborts.
    """
    source = _write_source(tmp_path / "source")
    inbox = _inbox_with(source, "locked", "ok")
    (inbox / "locked").chmod(0o000)
    (source / ".atlas-inbox").chmod(0o111)  # traversable, NOT listable
    try:
        if os.access(source / ".atlas-inbox", os.R_OK):
            pytest.skip("filesystem does not enforce directory mode bits")
        with caplog.at_level("WARNING"):
            manifest = _discover(tmp_path, source)
    finally:
        (source / ".atlas-inbox").chmod(0o755)
        (inbox / "locked").chmod(0o755)

    events = manifest["agent_events"]
    assert isinstance(events, list)
    assert [(e["project_id"], e["event_id"]) for e in events] == [("ok", "evt-2")]
    assert any("agent-event scope not readable" in m and "locked" in m for m in caplog.messages), (
        "the inventory must name the scope it could not read"
    )


@pytest.mark.skipif(_IS_ROOT, reason="root reads regardless of mode bits")
@pytest.mark.parametrize(
    "target",
    ["inbox_parent", "agent_events_root", "project_dir"],
    ids=["inbox-parent-0444", "agent-events-0444", "project-dir-0444"],
)
def test_listable_but_untraversable_event_scopes_do_not_abort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, target: str
) -> None:
    """Mode 0444 is readable but not traversable, so `is_dir()` itself raises.

    `pathlib` swallows only ENOENT/ENOTDIR/EBADF/ELOOP, so guarding just
    `iterdir()` left every 0444 scope still aborting the whole run.
    """
    source = _write_source(tmp_path / "source")
    inbox = _inbox_with(source, "proj-a")
    locked = {
        "inbox_parent": source / ".atlas-inbox",
        "agent_events_root": inbox,
        "project_dir": inbox / "proj-a",
    }[target]
    locked.chmod(0o444)
    try:
        if os.access(locked / "probe", os.F_OK):
            pytest.skip("filesystem does not enforce the missing execute bit")
        with caplog.at_level("WARNING"):
            manifest = _discover(tmp_path, source)
    finally:
        locked.chmod(0o755)

    assert "README.md" in _by_path(manifest), "the run completes"
    assert any("agent-event scope not readable" in m for m in caplog.messages)


def test_unexpected_oserror_in_event_inventory_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-F: only unreadable-scope errnos are absorbed; faults stay visible.

    The guard must not become "catch everything and continue" -- an EIO is a
    genuine failure, not a scope this run may skip.
    """
    source = _write_source(tmp_path / "source")
    _inbox_with(source, "proj-a")

    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path):  # type: ignore[no-untyped-def]
        if self.name == "agent-events":
            raise OSError(errno.EIO, "simulated I/O error")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    with pytest.raises(OSError) as excinfo:
        discover(source)
    assert excinfo.value.errno == errno.EIO, "an unexpected fault must not be swallowed"

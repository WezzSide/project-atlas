"""AS-OBSIDIAN-CAPTURE-001-F6 — an unreadable note is an operator condition.

Both projection writers read the prior note in order to splice a fresh
generated span into it. When that read fails -- the file is not valid UTF-8, or
cannot be opened -- the raw ``UnicodeDecodeError`` or ``PermissionError``
escaped the module's own error boundary, so a caller catching
``GraphProjectionError`` or ``ObsidianProjectionError`` did not catch these at
all. (The two ``PermissionError`` cases already carried the path via
``OSError.filename``; the escape is what was wrong in all four.)

This is a *consistency* defect rather than a design question, because one writer
in the same lane already does it correctly: ``obsidian_capture_note.write_note``
catches ``(OSError, UnicodeError)`` and raises ``OBSIDIAN_NOTE_CONFLICT``. The
two projection writers did not.

Worth recording why nothing caught it: #707's independent verification fuzzed
60,000 malformed input pairs through the graph adapter and reported **0 raw
leaks**. That is not contradicted here -- its corpus was *valid UTF-8* with
malformed markers, so invalid bytes and unreadable files were never in the
space. The boundary had a hole the instrument could not reach.

Truth boundary: this is availability and diagnostics, **not** data loss. For
``graph_projections`` the read happens while building the write plan, before
``_promote``, so a failure aborts before anything is written and the "failed
promote leaves prior bytes intact" contract already held. That is **not** true
of ``obsidian_projection``, which calls ``_write_atomic`` inside its per-project
loop: in a multi-project vault an earlier note that merged cleanly has already
been rewritten when a later one fails. Pre-existing, proven on base, and
disclosed in the evidence receipt. F6 does not change what is written; it
changes what the operator is told when nothing can be.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from project_atlas.connect import connect_project
from project_atlas.graph_projections import (
    GraphProjectionError,
    materialize_projections,
    write_projection_outputs,
)
from project_atlas.obsidian_projection import (
    ObsidianProjectionError,
    materialize_obsidian_projection,
    project_note_path,
)

# Bytes that are not valid UTF-8: a lone 0xFF, and a truncated 2-byte sequence.
UNDECODABLE = b"\xff\xfe not utf-8 \xc3\x28\n"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def graph_vault(tmp_path: Path) -> tuple[Path, Path, object]:
    vault = tmp_path / "vault"
    vault.mkdir()
    bundle = materialize_projections(project_id="demo", relationships=(), health=None)
    write_projection_outputs(bundle, vault=vault)
    note = vault / "generated/graph/projections/demo/relationships.md"
    assert note.is_file()
    return vault, note, bundle


@pytest.fixture
def projection_vault(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "src-project"
    root.mkdir()
    (root / "README.md").write_text("# F6\n\nbody.\n", encoding="utf-8")
    connected = connect_project(root)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    return vault, project_note_path(vault, project_id), project_id


# ---------------------------------------------------------------------------
# graph_projections -- the domain type is asserted, not merely "raises".
# ---------------------------------------------------------------------------


def test_f6_graph_undecodable_note_raises_domain_error(graph_vault) -> None:
    vault, note, bundle = graph_vault
    note.write_bytes(UNDECODABLE)
    with pytest.raises(GraphProjectionError) as caught:
        write_projection_outputs(bundle, vault=vault)
    assert "relationships.md" in str(caught.value), "the message must name the note"


def test_f6_graph_unreadable_note_raises_domain_error(
    graph_vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read raises OSError -- injected, not provoked via the filesystem.

    An earlier revision used ``chmod(0o000)``. That is not portable: on Windows
    ``chmod`` only clears the read-only bit, so the read still succeeds and the
    test failed there with ``DID NOT RAISE``; it also passes vacuously as root.
    Independent verification caught it red on the Windows CI job.

    Injecting the failure is strictly better than skipping the test on Windows:
    it exercises the boundary on every runner and every uid, and it tests what
    this package actually claims -- that a failing read becomes a domain error
    -- rather than testing the operating system's permission semantics.
    """
    vault, _note, bundle = graph_vault
    real = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self.name == "relationships.md":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(GraphProjectionError) as caught:
        write_projection_outputs(bundle, vault=vault)
    assert "relationships.md" in str(caught.value), "the message must name the note"
    assert isinstance(caught.value.__cause__, PermissionError)


# ---------------------------------------------------------------------------
# obsidian_projection.
# ---------------------------------------------------------------------------


def test_f6_projection_undecodable_note_raises_domain_error(projection_vault) -> None:
    vault, note, project_id = projection_vault
    note.write_bytes(UNDECODABLE)
    with pytest.raises(ObsidianProjectionError) as caught:
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)
    assert note.name in str(caught.value), "the message must name the note"


def test_f6_projection_unreadable_note_raises_domain_error(
    projection_vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected for the same portability reason as the graph case above."""
    vault, note, project_id = projection_vault
    real = Path.read_bytes

    def deny(self: Path) -> bytes:
        if self.name == note.name:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(ObsidianProjectionError) as caught:
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)
    assert note.name in str(caught.value), "the message must name the note"
    assert isinstance(caught.value.__cause__, PermissionError)


# ---------------------------------------------------------------------------
# Adversarial: the graph writer's failure path must still write nothing.
#
# Scoped deliberately: both tests below drive `write_projection_outputs`, which
# builds the whole plan before `_promote`. `obsidian_projection` does NOT have
# this property -- it writes inside its per-project loop -- as the module
# docstring above records.
#
# The value of the existing contract is that a failed promote leaves prior bytes
# intact. Adding an exception boundary must not change that -- and asserting it
# is what proves the guard was added *before* the write plan is promoted rather
# than somewhere that could half-apply it.
# ---------------------------------------------------------------------------


def test_f6_graph_failed_read_writes_nothing(graph_vault) -> None:
    vault, note, bundle = graph_vault
    health = vault / "generated/graph/projections/demo/graph-health.md"
    assert health.is_file(), "precondition: a sibling output exists"
    health_before = _sha(health)

    note.write_bytes(UNDECODABLE)
    note_before = _sha(note)

    with pytest.raises(GraphProjectionError):
        write_projection_outputs(bundle, vault=vault)

    assert _sha(note) == note_before, "the offending note was modified"
    assert _sha(health) == health_before, "a sibling output was written despite failure"


def test_f6_graph_no_staging_residue_after_failed_read(graph_vault) -> None:
    vault, note, bundle = graph_vault
    note.write_bytes(UNDECODABLE)
    with pytest.raises(GraphProjectionError):
        write_projection_outputs(bundle, vault=vault)
    residue = [p.name for p in note.parent.iterdir() if p.suffix not in {".md"}]
    assert residue == [], f"staging residue left behind: {residue}"


# ---------------------------------------------------------------------------
# The guard must not swallow the distinctions that already worked.
# ---------------------------------------------------------------------------


def test_f6_valid_note_still_merges(graph_vault) -> None:
    """The boundary must catch read failures only -- not everything."""
    vault, note, bundle = graph_vault
    before = _sha(note)
    write_projection_outputs(bundle, vault=vault)
    assert _sha(note) == before, "an unchanged refresh must stay byte-identical"


def test_f6_malformed_markers_still_raise_their_own_diagnostic(graph_vault) -> None:
    """A structurally malformed note is a *different* failure from an unreadable
    one, and must keep its own F3 diagnostic rather than being relabelled."""
    vault, note, bundle = graph_vault
    text = note.read_bytes().decode("utf-8")
    note.write_bytes((text + "\n<!-- atlas:generated:start -->\n").encode("utf-8"))

    with pytest.raises(GraphProjectionError) as caught:
        write_projection_outputs(bundle, vault=vault)

    message = str(caught.value)
    assert "malformed-generated-markers" in message, message
    assert "unreadable-existing-note" not in message, (
        "the read guard swallowed a structural diagnostic"
    )


# ---------------------------------------------------------------------------
# The write side of the same condition.
#
# On Windows a note that "cannot be opened" does not fail the read at all --
# `chmod` there only clears the read-only bit, so the read succeeds and it is
# `os.replace` that raises WinError 5. The read guard alone therefore left the
# claim platform-incomplete, which only surfaced when independent verification
# ran the Windows CI job. Injected here so it is checked on every runner.
# ---------------------------------------------------------------------------


def test_f6_projection_unwritable_note_raises_domain_error(
    projection_vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, note, project_id = projection_vault
    real_replace = os.replace

    def deny(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if str(dst).endswith(note.name):
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr("project_atlas.obsidian_projection.os.replace", deny)

    with pytest.raises(ObsidianProjectionError) as caught:
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)

    assert "unwritable-note" in str(caught.value), str(caught.value)
    assert isinstance(caught.value.__cause__, PermissionError)


def test_f6_failed_write_leaves_no_tmp_residue(
    projection_vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refused write must not leave a .tmp file behind for a human to find."""
    vault, note, project_id = projection_vault
    real_replace = os.replace

    def deny(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if str(dst).endswith(note.name):
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr("project_atlas.obsidian_projection.os.replace", deny)
    with pytest.raises(ObsidianProjectionError):
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)

    residue = sorted(p.name for p in note.parent.iterdir() if p.suffix == ".tmp")
    assert residue == [], f"staging residue left behind: {residue}"


# ---------------------------------------------------------------------------
# The real filesystem path, POSIX only.
#
# The injected tests above prove the boundary; this proves the boundary is
# reachable by an actual unreadable file rather than only by a mock. Guarded
# per the convention already used by `test_logging.py` and
# `test_linux_filesystem_portability.py`, and skipped as root because root
# ignores the mode bits.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits; Windows chmod only clears read-only")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_f6_graph_real_unreadable_file_raises_domain_error(graph_vault) -> None:
    vault, note, bundle = graph_vault
    os.chmod(note, 0o000)
    try:
        with pytest.raises(GraphProjectionError) as caught:
            write_projection_outputs(bundle, vault=vault)
    finally:
        os.chmod(note, 0o644)
    assert "relationships.md" in str(caught.value)
    assert isinstance(caught.value.__cause__, PermissionError)


def test_f6_cleanup_failure_does_not_mask_the_domain_error(
    projection_vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing staging cleanup must not replace the error it is cleaning up after.

    Found by independent verification: `_write_atomic`'s `finally` removed the
    staging file unguarded, so if that unlink itself raised -- a concurrent
    permission change on the directory, say -- its raw `OSError` propagated out
    of the `finally` and **replaced** the `ObsidianProjectionError` raised just
    above. The caller got a raw exception on the very path this guard exists to
    cover, and the `.tmp` residue survived, contradicting the property
    `test_f6_failed_write_leaves_no_tmp_residue` pins.

    Cleanup is now best-effort. The residue in that case is a disclosed
    residual rather than a silent one, which is the right trade: masking the
    real error is strictly worse than leaving a file behind.
    """
    vault, note, project_id = projection_vault
    real_replace = os.replace

    def deny_replace(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if str(dst).endswith(note.name):
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst, *a, **k)

    def deny_unlink(self: Path, *a, **k):  # type: ignore[no-untyped-def]
        # errno deliberately DIFFERENT from the replace failure above, so the
        # cause-chain assertion below can tell them apart. Both are
        # PermissionError, so `isinstance(..., PermissionError)` alone would
        # pass under either -- verification pointed that out, and it is the
        # half of the property that actually matters here.
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr("project_atlas.obsidian_projection.os.replace", deny_replace)
    monkeypatch.setattr(Path, "unlink", deny_unlink)

    with pytest.raises(ObsidianProjectionError) as caught:
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)

    assert "unwritable-note" in str(caught.value), "the cleanup failure masked the real error"
    cause = caught.value.__cause__
    assert isinstance(cause, PermissionError)
    assert cause.errno == 5, (
        f"__cause__ is the cleanup failure (errno {cause.errno}), not the "
        "replace failure it should be chained from"
    )


def test_f6_cleanup_failure_is_logged_with_the_path_and_cause(
    projection_vault, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The swallowed cleanup must leave an operator-usable record.

    Pinned because the first attempt did not. It passed `extra={"path": ...,
    "error": ...}` at top level, and both formatters render `record.context`
    only — so the warning was emitted carrying neither the filename nor the
    error class. The log existed and said nothing, which is worse than no log,
    because the receipt claimed the residual was now operator-visible.

    Independent verification caught it. Asserting the payload, not merely that
    something was logged, is what makes that impossible to reintroduce.
    """
    vault, note, project_id = projection_vault
    real_replace = os.replace

    def deny_replace(src, dst, *a, **k):  # type: ignore[no-untyped-def]
        if str(dst).endswith(note.name):
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst, *a, **k)

    def deny_unlink(self: Path, *a, **k):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr("project_atlas.obsidian_projection.os.replace", deny_replace)
    monkeypatch.setattr(Path, "unlink", deny_unlink)

    with (
        caplog.at_level("WARNING", logger="project_atlas.obsidian_projection"),
        pytest.raises(ObsidianProjectionError),
    ):
        materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)

    records = [r for r in caplog.records if "staging file" in r.getMessage()]
    assert records, "the swallowed cleanup failure was not logged at all"
    context = getattr(records[0], "context", None)
    assert isinstance(context, dict), (
        "payload must nest under 'context' or both formatters discard it"
    )
    assert context.get("error") == "PermissionError"
    assert str(context.get("path", "")).endswith(".tmp"), context

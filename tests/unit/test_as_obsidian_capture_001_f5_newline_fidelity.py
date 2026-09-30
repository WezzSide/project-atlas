"""AS-OBSIDIAN-CAPTURE-001-F5 — HUMAN line endings are bytes, not formatting.

Every generated-span-preserving writer reads the prior note, splices a fresh
generated span into it, and writes the result back. Reading with
``Path.read_text`` applies universal-newline translation, so ``\r\n`` and a
lone ``\r`` become ``\n`` *before the merge ever sees the bytes* -- and the
operator's HUMAN region is rewritten by a refresh they did not request.

That contradicts three things the repository already claims:

* the F3 owner policy -- raw HUMAN bytes are immutable, "no auto-escaping, no
  normalisation, no zero-width rewriting";
* ``write_projection_outputs``'s own docstring -- "Preserves HUMAN protected
  regions byte-for-byte";
* ``canonical_content``'s boundary -- line-ending normalisation exists **only**
  for identity hashing, and "never changes what is persisted".

It is a *silent* mutation: the marker/count/placement checks that guard these
writers cannot see it, because every marker is still present, correctly
counted, and correctly placed. Only the bytes differ.

These tests therefore assert **bytes and digests**, never substring presence.
Substring assertions are what let this survive: ``"my note" in text`` is true
both before and after the line endings are destroyed.

Truth boundary: this package fixes line-ending fidelity on the read path. It
does not claim general byte fidelity for every transformation, and it does not
touch identity hashing (CORE3-014), which still normalises deliberately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from project_atlas.capture_sources import build_capture_request
from project_atlas.connect import connect_project
from project_atlas.graph_projections import (
    materialize_projections,
    write_projection_outputs,
)
from project_atlas.graph_relationships import RelationshipRecord
from project_atlas.ingestion import _generated_content
from project_atlas.obsidian_capture import canonical_content, capture, content_hash, retry
from project_atlas.obsidian_projection import (
    materialize_obsidian_projection,
    project_note_path,
)
from project_atlas.protected_regions import read_note_text

HUMAN_BEGIN = "<!-- BEGIN HUMAN: notes -->"
HUMAN_END = "<!-- END HUMAN: notes -->"
GENERATED_START = "<!-- atlas:generated:start -->"
GENERATED_END = "<!-- atlas:generated:end -->"

# Line-ending shapes a real editor produces. Every one of these is destroyed by
# universal-newline translation, and none of them is visible to a marker check.
LINE_ENDING_CASES = {
    "crlf": "line one\r\nline two\r\n",
    "lone-cr": "line one\rline two\r",
    "mixed-crlf-lf": "crlf line\r\nlf line\ncrlf again\r\n",
    "cr-inside-prose": "a carriage return \r in the middle of prose\n",
}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "projects" / "harbor-api").mkdir(parents=True)
    (root / "generated").mkdir(parents=True)
    return root


def _relationship(relationship_id: str) -> RelationshipRecord:
    return RelationshipRecord(
        project_id="demo",
        relationship_id=relationship_id,
        relationship_type="depends-on",
        source_entity_id="demo:a",
        target_entity_id="demo:b",
        source_graphify_id="a",
        target_graphify_id="b",
        link_quality="inferred",
        relationship_fingerprint="f" * 64,
        provenance={},
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _humanize(text: str, body: str) -> str:
    """Type ``body`` into the HUMAN placeholder the render already ships."""
    placeholder = f"{HUMAN_BEGIN}\n{HUMAN_END}"
    assert placeholder in text, "the note must ship an editable HUMAN placeholder"
    return text.replace(placeholder, f"{HUMAN_BEGIN}\n{body}{HUMAN_END}")


# ---------------------------------------------------------------------------
# The helper, and the control that proves the defect it exists to prevent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(LINE_ENDING_CASES))
def test_f5_read_note_text_preserves_bytes_exactly(label: str, tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    raw = LINE_ENDING_CASES[label].encode("utf-8")
    note.write_bytes(raw)
    assert read_note_text(note).encode("utf-8") == raw


@pytest.mark.parametrize("label", sorted(LINE_ENDING_CASES))
def test_f5_read_text_is_the_defect_this_helper_avoids(label: str, tmp_path: Path) -> None:
    """Load-bearing control.

    If this ever stops failing, ``Path.read_text`` no longer translates and the
    helper has become redundant -- which is worth knowing deliberately rather
    than discovering when someone "simplifies" it back.
    """
    note = tmp_path / "note.md"
    raw = LINE_ENDING_CASES[label].encode("utf-8")
    note.write_bytes(raw)
    assert note.read_text(encoding="utf-8").encode("utf-8") != raw


# ---------------------------------------------------------------------------
# Entry point 1 of 4 -- capture / retry.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(LINE_ENDING_CASES))
def test_f5_capture_retry_preserves_human_line_endings(label: str, vault: Path) -> None:
    result = capture(vault, build_capture_request(content=f"f5 {label}"))
    outputs = result["outputs"]
    assert isinstance(outputs, list) and outputs
    note = Path(str(outputs[0]["vault_root"])) / str(outputs[0]["relative_path"])

    body = LINE_ENDING_CASES[label]
    note.write_bytes(_humanize(read_note_text(note), body).encode("utf-8"))
    before_bytes, before_sha = note.read_bytes(), _sha(note)
    assert body.encode("utf-8") in before_bytes, "precondition: the bytes are on disk"

    retry(vault, result["capture_id"])

    assert body.encode("utf-8") in note.read_bytes(), "HUMAN line endings were rewritten"
    assert _sha(note) == before_sha, "an unchanged refresh must be byte-identical"


def test_f5_capture_retry_is_stable_across_repeated_refresh(vault: Path) -> None:
    """Erosion check: a second refresh must not finish what the first started.

    Both halves are asserted deliberately. Idempotence alone is *not* a useful
    guard here -- destruction is idempotent too, so an earlier revision of this
    test passed even with the defect fully present, which independent
    verification demonstrated by running it under the negative control. The
    surviving-bytes assertion is what makes it load-bearing.
    """
    body = LINE_ENDING_CASES["crlf"]
    result = capture(vault, build_capture_request(content="f5 repeat"))
    outputs = result["outputs"]
    note = Path(str(outputs[0]["vault_root"])) / str(outputs[0]["relative_path"])
    note.write_bytes(_humanize(read_note_text(note), body).encode("utf-8"))

    retry(vault, result["capture_id"])
    once = note.read_bytes()
    retry(vault, result["capture_id"])

    assert note.read_bytes() == once, "the second refresh changed the note"
    assert body.encode("utf-8") in once, "the first refresh already destroyed the bytes"


# ---------------------------------------------------------------------------
# Entry point 2 of 4 -- the living Obsidian projection.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(LINE_ENDING_CASES))
def test_f5_obsidian_projection_preserves_human_line_endings(label: str, tmp_path: Path) -> None:
    root = tmp_path / "src-project"
    root.mkdir()
    (root / "README.md").write_text("# F5\n\nbody.\n", encoding="utf-8")
    connected = connect_project(root)
    vault = Path(connected["vault"])
    project_id = str(connected["bound_project_id"])
    note = project_note_path(vault, project_id)

    body = LINE_ENDING_CASES[label]
    note.write_bytes(_humanize(read_note_text(note), body).encode("utf-8"))

    materialize_obsidian_projection(vault, project_id=project_id, refresh_brief=False)

    assert body.encode("utf-8") in note.read_bytes(), "HUMAN line endings were rewritten"


# ---------------------------------------------------------------------------
# Entry point 3 of 4 -- graph projections, whose docstring promises
# "Preserves HUMAN protected regions byte-for-byte".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(LINE_ENDING_CASES))
def test_f5_graph_projection_preserves_human_line_endings(label: str, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    bundle = materialize_projections(project_id="demo", relationships=(), health=None)
    write_projection_outputs(bundle, vault=vault)
    note = vault / "generated/graph/projections/demo/relationships.md"

    body = LINE_ENDING_CASES[label]
    note.write_bytes(_humanize(read_note_text(note), body).encode("utf-8"))
    before_sha = _sha(note)

    write_projection_outputs(bundle, vault=vault)

    assert body.encode("utf-8") in note.read_bytes(), "HUMAN line endings were rewritten"
    assert _sha(note) == before_sha, "byte-for-byte, as the docstring claims"


# ---------------------------------------------------------------------------
# Entry point 4 of 4 -- ingestion's own generated-span splice, which does not
# route through protected_regions and so is missed by any check that greps for
# merge_protected_regions.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OWNER-GATED, NOT UNKNOWN. The fourth writer lives in "
        "src/project_atlas/ingestion.py, which is a certified surface frozen by "
        "test_atlas3_demo_isolation_001.test_certified_surfaces_unmodified. "
        "Editing it requires an owner-approved, sha256-pinned exception under "
        "ARCHITECTURE.md SS9.1, which this lane cannot self-grant. The defect is "
        "real and reproduced on main; this test is left executable and strict so "
        "it flips to a visible XPASS failure the moment the gated fix lands, "
        "rather than the defect being silently forgotten. Tracked as F5-B."
    ),
)
@pytest.mark.parametrize("label", sorted(LINE_ENDING_CASES))
def test_f5b_ingestion_generated_content_preserves_human_line_endings(
    label: str, tmp_path: Path
) -> None:
    note = tmp_path / "note.md"
    body = LINE_ENDING_CASES[label]
    prior = (
        f"{GENERATED_START}\nOLD generated body\n{GENERATED_END}\n\n"
        f"{HUMAN_BEGIN}\n{body}{HUMAN_END}\n"
    )
    note.write_bytes(prior.encode("utf-8"))
    fresh = f"{GENERATED_START}\nNEW generated body\n{GENERATED_END}\n"

    merged = _generated_content(note, fresh)

    assert body in merged, "HUMAN line endings were rewritten"
    assert "NEW generated body" in merged, "the generated span must still refresh"
    assert "OLD generated body" not in merged


# ---------------------------------------------------------------------------
# The fix must not freeze the note, and must not disturb LF-only notes.
# ---------------------------------------------------------------------------


def test_f5_generated_span_still_refreshes_beside_crlf_human_content(
    tmp_path: Path,
) -> None:
    """Preserving HUMAN bytes must not accidentally preserve stale generated
    bytes -- generated content is derived and must still be replaced.

    The second render is given **different** input from the first, and the test
    asserts the old generated text is gone and the new text is present. An
    earlier revision re-materialised an identical bundle, so a regression that
    simply returned the prior note unchanged would have passed it: the
    assertions only checked marker counts and that the HUMAN body survived.
    Two independent reviewers caught that, and they were right -- a test whose
    stated purpose is "the generated span still refreshes" has to make the span
    actually change.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    empty = materialize_projections(project_id="demo", relationships=(), health=None)
    write_projection_outputs(empty, vault=vault)
    note = vault / "generated/graph/projections/demo/relationships.md"
    note.write_bytes(_humanize(read_note_text(note), LINE_ENDING_CASES["crlf"]).encode("utf-8"))
    stale_marker = "No retained graph relationships are present for this project."
    assert stale_marker in read_note_text(note), "precondition: the empty render is present"

    changed = materialize_projections(
        project_id="demo",
        relationships=(_relationship("rel-f5"),),
        health=None,
    )
    write_projection_outputs(changed, vault=vault)
    text = read_note_text(note)

    assert LINE_ENDING_CASES["crlf"] in text, "HUMAN bytes must survive the refresh"
    assert stale_marker not in text, "stale generated content was not replaced"
    assert "rel-f5" in text, "the new generated content did not appear"
    assert text.count(GENERATED_START) == 1, "exactly one generated span survives"
    assert text.count(HUMAN_BEGIN) == 1


def test_f5_lf_only_notes_are_unaffected(vault: Path) -> None:
    """Regression guard: the overwhelmingly common LF case must not change."""
    result = capture(vault, build_capture_request(content="f5 lf only"))
    outputs = result["outputs"]
    note = Path(str(outputs[0]["vault_root"])) / str(outputs[0]["relative_path"])
    note.write_bytes(_humanize(read_note_text(note), "plain lf line\n").encode("utf-8"))
    before = note.read_bytes()

    retry(vault, result["capture_id"])

    assert note.read_bytes() == before
    assert b"\r" not in note.read_bytes(), "no line endings were invented"


# ---------------------------------------------------------------------------
# Ownership detection must not depend on line endings.
#
# Found by independent verification of the first F5 candidate, and it is the
# sharpest lesson of this package: reading faithfully is not enough if a
# consumer of that text was silently relying on the translation.
#
# ``_existing_capture_id`` gated on ``text.startswith("---\n")``. The old
# translating read masked that; the faithful read exposed it. A note whose
# frontmatter delimiter ends ``\r\n`` -- a Windows editor, or a checkout with
# ``core.autocrlf=true`` -- was judged unmanaged and refused on every refresh
# with OBSIDIAN_NOTE_CONFLICT. It failed closed and lost no bytes, but the note
# never refreshed again and the error named the wrong cause.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "newline"),
    [("crlf-frontmatter", "\r\n"), ("cr-frontmatter", "\r")],
)
def test_f5_note_ownership_survives_non_lf_frontmatter(
    label: str, newline: str, vault: Path
) -> None:
    """A whole-note CRLF rewrite must still be recognised as Atlas-managed."""
    result = capture(vault, build_capture_request(content=f"f5 {label}"))
    outputs = result["outputs"]
    note = Path(str(outputs[0]["vault_root"])) / str(outputs[0]["relative_path"])

    rewritten = read_note_text(note).replace("\n", newline)
    note.write_bytes(rewritten.encode("utf-8"))

    retried = retry(vault, result["capture_id"])

    assert retried["status"] == "ok", (
        f"a {label} note was judged unmanaged: {retried.get('errors')}"
    )


def test_f5_ownership_probe_does_not_rewrite_human_bytes(vault: Path) -> None:
    """Normalising for the ownership probe must stay local to the probe.

    Scoped deliberately to the HUMAN region. On a whole-note CRLF rewrite the
    *generated* span legitimately comes back LF, because generated content is
    derived and is re-rendered from scratch every refresh -- Atlas owns those
    bytes and renders them canonically. Asserting whole-file CR parity would
    therefore assert the wrong invariant, and an earlier revision of this test
    did exactly that and failed for a correct reason. What must survive is the
    operator's HUMAN block.
    """
    result = capture(vault, build_capture_request(content="f5 probe locality"))
    outputs = result["outputs"]
    note = Path(str(outputs[0]["vault_root"])) / str(outputs[0]["relative_path"])
    # Insert an LF body first, then convert the WHOLE note to CRLF -- exactly
    # what a Windows editor does on save. That also gives the frontmatter a
    # CRLF delimiter, which is what exposed the ownership defect.
    with_body = _humanize(read_note_text(note), "line one\nline two\n")
    note.write_bytes(with_body.replace("\n", "\r\n").encode("utf-8"))

    retried = retry(vault, result["capture_id"])
    after = note.read_bytes()

    assert retried["status"] == "ok", retried.get("errors")
    assert b"line one\r\nline two\r\n" in after, (
        "the ownership probe's normalisation leaked into the persisted HUMAN bytes"
    )


# ---------------------------------------------------------------------------
# The boundary this fix must NOT cross: identity hashing still normalises.
# ---------------------------------------------------------------------------


def test_f5_identity_hashing_still_normalises_line_endings() -> None:
    """CORE3-014 is deliberate and unchanged.

    A capture differing only in transport line endings is the *same* content
    for identity. Storage fidelity and identity normalisation are separate
    concerns, and fixing the first must not silently alter the second.
    """
    assert canonical_content("a\r\nb\r\n") == "a\nb\n"
    assert canonical_content("a\rb\r") == "a\nb\n"
    assert content_hash("a\r\nb\r\n") == content_hash("a\nb\n")

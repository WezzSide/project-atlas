"""AS-OBSIDIAN-CAPTURE-001-F7 — a BOM must not make a note unmanageable.

Windows Notepad writes UTF-8 **with** a byte-order mark by default. An operator
who opens an Atlas-managed note there and saves it gets a file Atlas no longer
recognises as its own — correct ``capture_id``, correct frontmatter, just a
``U+FEFF`` prefix — and every subsequent refresh is refused with
``OBSIDIAN_NOTE_CONFLICT``: "refusing to overwrite a note Atlas does not
manage". The note stops updating permanently and the error names the wrong
cause.

Same consumer and same consequence as the CRLF-frontmatter regression F5's
verification caught, but a different trigger, so it is a separate package.
Pre-existing on ``main`` — F5 neither introduced nor fixed it.

**The trap, stated because it is sharper than the defect.** Refusing a note
Atlas does not recognise is *correct fail-closed behaviour*. The fix must widen
what Atlas **recognises**, never what it **accepts**. A change that started
accepting foreign notes would be far worse than the defect it repairs, so the
hostile-shape refusal set below is the load-bearing half of this package.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.capture_sources import build_capture_request
from project_atlas.obsidian_capture import capture, retry
from project_atlas.obsidian_capture_note import _existing_capture_id

# Explicit escape, never a literal U+FEFF: the literal is invisible in an editor,
# which is exactly what made two of this package's own negative controls silently
# no-op. The escape encodes to the same bytes (b"\xef\xbb\xbf").
BOM = "\ufeff"
HUMAN_BEGIN = "<!-- BEGIN HUMAN: notes -->"
HUMAN_END = "<!-- END HUMAN: notes -->"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "projects" / "harbor-api").mkdir(parents=True)
    (root / "generated").mkdir(parents=True)
    return root


def _note(vault: Path, result: dict) -> Path:
    output = result["outputs"][0]
    return Path(str(output["vault_root"])) / str(output["relative_path"])


# ---------------------------------------------------------------------------
# Recognition: the shapes a real editor produces must keep refreshing.
# ---------------------------------------------------------------------------

RECOGNISED = {
    "plain-lf": lambda t: t,
    "crlf": lambda t: t.replace("\n", "\r\n"),
    "bom-lf": lambda t: BOM + t,
    "bom-crlf-notepad": lambda t: BOM + t.replace("\n", "\r\n"),
    "bom-cr-only": lambda t: BOM + t.replace("\n", "\r"),
}


@pytest.mark.parametrize("label", sorted(RECOGNISED))
def test_f7_editor_written_note_still_refreshes(label: str, vault: Path) -> None:
    result = capture(vault, build_capture_request(content=f"f7 {label}"))
    note = _note(vault, result)
    text = note.read_bytes().decode("utf-8")
    note.write_bytes(RECOGNISED[label](text).encode("utf-8"))

    outcome = retry(vault, result["capture_id"])

    assert outcome["status"] == "ok", (
        f"{label}: Atlas refused its own note: {outcome.get('errors')}"
    )


def test_f7_bom_note_preserves_human_bytes(vault: Path) -> None:
    """Recognising the note is not enough — the human edit must still survive."""
    result = capture(vault, build_capture_request(content="f7 human survives"))
    note = _note(vault, result)
    text = (
        note.read_bytes()
        .decode("utf-8")
        .replace(
            f"{HUMAN_BEGIN}\n{HUMAN_END}",
            f"{HUMAN_BEGIN}\nkeep this human line\n{HUMAN_END}",
        )
    )
    note.write_bytes((BOM + text).encode("utf-8"))

    outcome = retry(vault, result["capture_id"])

    # Load-bearing, and it was not before: without this the test passed with
    # the fix REVERTED -- the retry was refused, the file left untouched, and
    # the human line was "still there" only because nothing had happened.
    # Independent verification caught it by noting the test survived control A.
    assert outcome["status"] == "ok", outcome.get("errors")
    assert b"keep this human line" in note.read_bytes()


def test_f7_probe_recognises_bom_directly() -> None:
    """Unit-level: the ownership probe itself, without the capture machinery."""
    frontmatter = "---\natlas:\n  managed: true\n  capture_id: rcap-abc\n---\nbody\n"
    assert _existing_capture_id(frontmatter) == "rcap-abc"
    assert _existing_capture_id(BOM + frontmatter) == "rcap-abc"
    assert _existing_capture_id(BOM + frontmatter.replace("\n", "\r\n")) == "rcap-abc"


# ---------------------------------------------------------------------------
# THE LOAD-BEARING HALF: recognition must not become acceptance.
#
# Every shape here is a note Atlas does NOT own. Each must still be refused,
# and the file must be left byte-identical. A fix that widens acceptance is
# worse than the defect it repairs, so these outnumber the positive cases.
# ---------------------------------------------------------------------------

HOSTILE = {
    "no-frontmatter": lambda t: "just prose\n",
    "no-frontmatter-bom": lambda t: BOM + "just prose\n",
    "foreign-frontmatter": lambda t: "---\nother: true\n---\nbody\n",
    "foreign-frontmatter-bom": lambda t: BOM + "---\nother: true\n---\nbody\n",
    "managed-false": lambda t: t.replace("managed: true", "managed: false"),
    "managed-false-bom": lambda t: BOM + t.replace("managed: true", "managed: false"),
    "managed-quoted-true": lambda t: t.replace("managed: true", 'managed: "true"'),
    "different-capture-id": lambda t: t.replace("rcap-", "rcapX"),
    "different-capture-id-bom": lambda t: BOM + t.replace("rcap-", "rcapX"),
    "malformed-yaml": lambda t: "---\n: : :\n---\nbody\n",
    "malformed-yaml-bom": lambda t: BOM + "---\n: : :\n---\nbody\n",
    "indented-delimiter": lambda t: "  " + t,
    "atlas-as-scalar": lambda t: "---\natlas: nope\n---\nbody\n",
    "non-string-capture-id": lambda t: t.replace("capture_id: rcap", "capture_id: 12345 #rcap"),
    "unterminated-frontmatter": lambda t: "---\natlas:\n  managed: true\n",
    "empty-file": lambda t: "",
    "bom-only": lambda t: BOM,
    "double-bom": lambda t: BOM + BOM + t,
}


@pytest.mark.parametrize("label", sorted(HOSTILE))
def test_f7_unowned_note_is_still_refused(label: str, vault: Path) -> None:
    result = capture(vault, build_capture_request(content=f"f7 hostile {label}"))
    note = _note(vault, result)
    note.write_bytes(HOSTILE[label](note.read_bytes().decode("utf-8")).encode("utf-8"))
    mutated = note.read_bytes()

    outcome = retry(vault, result["capture_id"])

    assert outcome["status"] != "ok", (
        f"{label}: Atlas ACCEPTED a note it does not own -- recognition became acceptance"
    )
    # Assert the REASON, not just that something refused: without this the test
    # would still pass if a shape were rejected for an unrelated cause (a secret
    # finding, a path escape), quietly losing the ownership coverage it exists
    # for. Verification confirmed all 18 currently refuse for this reason.
    codes = {e.get("code") for e in (outcome.get("errors") or [])}
    assert "OBSIDIAN_NOTE_CONFLICT" in codes, f"{label}: refused for the wrong reason: {codes}"
    assert note.read_bytes() == mutated, f"{label}: the refused note was modified"


# ---------------------------------------------------------------------------
# Claim boundary, pinned.
# ---------------------------------------------------------------------------


def test_f7_bom_is_not_preserved_and_that_is_deliberate(vault: Path) -> None:
    """The BOM sits before the frontmatter, in Atlas-owned territory.

    Atlas re-renders the generated region from scratch, so the mark is not
    carried across. That is consistent with "generated content is derived" and
    is NOT a HUMAN-byte loss -- the human region survives verbatim, which the
    assertion below pins alongside it. Recorded as behaviour rather than left
    for someone to discover: an editor that re-adds the BOM will simply be
    recognised again on the next refresh.
    """
    result = capture(vault, build_capture_request(content="f7 bom not preserved"))
    note = _note(vault, result)
    text = (
        note.read_bytes()
        .decode("utf-8")
        .replace(f"{HUMAN_BEGIN}\n{HUMAN_END}", f"{HUMAN_BEGIN}\nhuman stays\n{HUMAN_END}")
    )
    note.write_bytes((BOM + text).encode("utf-8"))
    assert note.read_bytes().startswith(b"\xef\xbb\xbf")

    retry(vault, result["capture_id"])

    after = note.read_bytes()
    assert not after.startswith(b"\xef\xbb\xbf"), "BOM survived; update this claim"
    assert b"human stays" in after, "HUMAN bytes must survive regardless"

"""AS-OBSIDIAN-CAPTURE-001 — capture identity, routing, projection, safety.

Covers the architecture's required invariant tests (§62) plus the component
boundaries of §61: capture identity, dedupe, routing, Markdown rendering,
path safety, and clipboard provider selection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from project_atlas.capture_io import materialize_under_root, write_atomic_under_root
from project_atlas.capture_sources import (
    MAX_CONTENT_BYTES,
    CaptureSourceError,
    build_capture_request,
    detect_clipboard_provider,
    read_clipboard_text,
)
from project_atlas.obsidian_capture import (
    CAPTURE_DIR,
    CaptureError,
    RoutingPolicy,
    canonical_content,
    capture,
    classify,
    content_hash,
    derive_title,
    identity_hash,
    list_captures,
    read_raw_content,
    resolve_project,
    retry,
    route,
)
from project_atlas.obsidian_capture_note import (
    ObsidianNoteError,
    build_frontmatter,
    note_filename,
    render_note,
    slugify,
    write_note,
)
from project_atlas.protected_regions import (
    GENERATED_END,
    GENERATED_START,
    ProtectedRegionError,
    extract_human_regions,
    merge_protected_regions,
)
from project_atlas.schema import validate_record


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "projects" / "harbor-api").mkdir(parents=True)
    (root / "generated").mkdir(parents=True)
    return root


def _request(content: str, **kwargs: object):
    return build_capture_request(content=content, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Capture identity and canonicalization (architecture §7)
# --------------------------------------------------------------------------


def test_canonicalization_normalizes_line_endings_only() -> None:
    """Transport-only differences share identity; real content does not."""
    assert canonical_content("a\r\nb") == "a\nb"
    assert canonical_content("a\rb") == "a\nb"
    assert content_hash("a\r\nb") == content_hash("a\nb")
    # Indentation is semantically relevant and must NOT be normalized away.
    assert content_hash("  a\nb") != content_hash("a\nb")
    # Leading/trailing whitespace is preserved (no aggressive stripping, §7.3).
    assert content_hash("a\n") != content_hash("a")


def test_canonicalization_applies_nfc() -> None:
    composed = "café"
    decomposed = "café"
    assert composed != decomposed
    assert content_hash(composed) == content_hash(decomposed)


def test_content_hash_and_identity_hash_are_not_conflated() -> None:
    """Architecture §7: content identity != logical-capture identity."""
    args = {"content": "same text", "source_type": "text", "source_application": "chatgpt"}
    a = identity_hash(project_id="harbor-api", **args)  # type: ignore[arg-type]
    b = identity_hash(project_id="other", **args)  # type: ignore[arg-type]
    assert a != b, "same content in two projects is two logical captures"
    assert content_hash("same text") == content_hash("same text")

    c = identity_hash(
        content="same text",
        project_id="harbor-api",
        source_type="text",
        source_application="claude",
    )
    assert a != c, "source application participates in logical identity"


# --------------------------------------------------------------------------
# Deduplication (architecture §8, §66, INV-006)
# --------------------------------------------------------------------------


def test_duplicate_capture_creates_no_second_record_or_note(vault: Path) -> None:
    first = capture(vault, _request("duplicate me"))
    second = capture(vault, _request("duplicate me"))

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["existing_capture_id"] == first["capture_id"]
    assert second["capture_id"] == first["capture_id"]

    records = sorted((vault / CAPTURE_DIR).glob("rcap-*.json"))
    assert len(records) == 1
    notes = sorted((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    assert len(notes) == 1


def test_reprocessing_is_idempotent_and_deterministic(vault: Path) -> None:
    """INV-006 + NFR-001: same input renders byte-identical output."""
    result = capture(vault, _request("stable content"))
    note = vault / "generated" / "obsidian" / "captures"
    path = next(note.rglob("*.md"))
    first_bytes = path.read_bytes()

    again = retry(vault, result["capture_id"])
    assert again["status"] == "ok"
    assert path.read_bytes() == first_bytes


# --------------------------------------------------------------------------
# Raw evidence preservation (INV-001, architecture §66, §68)
# --------------------------------------------------------------------------


def test_raw_evidence_survives_rendering_failure(vault: Path, tmp_path: Path) -> None:
    """Required test §62: persist succeeds, Obsidian fails, raw still exists."""
    not_a_directory = tmp_path / "blocker"
    not_a_directory.write_text("i am a file", encoding="utf-8")

    result = capture(vault, _request("precious evidence"), obsidian_root=not_a_directory)

    assert result["status"] == "partial"
    assert result["errors"], "the rendering failure must be reported, not swallowed"
    assert result["errors"][0]["stage"] == "render"
    assert result["lifecycle_state"] == "persisted"
    # The evidence is intact and recoverable.
    assert read_raw_content(vault, result["capture_id"]) == "precious evidence"
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["provenance"]["raw_content_persisted"] is True
    assert record["stage_failures"][0]["stage"] == "render"


def test_failed_render_is_resumable_via_retry(vault: Path, tmp_path: Path) -> None:
    """Architecture §20: recovery reloads evidence from the store."""
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    failed = capture(vault, _request("resume me"), obsidian_root=blocker)
    assert failed["status"] == "partial"

    resumed = retry(vault, failed["capture_id"])
    assert resumed["status"] == "ok"
    assert resumed["lifecycle_state"] == "rendered"
    assert resumed["outputs"], "retry must produce the note the first attempt could not"
    record = json.loads(
        (vault / CAPTURE_DIR / f"{failed['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["stage_failures"] == [], "a resolved failure must be cleared"


def test_raw_content_is_stored_verbatim_not_canonicalized(vault: Path) -> None:
    """Identity canonicalization must never rewrite preserved evidence."""
    original = "line one\r\nline two\r\n"
    result = capture(vault, _request(original))
    assert read_raw_content(vault, result["capture_id"]) == original


# --------------------------------------------------------------------------
# Unicode (required test §62)
# --------------------------------------------------------------------------


def test_unicode_dutch_emoji_and_code_blocks_are_preserved(vault: Path) -> None:
    content = (
        "Bijvoorbeeld: eeuwigdurende ijsvrije oevers één keer.\n"
        "Emoji: \U0001f680\U0001f9e0✅\n"
        "Speciaal: <>&\"'`| — üñ\n"
        "```python\ndef f(x: dict[str, int]) -> None:\n    return None\n```\n"
    )
    result = capture(vault, _request(content))
    assert read_raw_content(vault, result["capture_id"]) == content
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    rendered = note.read_text(encoding="utf-8")
    assert "\U0001f680" in rendered
    assert "eeuwigdurende" in rendered
    assert "def f(x: dict[str, int]) -> None:" in rendered


# --------------------------------------------------------------------------
# Provenance (required test §62, architecture §9, §60)
# --------------------------------------------------------------------------


def test_note_capture_id_resolves_to_the_persisted_capture(vault: Path) -> None:
    result = capture(vault, _request("traceable"))
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    text = note.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---\n")[1])

    capture_id = frontmatter["atlas"]["capture_id"]
    assert capture_id == result["capture_id"]
    record_path = vault / CAPTURE_DIR / f"{capture_id}.json"
    assert record_path.is_file()

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["content_hash"] == frontmatter["atlas"]["content_hash"]
    # note -> capture -> raw evidence closes the trace.
    assert (vault / record["content_path"]).read_text(encoding="utf-8") == "traceable"


def test_record_validates_against_its_shipped_schema(vault: Path) -> None:
    result = capture(vault, _request("schema check", source_application="chatgpt"))
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    validate_record(record, "raw-capture")
    assert record["authority"]["classification"] == "NON_CANONICAL"
    assert record["honesty"]["capture_is_authority"] is False


# --------------------------------------------------------------------------
# Path traversal protection (required test §62, architecture §32, §64)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    ["../../outside", "../outside", "/etc", "..", "C:evil", "a/../../b"],
)
def test_routing_traversal_values_are_rejected(destination: str) -> None:
    policy = RoutingPolicy(inbox=destination)
    with pytest.raises(CaptureError) as excinfo:
        policy.validate()
    assert excinfo.value.code == "ROUTING_UNSAFE"


def test_capture_rejects_traversal_routing_before_writing(vault: Path) -> None:
    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request("x"), routing=RoutingPolicy(inbox="../../outside"))
    assert excinfo.value.code == "ROUTING_UNSAFE"
    assert not (vault / CAPTURE_DIR).exists(), "nothing may be written on unsafe routing"


def test_note_write_rejects_symlink_escape(vault: Path, tmp_path: Path) -> None:
    """A symlinked destination directory must not smuggle writes out (§64)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    obsidian_root = tmp_path / "obs"
    (obsidian_root / "00 Inbox").mkdir(parents=True)
    (obsidian_root / "00 Inbox" / "Atlas Captures").symlink_to(outside, target_is_directory=True)

    result = capture(vault, _request("escape attempt"), obsidian_root=obsidian_root)
    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "PATH_ESCAPES_VAULT"
    assert not list(outside.iterdir()), "no file may land outside the configured root"
    # Evidence is still preserved (INV-007).
    assert read_raw_content(vault, result["capture_id"]) == "escape attempt"


@pytest.mark.parametrize("project", ["../evil", "a/b", "..", "", "  "])
def test_path_shaped_project_ids_are_rejected(vault: Path, project: str) -> None:
    if not project.strip():
        assert resolve_project(vault, project) is None
        return
    with pytest.raises(CaptureError) as excinfo:
        resolve_project(vault, project)
    assert excinfo.value.code in {"PATH_SHAPED_PROJECT_ID", "UNMATCHED_PROJECT"}


def test_capture_id_must_be_well_formed(vault: Path) -> None:
    for bad in ["../../etc/passwd", "rcap-../x", "nope", "rcap-zz"]:
        with pytest.raises(CaptureError) as excinfo:
            read_raw_content(vault, bad)
        assert excinfo.value.code == "MALFORMED_CAPTURE_ID"


# --------------------------------------------------------------------------
# Routing (architecture §15)
# --------------------------------------------------------------------------


def test_routing_priority_explicit_project_then_classification_then_inbox() -> None:
    policy = RoutingPolicy()
    explicit = route(project_id="harbor-api", classification="conversation", policy=policy)
    assert explicit["destination"] == "10 Projects/harbor-api/Conversations"
    assert explicit["reason"] == "explicit-project"
    assert explicit["fallback"] is False

    classified = route(project_id=None, classification="decision", policy=policy)
    assert classified["destination"] == "20 Decisions"
    assert classified["reason"] == "deterministic-classification"

    fallback = route(project_id=None, classification="note", policy=policy)
    assert fallback["destination"] == "00 Inbox/Atlas Captures"
    assert fallback["reason"] == "inbox-fallback"
    assert fallback["fallback"] is True


def test_unroutable_capture_is_never_discarded(vault: Path) -> None:
    """Architecture §15: never discard content because routing fails."""
    result = capture(vault, _request("no project given"))
    assert result["project_id"] is None
    assert result["status"] == "ok"
    assert "Inbox" in result["outputs"][0]["relative_path"]


def test_explicit_unknown_project_fails_closed(vault: Path) -> None:
    """Atlas never invents project attribution."""
    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request("x", project_reference="not-a-project"))
    assert excinfo.value.code == "UNMATCHED_PROJECT"


def test_classification_is_deterministic_and_bounded() -> None:
    assert classify(_request("x", source_type="conversation")) == "conversation"
    assert classify(_request("x", source_type="web")) == "research"
    assert classify(_request("x", source_type="text")) == "note"
    assert classify(_request("x"), explicit="directive") == "directive"
    with pytest.raises(CaptureError) as excinfo:
        classify(_request("x"), explicit="not-a-class")
    assert excinfo.value.code == "UNSUPPORTED_CLASSIFICATION"


# --------------------------------------------------------------------------
# Markdown / YAML rendering (architecture §16, §33, §35)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_title",
    [
        "key: value",
        "---",
        'quote " inside',
        "single ' inside",
        "[bracketed]",
        "# hash",
        "line\nbreak",
        "ünicöde \U0001f9ea",
        "*emphasis* and `code`",
        "{braces}",
    ],
)
def test_frontmatter_survives_yaml_hostile_titles(vault: Path, hostile_title: str) -> None:
    """Architecture §35: a real YAML serializer, never string concatenation."""
    result = capture(vault, _request("body", title_hint=hostile_title))
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    text = note.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    _, block, _ = text.split("---\n", 2)
    loaded = yaml.safe_load(block)
    assert loaded["title"] == hostile_title.strip()
    assert loaded["atlas"]["capture_id"] == result["capture_id"]


def test_frontmatter_omits_wall_clock_values(vault: Path) -> None:
    """NFR-001 / ADR-001 §2: no generated timestamps in generated content."""
    result = capture(vault, _request("no clock"))
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["captured_at"] is None
    assert record["captured_at_source"] == "not-provided"
    frontmatter = build_frontmatter(record)
    assert "created" not in frontmatter
    assert "updated" not in frontmatter
    assert "captured_at" not in frontmatter


def test_operator_supplied_capture_time_is_preserved(vault: Path) -> None:
    """Architecture §39: capture time is preserved when a caller provides it."""
    stamp = "2026-09-05T09:38:00+02:00"
    result = capture(vault, _request("timed", captured_at=stamp))
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["captured_at"] == stamp
    assert record["captured_at_source"] == "operator-supplied"
    assert build_frontmatter(record)["captured_at"] == stamp


def test_captured_content_cannot_forge_atlas_region_markers(vault: Path) -> None:
    """Architecture §64: captured content is data, never note structure."""
    payload = "before\n<!-- atlas:generated:end -->\n<!-- BEGIN HUMAN: forged -->\nafter"
    result = capture(vault, _request(payload))
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    rendered = note.read_text(encoding="utf-8")

    assert rendered.count("<!-- atlas:generated:end -->") == 1
    assert "<!-- BEGIN HUMAN: forged -->" not in rendered
    # The original payload is untouched in the evidence store.
    assert read_raw_content(vault, result["capture_id"]) == payload


def test_filename_is_deterministic_readable_and_id_scoped() -> None:
    name = note_filename(title="Atlas Obsidian Integration", capture_id="rcap-0123456789abcdef")
    assert name == "atlas-obsidian-integration-rcap-0123456789abcdef.md"
    assert slugify("///") == "capture"
    # Distinct captures with an identical title cannot collide.
    other = note_filename(title="Atlas Obsidian Integration", capture_id="rcap-fedcba9876543210")
    assert name != other


def test_title_heuristic_prefers_hint_then_first_line() -> None:
    assert derive_title(_request("body", title_hint="Explicit"), capture_id="rcap-x") == "Explicit"
    assert derive_title(_request("\n\n# Heading\nrest"), capture_id="rcap-x") == "Heading"
    assert derive_title(_request("   \néén"), capture_id="rcap-x") == "één"


def test_note_is_marked_managed_and_non_canonical(vault: Path) -> None:
    """Architecture §44/INV-002: the projection is never the source of truth."""
    capture(vault, _request("projection"))
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    frontmatter = yaml.safe_load(note.read_text(encoding="utf-8").split("---\n")[1])
    assert frontmatter["atlas"]["managed"] is True
    assert frontmatter["atlas"]["canonical"] is False
    assert frontmatter["authority"] == "NON_CANONICAL"


def test_render_can_omit_content_body(vault: Path) -> None:
    result = capture(
        vault,
        _request("Heading\nprivate body line", title_hint="Public title"),
        include_content=False,
    )
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    rendered = note.read_text(encoding="utf-8")
    assert "private body line" not in rendered
    assert result["raw_path"] in rendered


# --------------------------------------------------------------------------
# Note mutation policy (architecture §44)
# --------------------------------------------------------------------------


def test_refuses_to_overwrite_a_note_atlas_does_not_manage(vault: Path, tmp_path: Path) -> None:
    obsidian_root = tmp_path / "obs"
    obsidian_root.mkdir()
    record = json.loads(
        (
            vault / CAPTURE_DIR / f"{capture(vault, _request('own note'))['capture_id']}.json"
        ).read_text(encoding="utf-8")
    )
    destination = obsidian_root / record["routing"]["destination"]
    destination.mkdir(parents=True)
    filename = note_filename(title=record["title"], capture_id=record["capture_id"])
    (destination / filename).write_text("# hand written by a human\n", encoding="utf-8")

    with pytest.raises(ObsidianNoteError) as excinfo:
        write_note(record, content="own note", obsidian_root=obsidian_root)
    assert excinfo.value.code == "OBSIDIAN_NOTE_CONFLICT"
    assert (destination / filename).read_text(encoding="utf-8") == "# hand written by a human\n"


def test_rewriting_atlas_own_note_is_allowed(vault: Path, tmp_path: Path) -> None:
    obsidian_root = tmp_path / "obs"
    obsidian_root.mkdir()
    result = capture(vault, _request("mine"), obsidian_root=obsidian_root)
    assert result["status"] == "ok"
    again = retry(vault, result["capture_id"], obsidian_root=obsidian_root)
    assert again["status"] == "ok"


# --------------------------------------------------------------------------
# Secrets and privacy (architecture §37, §38)
# --------------------------------------------------------------------------


def test_secret_bearing_capture_is_rejected_before_any_write(vault: Path) -> None:
    """NFR-004 / AT-014: no plaintext credential may reach generated output.

    This replaces an earlier contract that preserved the raw payload verbatim
    and redacted only the projection. Independent review established that the
    raw store lives under ``generated/``, so that design put a live credential
    into generated output and into every vault backup and sync. Repository
    truth (AGENTS.md: "excluded or redacted before any generated output",
    "0 secrets in output") outranks verbatim-evidence fidelity, so INV-001 is
    scoped to content that clears this gate.
    """
    payload = "deploy notes\napi_key = sk-FAKEFAKEFAKEFAKEFAKEFAKE0123\ntail"

    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request(payload))

    assert excinfo.value.code == "SECRET_CONTENT"
    # The matched value is never echoed back (CODEX-SEC-006).
    assert "sk-FAKEFAKE" not in str(excinfo.value)
    assert "api-key-assignment" in str(excinfo.value)
    # Nothing at all was written.
    assert not (vault / CAPTURE_DIR).exists()
    assert not (vault / "generated" / "obsidian").exists()


@pytest.mark.parametrize(
    ("pattern", "secret"),
    [
        ("api-key-assignment", "api_key = sk-FAKEFAKEFAKEFAKEFAKEFAKE0123"),
        ("cloud-access-key", "AKIAIOSFODNN7EXAMPLE"),
        ("bearer-token", "Bearer FAKEfakeFAKEfakeFAKEfake0123456789"),
        ("connection-string", "postgres://u:FAKEPW@db.internal:5432/x"),
    ],
)
def test_no_secret_class_reaches_generated_output(vault: Path, pattern: str, secret: str) -> None:
    """Every canonical detector class fails closed, verified on disk bytes."""
    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request(f"line one\n{secret}\nline two"))
    assert excinfo.value.code == "SECRET_CONTENT"

    generated = vault / "generated"
    on_disk = [
        path
        for path in generated.rglob("*")
        if path.is_file() and secret in path.read_bytes().decode("utf-8", "replace")
    ]
    assert on_disk == [], f"{pattern} leaked into generated output"


@pytest.mark.parametrize(
    "field",
    [
        "title_hint",
        "source_locator",
        "metadata",
        "metadata_key",
        "captured_at",
        "source_application",
    ],
)
def test_secret_in_secondary_fields_is_rejected(vault: Path, field: str) -> None:
    """A title becomes the filename; a locator and metadata reach the record.

    ``metadata_key``, ``captured_at`` and ``source_application`` were added
    after independent verification found the gate scanned metadata *values*
    but not their keys, and did not scan ``captured_at`` at all — so a
    credential passed to ``--captured-at`` was persisted verbatim into the
    record JSON and the note frontmatter while ``secret_scan.findings``
    still reported an empty list (AT-014 coverage gap).
    """
    secret = "AKIAIOSFODNN7EXAMPLE"
    kwargs: dict[str, object] = {"content": "entirely clean body"}
    if field == "title_hint":
        kwargs["title_hint"] = secret
    elif field == "source_locator":
        kwargs["source_locator"] = f"https://example.test/{secret}"
    elif field == "metadata_key":
        kwargs["source_metadata"] = {secret: "an ordinary value"}
    elif field == "captured_at":
        kwargs["captured_at"] = secret
    elif field == "source_application":
        # Short enough to survive the 32-character token clamp, and lower-case
        # so it is not incidentally rejected by the record schema instead.
        kwargs["source_application"] = "password=hunter2secret"
        secret = "password=hunter2secret"
    else:
        kwargs["source_metadata"] = {"page_title": secret}

    with pytest.raises(CaptureError) as excinfo:
        capture(vault, build_capture_request(**kwargs))  # type: ignore[arg-type]
    assert excinfo.value.code == "SECRET_CONTENT"
    assert not (vault / CAPTURE_DIR).exists()
    # Byte evidence, not a status string: nothing under generated/ holds it.
    generated = vault / "generated"
    leaked = [
        path
        for path in generated.rglob("*")
        if path.is_file() and secret in path.read_bytes().decode("utf-8", "replace")
    ]
    assert leaked == [], f"{field} leaked a credential into generated output"


def test_operator_supplied_captured_at_is_scanned_before_persistence(
    vault: Path,
) -> None:
    """AT-014: the gate must not certify a scan it never performed.

    Regression for the exact defect: ``captured_at`` reached both the record
    JSON and the note frontmatter unscanned, so the persisted record asserted
    ``secret_scan: {"findings": []}`` about a payload that carried a live
    bearer token. A clean ``captured_at`` must still be accepted verbatim.
    """
    token = "Bearer FAKEfakeFAKEfakeFAKEfake0123456789"

    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request("routine note", captured_at=token))
    assert excinfo.value.code == "SECRET_CONTENT"
    assert "bearer-token" in str(excinfo.value)
    # The matched value is never echoed back (CODEX-SEC-006).
    assert "FAKEfakeFAKE" not in str(excinfo.value)
    assert not (vault / "generated" / "obsidian").exists()

    # The field itself is not banned — only credential-shaped content is.
    ok = capture(vault, _request("routine note", captured_at="2026-09-06T10:00:00Z"))
    record = json.loads(
        (vault / CAPTURE_DIR / f"{ok['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["captured_at"] == "2026-09-06T10:00:00Z"
    assert record["secret_scan"]["findings"] == []


def test_retry_refuses_a_legacy_unsafe_raw_artifact(vault: Path) -> None:
    """A store written before this gate must not be re-rendered."""
    result = capture(vault, _request("clean body"))
    raw = vault / result["raw_path"]
    raw.write_text("clean body\nAKIAIOSFODNN7EXAMPLE", encoding="utf-8")
    record_path = vault / CAPTURE_DIR / f"{result['capture_id']}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["content_hash"] = content_hash(raw.read_text(encoding="utf-8"))
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(CaptureError) as excinfo:
        retry(vault, result["capture_id"])
    assert excinfo.value.code == "SECRET_CONTENT"


def test_secret_typed_into_a_human_region_is_not_re_persisted(vault: Path) -> None:
    """Atlas cannot unwrite what a human saved, but must not propagate it."""
    result = capture(vault, _request("clean body"))
    note = next((vault / "generated" / "obsidian" / "captures").rglob("*.md"))
    note.write_text(
        note.read_text(encoding="utf-8").replace(
            "<!-- BEGIN HUMAN: notes -->",
            "<!-- BEGIN HUMAN: notes -->\nAKIAIOSFODNN7EXAMPLE",
            1,
        ),
        encoding="utf-8",
    )

    again = retry(vault, result["capture_id"])
    assert again["status"] == "partial"
    assert again["errors"][0]["code"] == "SECRET_CONTENT"


def test_clean_capture_still_preserves_raw_verbatim(vault: Path) -> None:
    """INV-001 is scoped, not removed: non-secret evidence stays byte-exact."""
    payload = "ordinary notes\r\nwith CRLF and Ünicode ✅\n"
    result = capture(vault, _request(payload))
    assert result["status"] == "ok"
    assert read_raw_content(vault, result["capture_id"]) == payload
    assert result["secret_findings"] == []


def test_capture_declares_no_external_transmission(vault: Path) -> None:
    """Architecture §37/INV-005: capture is local-first with no egress."""
    result = capture(vault, _request("local only"))
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["honesty"]["external_transmission"] is False


# --------------------------------------------------------------------------
# Source adapters and clipboard (architecture §6.1, §22, §24)
# --------------------------------------------------------------------------


def test_request_validation_bounds_and_rejects_empty() -> None:
    with pytest.raises(CaptureSourceError) as empty:
        build_capture_request(content="   \n  ")
    assert empty.value.code == "EMPTY_CONTENT"

    with pytest.raises(CaptureSourceError) as big:
        build_capture_request(content="x" * (MAX_CONTENT_BYTES + 1))
    assert big.value.code == "CAPTURE_INPUT_TOO_LARGE"

    with pytest.raises(CaptureSourceError) as bad_type:
        build_capture_request(content="x", source_type="telepathy")
    assert bad_type.value.code == "UNSUPPORTED_SOURCE_TYPE"


def test_clipboard_provider_selection_prefers_session_type() -> None:
    available = {"wl-paste", "xclip", "xsel"}

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    wayland = detect_clipboard_provider({"XDG_SESSION_TYPE": "wayland"}, which=which)
    assert wayland.name == "wl-paste"

    x11 = detect_clipboard_provider({"XDG_SESSION_TYPE": "x11"}, which=which)
    assert x11.name == "xclip"

    available = {"xsel"}
    assert detect_clipboard_provider({"XDG_SESSION_TYPE": "x11"}, which=which).name == "xsel"


def test_clipboard_absence_is_an_actionable_error() -> None:
    with pytest.raises(CaptureSourceError) as excinfo:
        detect_clipboard_provider({"XDG_SESSION_TYPE": "x11"}, which=lambda _name: None)
    assert excinfo.value.code == "CLIPBOARD_UNAVAILABLE"
    assert "--stdin" in str(excinfo.value)


def test_clipboard_content_is_read_as_data_never_executed() -> None:
    """Architecture §22/§64: a fixed argv, no shell, content is inert data."""
    seen: dict[str, object] = {}

    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, b"$(rm -rf /) && echo pwned", b"")

    text = read_clipboard_text(
        environ={"XDG_SESSION_TYPE": "x11"},
        runner=runner,
        which=lambda name: f"/usr/bin/{name}" if name == "xclip" else None,
    )
    assert text == "$(rm -rf /) && echo pwned"
    assert seen["argv"] == ["xclip", "-selection", "clipboard", "-o"]
    assert seen["kwargs"]["shell"] is False  # type: ignore[index]


def test_clipboard_non_utf8_fails_closed() -> None:
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, b"\xff\xfe\x00binary", b"")

    with pytest.raises(CaptureSourceError) as excinfo:
        read_clipboard_text(
            environ={"XDG_SESSION_TYPE": "x11"},
            runner=runner,
            which=lambda name: f"/usr/bin/{name}" if name == "xclip" else None,
        )
    assert excinfo.value.code == "CLIPBOARD_NOT_TEXT"


# --------------------------------------------------------------------------
# Offline operation (required test §62, INV-004)
# --------------------------------------------------------------------------


def test_capture_works_with_no_model_provider_available(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-004: deterministic capture never needs an external model."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ATLAS_AI_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("capture must not open a network connection")

    monkeypatch.setattr("socket.socket.connect", no_network)
    result = capture(vault, _request("offline capture"))
    assert result["status"] == "ok"
    assert result["outputs"]


# --------------------------------------------------------------------------
# Listing lens
# --------------------------------------------------------------------------


def test_list_captures_is_project_scoped_and_deterministic(vault: Path) -> None:
    capture(vault, _request("one", project_reference="harbor-api"))
    capture(vault, _request("two"))

    scoped = list_captures(vault, project_id="harbor-api")
    assert [row["title"] for row in scoped] == ["one"]

    everything = list_captures(vault)
    assert len(everything) == 2
    ids = [row["capture_id"] for row in everything]
    assert ids == sorted(ids, reverse=True), "deterministic id order, not wall-clock"
    assert all(row["authority"] is False for row in everything)


def test_render_note_is_a_pure_function_of_the_record(vault: Path) -> None:
    result = capture(vault, _request("pure"))
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert render_note(record, content="pure") == render_note(record, content="pure")


# --------------------------------------------------------------------------
# Contained atomic writes (project_atlas.capture_io).
#
# The Windows leg of the exact-head CI matrix (run 33956239428) showed two
# concurrency faults in this layer that Linux never exhibits, so both are
# pinned here rather than left to the platform that happened to find them.
# --------------------------------------------------------------------------


def test_raw_store_symlinked_out_of_the_vault_fails_closed(vault: Path, tmp_path: Path) -> None:
    """A junction/symlink planted at the capture store must not leak evidence."""
    stolen = tmp_path / "stolen"
    stolen.mkdir()
    (vault / "generated" / "ops").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "ops" / "raw-captures").symlink_to(stolen, target_is_directory=True)

    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request("secret evidence"))
    assert excinfo.value.code == "PATH_ESCAPES_VAULT"
    assert [p for p in stolen.rglob("*") if p.is_file()] == [], "no bytes may escape"


def test_lexical_containment_precedes_any_directory_creation(tmp_path: Path) -> None:
    """The cheap lexical gate runs before mkdir, so nothing is created outside."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside" / "nested"

    with pytest.raises(ValueError, match="escapes root"):
        write_atomic_under_root(outside / "f.txt", b"x", root=root, label="test")
    assert not outside.exists(), "a rejected target must not be materialized"


def test_atomic_write_retries_a_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows can raise EACCES on a benign replace race; a bounded retry wins.

    Atomicity is preserved because each ``os.replace`` attempt either
    replaces the destination wholly or leaves it untouched.
    """
    root = tmp_path / "root"
    root.mkdir()
    target = root / "out.txt"
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr("project_atlas.capture_io.os.replace", flaky)
    monkeypatch.setattr("project_atlas.capture_io.RETRY_BACKOFF_SECONDS", 0)

    write_atomic_under_root(target, b"payload", root=root, label="test")
    assert target.read_bytes() == b"payload"
    assert calls["n"] == 3
    assert list(root.glob("*.tmp")) == [], "no temp file may survive"


def test_atomic_write_gives_up_after_bounded_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry is bounded: a persistent failure surfaces, never loops."""
    root = tmp_path / "root"
    root.mkdir()

    def always_denied(src: object, dst: object) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("project_atlas.capture_io.os.replace", always_denied)
    monkeypatch.setattr("project_atlas.capture_io.RETRY_BACKOFF_SECONDS", 0)

    with pytest.raises(PermissionError):
        write_atomic_under_root(root / "out.txt", b"x", root=root, label="test")
    assert list(root.glob("*.tmp")) == [], "the temp file is cleaned up on failure"


def test_mkdir_tolerates_a_concurrent_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows may report EACCES rather than EEXIST for a concurrent mkdir."""
    root = tmp_path / "root"
    root.mkdir()
    nested = root / "a" / "b"
    real_mkdir = Path.mkdir
    calls = {"n": 0}

    def flaky(self: Path, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]
            raise PermissionError(13, "Access is denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", flaky)
    monkeypatch.setattr("project_atlas.capture_io.RETRY_BACKOFF_SECONDS", 0)

    write_atomic_under_root(nested / "f.txt", b"y", root=root, label="test")
    monkeypatch.undo()
    assert (nested / "f.txt").read_bytes() == b"y"


def test_atomic_write_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    """Repeated writes of the same content-addressed bytes converge."""
    root = tmp_path / "root"
    root.mkdir()
    target = root / "nested" / "out.txt"
    for _ in range(5):
        write_atomic_under_root(target, b"same", root=root, label="test")
    assert target.read_bytes() == b"same"
    assert list(target.parent.glob("*.tmp")) == []


# --------------------------------------------------------------------------
# AS-OBSIDIAN-CAPTURE-001-R1 — default projection root trust anchor.
#
# Independent verification found that a symlink planted under
# generated/obsidian let the *default* projection root become its own
# containment anchor, so a capture wrote the derived note outside the Atlas
# vault and still reported status "ok". For the implicit in-vault projection
# the Atlas vault is the trust anchor; an attacker-controlled symlink inside
# generated/obsidian must never redefine it.
# --------------------------------------------------------------------------


def _outside_files(outside: Path) -> list[Path]:
    return [p for p in outside.rglob("*") if p.is_file()]


@pytest.mark.parametrize(
    ("case", "link_at"),
    [
        ("R1-A leaf", "generated/obsidian/captures"),
        ("R1-B intermediate", "generated/obsidian"),
    ],
)
def test_default_projection_root_symlink_fails_closed(
    vault: Path, tmp_path: Path, case: str, link_at: str
) -> None:
    """The default projection root is anchored on the vault, not on itself."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = vault / link_at
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)

    result = capture(vault, _request("exfiltrate me"))

    assert result["status"] == "partial", f"{case}: a blocked projection is not success"
    assert result["errors"][0]["code"] == "PATH_ESCAPES_VAULT"
    assert result["outputs"] == []
    assert _outside_files(outside) == [], f"{case}: zero bytes may be written outside"
    # INV-001: the raw evidence still survives and still verifies.
    assert read_raw_content(vault, result["capture_id"]) == "exfiltrate me"
    record = json.loads(
        (vault / CAPTURE_DIR / f"{result['capture_id']}.json").read_text(encoding="utf-8")
    )
    assert record["content_hash"] == content_hash("exfiltrate me")


def test_default_projection_symlink_chain_fails_closed(vault: Path, tmp_path: Path) -> None:
    """A chained link must not be followed out of the vault either."""
    outside = tmp_path / "outside"
    outside.mkdir()
    middle = tmp_path / "middle"
    middle.symlink_to(outside, target_is_directory=True)
    (vault / "generated" / "obsidian").mkdir(parents=True, exist_ok=True)
    (vault / "generated" / "obsidian" / "captures").symlink_to(middle, target_is_directory=True)

    result = capture(vault, _request("chained"))
    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "PATH_ESCAPES_VAULT"
    assert _outside_files(outside) == []


def test_default_projection_relative_symlink_target_fails_closed(
    vault: Path, tmp_path: Path
) -> None:
    """A relative link target escapes just as effectively; reject it too."""
    outside = tmp_path / "outside"
    outside.mkdir()
    obsidian = vault / "generated" / "obsidian"
    obsidian.mkdir(parents=True, exist_ok=True)
    relative = Path(os.path.relpath(outside, obsidian))
    (obsidian / "captures").symlink_to(relative, target_is_directory=True)

    result = capture(vault, _request("relative"))
    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "PATH_ESCAPES_VAULT"
    assert _outside_files(outside) == []


def test_blocked_default_projection_never_materializes_the_far_side(
    vault: Path, tmp_path: Path
) -> None:
    """The walk must stop *before* descending through a planted link.

    ``mkdir(parents=True)`` on the far side of a symlinked ancestor is already
    a write outside the boundary, even when it only creates a directory.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "generated" / "obsidian").symlink_to(outside, target_is_directory=True)

    capture(vault, _request("no mkdir outside"))

    assert list(outside.iterdir()) == [], "not even an empty directory may be created"


def test_normal_default_projection_still_writes_inside_the_vault(vault: Path) -> None:
    """R1-C: the ordinary path is unaffected by the remediation."""
    result = capture(vault, _request("ordinary"))
    assert result["status"] == "ok"
    note = vault / "generated" / "obsidian" / "captures" / result["outputs"][0]["relative_path"]
    assert note.is_file()
    assert note.resolve().is_relative_to(vault.resolve())


def test_explicit_external_obsidian_root_remains_supported(vault: Path, tmp_path: Path) -> None:
    """R1-D: the documented external opt-in must not become a failure."""
    external = tmp_path / "ExternalObsidianVault"
    external.mkdir()
    result = capture(vault, _request("external opt-in"), obsidian_root=external)
    assert result["status"] == "ok"
    note = next(external.rglob("*.md"))
    assert note.is_relative_to(external)
    assert read_raw_content(vault, result["capture_id"]) == "external opt-in"


def test_materialize_under_root_rejects_a_symlinked_component(tmp_path: Path) -> None:
    """Unit-level: the walk rejects at the link, before creating beyond it."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "a").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes root"):
        materialize_under_root(root, Path("a/b/c"), label="test")
    assert list(outside.iterdir()) == []


def test_materialize_under_root_creates_a_clean_chain(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    created = materialize_under_root(root, Path("a/b/c"), label="test")
    assert created.is_dir()
    assert created.resolve().is_relative_to(root.resolve())
    # Idempotent.
    assert materialize_under_root(root, Path("a/b/c"), label="test") == created


@pytest.mark.parametrize(
    "hostile",
    ["/etc", "C:evil", "../outside", "a/../../b", "con", "trailing.", "trailing "],
)
def test_materialize_under_root_rejects_non_relative_or_unsafe_paths(
    tmp_path: Path, hostile: str
) -> None:
    """Validated with the canonical primitive, so the rules hold on Windows too.

    ``Path("/etc")`` is not ``is_absolute()`` on Windows -- it carries a root
    but no drive -- so an ad-hoc check would let it through there.
    """
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="unsafe test"):
        materialize_under_root(root, Path(hostile), label="test")
    assert list(root.iterdir()) == [], "nothing may be created for a rejected path"


# ---------------------------------------------------------------------------
# Retry human-edit preservation (AT-011).
#
# ``atlas capture retry`` re-renders the same persisted capture. Before this
# fix ``write_note()`` unconditionally replaced the whole file, so any human
# edit made outside the generated region was silently destroyed. This is the
# same contract already shipped for the living project projection
# (``obsidian_projection.py``): content wrapped in a named
# ``<!-- BEGIN HUMAN: name --> ... <!-- END HUMAN: name -->`` block survives
# a re-render byte-for-byte via the shared ``protected_regions`` primitive.
# ---------------------------------------------------------------------------


def _note_path_for(vault: Path, result: dict[str, object]) -> Path:
    outputs = result["outputs"]
    assert isinstance(outputs, list) and outputs
    output = outputs[0]
    return Path(str(output["vault_root"])) / str(output["relative_path"])


def test_render_note_ships_a_human_notes_placeholder(vault: Path) -> None:
    """A discoverable, safe place to write persistent notes exists by default."""
    result = capture(vault, build_capture_request(content="placeholder check"))
    note = _note_path_for(vault, result).read_text(encoding="utf-8")
    assert "<!-- BEGIN HUMAN: notes -->" in note
    assert "<!-- END HUMAN: notes -->" in note


def test_retry_with_unchanged_note_is_safe_and_idempotent(vault: Path) -> None:
    """Retrying without any human edit reproduces byte-identical output."""
    result = capture(vault, build_capture_request(content="idempotent retry"))
    note_path = _note_path_for(vault, result)
    before = note_path.read_bytes()

    retry(vault, result["capture_id"])

    assert note_path.read_bytes() == before


def test_retry_preserves_human_edited_content(vault: Path) -> None:
    """The exact scenario from the P1 finding: a human edit must survive."""
    result = capture(vault, build_capture_request(content="human edit retry"))
    note_path = _note_path_for(vault, result)
    original = note_path.read_text(encoding="utf-8")
    edited = original.replace(
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
        "<!-- BEGIN HUMAN: notes -->\nDo not delete this. -- a human\n<!-- END HUMAN: notes -->",
    )
    assert edited != original, "the placeholder must actually be present to edit"
    note_path.write_text(edited, encoding="utf-8")

    retry(vault, result["capture_id"])

    after = note_path.read_text(encoding="utf-8")
    assert "Do not delete this. -- a human" in after


def test_retry_preserves_human_edit_across_repeated_identical_retries(vault: Path) -> None:
    """Retrying twice more must not erode a preserved human edit."""
    result = capture(vault, build_capture_request(content="repeated retry"))
    note_path = _note_path_for(vault, result)
    original = note_path.read_text(encoding="utf-8")
    note_path.write_text(
        original.replace(
            "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
            "<!-- BEGIN HUMAN: notes -->\nsurvive twice\n<!-- END HUMAN: notes -->",
        ),
        encoding="utf-8",
    )

    retry(vault, result["capture_id"])
    retry(vault, result["capture_id"])

    assert "survive twice" in note_path.read_text(encoding="utf-8")


def test_retry_with_malformed_human_markers_fails_closed(vault: Path) -> None:
    """A corrupted marker pair fails the render stage rather than guessing.

    Rendering failure is isolated, never fatal (INV-007) -- ``retry()``
    returns ``status: "partial"`` with the failure recorded, it does not
    raise. What must never happen is a *silent* rewrite: the merge is
    computed in memory before any write, so a rejected merge never reaches
    ``_write_atomic`` and the existing (corrupted) note is left exactly as
    it was for a human to fix.
    """
    result = capture(vault, build_capture_request(content="malformed markers"))
    note_path = _note_path_for(vault, result)
    original = note_path.read_text(encoding="utf-8")
    # Unbalanced: a BEGIN with no matching END.
    corrupted = original.replace(
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
        "<!-- BEGIN HUMAN: notes -->\nunterminated",
    )
    note_path.write_text(corrupted, encoding="utf-8")

    retried = retry(vault, result["capture_id"])

    assert retried["status"] == "partial"
    assert retried["errors"][0]["code"] == "OBSIDIAN_NOTE_CONFLICT"
    assert note_path.read_text(encoding="utf-8") == corrupted, (
        "a rejected merge must not silently rewrite or drop the existing note"
    )
    assert list(note_path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    ("case", "blocks"),
    [
        ("two", "{a}{b}"),
        ("three", "{a}{b}{c}"),
        ("separated-by-unique", "{a}{u}{b}"),
        ("empty", "{e}{e}"),
        ("identical-content", "{a}{a}"),
        ("unicode-name", "{ua}{ub}"),
        ("marker-whitespace", "{ws}{b}"),
    ],
)
def test_duplicate_human_region_names_fail_closed(case: str, blocks: str) -> None:
    """Duplicate region names are ambiguous identity, so the merge refuses.

    A region's *name* is its identity: the fresh render has one named slot, so
    two blocks with the same name give no way to say which one it refers to.
    Extraction keys blocks by name, so before this check the later block
    silently overwrote the earlier one and a re-render dropped human-authored
    content with no error and no diagnostic -- the exact class of silent human
    data loss this module exists to prevent. Rejecting is the safe reading:
    Atlas cannot guess, so a human resolves the duplicate names.

    Content equality does not make it unambiguous -- the ambiguity is in the
    identity, not the payload -- so the identical-content and empty cases are
    rejected too.
    """
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    existing = (
        GENERATED_START
        + GENERATED_END
        + blocks.format(
            a=human(name="notes", body="FIRST"),
            b=human(name="notes", body="SECOND"),
            c=human(name="notes", body="THIRD"),
            e=human(name="notes", body=""),
            u=human(name="unique", body="KEEP"),
            ua=human(name="\u5099\u8003", body="FIRST"),
            ub=human(name="\u5099\u8003", body="SECOND"),
            ws="<!--  BEGIN HUMAN: notes  -->\nFIRST\n<!-- END HUMAN: notes -->",
        )
    )
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
    )

    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        merge_protected_regions(existing=existing, rendered=rendered, path=f"{case}.md")


def test_duplicate_names_in_the_rendered_template_are_rejected() -> None:
    """The template is not trusted either -- including on a first write."""
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
    )
    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        merge_protected_regions(existing=None, rendered=rendered, path="first.md")

    existing = (
        GENERATED_START
        + GENERATED_END
        + "<!-- BEGIN HUMAN: notes -->\nOLD\n<!-- END HUMAN: notes -->"
    )
    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        merge_protected_regions(existing=existing, rendered=rendered, path="second.md")


def test_extract_human_regions_refuses_duplicates_on_its_own() -> None:
    """The extractor is exported, so it must not drop a block when called alone.

    ``validate_protected_markers`` rejects duplicates first, so this guard is
    unreachable through ``merge_protected_regions``. It exists because silently
    returning one of two same-named blocks is precisely the data loss this
    module prevents, and a future caller may reach the extractor directly.
    """
    text = (
        "<!-- BEGIN HUMAN: notes -->\nFIRST\n<!-- END HUMAN: notes -->"
        "<!-- BEGIN HUMAN: notes -->\nSECOND\n<!-- END HUMAN: notes -->"
    )
    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        extract_human_regions(text)


def test_distinct_region_names_are_unaffected() -> None:
    """The valid path must not become stricter: distinct names still merge."""
    existing = (
        GENERATED_START
        + GENERATED_END
        + "<!-- BEGIN HUMAN: notes -->\nKEEP A\n<!-- END HUMAN: notes -->"
        + "<!-- BEGIN HUMAN: todo -->\nKEEP B\n<!-- END HUMAN: todo -->"
    )
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
        "<!-- BEGIN HUMAN: todo -->\n<!-- END HUMAN: todo -->"
    )
    merged = merge_protected_regions(existing=existing, rendered=rendered, path="ok.md")
    assert "KEEP A" in merged
    assert "KEEP B" in merged
    assert "fresh" in merged


def _HUMAN_BEGIN_NAMES(text: str) -> list[str]:
    """Region names in a fixture, for boundary assertions."""
    return re.findall(r"<!--\s*BEGIN HUMAN:\s*([^\s>]+)\s*-->", text)


@pytest.mark.parametrize(
    ("case", "existing_blocks"),
    [
        ("nested-distinct", "{outer}"),
        ("nested-distinct-deep", "{deep}"),
        ("case-differs-is-distinct", "{cap}{low}"),
        ("siblings-distinct", "{a}{b}"),
    ],
)
def test_duplicate_check_does_not_decide_nesting_policy(case: str, existing_blocks: str) -> None:
    """F1 refuses ambiguous *identity*; it does not rule on nesting.

    Nested regions with distinct names legitimately produce a merged document
    that carries the inner block twice. Whether that is the right behaviour is
    a separate open question, and running the duplicate-name check over the
    *merged* text would answer it by refusing every nested document. So the
    check is applied to the merge's inputs only, and this test pins the
    boundary: these documents must merge exactly as they did before F1.

    Names are compared exactly, matching the identity contract the merge
    itself uses, so ``Notes`` and ``notes`` are two regions, not a duplicate.
    """
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    inner = human(name="inner", body="INNER CONTENT")
    existing = (
        GENERATED_START
        + GENERATED_END
        + existing_blocks.format(
            outer=human(name="outer", body=inner),
            deep=human(name="a", body=human(name="b", body=human(name="c", body="X"))),
            cap=human(name="Notes", body="CAPITALISED"),
            low=human(name="notes", body="lowercase"),
            a=human(name="alpha", body="A"),
            b=human(name="beta", body="B"),
        )
    )
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
    )

    merged = merge_protected_regions(existing=existing, rendered=rendered, path=f"{case}.md")

    # Every distinct name survives; nothing is refused.
    for name in _HUMAN_BEGIN_NAMES(existing):
        assert f"BEGIN HUMAN: {name}" in merged


def test_nested_same_name_is_an_identity_failure_not_a_nesting_ruling() -> None:
    """The one nesting shape F1 does refuse, and why it is still about identity."""
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
    )
    nested_same = (
        GENERATED_START
        + GENERATED_END
        + human(name="notes", body=human(name="notes", body="INNER"))
    )
    # Structural pairing catches this before identity does: nothing in the
    # marker text says which END closes which BEGIN, so the document has no
    # single valid reading. Either way it fails closed and the note is
    # untouched -- the code is more precise, the verdict is unchanged.
    with pytest.raises(ProtectedRegionError, match="ambiguous-protected-region-nesting"):
        merge_protected_regions(existing=nested_same, rendered=rendered, path="nested-same.md")

    # ...while the same shape with distinct names is accepted, so the refusal
    # is keyed on the repeated name and not on the nesting itself.
    nested_distinct = (
        GENERATED_START
        + GENERATED_END
        + human(name="outer", body=human(name="inner", body="INNER"))
    )
    merge_protected_regions(existing=nested_distinct, rendered=rendered, path="nested-distinct.md")


@pytest.mark.parametrize(
    ("case", "blocks"),
    [
        ("siblings-inside-a-container", "{outer_dup}"),
        ("siblings-two-levels-down", "{deep_dup}"),
        ("container-dup-beside-a-unique-top-level", "{unique}{outer_dup}"),
    ],
)
def test_same_name_siblings_are_ambiguous_at_every_depth(case: str, blocks: str) -> None:
    """Sibling ambiguity is scoped, not top-level-only.

    Regression for a fail-open: the containment walk compared sibling names
    only at the root, so two same-name blocks sitting inside a
    differently-named container were accepted. `extract_human_regions` keys by
    name, so one of them was then silently dropped -- the exact human-content
    loss this module exists to prevent, just one level down from where it was
    being looked for.
    """
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    dup_pair = human(name="notes", body="FIRST") + human(name="notes", body="SECOND")
    existing = (
        GENERATED_START
        + GENERATED_END
        + blocks.format(
            outer_dup=human(name="outer", body=dup_pair),
            deep_dup=human(name="a", body=human(name="b", body=dup_pair)),
            unique=human(name="solo", body="KEEP"),
        )
    )
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->"
    )

    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        merge_protected_regions(existing=existing, rendered=rendered, path=f"{case}.md")
    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        extract_human_regions(existing)


def test_the_same_name_in_two_different_scopes_is_preserved_independently() -> None:
    """F2-01: scope is part of identity, so `a/x` and `b/x` are two regions.

    This test previously pinned the opposite: it asserted `ONE` was *lost*,
    because name-keyed resolution collapsed both blocks into one dictionary
    slot, the last one parsed won, and the merge then spliced the survivor
    into the earlier container. That was a real human-content loss recorded as
    the open F2 question; the owner decision resolves it in favour of
    structural identity, so the assertion is inverted here deliberately.

    Both payloads must survive, and each must stay in its own container --
    preservation alone is not enough if the bytes land in the wrong scope.
    """
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    existing = (
        f"{GENERATED_START}\nold\n{GENERATED_END}\n"
        + human(name="a", body=human(name="x", body="ONE"))
        + human(name="b", body=human(name="x", body="TWO"))
    )
    rendered = (
        f"{GENERATED_START}\nfresh\n{GENERATED_END}\n"
        + human(name="a", body=human(name="x", body=""))
        + human(name="b", body=human(name="x", body=""))
    )

    merged = merge_protected_regions(existing=existing, rendered=rendered, path="ok.md")

    assert "fresh" in merged
    assert merged.count("ONE") == 1
    assert merged.count("TWO") == 1
    inside_a = merged.split("<!-- BEGIN HUMAN: a -->")[1].split("<!-- END HUMAN: a -->")[0]
    inside_b = merged.split("<!-- BEGIN HUMAN: b -->")[1].split("<!-- END HUMAN: b -->")[0]
    assert "ONE" in inside_a and "TWO" not in inside_a
    assert "TWO" in inside_b and "ONE" not in inside_b


# ---------------------------------------------------------------------------
# F2 acceptance matrix. Region identity is ancestry scope + name, so `a/x` and
# `b/x` are independent regions; same-scope repeats and structurally unpairable
# nesting fail closed, and nothing is written until the whole plan resolves.
# ---------------------------------------------------------------------------

_H = "<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->".format


def _doc(body: str, generated: str = "generated") -> str:
    return f"{GENERATED_START}\n{generated}\n{GENERATED_END}\n" + body


def _scope(text: str, name: str) -> str:
    """The bytes between a container's own markers."""
    return text.split(f"<!-- BEGIN HUMAN: {name} -->")[1].split(f"<!-- END HUMAN: {name} -->")[0]


def test_f2_04_same_leaf_name_at_different_scopes_are_distinct_identities() -> None:
    """F2-04: ancestry is part of the key, so `x` and `a/x` never share a slot."""
    existing = _doc(_H(name="x", body="TOP") + _H(name="a", body=_H(name="x", body="NESTED")))

    regions = extract_human_regions(existing)

    assert set(regions) == {("x",), ("a",), ("a", "x")}
    assert "TOP" in regions[("x",)]
    assert "NESTED" in regions[("a", "x")]


def test_f2_07_a_human_edit_lands_only_in_the_scope_it_was_made_in() -> None:
    """F2-07: editing `a/x` must not touch `b/x`."""
    rendered = _doc(
        _H(name="a", body=_H(name="x", body="")) + _H(name="b", body=_H(name="x", body="")),
        generated="fresh",
    )
    edited = _doc(
        _H(name="a", body=_H(name="x", body="ONE, edited by a human"))
        + _H(name="b", body=_H(name="x", body="TWO"))
    )

    merged = merge_protected_regions(existing=edited, rendered=rendered, path="edit.md")

    assert "ONE, edited by a human" in _scope(merged, "a")
    assert "edited by a human" not in _scope(merged, "b")
    assert "TWO" in _scope(merged, "b")


def test_f2_08_generated_changes_leave_both_scopes_byte_preserved() -> None:
    """F2-08: rewriting the generated span must not disturb either region."""
    existing = _doc(
        _H(name="a", body=_H(name="x", body="ONE")) + _H(name="b", body=_H(name="x", body="TWO"))
    )
    rendered = _doc(
        _H(name="a", body=_H(name="x", body="")) + _H(name="b", body=_H(name="x", body="")),
        generated="COMPLETELY DIFFERENT GENERATED BODY",
    )

    merged = merge_protected_regions(existing=existing, rendered=rendered, path="gen.md")

    assert "COMPLETELY DIFFERENT GENERATED BODY" in merged
    assert _H(name="x", body="ONE") in merged
    assert _H(name="x", body="TWO") in merged


def test_f2_09_one_ambiguity_aborts_the_whole_merge() -> None:
    """F2-09: a resolvable region alongside an ambiguous one changes nothing.

    The plan is resolved and validated before any byte is written, so the
    caller cannot be handed a note where `solo` was updated and the ambiguous
    container was left behind.
    """
    existing = _doc(
        _H(name="solo", body="KEEP ME")
        + _H(name="a", body=_H(name="x", body="1") + _H(name="x", body="2"))
    )
    rendered = _doc(_H(name="solo", body=""), generated="fresh")
    before = existing

    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        merge_protected_regions(existing=existing, rendered=rendered, path="abort.md")

    assert existing == before


def test_f2_10_reordering_sibling_containers_moves_nothing_between_them() -> None:
    """F2-10: position locates a span; it is never the identity."""
    existing = _doc(
        _H(name="a", body=_H(name="x", body="ONE")) + _H(name="b", body=_H(name="x", body="TWO"))
    )
    reordered = _doc(
        _H(name="b", body=_H(name="x", body="")) + _H(name="a", body=_H(name="x", body="")),
        generated="fresh",
    )

    merged = merge_protected_regions(existing=existing, rendered=reordered, path="order.md")

    assert "ONE" in _scope(merged, "a") and "TWO" not in _scope(merged, "a")
    assert "TWO" in _scope(merged, "b") and "ONE" not in _scope(merged, "b")


@pytest.mark.parametrize(
    ("case", "existing_body"),
    [
        ("same-scope-siblings", "{dup}"),
        ("same-scope-inside-a-container", "{outer_dup}"),
        ("crossed-markers", "{crossed}"),
    ],
)
def test_f2_structurally_ambiguous_documents_fail_closed(case: str, existing_body: str) -> None:
    """F2-02/F2-05/section 3: no unique identity means no write."""
    existing = _doc(
        existing_body.format(
            dup=_H(name="x", body="1") + _H(name="x", body="2"),
            outer_dup=_H(name="a", body=_H(name="x", body="1") + _H(name="x", body="2")),
            crossed=(
                "<!-- BEGIN HUMAN: a -->\n<!-- BEGIN HUMAN: b -->\n"
                "<!-- END HUMAN: a -->\n<!-- END HUMAN: b -->"
            ),
        )
    )
    rendered = _doc(_H(name="notes", body=""), generated="fresh")

    with pytest.raises(ProtectedRegionError):
        merge_protected_regions(existing=existing, rendered=rendered, path=f"{case}.md")


def test_f2_06_identities_stay_stable_across_repeated_renders() -> None:
    """F2-06: six generations, both scopes byte-preserved and shape settled."""
    document = _doc(
        _H(name="a", body=_H(name="x", body="ONE")) + _H(name="b", body=_H(name="x", body="TWO"))
    )
    rendered = _doc(
        _H(name="a", body=_H(name="x", body="")) + _H(name="b", body=_H(name="x", body="")),
        generated="fresh",
    )

    seen = []
    for generation in range(6):
        document = merge_protected_regions(
            existing=document, rendered=rendered, path=f"gen{generation}.md"
        )
        seen.append(document)
        assert document.count("ONE") == 1, f"ONE duplicated or lost at R{generation}"
        assert document.count("TWO") == 1, f"TWO duplicated or lost at R{generation}"
        assert "ONE" in _scope(document, "a")
        assert "TWO" in _scope(document, "b")

    assert len(set(seen)) == 1, "merge is not idempotent across generations"


def test_retry_leaves_a_duplicate_region_note_byte_identical(vault: Path) -> None:
    """End-to-end: ambiguity must cost the human nothing.

    Before the fix this retry rewrote the note and the first block's content
    was gone. The contract now matches the malformed-marker case above: a
    rejected merge never reaches ``_write_atomic``, so the note stays exactly
    as the human left it and the raw evidence is untouched.
    """
    result = capture(vault, build_capture_request(content="body under duplicates"))
    note_path = _note_path_for(vault, result)
    raw_before = read_raw_content(vault, result["capture_id"])
    duplicated = note_path.read_text(encoding="utf-8").replace(
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
        "<!-- BEGIN HUMAN: notes -->\nFIRST BLOCK\n<!-- END HUMAN: notes -->\n"
        "<!-- BEGIN HUMAN: notes -->\nSECOND BLOCK\n<!-- END HUMAN: notes -->",
    )
    note_path.write_text(duplicated, encoding="utf-8")

    retried = retry(vault, result["capture_id"])

    assert retried["status"] == "partial"
    assert retried["errors"][0]["code"] == "OBSIDIAN_NOTE_CONFLICT"
    assert note_path.read_text(encoding="utf-8") == duplicated
    assert "FIRST BLOCK" in duplicated and "SECOND BLOCK" in duplicated
    assert read_raw_content(vault, result["capture_id"]) == raw_before
    assert list(note_path.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("workers", [8, 16])
def test_concurrent_retries_never_silently_drop_a_duplicate_block(
    vault: Path, workers: int
) -> None:
    """Racing retries must not resolve the ambiguity by accident.

    Run at both widths: the ambiguity is decided per-merge, so a wider race is
    not merely more of the same -- it is more chances for two retries to
    interleave between the read and the (refused) write.
    """
    result = capture(vault, build_capture_request(content="body under a race"))
    note_path = _note_path_for(vault, result)
    duplicated = note_path.read_text(encoding="utf-8").replace(
        "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
        "<!-- BEGIN HUMAN: notes -->\nFIRST BLOCK\n<!-- END HUMAN: notes -->\n"
        "<!-- BEGIN HUMAN: notes -->\nSECOND BLOCK\n<!-- END HUMAN: notes -->",
    )
    note_path.write_text(duplicated, encoding="utf-8")

    sha_before = hashlib.sha256(note_path.read_bytes()).hexdigest()
    barrier = threading.Barrier(workers)
    # A thread that raises does not fail its parent, so collect outcomes and
    # assert on them. Without this the test passes even if every retry blew up,
    # which would make the byte-stability assertion below vacuous. The barrier
    # and joins are bounded so a deadlock fails the test instead of hanging the
    # suite.
    outcomes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def _retry() -> None:
        try:
            barrier.wait(timeout=30)
            outcomes.append(retry(vault, result["capture_id"]))
        except BaseException as exc:  # re-asserted on the main thread below
            failures.append(exc)

    threads = [threading.Thread(target=_retry) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "concurrent retry deadlocked"

    assert failures == [], f"retry raised in a worker thread: {failures!r}"
    assert len(outcomes) == workers
    # Every racing retry must refuse the ambiguity -- none may "win".
    for outcome in outcomes:
        assert outcome["status"] == "partial"
        assert outcome["errors"][0]["code"] == "OBSIDIAN_NOTE_CONFLICT"  # type: ignore[index]

    assert hashlib.sha256(note_path.read_bytes()).hexdigest() == sha_before
    assert note_path.read_text(encoding="utf-8") == duplicated
    assert "FIRST BLOCK" in duplicated and "SECOND BLOCK" in duplicated
    assert list(note_path.parent.glob("*.tmp")) == []
    assert list(note_path.parent.glob("*.partial")) == []


def test_retry_after_source_disappears_still_preserves_human_edit(vault: Path) -> None:
    """Retry reloads raw evidence independently; the human edit still survives.

    Exercises the general contract (retry never depends on anything but the
    persisted raw evidence and the existing note) alongside human-edit
    preservation in the same call.
    """
    result = capture(vault, build_capture_request(content="reload from store"))
    note_path = _note_path_for(vault, result)
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(
            "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
            "<!-- BEGIN HUMAN: notes -->\nreloaded fine\n<!-- END HUMAN: notes -->",
        ),
        encoding="utf-8",
    )

    retried = retry(vault, result["capture_id"])

    assert retried["status"] == "ok"
    assert "reloaded fine" in note_path.read_text(encoding="utf-8")


def test_concurrent_retries_never_corrupt_the_note(vault: Path) -> None:
    """Concurrent retries race on one file; the result is always one whole,
    valid render -- never a torn or mixed write (last-writer-wins is
    acceptable, corruption is not; concurrent-signal linearizability is
    NOT_CONTRACTED, matching the capture-race contract elsewhere)."""
    import threading

    result = capture(vault, build_capture_request(content="concurrent retry"))
    note_path = _note_path_for(vault, result)
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(
            "<!-- BEGIN HUMAN: notes -->\n<!-- END HUMAN: notes -->",
            "<!-- BEGIN HUMAN: notes -->\nmust survive the race\n<!-- END HUMAN: notes -->",
        ),
        encoding="utf-8",
    )

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=15)
            retry(vault, result["capture_id"])
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"concurrent retry raised: {errors}"
    final = note_path.read_text(encoding="utf-8")
    assert "must survive the race" in final
    assert final.count("<!-- BEGIN HUMAN: notes -->") == 1
    assert list(note_path.parent.glob("*.tmp")) == []


def test_p1_symlink_containment_still_holds_after_retry_fix(vault: Path, tmp_path: Path) -> None:
    """The retry fix must not reopen the default-projection symlink escape.

    A projection failure is isolated, never fatal (INV-007): ``capture()``
    returns ``status: "partial"`` with ``PATH_ESCAPES_VAULT`` recorded, it
    does not raise. What matters is that raw evidence is preserved and zero
    bytes land outside the vault.
    """
    stolen = tmp_path / "stolen"
    stolen.mkdir()
    (vault / "generated" / "obsidian").symlink_to(stolen, target_is_directory=True)

    result = capture(vault, build_capture_request(content="containment still holds"))

    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "PATH_ESCAPES_VAULT"
    assert [p for p in stolen.rglob("*") if p.is_file()] == []


def test_dedupe_rejects_a_symlinked_record_path(vault: Path, tmp_path: Path) -> None:
    """The dedupe read must not follow a planted symlink out of the vault."""
    result = capture(vault, _request("dedupe target"))
    record = vault / CAPTURE_DIR / f"{result['capture_id']}.json"
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"identity_hash": "sha256:" + "0" * 64}), encoding="utf-8")
    record.unlink()
    record.symlink_to(outside)

    with pytest.raises(CaptureError) as excinfo:
        capture(vault, _request("dedupe target"))
    assert excinfo.value.code == "PATH_ESCAPES_VAULT"


def test_unencodable_content_fails_with_a_stable_code() -> None:
    """Unpaired surrogates must not escape as a raw UnicodeEncodeError."""
    with pytest.raises(CaptureSourceError) as excinfo:
        build_capture_request(content="lead \ud800 surrogate")
    assert excinfo.value.code == "CONTENT_NOT_ENCODABLE"


@pytest.mark.parametrize("content", ["lead \ud800 surrogate", "trail \udfff", "\ud800\udfff"])
def test_service_rejects_unencodable_content_before_hashing(vault: Path, content: str) -> None:
    """AS-OBSIDIAN-CAPTURE-001 R3: direct requests use stable errors too."""
    request = replace(_request("valid construction"), content=content)
    before = {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*") if p.is_file()}

    with pytest.raises(CaptureError) as excinfo:
        capture(vault, request)

    assert excinfo.value.code == "MALFORMED_REQUEST"
    assert {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*") if p.is_file()} == before


def test_ai_enrichment_true_is_rejected_rather_than_silently_ignored() -> None:
    """No capture path reads this flag; accepting it would claim a capability."""
    from pydantic import ValidationError

    from project_atlas.config import AtlasConfig

    AtlasConfig.model_validate({"capture": {"processing": {"ai_enrichment": False}}})
    with pytest.raises(ValidationError, match="not implemented"):
        AtlasConfig.model_validate({"capture": {"processing": {"ai_enrichment": True}}})


def test_nested_distinct_names_survive_repeated_renders_like_pre_f1() -> None:
    """R0..R3: the scope leak. A single first render is not enough to see it.

    Nested distinct-name regions merge cleanly the first time, and the merge
    itself emits the inner block a second time alongside the outer block that
    already contains it. That merged document is the *next* render's input, so
    a duplicate-name check counting raw occurrences refused, at R2, a document
    the merge had just produced. Pre-F1 the same sequence rendered
    indefinitely.

    Independently reproduced against sealed main before the fix: R1 identical
    on both trees, R2 refused on the candidate and fine on main. F1 must not
    change nested distinct-name behaviour at any generation -- that is F2's
    question, and refusing here would answer it by side effect.
    """
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    document = (
        f"{GENERATED_START}\ngenerated v0\n{GENERATED_END}\n"
        + human(name="outer", body="outer text\n" + human(name="inner", body="INNER"))
        + "\n"
    )

    for generation in range(1, 4):
        document = merge_protected_regions(
            existing=document,
            rendered=f"{GENERATED_START}\ngenerated v{generation}\n{GENERATED_END}\n",
            path="nested.md",
        )
        # Human payload is never lost, at any generation.
        assert "INNER" in document, f"inner content lost at R{generation}"
        assert "outer text" in document, f"outer content lost at R{generation}"
        # Under structural identity the inner block is carried by its parent
        # rather than appended alongside it, so it appears once. Pre-F2 it
        # appeared twice: name-keyed resolution treated `inner` as a separate
        # top-level region and re-emitted it next to the `outer` block that
        # already contained it. Duplication was not loss, but it was spurious
        # content the human never wrote.
        assert document.count("<!-- BEGIN HUMAN: outer -->") == 1
        assert document.count("<!-- BEGIN HUMAN: inner -->") == 1, (
            f"nested-distinct shape changed at R{generation}"
        )
        assert document.count("INNER") == 1


def test_nested_distinct_document_is_stable_after_the_first_merge() -> None:
    """The merged document is a fixed point once the generated body settles.

    Guards the other direction: the fix must not make each render accumulate
    another copy of the inner block, which would be silent growth rather than
    a refusal.
    """
    human = ("<!-- BEGIN HUMAN: {name} -->\n{body}\n<!-- END HUMAN: {name} -->").format
    rendered = f"{GENERATED_START}\nstable\n{GENERATED_END}\n"
    document = rendered + human(name="outer", body="o\n" + human(name="inner", body="I")) + "\n"

    first = merge_protected_regions(existing=document, rendered=rendered, path="n.md")
    second = merge_protected_regions(existing=first, rendered=rendered, path="n.md")
    third = merge_protected_regions(existing=second, rendered=rendered, path="n.md")

    assert second == first, "merge is not idempotent for nested distinct names"
    assert third == second


def test_duplicate_detection_stays_linear_in_region_count() -> None:
    """The ambiguity check runs on every merge, so it must not go superlinear.

    An earlier structural version compared every span against every other:
    O(n^2) typical, O(n^3) worst case. Independent verification measured 400
    same-name regions in a 22 KB note at 7.2 seconds. Containment is resolved
    with a stack over spans in document order instead, so each span is pushed
    and popped once.

    The bound is deliberately loose -- this is a guard against an algorithmic
    class, not a benchmark. The quadratic version needed minutes at this size;
    the linear one needs milliseconds, so ordinary CI noise cannot decide it.
    """
    regions = 2000
    document = (
        f"{GENERATED_START}\ngenerated\n{GENERATED_END}\n"
        + "<!-- BEGIN HUMAN: dup -->\nx\n<!-- END HUMAN: dup -->\n" * regions
    )

    started = time.perf_counter()
    with pytest.raises(ProtectedRegionError, match="duplicate-protected-region-names"):
        merge_protected_regions(existing=document, rendered=document, path="big.md")
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, (
        f"duplicate detection took {elapsed:.1f}s for {regions} regions; "
        "the quadratic implementation needed minutes at this size"
    )

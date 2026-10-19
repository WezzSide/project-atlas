"""#726 — openai_import_real source_sha256 must verify the named file.

``source_sha256`` hashed ``read_text()`` output (universal newlines), so a
CRLF export could never be re-hashed from ``source_path``. ``source_bytes``
came from ``stat().st_size`` (untranslated), so the receipt could also
disagree with itself.

chatgpt_bridge.py has the same defect but remains OWNER_ONLY: it is a
DENY-listed certified 2.x surface under test_atlas3_demo_isolation_001.
This package remediates the unfrozen importer only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from project_atlas.openai_import_real import import_openai_export


def test_crlf_export_digest_matches_on_disk_bytes(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "chat-export.md"
    raw = b"User: hello\r\nAssistant: world\r\n"
    export.write_bytes(raw)
    report = import_openai_export(vault, export, import_id="imp-crlf")
    assert report["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["source_bytes"] == len(raw)
    assert report["source_sha256"] == hashlib.sha256(export.read_bytes()).hexdigest()


def test_lf_export_digest_still_matches(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    export = tmp_path / "chat-export.md"
    raw = b"User: hello\nAssistant: world\n"
    export.write_bytes(raw)
    report = import_openai_export(vault, export, import_id="imp-lf")
    assert report["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["source_bytes"] == len(raw)
    assert report["turn_count"] == 2

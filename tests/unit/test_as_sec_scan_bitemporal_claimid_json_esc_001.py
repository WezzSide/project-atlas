"""AS-SEC-SCAN-BITEMPORAL-CLAIMID-JSON-ESC-001 — decoded claim ids must not persist."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.bitemporal import (
    BitemporalError,
    ClaimValidityWindow,
    normalize_validity_window,
    write_validity_catalog,
)
from project_atlas.bitemporal_catalog import write_project_validity_catalog
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def _catalog_text(vault: Path, catalog_id: str = "harbor-api") -> str:
    path = vault / "generated" / "ops" / "bitemporal" / f"{catalog_id}-validity-catalog.json"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_json_unicode_escape_claim_id_is_not_persisted_in_catalog(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    src = vault / "sources" / "imported-documents" / "src-t.md"
    src.parent.mkdir(parents=True)
    src.write_text("timestamp: 2026-01-01T00:00:00+00:00\nbody\n", encoding="utf-8")
    claims = vault / "state" / "claims" / "harbor-api.json"
    claims.parent.mkdir(parents=True)
    raw = (
        "{\n"
        '  "claims": [{\n'
        f'    "claim_id": "{ESC}",\n'
        '    "subject": "datastore",\n'
        '    "field": "engine",\n'
        '    "value": "postgres",\n'
        '    "provenance": [{\n'
        '      "resource": "sources/imported-documents/src-t.md",\n'
        '      "source_id": "src-t"\n'
        "    }]\n"
        "  }]\n"
        "}\n"
    )
    claims.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    result = write_project_validity_catalog(vault, "harbor-api")
    assert result is None
    written = _catalog_text(vault)
    assert TOKEN not in written
    assert scan_text(written) == []


def test_json_unicode_escape_compilation_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    src = vault / "sources" / "imported-documents" / "src-t.md"
    src.parent.mkdir(parents=True)
    src.write_text("timestamp: 2026-01-01T00:00:00+00:00\nbody\n", encoding="utf-8")
    state = vault / "state" / "current-state" / "harbor-api.json"
    state.parent.mkdir(parents=True)
    raw_state = "{\n" + f'  "compilation_id": "{ESC}"\n' + "}\n"
    state.write_text(raw_state, encoding="utf-8")
    assert scan_text(raw_state) == []
    claims = vault / "state" / "claims" / "harbor-api.json"
    claims.parent.mkdir(parents=True)
    claims.write_text(
        (
            "{\n"
            '  "claims": [{\n'
            '    "claim_id": "claim.safe",\n'
            '    "subject": "datastore",\n'
            '    "field": "engine",\n'
            '    "value": "postgres",\n'
            '    "provenance": [{\n'
            '      "resource": "sources/imported-documents/src-t.md",\n'
            '      "source_id": "src-t"\n'
            "    }]\n"
            "  }]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    result = write_project_validity_catalog(vault, "harbor-api")
    assert result is not None
    written = _catalog_text(vault)
    assert TOKEN not in written
    assert scan_text(written) == []
    assert "unbound-compilation" in written


def test_normalize_rejects_secret_claim_id() -> None:
    window = ClaimValidityWindow(
        claim_id=TOKEN,
        valid_from="2026-01-01T00:00:00+00:00",
        knowledge_compilation_id="compile-1",
        evidence_kind="document-declared",
        core005_temporal_status="current",
    )
    with pytest.raises(BitemporalError, match="bitemporal-claim-id-secret"):
        normalize_validity_window(window)


def test_write_catalog_rejects_secret_catalog_id(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    window = ClaimValidityWindow(
        claim_id="claim.safe",
        valid_from="2026-01-01T00:00:00+00:00",
        knowledge_compilation_id="compile-1",
        evidence_kind="document-declared",
        core005_temporal_status="current",
    )
    with pytest.raises(BitemporalError, match="bitemporal-catalog-id-secret"):
        write_validity_catalog(vault, [window], catalog_id=TOKEN)
    written = _catalog_text(vault, TOKEN)
    assert written == ""

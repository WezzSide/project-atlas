"""AS-INTEL-MIXED-CORRUPT-CLAIMS-001 — mixed valid+corrupt is not healthy.

``load_assessable_claims`` / ``load_validity_windows`` skipped non-dict or
incomplete claim rows and still returned OBSERVED / VALID_EMPTY. Core
``query_knowledge`` / ``kdiff`` fail closed on the same shape. Intelligence
must not silently filter corruption.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.web_api.intelligence import (
    HonestyClass,
    WebIntelligenceError,
    load_assessable_claims,
    load_validity_windows,
    read_intelligence_evidence,
)

HASH_A = "a" * 64


def _honest(project_id: str = "harbor-api") -> dict[str, object]:
    return {
        "claim_id": "clm-honest",
        "project_id": project_id,
        "subject": f"project:{project_id}",
        "field": "datastore",
        "value": "PostgreSQL 15",
        "claim_type": "architecture-statement",
        "authority": "primary",
        "confidence": "high",
        "lifecycle": "new",
        "provenance": [
            {"source_id": "src-a", "resource": "docs/src-a.md", "sha256": HASH_A}
        ],
    }


def _write_claims(vault: Path, project_id: str, claims: list[object]) -> None:
    root = vault / "state" / "claims"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{project_id}.json").write_text(
        json.dumps({"schema_version": 1, "project_id": project_id, "claims": claims}),
        encoding="utf-8",
    )


def test_mixed_non_dict_claim_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_honest(), "not-a-claim"])
    with pytest.raises(WebIntelligenceError) as exc_info:
        load_assessable_claims(vault, "harbor-api")
    assert exc_info.value.honesty is HonestyClass.MALFORMED_INPUT


def test_incomplete_claim_row_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    leak = {
        "project_id": "harbor-api",
        "subject": "project:harbor-api",
        "field": "datastore",
        "value": "FOREIGN-LEAK-POSTGRES-99",
    }
    _write_claims(vault, "harbor-api", [_honest(), leak])
    with pytest.raises(WebIntelligenceError) as exc_info:
        load_assessable_claims(vault, "harbor-api")
    assert exc_info.value.honesty is HonestyClass.MALFORMED_INPUT
    with pytest.raises(WebIntelligenceError):
        read_intelligence_evidence(vault, "harbor-api")


def test_honest_claims_still_load(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_honest()])
    loaded = load_assessable_claims(vault, "harbor-api")
    assert [claim.claim_id for claim in loaded] == ["clm-honest"]
    windows = load_validity_windows(vault, "harbor-api")
    assert windows == ()


def test_mixed_validity_window_source_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _write_claims(vault, "harbor-api", [_honest(), "not-a-claim"])
    with pytest.raises(WebIntelligenceError) as exc_info:
        load_validity_windows(vault, "harbor-api")
    assert exc_info.value.honesty is HonestyClass.MALFORMED_INPUT

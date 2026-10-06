"""AS-SEC-SCAN-SCHEMA-COMPAT-JSON-ESC-001 — decoded schema ids must not persist."""

from __future__ import annotations

import json
from pathlib import Path

from project_atlas.schema_compat import ScanTarget, build_report
from project_atlas.secrets import scan_text

TOKEN = "AKIAAAAAAAAAAAAAAAAA"
ESC = r"\u0041KIAAAAAAAAAAAAAAAAA"


def test_json_unicode_escape_schema_id_is_not_persisted(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    raw = f'{{"schema": "{ESC}", "schema_version": 1}}\n'
    path = vault / "generated" / "ops" / "event-tombstones.json"
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8")
    assert scan_text(raw) == []
    assert json.loads(raw)["schema"] == TOKEN
    report = build_report(
        vault,
        targets=[
            ScanTarget(
                "generated/ops/event-tombstones.json",
                "event-tombstone-index",
            )
        ],
        write=True,
    )
    written = (vault / "generated" / "ops" / "schema-compat-report.json").read_text(
        encoding="utf-8"
    )
    assert TOKEN not in written
    assert scan_text(written) == []
    finding = report["findings"][0]
    assert finding.get("from_schema") != TOKEN

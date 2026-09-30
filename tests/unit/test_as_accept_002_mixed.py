"""AS-ACCEPT-002 Band A — MIX reconciled CLI + partial evidence honesty.

Wave-A2 P0: AX2-MIX-006, AX2-MIX-007.
INV-A09 / INV-A01 / INV-A04 / INV-A05 / INV-A06.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit._as_accept_002_helpers import (
    materialize_knowledge_vault,
    signal_map,
    write_json,
)

from project_atlas.cli import main as cli_main
from project_atlas.knowledge_query import answer_to_json, query_knowledge
from project_atlas.ops_health import build_health_snapshot


def test_ax2_mix_006_reconciled_cli_exposes_diag_failure_and_ops_health(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AX2-MIX-006: tip exposes diagnostic failure stdout AND ops health.

    INV-A09 / reconcile — single tip keeps both CLI intents; success firewall holds.
    """
    vault = materialize_knowledge_vault(tmp_path)

    # Success path remains 007 answer JSON (firewall).
    lib_ok = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    code_ok = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:AS-ID-001",
            "--field",
            "title",
            "--kind",
            "authoritative",
        ]
    )
    assert code_ok == 0
    out_ok = capsys.readouterr().out
    assert out_ok == lib_ok
    assert json.loads(out_ok)["package"] == "AS-CORE-007"

    # Integrity failure → DIAG envelope on stdout.
    auth_path = vault / "state" / "authoritative-state" / "project-atlas.json"
    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["compilation_id"] = "compile-mix006-drift"
    auth_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    code_fail = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:AS-ID-001",
            "--field",
            "title",
        ]
    )
    assert code_fail == 1
    fail_payload = json.loads(capsys.readouterr().out)
    assert fail_payload["package"] == "AS-QUERY-DIAG-001"
    assert fail_payload["outcome_class"] == "integrity_failure"
    assert "value" not in fail_payload

    # Fresh vault for ops health (authoritative state above is drifted).
    ops_vault = materialize_knowledge_vault(tmp_path / "ops")
    code_health = cli_main(["ops", "health", "--vault", str(ops_vault), "--json", "--no-write"])
    assert code_health == 0
    health = json.loads(capsys.readouterr().out)
    assert health["schema"] == "atlas.ops.health_snapshot.v1"
    assert health["authority_plane"] == "none"
    assert health["truth_plane"] == "operational"


def test_ax2_mix_007_partial_evidence_honesty_with_authoritative_claims(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AX2-MIX-007: empty promotion + absent query-diagnostics + claims present.

    SIG-005 ok/0; SIG-009 unknown; query answer quiet; estate ≠ healthy solely
    from claims existing (INV-A05 / INV-A06 / INV-A09).
    """
    vault = materialize_knowledge_vault(tmp_path)
    write_json(vault / "quarantine" / "promotion-failures" / "index.json", [])
    # Explicitly omit generated/ops/evidence/query-diagnostics.json

    snapshot = build_health_snapshot(vault)
    signals = signal_map(snapshot)
    assert signals["OPS-SIG-005"]["status"] == "ok"
    assert signals["OPS-SIG-005"]["observed_value"] == 0
    assert signals["OPS-SIG-005"]["evidence_refs"]
    # Absent query-diagnostics evidence → unknown (recommended signal when absent).
    if "OPS-SIG-009" in signals:
        assert signals["OPS-SIG-009"]["status"] == "unknown"
    assert snapshot["rollup"]["estate"] != "healthy"
    assert snapshot["authority_plane"] == "none"

    lib = answer_to_json(
        query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    )
    code = cli_main(
        [
            "query",
            "--vault",
            str(vault),
            "--project",
            "project-atlas",
            "--subject",
            "wp:AS-ID-001",
            "--field",
            "title",
            "--kind",
            "authoritative",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out == lib
    payload = json.loads(out)
    assert payload["package"] == "AS-CORE-007"
    assert "outcome_class" not in payload
    # Claims existence must not launder estate.
    assert (vault / "state" / "claims" / "project-atlas.json").is_file()
    assert snapshot["rollup"]["estate"] != "healthy"

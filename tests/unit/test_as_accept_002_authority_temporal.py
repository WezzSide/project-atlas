"""AS-ACCEPT-002 Band A — ATF authority/temporal fail-closed vs ops/diag.

Wave-A2 P0: AX2-ATF-001, AX2-ATF-004.
INV-A07 / INV-A08 — re-invoke ACCEPT-001; do not amend Wave-A modules.
AX-AUTH-005 consume integrity is owned by AS-CORE-007 (query/validate fail-closed).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit import test_as_accept_001_temporal as accept_001_temporal
from tests.unit._as_accept_002_helpers import (
    hash_tree,
    materialize_knowledge_vault,
    truth_plane_paths,
)

from project_atlas.cli import main as cli_main
from project_atlas.knowledge_query import (
    query_diagnostic_from_answer,
    query_knowledge,
)
from project_atlas.ops_health import build_health_snapshot, emit_health_snapshot


def test_ax2_atf_001_historical_genesis_not_resurrected_on_combined_tip() -> None:
    """AX2-ATF-001: re-run AX-TMP-010 spirit after DIAG+OBS land.

    INV-A08 — ACCEPT-001 temporal fail-closed remains true; historical genesis
    not resurrected by authority (health/diag consumers covered in ATF-004).
    """
    # Re-invoke certified Wave-A node without amending ACCEPT-001 modules.
    # Call via module attr so pytest does not collect the imported test_* name.
    accept_001_temporal.test_ax_tmp_010_historical_genesis_not_resurrected_by_authority()


def test_ax2_atf_004_ops_and_diag_do_not_rewrite_truth_planes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AX2-ATF-004: health snapshot + query diag leave truth-plane bytes unchanged.

    INV-A07 — HEALTH/DIAG never Layer-B / authority / temporal writers;
    authority_plane: none.
    """
    vault = materialize_knowledge_vault(tmp_path)
    probes = truth_plane_paths(vault)
    assert probes, "expected authoritative/claims/current state files"
    before_bytes = {path: path.read_bytes() for path in probes}
    before_mtime = {path: path.stat().st_mtime_ns for path in probes}
    before_tree = hash_tree(vault / "state")

    answer = query_knowledge(vault, "project-atlas", "wp:AS-ID-001", "title", kind="authoritative")
    query_diagnostic_from_answer(answer)
    snapshot = build_health_snapshot(vault)
    assert snapshot["authority_plane"] == "none"
    assert snapshot["truth_plane"] == "operational"
    emit_health_snapshot(vault, persist=True)

    code = cli_main(["ops", "health", "--vault", str(vault), "--json", "--no-write"])
    assert code == 0
    # Drain stdout so later tests are not polluted.
    _ = capsys.readouterr()

    for path in probes:
        assert path.read_bytes() == before_bytes[path]
        assert path.stat().st_mtime_ns == before_mtime[path]
    assert hash_tree(vault / "state") == before_tree

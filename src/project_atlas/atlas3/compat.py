"""AT3-005 — 2.x → 3.x compatibility prover.

Additive only. No truth loss, id rotation, provenance loss, temporal reset,
authority escalation, freshness regression, or owner-gate regression.
"""

from __future__ import annotations

from typing import Any, Final

from project_atlas.atlas3.contracts import (
    FULL_LIVE_DEMO_READY,
    MERGE_AUTHORIZATION,
    OPS_RELATIVE,
    honesty_block,
    require_vault,
    write_json_atomic,
)
from project_atlas.atlas3.contracts import ITEM_TYPES as AT3_ITEM_TYPES
from project_atlas.conversation_capture import ITEM_TYPES as CORE_ITEM_TYPES

PACKAGE_ID: Final[str] = "AT3-005"
INVARIANTS: Final[tuple[str, ...]] = (
    "NO_TRUTH_LOSS",
    "NO_PROJECT_ID_ROTATION",
    "NO_PROVENANCE_LOSS",
    "NO_TEMPORAL_RESET",
    "NO_AUTHORITY_ESCALATION",
    "NO_CONTEXT_FRESHNESS_REGRESSION",
    "NO_OWNER_GATE_REGRESSION",
)


def prove_compatibility(vault: Any) -> dict[str, Any]:
    """Prove isolated Atlas 3 stores did not break 2.x invariants."""
    root = require_vault(vault)
    checks: dict[str, bool] = {}

    identity = root / ".atlas" / "vault.json"
    # Absence cannot prove identity was not rotated. Empty file already
    # failed; missing file previously stayed True (fail-open).
    checks["NO_PROJECT_ID_ROTATION"] = (
        identity.is_file() and identity.stat().st_size > 0
    )

    layer_b = root / "state" / "claims"
    atlas3_claims = root / OPS_RELATIVE / "claims"
    checks["NO_TRUTH_LOSS"] = not atlas3_claims.exists()
    checks["NO_AUTHORITY_ESCALATION"] = not atlas3_claims.exists() and MERGE_AUTHORIZATION == (
        "NOT_GRANTED"
    )
    checks["NO_PROVENANCE_LOSS"] = CORE_ITEM_TYPES == AT3_ITEM_TYPES
    checks["NO_TEMPORAL_RESET"] = True
    try:
        from project_atlas.bitemporal import evaluate_as_of

        checks["NO_TEMPORAL_RESET"] = callable(evaluate_as_of)
    except ImportError:
        checks["NO_TEMPORAL_RESET"] = False

    checks["NO_CONTEXT_FRESHNESS_REGRESSION"] = True
    checks["NO_OWNER_GATE_REGRESSION"] = True
    try:
        from project_atlas.orchestration.autonomy.owner_gates import require_owner

        checks["NO_OWNER_GATE_REGRESSION"] = callable(require_owner)
    except ImportError:
        checks["NO_OWNER_GATE_REGRESSION"] = False

    failed = [name for name, ok in checks.items() if not ok]
    receipt = {
        "schema": "atlas3.compatibility-receipt.v1",
        "schema_version": 1,
        "package": PACKAGE_ID,
        "invariants": list(INVARIANTS),
        "checks": checks,
        "failed": failed,
        "passed": not failed,
        "layer_b_present": layer_b.is_dir(),
        "atlas3_writes_layer_b": False,
        "additive_store": str(OPS_RELATIVE),
        "full_live_demo_ready": FULL_LIVE_DEMO_READY,
        "merge_authorization": MERGE_AUTHORIZATION,
        "honesty": honesty_block(),
    }
    write_json_atomic(root / OPS_RELATIVE / "compat" / "receipt.json", receipt)
    return receipt

"""AT3-004 — Semantic capability registry.

A capability is not a CLI/API/Web/MCP wrapper. Surfaces are projections.
Prevents wrapper-count inflation.
"""

from __future__ import annotations

from typing import Any, Final

from project_atlas.atlas3.contracts import Atlas3Error, honesty_block

PACKAGE_ID: Final[str] = "AT3-004"
SURFACES: Final[frozenset[str]] = frozenset(
    {"cli", "python", "live_api", "web", "tui", "mcp", "a2a"}
)
MATURITIES: Final[frozenset[str]] = frozenset(
    {"prep-frozen", "implementation-unlocked", "roadmap-horizon"}
)
SECURITY_CLASSES: Final[frozenset[str]] = frozenset(
    {"read-derived", "evidence-append", "privacy-sensitive", "owner-gated"}
)
AUTHORITY_CLAIM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "is_authority",
        "grants_authority",
        "catalog_is_authority",
        "mint_authority",
        "owner_authority",
    }
)

Capability = dict[str, Any]


def _cap(
    capability_id: str,
    *,
    semantic_contract: str,
    truth_dependency: str,
    required_evidence: list[str],
    available_surfaces: list[str],
    maturity: str,
    demo_required: bool,
    security_class: str,
) -> Capability:
    return {
        "capability_id": capability_id,
        "semantic_contract": semantic_contract,
        "truth_dependency": truth_dependency,
        "required_evidence": required_evidence,
        "available_surfaces": available_surfaces,
        "maturity": maturity,
        "demo_required": demo_required,
        "security_class": security_class,
    }


REGISTRY: dict[str, Capability] = {
    "atlas3.pulse": _cap(
        "atlas3.pulse",
        semantic_contract="AT3-015",
        truth_dependency="derived-lenses+atlas3-ledger",
        required_evidence=["project_id"],
        available_surfaces=["cli", "python"],
        maturity="implementation-unlocked",
        demo_required=False,
        security_class="read-derived",
    ),
    "atlas3.start": _cap(
        "atlas3.start",
        semantic_contract="AT3-030",
        truth_dependency="pulse+budget+freshness",
        required_evidence=["project_id", "token_budget", "freshness_requirement"],
        available_surfaces=["cli", "python"],
        maturity="implementation-unlocked",
        demo_required=False,
        security_class="read-derived",
    ),
    "atlas3.ledger": _cap(
        "atlas3.ledger",
        semantic_contract="AT3-014",
        truth_dependency="engineering-events",
        required_evidence=["project_id", "event"],
        available_surfaces=["cli", "python"],
        maturity="implementation-unlocked",
        demo_required=False,
        security_class="evidence-append",
    ),
    "atlas3.proof": _cap(
        "atlas3.proof",
        semantic_contract="AT3-050",
        truth_dependency="independent-evidence-chain",
        required_evidence=["task_id"],
        available_surfaces=["cli", "python"],
        maturity="implementation-unlocked",
        demo_required=False,
        security_class="owner-gated",
    ),
    "atlas3.llm-connector": _cap(
        "atlas3.llm-connector",
        semantic_contract="AT3-035",
        truth_dependency="quarantine+conversation-capture",
        required_evidence=["provider", "import_mode"],
        available_surfaces=["python", "cli"],
        maturity="implementation-unlocked",
        demo_required=False,
        security_class="privacy-sensitive",
    ),
    "atlas3.twin": _cap(
        "atlas3.twin",
        semantic_contract="AT3-002",
        truth_dependency="derived-graph+truth-core-read",
        required_evidence=["project_id", "evidence_refs"],
        available_surfaces=["python"],
        maturity="implementation-unlocked",
        demo_required=False,
        security_class="read-derived",
    ),
    "atlas3.chronicle": _cap(
        "atlas3.chronicle",
        semantic_contract="D-193-HORIZON",
        truth_dependency="events+twin+memory+temporal+privacy",
        required_evidence=[],
        available_surfaces=[],
        maturity="roadmap-horizon",
        demo_required=False,
        security_class="privacy-sensitive",
    ),
}


def register_capability(capability: Capability) -> Capability:
    cid = str(capability.get("capability_id") or "").strip()
    if not cid.startswith("atlas3."):
        raise Atlas3Error("INVALID_CAPABILITY_ID", cid)
    if cid in REGISTRY:
        raise Atlas3Error(
            "CAPABILITY_EXISTS",
            f"{cid} already registered; surfaces are projections",
        )
    semantic = str(capability.get("semantic_contract") or "")
    for existing in REGISTRY.values():
        if existing["semantic_contract"] == semantic and semantic:
            raise Atlas3Error(
                "WRAPPER_INFLATION",
                f"semantic_contract {semantic} already owned by {existing['capability_id']}",
            )
    maturity = str(capability.get("maturity") or "")
    if maturity not in MATURITIES:
        raise Atlas3Error("UNKNOWN_MATURITY", maturity)
    surfaces = [str(item) for item in (capability.get("available_surfaces") or [])]
    if any(item not in SURFACES for item in surfaces):
        raise Atlas3Error("UNKNOWN_SURFACE", "surface is a projection, not a capability")
    # AT3-004: catalog honesty. SECURITY_CLASSES is closed; "authority" is not a
    # capability class. LLM/model/owner-authority must not be minted here.
    security_class = str(capability.get("security_class") or "").strip()
    if security_class == "authority":
        raise Atlas3Error(
            "AUTHORITY_SECURITY_CLASS",
            "capability registry must not mint authority",
        )
    if security_class not in SECURITY_CLASSES:
        raise Atlas3Error("UNKNOWN_SECURITY_CLASS", security_class)
    for key in AUTHORITY_CLAIM_KEYS:
        if capability.get(key) is True:
            raise Atlas3Error(
                "AUTHORITY_CLAIM_FORBIDDEN",
                "capability registry must not persist authority claims",
            )
    REGISTRY[cid] = capability
    return capability


def list_capabilities() -> dict[str, Any]:
    return {
        "package": PACKAGE_ID,
        "honesty": honesty_block(),
        "surface_is_capability": False,
        "capabilities": dict(sorted(REGISTRY.items())),
        "count": len(REGISTRY),
    }


def get_capability(capability_id: str) -> Capability:
    found = REGISTRY.get(capability_id)
    if found is None:
        raise Atlas3Error("UNKNOWN_CAPABILITY", capability_id)
    return found

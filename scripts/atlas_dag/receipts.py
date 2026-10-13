"""ATLAS_IV_RECEIPT_V1 formal-IV eligibility (D-007 + D-PR720 trust boundary).

A receipt satisfies the formal-IV gate for a candidate only when every check
passes. Anything else is rejected with explicit reasons (fail-closed):

- exact HEAD *and* exact TREE are required; an unknown candidate head/tree
  rejects the receipt (CANDIDATE_HEAD_UNKNOWN / CANDIDATE_TREE_UNKNOWN).
- self-declared receipt fields are not authenticated identity: the verifier
  must be bound in the trusted pool to a principal, and the receipt's actual
  source (GitHub comment author) must match that principal.
"""
from __future__ import annotations

PASS_SHAPED_RESULTS = frozenset({"PASS", "PASS_WITH_FINDINGS"})


def formal_iv_status(
    receipt: dict,
    pr_head: str | None,
    pr_tree: str | None,
    bindings: dict[str, str],
    declared: list[str],
    pool_present: bool,
    source_author: str | None,
) -> tuple[bool, list[str]]:
    """Return (eligible, reasons). eligible is True only when reasons is empty."""
    reasons: list[str] = []

    if not pr_head:
        reasons.append("CANDIDATE_HEAD_UNKNOWN")
    elif receipt.get("head") != pr_head:
        reasons.append("HEAD_MISMATCH")

    if not pr_tree:
        reasons.append("CANDIDATE_TREE_UNKNOWN")
    elif receipt.get("tree") != pr_tree:
        reasons.append("TREE_MISMATCH")

    if receipt.get("formal_independence") != "PASS":
        reasons.append("FORMAL_INDEPENDENCE_NOT_PASS")

    if receipt.get("candidate_author_conflict"):
        reasons.append("AUTHOR_CONFLICT")

    if receipt.get("write_activity_count", 1) != 0:
        reasons.append("WRITE_ACTIVITY_NONZERO")

    verifier = receipt.get("verifier_id", "")
    if not pool_present:
        reasons.append("VERIFIER_POOL_UNDEFINED")
    elif verifier in declared:
        # Bare-label declaration is NOT authentication.
        reasons.append("VERIFIER_IDENTITY_UNBOUND")
    elif verifier not in bindings:
        reasons.append("VERIFIER_NOT_APPROVED")
    else:
        if not source_author:
            reasons.append("SOURCE_IDENTITY_MISSING")
        elif bindings[verifier] != f"github:{source_author}":
            reasons.append("PRINCIPAL_MISMATCH")

    if receipt.get("result") not in PASS_SHAPED_RESULTS:
        reasons.append(f"RESULT_NOT_PASS_SHAPED:{receipt.get('result')}")

    return (not reasons, reasons)


def latest_eligible_receipt(
    receipts: list[dict],
    pr_head: str | None,
    pr_tree: str | None,
    bindings: dict[str, str],
    declared: list[str],
    pool_present: bool,
) -> tuple[dict | None, dict[str, list[str]]]:
    """Pick the newest eligible receipt; return (receipt, per-receipt rejection reasons)."""
    rejected: dict[str, list[str]] = {}
    eligible: list[dict] = []
    for receipt in receipts:
        source = receipt.get("_source") or {}
        ok, why = formal_iv_status(
            receipt, pr_head, pr_tree, bindings, declared, pool_present,
            source.get("author"),
        )
        if ok:
            eligible.append(receipt)
        else:
            rejected[receipt["receipt_id"]] = why
    if not eligible:
        return None, rejected
    return eligible[-1], rejected

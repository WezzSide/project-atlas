"""Read-only Merge Guardian (D-008).

Identity invariant: REMOTE PR HEAD = CI HEAD = IV HEAD = MERGE CANDIDATE HEAD.
An open PR's prospective merge_commit_sha is never an actual merge receipt.
Output is deterministic: MERGE_GATE = PASS|FAIL plus sorted REASONS.
"""
from __future__ import annotations

PASS_SHAPED_RESULTS = frozenset({"PASS", "PASS_WITH_FINDINGS"})


def evaluate(
    *,
    head: str | None,
    ci_status: str,
    receipt: dict | None,
    rejected_receipts: dict[str, list[str]],
    claim_integrity: str,
    mergeable: str | None,
    pr_open: bool,
    events: list[dict],
    pr: int,
) -> dict:
    reasons: list[str] = []

    if not head:
        reasons.append("REMOTE_HEAD_UNKNOWN")

    if ci_status != "PASS":
        reasons.append(f"EXACT_HEAD_CI_NOT_PASS:{ci_status}")

    if receipt is None:
        if rejected_receipts:
            worst = sorted(rejected_receipts)[0]
            reasons.append(f"FORMAL_IV_REJECTED:{worst}")
        else:
            reasons.append("FORMAL_IV_MISSING")
    else:
        if receipt.get("head") != head:
            reasons.append("IV_HEAD_MISMATCH")
        if receipt.get("result") not in PASS_SHAPED_RESULTS:
            reasons.append(f"IV_RESULT_NOT_PASS_SHAPED:{receipt.get('result')}")
        if receipt.get("p0", 1) != 0:
            reasons.append("P0_OPEN")
        if receipt.get("p1", 1) != 0:
            reasons.append("P1_OPEN")

    if claim_integrity != "PASS":
        reasons.append(f"CLAIM_INTEGRITY_NOT_PASS:{claim_integrity}")

    if mergeable != "MERGEABLE":
        reasons.append(f"MERGEABILITY_NOT_CONFIRMED:{mergeable or 'UNKNOWN'}")

    for event in events:
        if event.get("pr") != pr:
            continue
        if event.get("head") and head and event["head"] != head:
            continue  # stale-head events are history only
        if event["event"] == "HUMAN_GATE_REQUIRED":
            reasons.append("HUMAN_GATE_OPEN")
        if event["event"] in ("MERGED", "SEALED") and pr_open:
            # Prospective merge SHA / unproven merge receipt cannot satisfy the gate.
            reasons.append("MERGE_RECEIPT_UNPROVEN:PR_OPEN")

    reasons = sorted(set(reasons))
    return {
        "merge_gate": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
    }

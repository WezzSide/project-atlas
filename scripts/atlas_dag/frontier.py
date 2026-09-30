"""Frontier classification (D-006).

Waiting is lane-local: one PR waiting on IV never blocks another runnable lane.
Read-only diagnosis is always safe, so a node is at minimum RUNNABLE_READONLY
unless ownership, freeze, or a human gate says otherwise.
"""
from __future__ import annotations

FRONTIER_STATES = (
    "RUNNABLE_READONLY",
    "RUNNABLE_WRITE",
    "WAITING_CI",
    "WAITING_IV",
    "WAITING_OWNER",
    "HUMAN_IMPACT_GATE",
    "FROZEN",
    "MERGE_ELIGIBLE",
    "MERGED_UNSEALED",
    "SUPERSEDED",
)


def classify(node: dict) -> str:
    gate = node.get("gate", {})
    if gate.get("merge_gate") == "PASS":
        return "MERGE_ELIGIBLE"
    if node.get("frozen"):
        return "FROZEN"
    if node.get("ownership") == "OWNED":
        return "RUNNABLE_WRITE"
    # UNOWNED and AMBIGUOUS both fail closed to read-only: lane ownership is a
    # mutex and ambiguous ownership is never write authorization.
    return "RUNNABLE_READONLY"


def waiting_on(node: dict) -> list[str]:
    """Non-blocking annotation of what this lane is waiting for."""
    waits: list[str] = []
    gate = node.get("gate", {})
    if node.get("ci_status") != "PASS":
        waits.append("CI")
    if node.get("formal_iv") is None:
        waits.append("IV")
    if node.get("claim_integrity") != "PASS":
        waits.append("CLAIM_INTEGRITY")
    if node.get("owner") is None:
        waits.append("OWNER")
    if "HUMAN_GATE_OPEN" in gate.get("reasons", []):
        waits.append("HUMAN")
    return waits

"""DAG reconstruction from repository/GitHub truth + events + receipts (D-001, D-006).

Repository truth always overrides events. Any unavailable input is recorded as
UNKNOWN; ownership ambiguity is UNKNOWN, never UNOWNED (fail-closed).
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import events as events_mod
from . import gate as gate_mod
from .frontier import classify, waiting_on
from .receipts import latest_eligible_receipt

SCHEMA_NAME = "ATLAS_DAG_SNAPSHOT_V1"
UNKNOWN = "UNKNOWN"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ci_status_for_head(runs: list[dict]) -> tuple[str, str | None]:
    """Reduce CI runs for one exact head to (status, run_id).

    status in {PASS, FAIL, PENDING, NONE}. Deterministic and conservative:
    any concluded non-success run on this head => FAIL (a newer passing run can
    never launder an older failure); pending runs => PENDING; PASS requires at
    least one concluded run and none failing or pending.
    """
    if not runs:
        return "NONE", None
    ordered = sorted(runs, key=lambda r: (r.get("created_at", ""), str(r.get("id", ""))))
    completed = [r for r in ordered if r.get("status") == "completed"]
    failing = [r for r in completed if r.get("conclusion") != "success"]
    if failing:
        return "FAIL", str(failing[-1].get("id"))
    pending = [r for r in ordered if r.get("status") != "completed"]
    if pending:
        return "PENDING", str(pending[-1].get("id"))
    if completed:
        return "PASS", str(completed[-1].get("id"))
    return "NONE", None


def _is_live_event(event: dict, live_head: str | None) -> bool:
    """Head-bound events whose head differs from the live head are history only."""
    event_head = event.get("head")
    if not event_head or not live_head:
        return True
    return event_head == live_head


def _latest_event(events: list[dict], name: str, pr: int | None = None,
                  live_head: str | None = None) -> dict | None:
    matches = [
        e for e in events
        if e.get("event") == name
        and (pr is None or e.get("pr") == pr)
        and _is_live_event(e, live_head)
    ]
    return matches[-1] if matches else None


def ownership(events: list[dict], pr: int, live_head: str | None = None) -> tuple[str, list[str]]:
    """Return (status, claimants). LANE OWNERSHIP IS A MUTEX: more than one
    active claimant => AMBIGUOUS, never last-writer-wins."""
    active: list[str] = []
    for e in events:
        if e.get("pr") != pr or not _is_live_event(e, live_head):
            continue
        if e["event"] == "OWNER_CLAIMED":
            actor = e.get("actor")
            if actor and actor not in active:
                active.append(actor)
        elif e["event"] == "OWNER_RELEASED":
            actor = e.get("actor")
            if actor in active:
                active.remove(actor)
    if len(active) == 1:
        return "OWNED", active
    if len(active) > 1:
        return "AMBIGUOUS", sorted(active)
    return "UNOWNED", []


def _claim_integrity(events: list[dict], receipt: dict | None, pr: int,
                     live_head: str | None) -> str:
    ev = _latest_event(events, "CLAIM_INTEGRITY_CHANGED", pr=pr, live_head=live_head)
    if ev is not None:
        state = str(ev.get("state", "")).upper()
        if "FAIL" in state:
            return "FAIL"
        if "PASS" in state:
            return "PASS"
    if receipt is not None:
        return receipt.get("claim_integrity", UNKNOWN)
    return UNKNOWN


def _is_frozen(events: list[dict], pr: int, claim_integrity: str,
               live_head: str | None) -> bool:
    ev = _latest_event(events, "HUMAN_GATE_REQUIRED", pr=pr, live_head=live_head)
    if ev is not None:
        state = str(ev.get("state", "")).upper()
        if state == "FROZEN":
            return True
    return claim_integrity == "FAIL"


def build_pr_node(
    pr: dict,
    main_head: str | None,
    events: list[dict],
    receipts: list[dict],
    bindings: dict[str, str],
    declared: list[str],
    pool_present: bool,
    client,
) -> dict:
    number = pr["number"]
    head = pr.get("headRefOid")
    commit = client.commit(head) if head else None
    tree = commit["tree"] if commit else None

    pr_receipts = [r for r in receipts if r.get("pr") == number]
    receipt, rejected_receipts = latest_eligible_receipt(
        pr_receipts, head, tree, bindings, declared, pool_present
    )

    runs = client.runs_for_head(head) if head else []
    ci_status, ci_run_id = ci_status_for_head(runs)

    ownership_state, claimants = ownership(events, number, head)
    owner = claimants[0] if ownership_state == "OWNED" else None
    claim = _claim_integrity(events, receipt, number, head)
    frozen = _is_frozen(events, number, claim, head)

    gate_result = gate_mod.evaluate(
        head=head,
        ci_status=ci_status,
        receipt=receipt,
        rejected_receipts=rejected_receipts,
        claim_integrity=claim,
        mergeable=pr.get("mergeable"),
        pr_open=True,
        events=events,
        pr=number,
    )

    node = {
        "node_id": f"pr-{number}",
        "lane": f"pr/{number}",
        "pr": number,
        "title": pr.get("title"),
        "head": head,
        "tree": tree,
        "base": pr.get("baseRefName"),
        "owner": owner,
        "ownership": ownership_state,
        "claimants": claimants,
        "state": None,  # filled by classify()
        "hazard_class": "A_REAL_HOST_SAFE",
        "ci_status": ci_status,
        "ci_run_id": ci_run_id,
        "mergeable": pr.get("mergeable", UNKNOWN),
        "draft": bool(pr.get("isDraft")),
        "claim_integrity": claim,
        "frozen": frozen,
        "formal_iv": receipt["receipt_id"] if receipt else None,
        "rejected_receipts": rejected_receipts,
        "gate": gate_result,
        "stale_events": [
            e["event_id"]
            for e in events
            if e.get("pr") == number and e.get("head") and head and e["head"] != head
        ],
        "dependencies": sorted({d for e in events if e.get("pr") == number
                                for d in e.get("dependencies", [])}),
        "blockers": gate_result["reasons"],
        "next_actions": [],
        "review_comment_count": len(client.review_comments(number)),
    }
    node["state"] = classify(node)
    node["waiting_on"] = waiting_on(node)
    node["next_actions"] = _next_actions(node)
    return node


def build_snapshot(client, clock=utcnow) -> dict:
    main_branch = client.default_branch() or "main"
    main = client.branch_head(main_branch)
    main_head = main["sha"] if main else None
    main_tree = main["tree"] if main else None

    issue = client.dag_issue()
    comments = client.issue_comments(issue["number"]) if issue else []
    ingested = events_mod.ingest_comments(comments)
    bindings, declared, pool_present = events_mod.parse_verifier_pool(
        client.issue_body(issue["number"]) if issue else None
    )

    nodes = [
        build_pr_node(pr, main_head, ingested.events, ingested.receipts,
                      bindings, declared, pool_present, client)
        for pr in sorted(client.open_prs(), key=lambda p: p["number"])
    ]
    runnable = [n for n in nodes if n["state"] in ("RUNNABLE_READONLY", "RUNNABLE_WRITE")]
    return {
        "schema": SCHEMA_NAME,
        "generated_at_utc": clock(),
        "repo": client.repo or UNKNOWN,
        "main_branch": main_branch,
        "main_head": main_head,
        "main_tree": main_tree,
        "dag_issue": issue["number"] if issue else None,
        "event_count": len(ingested.events),
        "invalid_events": len(ingested.invalid),
        "safe_runnable_count": len(runnable),
        "nodes": nodes,
    }


def _next_actions(node: dict) -> list[str]:
    if node["state"] == "MERGE_ELIGIBLE":
        return ["HUMAN_MERGE_DECISION"]
    if node["state"] == "FROZEN":
        return ["AWAIT_DISTINCT_FORMAL_VERIFIER"]
    if node["state"] in ("RUNNABLE_READONLY", "RUNNABLE_WRITE"):
        return ["DELTA_FIRST_REVALIDATION"]
    if node["state"] == "WAITING_OWNER":
        return ["HAND_OFF_OR_WAIT"]
    return []

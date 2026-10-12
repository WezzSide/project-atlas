"""Derive improvement panels from loaded evidence records."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from project_atlas.improvement_plane.quality import capped_occurrence_score_inputs

_ISO_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))"
)

_OWNER_BLOCK_MARKERS = (
    "BLOCKED_BY_OWNER",
    "WAITING_OWNER",
    "OWNER_REQUIRED",
    "MERGE_AUTHORIZATION = NOT_GRANTED",
    "PR_CLOSE_AUTHORIZATION",
)

_OPEN_STATUSES = {"OPEN", "open", "UNRESOLVED", "unresolved", "ACTIVE", "active"}


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        match = _ISO_RE.search(value)
        if not match:
            return None
        candidate = match.group("ts")
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def extract_record_timestamp(payload: dict[str, Any]) -> tuple[str, datetime | None, str | None]:
    """Return (status, parsed, raw) for the best supported timestamp field.

    Missing timestamps are ``unknown``, never zero elapsed time.
    """
    candidates = (
        "as_of_utc",
        "as_of",
        "reference_utc",
        "generated_at",
        "timestamp",
        "observed_at",
        "sealed_at",
    )
    for key in candidates:
        raw = payload.get(key)
        parsed = _parse_utc(raw)
        if parsed is not None:
            return "present", parsed, str(raw)

    live = payload.get("live_refresh")
    if isinstance(live, dict):
        for key in ("timestamp", "as_of_utc", "as_of"):
            raw = live.get(key)
            parsed = _parse_utc(raw)
            if parsed is not None:
                return "present", parsed, str(raw)
        # Explicit non-ISO prose timestamps stay unknown rather than inventing.
        live_ts = live.get("timestamp")
        if isinstance(live_ts, str) and live_ts.strip():
            return "unknown", None, live_ts

    return "unknown", None, None


def analyze_waiting_work(records: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        path = str(record["path"])

        nodes = payload.get("owner_gated_nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                items.append(
                    {
                        "kind": "owner_gated",
                        "id": str(node.get("node_id") or node.get("id") or "unknown"),
                        "summary": str(
                            node.get("description")
                            or node.get("next_required_owner_action")
                            or "owner-gated work"
                        ),
                        "source": path,
                        "dependents": list(node.get("dependents") or []),
                        "classification": "BLOCKED_BY_OWNER",
                    }
                )

        dag = payload.get("successor_dag")
        if isinstance(dag, dict):
            for node in dag.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                classification = str(node.get("classification") or "")
                if classification in {
                    "BLOCKED_BY_OWNER",
                    "BLOCKED_EXTERNAL",
                    "WAITING_OWNER",
                    "READY",
                    "DERIVABLE",
                }:
                    if classification == "ALREADY_COMPLETE":
                        continue
                    items.append(
                        {
                            "kind": "successor_dag",
                            "id": str(node.get("node") or "unknown"),
                            "summary": str(node.get("note") or classification),
                            "source": path,
                            "dependents": [],
                            "classification": classification,
                        }
                    )

        # Explicit disposition markers in free-form packets.
        for key, value in payload.items():
            if key in {"owner_gated_nodes", "successor_dag", "findings"}:
                continue
            if isinstance(value, dict) and str(value.get("classification") or "") in {
                "BLOCKED_BY_OWNER",
                "WAITING_OWNER",
            }:
                items.append(
                    {
                        "kind": "disposition",
                        "id": key,
                        "summary": str(value.get("disposition") or value.get("classification")),
                        "source": path,
                        "dependents": [],
                        "classification": str(value.get("classification")),
                    }
                )

    waiting_only = [
        item
        for item in items
        if item["classification"]
        in {"BLOCKED_BY_OWNER", "BLOCKED_EXTERNAL", "WAITING_OWNER", "DERIVABLE", "READY"}
    ]
    return {
        "count": len(waiting_only),
        "items": waiting_only,
        "note": (
            "Waiting items are observed from evidence packets only. "
            "READY/DERIVABLE are listed as queued opportunities, not as authority to start."
        ),
    }


def _finding_is_open(finding: dict[str, Any]) -> bool:
    status = finding.get("status")
    if status is not None:
        status_text = str(status)
        if status_text.upper() in {"CLOSED", "RESOLVED", "PASS", "FIXED"}:
            return False
        return status_text in _OPEN_STATUSES
    severity = str(finding.get("severity") or "").upper()
    if severity in {"HIGH", "CRITICAL", "ERROR"}:
        return True
    # Untyped finding dicts are treated as open observations.
    return True


def _related_finding_bucket_key(
    buckets: dict[str, dict[str, Any]], finding_id: str
) -> str | None:
    """Return an existing bucket when hard-counter IDs echo a finding code.

    Example: ``secrets.REMOTE_PASSWORD_ECHO`` relates to
    ``GIT_REMOTE_PASSWORD_ECHO``. Prevents near-duplicate recommendations from
    the same packet. Requires leaf length >= 8 to avoid trivial collisions.
    """
    if finding_id in buckets:
        return finding_id
    leaf = finding_id.rsplit(".", 1)[-1]
    if len(leaf) < 8:
        return None
    for existing in buckets:
        existing_leaf = existing.rsplit(".", 1)[-1]
        if existing.endswith(leaf) or leaf.endswith(existing_leaf):
            return existing
    return None


def _record_failure(
    buckets: dict[str, dict[str, Any]],
    *,
    finding_id: str,
    failure_class: str,
    path: str,
    open_hit: bool,
    status: str | None,
    merge_related: bool = False,
) -> None:
    key = finding_id
    if merge_related:
        related = _related_finding_bucket_key(buckets, finding_id)
        if related is not None:
            key = related
    bucket = buckets.setdefault(
        key,
        {
            "finding_id": key,
            "failure_class": failure_class,
            "occurrences": 0,
            "open_occurrences": 0,
            "sources": set(),
            "statuses": set(),
            "related_ids": set(),
        },
    )
    if key != finding_id:
        bucket["related_ids"].add(finding_id)
    bucket["occurrences"] += 1
    if open_hit:
        bucket["open_occurrences"] += 1
    bucket["sources"].add(path)
    if status is not None:
        bucket["statuses"].add(status)
    if failure_class not in {"UNKNOWN", "NONE"}:
        bucket["failure_class"] = failure_class


def analyze_recurring_failures(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        path = str(record["path"])
        findings = payload.get("findings")
        if isinstance(findings, list):
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                finding_id = str(
                    finding.get("finding_id")
                    or finding.get("code")
                    or finding.get("id")
                    or finding.get("attack")
                    or "unknown-finding"
                )
                if finding_id == "unknown-finding":
                    # Skip anonymous empty shells; require an identifiable code.
                    continue
                failure_class = str(
                    finding.get("failure_class")
                    or finding.get("classification")
                    or finding.get("severity")
                    or "UNKNOWN"
                )
                status = finding.get("status")
                _record_failure(
                    buckets,
                    finding_id=finding_id,
                    failure_class=failure_class,
                    path=path,
                    open_hit=_finding_is_open(finding),
                    status=str(status) if status is not None else None,
                )

        hard = payload.get("hard_counters")
        if isinstance(hard, dict):
            for code, value in hard.items():
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
                if value <= 0:
                    continue
                _record_failure(
                    buckets,
                    finding_id=str(code),
                    failure_class="HARD_COUNTER",
                    path=path,
                    open_hit=True,
                    status="OPEN",
                    merge_related=True,
                )

    items: list[dict[str, Any]] = []
    for bucket in buckets.values():
        if bucket["open_occurrences"] < 1:
            continue
        if (
            bucket["occurrences"] < 2
            and len(bucket["sources"]) < 2
            and bucket["failure_class"] in {"NONE", "UNKNOWN"}
        ):
            # Single isolated open finding without a strong class stays out.
            continue
        items.append(
            {
                "finding_id": bucket["finding_id"],
                "failure_class": bucket["failure_class"],
                "occurrences": bucket["occurrences"],
                "open_occurrences": bucket["open_occurrences"],
                "source_count": len(bucket["sources"]),
                "sources": sorted(bucket["sources"]),
                "statuses": sorted(bucket["statuses"]),
                "related_ids": sorted(bucket.get("related_ids") or []),
            }
        )

    items.sort(
        key=lambda row: (
            -int(row["open_occurrences"]),
            -int(row["source_count"]),
            row["finding_id"],
        )
    )
    return {
        "count": len(items),
        "items": items,
        "note": (
            "Recurrence is counted across evidence packets. "
            "Absence of a finding in other packets is unknown, not proof of uniqueness."
        ),
    }


def analyze_closed_findings(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect explicitly CLOSED/RESOLVED findings (positive resolution evidence)."""
    closed: dict[str, dict[str, Any]] = {}
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        path = str(record["path"])
        findings = payload.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            status = finding.get("status")
            if status is None or str(status).upper() not in {
                "CLOSED",
                "RESOLVED",
                "FIXED",
                "PASS",
            }:
                continue
            finding_id = str(
                finding.get("finding_id")
                or finding.get("code")
                or finding.get("id")
                or finding.get("attack")
                or ""
            )
            if not finding_id:
                continue
            bucket = closed.setdefault(
                finding_id,
                {
                    "finding_id": finding_id,
                    "statuses": set(),
                    "sources": set(),
                },
            )
            bucket["statuses"].add(str(status).upper())
            bucket["sources"].add(path)
    items = [
        {
            "finding_id": row["finding_id"],
            "statuses": sorted(row["statuses"]),
            "sources": sorted(row["sources"]),
            "source_count": len(row["sources"]),
        }
        for row in closed.values()
    ]
    items.sort(key=lambda row: row["finding_id"])
    return {
        "count": len(items),
        "items": items,
        "note": (
            "Closed findings are positive resolution evidence from packets. "
            "Absence of an open finding without a closed record remains unobservable."
        ),
    }


def analyze_evidence_freshness(
    records: list[dict[str, Any]],
    *,
    reference_utc: str | None,
) -> dict[str, Any]:
    reference = _parse_utc(reference_utc) if reference_utc else None
    items: list[dict[str, Any]] = []
    with_timestamp = 0
    timestamp_unknown = 0

    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        status, parsed, raw = extract_record_timestamp(payload)
        waiting_elapsed: int | None = None
        if status == "present" and parsed is not None and reference is not None:
            waiting_elapsed = max(0, int((reference - parsed).total_seconds()))
            with_timestamp += 1
        elif status == "present" and parsed is not None and reference is None:
            with_timestamp += 1
        else:
            timestamp_unknown += 1

        # IV presence signals — missing IV receipt ≠ verification never happened.
        iv_signals: list[dict[str, str]] = []
        for key, value in payload.items():
            key_l = key.lower()
            if "iv" in key_l or key_l.endswith("_iv"):
                if value is None:
                    iv_signals.append(
                        {
                            "field": key,
                            "status": "absent",
                            "note": (
                                "Missing IV field is unknown, not proof "
                                "verification never happened."
                            ),
                        }
                    )
                elif isinstance(value, dict):
                    iv_signals.append(
                        {
                            "field": key,
                            "status": str(value.get("status") or value.get("class") or "present"),
                            "note": "Observed IV metadata only; not a re-certification.",
                        }
                    )
                else:
                    iv_signals.append(
                        {
                            "field": key,
                            "status": "present",
                            "note": "Observed IV metadata only; not a re-certification.",
                        }
                    )

        items.append(
            {
                "path": str(record["path"]),
                "timestamp_status": status if status == "present" else "unknown",
                "timestamp_raw": raw,
                "waiting_elapsed_seconds": waiting_elapsed,
                "active_engineering_seconds": None,
                "active_engineering_note": (
                    "Active engineering time is not instrumented in these evidence "
                    "packets; value stays unknown (not zero)."
                ),
                "iv_signals": iv_signals,
            }
        )

    return {
        "reference_utc": reference_utc,
        "reference_status": "present" if reference is not None else "unknown",
        "with_timestamp": with_timestamp,
        "timestamp_unknown": timestamp_unknown,
        "items": items,
        "note": (
            "Elapsed waiting time uses evidence timestamps vs --reference-utc when both "
            "parse. Missing timestamps are unknown, never zero. Active engineering time "
            "is not derived from wall-clock gaps."
        ),
    }


def analyze_owner_action_backlog(records: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        path = str(record["path"])
        nodes = payload.get("owner_gated_nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                dependents = list(node.get("dependents") or [])
                items.append(
                    {
                        "node_id": str(node.get("node_id") or node.get("id") or "unknown"),
                        "description": str(node.get("description") or ""),
                        "next_required_owner_action": str(
                            node.get("next_required_owner_action") or ""
                        ),
                        "why_owner_only": str(node.get("why_owner_only") or ""),
                        "autonomous_preparation_complete": node.get(
                            "autonomous_preparation_complete"
                        ),
                        "dependents": dependents,
                        "dependent_count": len(dependents),
                        "source": path,
                    }
                )

        # Free-text authorization blockers commonly present in packets.
        note = payload.get("note")
        if isinstance(note, str) and any(marker in note for marker in _OWNER_BLOCK_MARKERS):
            items.append(
                {
                    "node_id": f"note:{path}",
                    "description": note[:240],
                    "next_required_owner_action": "Owner authorization decision required",
                    "why_owner_only": "Evidence note cites owner authorization boundary",
                    "autonomous_preparation_complete": None,
                    "dependents": [],
                    "dependent_count": 0,
                    "source": path,
                }
            )

        if payload.get("merge_authorized") is False and payload.get("findings"):
            open_findings = [
                f
                for f in (payload.get("findings") or [])
                if isinstance(f, dict)
                and (f.get("status") is None or str(f.get("status")) in _OPEN_STATUSES)
            ]
            if open_findings:
                items.append(
                    {
                        "node_id": f"merge-gate:{path}",
                        "description": (
                            f"{len(open_findings)} open finding(s) with merge_authorized=false"
                        ),
                        "next_required_owner_action": (
                            "Owner merge/authorization decision after evidence review"
                        ),
                        "why_owner_only": "merge_authorized is false in evidence packet",
                        "autonomous_preparation_complete": None,
                        "dependents": [],
                        "dependent_count": 0,
                        "source": path,
                    }
                )

    items.sort(key=lambda row: (-int(row["dependent_count"]), row["node_id"]))
    return {
        "count": len(items),
        "items": items,
        "note": (
            "Owner backlog is observational. Listing an action does not grant "
            "MERGE_AUTHORIZATION or execution authority."
        ),
    }


def analyze_progress_signals(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Collect non-resolution progress markers from supported packet fields.

    These markers capture evidence movement (candidate supersession, remediation
    status progression) without certifying resolution.
    """
    items: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        path = str(record["path"])

        candidate_head = payload.get("review_candidate_head")
        candidate_tree = payload.get("review_candidate_tree")
        if isinstance(candidate_head, str) and isinstance(candidate_tree, str):
            ci_state = str(payload.get("ci_status_at_freeze") or "unknown")
            items.append(
                {
                    "id": f"candidate:{path}",
                    "kind": "candidate_supersession",
                    "summary": (
                        f"candidate_head={candidate_head} "
                        f"candidate_tree={candidate_tree} ci_state={ci_state}"
                    ),
                    "source": path,
                    "status": "evidence_progress",
                }
            )

        top_status = payload.get("status")
        finding_id = payload.get("finding_id")
        if isinstance(top_status, str) and isinstance(finding_id, str) and finding_id:
            items.append(
                {
                    "id": f"progress:{path}:finding:{finding_id}",
                    "kind": "status_progress",
                    "summary": f"finding_id={finding_id} status={top_status}",
                    "source": path,
                    "status": "evidence_progress",
                }
            )

    items.sort(key=lambda row: row["id"])
    return {
        "count": len(items),
        "items": items,
        "note": (
            "Progress markers reflect evidence movement only. They never certify "
            "resolution, independent verification, merge authorization, or closure."
        ),
    }


def analyze_data_quality_risks(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Surface labeled evidence-quality risks without inventing attributions."""
    finding_statuses: dict[str, set[str]] = defaultdict(set)
    finding_sources: dict[str, set[str]] = defaultdict(set)
    unreadable = 0
    for record in records:
        if record.get("parse_status") != "ok":
            unreadable += 1
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        path = str(record["path"])
        findings = payload.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            finding_id = str(
                finding.get("finding_id")
                or finding.get("code")
                or finding.get("id")
                or finding.get("attack")
                or ""
            )
            if not finding_id:
                continue
            status = finding.get("status")
            if status is not None:
                finding_statuses[finding_id].add(str(status).upper())
            finding_sources[finding_id].add(path)

    contradictory: list[dict[str, Any]] = []
    for finding_id, statuses in sorted(finding_statuses.items()):
        openish = {s for s in statuses if s in {"OPEN", "UNRESOLVED", "ACTIVE"}}
        closedish = {s for s in statuses if s in {"CLOSED", "RESOLVED", "PASS", "FIXED"}}
        if openish and closedish:
            contradictory.append(
                {
                    "finding_id": finding_id,
                    "statuses": sorted(statuses),
                    "sources": sorted(finding_sources[finding_id]),
                    "risk": "contradictory_status",
                }
            )

    duplicates = [
        {
            "finding_id": finding_id,
            "source_count": len(sources),
            "sources": sorted(sources),
            "risk": "duplicate_records",
        }
        for finding_id, sources in sorted(finding_sources.items())
        if len(sources) >= 2
    ]

    return {
        "unreadable_packets": unreadable,
        "contradictory_status": contradictory,
        "duplicate_records": duplicates,
        "incorrect_attribution": {
            "status": "not_inferred",
            "note": (
                "This lane cites packet paths only and does not invent project/owner "
                "attribution beyond fields present in the evidence."
            ),
        },
        "note": (
            "Risk panel is observational. Contradictory OPEN+CLOSED for one finding_id "
            "may be legitimate lifecycle across packets or a data-quality issue."
        ),
    }


def _kind_for_category(category: str) -> str:
    if category == "owner_decision":
        return "owner_decision"
    if category == "recurring_failure":
        return "engineering"
    if category in {"evidence_freshness", "stale_evidence"}:
        return "data_quality"
    if category == "external_dependency":
        return "external_dependency"
    if category == "queued_opportunity":
        # Queued READY/DERIVABLE is opportunity hygiene, not a defect signal.
        return "other"
    return "other"


def build_recommendations(
    *,
    waiting: dict[str, Any],
    recurring: dict[str, Any],
    freshness: dict[str, Any],
    owner: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rank evidence-backed improvement actions. Authority remains none."""
    scored: list[dict[str, Any]] = []

    for item in owner.get("items") or []:
        dependents = int(item.get("dependent_count") or 0)
        score = 100 + dependents * 25
        node_id = item["node_id"]
        scored.append(
            {
                "score": score,
                "category": "owner_decision",
                "recommendation_id": f"owner:{node_id}",
                "title": f"Resolve owner gate {node_id}",
                "observed_problem": (
                    f"Owner-gated work {node_id} is blocking progress"
                    + (f" with {dependents} dependent(s)" if dependents else "")
                ),
                "proposed_action": item.get("next_required_owner_action")
                or "Owner decision required",
                "required_actor": "owner",
                "dependency": (
                    f"dependents={','.join(item.get('dependents') or [])}"
                    if item.get("dependents")
                    else "none_stated"
                ),
                "scope": "owner_authorization_boundary",
                "rationale": item.get("description") or item.get("why_owner_only") or "",
                "ranking_rationale": (
                    f"score={score}: base owner-gate weight 100 "
                    f"+ 25 per dependent ({dependents})"
                ),
                "source_records": [item["source"]],
                "uncertainty": (
                    "Owner gate presence is taken from evidence text; live PR/merge "
                    "state may have moved since the packet was written."
                ),
                "evidence_strength": "high" if dependents else "medium",
                "authority": "none",
            }
        )

    for item in recurring.get("items") or []:
        capped_occ, sources, rank_note = capped_occurrence_score_inputs(
            open_occurrences=int(item["open_occurrences"]),
            source_count=int(item["source_count"]),
        )
        score = 70 + capped_occ * 10 + sources * 15
        fid = str(item["finding_id"])
        scored.append(
            {
                "score": score,
                "category": "recurring_failure",
                "recommendation_id": f"finding:{fid}",
                "title": f"Investigate recurring failure {fid}",
                "observed_problem": (
                    f"Finding {fid} recurs with failure_class={item['failure_class']}"
                ),
                "proposed_action": (
                    f"Triage failure_class={item['failure_class']} across "
                    f"{item['source_count']} evidence source(s); do not auto-retry."
                ),
                "required_actor": "engineer",
                "dependency": "evidence_packets_only",
                "scope": "failure_triage",
                "rationale": (
                    f"Observed {item['open_occurrences']} open occurrence(s) "
                    f"across {item['source_count']} unique source(s)."
                    + (
                        f" Related counter IDs merged: {item['related_ids']}."
                        if item.get("related_ids")
                        else ""
                    )
                ),
                "ranking_rationale": (
                    f"score={score}: base 70 + 10*capped_occurrences({capped_occ}) "
                    f"+ 15*unique_sources({sources}); {rank_note}"
                ),
                "source_records": list(item["sources"]),
                "uncertainty": (
                    "Packets may duplicate the same incident; ranking uses unique "
                    "sources and caps raw occurrences to limit duplicate inflation."
                ),
                "evidence_strength": "high" if item["source_count"] >= 2 else "medium",
                "authority": "none",
            }
        )

    for item in waiting.get("items") or []:
        if item.get("classification") != "BLOCKED_EXTERNAL":
            continue
        scored.append(
            {
                "score": 60,
                "category": "external_dependency",
                "recommendation_id": f"external:{item.get('id')}",
                "title": f"Unblock external dependency {item.get('id')}",
                "observed_problem": (
                    f"External blocker remains: {item.get('summary') or item.get('id')}"
                ),
                "proposed_action": (
                    "Arrange required external capability; this lane cannot execute it."
                ),
                "required_actor": "external_executor",
                "dependency": "external_capability",
                "scope": "external_dependency",
                "rationale": item.get("summary") or "BLOCKED_EXTERNAL",
                "ranking_rationale": "score=60: external blocker below owner, above hygiene",
                "source_records": [item["source"]],
                "uncertainty": (
                    "External readiness is not observable from evidence packets alone."
                ),
                "evidence_strength": "medium",
                "authority": "none",
            }
        )

    unknown_ts = [
        row
        for row in (freshness.get("items") or [])
        if row.get("timestamp_status") == "unknown"
    ]
    if unknown_ts:
        scored.append(
            {
                "score": 40,
                "category": "evidence_freshness",
                "recommendation_id": "quality:missing-timestamps",
                "title": "Add comparable timestamps to undated evidence packets",
                "observed_problem": (
                    f"{len(unknown_ts)} evidence packet(s) lack parseable timestamps, "
                    "so waiting age stays unknown"
                ),
                "proposed_action": (
                    "When authors refresh packets, include as_of_utc so waiting age "
                    "can be measured without inventing zeros."
                ),
                "required_actor": "evidence_author",
                "dependency": "packet_refresh_cycle",
                "scope": "evidence_hygiene",
                "rationale": f"{len(unknown_ts)} scanned packet(s) lack a parseable timestamp.",
                "ranking_rationale": "score=40: hygiene weight below owner gates and failures",
                "source_records": [row["path"] for row in unknown_ts[:12]],
                "uncertainty": (
                    "Packets may still be fresh; missing timestamp is unknown age, "
                    "not proof of staleness."
                ),
                "evidence_strength": "medium",
                "authority": "none",
            }
        )

    aged = [
        row
        for row in (freshness.get("items") or [])
        if isinstance(row.get("waiting_elapsed_seconds"), int)
        and int(row["waiting_elapsed_seconds"]) >= 7 * 24 * 3600
    ]
    for row in aged[:5]:
        elapsed = int(row["waiting_elapsed_seconds"])
        scored.append(
            {
                "score": 55,
                "category": "stale_evidence",
                "recommendation_id": f"stale:{row['path']}",
                "title": f"Refresh or close aged evidence {row['path']}",
                "observed_problem": (
                    f"Evidence packet age is {elapsed}s vs reference_utc (>= 7d)"
                ),
                "proposed_action": (
                    "Re-verify live state or mark the packet superseded; do not treat "
                    "age alone as a defect."
                ),
                "required_actor": "operator",
                "dependency": "live_state_recheck",
                "scope": "stale_snapshot",
                "rationale": (
                    f"Parseable evidence timestamp is {elapsed}s before reference_utc."
                ),
                "ranking_rationale": (
                    f"score=55: stale-packet weight when elapsed>={7 * 24 * 3600}s"
                ),
                "source_records": [row["path"]],
                "uncertainty": (
                    "Age measures packet timestamp vs reference only; work may already "
                    "be complete without an updated packet."
                ),
                "evidence_strength": "medium",
                "authority": "none",
            }
        )

    ready_waiting = [
        item
        for item in (waiting.get("items") or [])
        if item.get("classification") in {"READY", "DERIVABLE"}
    ]
    for item in ready_waiting[:5]:
        scored.append(
            {
                "score": 50,
                "category": "queued_opportunity",
                "recommendation_id": f"queue:{item.get('classification')}:{item.get('id')}",
                "title": f"Consider queued item {item['id']}",
                "observed_problem": (
                    f"Queued {item['classification']} item remains in successor evidence"
                ),
                "proposed_action": (
                    "Owner/operator may schedule this READY/DERIVABLE item; this report "
                    "does not dispatch it."
                ),
                "required_actor": "owner_or_operator",
                "dependency": item.get("classification"),
                "scope": "queued_work",
                "rationale": item.get("summary") or item.get("classification"),
                "ranking_rationale": "score=50: opportunity weight below blockers/failures",
                "source_records": [item["source"]],
                "uncertainty": (
                    "READY in an old packet may no longer be ready; confirm against live state."
                ),
                "evidence_strength": "low",
                "authority": "none",
            }
        )

    for row in scored:
        row["kind"] = _kind_for_category(str(row["category"]))

    # Fair selection: keep top items per kind so owner gates cannot hide engineering.
    per_kind_caps = {
        "owner_decision": 5,
        "engineering": 5,
        "data_quality": 3,
        "external_dependency": 3,
        "other": 2,
    }
    selected: list[dict[str, Any]] = []
    for kind, cap in per_kind_caps.items():
        bucket = [row for row in scored if row.get("kind") == kind]
        bucket.sort(key=lambda row: (-int(row["score"]), row["title"]))
        selected.extend(bucket[:cap])
    selected.sort(key=lambda row: (-int(row["score"]), str(row["kind"]), row["title"]))

    recommendations: list[dict[str, Any]] = []
    for index, row in enumerate(selected[:16], start=1):
        recommendations.append(
            {
                "rank": index,
                "recommendation_id": row["recommendation_id"],
                "kind": row["kind"],
                "category": row["category"],
                "title": row["title"],
                "observed_problem": row["observed_problem"],
                "proposed_action": row["proposed_action"],
                "required_actor": row["required_actor"],
                "dependency": row["dependency"],
                "scope": row["scope"],
                "rationale": row["rationale"],
                "ranking_rationale": row["ranking_rationale"],
                "source_records": row["source_records"],
                "uncertainty": row["uncertainty"],
                "evidence_strength": row["evidence_strength"],
                "authority": "none",
                "score": row["score"],
            }
        )
    return recommendations


def recommendations_by_kind(
    recommendations: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        "owner_decision": [],
        "engineering": [],
        "data_quality": [],
        "external_dependency": [],
        "other": [],
    }
    for rec in recommendations:
        kind = str(rec.get("kind") or "other")
        grouped.setdefault(kind, []).append(rec)
    return grouped


def collect_source_pin(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("package_id") == "AS-IMPR-PLANE-001" and payload.get("pin_kind") == (
            "source_snapshot"
        ):
            return {
                "path": record["path"],
                "head": payload.get("head"),
                "tree": payload.get("tree"),
                "base_ref": payload.get("base_ref"),
            }
    return None

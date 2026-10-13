"""ATLAS_EVENT_V1 / ATLAS_IV_RECEIPT_V1 ingestion from the DAG Control issue (D-002, D-007).

Parser behavior (D-002):
- schema-validate every fenced ```json payload;
- duplicate event ID is idempotent (first wins after canonical ordering);
- reordered events are normalized by (timestamp_utc, event_id);
- stable-state noise (HEAD_UNCHANGED, CI_STILL_RUNNING, STILL_WAITING, STILL_FROZEN,
  NO_CHANGE) is rejected, not stored;
- invalid payloads are reported, never crash ingestion;
- stale-head events remain history only (marked by the model, which knows live heads).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

EVENT_SCHEMA = "atlas_event_v1.schema.json"
RECEIPT_SCHEMA = "atlas_iv_receipt_v1.schema.json"

FORBIDDEN_STABLE_STATE = frozenset(
    {"HEAD_UNCHANGED", "CI_STILL_RUNNING", "STILL_WAITING", "STILL_FROZEN", "NO_CHANGE"}
)

_FENCED_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)


@dataclass
class IngestResult:
    events: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    invalid: list[tuple[str, str]] = field(default_factory=list)  # (comment marker, reason)

    @property
    def by_id(self) -> dict[str, dict]:
        return {e["event_id"]: e for e in self.events}


def _comment_source(comment: dict) -> dict:
    """Trusted-source metadata for a receipt: who actually authored the comment."""
    user = comment.get("user") or {}
    return {
        "comment_id": comment.get("id"),
        "author": user.get("login"),
        "url": comment.get("html_url"),
    }


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validator_for(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name), format_checker=FormatChecker())


def extract_payloads(body: str) -> list[tuple[int, str]]:
    """Return (index, raw_json_text) for every fenced json block in a comment body."""
    return [(i, m.group(1).strip()) for i, m in enumerate(_FENCED_RE.finditer(body or ""))]


def _canonical_order(events: list[dict]) -> list[dict]:
    return sorted(events, key=lambda e: (e.get("timestamp_utc", ""), e.get("event_id", "")))


def ingest_comments(comments: list[dict]) -> IngestResult:
    """Ingest raw issue comments into validated, deduplicated, canonically ordered streams."""
    event_validator = validator_for(EVENT_SCHEMA)
    receipt_validator = validator_for(RECEIPT_SCHEMA)
    result = IngestResult()
    seen_ids: set[str] = set()
    seen_receipts: set[str] = set()

    for pos, comment in enumerate(comments):
        marker = f"comment#{pos}"
        for idx, raw in extract_payloads(comment.get("body", "")):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                result.invalid.append((f"{marker}/block{idx}", f"invalid json: {exc.msg}"))
                continue
            if not isinstance(payload, dict) or "schema" not in payload:
                result.invalid.append((f"{marker}/block{idx}", "missing schema field"))
                continue
            schema_name = payload["schema"]
            if schema_name == "ATLAS_EVENT_V1":
                errors = sorted(event_validator.iter_errors(payload), key=lambda e: e.path)
                if errors:
                    result.invalid.append(
                        (f"{marker}/block{idx}", f"schema: {errors[0].message}")
                    )
                    continue
                if payload["event"] in FORBIDDEN_STABLE_STATE:
                    result.invalid.append(
                        (f"{marker}/block{idx}",
                         f"stable-state noise event: {payload['event']}")
                    )
                    continue
                if payload["event_id"] in seen_ids:
                    continue  # idempotent on duplicate event ID
                seen_ids.add(payload["event_id"])
                result.events.append(payload)
            elif schema_name == "ATLAS_IV_RECEIPT_V1":
                errors = sorted(receipt_validator.iter_errors(payload), key=lambda e: e.path)
                if errors:
                    result.invalid.append(
                        (f"{marker}/block{idx}", f"schema: {errors[0].message}")
                    )
                    continue
                if payload["receipt_id"] in seen_receipts:
                    continue
                seen_receipts.add(payload["receipt_id"])
                # Carry trusted-source metadata: self-declared receipt fields are
                # NOT authenticated identity (D-PR720 trust boundary).
                payload["_source"] = _comment_source(comment)
                result.receipts.append(payload)
            else:
                result.invalid.append((f"{marker}/block{idx}", f"unknown schema: {schema_name}"))

    result.events = _canonical_order(result.events)
    result.receipts.sort(key=lambda r: (r.get("timestamp_utc", ""), r.get("receipt_id", "")))
    return result


def parse_verifier_pool(issue_body: str | None) -> tuple[dict[str, str], list[str], bool]:
    """Extract the verifier pool from an ATLAS_VERIFIER_POOL_V1 block.

    Returns (bindings, declared, present):
    - bindings: verifier_id -> trusted principal (e.g. "github:LOGIN").
      Only object entries with both verifier_id and principal authenticate.
    - declared: bare-string entries — parseable as DECLARED_BUT_UNBOUND, which
      MUST NOT satisfy formal IV.
    - present: whether a pool block was found at all.

    Absent pool => ({}, [], False): fail-closed, no formal IV can be satisfied.
    """
    for _idx, raw in extract_payloads(issue_body or ""):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("schema") == "ATLAS_VERIFIER_POOL_V1":
            entries = payload.get("verifiers")
            if not isinstance(entries, list):
                return {}, [], True
            bindings: dict[str, str] = {}
            declared: list[str] = []
            for entry in entries:
                if isinstance(entry, str):
                    if entry.strip():
                        declared.append(entry.strip())
                elif isinstance(entry, dict):
                    verifier_id = entry.get("verifier_id")
                    principal = entry.get("principal")
                    if isinstance(verifier_id, str) and isinstance(principal, str) \
                            and verifier_id.strip() and principal.strip():
                        bindings[verifier_id.strip()] = principal.strip()
            return bindings, sorted(set(declared)), True
    return {}, [], False

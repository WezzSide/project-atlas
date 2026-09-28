"""Fail-closed merge gate: evidence-freshness recheck immediately before merge mutation.

Governance defect being closed (PR #1025 race, 2026-09-28): a blocking independent
verification (IV) comment was posted at 10:05:00Z and the merge happened at 10:05:38Z
because the merge decision relied on an earlier admission snapshot. The invariant this
module enforces, at the merge instant, is:

  * candidate HEAD and TREE are exactly the ones the merge authority was bound to;
  * base is exact (or the authority explicitly rebinds to a named new base);
  * every required CI run is terminal SUCCESS and bound to the exact HEAD;
  * the latest IV record is terminal, bound to the exact HEAD/TREE, verdict PASS with
    BLOCKING_P0=0 and BLOCKING_P1=0;
  * no blocking evidence (P0/P1 > 0, IV FAIL/REJECTED, security marker) exists that is
    newer than the authority decision -- regardless of author;
  * the authority itself is bound to the current candidate and not consumed.

Any violation => DENY. The decision is a receipt carrying timestamps and evidence ids,
so the merge log can later prove which evidence was current at the mutation instant.

Pure evaluation: `evaluate(authority, snapshot)` takes plain dicts (no network), so the
race can be tested deterministically. `collect_snapshot()` builds the same shape from
`gh` for operational use (`python -m controller.merge_gate --pr N --authority a.json`).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

BLOCKING_MARKERS = (
    re.compile(r"BLOCKING_P0\s*=\s*([1-9]\d*)", re.I),
    re.compile(r"BLOCKING_P1\s*=\s*([1-9]\d*)", re.I),
    re.compile(r"\bIV[_ ]VERDICT\s*[:=]\s*\**FAIL", re.I),
    re.compile(r"\bIV verdict:\s*\**FAIL", re.I),
    re.compile(r"\bIV_FAIL\b", re.I),
    re.compile(r"\bREJECTED\b"),
    re.compile(r"\bSECURITY[_ ]BLOCKER\b", re.I),
    re.compile(r"\bMERGE_AUTHORITY_INVALIDATED\b"),
)
PASS_MARKERS = (
    re.compile(r"\bIV_VERDICT\s*=\s*\**PASS", re.I),
    re.compile(r"\bIV[^\n]{0,40}\bPASS\b"),
)
P0_RE = re.compile(r"BLOCKING_P0\s*=\s*(\d+)", re.I)
P1_RE = re.compile(r"BLOCKING_P1\s*=\s*(\d+)", re.I)
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
TERMINAL_OK = {"success"}


def _ts(value: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclasses.dataclass(frozen=True)
class Decision:
    verdict: str  # "ALLOW" | "DENY"
    reasons: tuple[str, ...]
    receipt: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reasons": list(self.reasons), "receipt": self.receipt}


def _is_blocking(body: str) -> bool:
    return any(m.search(body or "") for m in BLOCKING_MARKERS)


def _is_iv_record(body: str) -> bool:
    b = body or ""
    return ("INDEPENDENT IV" in b.upper()) or bool(P0_RE.search(b) and P1_RE.search(b))


def evaluate(authority: dict[str, Any], snapshot: dict[str, Any]) -> Decision:
    """Decide ALLOW/DENY for one merge mutation.

    authority: {"pr", "head", "tree", "base", "decided_at", "required_checks": [names],
                "rebind_base": optional new base sha, "consumed": bool}
    snapshot:  {"observed_at", "pr": {"number","state","head","tree","base","merged"},
                "check_runs": [{"name","head_sha","status","conclusion","id","completed_at"}],
                "comments": [{"id","created_at","author","body"}],
                "reviews": [{"id","submitted_at","author","state","body"}]}
    """
    reasons: list[str] = []
    pr = snapshot.get("pr") or {}
    decided_at = _ts(authority["decided_at"])
    observed_at = _ts(snapshot["observed_at"])
    if observed_at < decided_at:
        reasons.append("SNAPSHOT_OLDER_THAN_AUTHORITY")
    if authority.get("consumed"):
        reasons.append("AUTHORITY_ALREADY_CONSUMED")
    if int(pr.get("number", -1)) != int(authority["pr"]):
        reasons.append("PR_MISMATCH")
    if pr.get("merged") or str(pr.get("state", "")).upper() != "OPEN":
        reasons.append("PR_NOT_OPEN")
    if pr.get("head") != authority["head"]:
        reasons.append(f"HEAD_DRIFT:{pr.get('head')}")
    if pr.get("tree") != authority["tree"]:
        reasons.append(f"TREE_DRIFT:{pr.get('tree')}")
    expected_base = authority.get("rebind_base") or authority["base"]
    if pr.get("base") != expected_base:
        reasons.append(f"BASE_DRIFT:{pr.get('base')}")

    # required CI: terminal SUCCESS, exact head, and current (newest run per name)
    runs_by_name: dict[str, list[dict[str, Any]]] = {}
    for r in snapshot.get("check_runs") or []:
        runs_by_name.setdefault(str(r.get("name")), []).append(r)
    ci_ids: dict[str, Any] = {}
    for name in authority.get("required_checks") or []:
        runs = [r for r in runs_by_name.get(name, []) if r.get("head_sha") == authority["head"]]
        if not runs:
            reasons.append(f"CI_MISSING:{name}")
            continue
        newest = max(runs, key=lambda r: str(r.get("completed_at") or r.get("created_at") or ""))
        if (
            newest.get("status") != "completed"
            or str(newest.get("conclusion", "")).lower() not in TERMINAL_OK
        ):
            reasons.append(
                f"CI_NOT_TERMINAL_SUCCESS:{name}:{newest.get('status')}/{newest.get('conclusion')}"
            )
        ci_ids[name] = newest.get("id")

    # evidence stream: comments + reviews, ordered by time
    events: list[dict[str, Any]] = []
    for c in snapshot.get("comments") or []:
        events.append(
            {
                "kind": "comment",
                "id": c.get("id"),
                "at": c["created_at"],
                "author": c.get("author"),
                "body": c.get("body") or "",
            }
        )
    for rv in snapshot.get("reviews") or []:
        body = (rv.get("body") or "") + (
            " REJECTED" if str(rv.get("state", "")).upper() == "CHANGES_REQUESTED" else ""
        )
        events.append(
            {
                "kind": "review",
                "id": rv.get("id"),
                "at": rv["submitted_at"],
                "author": rv.get("author"),
                "body": body,
            }
        )
    events.sort(key=lambda e: _ts(e["at"]))

    newer_blocking = [e for e in events if _ts(e["at"]) > decided_at and _is_blocking(e["body"])]
    for e in newer_blocking:
        reasons.append(f"NEWER_BLOCKING_EVIDENCE:{e['kind']}:{e['id']}@{e['at']}")

    iv_records = [e for e in events if _is_iv_record(e["body"])]
    latest_iv = iv_records[-1] if iv_records else None
    if latest_iv is None:
        reasons.append("IV_MISSING")
    else:
        body = latest_iv["body"]
        shas = set(SHA_RE.findall(body))
        if authority["head"] not in shas or authority["tree"] not in shas:
            reasons.append(f"IV_NOT_BOUND_TO_CANDIDATE:{latest_iv['id']}")
        p0 = int((P0_RE.search(body) or [None, "1"])[1])
        p1 = int((P1_RE.search(body) or [None, "1"])[1])
        if p0 or p1 or _is_blocking(body) or not any(m.search(body) for m in PASS_MARKERS):
            reasons.append(f"IV_NOT_PASS:{latest_iv['id']}:P0={p0}:P1={p1}")

    receipt = {
        "schema": "atlas-merge-gate-receipt/v1",
        "pr": authority["pr"],
        "authority": {
            k: authority.get(k) for k in ("head", "tree", "base", "rebind_base", "decided_at")
        },
        "observed_at": snapshot["observed_at"],
        "observed": {k: pr.get(k) for k in ("head", "tree", "base", "state", "merged")},
        "required_ci": ci_ids,
        "latest_iv": None
        if latest_iv is None
        else {"id": latest_iv["id"], "at": latest_iv["at"], "author": latest_iv["author"]},
        "newest_evidence": None
        if not events
        else {"id": events[-1]["id"], "at": events[-1]["at"], "kind": events[-1]["kind"]},
        "newer_blocking": [{"id": e["id"], "at": e["at"]} for e in newer_blocking],
    }
    verdict = "ALLOW" if not reasons else "DENY"
    return Decision(verdict, tuple(reasons), receipt)


# --- operational collector (gh-backed; injectable for tests) -------------------------------------


def _gh_json(args: list[str], runner: Callable[..., Any] = subprocess.run) -> Any:
    out = runner(["gh", *args], check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def collect_snapshot(
    repo: str, pr: int, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any]:
    p = _gh_json(["api", f"repos/{repo}/pulls/{pr}"], runner)
    head = p["head"]["sha"]
    tree = _gh_json(["api", f"repos/{repo}/git/commits/{head}"], runner)["tree"]["sha"]
    runs = (
        _gh_json(["api", f"repos/{repo}/actions/runs?head_sha={head}&per_page=100"], runner) or {}
    )
    comments = (
        _gh_json(["api", f"repos/{repo}/issues/{pr}/comments?per_page=100", "--paginate"], runner)
        or []
    )
    reviews = (
        _gh_json(["api", f"repos/{repo}/pulls/{pr}/reviews?per_page=100", "--paginate"], runner)
        or []
    )
    return {
        "observed_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pr": {
            "number": p["number"],
            "state": p["state"].upper(),
            "merged": bool(p.get("merged")),
            "head": head,
            "tree": tree,
            "base": p["base"]["sha"],
        },
        "check_runs": [
            {
                "name": r["name"],
                "head_sha": r["head_sha"],
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "id": r["id"],
                "completed_at": r.get("updated_at"),
                "created_at": r.get("created_at"),
            }
            for r in runs.get("workflow_runs", [])
        ],
        "comments": [
            {
                "id": c["id"],
                "created_at": c["created_at"],
                "author": c["user"]["login"],
                "body": c.get("body") or "",
            }
            for c in comments
        ],
        "reviews": [
            {
                "id": r["id"],
                "submitted_at": r.get("submitted_at") or r.get("created_at"),
                "author": r["user"]["login"],
                "state": r.get("state"),
                "body": r.get("body") or "",
            }
            for r in reviews
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import pathlib

    ap = argparse.ArgumentParser(
        description="Pre-merge evidence-freshness gate (read-only; never merges)"
    )
    ap.add_argument("--repo", default="WezzSide/project-atlas")
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument(
        "--authority",
        required=True,
        help="JSON file: pr, head, tree, base, decided_at, required_checks",
    )
    ap.add_argument("--receipt", help="write decision receipt JSON here")
    a = ap.parse_args(argv)
    authority = json.loads(pathlib.Path(a.authority).read_text(encoding="utf-8"))
    decision = evaluate(authority, collect_snapshot(a.repo, a.pr))
    text = json.dumps(decision.to_dict(), indent=2)
    if a.receipt:
        pathlib.Path(a.receipt).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if decision.verdict == "ALLOW" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

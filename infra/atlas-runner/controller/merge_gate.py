"""Fail-closed merge gate: evidence-freshness recheck immediately before merge mutation.

Governance defect being closed (PR #1025 race, 2026-09-28): a blocking independent
verification (IV) comment was posted at 10:05:00Z and the merge happened at 10:05:38Z
because the merge decision relied on an earlier admission snapshot. The invariant this
module enforces, at the merge instant, is:

  * candidate HEAD and TREE are exactly the ones the merge authority was bound to;
  * base is exact (or the authority explicitly rebinds to a named new base);
  * every required CI run is terminal SUCCESS and bound to the exact HEAD;
  * the positive IV record is the exact one the authority was bound to (comment id, author,
    body sha256, updated_at — optionally restricted to a trusted verifier allowlist), unedited
    since binding, bound to the exact HEAD/TREE, and its canonical record fields say
    IV_VERDICT=PASS with BLOCKING_P0=0 and BLOCKING_P1=0 (fenced code, HTML comments, inline
    code, quotes and URLs never count);
  * no blocking evidence (P0/P1 > 0, IV FAIL/REJECTED, security marker) exists whose effective
    (edit-aware) timestamp is at or after the authority decision -- regardless of author;
  * the authority itself is bound to the current candidate and not consumed.

Any violation => DENY. The decision is a receipt carrying timestamps and evidence ids,
so the merge log can later prove which evidence was current at the mutation instant.

Pure evaluation: `evaluate(authority, snapshot)` takes plain dicts (no network), so the
race can be tested deterministically. `collect_snapshot()` builds the same shape from
`gh` for operational use (`python -m controller.merge_gate --pr N --authority a.json`).
Authority file fields: pr, head, tree, base, decided_at, required_checks,
iv_binding{iv_evidence_id, iv_author, iv_updated_at, iv_body_sha256}, optional
trusted_iv_authors, optional rebind_base, consumed.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

BLOCKING_MARKERS = (
    re.compile(r"BLOCKING_P0\s*=\s*([1-9]\d*)", re.I),
    re.compile(r"BLOCKING_P1\s*=\s*([1-9]\d*)", re.I),
    re.compile(r"\bIV_VERDICT\s*=\s*`?\**FAIL", re.I),
    re.compile(r"\bIV verdict:\s*\**FAIL", re.I),
    re.compile(r"\bIV_FAIL\b", re.I),
    re.compile(r"\bREJECTED\b"),
    re.compile(r"\bSECURITY[_ ]BLOCKER\b", re.I),
    re.compile(r"\bMERGE_AUTHORITY_INVALIDATED\b"),
)
# Canonical IV record fields are whole lines of the form KEY=VALUE (optionally **bold** or a list
# item). Fenced/indented code, code spans and HTML comments/blocks are stripped BEFORE parsing, so
# examples, quotes and hidden markup can never establish a verdict. No fuzzy fallback exists.
RECORD_KEYS = ("IV_VERDICT", "BLOCKING_P0", "BLOCKING_P1")
RECORD_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?\*{0,2}(IV_VERDICT|BLOCKING_P0|BLOCKING_P1)\s*=\s*([A-Za-z0-9_]+)\*{0,2}\s*[.;]?\s*$"
)
FENCE_RE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.S)
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
HTML_BLOCK_RE = re.compile(r"<(pre|code|script|style)\b.*?(?:</\1\s*>|\Z)", re.S | re.I)
# CommonMark indented code block: >=4 spaces / tab after a block boundary (body start, blank
# or whitespace-only line, ATX heading). Bodies are CRLF-normalised before this is applied.
INDENTED_CODE_RE = re.compile(r"(\A|\n[ \t]*\n|(?:\A|\n)#[^\n]*\n)((?:(?: {4}|\t)[^\n]*\n?)+)")
# Stripped contexts are replaced by a visible placeholder, never by bare whitespace, so text that
# shared a line with a code span / fence / comment cannot become a whole-line record.
STRIPPED = "[stripped]"
BACKTICK_RUN_RE = re.compile(r"`+")
BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
SHA_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
TERMINAL_OK = {"success"}


class MalformedEvidence(ValueError):
    """Evidence that cannot be interpreted; the caller must DENY, never ALLOW."""


def _ts(value: Any) -> _dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise MalformedEvidence(f"timestamp missing or not a string: {value!r}")
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedEvidence(f"timestamp not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise MalformedEvidence(f"timestamp has no timezone: {value!r}")
    return parsed


@dataclasses.dataclass(frozen=True)
class Decision:
    verdict: str  # "ALLOW" | "DENY"
    reasons: tuple[str, ...]
    receipt: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reasons": list(self.reasons), "receipt": self.receipt}


def _strip_code_spans(text: str) -> str:
    """CommonMark code spans: a backtick run of length n opens a span closed by the next run of
    exactly length n (runs of other lengths are literal); a span never crosses a blank line.
    Any length of run is handled (`x`, ``x``, ``` `x` ```), unlike a single-backtick regex."""
    runs = BACKTICK_RUN_RE.finditer(text)
    runs = list(runs)
    out: list[str] = []
    pos = 0
    i = 0
    while i < len(runs):
        opener = runs[i]
        n = opener.end() - opener.start()
        j = i + 1
        while j < len(runs) and (runs[j].end() - runs[j].start()) != n:
            j += 1
        if j < len(runs) and not BLANK_LINE_RE.search(text[opener.end() : runs[j].start()]):
            out.append(text[pos : opener.start()])
            out.append(STRIPPED)
            pos = runs[j].end()
            i = j + 1
        else:
            i += 1
    out.append(text[pos:])
    return "".join(out)


def _strip_non_record_context(body: str, *, inline_code: bool = True) -> str:
    """Remove fenced/indented code, HTML comments and pre/code blocks (terminated or not) and,
    by default, code spans. Nothing inside those contexts is a record field. Line endings are
    normalised first so CRLF bodies (GitHub web submissions) get identical treatment."""
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    text = FENCE_RE.sub(STRIPPED, text)
    text = HTML_COMMENT_RE.sub(STRIPPED, text)
    text = HTML_BLOCK_RE.sub(STRIPPED, text)
    text = INDENTED_CODE_RE.sub(lambda m: m.group(1) + STRIPPED + "\n", text)
    return _strip_code_spans(text) if inline_code else text


def parse_iv_record(body: str) -> dict[str, Any]:
    """Deterministic structured parse of an IV record.

    Returns {"verdict": PASS|FAIL|MISSING|AMBIGUOUS|MALFORMED, "p0": int|None, "p1": int|None}.
    Only whole canonical lines count; duplicates with differing values => AMBIGUOUS; a key with a
    value outside its domain => MALFORMED. Never raises.
    """
    found: dict[str, set[str]] = {k: set() for k in RECORD_KEYS}
    for line in _strip_non_record_context(body).splitlines():
        m = RECORD_LINE_RE.match(line)
        if m:
            found[m.group(1)].add(m.group(2))  # case-exact: PASS/FAIL only
    out: dict[str, Any] = {"verdict": "MISSING", "p0": None, "p1": None}
    vals = found["IV_VERDICT"]
    if len(vals) > 1:
        out["verdict"] = "AMBIGUOUS"
    elif len(vals) == 1:
        v = next(iter(vals))
        out["verdict"] = v if v in {"PASS", "FAIL"} else "MALFORMED"
    for key, slot in (("BLOCKING_P0", "p0"), ("BLOCKING_P1", "p1")):
        vs = found[key]
        if len(vs) == 1 and next(iter(vs)).isdigit():
            out[slot] = int(next(iter(vs)))
        elif vs:
            out["verdict"] = "MALFORMED" if out["verdict"] == "PASS" else out["verdict"]
            out[slot] = None
    return out


def _body_sha256(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def _sha(value: Any) -> str | None:
    """Normalize a 40-hex sha to lowercase; anything else -> None (never equal to a real sha)."""
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value.strip()):
        return value.strip().lower()
    return None


def _is_blocking(body: str) -> bool:
    return any(m.search(body or "") for m in BLOCKING_MARKERS)


def evaluate(authority: dict[str, Any], snapshot: dict[str, Any]) -> Decision:
    """Decide ALLOW/DENY for one merge mutation; malformed inputs always DENY (never raise)."""
    try:
        return _evaluate(authority, snapshot)
    except (MalformedEvidence, KeyError, TypeError, ValueError, AttributeError) as exc:
        return Decision(
            "DENY",
            (f"MALFORMED_EVIDENCE:{type(exc).__name__}:{str(exc)[:120]}",),
            {
                "schema": "atlas-merge-gate-receipt/v1",
                "pr": authority.get("pr") if isinstance(authority, dict) else None,
                "observed_at": snapshot.get("observed_at") if isinstance(snapshot, dict) else None,
                "malformed": True,
            },
        )


def _evaluate(authority: dict[str, Any], snapshot: dict[str, Any]) -> Decision:
    """Decide ALLOW/DENY for one merge mutation.

    authority: {"pr", "head", "tree", "base", "decided_at", "required_checks": [names],
                "iv_binding": {"iv_evidence_id", "iv_author", "iv_updated_at", "iv_body_sha256"},
                "trusted_iv_authors": optional non-empty allowlist,
                "rebind_base": optional new base sha, "consumed": bool}
    snapshot:  {"observed_at", "pr": {"number","state","head","tree","base","merged"},
                "check_runs": [{"name","head_sha","status","conclusion","id","completed_at"}],
                "comments": [{"id","created_at","updated_at","author","body"}],
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
    a_head, a_tree = _sha(authority["head"]), _sha(authority["tree"])
    if a_head is None or a_tree is None or _sha(authority["base"]) is None:
        raise MalformedEvidence("authority head/tree/base must be 40-hex shas")
    if authority.get("rebind_base") is not None and _sha(authority["rebind_base"]) is None:
        raise MalformedEvidence("authority rebind_base must be a 40-hex sha")
    if _sha(pr.get("head")) != a_head:
        reasons.append(f"HEAD_DRIFT:{pr.get('head')}")
    if _sha(pr.get("tree")) != a_tree:
        reasons.append(f"TREE_DRIFT:{pr.get('tree')}")
    expected_base = _sha(authority.get("rebind_base") or authority["base"])
    if _sha(pr.get("base")) != expected_base:
        reasons.append(f"BASE_DRIFT:{pr.get('base')}")

    # required CI: terminal SUCCESS, exact head, and current (newest run per name)
    runs_by_name: dict[str, list[dict[str, Any]]] = {}
    for r in snapshot.get("check_runs") or []:
        runs_by_name.setdefault(str(r.get("name")), []).append(r)
    ci_ids: dict[str, Any] = {}
    required = authority.get("required_checks")
    if not isinstance(required, list) or not required:
        reasons.append("NO_REQUIRED_CHECKS")
        required = []
    elif any(not isinstance(n, str) or not n.strip() for n in required):
        reasons.append("MALFORMED_REQUIRED_CHECKS")
        required = []
    for name in required:
        runs = [r for r in runs_by_name.get(name, []) if _sha(r.get("head_sha")) == a_head]
        if not runs:
            reasons.append(f"CI_MISSING:{name}")
            continue
        newest = max(runs, key=lambda r: _ts(r.get("completed_at") or r.get("created_at")))
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
        created = _ts(c["created_at"])
        updated = _ts(c["updated_at"]) if c.get("updated_at") else created
        if updated < created:
            raise MalformedEvidence(f"comment {c.get('id')} updated_at precedes created_at")
        events.append(
            {
                "kind": "comment",
                "id": c.get("id"),
                "at": (c.get("updated_at") or c["created_at"]),  # effective (edit-aware)
                "created_at": c["created_at"],
                "updated_at": c.get("updated_at") or c["created_at"],
                "author": c.get("author"),
                "body": c.get("body") or "",
            }
        )
    for rv in snapshot.get("reviews") or []:
        state = str(rv.get("state") or "").upper()
        if state not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}:
            raise MalformedEvidence(f"review {rv.get('id')} has unknown state {state!r}")
        body = (rv.get("body") or "") + (" REJECTED" if state == "CHANGES_REQUESTED" else "")
        events.append(
            {
                "kind": "review",
                "id": rv.get("id"),
                "at": rv["submitted_at"],
                "created_at": rv["submitted_at"],
                "updated_at": rv["submitted_at"],
                "author": rv.get("author"),
                "body": body,
            }
        )
    events.sort(key=lambda e: _ts(e["at"]))

    newer_blocking = [e for e in events if _ts(e["at"]) >= decided_at and _is_blocking(e["body"])]
    for e in newer_blocking:
        reasons.append(f"NEWER_BLOCKING_EVIDENCE:{e['kind']}:{e['id']}@{e['at']}")

    # Positive IV evidence must be the exact record the authority was bound to: id + author +
    # body hash + updated_at. Free-form PR text never establishes PASS.
    binding = authority.get("iv_binding")
    latest_iv: dict[str, Any] | None = None
    if not isinstance(binding, dict) or not all(
        binding.get(k) for k in ("iv_evidence_id", "iv_author", "iv_updated_at", "iv_body_sha256")
    ):
        reasons.append("IV_BINDING_MISSING")
    else:
        trusted = authority.get("trusted_iv_authors")
        if trusted is not None and (
            not isinstance(trusted, list) or not trusted or binding["iv_author"] not in trusted
        ):
            reasons.append("IV_AUTHOR_UNTRUSTED")
        if _ts(binding["iv_updated_at"]) >= decided_at:
            reasons.append("IV_BINDING_NOT_BEFORE_AUTHORITY")
        bound = [
            e
            for e in events
            if e["kind"] == "comment" and str(e["id"]) == str(binding["iv_evidence_id"])
        ]
        if len(bound) != 1:
            reasons.append(f"IV_EVIDENCE_NOT_FOUND:{binding['iv_evidence_id']}")
        else:
            latest_iv = bound[0]
            body = latest_iv["body"]
            if latest_iv["author"] != binding["iv_author"]:
                reasons.append(f"IV_AUTHOR_MISMATCH:{latest_iv['author']}")
            if _body_sha256(body) != str(binding["iv_body_sha256"]).lower():
                reasons.append(f"IV_BODY_HASH_MISMATCH:{latest_iv['id']}")
            if _ts(latest_iv["updated_at"]) != _ts(binding["iv_updated_at"]):
                reasons.append(f"IV_EDITED:{latest_iv['id']}@{latest_iv['updated_at']}")
            if _ts(latest_iv["updated_at"]) >= decided_at:
                reasons.append(f"IV_EDITED_AFTER_AUTHORITY:{latest_iv['id']}")
            # binding SHAs are conventionally written in backticks, so keep inline code here
            visible = _strip_non_record_context(body, inline_code=False)
            shas = {x.lower() for x in SHA_RE.findall(visible)}
            if a_head not in shas or a_tree not in shas:
                reasons.append(f"IV_NOT_BOUND_TO_CANDIDATE:{latest_iv['id']}")
            rec = parse_iv_record(body)
            if (
                rec["verdict"] != "PASS"
                or rec["p0"] is None
                or rec["p1"] is None
                or rec["p0"]
                or rec["p1"]
                or _is_blocking(_strip_non_record_context(body))
            ):
                reasons.append(
                    f"IV_NOT_PASS:{latest_iv['id']}:verdict={rec['verdict']}:"
                    f"P0={rec['p0']}:P1={rec['p1']}"
                )

    receipt = {
        "schema": "atlas-merge-gate-receipt/v1",
        "pr": authority["pr"],
        "authority": {
            "head": a_head,
            "tree": a_tree,
            "base": _sha(authority["base"]),
            "rebind_base": _sha(authority.get("rebind_base")),
            "decided_at": authority["decided_at"],
        },
        "observed_at": snapshot["observed_at"],
        "observed": {k: pr.get(k) for k in ("head", "tree", "base", "state", "merged")},
        "required_ci": ci_ids,
        "iv_binding": None
        if not isinstance(binding, dict)
        else {
            k: binding.get(k)
            for k in ("iv_evidence_id", "iv_author", "iv_updated_at", "iv_body_sha256")
        },
        "latest_iv": None
        if latest_iv is None
        else {
            "id": latest_iv["id"],
            "at": latest_iv["at"],
            "updated_at": latest_iv["updated_at"],
            "author": latest_iv["author"],
            "body_sha256": _body_sha256(latest_iv["body"]),
        },
        "newest_evidence": None
        if not events
        else {"id": events[-1]["id"], "at": events[-1]["at"], "kind": events[-1]["kind"]},
        "newer_blocking": [{"id": e["id"], "at": e["at"]} for e in newer_blocking],
    }
    verdict = "ALLOW" if not reasons else "DENY"
    return Decision(verdict, tuple(reasons), receipt)


# --- operational collector (gh-backed; injectable for tests) -------------------------------------


def _gh_json(args: list[str], runner: Callable[..., Any] = subprocess.run) -> Any:
    try:
        out = runner(["gh", *args], check=True, capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, OSError) as exc:
        raise MalformedEvidence(f"gh api failed: {' '.join(args)}: {exc}") from exc
    try:
        return json.loads(out) if out.strip() else None
    except ValueError as exc:
        raise MalformedEvidence(f"gh api returned non-JSON for {' '.join(args)}") from exc


def _gh_paginated(path: str, runner: Callable[..., Any]) -> list[Any]:
    """All pages of a list endpoint; --slurp yields one JSON array of page arrays."""
    pages = _gh_json(["api", path, "--paginate", "--slurp"], runner) or []
    items: list[Any] = []
    for page in pages:
        if not isinstance(page, list):
            raise MalformedEvidence(f"unexpected page shape for {path}")
        items.extend(page)
    return items


def collect_snapshot(
    repo: str, pr: int, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any]:
    p = _gh_json(["api", f"repos/{repo}/pulls/{pr}"], runner)
    if not isinstance(p, dict):
        raise MalformedEvidence("pull request payload missing")
    head = p["head"]["sha"]
    tree = _gh_json(["api", f"repos/{repo}/git/commits/{head}"], runner)["tree"]["sha"]
    run_pages = (
        _gh_json(
            [
                "api",
                f"repos/{repo}/actions/runs?head_sha={head}&per_page=100",
                "--paginate",
                "--slurp",
            ],
            runner,
        )
        or []
    )
    runs: list[dict[str, Any]] = []
    for page in run_pages:
        if not isinstance(page, dict) or not isinstance(page.get("workflow_runs"), list):
            raise MalformedEvidence("unexpected workflow_runs page shape")
        runs.extend(page["workflow_runs"])
    comments = _gh_paginated(f"repos/{repo}/issues/{pr}/comments?per_page=100", runner)
    reviews = _gh_paginated(f"repos/{repo}/pulls/{pr}/reviews?per_page=100", runner)
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
            for r in runs
        ],
        "comments": [
            {
                "id": c["id"],
                "created_at": c["created_at"],
                "updated_at": c.get("updated_at") or c["created_at"],
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
        help="JSON file: pr, head, tree, base, decided_at, required_checks, iv_binding",
    )
    ap.add_argument("--receipt", help="write decision receipt JSON here")
    a = ap.parse_args(argv)
    authority = json.loads(pathlib.Path(a.authority).read_text(encoding="utf-8"))
    try:
        snapshot = collect_snapshot(a.repo, a.pr)
    except MalformedEvidence as exc:
        decision = Decision(
            "DENY",
            (f"EVIDENCE_COLLECTION_FAILED:{exc}",),
            {"schema": "atlas-merge-gate-receipt/v1", "pr": a.pr},
        )
    else:
        decision = evaluate(authority, snapshot)
    text = json.dumps(decision.to_dict(), indent=2)
    if a.receipt:
        pathlib.Path(a.receipt).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if decision.verdict == "ALLOW" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""AS-CODER-ALPHA-DECISIONS-001/003 — Decision memory derived lens from Core.

Surfaces important decisions from:
- Layer B ``projects/<id>/decisions.md`` headings (when present)
- Layer A imported ADR/formal decision docs (heading harvest)
- persisted claims with ``claim_type == decision``
- durable human dispositions under ``state/human-decisions/``

ACTIVE_GOVERNING requires defensible authority evidence (D-043).
Honesty: lens != authority; UNKNOWN when no decision evidence exists.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from project_atlas.inventory_drift import attach_source_drift
from project_atlas.secrets import scan_text

PACKAGE_ID = "AS-CODER-ALPHA-DECISIONS-001"
GENERATOR_ID = "atlas-coder-alpha-decisions-001"
ANSWERS_RELATIVE = Path("generated") / "answers"
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_ADR_TITLE_RE = re.compile(r"\bADR[- ]?\d+\b", re.IGNORECASE)
_DECISION_PATH_HINTS = ("decision", "adr", "rfc")
_SECTION_HEADER_NOISE = frozenset(
    {
        "context",
        "consequences",
        "decision",
        "status",
        "background",
        "summary",
        "overview",
        "notes",
        "references",
        "related",
        "migration and validation",
        "options",
        "alternatives",
        "rationale",
        "discussion",
        "appendix",
        "see also",
        "next steps",
        "changelog",
        "cli integration",
        "current state",
        "dependency and capability reports",
        "determinism",
        "canonical-state boundary",
        "implementation",
        "motivation",
        "problem",
        "proposal",
        "goals",
        "non-goals",
        "scope",
        "out of scope",
        "future work",
        "open questions",
        "security",
        "testing",
        "rollout",
        "compatibility",
    }
)
_NON_DECISION_HINTS = (
    "implementation note",
    "changelog",
    "release notes",
    "todo",
    "scratch",
    "release certified",
    "accepted = yes",
    "receipt.md",
    "signed receipt",
)


def _decision_source_path(path: str) -> bool:
    """True for DECISIONS.md / ADR-like paths. README is not governing evidence."""
    posix = path.replace("\\", "/").removeprefix("./")
    lower = posix.lower()
    name = Path(posix).name.lower()
    if name in {"readme.md", "readme.txt", "readme"}:
        return False
    if name == "decisions.md":
        return True
    if name.startswith(("adr-", "adr_", "rfc-", "rfc_")):
        return True
    return any(hint in lower for hint in _DECISION_PATH_HINTS)


def _is_section_header_noise(title: str) -> bool:
    lowered = title.strip().lower().rstrip(":")
    if lowered in _SECTION_HEADER_NOISE:
        return True
    # Single-token structural headings are not governing decisions.
    return " " not in lowered and lowered in {
        "context",
        "consequences",
        "decision",
        "status",
        "background",
        "summary",
        "options",
        "rationale",
    }


def _looks_like_formal_decision(title: str, *, path: str = "") -> bool:
    """True when title evidence supports a governing decision statement.

    Path alone is insufficient — ADR files contain many structural headings
    that must not become ACTIVE_GOVERNING (D-043).
    """
    if _is_section_header_noise(title):
        return False
    if _ADR_TITLE_RE.search(title):
        return True
    lower = title.strip().lower()
    # Numbered decision bullets common in ADR bodies.
    if re.match(r"^\d+\.\s+\S+", title.strip()) and len(lower.split()) >= 4:
        return True
    if lower.startswith(("decide ", "decision:", "we will ", "adopt ", "use ", "prefer ")):
        return True
    if "prefer " in lower or "must " in lower or "shall " in lower:
        return len(lower.split()) >= 4
    return False


def _classify_decision_status(
    title: str,
    *,
    kind: str,
    path: str = "",
    lifecycle: str | None = None,
    verification: str | None = None,
    authority: str | None = None,
) -> str:
    """Deterministic decision status label (no confidence score).

    ACTIVE_GOVERNING requires defensible authority evidence (D-043):
    owner disposition, accepted human review, ADR/formal decision, or
    verified decision claim — not bare headings/receipts/status prose.
    """
    life = (lifecycle or "").strip().lower()
    ver = (verification or "").strip().lower()
    auth = (authority or "").strip().lower()
    if life in {"superseded", "stale", "stale-or-superseded"}:
        return "SUPERSEDED"
    if life in {"rejected"} or ver in {"rejected"}:
        return "REJECTED"
    if life in {"contradicted"}:
        return "OPEN_PROPOSED"
    if ver in {"pending", "conflicting"}:
        return "OPEN_PROPOSED"

    lower = title.strip().lower()
    path_l = path.lower()
    if _is_section_header_noise(title):
        return "NON_DECISION"
    if any(hint in lower for hint in _NON_DECISION_HINTS):
        return "NON_DECISION"
    if "superseded" in lower or "obsolete" in lower:
        return "SUPERSEDED"
    if "rejected" in lower or "declined" in lower:
        return "REJECTED"
    if "proposed" in lower or "open question" in lower or "wip" in lower:
        return "OPEN_PROPOSED"
    if "historical" in lower or "archive" in path_l:
        return "HISTORICAL"
    # Certification / receipt theatre is not a governing product decision.
    if any(
        token in path_l for token in ("/receipt", "receipt.md", "/demo/", "fixtures/", "worklog")
    ):
        return "NON_DECISION"

    # Explicit human / owner dispositions are governing when accepted.
    if auth in {"human-disposition", "owner-disposition"} or kind == "human-disposition":
        return "ACTIVE_GOVERNING"

    # Verified / accepted decision claims are governing (verification is the
    # authority evidence). Unverified claims need formal shape (ADR/etc.).
    if kind == "claim":
        if ver in {"verified", "accepted"}:
            return "ACTIVE_GOVERNING"
        if _ADR_TITLE_RE.search(title) or _looks_like_formal_decision(title, path=path):
            return "ACTIVE_GOVERNING"
        return "OPEN_PROPOSED"

    # ADR / formal decision records — only ADR-titled or formal decision prose.
    if "adr" in path_l or kind == "adr-heading":
        if _looks_like_formal_decision(title, path=path):
            return "ACTIVE_GOVERNING"
        if _is_section_header_noise(title):
            return "NON_DECISION"
        # Other ADR headings are structural, not governing decisions.
        return "NON_DECISION"

    # Project decisions note: only formal-looking titles govern.
    if kind == "project-note":
        if _looks_like_formal_decision(title, path=path):
            return "ACTIVE_GOVERNING"
        return "OPEN_PROPOSED"

    # Generic imported headings default to proposed, never auto-governing.
    if kind == "imported-heading":
        if _looks_like_formal_decision(title, path=path):
            return "ACTIVE_GOVERNING"
        return "OPEN_PROPOSED"

    return "IMPLEMENTATION_NOTE"


class ProjectDecisionsError(ValueError):
    """Fail-closed decisions lens error."""


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _list_projects(vault: Path) -> list[str]:
    root = vault / "projects"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _safe_project_id(project_id: str) -> str:
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ProjectDecisionsError(f"unsafe project id: {project_id!r}")
    return project_id


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _headings(text: str) -> list[str]:
    titles: list[str] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group(2).strip()
        if not title:
            continue
        # Skip boilerplate titles.
        lowered = title.lower()
        if lowered.startswith("decisions"):
            continue
        if lowered in {"stack", "architecture", "overview", "readme"}:
            continue
        titles.append(title)
    return titles


def _decision_headings_from_note(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if "_No verified agent events" in text and text.count("#") <= 1:
        return []
    return _headings(text)


def _manifest_sources(vault: Path) -> list[dict[str, Any]]:
    payload = _read_json(vault / "generated" / "ops" / "connect-manifest.json")
    if not payload:
        return []
    rows = payload.get("sources")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and not row.get("exclusion_reason")]


def _claims_by_id(vault: Path, project_id: str) -> dict[str, dict[str, Any]]:
    payload = _read_json(vault / "state" / "claims" / f"{project_id}.json")
    if not payload:
        return {}
    claims = payload.get("claims")
    if not isinstance(claims, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "").strip()
        if claim_id:
            out[claim_id] = claim
    return out


def _is_decision_bearing_claim(claim: dict[str, Any]) -> bool:
    claim_type = str(claim.get("claim_type") or "").strip().lower()
    field = str(claim.get("field") or "").strip().lower()
    return claim_type == "decision" or "decision" in claim_type or field == "decision"


def _claim_decision_title(claim: dict[str, Any]) -> str:
    return str(
        claim.get("value") or claim.get("normalized_text") or claim.get("field") or ""
    ).strip()


def _human_disposition_decisions(vault: Path, project_id: str) -> list[dict[str, str]]:
    """Load accepted human dispositions as governing decision evidence.

    Only decision-bearing reviews promote to ACTIVE_GOVERNING. Accept/reject of
    tech-stack or other non-decision claims must not pollute governing status
    with bare ``review_id`` labels (D-043 review P1).
    """
    path = vault / "state" / "human-decisions" / f"{project_id}.json"
    payload = _read_json(path)
    if not payload:
        return []
    claims = _claims_by_id(vault, project_id)
    out: list[dict[str, str]] = []
    for entry in payload.get("decisions") or []:
        if not isinstance(entry, dict):
            continue
        decision = str(entry.get("decision") or "").lower()
        if decision not in {"accept", "accepted"}:
            continue

        category = str(entry.get("category") or "").strip().lower()
        subject_id = str(entry.get("subject_id") or "").strip()
        winner = str(entry.get("winner_claim_id") or "").strip()
        claim_id = ""
        if category == "conflict" and winner:
            claim_id = winner
        elif category in {"pending-claim", "low-confidence"} and subject_id:
            claim_id = subject_id

        title = ""
        if claim_id:
            claim = claims.get(claim_id)
            if claim is None or not _is_decision_bearing_claim(claim):
                continue
            title = _claim_decision_title(claim)
        else:
            # Explicit owner notes only — never promote bare review_id.
            title = str(entry.get("summary") or entry.get("note") or "").strip()
            if not title or not _looks_like_formal_decision(title):
                continue

        if not title or _is_section_header_noise(title):
            continue
        out.append(
            {
                "title": title[:200],
                "kind": "human-disposition",
                "status": "ACTIVE_GOVERNING",
                "authority": "human-disposition",
                "path": path.relative_to(vault).as_posix(),
                "source": path.relative_to(vault).as_posix(),
            }
        )
    return out[:20]


def _decision_headings_from_imports(vault: Path, project_id: str) -> list[dict[str, str]]:
    """Harvest decision-like headings from imported Layer A docs."""
    rows = _manifest_sources(vault)
    found: list[dict[str, str]] = []
    for row in rows:
        likely = str(row.get("likely_project") or "unknown-project")
        if likely not in {project_id, "unknown-project"}:
            continue
        path = str(row.get("path") or "")
        source_id = str(row.get("source_id") or "")
        path_l = path.lower()
        if not any(hint in path_l for hint in _DECISION_PATH_HINTS):
            continue
        if not source_id:
            continue
        imported = vault / "sources" / "imported-documents" / f"{source_id}.md"
        if not imported.is_file():
            continue
        try:
            text = imported.read_text(encoding="utf-8")
        except OSError:
            continue
        kind = "adr-heading" if "adr" in path_l else "imported-heading"
        for title in _headings(text)[:12]:
            status = _classify_decision_status(title, kind=kind, path=path)
            if status == "NON_DECISION":
                continue
            found.append(
                {
                    "title": title,
                    "source": imported.relative_to(vault).as_posix(),
                    "kind": kind,
                    "path": path,
                    "status": status,
                    "authority": "adr" if kind == "adr-heading" else "imported-heading",
                }
            )
    return found[:20]


def _decision_claims(vault: Path, project_id: str) -> list[dict[str, str]]:
    payload = _read_json(vault / "state" / "claims" / f"{project_id}.json")
    if not payload:
        return []
    claims = payload.get("claims")
    if not isinstance(claims, list):
        return []
    out: list[dict[str, str]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_type = str(claim.get("claim_type") or "")
        if claim_type != "decision" and "decision" not in claim_type:
            continue
        title = str(claim.get("value") or claim.get("normalized_text") or claim.get("field") or "")
        if not title.strip():
            continue
        claim_id = str(claim.get("claim_id") or "")
        # AS-SEC-SCAN-DECISIONS-JSON-ESC-001: json.loads of claims can decode
        # \\u escapes that scan_text misses on raw bytes.
        if scan_text(title) or scan_text(claim_id):
            continue
        status = _classify_decision_status(
            title,
            kind="claim",
            lifecycle=str(claim.get("lifecycle") or "") or None,
            verification=str(claim.get("verification") or "") or None,
        )
        out.append(
            {
                "title": title.strip()[:200],
                "claim_id": claim_id,
                "kind": "claim",
                "status": status,
                "authority": "claim",
                "lifecycle": str(claim.get("lifecycle") or ""),
            }
        )
    return out[:20]


def build_decisions_lens(vault: Path, project_id: str) -> dict[str, Any]:
    """Build one derived decisions lens for ``project_id``."""
    project_id = _safe_project_id(project_id)
    inspected: list[str] = []
    decisions: list[dict[str, str]] = []

    human_path = vault / "state" / "human-decisions" / f"{project_id}.json"
    if human_path.is_file():
        inspected.append(human_path.relative_to(vault).as_posix())
    decisions.extend(_human_disposition_decisions(vault, project_id))

    note = vault / "projects" / project_id / "decisions.md"
    if note.is_file():
        inspected.append(note.relative_to(vault).as_posix())
        for title in _decision_headings_from_note(note):
            status = _classify_decision_status(title, kind="project-note")
            if status == "NON_DECISION":
                continue
            decisions.append(
                {
                    "title": title,
                    "source": note.relative_to(vault).as_posix(),
                    "kind": "project-note",
                    "status": status,
                    "authority": "project-note",
                }
            )

    claims_path = vault / "state" / "claims" / f"{project_id}.json"
    if claims_path.is_file():
        inspected.append(claims_path.relative_to(vault).as_posix())
    decisions.extend(_decision_claims(vault, project_id))

    manifest_path = vault / "generated" / "ops" / "connect-manifest.json"
    if manifest_path.is_file():
        inspected.append(manifest_path.relative_to(vault).as_posix())
    decisions.extend(_decision_headings_from_imports(vault, project_id))

    # Deduplicate by normalized title; prefer higher status, then authority.
    status_rank = {
        "ACTIVE_GOVERNING": 0,
        "OPEN_PROPOSED": 1,
        "IMPLEMENTATION_NOTE": 2,
        "HISTORICAL": 3,
        "SUPERSEDED": 4,
        "REJECTED": 5,
        "NON_DECISION": 6,
    }
    authority_rank = {
        "human-disposition": 0,
        "owner-disposition": 0,
        "adr": 1,
        "claim": 2,
        "project-note": 3,
        "imported-heading": 4,
    }
    best: dict[str, dict[str, str]] = {}
    for item in decisions:
        key = item["title"].strip().lower()
        prior = best.get(key)
        if prior is None:
            best[key] = item
            continue
        item_status = status_rank.get(item.get("status", ""), 9)
        prior_status = status_rank.get(prior.get("status", ""), 9)
        if item_status < prior_status:
            best[key] = item
            continue
        if item_status == prior_status and authority_rank.get(
            item.get("authority", ""), 9
        ) < authority_rank.get(prior.get("authority", ""), 9):
            best[key] = item
    unique = sorted(
        best.values(),
        key=lambda item: (
            status_rank.get(str(item.get("status")), 9),
            authority_rank.get(str(item.get("authority")), 9),
            item["title"].lower(),
        ),
    )

    if unique:
        status = "derived"
        active = [item for item in unique if item.get("status") == "ACTIVE_GOVERNING"]
        focus = active or [
            item
            for item in unique
            if item.get("status") not in {"NON_DECISION", "HISTORICAL", "SUPERSEDED", "REJECTED"}
        ]
        focus = focus or unique
        titles = [
            f"{item['title']} [{item.get('status', 'UNKNOWN')}]" for item in focus[:8]
        ]
        summary = (
            f"decisions={len(unique)}; active_governing={len(active)}; "
            + "; ".join(titles)
        )
        if len(summary) > 480:
            summary = summary[:479].rstrip() + "…"
        value = summary
        notes = [
            "ACTIVE_GOVERNING requires ADR/formal/human-disposition/verified claim evidence",
            "section headers / receipts / release labels are NON_DECISION",
            "status labels are deterministic classifiers, not confidence scores",
            "lens!=Layer-B-authority",
            "UI!=canonical",
            "not a ranked trust score",
        ]
    else:
        status = "unknown"
        summary = None
        value = None
        notes = [
            "No decision evidence found in note/claims/ADR sources; UNKNOWN",
            "lens!=Layer-B-authority",
            "UI!=canonical",
            "UNKNOWN!=healthy",
        ]

    lens = attach_source_drift(
        {
            "schema_version": 1,
            "schema": "atlas.coder-alpha.decisions-lens.v1",
            "package": PACKAGE_ID,
            "answer_id": f"ans-decisions-{project_id}",
            "subject": project_id,
            "field": "decisions",
            "title": "What decisions matter?",
            "summary": summary,
            "value": value,
            "status": status,
            "authority": "derived-lens",
            "layer": "C",
            "project_id": project_id,
            "decisions": unique[:20],
            "decision_count": len(unique),
            "active_governing_count": len(
                [item for item in unique if item.get("status") == "ACTIVE_GOVERNING"]
            ),
            "inspected_artifacts": inspected,
            "notes": notes,
            "generated": {"by": GENERATOR_ID},
        },
        vault,
        project_id,
        path_filter=_decision_source_path,
    )
    honesty = dict(lens.get("honesty") or {}) if isinstance(lens.get("honesty"), dict) else {}
    honesty["governing_evidence_stale"] = (
        str((lens.get("source_drift") or {}).get("status") or "") == "STALE"
    )
    lens["honesty"] = honesty
    return lens


def materialize_decisions_lenses(
    vault: Path,
    *,
    project_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write decisions answer lenses under ``generated/answers/``."""
    vault = vault.expanduser().resolve()
    if not vault.is_dir():
        raise ProjectDecisionsError(f"vault is not a directory: {vault}")
    selected = project_ids if project_ids is not None else _list_projects(vault)
    written: list[str] = []
    lenses: list[dict[str, Any]] = []
    for project_id in selected:
        lens = build_decisions_lens(vault, project_id)
        lenses.append(lens)
        path = vault / ANSWERS_RELATIVE / f"{lens['answer_id']}.json"
        _write_atomic(
            path,
            (json.dumps(lens, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        written.append(path.relative_to(vault).as_posix())
    return {
        "schema_version": 1,
        "schema": "atlas.coder-alpha.decisions-receipt.v1",
        "package": PACKAGE_ID,
        "status": "ok",
        "vault": vault.as_posix(),
        "projects": list(selected),
        "answers_written": written,
        "lenses": lenses,
        "generated": {"by": GENERATOR_ID},
        "honesty": {
            "authentic_pilot": False,
            "release_certified": False,
            "atlas_opt_wake_gate": "CLOSED",
            "lens_is_authority": False,
        },
    }

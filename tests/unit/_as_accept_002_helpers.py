"""AS-ACCEPT-002 shared helpers — disposable fixtures only (tests package).

Band A combined DIAG x OBS x auth/tmp external acceptance.
Does not invent production behavior (wave design section 12).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from project_atlas.knowledge_compiler import compile_knowledge, render_bundle

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "as-core-005" / "real-sources"

# Contracted QueryDiagnostic keys (AS-QUERY-DIAG-001 / extra=forbid model).
QUERY_DIAGNOSTIC_KEYS = frozenset(
    {
        "schema_version",
        "package",
        "outcome_class",
        "query_shape",
        "project_id",
        "subject",
        "field",
        "fields",
        "kind",
        "compilation_id",
        "error_code",
        "message",
        "answer_status",
        "item_outcome_classes",
        "inspected_artifacts",
    }
)


def sid(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"source-{digest}"


def entry(rel_path: str, classification: str = "validation") -> dict[str, Any]:
    key = rel_path.replace("/", "__")
    text = (_FIXTURE_DIR / key).read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "source_id": sid(rel_path),
        "path": rel_path,
        "classification": classification,
        "source": f"../../sources/imported-documents/{sid(rel_path)}.md",
        "sha256": sha,
        "text": text,
    }


def entries() -> list[dict[str, Any]]:
    return [
        entry("docs/plan.md", "architecture"),
        entry("docs/evidence/AS-CORE-002-post-merge-receipt.yaml"),
        entry("docs/evidence/AS-CORE-002-source-lifecycle-recertification.yaml"),
        entry("docs/evidence/AS-CORE-003-claim-identity-amendment-plan.yaml"),
        entry("docs/evidence/AS-CORE-003-receipt.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-003.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-003-review.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-004.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-005.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-005-review.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-006.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-candidate-006-review-addendum.yaml"),
        entry("docs/evidence/AS-CORE-003-v2-remediation-receipt.yaml"),
        entry("docs/evidence/AS-ID-001-receipt.yaml"),
        entry("docs/evidence/AS-ID-001-governor-remediation-receipt.yaml"),
        entry("docs/evidence/AS-ID-001-final-certification-remediation-receipt.yaml"),
        entry("docs/evidence/AS-ID-001-retired-slot-resolution-wiring-receipt.yaml"),
        entry("docs/evidence/AS-RET-001-receipt.yaml"),
        entry("docs/evidence/AS-RET-001-post-merge-receipt.yaml"),
        entry("docs/evidence/AS-SEC-001-certification-carry-forward.yaml"),
        entry("docs/evidence/AS-SEC-001-post-merge-validation.yaml"),
    ]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")


def materialize_knowledge_vault(tmp_path: Path) -> Path:
    """Compile AS-ID-001 title fixture into a disposable vault (consume-only)."""
    vault = tmp_path / "vault"
    for rel in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# scaffold\n", encoding="utf-8")
    write_json(
        vault / ".atlas" / "vault.json",
        {"vault_id": "atlas-main", "vault_uuid": "fixture-vault-uuid-accept-002"},
    )
    bundle = compile_knowledge("project-atlas", entries(), tmp_path / "compile")
    for rel, content in render_bundle(bundle, "project-atlas").items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return vault


def identity_only_ops_vault(tmp_path: Path) -> Path:
    """OBS identity vault — no sync/backup/quarantine/promotion/readiness evidence."""
    vault = tmp_path / "ops-vault"
    vault.mkdir(parents=True, exist_ok=True)
    write_json(
        vault / ".atlas" / "vault.json",
        {"vault_id": "atlas-main", "vault_uuid": "fixture-vault-uuid-accept-002"},
    )
    (vault / "state" / "authoritative-state").mkdir(parents=True)
    (vault / "state" / "current-state").mkdir(parents=True)
    (vault / "state" / "claims").mkdir(parents=True)
    write_json(vault / "state" / "authoritative-state" / "probe.json", {"ok": True})
    write_json(vault / "state" / "current-state" / "probe.json", {"ok": True})
    write_json(vault / "state" / "claims" / "probe.json", {"ok": True})
    return vault


def hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def truth_plane_paths(vault: Path) -> list[Path]:
    roots = [
        vault / "state" / "authoritative-state",
        vault / "state" / "current-state",
        vault / "state" / "claims",
    ]
    paths: list[Path] = []
    for root in roots:
        if root.is_dir():
            paths.extend(sorted(p for p in root.rglob("*") if p.is_file()))
    return paths


def signal_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["signal_id"]: item for item in snapshot["signals"]}

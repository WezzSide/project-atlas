"""Strict Graphify state and projection validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .safe_project import confined_file, require_project_id


@dataclass
class GraphValidationReport:
    project_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nodes_checked: int = 0
    relationships_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "project_id": self.project_id, "nodes_checked": self.nodes_checked, "relationships_checked": self.relationships_checked, "errors": self.errors, "warnings": self.warnings}


def validate(vault_root: Path, project_id: str) -> GraphValidationReport:
    pid = require_project_id(project_id)
    report = GraphValidationReport(pid)
    base = vault_root / "relationships"
    nodes_path = confined_file(base / "nodes", pid, "{project_id}.jsonl")
    edges_path = confined_file(base / "edges", pid, "{project_id}.jsonl")
    state_path = confined_file(base / "state", pid, "{project_id}.json")
    receipt_paths = sorted((base / "receipts").glob(f"{pid}-*.json"))
    if not state_path.is_file():
        report.errors.append("missing graph state")
        return report
    if not receipt_paths:
        report.errors.append("missing graph receipt")
    for path, field in ((nodes_path, "nodes"), (edges_path, "relationships")):
        if path.is_file():
            import json
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    report.nodes_checked += field == "nodes"
                    report.relationships_checked += field == "relationships"
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        report.errors.append(f"invalid JSONL: {path}")
        else:
            report.warnings.append(f"missing optional store: {path.name}")
    for projection in ("relationships.md", "graph-health.md"):
        if not confined_file(vault_root / "projects" / pid, pid, projection).is_file():
            report.errors.append(f"missing graph projection: {projection}")
    return report

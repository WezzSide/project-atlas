#!/usr/bin/env python3
"""Rebuild Graphify Markdown projections from canonical graph state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from internal import atlas_router, graph_projection  # noqa: E402
from internal.safe_project import confined_file, require_project_id  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--from-graph-state", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        pid = require_project_id(args.project_id)
        state_path = confined_file(args.vault / "relationships" / "state", pid, "{project_id}.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        nodes = list(state.get("nodes", {}).values())
        edges = list(state.get("relationships", {}).values())
        quarantine = list(state.get("quarantine", {}).values())
        receipt_id = str(state.get("last_receipt", "unknown"))
        content = graph_projection.render_relationships(pid, nodes, edges, quarantine, receipt_id)
        if not args.dry_run:
            changed, no_op = atlas_router.update_derived_projection(vault_root=args.vault, project_id=pid, relative_path=f"projects/{pid}/relationships.md", content=content, settings=atlas_router.RoutingSettings())
        else:
            changed, no_op = False, True
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    result = {"ok": True, "project_id": pid, "changed": changed, "no_op": no_op}
    print(json.dumps(result) if args.json_output else ("changed" if changed else "no-op"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

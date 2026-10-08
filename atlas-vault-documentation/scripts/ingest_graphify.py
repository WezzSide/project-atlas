#!/usr/bin/env python3
"""Ingest inventory-backed Graphify artifacts into derived Atlas state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import atlas_config  # noqa: E402
from internal import graph_ingestion  # noqa: E402
from internal.safe_project import confined_file, require_project_id  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        pid = require_project_id(args.project_id)
        inventory_path = confined_file(args.vault / "ingestion" / "inventory", pid, "{project_id}.json")
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        config, _, _ = atlas_config.load_config(args.config, start=Path(str(inventory["project_root"])))
        result = graph_ingestion.ingest_graphify(project_id=pid, vault_root=args.vault, project_root=Path(str(inventory["project_root"])), inventory=inventory, config=config, incremental=args.incremental, dry_run=args.dry_run, strict=args.strict)
    except (OSError, ValueError, KeyError, graph_ingestion.GraphIngestionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, default=str) if args.json_output else f"{result['status']}: {pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

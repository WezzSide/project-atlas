#!/usr/bin/env python3
"""Minimal read-only consumer for atlas.improvement-plane.report.v1 (stdlib only).

Usage:
  python docs/examples/as_impr_plane_001_consume_report.py /path/to/report.json

Does not write, network, or import project_atlas. Unknown schemas fail closed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_SCHEMA = "atlas.improvement-plane.report.v1"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(
            "usage: as_impr_plane_001_consume_report.py <report.json>\n"
        )
        return 2
    path = Path(argv[1])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"unreadable: {exc}\n")
        return 2
    if not isinstance(raw, dict):
        sys.stderr.write("error: report must be a JSON object\n")
        return 2
    schema = raw.get("schema")
    if schema != EXPECTED_SCHEMA:
        sys.stderr.write(
            f"incompatible schema: {schema!r} (expected {EXPECTED_SCHEMA})\n"
        )
        return 2
    recs = raw.get("recommendations")
    if not isinstance(recs, list):
        sys.stderr.write("error: recommendations missing or not a list\n")
        return 2
    print(f"schema={schema}")
    print(f"package_id={raw.get('package_id')}")
    print(f"recommendation_count={len(recs)}")
    authority = {
        str(r.get("authority")) for r in recs if isinstance(r, dict)
    }
    print(f"authority_values={sorted(authority)}")
    for row in recs:
        if not isinstance(row, dict):
            continue
        rid = row.get("recommendation_id")
        sources = row.get("source_records") or []
        print(f"- {rid} kind={row.get('kind')} sources={sources}")
    # Consumer must not treat this as gate resolution.
    print("note=RECOMMENDATION_NE_AUTHORITY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

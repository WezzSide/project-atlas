#!/usr/bin/env python3
"""atlas-dag entry point (D-006)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from atlas_dag.cli import main

if __name__ == "__main__":
    sys.exit(main())

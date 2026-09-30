"""Shared capability-maturity guard for Atlas 2.2 prep packages.

GOVERNANCE_GUARD_RECONCILIATION (D-INTEGRATE-007A). The 2.2 prep branch-scope
guards historically asserted that a branch touched ONLY a package's prep-docs
tree and NOTHING under ``src/`` (``git diff --name-only origin/main...HEAD``).
That blanket rule red-flagged every legitimate feature branch that ships 2.2
``src/`` code. This helper replaces the blanket rule with a check keyed on the
single capability-maturity source of truth
(``docs/atlas-2.2/PACKAGE-MATURITY.json``):

* a **prep-frozen** package still forbids runtime/``src/`` mutation of *its own*
  declared production surface (DENY preserved);
* an **implementation-unlocked** capability may legitimately mutate its
  production surface under normal governance (ALLOW).

The PREP <-> IMPLEMENTATION boundary is preserved: only capabilities with real
merged runtime evidence are marked implementation-unlocked, and frozen packages
stay frozen. This is not a blanket 2.2 unlock.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATURITY_PATH = ROOT / "docs" / "atlas-2.2" / "PACKAGE-MATURITY.json"

PREP_FROZEN = "prep-frozen"
IMPLEMENTATION_UNLOCKED = "implementation-unlocked"
KNOWN_MATURITIES = frozenset({PREP_FROZEN, IMPLEMENTATION_UNLOCKED})


@lru_cache(maxsize=1)
def load_maturity() -> dict[str, object]:
    """Load and cache the capability-maturity source of truth."""
    data = json.loads(MATURITY_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "PACKAGE-MATURITY.json must be a JSON object"
    return data


def _packages() -> dict[str, dict[str, object]]:
    packages = load_maturity()["packages"]
    assert isinstance(packages, dict), "PACKAGE-MATURITY.json 'packages' must be an object"
    return packages  # type: ignore[return-value]


def package_entry(package: str) -> dict[str, object]:
    """Return the maturity entry for ``package`` (fails closed if unknown)."""
    packages = _packages()
    assert package in packages, f"unknown 2.2 package {package!r} in PACKAGE-MATURITY.json"
    entry = packages[package]
    assert isinstance(entry, dict), f"maturity entry for {package!r} must be an object"
    maturity = entry.get("maturity")
    assert maturity in KNOWN_MATURITIES, f"unknown maturity {maturity!r} for {package!r}"
    return entry


def production_surface(package: str) -> list[str]:
    """Return the declared production surface paths for ``package``."""
    surface = package_entry(package).get("production_surface", [])
    assert isinstance(surface, list), f"production_surface for {package!r} must be a list"
    return [str(item) for item in surface]


def is_unlocked(package: str) -> bool:
    return package_entry(package)["maturity"] == IMPLEMENTATION_UNLOCKED


def branch_changes(root: Path = ROOT, *, include_worktree: bool = True) -> set[str]:
    """Return branch-relative changed paths (``origin/main...HEAD`` + worktree).

    Worktree (uncommitted) changes are folded in so the guard is meaningful for
    local verification before push, matching the strictest legacy guard.

    Shallow CI checkouts often lack ``origin/main``; attempt a depth-1 fetch,
    then fall back to an empty committed delta so guards still evaluate the
    worktree without hard-failing on missing merge-base history.
    """
    changed: set[str] = set()
    base_ok = (
        subprocess.run(
            ["git", "rev-parse", "--verify", "origin/main"],
            cwd=root,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    if not base_ok:
        subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", "main"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        base_ok = (
            subprocess.run(
                ["git", "rev-parse", "--verify", "origin/main"],
                cwd=root,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    if base_ok:
        diff = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff.returncode == 0:
            changed = {
                line.strip().replace("\\", "/") for line in diff.stdout.splitlines() if line.strip()
            }
    if include_worktree:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=root,
            text=True,
        )
        for line in status.splitlines():
            path = line[3:].strip().replace("\\", "/")
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path:
                changed.add(path)
    return changed


def _matches_surface(path: str, surface: list[str]) -> bool:
    for entry in surface:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def surface_violations(package: str, changed: set[str]) -> list[str]:
    """Return changed paths that fall on ``package``'s production surface."""
    surface = production_surface(package)
    return sorted(path for path in changed if _matches_surface(path, surface))


def assert_prep_branch_scope(package: str, *, changed: set[str] | None = None) -> None:
    """Assert the branch respects ``package``'s capability maturity.

    * ``implementation-unlocked``: production-surface mutation is permitted
      (ALLOW); nothing is asserted about ``src/``.
    * ``prep-frozen``: the branch must NOT mutate ``package``'s declared
      production surface (DENY); fails closed with the offending paths.
    """
    if changed is None:
        changed = branch_changes()
    if is_unlocked(package):
        return
    violations = surface_violations(package, changed)
    assert not violations, (
        f"prep-frozen package {package!r} must not mutate its production surface; "
        f"offending paths: {violations}"
    )

"""Command line entry: ``python -m project_atlas.fleet_observer``.

Writes two files: the full local observation (must be outside the repository) and the
public-safe projection. It changes nothing on any host.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from project_atlas.fleet_observer.config import ConfigError, load_config
from project_atlas.fleet_observer.probe import observe, ssh_runner
from project_atlas.fleet_observer.projection import PublicLeakError, project_public


class GitRepository:
    """Read-only questions to a local clone. It never fetches, checks out or writes."""

    def __init__(self, root: Path, ref: str) -> None:
        self._root = root
        self._ref = ref

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self._root), *args],
            capture_output=True,
            text=True,
            check=False,
            env=None,
        )

    def main_revision(self) -> str:
        result = self._git("rev-parse", "--verify", f"{self._ref}^{{commit}}")
        if result.returncode != 0:
            raise ConfigError("reference branch not found in the local clone")
        return result.stdout.strip()

    def is_on_main(self, revision: str) -> bool:
        return self._git("merge-base", "--is-ancestor", revision, self._ref).returncode == 0

    def commits_since(self, revision: str, paths: tuple[str, ...]) -> list[str]:
        result = self._git("rev-list", f"{revision}..{self._ref}", "--", *paths)
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.split() if line]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m project_atlas.fleet_observer")
    parser.add_argument("--config", type=Path, required=True, help="operator configuration")
    parser.add_argument("--repo", type=Path, required=True, help="local clone of the repository")
    parser.add_argument("--ref", default="origin/main", help="reference branch in that clone")
    parser.add_argument("--raw-out", type=Path, required=True, help="full local record")
    parser.add_argument("--public-out", type=Path, required=True, help="public projection")
    args = parser.parse_args(argv)
    if _inside(args.raw_out, args.repo) or _inside(args.config, args.repo):
        print("refused: the configuration and the full record must stay outside the repository")
        return 2
    try:
        config = load_config(args.config)
        repository = GitRepository(args.repo, args.ref)
        observations = observe(config, ssh_runner)
        now = datetime.now(UTC)
        public = project_public(config, observations, repository, now)
    except (ConfigError, PublicLeakError) as exc:
        print(f"refused: {exc}")
        return 2
    raw = {
        "schema": "atlas-fleet-observation-raw/v1",
        "observed_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observations": [item.to_raw() for item in observations],
    }
    args.raw_out.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.public_out.write_text(
        json.dumps(public, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    unreached = sorted({item.host for item in observations if not item.reachable})
    print(f"components: {len(public['components'])}; hosts not reached: {len(unreached)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

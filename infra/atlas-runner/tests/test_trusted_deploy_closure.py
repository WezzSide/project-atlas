"""Trusted-deploy closure tests (ATLAS-RUNNER-TRUSTED-DEPLOY-CLOSURE-002).

Exercises the INCIDENT-001 repairs with real subprocesses against sandboxed
directories and a fake git / fake systemctl — no root, no network, no docker:

- defect A: health-context parity entrypoint (with/without/missing/unreadable
  env file, secret non-disclosure);
- defect B: archive-source freshness preflight (wrong remote, fetch failure,
  revision absent/present, dirty worktree irrelevance);
- defect C: target-revision deploy tool bootstrap (hash binding, malformed
  input, broken-previous-tool regression).

SANDBOXED != LIVE: production paths remain the script defaults; the ATLAS_*
overrides used here are test hooks only.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

INFRA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = INFRA_DIR.parents[1]
SCRIPTS = INFRA_DIR / "scripts"
HEALTH = SCRIPTS / "atlas-runner-health.sh"
SHIM = SCRIPTS / "atlas-deploy-exec.sh"
DEPLOY = SCRIPTS / "deploy-release.sh"

REV = "a" * 40
SECRET_MARKER = "s3cr3t-marker-value-9f8e7d"


def _run(cmd: list[str], *, env: dict | None = None, stdin: bytes | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd, env=full_env, input=stdin, cwd=cwd, capture_output=True, text=False, timeout=60
    )


def _fake_bin(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-bin.sh"
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# Defect A — health context entrypoint
# ---------------------------------------------------------------------------


def test_health_with_credential_environment_executes_cli(tmp_path: Path) -> None:
    env_file = tmp_path / "atlas-runner.env"
    env_file.write_text(f"ATLAS_GITHUB_TOKEN={SECRET_MARKER}\n", encoding="utf-8")
    seen = tmp_path / "seen.txt"
    fake = _fake_bin(
        tmp_path,
        f'echo "token-present=${{ATLAS_GITHUB_TOKEN:+yes}}" > "{seen}"\n'
        'echo \'"status": "healthy", "checks": {"github_api": {"ok": true}}\'',
    )
    proc = _run(
        ["bash", str(HEALTH)],
        env={"ATLAS_RUNNER_ENV_FILE": str(env_file), "ATLAS_RUNNER_BIN": str(fake)},
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert '"status": "healthy"' in proc.stdout.decode()
    assert seen.read_text(encoding="utf-8").strip() == "token-present=yes"


def test_health_without_required_environment_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.env"
    proc = _run(
        ["bash", str(HEALTH)],
        env={"ATLAS_RUNNER_ENV_FILE": str(missing), "ATLAS_RUNNER_BIN": "/bin/true"},
    )
    assert proc.returncode == 2
    assert "missing" in proc.stderr.decode()


def test_health_with_unreadable_env_file_fails_closed(tmp_path: Path) -> None:
    """Non-root context: chmod-000 env file must be unreadable -> exit 2.

    Skipped when the suite runs as root: root bypasses file permission bits
    (this is exactly what the deploy host does — see the paired root test
    below). Running this assertion as root would be a false expectation.
    """
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits; see root-context test below")
    env_file = tmp_path / "atlas-runner.env"
    env_file.write_text(f"ATLAS_GITHUB_TOKEN={SECRET_MARKER}\n", encoding="utf-8")
    env_file.chmod(0)
    try:
        proc = _run(
            ["bash", str(HEALTH)],
            env={"ATLAS_RUNNER_ENV_FILE": str(env_file), "ATLAS_RUNNER_BIN": "/bin/true"},
        )
    finally:
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert proc.returncode == 2
    assert "unreadable" in proc.stderr.decode()


@pytest.mark.skipif(os.geteuid() != 0, reason="root-context test: requires euid 0")
def test_health_env_file_sourced_when_readable_by_root(tmp_path: Path) -> None:
    """Root context (deploy host): a chmod-000 env file is still readable by
    root, so the helper sources it and runs the CLI. Documents the real
    permission semantics the deploy gate operates under, and still asserts no
    secret reaches the output."""
    env_file = tmp_path / "atlas-runner.env"
    env_file.write_text(f"ATLAS_GITHUB_TOKEN={SECRET_MARKER}\n", encoding="utf-8")
    env_file.chmod(0)
    seen = tmp_path / "seen.txt"
    fake = _fake_bin(tmp_path, f'echo "sourced" > "{seen}"; echo \'"status": "healthy"\'')
    try:
        proc = _run(
            ["bash", str(HEALTH)],
            env={"ATLAS_RUNNER_ENV_FILE": str(env_file), "ATLAS_RUNNER_BIN": str(fake)},
        )
    finally:
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert proc.returncode == 0, proc.stderr.decode()
    assert seen.read_text(encoding="utf-8").strip() == "sourced"
    assert SECRET_MARKER not in proc.stdout.decode() + proc.stderr.decode()


def test_health_never_prints_secret_values(tmp_path: Path) -> None:
    env_file = tmp_path / "atlas-runner.env"
    env_file.write_text(
        f"ATLAS_GITHUB_TOKEN={SECRET_MARKER}\nOTHER={SECRET_MARKER}\n", encoding="utf-8"
    )
    fake = _fake_bin(tmp_path, 'echo \'"status": "healthy", "detail": "ok"\'')
    proc = _run(
        ["bash", str(HEALTH)],
        env={"ATLAS_RUNNER_ENV_FILE": str(env_file), "ATLAS_RUNNER_BIN": str(fake)},
    )
    assert proc.returncode == 0
    combined = proc.stdout.decode() + proc.stderr.decode()
    assert SECRET_MARKER not in combined


def test_health_helper_uses_static_default_env_path() -> None:
    text = HEALTH.read_text(encoding="utf-8")
    assert 'ATLAS_RUNNER_ENV_FILE:-/etc/atlas-runner/config/atlas-runner.env' in text


# ---------------------------------------------------------------------------
# Defect B — archive source freshness (deploy-release.sh preflight)
# ---------------------------------------------------------------------------


class FakeDocker:
    """Stateful fake docker CLI for sandboxed deploys (no daemon, no network).

    Images are files under <state>/images holding ``<image-id> <revision-label>``.
    Every build yields a NEW image ID so a reused image is distinguishable from a
    rebuilt one. ``FAKE_DOCKER_PY`` selects the interpreter version the image
    reports; ``FAKE_DOCKER_BUILD_FAILS=1`` makes ``docker build`` fail;
    ``FAKE_DOCKER_NET_FAILS=1`` fails the build-network preflight.
    """

    def __init__(self, tmp_path: Path):
        self.state = tmp_path / "docker-state"
        (self.state / "images").mkdir(parents=True)
        self.log = self.state / "docker.log"
        self.bin = tmp_path / "docker"
        self.bin.write_text(
            "#!/usr/bin/env bash\n"
            'S="$FAKE_DOCKER_STATE"\n'
            'echo "$@" >> "$S/docker.log"\n'
            'cmd="$1"; shift\n'
            'case "$cmd" in\n'
            "  image)\n"
            '    tag="${@: -1}"; f="$S/images/${tag}"\n'
            '    [ -f "$f" ] || exit 1; cat "$f" ;;\n'
            "  build)\n"
            '    [ "${FAKE_DOCKER_BUILD_FAILS:-0}" = 1 ] && { echo "build failed" >&2; exit 1; }\n'
            '    tag=; label=\n'
            '    while [ $# -gt 0 ]; do case "$1" in\n'
            '      --tag) tag="$2"; shift 2 ;;\n'
            '      --label) label="${2#*=}"; shift 2 ;;\n'
            '      *) shift ;;\n'
            "    esac; done\n"
            '    n="$(cat "$S/counter" 2>/dev/null || echo 0)"; n=$((n+1))\n'
            '    echo "$n" > "$S/counter"\n'
            '    id="sha256:$(printf "%s-%s" "$tag" "$n" | sha256sum | cut -c1-64)"\n'
            '    echo "$id $label" > "$S/images/${tag}" ;;\n'
            "  run)\n"
            '    case "$*" in *create_connection*)\n'
            '      [ "${FAKE_DOCKER_NET_FAILS:-0}" = 1 ] && exit 1; exit 0 ;;\n'
            "    esac\n"
            '    ver="${FAKE_DOCKER_PY:-3.12.14}"; IFS=. read -r a b _ <<<"$ver"\n'
            '    if [ "$a" -gt 3 ] || { [ "$a" -eq 3 ] && [ "$b" -ge 12 ]; }; then\n'
            '      echo "$ver"; exit 0\n'
            "    fi\n"
            '    echo "AssertionError: $ver" >&2; exit 1 ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        self.bin.chmod(0o755)

    def env(self) -> dict:
        return {"ATLAS_DOCKER": str(self.bin), "FAKE_DOCKER_STATE": str(self.state)}

    def calls(self, verb: str) -> list[str]:
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines()
                if ln.split(" ", 1)[0] == verb]

    def seed(self, revision: str, image_id: str, label_revision: str) -> None:
        tag = f"atlas-runner-worker:{revision}"
        (self.state / "images" / tag).write_text(f"{image_id} {label_revision}\n", encoding="utf-8")


class FakeGitRepo:
    """Minimal fake git CLI + repo dir recording invocations."""

    def __init__(self, tmp_path: Path, *, remote_url: str, have_rev: bool = True,
                 fetch_fails: bool = False):
        self.dir = tmp_path / "clone"
        self.dir.mkdir()
        self.log = tmp_path / "git.log"
        self.remote_url = remote_url
        self.have_rev = have_rev
        self.fetch_fails = fetch_fails
        self.bin = tmp_path / "git"
        self.bin.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> "{self.log}"\n'
            'case "$1" in\n'
            "  -C) shift; repo=$1; shift ;;\n"
            "  *) repo=.; ;;\n"
            "esac\n"
            'case "$1" in\n'
            '  rev-parse) if [ "$2" = "--git-dir" ]; then\n'
            '      echo ".git"; else printf \'%s\\n\' "$2"; fi; exit 0 ;;\n'
            '  "remote") echo "$FAKE_REMOTE_URL" ;;\n'
            '  "fetch") [ "$FAKE_FETCH_FAILS" = 1 ] && exit 1; exit 0 ;;\n'
            '  "cat-file") [ "$FAKE_HAVE_REV" = 1 ] && exit 0 || exit 1 ;;\n'
            '  "archive") tar -C "$FAKE_TEMPLATE" -cf - . ;;\n'
            '  *) exit 1 ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        self.bin.chmod(0o755)
        (tmp_path / "systemd").mkdir(exist_ok=True)
        template = tmp_path / "template" / "infra" / "atlas-runner"
        (template / "controller").mkdir(parents=True)
        (template / "scripts").mkdir()
        (template / "systemd").mkdir()
        (template / "tests").mkdir()
        (template / "entrypoint.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        for name in ("bootstrap-vps.sh", "smoke-workload.sh", "atlas-runner-health.sh"):
            f = template / "scripts" / name
            body = "#!/usr/bin/env bash\n"
            if name == "atlas-runner-health.sh":
                body += '[ "${FAKE_HEALTH_FAILS:-0}" = 1 ] && exit 1\nexit 0\n'
            f.write_text(body, encoding="utf-8")
            f.chmod(0o755)
        (template / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        (template / "systemd" / "atlas-runner-controller.service").write_text(
            "[Service]\nExecStart=/bin/true\n", encoding="utf-8"
        )
        self.template = template.parents[1]  # tmp/template
        self.docker = FakeDocker(tmp_path)

    def env(self, tmp_path: Path) -> dict:
        return {
            "PATH": f"{self.bin.parent}:{os.environ['PATH']}",
            "FAKE_REMOTE_URL": self.remote_url,
            "FAKE_HAVE_REV": "1" if self.have_rev else "0",
            "FAKE_FETCH_FAILS": "1" if self.fetch_fails else "0",
            "FAKE_TEMPLATE": str(self.template),
            "ATLAS_DEPLOY_SKIP_ROOT_CHECK": "1",
            "ATLAS_RUNNER_REPO_ROOT": str(self.dir),
            "ATLAS_RELEASES_DIR": str(tmp_path / "releases"),
            "ATLAS_CURRENT_LINK": str(tmp_path / "current"),
            "ATLAS_SYSTEMD_SYSTEM_DIR": str(tmp_path / "systemd"),
            "ATLAS_SYSTEMCTL": "/bin/true",
            "ATLAS_POST_RESTART_SLEEP": "0",
            **self.docker.env(),
        }


def test_preflight_rejects_untrusted_remote(tmp_path: Path) -> None:
    repo = FakeGitRepo(tmp_path, remote_url="https://github.com/evil/other.git")
    proc = _run(["bash", str(DEPLOY), REV, "--skip-tests"], env=repo.env(tmp_path))
    assert proc.returncode != 0
    assert b"not a trusted repository" in proc.stdout


def test_preflight_fails_closed_when_fetch_fails(tmp_path: Path) -> None:
    repo = FakeGitRepo(
        tmp_path,
        remote_url="https://github.com/WezzSide/project-atlas.git",
        fetch_fails=True,
    )
    proc = _run(["bash", str(DEPLOY), REV, "--skip-tests"], env=repo.env(tmp_path))
    assert proc.returncode != 0
    assert b"fetch of trusted remote failed" in proc.stdout


def test_preflight_rejects_revision_absent_after_fetch(tmp_path: Path) -> None:
    repo = FakeGitRepo(
        tmp_path,
        remote_url="https://github.com/WezzSide/project-atlas.git",
        have_rev=False,
    )
    proc = _run(["bash", str(DEPLOY), REV, "--skip-tests"], env=repo.env(tmp_path))
    assert proc.returncode != 0
    assert b"not present in archive source" in proc.stdout


def test_dirty_host_working_tree_cannot_enter_archive(tmp_path: Path) -> None:
    """git archive reads committed objects only; assert the script archives
    REV (not the working tree) and that a dirty marker file is absent."""
    repo = FakeGitRepo(
        tmp_path, remote_url="https://github.com/B0LK13/project-atlas.git"
    )
    (repo.dir / "DIRTY-MARKER.txt").write_text("uncommitted\n", encoding="utf-8")
    env = repo.env(tmp_path)
    proc = _run(["bash", str(DEPLOY), REV, "--skip-tests"], env=env)
    assert proc.returncode == 0, proc.stderr.decode()
    log = repo.log.read_text(encoding="utf-8")
    assert f"archive {REV} infra/atlas-runner" in log
    staged = Path(env["ATLAS_RELEASES_DIR"]) / REV
    assert not (staged / "DIRTY-MARKER.txt").exists()


def test_full_sandbox_deploy_activates_and_uses_health_entrypoint(tmp_path: Path) -> None:
    repo = FakeGitRepo(tmp_path, remote_url="https://github.com/WezzSide/project-atlas.git")
    env = repo.env(tmp_path)
    env_file = tmp_path / "atlas-runner.env"
    env_file.write_text("ATLAS_GITHUB_TOKEN=fake\n")
    env["ATLAS_RUNNER_ENV_FILE"] = str(env_file)  # helper inherits; gate uses helper
    env["ATLAS_RUNNER_BIN"] = "/bin/true"
    proc = _run(["bash", str(DEPLOY), REV, "--skip-tests"], env=env)
    assert proc.returncode == 0, proc.stderr.decode()
    current = Path(env["ATLAS_CURRENT_LINK"])
    assert current.is_symlink()
    assert current.resolve() == Path(env["ATLAS_RELEASES_DIR"]) / REV
    assert (current / "scripts" / "atlas-runner-health.sh").exists()
    source_rev = (current / ".source-revision").read_text(encoding="utf-8").strip()
    assert source_rev == REV


def test_deploy_fails_when_health_entrypoint_missing(tmp_path: Path) -> None:
    repo = FakeGitRepo(tmp_path, remote_url="https://github.com/WezzSide/project-atlas.git")
    helper = repo.template / "infra" / "atlas-runner" / "scripts" / "atlas-runner-health.sh"
    helper.unlink()
    proc = _run(["bash", str(DEPLOY), REV, "--skip-tests"], env=repo.env(tmp_path))
    assert proc.returncode != 0
    assert b"atlas-runner-health.sh" in proc.stdout


# ---------------------------------------------------------------------------
# Defect C — target-revision deploy tool bootstrap (host shim)
# ---------------------------------------------------------------------------


def _tool_bytes(body: str = 'echo "TOOL-RAN $1"') -> bytes:
    return f"#!/usr/bin/env bash\n{body}\n".encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_shim_executes_hash_bound_tool(tmp_path: Path) -> None:
    tool = _tool_bytes()
    proc = _run(
        ["bash", str(SHIM), REV, _sha256(tool)],
        env={"ATLAS_DEPLOY_STAGING_DIR": str(tmp_path / "staging")},
        stdin=tool,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert f"TOOL-RAN {REV}" in proc.stdout.decode()


def test_shim_rejects_hash_mismatch(tmp_path: Path) -> None:
    tool = _tool_bytes()
    wrong = "0" * 64
    proc = _run(
        ["bash", str(SHIM), REV, wrong],
        env={"ATLAS_DEPLOY_STAGING_DIR": str(tmp_path / "staging")},
        stdin=tool,
    )
    assert proc.returncode == 1
    assert b"hash mismatch" in proc.stderr


@pytest.mark.parametrize(
    "rev,digest",
    [("nothex", "0" * 64), ("a" * 40, "nothex"), ("A" * 40, "0" * 64)],
)
def test_shim_rejects_malformed_arguments(tmp_path: Path, rev: str, digest: str) -> None:
    proc = _run(
        ["bash", str(SHIM), rev, digest],
        env={"ATLAS_DEPLOY_STAGING_DIR": str(tmp_path / "staging")},
        stdin=_tool_bytes(),
    )
    assert proc.returncode == 2


def test_shim_never_reads_tool_from_previous_release(tmp_path: Path) -> None:
    """Defect C regression: the shim's input is stdin only; nothing references
    the currently deployed release's copy of the deploy tool."""
    text = SHIM.read_text(encoding="utf-8")
    assert "/opt/atlas-runner/current" not in text
    assert "CURRENT_LINK" not in text


def test_broken_previous_deploy_tool_cannot_block_fix(tmp_path: Path) -> None:
    """Simulate the INCIDENT-001 defect-C scenario end to end: the 'previous
    release' deploy tool is garbage, yet a target-revision fix deploys."""
    previous = tmp_path / "previous-release" / "scripts"
    previous.mkdir(parents=True)
    (previous / "deploy-release.sh").write_text("this is not a script (((")
    tool = _tool_bytes('echo "FIXED-TOOL-RAN $1"')
    proc = _run(
        ["bash", str(SHIM), REV, _sha256(tool)],
        env={"ATLAS_DEPLOY_STAGING_DIR": str(tmp_path / "staging")},
        stdin=tool,
    )
    assert proc.returncode == 0
    assert f"FIXED-TOOL-RAN {REV}" in proc.stdout.decode()

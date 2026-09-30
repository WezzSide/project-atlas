import json
import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LINUX_SCRIPT = REPO_ROOT / "scripts" / "bootstrap-dev-tooling.sh"
WINDOWS_SCRIPT = REPO_ROOT / "scripts" / "bootstrap-dev-tooling.ps1"


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR)


def _mk_stub_command(bin_dir: Path, name: str, body: str) -> None:
    _write_exec(bin_dir / name, "#!/usr/bin/env bash\nset -e\n" + body + "\n")


def _linux_stub_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    _mk_stub_command(
        bin_dir,
        "npm",
        """
if [ "$1" = "--version" ]; then echo "11.12.1"; exit 0; fi
if [ "$1" = "list" ]; then
  pkg="${@: -1}"
  case "$pkg" in
    codebase-memory-mcp@0.10.8|github-mcp-server@1.12.0|@playwright/mcp@0.0.80)
      echo "$pkg"
      exit 0
      ;;
    *) exit 1 ;;
  esac
fi
exit 1
""",
    )
    _mk_stub_command(bin_dir, "semgrep", 'echo "1.176.1"')
    _mk_stub_command(bin_dir, "pip-audit", 'echo "pip-audit 2.10.0"')
    _mk_stub_command(bin_dir, "yamllint", 'echo "yamllint 1.38.0"')
    _mk_stub_command(bin_dir, "pre-commit", 'echo "pre-commit 4.6.2"')
    _mk_stub_command(bin_dir, "gitleaks", 'echo "8.30.1"')
    _mk_stub_command(bin_dir, "trivy", 'echo "Version: 0.74.0"')
    _mk_stub_command(bin_dir, "syft", 'echo "Version: 1.51.1"')
    _mk_stub_command(bin_dir, "grype", 'echo "Version: 0.118.0"')
    _mk_stub_command(bin_dir, "actionlint", 'echo "v1.7.12"')
    _mk_stub_command(bin_dir, "hadolint", 'echo "Haskell Dockerfile Linter 2.15.1"')
    _mk_stub_command(bin_dir, "taplo", 'echo "taplo 0.10.0"')

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return env


def test_linux_check_reports_version_drift_for_required_pin(tmp_path: Path) -> None:
    env = _linux_stub_env(tmp_path)
    proc = subprocess.run(
        ["bash", str(LINUX_SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 3
    assert "version_drift: pip-audit==2.10.1" in proc.stdout
    assert "required_issue_count=" in proc.stdout


def test_linux_check_handles_npm_command_failure_without_abort(tmp_path: Path) -> None:
    env = _linux_stub_env(tmp_path)
    npm = tmp_path / "bin" / "npm"
    _write_exec(
        npm,
        (
            "#!/usr/bin/env bash\n"
            "set -e\n"
            "if [ \"$1\" = \"--version\" ]; then echo \"11.12.1\"; exit 0; fi\n"
            "exit 2\n"
        ),
    )
    proc = subprocess.run(
        ["bash", str(LINUX_SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 3
    assert "missing: codebase-memory-mcp@0.10.8 (install_required)" in proc.stdout
    assert "required_issue_count=" in proc.stdout


def _windows_stub_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    userprofile = tmp_path / "user"
    appdata = tmp_path / "appdata"
    userprofile.mkdir(parents=True)
    appdata.mkdir(parents=True)

    _mk_stub_command(
        bin_dir,
        "npm",
        """
if [ "$1" = "--version" ]; then echo "11.12.1"; exit 0; fi
if [ "$1" = "list" ]; then
  pkg="${@: -1}"
  case "$pkg" in
    codebase-memory-mcp@0.10.8|github-mcp-server@1.12.0|@playwright/mcp@0.0.80)
      echo "$pkg"
      exit 0
      ;;
    *) exit 1 ;;
  esac
fi
exit 1
""",
    )
    _mk_stub_command(bin_dir, "codebase-memory-mcp", 'echo "0.10.8"')
    _mk_stub_command(bin_dir, "github-mcp-server", 'echo "1.12.0"')
    _mk_stub_command(bin_dir, "npx", 'echo "ok"')
    _mk_stub_command(bin_dir, "semgrep", 'echo "1.176.1"')
    _mk_stub_command(bin_dir, "pip-audit", 'echo "pip-audit 2.10.0"')
    _mk_stub_command(bin_dir, "yamllint", 'echo "yamllint 1.38.0"')
    _mk_stub_command(bin_dir, "pre-commit", 'echo "pre-commit 4.6.2"')
    _mk_stub_command(bin_dir, "gitleaks", 'echo "8.30.1"')
    _mk_stub_command(bin_dir, "trivy", 'echo "Version: 0.74.0"')
    _mk_stub_command(bin_dir, "syft", 'echo "Version: 1.51.1"')
    _mk_stub_command(bin_dir, "grype", 'echo "Version: 0.118.0"')
    _mk_stub_command(bin_dir, "actionlint", 'echo "v1.7.12"')
    _mk_stub_command(bin_dir, "hadolint", 'echo "Haskell Dockerfile Linter 2.15.1"')
    _mk_stub_command(bin_dir, "taplo", 'echo "taplo 0.10.0"')

    wrapper = userprofile / ".local" / "bin"
    wrapper.mkdir(parents=True)
    (wrapper / "github-mcp-wrapper.ps1").write_text("param([string[]]$McpArgs)\n")

    mcp_servers = {
        "mcpServers": {
            "codebase-memory": {"command": "codebase-memory-mcp", "args": []},
            "github": {
                "command": "pwsh",
                "args": [
                    "-NoProfile",
                    "-File",
                    str(wrapper / "github-mcp-wrapper.ps1"),
                ],
            },
            "playwright": {
                "command": "npx",
                "args": ["-y", "@playwright/mcp@0.0.80", "--headless"],
            },
            "context7": {"command": "npx", "args": ["-y", "@upstash/context7-mcp@4.0.5"]},
        }
    }

    copilot_cfg = userprofile / ".copilot"
    copilot_cfg.mkdir(parents=True)
    (copilot_cfg / "mcp-config.json").write_text(json.dumps(mcp_servers))
    cursor_cfg = userprofile / ".cursor"
    cursor_cfg.mkdir(parents=True)
    (cursor_cfg / "mcp.json").write_text(json.dumps(mcp_servers))
    vscode_cfg = appdata / "Code" / "User"
    vscode_cfg.mkdir(parents=True)
    (vscode_cfg / "mcp.json").write_text(json.dumps(mcp_servers))

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["USERPROFILE"] = str(userprofile)
    env["APPDATA"] = str(appdata)
    env["HOME"] = str(userprofile)
    return env


def test_windows_check_reports_required_version_drift(tmp_path: Path) -> None:
    env = _windows_stub_env(tmp_path)
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(WINDOWS_SCRIPT), "-Check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 3
    assert "version_drift: pip-audit==2.10.1" in proc.stdout
    assert "required_issue_count=" in proc.stdout

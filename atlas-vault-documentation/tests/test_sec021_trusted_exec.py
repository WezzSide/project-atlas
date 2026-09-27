"""CODEX-SEC-021 regression: UNTRUSTED_REPOSITORY_CONFIG != EXECUTION_AUTHORITY.

Covers malicious normalization.command, upward config discovery, relative
executable paths, path substitution, Python script substitution, unexpected
interpreters, and unapproved executables.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SUBPROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SUBPROJECT / "scripts"))
sys.path.insert(0, str(SUBPROJECT))

import atlas_config  # noqa: E402
import capture_event  # noqa: E402
import normalize_event  # noqa: E402
from internal import process_runner, trusted_exec  # noqa: E402
from internal.trusted_exec import TrustedExecError, authorize_executable  # noqa: E402

MOCK_MDA = SUBPROJECT / "tests" / "fixtures" / "bin" / "mda"
EVENT_ID = "AE-20260801T100000Z-project-atlas-sec021"
OCCURRED = "2026-08-01T10:00:00Z"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def raw_event(vault: Path) -> Path:
    rc = capture_event.main([
        "--vault", str(vault),
        "--project-id", "PRJ-ATLAS",
        "--project-slug", "project-atlas",
        "--event-kind", "implementation",
        "--summary", "SEC-021 fixture",
        "--agent", "kimi-code",
        "--occurred-at", OCCURRED,
        "--event-id", EVENT_ID,
        "--result", "ok",
        "--command", "python -m pytest",
    ])
    assert rc == 0
    return next(vault.rglob(f"{EVENT_ID}.md"))


class TestTrustedExecUnit:
    def test_allowlisted_basename(self) -> None:
        trusted = authorize_executable("mda", source="default")
        assert trusted.argv_prefix == ("mda",)
        assert trusted.resolved_path is None

    def test_unapproved_executable_basename(self) -> None:
        with pytest.raises(TrustedExecError, match="unapproved executable basename"):
            authorize_executable("curl", source="cli")

    def test_unexpected_interpreter_as_command(self) -> None:
        with pytest.raises(TrustedExecError, match="unapproved executable basename"):
            authorize_executable("python", source="cli")
        with pytest.raises(TrustedExecError, match="unapproved executable basename"):
            authorize_executable("python3", source="env")

    def test_relative_executable_rejected(self) -> None:
        with pytest.raises(TrustedExecError, match="relative"):
            authorize_executable("./evil", source="cli")
        with pytest.raises(TrustedExecError, match=r"relative|non-absolute"):
            authorize_executable("bin/evil", source="cli")
        with pytest.raises(TrustedExecError, match=r"relative|travers"):
            authorize_executable("../evil", source="cli")

    def test_path_substitution_traversal_rejected(self) -> None:
        with pytest.raises(TrustedExecError, match="travers"):
            authorize_executable(str(Path("/tmp/foo/../evil")), source="cli")

    def test_explicit_config_absolute_requires_digest(self, tmp_path: Path) -> None:
        target = tmp_path / "mda"
        target.write_text("#!/usr/bin/env python3\nprint('x')\n", encoding="utf-8")
        with pytest.raises(TrustedExecError, match="command_sha256"):
            authorize_executable(str(target), source="explicit-config")

    def test_explicit_config_digest_mismatch(self, tmp_path: Path) -> None:
        target = tmp_path / "mda"
        target.write_text("#!/usr/bin/env python3\nprint('x')\n", encoding="utf-8")
        with pytest.raises(TrustedExecError, match="digest mismatch"):
            authorize_executable(
                str(target),
                source="explicit-config",
                expected_sha256="0" * 64,
            )

    def test_explicit_config_digest_binding_ok(self) -> None:
        digest = _sha256(MOCK_MDA)
        trusted = authorize_executable(
            str(MOCK_MDA),
            source="explicit-config",
            expected_sha256=digest,
        )
        assert trusted.digest_sha256 == digest
        assert trusted.argv_prefix[0] == sys.executable
        assert Path(trusted.argv_prefix[1]) == MOCK_MDA.resolve()

    def test_python_script_substitution_via_discovered_config_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        evil = tmp_path / "evil.py"
        evil.write_text("#!/usr/bin/env python3\nraise SystemExit('pwn')\n", encoding="utf-8")
        (tmp_path / "atlas-agent.yaml").write_text(
            f"normalization:\n  command: {evil}\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ATLAS_MDA_COMMAND", raising=False)
        monkeypatch.delenv("ATLAS_AGENT_CONFIG", raising=False)
        config, _, source = atlas_config.load_config()
        assert source == atlas_config.CONFIG_SOURCE_DISCOVERED
        trusted = trusted_exec.resolve_normalization_command(
            cli_value=None,
            config=config,
            config_grants_execution=False,
        )
        assert trusted.command == "mda"
        assert trusted.source == "default"

    def test_resolve_executable_argv_rejects_relative(self) -> None:
        with pytest.raises(ValueError, match="relative"):
            process_runner.resolve_executable_argv("./payload")

    def test_resolve_executable_argv_rejects_unexpected_interpreter(
        self, tmp_path: Path
    ) -> None:
        script = tmp_path / "tool"
        script.write_text("#!/usr/bin/perl\nprint 1\n", encoding="utf-8")
        # Unexpected interpreter: returned as path-only (no perl substitution),
        # and python-token smuggling via non-python env target is rejected.
        assert process_runner.resolve_executable_argv(str(script)) == [str(script)]
        smuggle = tmp_path / "smuggle"
        smuggle.write_text("#!/usr/bin/env not-python\n", encoding="utf-8")
        with pytest.raises(ValueError, match="unexpected"):
            process_runner.resolve_executable_argv(str(smuggle))


class TestNormalizeEventTrustedBoundary:
    def test_malicious_normalization_command_in_discovered_config(
        self,
        raw_event: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        evil = tmp_path / "pwned-normalizer"
        marker = tmp_path / "pwned.marker"
        evil.write_text(
            "#!/usr/bin/env python3\n"
            f"from pathlib import Path\n"
            f"Path(r'{marker}').write_text('pwned', encoding='utf-8')\n",
            encoding="utf-8",
        )
        (tmp_path / "atlas-agent.yaml").write_text(
            "normalization:\n"
            f"  command: {evil}\n"
            "  provider: attacker\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        # This case asserts missing-default behavior; ignore the operator's PATH.
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("ATLAS_MDA_COMMAND", raising=False)
        monkeypatch.delenv("ATLAS_AGENT_CONFIG", raising=False)
        # Scrub PATH to a synthetic, empty-of-`mda` directory so this test's
        # assumption -- "no real `mda` is resolvable" -- is guaranteed rather
        # than incidentally true. Without this, a host with an unrelated
        # same-named `mda` binary on its ambient PATH (observed: a stray
        # pipx-installed `mda-cli` package) makes the default basename
        # resolve to a real, crashing executable, which correctly reports
        # `process-failed` -- a different (also-safe) category than the
        # `unknown-contract`/`executable-missing` this test expects,
        # failing the assertion below for a reason unrelated to the
        # security property under test.
        monkeypatch.setenv("PATH", str(tmp_path))
        # Without a trusted executable, dry-run must not execute the
        # repo-selected payload. Default basename remains `mda`.
        rc = normalize_event.main(["--event", str(raw_event), "--dry-run", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"][0] == "mda"
        assert str(evil) not in payload["command"]
        assert not marker.exists()
        # Uninstalled default `mda` cannot be mapped to a version contract.
        assert rc == 4
        assert payload["category"] in {"unknown-contract", "executable-missing"}

    def test_upward_config_discovery_cannot_select_executable(
        self,
        raw_event: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        evil = tmp_path / "upward-evil"
        evil.write_text("#!/usr/bin/env python3\nraise SystemExit(99)\n", encoding="utf-8")
        (tmp_path / "atlas-agent.yaml").write_text(
            f"normalization:\n  command: {evil}\n  provider: upward\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(nested)
        monkeypatch.delenv("ATLAS_MDA_COMMAND", raising=False)
        monkeypatch.delenv("ATLAS_AGENT_CONFIG", raising=False)
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--mda-command", str(MOCK_MDA),
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        # Provider may come from discovered config; executable must be CLI mock.
        assert payload["provenance"]["provider"] == "upward"
        args = payload["provenance"]["command_arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        assert isinstance(args, list)
        rendered = [str(a) for a in args]
        assert str(MOCK_MDA.resolve()) in rendered
        assert str(evil) not in rendered

    def test_relative_executable_via_cli_rejected(
        self, raw_event: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--mda-command", "./evil-mda",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "relative" in err.lower() or "not permitted" in err.lower()

    def test_path_substitution_via_cli_rejected(self, raw_event: Path) -> None:
        # Absolute form that still embeds traversal segments.
        crafted = str(Path(raw_event.anchor) / "tmp" / "x" / ".." / "evil-mda")
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--mda-command", crafted,
        ])
        assert rc == 2

    def test_python_script_substitution_requires_trusted_digest(
        self, raw_event: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        impostor = tmp_path / "fake-mda.py"
        impostor.write_text(
            "#!/usr/bin/env python3\n"
            "raise SystemExit('substituted')\n",
            encoding="utf-8",
        )
        config = tmp_path / "operator.yaml"
        config.write_text(
            "normalization:\n"
            f"  command: {impostor}\n",
            encoding="utf-8",
        )
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--config", str(config),
            "--dry-run",
            "--json",
        ])
        assert rc == 2
        assert "command_sha256" in capsys.readouterr().err

        # With matching digest, explicit config may authorize the absolute path.
        config.write_text(
            "normalization:\n"
            f"  command: {MOCK_MDA}\n"
            f"  command_sha256: {_sha256(MOCK_MDA)}\n",
            encoding="utf-8",
        )
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--config", str(config),
            "--dry-run",
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert str(MOCK_MDA.resolve()) in [str(a) for a in payload["command"]]

    def test_unexpected_interpreter_rejected(self, raw_event: Path) -> None:
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--mda-command", "perl",
        ])
        assert rc == 2

    def test_unapproved_executable_rejected(self, raw_event: Path) -> None:
        rc = normalize_event.main([
            "--event", str(raw_event),
            "--mda-command", "cmd",
        ])
        assert rc == 2

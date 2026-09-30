"""Unit tests for structured logging (A-005 / SEC-ADV-INT-002)."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import project_atlas.logging as project_logging
from project_atlas.logging import ConsoleFormatter, JsonFormatter, configure_logging, get_logger
from project_atlas.secrets import REDACTED_PLACEHOLDER


def test_json_formatter_emits_parseable_record() -> None:
    record = logging.LogRecord("project_atlas.test", logging.INFO, __file__, 1, "hello", (), None)
    record.context = {"path": "a.md"}  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["path"] == "a.md"


def test_configure_logging_is_idempotent() -> None:
    logger = logging.getLogger("project_atlas")
    before = len(logger.handlers)  # pytest's log plugin attaches its own handlers
    configure_logging(level="DEBUG")
    configure_logging(level="INFO")
    assert len(logger.handlers) <= before + 1
    assert logger.level == logging.INFO


def test_child_logger_namespaced() -> None:
    assert get_logger("scaffold").name == "project_atlas.scaffold"


def test_formatters_redact_secret_shaped_message_and_context() -> None:
    """SEC-ADV-INT-002: logging boundary redacts interpolated secret canaries."""
    synth = "sk_test_SYNTHETIC_ADV_INT_002_DO_NOT_USE_ABCDEF12"
    msg = f"api_key = '{synth}'"
    record = logging.LogRecord("project_atlas.test", logging.INFO, __file__, 1, msg, (), None)
    record.context = {"detail": msg, "ok": "safe"}  # type: ignore[attr-defined]

    json_line = JsonFormatter().format(record)
    console_line = ConsoleFormatter().format(record)
    for blob in (json_line, console_line):
        assert synth not in blob
        assert REDACTED_PLACEHOLDER in blob
    payload = json.loads(json_line)
    assert payload["ok"] == "safe"
    assert synth not in payload["message"]
    assert synth not in str(payload.get("detail", ""))


def _reset_logging_state(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    """Detach this module's handler and reset its state, as at interpreter start.

    Restores nothing by hand: `monkeypatch` reverts the module globals, and the
    handler added by the test is removed in the fixture's teardown below.
    """
    logger = logging.getLogger("project_atlas")
    for handler in [h for h in logger.handlers if h is project_logging._HANDLER]:
        logger.removeHandler(handler)
    monkeypatch.setattr(project_logging, "_CONFIGURED", False)
    monkeypatch.setattr(project_logging, "_HANDLER", None)
    return logger


@pytest.fixture
def fresh_logging(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    logger = _reset_logging_state(monkeypatch)
    installed: list[logging.Handler] = []
    yield logger, installed
    for handler in logger.handlers[:]:
        if handler is project_logging._HANDLER:
            logger.removeHandler(handler)


def _emitted(logger: logging.Logger) -> str:
    """Emit one WARNING through this module's handler and return the text."""
    handler = project_logging._HANDLER
    assert handler is not None
    stream = io.StringIO()
    assert isinstance(handler, logging.StreamHandler)
    original, handler.stream = handler.stream, stream  # type: ignore[attr-defined]
    try:
        logger.warning("probe %s", "value")
    finally:
        handler.stream = original  # type: ignore[attr-defined]
    return stream.getvalue()


def test_json_format_applies_after_an_implicit_console_configuration(
    fresh_logging: tuple[logging.Logger, list[logging.Handler]],
) -> None:
    """The `atlas --log-format json` defect, pinned at the library boundary.

    Every module runs `_log = get_logger(...)` at import, which configures
    logging with the console default before the CLI parses `--log-format`.
    Selecting the formatter only on the first call therefore pinned the whole
    process to console, and a JSON-requesting deployment got console output
    with no error.
    """
    logger, _ = fresh_logging
    get_logger("importing_module")  # implicit console configuration, as at import
    assert isinstance(project_logging._HANDLER.formatter, ConsoleFormatter)  # type: ignore[union-attr]

    configure_logging(level="INFO", log_format="json")

    assert isinstance(project_logging._HANDLER.formatter, JsonFormatter)  # type: ignore[union-attr]
    payload = json.loads(_emitted(logger))
    assert payload["message"] == "probe value"
    assert payload["level"] == "WARNING"


def test_reconfiguration_never_attaches_a_second_handler(
    fresh_logging: tuple[logging.Logger, list[logging.Handler]],
) -> None:
    """Re-installing the formatter must not duplicate every record."""
    logger, _ = fresh_logging
    get_logger("importing_module")
    owned = [h for h in logger.handlers if h is project_logging._HANDLER]
    configure_logging(level="DEBUG", log_format="json")
    configure_logging(level="INFO", log_format="console")
    assert [h for h in logger.handlers if h is project_logging._HANDLER] == owned
    assert len(_emitted(logger).strip().splitlines()) == 1


def test_omitted_format_keeps_the_explicitly_requested_one(
    fresh_logging: tuple[logging.Logger, list[logging.Handler]],
) -> None:
    """An implicit call must never downgrade a format the caller asked for."""
    logger, _ = fresh_logging
    configure_logging(level="INFO", log_format="json")
    configure_logging(level="DEBUG")  # no format: keep JSON
    assert isinstance(project_logging._HANDLER.formatter, JsonFormatter)  # type: ignore[union-attr]
    json.loads(_emitted(logger))  # still parseable


def test_handler_is_never_attached_without_a_formatter(
    fresh_logging: tuple[logging.Logger, list[logging.Handler]],
) -> None:
    """A record emitted mid-configuration must not bypass this module's formatters.

    Attaching the handler before setting its formatter leaves a window in
    which `logging`'s default formatter renders the record: bare
    `%(message)s`, no level, no logger name, and none of the redaction the
    formatters here apply. Observed by capturing the logger's handler list at
    the moment `addHandler` runs.
    """
    _logger, _ = fresh_logging
    seen: list[logging.Formatter | None] = []
    real_add = logging.Logger.addHandler

    def recording_add(self: logging.Logger, handler: logging.Handler) -> None:
        if self.name == "project_atlas":
            seen.append(handler.formatter)
        real_add(self, handler)

    logging.Logger.addHandler = recording_add  # type: ignore[method-assign]
    try:
        configure_logging(level="INFO", log_format="json")
    finally:
        logging.Logger.addHandler = real_add  # type: ignore[method-assign]

    assert seen, "the handler must have been attached"
    assert all(isinstance(formatter, (JsonFormatter, ConsoleFormatter)) for formatter in seen), (
        f"handler attached without one of this module's formatters: {seen}"
    )
    assert isinstance(seen[0], JsonFormatter), "the requested format must be the one attached"


def test_first_configuration_without_a_format_is_console(
    fresh_logging: tuple[logging.Logger, list[logging.Handler]],
) -> None:
    logger, _ = fresh_logging
    configure_logging(level="INFO")
    assert isinstance(project_logging._HANDLER.formatter, ConsoleFormatter)  # type: ignore[union-attr]
    assert _emitted(logger).startswith("WARNING project_atlas:")


def _run_cli(
    args: list[str], *, cwd: Path, source: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the real CLI in a fresh interpreter.

    A subprocess is the only way to see this defect: `project_atlas.cli` does
    `_log = get_logger("cli")` at module scope, so importing it configures
    logging with the console default before `--log-format` is parsed. A test
    that has already imported the module is past the point where the bug
    happens.
    """
    program = "import sys; from project_atlas.cli import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run(
        [sys.executable, "-c", program, *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )


def _discover_tree(tmp_path: Path) -> Path:
    """A source tree whose discovery emits exactly one WARNING record.

    An unreadable directory is the cheapest such input; a mode-000 *file* is
    recorded as `unreadable` evidence with no diagnostic at all.
    """
    source = tmp_path / "source"
    (source / "docs").mkdir(parents=True)
    (source / "README.md").write_text("# readme\n", encoding="utf-8")
    locked = source / "docs" / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    return source


def _workdir(tmp_path: Path, *, with_config: bool) -> Path:
    """A working directory the CLI will (or will not) find a config file in.

    With a config file, `load_config` emits an INFO record *before* the
    format taken from that file could possibly be known -- the bootstrap
    record this fix exists to govern.
    """
    workdir = tmp_path / ("wd_config" if with_config else "wd_plain")
    workdir.mkdir()
    if with_config:
        (workdir / "pyproject.toml").write_text("[tool.atlas]\n", encoding="utf-8")
    return workdir


def _classify(stderr: str) -> tuple[list[dict[str, object]], list[str]]:
    """Split stderr into parsed JSON records and non-JSON (console) lines."""
    parsed: list[dict[str, object]] = []
    console: list[str] = []
    for line in stderr.splitlines():
        if not line.strip():
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            console.append(line)
    return parsed, console


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory mode bits provoke the warning")
@pytest.mark.parametrize("with_config", [True, False], ids=["config-file", "no-config-file"])
def test_cli_log_format_json_emits_only_json_records(tmp_path: Path, with_config: bool) -> None:
    """`--log-format json` must govern every record, including the earliest.

    Before OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906 the flag was applied only
    after `load_config`, so with a config file present the operator got one
    console line followed by JSON -- a stream no JSON consumer can parse, with
    no error to indicate it.
    """
    source = _discover_tree(tmp_path)
    workdir = _workdir(tmp_path, with_config=with_config)
    try:
        if os.access(source / "docs" / "locked", os.R_OK):  # pragma: no cover - root
            pytest.skip("filesystem does not enforce directory mode bits")
        completed = _run_cli(
            [
                "--log-format",
                "json",
                "discover",
                "--source",
                str(source),
                "--output",
                str(tmp_path / "m.json"),
            ],
            cwd=workdir,
        )
    finally:
        (source / "docs" / "locked").chmod(0o755)

    assert completed.returncode == 0, completed.stderr
    parsed, console = _classify(completed.stderr)
    assert console == [], f"no record may be console in JSON mode: {console}"
    assert parsed, "the unreadable scope must produce a diagnostic"
    assert any(r["level"] == "WARNING" for r in parsed)
    if with_config:
        assert any(
            r["logger"] == "project_atlas.config" and r["message"] == "loaded configuration"
            for r in parsed
        ), "the bootstrap config record must itself be JSON"
    for record in parsed:
        assert str(record["logger"]).startswith("project_atlas")
    # No handler was attached twice by the extra configuration call.
    messages = [(r["logger"], r["message"]) for r in parsed]
    assert len(messages) == len(set(messages)), f"duplicate records: {messages}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory mode bits provoke the warning")
@pytest.mark.parametrize(
    "flag", [[], ["--log-format", "console"]], ids=["default", "explicit-console"]
)
def test_cli_console_format_is_unchanged(tmp_path: Path, flag: list[str]) -> None:
    """The console path must be untouched, defaulted or requested explicitly."""
    source = _discover_tree(tmp_path)
    workdir = _workdir(tmp_path, with_config=True)
    try:
        if os.access(source / "docs" / "locked", os.R_OK):  # pragma: no cover - root
            pytest.skip("filesystem does not enforce directory mode bits")
        completed = _run_cli(
            [*flag, "discover", "--source", str(source), "--output", str(tmp_path / "m.json")],
            cwd=workdir,
        )
    finally:
        (source / "docs" / "locked").chmod(0o755)

    assert completed.returncode == 0, completed.stderr
    parsed, console = _classify(completed.stderr)
    assert parsed == [], f"console mode must not emit JSON: {parsed}"
    assert any("loaded configuration" in line for line in console)
    assert any("inaccessible discovery scope" in line for line in console)
    assert all(line.startswith(("INFO ", "WARNING ", "ERROR ", "DEBUG ")) for line in console)


def test_cli_failure_path_is_json(tmp_path: Path) -> None:
    """An error record is governed too, not just warnings."""
    workdir = _workdir(tmp_path, with_config=True)
    completed = _run_cli(
        [
            "--log-format",
            "json",
            "discover",
            "--source",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "m.json"),
        ],
        cwd=workdir,
    )

    assert completed.returncode != 0
    parsed, console = _classify(completed.stderr)
    assert console == [], f"the failure path must not fall back to console: {console}"
    assert any(record["level"] == "ERROR" for record in parsed), parsed


def test_cli_rejects_an_invalid_log_format(tmp_path: Path) -> None:
    """Parser semantics for a bad value are unchanged (argparse usage error)."""
    completed = _run_cli(["--log-format", "yaml", "version"], cwd=tmp_path)
    assert completed.returncode == 2
    assert "invalid choice" in completed.stderr


@pytest.mark.parametrize("args", [["--help"], ["version"]], ids=["help", "version"])
def test_cli_help_and_version_are_unaffected(tmp_path: Path, args: list[str]) -> None:
    completed = _run_cli(args, cwd=tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip()


def test_cli_version_with_json_format_emits_no_console_record(tmp_path: Path) -> None:
    """The smoke path must not regress into mixed output."""
    workdir = _workdir(tmp_path, with_config=True)
    completed = _run_cli(["--log-format", "json", "version"], cwd=workdir)
    assert completed.returncode == 0, completed.stderr
    _, console = _classify(completed.stderr)
    assert console == [], console

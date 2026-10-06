"""Read-only fleet observer: the probe is three fixed read-only templates, unsafe configuration
is refused, unreadable data stays UNKNOWN, state is never rounded upward, and the public
projection is schema-valid and free of host-side names."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from project_atlas.fleet_observer import (
    ConfigError,
    PublicLeakError,
    build_probe_script,
    observe,
    parse_probe_output,
    project_public,
)
from project_atlas.fleet_observer.__main__ import main
from project_atlas.fleet_observer.config import parse_config

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "global" / "baseline" / "evidence" / "fleet-provenance-map.schema.json"
SHA = "b2f97ff268f0e995f1f49871944fbcaedc04a514"
MAIN = "92e833d0a6918d75b5cc3b202873aeb1f0ac0484"
NOW = datetime(2026, 10, 6, 9, 40, tzinfo=UTC)

_ALLOWED_LINE = re.compile(
    r"^(echo '@@atlas-observer (\d+ (unit|link|progress)|end)'"
    r"|systemctl show( -p [A-Za-z]+)+ -- '[A-Za-z0-9@_.:-]+' 2>/dev/null \|\| true"
    r"|readlink -- '/[A-Za-z0-9_./+-]+' 2>/dev/null \|\| true"
    r"|cat -- '/[A-Za-z0-9_./+-]+' 2>/dev/null \|\| true)$"
)


def _config(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "repository": "example/repository",
        "hosts": {
            "VPS1": {"alias": "node-a", "role_id": "ROLE-A", "role": "execution"},
            "VPS2": {"alias": "node-b", "role_id": "ROLE-B", "role": "verifier and runner"},
            "GITHUB_HOSTED": {"role_id": "NONE", "role": "hosted runner"},
        },
        "components": [
            {
                "id": "runner-controller",
                "expected_role": "Starts executor workers",
                "repository": {"implementation": "IMPLEMENTED", "paths": ["infra/atlas-runner"]},
                "integration": "INTEGRATED",
                "live_validation": "RECORDED_SINGLE_HOST",
                "instances": [
                    {
                        "host": "VPS2",
                        "unit": "example-runner.service",
                        "release_link": "/srv/example/runner/current",
                        "runtime_identity": "DEDICATED",
                    }
                ],
            },
            {
                "id": "mission-supervisor",
                "expected_role": "Per-host mission loop",
                "repository": {"implementation": "NOT_IN_REPOSITORY", "paths": []},
                "integration": "UNKNOWN",
                "live_validation": "NOT_VALIDATED",
                "instances": [
                    {
                        "host": "VPS1",
                        "unit": "example-supervisor.service",
                        "progress_file": "/srv/example/state/heartbeat.json",
                    },
                    {
                        "host": "VPS2",
                        "unit": "example-supervisor.service",
                        "progress_file": "/srv/example/state/heartbeat.json",
                    },
                ],
            },
            {
                "id": "verify-workflow",
                "expected_role": "Independent verification",
                "repository": {"implementation": "IMPLEMENTED", "paths": [".github/workflows"]},
                "integration": "INTEGRATED",
                "live_validation": "UNKNOWN",
                "instances": [{"host": "GITHUB_HOSTED", "runtime_identity": "NOT_APPLICABLE"}],
            },
        ],
    }
    data.update(overrides)
    return data


class FakeRepository:
    def __init__(self, on_main: bool = True, behind: list[str] | None = None) -> None:
        self.on_main = on_main
        self.behind = behind if behind is not None else ["f7d6b864aa", "ececbf25bb"]

    def main_revision(self) -> str:
        return MAIN

    def is_on_main(self, revision: str) -> bool:
        return self.on_main and revision == SHA

    def commits_since(self, revision: str, paths: tuple[str, ...]) -> list[str]:
        return list(self.behind)


def _unit(load: str, active: str, unit_file: str, restarts: int) -> str:
    return (
        f"LoadState={load}\nActiveState={active}\nSubState=x\n"
        f"UnitFileState={unit_file}\nNRestarts={restarts}\n"
    )


VPS2_OUTPUT = (
    "@@atlas-observer 0 unit\n"
    + _unit("loaded", "active", "enabled", 19)
    + f"@@atlas-observer 0 link\n/srv/example/runner/releases/{SHA}\n"
    + "@@atlas-observer 1 unit\n"
    + _unit("loaded", "active", "enabled", 0)
    + '@@atlas-observer 1 progress\n{"status": "EXECUTOR_FAILURE_BACKOFF", '
    + '"consecutive_failures": 26, "last_success_at": "2026-09-30T09:31:31Z", "secret": "x"}\n'
    + "@@atlas-observer end\n"
)
VPS1_OUTPUT = (
    "@@atlas-observer 0 unit\n"
    + _unit("loaded", "active", "enabled", 0)
    + "@@atlas-observer 0 progress\n@@atlas-observer end\n"
)


def _runner(outputs: dict[str, str]) -> Any:
    def run(alias: str, script: str) -> str:
        if alias not in outputs:
            raise OSError("unreachable")
        return outputs[alias]

    return run


def _project(outputs: dict[str, str], repository: FakeRepository | None = None) -> dict[str, Any]:
    config = parse_config(_config())
    observations = observe(config, _runner(outputs))
    return project_public(config, observations, repository or FakeRepository(), NOW)


def _by_id(document: dict[str, Any], component_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(c for c in document["components"] if c["id"] == component_id)
    return found


def test_probe_script_is_only_the_three_read_only_templates() -> None:
    config = parse_config(_config())
    for host in ("VPS1", "VPS2"):
        script = build_probe_script(config, host)
        for line in script.splitlines():
            assert _ALLOWED_LINE.match(line), line
        for forbidden in ("sudo", "restart", "start ", "stop", "reload", "enable", "rm ", ">>"):
            assert forbidden not in script, forbidden


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit", "x.service; reboot"),
        ("unit", "$(reboot)"),
        ("release_link", "/srv/a'b"),
        ("release_link", "/srv/../etc/shadow"),
        ("release_link", "relative/path"),
        ("progress_file", "/srv/a b"),
    ],
)
def test_unsafe_configuration_values_are_refused(field: str, value: str) -> None:
    data = _config()
    data["components"][0]["instances"][0][field] = value
    with pytest.raises(ConfigError):
        parse_config(data)


def test_unsafe_alias_and_structural_errors_are_refused() -> None:
    data = _config()
    data["hosts"]["VPS1"]["alias"] = "-oProxyCommand=x"
    with pytest.raises(ConfigError):
        parse_config(data)
    data = _config()
    data["components"][1]["repository"]["paths"] = ["src"]
    with pytest.raises(ConfigError):
        parse_config(data)
    data = _config()
    data["components"][2]["instances"][0]["unit"] = "x.service"
    with pytest.raises(ConfigError):
        parse_config(data)
    with pytest.raises(ConfigError):
        parse_config([])


def test_projection_is_schema_valid_and_ties_only_a_main_revision() -> None:
    document = _project({"node-a": VPS1_OUTPUT, "node-b": VPS2_OUTPUT})
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(document)
    runner = _by_id(document, "runner-controller")
    assert runner["provenance_confidence"] == "EXACT_REVISION"
    assert runner["instances"][0]["deployed_revision"] == SHA
    assert runner["instances"][0]["drift_from_main"] == "BEHIND"
    assert runner["drift"]["main_ahead_by_commits_in_paths"] == 2
    assert runner["instances"][0]["note"] == "restarted repeatedly; cause UNKNOWN"
    workflow = _by_id(document, "verify-workflow")
    assert workflow["provenance_confidence"] == "NOT_APPLICABLE"
    assert workflow["instances"][0]["deployment"] == "NOT_APPLICABLE"


def test_progress_needs_a_recent_success_and_no_failures() -> None:
    def supervisor(progress: str) -> str:
        unit = "@@atlas-observer 0 unit\n" + _unit("loaded", "active", "enabled", 0)
        document = _project(
            {
                "node-a": unit
                + "@@atlas-observer 0 progress\n"
                + progress
                + "\n@@atlas-observer end\n"
            }
        )
        entry = next(
            e for e in _by_id(document, "mission-supervisor")["instances"] if e["host"] == "VPS1"
        )
        health: str = entry["health"]
        return health

    recent = '{"consecutive_failures": 0, "last_success_at": "2026-10-06T03:39:19Z"}'
    stale = '{"consecutive_failures": 0, "last_success_at": "2026-09-30T09:31:31Z"}'
    assert supervisor(recent) == "PROGRESSING"
    assert supervisor(stale) == "UNKNOWN"
    assert supervisor('{"consecutive_failures": 0}') == "UNKNOWN"
    assert supervisor('{"consecutive_failures": true, "last_success_at": "x"}') == "UNKNOWN"
    assert supervisor("not json") == "UNKNOWN"


def test_a_release_that_is_not_a_main_commit_is_not_published() -> None:
    outputs = {"node-a": VPS1_OUTPUT, "node-b": VPS2_OUTPUT}
    document = _project(outputs, FakeRepository(on_main=False))
    runner = _by_id(document, "runner-controller")
    assert runner["provenance_confidence"] == "NONE"
    assert runner["instances"][0]["deployed_revision"] is None
    assert runner["instances"][0]["drift_from_main"] == "UNKNOWN"
    assert "drift" not in runner


def test_counters_become_state_words_and_unreadable_progress_stays_unknown() -> None:
    document = _project({"node-a": VPS1_OUTPUT, "node-b": VPS2_OUTPUT})
    supervisor = _by_id(document, "mission-supervisor")
    by_host = {entry["host"]: entry for entry in supervisor["instances"]}
    assert by_host["VPS2"]["health"] == "DEGRADED"
    assert by_host["VPS1"]["activity"] == "ACTIVE"
    assert by_host["VPS1"]["health"] == "UNKNOWN"
    assert supervisor["provenance_confidence"] == "NONE"
    text = json.dumps(document)
    assert '"26"' not in text and ": 26" not in text and ": 19" not in text
    assert "secret" not in text


def test_an_unreachable_host_is_unknown_not_absent() -> None:
    document = _project({"node-b": VPS2_OUTPUT})
    supervisor = _by_id(document, "mission-supervisor")
    vps1 = next(entry for entry in supervisor["instances"] if entry["host"] == "VPS1")
    assert vps1["deployment"] == "UNKNOWN"
    assert vps1["activity"] == "UNKNOWN"
    assert vps1["health"] == "UNKNOWN"
    assert vps1["note"] == "host not reached; nothing observed"


def test_truncated_output_is_not_trusted() -> None:
    config = parse_config(_config())
    truncated = VPS2_OUTPUT.replace("@@atlas-observer end\n", "")
    observations = parse_probe_output(config, "VPS2", truncated)
    assert all(not item.reachable and not item.unit_properties for item in observations)


def test_inactive_states_are_not_rounded_upward() -> None:
    disabled = "@@atlas-observer 0 unit\n" + _unit("loaded", "inactive", "disabled", 0)
    missing = "@@atlas-observer 1 unit\n" + _unit("not-found", "inactive", "", 0)
    document = _project({"node-b": disabled + missing + "@@atlas-observer end\n"})
    runner = _by_id(document, "runner-controller")["instances"][0]
    assert (runner["activity"], runner["health"]) == ("DISABLED", "NOT_APPLICABLE")
    instances = _by_id(document, "mission-supervisor")["instances"]
    supervisor = next(entry for entry in instances if entry["host"] == "VPS2")
    assert (supervisor["deployment"], supervisor["activity"]) == ("NOT_FOUND", "NOT_APPLICABLE")


def test_host_side_names_never_reach_the_public_projection() -> None:
    outputs = {"node-a": VPS1_OUTPUT, "node-b": VPS2_OUTPUT}
    text = json.dumps(_project(outputs))
    for private in ("node-a", "node-b", "example-runner", "example-supervisor", "/srv/"):
        assert private not in text, private
    data = _config()
    data["hosts"]["VPS2"]["role"] = "runs on node-b"
    config = parse_config(data)
    observations = observe(config, _runner(outputs))
    with pytest.raises(PublicLeakError):
        project_public(config, observations, FakeRepository(), NOW)


def test_cli_refuses_to_keep_the_full_record_inside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(_config()), encoding="utf-8")
    arguments = ["--config", str(config), "--repo", str(repo)]
    arguments += ["--raw-out", str(repo / "raw.json"), "--public-out", str(tmp_path / "p.json")]
    assert main(arguments) == 2
    assert not (repo / "raw.json").exists()

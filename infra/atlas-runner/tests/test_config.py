"""Config parsing tests (AS-RUNNER-FABRIC-001). CONFIG_ACCEPTED != AUTHORITY."""

from __future__ import annotations

import pytest
from controller.config import ConfigError, load_config, parse_config, validate_task_env

BASE = {
    "github": {"owner": "o", "repo": "r"},
}


def test_minimal_config_parses():
    cfg = parse_config(dict(BASE))
    assert cfg.github.owner == "o"
    assert cfg.max_concurrent_jobs == 1
    assert cfg.worker.image == "atlas-runner-worker:latest"
    assert cfg.labels == ("self-hosted", "linux", "x64", "atlas", "executor")
    assert cfg.worker.timeout_seconds == 1800


def test_unknown_root_key_rejected():
    with pytest.raises(ConfigError, match="unknown config key"):
        parse_config({**BASE, "surprise": True})


def test_unknown_section_key_rejected():
    with pytest.raises(ConfigError, match="unknown config key"):
        parse_config({"github": {"owner": "o", "repo": "r", "hacked": 1}})


def test_missing_github_section_rejected():
    with pytest.raises(ConfigError, match="github"):
        parse_config({})


def test_missing_owner_rejected():
    with pytest.raises(ConfigError, match="owner"):
        parse_config({"github": {"repo": "r"}})


def test_http_api_url_rejected():
    with pytest.raises(ConfigError, match="https"):
        parse_config({"github": {"owner": "o", "repo": "r", "api_url": "http://evil"}})


def test_host_network_rejected():
    with pytest.raises(ConfigError, match="host"):
        parse_config({"github": {"owner": "o", "repo": "r"}, "worker": {"network": "host"}})


def test_invalid_env_key_rejected():
    with pytest.raises(ConfigError, match="env key"):
        parse_config(
            {"github": {"owner": "o", "repo": "r"}, "worker": {"env": {"bad key": "x"}}}
        )


def test_secret_shaped_env_key_rejected_by_default():
    with pytest.raises(ConfigError, match="deny-list"):
        parse_config({"github": {"owner": "o", "repo": "r"}, "worker": {"env": {"MY_TOKEN": "x"}}})


def test_secret_shaped_env_key_allowed_when_listed():
    cfg = parse_config(
        {
            "github": {"owner": "o", "repo": "r"},
            "worker": {"env": {"MY_TOKEN": "x"}},
            "allow_secret_env": ["MY_TOKEN"],
        }
    )
    assert cfg.worker.env["MY_TOKEN"] == "x"


def test_negative_values_rejected():
    with pytest.raises(ConfigError):
        parse_config({"github": {"owner": "o", "repo": "r"}, "max_concurrent_jobs": 0})
    with pytest.raises(ConfigError):
        parse_config({"github": {"owner": "o", "repo": "r"}, "worker": {"memory_mb": -1}})


def test_task_env_validation():
    assert validate_task_env({"CI": "true"}) == {"CI": "true"}
    with pytest.raises(ConfigError):
        validate_task_env({"1BAD": "x"})
    with pytest.raises(ConfigError):
        validate_task_env({"LEAK_PASSWORD": "x"})
    assert validate_task_env({"LEAK_PASSWORD": "x"}, allow=("LEAK_PASSWORD",)) == {
        "LEAK_PASSWORD": "x"
    }


def test_load_config_from_toml(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[github]\nowner = "oo"\nrepo = "rr"\n', encoding="utf-8")
    cfg = load_config(target)
    assert cfg.github.repo == "rr"

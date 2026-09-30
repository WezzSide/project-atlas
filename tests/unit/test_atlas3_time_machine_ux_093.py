"""AT3-093 — isolated Time Machine UX reuse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_atlas.atlas3.contracts import Atlas3Error
from project_atlas.atlas3.time_machine_ux import (
    KDIFF_PACKAGE_ID,
    PACKAGE_ID,
    UX_SURFACE,
    compile_time_machine_ux,
)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def _write_declared(vault: Path, payload: dict[str, object]) -> None:
    path = vault / "generated" / "ops" / "atlas3" / "time-machine" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_missing_stays_unknown(tmp_path: Path) -> None:
    report = compile_time_machine_ux(_vault(tmp_path), "harbor-api")
    assert report["package_id"] == PACKAGE_ID
    assert report["data_package_id"] == KDIFF_PACKAGE_ID
    assert report["ux_surface"] == UX_SURFACE
    assert report["status"] == "UNKNOWN"
    assert report["engine"] == KDIFF_PACKAGE_ID
    assert report["second_temporal_engine"] is False
    assert report["wall_clock_is_valid_time"] is False
    assert report["as_of_is_authority"] is False
    assert report["new_cli_command"] is False
    assert report["merge_authorization"] == "NOT_GRANTED"
    assert report["write_applied"] is False


def test_composes_declared_kdiff_snapshots(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "engine": "AS-2.2-KDIFF-001",
            "snapshots": [{"valid_time": "2024-01-01T00:00:00Z", "kind": "kdiff-as-of-snapshot"}],
        },
    )
    report = compile_time_machine_ux(vault, "harbor-api")
    assert report["status"] == "derived"
    assert report["counts"]["snapshots"] == 1
    assert report["snapshots"][0]["valid_time"] == "2024-01-01T00:00:00Z"
    assert report["snapshots"][0]["engine"] == KDIFF_PACKAGE_ID


def test_second_clock_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {"project_id": "harbor-api", "engine": "atlas3-clock-2", "snapshots": []},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "SECOND_CLOCK"


def test_wall_clock_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {
            "project_id": "harbor-api",
            "snapshots": [{"valid_time": "2024-01-01T00:00:00Z", "wall_clock_is_valid_time": True}],
        },
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "WALL_CLOCK_AS_VALID_TIME"


def test_as_of_authority_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {"project_id": "harbor-api", "as_of_is_authority": True, "snapshots": []},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "AS_OF_AUTHORITY"


def test_cross_project_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(vault, {"project_id": "foreign-api", "snapshots": []})
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "CROSS_PROJECT"


def test_missing_valid_time_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_declared(
        vault,
        {"project_id": "harbor-api", "snapshots": [{"kind": "kdiff-as-of-snapshot"}]},
    )
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "VALID_TIME_REQUIRED"


def test_corrupt_fails_closed(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    path = vault / "generated" / "ops" / "atlas3" / "time-machine" / "harbor-api" / "declared.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "TIME_MACHINE_CORRUPT"


def test_unknown_project_fails_closed(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    with pytest.raises(Atlas3Error) as exc:
        compile_time_machine_ux(vault, "harbor-api")
    assert exc.value.code == "UNKNOWN_PROJECT"


def test_module_does_not_write() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/project_atlas/atlas3/time_machine_ux.py").read_text(encoding="utf-8")
    for name in (
        "write_json_atomic",
        "write_text(",
        "from project_atlas.knowledge_diff",
        "from project_atlas.bitemporal",
        "chatgpt_bridge",
        "add_parser",
        "atlas.query.read",
    ):
        assert name not in source

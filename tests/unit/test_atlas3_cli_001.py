"""Additive Atlas 3 CLI smoke. Existing commands remain registered."""

from __future__ import annotations

from pathlib import Path

from project_atlas.cli import EXIT_ERROR, EXIT_OK, main


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "projects" / "harbor-api").mkdir(parents=True)
    return vault


def test_help_lists_atlas3_and_keeps_core() -> None:
    from project_atlas.cli import build_parser

    text = build_parser().format_help()
    text.encode("cp1252")
    for name in (
        "pulse",
        "start",
        "proof",
        "memory",
        "ledger",
        "capabilities",
        "compatibility",
        "inventory",
        "file-graph",
        "estate-nodes",
        "causal-graph",
        "decided-by",
        "rel-expand",
        "iv-bind",
        "adv-bind",
        "surface-contract",
        "transport-authority",
        "provider-register",
        "impact-explorer",
        "twin-health",
        "home",
        "timeline",
        "decision-explorer",
        "truth-graph",
        "mission",
        "multi-project-twin",
        "org-identity",
        "claim-nodes",
        "conflict-unknown",
        "graph-authority",
        "connect",
        "ask2",
        "kdiff",
    ):
        assert name in text


def test_pulse_and_start_and_proof_and_memory(tmp_path: Path, capsys: object) -> None:
    vault = _vault(tmp_path)
    assert main(["pulse", "--vault", str(vault), "--project", "harbor-api", "--json"]) == EXIT_OK
    assert (
        main(
            ["start", "--vault", str(vault), "--project", "harbor-api", "--budget", "64", "--json"]
        )
        == EXIT_OK
    )
    assert (
        main(
            [
                "proof",
                "AT3-050-CLI",
                "--vault",
                str(vault),
                "--project",
                "harbor-api",
                "--model-claims-complete",
                "--json",
            ]
        )
        == EXIT_OK
    )
    assert main(["memory", "providers"]) == EXIT_OK
    assert main(["memory", "sync", "--json"]) == EXIT_OK
    assert main(["memory", "honesty", "--vault", str(vault), "--project", "harbor-api"]) == EXIT_OK
    assert (
        main(
            [
                "ledger",
                "append",
                "--vault",
                str(vault),
                "--project",
                "harbor-api",
                "--kind",
                "commit",
                "--summary",
                "cli append",
            ]
        )
        == EXIT_OK
    )
    assert main(["capabilities", "--json"]) == EXIT_OK
    assert main(["compatibility", "--vault", str(vault), "--json"]) == EXIT_OK
    assert (
        main(
            [
                "start",
                "--vault",
                str(vault),
                "--project",
                "harbor-api",
                "--budget",
                "64",
                "--freshness",
                "CURRENT",
                "--json",
            ]
        )
        == EXIT_OK
    )


def test_start_without_budget_is_usage_error() -> None:
    # argparse required=True → exit 2
    try:
        code = main(["start", "--vault", ".", "--project", "harbor-api"])
    except SystemExit as exc:
        code = int(exc.code or 0)
    assert code == 2


def test_unknown_project_is_operational_error(tmp_path: Path) -> None:
    vault = tmp_path / "empty"
    vault.mkdir()
    assert main(["pulse", "--vault", str(vault), "--project", "missing"]) == EXIT_ERROR

"""Current-main successor to historical PR #657 (`07367652`, base `9b554a1d`,
still open, `mergeable=CONFLICTING` against current main) plus the identical
open dogfood PR #657 branch `atlas3/validate-report-dogfood-004` -- neither
cherry-picked. Reconstructed fresh: `atlas validate-report --vault <dir>`,
an additive Atlas 3 command that exposes the canonical ``validate()``
result completely, without changing the existing `atlas validate` command
or reimplementing its severity->exit-code mapping.

HISTORICAL_CONTRACT = STILL VALID -- authentic dogfooding of `atlas
validate` observed only a single `broken link: ...` line printed on
failure; `validate()` itself is not fail-fast (it already runs every
sub-validator unconditionally and returns the complete errors/findings
lists) -- the gap is that nothing surfaces the complete result. This
command closes that gap additively.

Claim boundaries (not overclaimed): this is "additively registered through
Atlas 3" (the CLI surface) and "reuses the canonical validation result" --
not an independent Atlas 3 subsystem, and "complete payload" means the
complete current ``validate()`` result contract (``ok`` / ``errors`` /
``findings`` / ``markdown_files``), not a new, independently versioned
public schema.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from project_atlas.atlas3.validate_report import compile_validate_report
from project_atlas.cli import EXIT_ERROR, EXIT_OK, build_parser, main
from project_atlas.validation import validate, validation_exit_code


def _seed_vault(vault: Path) -> None:
    """Identical minimal clean-vault fixture to the one
    ``test_as_val_001_freshness_orphan.py`` already relies on for a
    ``validate() -> ok=True, errors=[]`` baseline -- not a new fixture
    convention."""
    for relative in (
        "index.md",
        "projects/index.md",
        "sources/index.md",
        "01-portfolio/index.md",
    ):
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class TestExistingValidateCommandUnaffected:
    """CONTRACT: NEW VALIDATE-REPORT != MODIFY EXISTING VALIDATE."""

    def test_originate_remains_registered(self) -> None:
        text = build_parser().format_help()
        assert "originate" in text

    def test_validate_report_is_additively_registered_alongside_validate(self) -> None:
        text = build_parser().format_help()
        assert "validate" in text
        assert "validate-report" in text

    def test_existing_validate_exit_code_unchanged_for_clean_vault(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        assert main(["validate", "--vault", str(vault)]) == EXIT_OK

    def test_existing_validate_exit_code_unchanged_for_error_vault(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"  # deliberately not seeded -- 4 missing-file errors
        vault.mkdir()
        assert main(["validate", "--vault", str(vault)]) == EXIT_ERROR

    def test_existing_validate_parser_takes_only_vault_no_new_arguments(self) -> None:
        import argparse

        parser = build_parser()
        sub = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        validate_parser = sub.choices["validate"]
        option_strings = {
            opt
            for action in validate_parser._actions
            for opt in action.option_strings
            if opt != "-h" and opt != "--help"
        }
        assert option_strings == {"--vault"}


class TestResultCompleteness:
    """CONTRACT: preserve at least ok / errors / findings / markdown_files,
    completely, via validation_exit_code() as the sole exit-mapping
    authority -- not a parallel reimplementation."""

    def test_clean_vault(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        report = compile_validate_report(vault)
        assert report["ok"] is True
        assert report["errors"] == []
        assert report["exit_code"] == 0
        assert report["markdown_files"] == 4

    def test_one_error(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        (vault / "projects" / "index.md").write_text(
            "[broken](does-not-exist.md)\n", encoding="utf-8"
        )
        report = compile_validate_report(vault)
        assert report["ok"] is False
        assert len(report["errors"]) == 1
        assert "does-not-exist.md" in report["errors"][0]
        assert report["exit_code"] == 1

    def test_multiple_errors_all_serialized_not_just_first(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        (vault / "projects" / "index.md").write_text(
            "[a](missing-a.md) [b](missing-b.md) [c](missing-c.md)\n",
            encoding="utf-8",
        )
        report = compile_validate_report(vault)
        assert len(report["errors"]) == 3, "must not print/keep only the first error"
        joined = "\n".join(report["errors"])
        for target in ("missing-a.md", "missing-b.md", "missing-c.md"):
            assert target in joined

    def test_warning_only_is_ok_true_with_findings_present(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        orphan = vault / "projects" / "lone" / "stray.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("# stray\n", encoding="utf-8")
        report = compile_validate_report(vault)
        assert report["ok"] is True
        assert report["exit_code"] == 0
        orphans = [f for f in report["findings"] if f["rule_id"] == "H-007-orphan"]
        assert len(orphans) == 1

    def test_error_plus_warning_keeps_both_not_just_the_error(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        (vault / "projects" / "index.md").write_text(
            "[broken](missing.md)\n", encoding="utf-8"
        )
        orphan = vault / "projects" / "lone" / "stray.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("# stray\n", encoding="utf-8")
        report = compile_validate_report(vault)
        assert report["ok"] is False  # errors dominate the boolean
        assert len(report["errors"]) == 1
        orphans = [f for f in report["findings"] if f["rule_id"] == "H-007-orphan"]
        assert len(orphans) == 1, "a co-occurring warning finding must not be dropped"

    def test_no_markdown_files_beyond_scaffold_reports_missing_file_errors(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()  # completely empty vault directory
        report = compile_validate_report(vault)
        assert report["markdown_files"] == 0
        assert len(report["errors"]) == 4  # the 4 required-file checks
        assert report["ok"] is False

    def test_large_finding_set_is_not_truncated(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        for i in range(200):
            note = vault / "projects" / f"p{i}" / "stray.md"
            note.parent.mkdir(parents=True)
            note.write_text(f"# stray {i}\n", encoding="utf-8")
        report = compile_validate_report(vault)
        orphans = [f for f in report["findings"] if f["rule_id"] == "H-007-orphan"]
        assert len(orphans) == 200

    def test_malformed_vault_fails_controlled_not_a_traceback(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "vault-is-actually-a-file"
        not_a_dir.write_text("not a vault", encoding="utf-8")
        report = compile_validate_report(not_a_dir)
        assert report["ok"] is False
        assert report["exit_code"] == 1
        assert report["errors"]

    def test_missing_vault_fails_controlled_not_a_traceback(self, tmp_path: Path) -> None:
        report = compile_validate_report(tmp_path / "does-not-exist-at-all")
        assert report["ok"] is False
        assert report["exit_code"] == 1
        assert report["errors"]

    @pytest.mark.parametrize(
        "make_vault",
        [
            lambda v: _seed_vault(v),
            lambda v: v.mkdir(),
        ],
    )
    def test_exit_code_always_equals_canonical_validation_exit_code(
        self, tmp_path: Path, make_vault: object
    ) -> None:
        """Direct proof against the two exit-code negative controls: never
        hardcode 0, never reimplement the mapping."""
        vault = tmp_path / "vault"
        make_vault(vault)  # type: ignore[operator]
        direct = validate(vault)
        report = compile_validate_report(vault)
        assert report["exit_code"] == validation_exit_code(direct)
        assert report["ok"] == direct["ok"]
        assert report["errors"] == direct["errors"]
        assert report["findings"] == direct["findings"]
        assert report["markdown_files"] == direct["markdown_files"]


class TestNegativeControls:
    """Each test fails if the implementation regresses to one of the
    forbidden shapes listed in the successor contract."""

    def test_does_not_call_validate_more_than_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        calls = {"n": 0}
        real_validate = validate

        def counting_validate(*args: object, **kwargs: object) -> dict[str, object]:
            calls["n"] += 1
            return real_validate(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("project_atlas.atlas3.validate_report.validate", counting_validate)
        compile_validate_report(vault)
        assert calls["n"] == 1

    def test_does_not_mutate_the_returned_validate_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        sentinel = {
            "ok": False,
            "errors": ["one"],
            "findings": [{"rule_id": "X", "finding_id": "1", "severity": "warning"}],
            "markdown_files": 4,
        }
        monkeypatch.setattr(
            "project_atlas.atlas3.validate_report.validate", lambda *a, **k: sentinel
        )
        report = compile_validate_report(vault)
        assert sentinel["errors"] == ["one"], "the original result must not be mutated"
        assert report["errors"] == sentinel["errors"]
        assert report["errors"] is not sentinel["errors"], "report must hold its own copy"

    def test_never_exits_zero_when_errors_present(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        report = compile_validate_report(vault)
        assert report["exit_code"] != 0

    def test_does_not_write_canonical_or_ops_store_state(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        before = _hash_tree(vault)
        compile_validate_report(vault)
        after = _hash_tree(vault)
        assert after == before, "VALIDATE-REPORT IS OBSERVATIONAL: zero canonical writes"
        assert not (vault / "generated" / "ops" / "atlas3").exists()


class TestCliEndToEnd:
    def test_clean_vault_via_cli_is_deterministic_json_on_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        vault = tmp_path / "vault"
        _seed_vault(vault)
        assert main(["validate-report", "--vault", str(vault)]) == EXIT_OK
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["schema"] == "atlas3.validate-report.v1"
        assert payload["observational"] is True

        # Deterministic: an identical second run byte-matches the first.
        assert main(["validate-report", "--vault", str(vault)]) == EXIT_OK
        assert capsys.readouterr().out == out

    def test_error_vault_via_cli_matches_existing_validate_exit_code(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        report_exit = main(["validate-report", "--vault", str(vault)])
        validate_exit = main(["validate", "--vault", str(vault)])
        assert report_exit == validate_exit == EXIT_ERROR

    def test_full_local_pipeline_vault_via_cli_no_mutation(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from project_atlas.cli import main as atlas_main

        source = tmp_path / "source"
        source.mkdir()
        (source / "README.md").write_text("# demo source\n", encoding="utf-8")
        vault = tmp_path / "vault"
        manifest = tmp_path / "manifest.json"
        assert atlas_main(["init", "--output", str(vault)]) == EXIT_OK
        assert (
            atlas_main(["discover", "--source", str(source), "--output", str(manifest)])
            == EXIT_OK
        )
        assert (
            atlas_main(
                [
                    "ingest",
                    "--manifest",
                    str(manifest),
                    "--vault",
                    str(vault),
                    "--source",
                    str(source),
                ]
            )
            == EXIT_OK
        )
        assert atlas_main(["build-indexes", "--vault", str(vault)]) == EXIT_OK
        capsys.readouterr()

        before = _hash_tree(vault)
        exit_code = main(["validate-report", "--vault", str(vault)])
        after = _hash_tree(vault)
        assert after == before
        payload = json.loads(capsys.readouterr().out)
        assert payload["exit_code"] == exit_code

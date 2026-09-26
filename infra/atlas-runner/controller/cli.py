"""argparse CLI for the atlas-runner controller (AS-RUNNER-FABRIC-001).

Contract: `python -m controller <subcommand>`. Exit codes: 0 ok, 1 runtime
failure, 2 usage/validation rejection (fail closed). SUBMIT_ACCEPTED !=
EXECUTED; STATUS_REPORT != VERIFIED.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from controller import CONFIG_SCHEMA_VERSION, CONTROLLER_VERSION
from controller.config import ConfigError, load_config
from controller.controller import Controller, source_revision
from controller.dockerctl import DockerCtl
from controller.evidence import validate_evidence
from controller.github import GitHubClient, StaticTokenProvider, TokenProvider
from controller.health import health_json, run_health
from controller.reconcile import Reconciler
from controller.state import StateStore, TaskConflictError


def _build_context(config_path: str):
    config = load_config(config_path)
    store = StateStore(Path(config.paths.state_dir) / "atlas-runner.db")
    docker = DockerCtl(jobs_root=config.paths.jobs_dir)
    token_provider: TokenProvider | None = None
    if config.github.owner and config.github.repo:
        token_provider = StaticTokenProvider()  # reads ATLAS_GITHUB_TOKEN (0600 env file)
    github = GitHubClient(
        owner=config.github.owner,
        repo=config.github.repo,
        api_url=config.github.api_url,
        token_provider=token_provider,
    )
    return config, store, docker, github


def _load_task_file(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read task file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("task file must contain a JSON object")
    return data


def _validate_task_schema(task: dict) -> None:
    """Validate a submitted task against the worker-task contract (stdlib)."""
    from controller.schemas import validate_worker_task

    errors = validate_worker_task(task)
    if errors:
        raise ConfigError("task rejected by worker-task schema: " + "; ".join(errors))


def cmd_run(args: argparse.Namespace) -> int:
    config, store, docker, github = _build_context(args.config)
    controller = Controller(config=config, store=store, docker=docker, github=github)
    Reconciler(config=config, store=store, docker=docker).reconcile()
    controller.run_forever()
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    config, store, docker, github = _build_context(args.config)
    controller = Controller(config=config, store=store, docker=docker, github=github)
    admitted = controller.poll_once()
    print(json.dumps({"admitted": admitted}, sort_keys=True))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    config, store, docker, github = _build_context(args.config)
    try:
        task = _load_task_file(args.task_json)
        _validate_task_schema(task)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    controller = Controller(config=config, store=store, docker=docker, github=github)
    try:
        outcome, detail = controller.submit_task(task)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (TaskConflictError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"outcome": outcome, "detail": detail}, sort_keys=True))
    return 0 if outcome != "conflict" else 2


def cmd_status(args: argparse.Namespace) -> int:
    _config, store, _docker, _github = _build_context(args.config)
    rows = store.active_executions()
    payload = {
        "active_workers": store.count_active_workers(),
        "active": [
            {
                "execution_id": r["execution_id"],
                "task_id": r["task_id"],
                "status": r["status"],
                "worker_name": r.get("worker_name"),
            }
            for r in rows
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"active_workers: {payload['active_workers']}")
        for row in payload["active"]:
            print(
                f"  {row['execution_id']} {row['status']} "
                f"{row['task_id']} {row['worker_name'] or ''}"
            )
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    config_text = None
    with contextlib.suppress(OSError):
        config_text = Path(args.config).read_text(encoding="utf-8")
    config, store, docker, github = _build_context(args.config)
    report, exit_code = run_health(
        config=config, store=store, docker=docker, github=github, config_text=config_text
    )
    print(health_json(report))
    return exit_code


def cmd_version(args: argparse.Namespace) -> int:
    config, _store, docker, _github = _build_context(args.config)
    release_link = "/opt/atlas-runner/current"
    release_target = None
    try:
        release_target = str(Path(release_link).resolve())
    except OSError:
        release_target = None
    image_digest = docker.image_digest(config.worker.image)
    source_rev = source_revision()
    baked = Path(__file__).resolve().parent / ".source-revision"
    if baked.is_file():
        source_rev = baked.read_text(encoding="utf-8").strip() or source_rev
    print(
        json.dumps(
            {
                "controller_version": CONTROLLER_VERSION,
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "source_revision": source_rev,
                "release": {"link": release_link, "target": release_target},
                "runner_image": config.worker.image,
                "runner_image_digest": image_digest,
                "actions_runner_version": _runner_version_from_image(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _runner_version_from_image() -> str | None:
    """Best effort: actions/runner version baked into the Dockerfile ARG."""
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    try:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if line.startswith("ARG RUNNER_VERSION="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def cmd_reconcile(args: argparse.Namespace) -> int:
    config, store, docker, _github = _build_context(args.config)
    reconciler = Reconciler(config=config, store=store, docker=docker)
    summary = reconciler.reconcile()
    anomalies = reconciler.foreign_worker_anomalies()
    print(
        json.dumps(
            {"summary": summary, "blocked_anomalies": anomalies},
            indent=2,
            sort_keys=True,
        )
    )
    return 2 if anomalies else 0


def cmd_list_workers(args: argparse.Namespace) -> int:
    _config, store, _docker, _github = _build_context(args.config)
    rows = store.active_executions()
    for row in rows:
        print(
            f"{row['execution_id']}\t{row['status']}\t"
            f"{row.get('worker_name') or '-'}\t{row['task_id']}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _config, store, _docker, _github = _build_context(args.config)
    row = store.get_execution(args.execution_id)
    if row is None:
        print(f"unknown execution: {args.execution_id}", file=sys.stderr)
        return 2
    task = store.get_task(row["task_id"])
    evidence_path = row.get("evidence_path")
    evidence_doc = None
    if evidence_path and Path(evidence_path).is_file():
        evidence_doc = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        errors = validate_evidence(evidence_doc)
        if errors:
            evidence_doc = {"invalid": True, "errors": errors}
    print(
        json.dumps(
            {"execution": row, "task": task, "evidence": evidence_doc},
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas-runner", description=__doc__)
    parser.add_argument("--config", default="/etc/atlas-runner/config/atlas-runner.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="daemon loop (graceful SIGTERM)").set_defaults(func=cmd_run)
    once = sub.add_parser("once", help="poll once (cron/systemd timer fallback)")
    once.set_defaults(func=cmd_once)

    submit = sub.add_parser("submit", help="submit a task JSON file")
    submit.add_argument("task_json")
    submit.set_defaults(func=cmd_submit)

    status = sub.add_parser("status", help="controller status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    sub.add_parser("health", help="health report JSON, exit 0/1/2").set_defaults(func=cmd_health)
    sub.add_parser("version", help="version + release identity JSON").set_defaults(func=cmd_version)
    rec = sub.add_parser("reconcile", help="crash recovery / stale cleanup")
    rec.set_defaults(func=cmd_reconcile)
    sub.add_parser("list-workers", help="list active workers").set_defaults(func=cmd_list_workers)

    show = sub.add_parser("show", help="show one execution")
    show.add_argument("execution_id")
    show.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

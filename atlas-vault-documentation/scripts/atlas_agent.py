#!/usr/bin/env python3
"""Universal managed Atlas agent launcher and documentation command."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_control import (
    authority,
    bootstrap,
    capability,
    doctor,
    event_client,
    event_reprocess,
    postflight,
    preflight,
    readiness,
    receipt_gate,
    repository_gate,
    session,
    skill_ack,
    skill_compiler,
    skill_loader,
    spool_sync,
)

ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = ROOT / "skills" / "atlas-governed-work"


def _json(value: Any, enabled: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str) if enabled else value)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--vault-root", type=Path)
    parser.add_argument("--agent", default="generic")
    parser.add_argument("--agent-id")
    parser.add_argument("--json", action="store_true", dest="json_output")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    _common(run_parser)
    run_parser.add_argument("--task-id", default="unknown")
    run_parser.add_argument("command_args", nargs=argparse.REMAINDER)
    pre_parser = sub.add_parser("preflight")
    _common(pre_parser)
    boot_parser = sub.add_parser("bootstrap")
    _common(boot_parser)
    boot_parser.add_argument("--task-id", default="unknown")
    ack_parser = sub.add_parser("acknowledge-skill")
    ack_parser.add_argument("--vault-root", type=Path, default=Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
    ack_parser.add_argument("--session-id", default=os.environ.get("ATLAS_SESSION_ID"))
    ack_parser.add_argument("--skill", default="atlas-governed-work")
    ack_parser.add_argument("--version")
    ack_parser.add_argument("--sha256")
    ack_parser.add_argument("--json", action="store_true", dest="json_output")
    cap_parser = sub.add_parser("capability-check")
    cap_parser.add_argument("--vault-root", type=Path, default=Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
    cap_parser.add_argument("--session-id", default=os.environ.get("ATLAS_SESSION_ID"))
    cap_parser.add_argument("--json", action="store_true", dest="json_output")
    sync_parser = sub.add_parser("sync-spool")
    sync_parser.add_argument("--spool-root", type=Path, required=True)
    sync_parser.add_argument("--vault-root", type=Path, required=True)
    sync_parser.add_argument("--mda-command", required=True)
    sync_parser.add_argument("--json", action="store_true", dest="json_output")
    reprocess_parser = sub.add_parser(
        "reprocess-events", help="resume captured Vault events for one managed session"
    )
    reprocess_parser.add_argument("--vault-root", type=Path, required=True)
    reprocess_parser.add_argument("--session-id", required=True)
    reprocess_parser.add_argument(
        "--mda-command", required=True, help="trusted mda-cli command name or path"
    )
    reprocess_parser.add_argument("--json", action="store_true", dest="json_output")
    promote_parser = sub.add_parser("promote-readiness")
    promote_parser.add_argument("--registry", type=Path, required=True)
    promote_parser.add_argument("--adapter-id", required=True)
    promote_parser.add_argument("--rehearsal-id", required=True)
    promote_parser.add_argument("--receipt", type=Path, required=True)
    promote_parser.add_argument("--authority-grant", type=Path, required=True, help="Independently issued authority grant (SEC-016/019)")
    promote_parser.add_argument("--authority-store", type=Path, help="Authority store root for revocation checks")
    promote_parser.add_argument("--skill-id", default="atlas-governed-work")
    promote_parser.add_argument("--skill-version", required=True)
    promote_parser.add_argument("--skill-sha256", required=True)
    promote_parser.add_argument("--json", action="store_true", dest="json_output")
    issue_auth = sub.add_parser("issue-authority")
    issue_auth.add_argument("--store", type=Path, required=True)
    issue_auth.add_argument("--purpose", default=authority.PURPOSE_PROMOTE_READINESS)
    issue_auth.add_argument("--adapter-id", required=True)
    issue_auth.add_argument("--skill-id", default="atlas-governed-work")
    issue_auth.add_argument("--skill-version", required=True)
    issue_auth.add_argument("--skill-sha256", required=True)
    issue_auth.add_argument("--issuer-id", required=True, help="Independent issuer identity (must not equal requester)")
    issue_auth.add_argument("--requester-id", help="Optional bound requester; must differ from issuer")
    issue_auth.add_argument("--evidence-receipt-id", help="Optional bound receipt id (evidence only, not authority)")
    issue_auth.add_argument("--json", action="store_true", dest="json_output")
    revoke_auth = sub.add_parser("revoke-authority")
    revoke_auth.add_argument("--store", type=Path, required=True)
    revoke_auth.add_argument("--grant-id", required=True)
    revoke_auth.add_argument("--json", action="store_true", dest="json_output")
    gate_parser = sub.add_parser("repository-gate")
    gate_parser.add_argument("--project-id", required=True)
    gate_parser.add_argument("--changed-file", action="append", default=[])
    gate_parser.add_argument("--receipt", type=Path)
    gate_parser.add_argument("--skill-sha256")
    gate_parser.add_argument("--json", action="store_true", dest="json_output")
    doc_parser = sub.add_parser("document")
    doc_parser.add_argument("--type", dest="event_type", required=True, choices=("implementation", "decision", "validation", "blocked", "completion", "session-start"))
    doc_parser.add_argument("--summary", required=True)
    doc_parser.add_argument("--work-package")
    doc_parser.add_argument("--session-id", default=os.environ.get("ATLAS_SESSION_ID"))
    doc_parser.add_argument("--vault-root", type=Path, default=Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
    doc_parser.add_argument("--json", action="store_true", dest="json_output")
    post_parser = sub.add_parser("postflight")
    post_parser.add_argument("--vault-root", type=Path, default=Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
    post_parser.add_argument("--session-id", default=os.environ.get("ATLAS_SESSION_ID"), required=False)
    post_parser.add_argument("--json", action="store_true", dest="json_output")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--vault-root", type=Path, default=Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
    status_parser.add_argument("--session-id", default=os.environ.get("ATLAS_SESSION_ID"))
    status_parser.add_argument("--json", action="store_true", dest="json_output")
    for name in ("receipt", "validate"):
        item = sub.add_parser(name)
        item.add_argument("--vault-root", type=Path, default=Path(os.environ["ATLAS_VAULT_ROOT"]) if os.environ.get("ATLAS_VAULT_ROOT") else None)
        item.add_argument("--session-id", default=os.environ.get("ATLAS_SESSION_ID"))
        item.add_argument("--json", action="store_true", dest="json_output")
    doctor_parser = sub.add_parser("doctor")
    _common(doctor_parser)
    verify_parser = sub.add_parser("verify-instructions")
    verify_parser.add_argument("--json", action="store_true", dest="json_output")
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--vault-id", required=True)
    install_parser.add_argument("--vault-root", type=Path, required=True)
    init_parser = sub.add_parser("init-project")
    init_parser.add_argument("--project-id", required=True)
    init_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-instructions":
            skill = skill_loader.load(SKILL_ROOT)
            errors = skill_compiler.verify(skill, ROOT / ".generated-agent-instructions")
            _json({"ok": not errors, "errors": errors}, args.json_output)
            return 0 if not errors else 1
        if args.command == "install":
            # AS-DEMO-2.2-RECOVERY-ID-001: reuse Core canonical writer (no second mint path).
            from project_atlas.vault_identity import VaultIdentityError, ensure_vault_identity

            root = args.vault_root.resolve()
            try:
                ensure_vault_identity(root, vault_id=args.vault_id)
            except VaultIdentityError as exc:
                raise ValueError(str(exc)) from exc
            _json({"ok": True, "vault_id": args.vault_id, "root": str(root)}, getattr(args, "json_output", False))
            return 0
        if args.command == "init-project":
            target = args.project_root / ".atlas" / "project.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"schema_version: 1\nproject:\n  id: {args.project_id}\n  name: {args.project_id}\ndocumentation:\n  skill_id: atlas-governed-work\n  require_receipt: true\n  strict: true\nvault:\n  required_vault_id: atlas-main\nevents:\n  require_start: true\n  require_validation: true\n  require_completion: true\n", encoding="utf-8")
            print(str(target))
            return 0
        if args.command == "preflight":
            result = preflight.run(project_root=args.project_root, vault_root=args.vault_root, agent_type=args.agent, agent_value=args.agent_id, skill_root=SKILL_ROOT)
            _json(result, args.json_output)
            return 0
        if args.command in {"bootstrap", "run"}:
            state, environment = bootstrap.start(project_root=args.project_root, vault_root=args.vault_root, agent_type=args.agent, agent_value=args.agent_id, task_id=args.task_id, skill_root=SKILL_ROOT)
            if args.command == "bootstrap":
                result = {"ok": True, "bootstrap_schema": 1, "project": {"id": state["session"]["project_id"], "root": state["preflight"]["project_root"]}, "vault": {**state["vault"], "verified": not state["preflight"]["spool"].get("mode", False)}, "session": {"id": state["session"]["session_id"], "agent_id": state["agent"]["agent_id"], "work_package": state["session"]["task_id"]}, "skill": {**state["skill"], "required": True}, "next_action": {"command": "atlas-agent acknowledge-skill --session current --json"}}
                _json(result, args.json_output)
                return 0
            command = list(args.command_args)
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise ValueError("atlas-agent run requires a command after --")
            # The managed launcher owns acknowledgement and capability gates;
            # a child agent cannot begin governed work in an unacknowledged session.
            skill_ack.acknowledge(Path(str(state["vault"]["root"])), str(state["session"]["session_id"]), str(state["skill"]["id"]), str(state["skill"]["version"]), str(state["skill"]["sha256"]))
            capability_result = capability.check(Path(str(state["vault"]["root"])), str(state["session"]["session_id"]))
            if not capability_result["ready"]:
                raise ValueError("managed launcher capability preflight failed")
            state = session.load(Path(str(state["vault"]["root"])), str(state["session"]["session_id"]))
            child_env = dict(os.environ, **environment, ATLAS_AGENT_CONTEXT=bootstrap.injected_context(state))
            completed = subprocess.run(command, env=child_env, check=False)
            result = postflight.run(Path(str(state["vault"]["root"])), str(state["session"]["session_id"]))
            return completed.returncode if completed.returncode else (0 if result["ok"] else 4)
        if args.command == "doctor":
            result = doctor.run(project_root=args.project_root, vault_root=args.vault_root, agent_type=args.agent, skill_root=SKILL_ROOT, generated_root=ROOT / ".generated-agent-instructions")
            _json(result, args.json_output)
            return 0 if result["ok"] else 1
        if args.command == "acknowledge-skill":
            if not args.vault_root or not args.session_id:
                raise ValueError("acknowledge-skill requires an active Vault and session")
            state = session.load(args.vault_root, args.session_id)
            result = skill_ack.acknowledge(args.vault_root, args.session_id, args.skill, args.version or str(state["skill"]["version"]), args.sha256 or str(state["skill"]["sha256"]))
            _json(result, args.json_output)
            return 0
        if args.command == "capability-check":
            if not args.vault_root or not args.session_id:
                raise ValueError("capability-check requires an active Vault and session")
            result = capability.check(args.vault_root, args.session_id)
            _json(result, args.json_output)
            return 0 if result["ready"] else 4
        if args.command == "sync-spool":
            result = spool_sync.synchronize(args.spool_root, args.vault_root, args.mda_command)
            _json(result, args.json_output)
            return 0
        if args.command == "reprocess-events":
            try:
                result = event_reprocess.reprocess(
                    vault_root=args.vault_root,
                    session_id=args.session_id,
                    mda_command=args.mda_command,
                )
            except event_reprocess.EventReprocessError as exc:
                _json({"ok": False, "status": "blocked", "error_code": exc.code, "message": str(exc)}, args.json_output)
                return 4
            _json(result, args.json_output)
            return 0
        if args.command == "issue-authority":
            # GRANT issuance is a separate privileged step from REQUEST/EXECUTION.
            result = authority.issue_grant(
                store=args.store,
                purpose=args.purpose,
                adapter_id=args.adapter_id,
                skill_id=args.skill_id,
                skill_version=args.skill_version,
                skill_sha256=args.skill_sha256,
                issuer_id=args.issuer_id,
                requester_id=args.requester_id,
                evidence_receipt_id=args.evidence_receipt_id,
            )
            _json(result, args.json_output)
            return 0
        if args.command == "revoke-authority":
            result = authority.revoke_grant(store=args.store, grant_id=args.grant_id)
            _json(result, args.json_output)
            return 0
        if args.command == "promote-readiness":
            # SEC-016: receipt is evidence only. SEC-019: selecting promote must not
            # create the authority that approves itself — require independent GRANT.
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            if receipt.get("is_authority") is True or receipt.get("receipt_is_authority") is True:
                raise ValueError("self-asserted receipt is not authority")
            pipeline = receipt.get("pipeline", {})
            if (
                receipt.get("receipt_type") not in {"atlas-agent-session", "atlas-governed-work-rehearsal"}
                or receipt.get("validation", {}).get("receipt") != "passed"
                or not receipt.get("capability", {}).get("ready")
                or receipt.get("skill", {}).get("id") != args.skill_id
                or receipt.get("skill", {}).get("version") != args.skill_version
                or receipt.get("skill", {}).get("sha256") != args.skill_sha256
                or receipt.get("pipeline", {}).get("pending_spool", 0) != 0
                or not (pipeline.get("captured") == pipeline.get("normalized") == pipeline.get("verified") == pipeline.get("routed"))
            ):
                raise ValueError("readiness promotion requires a validated agent-session receipt as evidence")
            requester_id = str(receipt.get("agent", {}).get("agent_id") or "")
            grant = authority.verify_grant(
                args.authority_grant,
                purpose=authority.PURPOSE_PROMOTE_READINESS,
                adapter_id=args.adapter_id,
                skill_id=args.skill_id,
                skill_version=args.skill_version,
                skill_sha256=args.skill_sha256,
                requester_id=requester_id or None,
                evidence_receipt_id=str(receipt.get("receipt_id") or "") or None,
                store=args.authority_store,
            )
            import hashlib

            receipt_sha256 = hashlib.sha256(args.receipt.read_bytes()).hexdigest()
            result = readiness.promote(
                args.registry,
                args.adapter_id,
                args.skill_id,
                args.skill_version,
                args.skill_sha256,
                args.rehearsal_id,
                receipt_sha256,
                authority_grant=grant,
            )
            _json(result, args.json_output)
            return 0
        if args.command == "repository-gate":
            result = repository_gate.validate(project_id=args.project_id, changed_files=args.changed_file, receipt_path=args.receipt, skill_sha256=args.skill_sha256)
            _json(result, args.json_output)
            return 0 if result["ok"] else 4
        if args.command == "document":
            if not args.vault_root or not args.session_id:
                raise ValueError("document requires an active Vault and session")
            result = event_client.document(vault_root=args.vault_root, session_id=args.session_id, event_type=args.event_type, summary=args.summary, work_package=args.work_package)
            _json(result, args.json_output)
            return 0
        if args.command == "postflight":
            if not args.vault_root or not args.session_id:
                raise ValueError("postflight requires Vault and session")
            result = postflight.run(args.vault_root, args.session_id)
            _json(result, args.json_output)
            return 0 if result["ok"] else 4
        if args.command in {"status", "receipt", "validate"}:
            if not args.vault_root or not args.session_id:
                raise ValueError("session command requires Vault and session")
            state = session.load(args.vault_root, args.session_id)
            if args.command == "receipt":
                receipt = receipt_gate.issue(args.vault_root, state)
                _json(receipt, args.json_output)
                return 0
            result = {"ok": True, "status": state.get("status"), "session": state.get("session"), "skill": state.get("skill"), "pipeline": state.get("pipeline"), "receipt_id": state.get("receipt_id")}
            if args.command == "validate":
                errors = receipt_gate.validate(state)
                result.update({"ok": not errors, "errors": errors})
            _json(result, args.json_output)
            return 0 if result["ok"] else 4
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

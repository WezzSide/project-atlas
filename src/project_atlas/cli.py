"""Command-line interface for Project Atlas (A-007).

Exit codes:
- 0: success (``atlas validate``: also WARNING/INFO-only findings — AS-H-010);
- 1: operational error (unsafe path, non-empty target, I/O failure), or
  ``atlas validate`` ERROR findings / legacy validation errors (AS-H-010);
- 2: usage error (argparse).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from project_atlas import __version__
from project_atlas.adv_release_cert import (
    AdvReleaseCertError,
    run_fixture_adv_release_certification,
)
from project_atlas.agent_handoff import (
    AgentHandoffError,
    create_handoff,
    export_agent_context,
    resume_handoff,
)
from project_atlas.api_server import ApiServerError, serve_api
from project_atlas.ask2 import Ask2Error, ask_atlas_2
from project_atlas.ask2 import answer_to_json as ask2_answer_to_json
from project_atlas.attention_hygiene import AttentionHygieneError, classify_attention
from project_atlas.authz import (
    AuthzError,
    publish_api_session_credentials,
    require_cli_elevated_operator,
)
from project_atlas.autonomy_l3 import AutonomyL3Error, run_bounded_l3_loop
from project_atlas.backup import (
    BackupError,
    create_snapshot,
    restore_bundle,
    verify_bundle,
)
from project_atlas.bitemporal_catalog import build_bitemporal_catalogs
from project_atlas.capture_sources import (
    SOURCE_TYPES,
    CaptureSourceError,
    build_capture_request,
    read_clipboard_text,
    read_stdin_text,
)
from project_atlas.compat_anchor import (
    CompatAnchorError,
    load_compatibility_anchor,
)
from project_atlas.config import AtlasConfig, load_config
from project_atlas.connect import (
    ConnectError,
    connect_project,
    resolve_bound_project_id,
    resolve_bound_vault,
)
from project_atlas.context_pack import (
    ContextEntry,
    ContextPackError,
    ProvenancePointer,
    build_context_pack,
)
from project_atlas.conversation_capture import (
    ConversationCaptureError,
    capture_conversation,
    envelope_from_cli_items,
    set_conversation_review_state,
)
from project_atlas.discovery import discover, write_manifest
from project_atlas.doctor import render_text as doctor_render_text
from project_atlas.doctor import run_doctor
from project_atlas.doctor import to_dict as doctor_to_dict
from project_atlas.domain.knowledge_query import KnowledgeQueryErrorCode, QueryShape
from project_atlas.estate_discovery import (
    DEFAULT_MAX_DEPTH,
    INCREMENTAL_CACHE_RELATIVE,
    REPORT_RELATIVE,
    ROOT_MODE_BOUNDED_DIRECTORY,
    ROOT_MODE_OWNER_AUTHORIZED_VOLUME,
    EstateDiscoveryError,
    connect_discovered_candidate,
    discover_estate,
    format_discovery_human,
    load_discovery_cache,
    review_candidates,
    write_discovery_cache,
    write_discovery_report,
)
from project_atlas.event_retention import (
    RetentionError,
    apply_event_retention,
)
from project_atlas.federation import (
    FederationError,
    FederationMember,
    build_join_inventory,
)
from project_atlas.graph_acceptance import (
    GraphAcceptanceError,
    accept_graphify_artifacts,
    inspect_acceptance,
)
from project_atlas.graph_relationships import (
    GraphRelationshipError,
    inspect_relationship_store,
    store_from_acceptance,
    write_relationship_outputs,
)
from project_atlas.graph_resolution import (
    GraphResolutionError,
    inspect_resolution,
    resolve_from_acceptance,
    write_resolution_outputs,
)
from project_atlas.human_loop import HumanLoopError, apply_review_decision
from project_atlas.indexes import build_indexes
from project_atlas.ingestion import ingest
from project_atlas.kci import (
    KciError,
    build_compile_request,
    issue_compile_receipt,
)
from project_atlas.kf2_fabric import (
    Kf2Error,
    register_entity,
    register_namespace,
    register_relationship,
)
from project_atlas.knowledge_diff import (
    AS_OF_KIND,
    DEFAULT_SUBJECT_CAP,
    KnowledgeDiffError,
    diff_knowledge,
    read_as_of,
)
from project_atlas.knowledge_diff import diff_to_json as kdiff_diff_to_json
from project_atlas.knowledge_diff import snapshot_to_json as kdiff_snapshot_to_json
from project_atlas.knowledge_inbox import (
    KnowledgeInboxError,
    list_inbox_items,
)
from project_atlas.knowledge_query import (
    KnowledgeQueryError,
    answer_to_json,
    diagnostic_to_json,
    list_authoritative,
    list_temporal,
    query_diagnostic_from_error,
    query_knowledge,
    query_knowledge_fields,
)
from project_atlas.lifecycle_cert import (
    LifecycleCertError,
    run_fixture_lifecycle_certification,
)
from project_atlas.logging import configure_logging, get_logger
from project_atlas.mcp_server import McpServerError, invoke_mcp_tool
from project_atlas.migrations.claim_v2_migration import migrate_v2
from project_atlas.obsidian_capture import (
    CLASSIFICATIONS,
    CaptureError,
    RoutingPolicy,
    read_raw_content,
)
from project_atlas.obsidian_capture import capture as capture_raw
from project_atlas.obsidian_capture import (
    list_captures as list_raw_captures,
)
from project_atlas.obsidian_capture import (
    retry as retry_capture,
)
from project_atlas.obsidian_projection import (
    ObsidianProjectionError,
    materialize_obsidian_projection,
)
from project_atlas.openai_import_real import (
    OpenAIRealImportError,
    import_openai_export,
)
from project_atlas.openai_importer_fixtures import (
    OpenAIImportFixtureError,
    build_openai_import_fixture_receipt,
    default_sample_path,
)
from project_atlas.openai_responses_poc import (
    OpenAIResponsesPocError,
    run_openai_responses_poc,
)
from project_atlas.ops_events import (
    OpsEventError,
    apply_retention,
    read_events,
    record_health_transition,
)
from project_atlas.ops_health import (
    OpsHealthError,
    emit_health_snapshot,
    snapshot_to_json,
)
from project_atlas.ops_report import (
    OpsReportError,
    emit_ops_report,
    report_to_json,
)
from project_atlas.overview import OverviewError, materialize_overview_lenses
from project_atlas.perf_baselines import PerfBaselineError, run_perf_baselines
from project_atlas.pilot_auth_prep import PilotAuthPrepError, write_pilot_prep_report
from project_atlas.portfolio import build_portfolio
from project_atlas.project_brief import ProjectBriefError, materialize_project_briefs
from project_atlas.project_changed import (
    ProjectChangedError,
    materialize_changed_lenses,
)
from project_atlas.project_decisions import (
    ProjectDecisionsError,
    materialize_decisions_lenses,
)
from project_atlas.project_next import (
    ProjectNextError,
    derive_next_lenses,
    materialize_next_lenses,
    render_next_text,
)
from project_atlas.project_roadmap import (
    ProjectRoadmapError,
    derive_roadmap_lenses,
    materialize_roadmap_lenses,
    render_roadmap_text,
)
from project_atlas.project_state import ProjectStateError, materialize_state_lenses
from project_atlas.project_unknown import ProjectUnknownError, materialize_unknown_lenses
from project_atlas.provider_adapters import (
    ProviderAdapter,
    ProviderError,
    build_adapter_registry,
    quarantine_provider_output,
)
from project_atlas.receipt_revocation import (
    RevocationError,
    inventory_with_revocations,
    list_revocations,
    receipt_trust_disposition,
    revoke_receipt,
)
from project_atlas.runtime_22 import (
    Runtime22Error,
)
from project_atlas.runtime_22 import (
    compile_context as runtime_compile_context,
)
from project_atlas.runtime_22 import (
    hybrid_retrieve as runtime_hybrid_retrieve,
)
from project_atlas.runtime_22 import (
    package_to_json as runtime_package_to_json,
)
from project_atlas.scaffold import ScaffoldError, create_scaffold
from project_atlas.scheduler_live import (
    SchedulerLiveError,
    arm_scheduler,
    dispatch_supervised_job,
)
from project_atlas.schema_compat import (
    SchemaCompatError,
    migrate_dry_run,
    scan_compat,
)
from project_atlas.session_capture import (
    SessionCaptureError,
    capture_session,
    list_captures,
)
from project_atlas.source_health import SourceHealthError, explain_source_health
from project_atlas.terminal_io import human_print
from project_atlas.twin_fixtures import (
    TwinFixtureError,
    TwinProjectRow,
    build_twin_projection_fixture,
)
from project_atlas.validation import validate, validation_exit_code
from project_atlas.workspace_registry import (
    WorkspaceRegistryError,
    build_dry_run_registry,
    write_dry_run_registry,
)
from project_atlas.xproj_duplicates import (
    XprojDuplicateError,
    detect_project_duplicates,
    inspect_duplicate_detection,
    write_duplicate_outputs,
)
from project_atlas.xproj_edges import (
    XprojEdgeError,
    apply_edge_registrations,
    inspect_edge_registry,
    load_edge_registry_state,
    write_edge_outputs,
)
from project_atlas.xproj_registry import (
    XprojRegistryError,
    apply_registrations,
    inspect_registry,
    load_registry_state,
    write_registry_outputs,
)

EXIT_OK = 0
EXIT_ERROR = 1

_log = get_logger("cli")



def _apply_stranger_defaults(args: argparse.Namespace) -> None:
    """Resolve omitted --vault/--project from local connect bind (D-044 A2).

    Ambiguous multi-project resolution fails closed via ConnectError.
    """
    if getattr(args, "vault", None) is None and getattr(args, "command", None) in {
        "overview",
        "state",
        "roadmap",
        "next",
        "changed",
        "decisions",
        "unknown",
        "attention",
        "source-health",
        "brief",
        "context",
        "handoff",
        "capture",
        "inbox",
        "obsidian",
        "review",
    }:
        args.vault = resolve_bound_vault()
    # Single-project commands
    if getattr(args, "command", None) in {"attention", "context"} and getattr(
        args, "project", None
    ) in {None, ""}:
        args.project = resolve_bound_project_id(vault=getattr(args, "vault", None))
    if getattr(args, "command", None) == "handoff" and getattr(
        args, "handoff_command", None
    ) == "create" and getattr(args, "project", None) in {None, ""}:
        args.project = resolve_bound_project_id(vault=getattr(args, "vault", None))
    if getattr(args, "command", None) == "capture" and getattr(
        args, "capture_command", None
    ) == "record" and getattr(args, "project", None) in {None, ""}:
        args.project = resolve_bound_project_id(vault=getattr(args, "vault", None))
    if getattr(args, "command", None) == "review" and getattr(
        args, "review_command", None
    ) == "decide" and getattr(args, "project", None) in {None, ""}:
        args.project = resolve_bound_project_id(vault=getattr(args, "vault", None))
    # Optional multi-project lists: if omitted and bind has one project, scope to it.
    # Ambiguous bind/vault must fail closed (D-047 IV) — never swallow ConnectError
    # into a silent vault-wide scan.
    if getattr(args, "command", None) in {
        "overview",
        "state",
        "roadmap",
        "changed",
        "decisions",
        "unknown",
        "brief",
        "source-health",
        "obsidian",
    }:
        projects = getattr(args, "projects", None)
        if projects is None and getattr(args, "project", None) in {None, ""}:
            only = resolve_bound_project_id(vault=getattr(args, "vault", None))
            if hasattr(args, "projects"):
                args.projects = [only]
            elif hasattr(args, "project"):
                args.project = only


def _raw_capture_content(args: argparse.Namespace, config: AtlasConfig) -> str:
    """Acquire raw content from exactly one source adapter (architecture §6.1)."""
    sources = [
        args.text is not None,
        bool(args.from_stdin),
        bool(args.from_clipboard),
    ]
    if sum(1 for item in sources if item) != 1:
        raise CaptureSourceError(
            "MALFORMED_REQUEST",
            "provide exactly one of --text, --stdin, or --clipboard",
        )
    if args.text is not None:
        return str(args.text)
    if args.from_stdin:
        return read_stdin_text()
    if not config.capture.clipboard.enabled:
        raise CaptureSourceError(
            "CLIPBOARD_DISABLED",
            "clipboard capture is disabled in configuration",
        )
    return read_clipboard_text()


def _raw_capture_routing(config: AtlasConfig) -> RoutingPolicy:
    routing = config.obsidian.routing
    return RoutingPolicy(
        inbox=routing.inbox,
        projects=routing.projects,
        decisions=routing.decisions,
        research=routing.research,
        directives=routing.directives,
    )


def _raw_capture_obsidian_root(args: argparse.Namespace, config: AtlasConfig) -> Path | None:
    """CLI argument wins over configuration (architecture §31)."""
    explicit = getattr(args, "obsidian_vault", None)
    if explicit is not None:
        return Path(explicit)
    if config.obsidian.vault_path:
        return Path(config.obsidian.vault_path)
    return None


def _print_raw_capture_report(report: dict[str, Any]) -> None:
    """Human-readable capture result (architecture §50)."""
    if report.get("duplicate"):
        print("Duplicate capture detected.")
        print(f"  Capture ID : {report.get('capture_id')}")
        print(f"  Hash       : {report.get('content_hash')}")
        print("  No duplicate note created.")
        return
    outputs = report.get("outputs") or []
    print(f"atlas capture text [{report.get('status')}]")
    print(f"  Capture ID : {report.get('capture_id')}")
    print(f"  Project    : {report.get('project_id') or 'UNKNOWN (inbox fallback)'}")
    print(f"  Class      : {report.get('classification')}")
    print(f"  Hash       : {report.get('content_hash')}")
    print(f"  Raw        : persisted ({report.get('raw_path')})")
    if outputs:
        for artifact in outputs:
            print(f"  Obsidian   : written ({artifact.get('relative_path')})")
    else:
        print("  Obsidian   : not written")
    print("  Atlas      : quarantined evidence (NOT Truth Core; not ingested)")
    for warning in report.get("warnings") or []:
        print(f"  warning    : {warning}")
    for error in report.get("errors") or []:
        print(f"  error      : [{error.get('code')}] {error.get('message')}")
        print(f"  retry with : atlas capture retry --capture-id {report.get('capture_id')}")


def _run_raw_capture(args: argparse.Namespace, config: AtlasConfig) -> int:
    """AS-OBSIDIAN-CAPTURE-001 CLI adapter.

    The CLI is an entry adapter only: it acquires content, builds a
    ``CaptureRequest`` and delegates to the shared capture service. No
    persistence, identity or routing logic lives here (architecture §21).
    """
    as_json = bool(getattr(args, "as_json", False))
    try:
        if args.capture_command == "show":
            # Read-only evidence recovery; content goes to stdout, logs to stderr.
            sys.stdout.write(read_raw_content(args.vault, args.capture_id))
            return EXIT_OK
        if args.capture_command == "raw-list":
            rows = list_raw_captures(
                args.vault,
                project_id=args.project,
                limit=args.limit,
            )
            report: dict[str, Any] = {
                "schema_version": 1,
                "package": "AS-OBSIDIAN-CAPTURE-001",
                "status": "ok",
                "count": len(rows),
                "captures": rows,
                "authority": False,
                "truth_boundary": (
                    "CAPTURE != AUTHORITY / PROJECTION != SOURCE / RAW != TRUTH CORE"
                ),
            }
            if as_json:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                print(f"atlas capture raw-list [{len(rows)}]")
                if not rows:
                    print("  UNKNOWN (no raw captures yet)")
                for row in rows:
                    print(
                        f"  - {row.get('capture_id')} [{row.get('classification')}] "
                        f"{row.get('title')} ({row.get('lifecycle_state')})"
                    )
            return EXIT_OK
        if args.capture_command == "retry":
            result = retry_capture(
                args.vault,
                args.capture_id,
                obsidian_root=_raw_capture_obsidian_root(args, config),
                include_content=config.obsidian.include_content,
            )
        else:
            content = _raw_capture_content(args, config)
            request = build_capture_request(
                content=content,
                source_type=args.source_type,
                source_application=(
                    args.source_application or config.capture.default_source_application
                ),
                source_adapter=(
                    "clipboard"
                    if args.from_clipboard
                    else ("stdin" if args.from_stdin else "text")
                ),
                project_reference=args.project,
                title_hint=args.title_hint,
                source_locator=args.source_locator,
                captured_at=args.captured_at,
            )
            result = capture_raw(
                args.vault,
                request,
                classification=args.classification,
                routing=_raw_capture_routing(config),
                obsidian_root=_raw_capture_obsidian_root(args, config),
                render=(not args.no_render) and config.obsidian.enabled,
                include_content=config.obsidian.include_content,
            )
    except (CaptureError, CaptureSourceError) as exc:
        _log.error("capture failed: %s", exc)
        body = {
            "status": "error",
            "error": exc.code,
            "message": str(exc),
            "package": "AS-OBSIDIAN-CAPTURE-001",
        }
        if as_json:
            print(json.dumps(body, indent=2, sort_keys=True))
        else:
            print(f"atlas capture {args.capture_command} error [{exc.code}]: {exc}")
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        _log.error("capture failed: %s", exc)
        return EXIT_ERROR

    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_raw_capture_report(result)
    # Evidence is durable either way; a partial result still fails loud so a
    # scripted caller notices the projection gap and can retry it.
    return EXIT_ERROR if result.get("status") == "partial" else EXIT_OK


def _load_conversation_envelope(args: argparse.Namespace) -> dict[str, Any]:
    """Load a structured conversation envelope from file, stdin, or CLI items."""
    sources = [
        bool(getattr(args, "input_path", None)),
        bool(getattr(args, "from_stdin", False)),
        bool(getattr(args, "cli_items", None)),
    ]
    if sum(bool(item) for item in sources) != 1:
        raise ConversationCaptureError(
            "MALFORMED_SCHEMA",
            "provide exactly one of --input, --stdin, or --item",
        )
    if args.input_path is not None:
        try:
            raw = args.input_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConversationCaptureError("MALFORMED_SCHEMA", "input is not valid JSON") from exc
    elif args.from_stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ConversationCaptureError("MALFORMED_SCHEMA", "stdin is not valid JSON") from exc
    else:
        if not args.summary or not args.provider:
            raise ConversationCaptureError(
                "MALFORMED_SCHEMA",
                "CLI item capture requires --provider and --summary",
            )
        return envelope_from_cli_items(
            summary=args.summary,
            provider=args.provider,
            items=list(args.cli_items),
            project_id=args.project,
            conversation_id=args.conversation_id or "",
        )
    if not isinstance(payload, dict):
        raise ConversationCaptureError("MALFORMED_SCHEMA", "envelope must be a JSON object")
    nested = payload.get("envelope")
    if isinstance(nested, dict):
        return nested
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas",
        description="Project Atlas - source-backed, offline-first project knowledge compiler.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a TOML configuration file (default: probe atlas.toml / pyproject.toml).",
    )
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR, CRITICAL.")
    parser.add_argument("--log-format", choices=["console", "json"], default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("version", help="Print the Project Atlas version.")

    init_parser = subparsers.add_parser("init", help="Create a vault scaffold (FR-001).")
    init_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Vault output directory. Must not exist as a file or non-empty directory.",
    )

    discover_parser = subparsers.add_parser(
        "discover",
        help=(
            "Discover sources (FR-002) or scan a bounded knowledge estate (D-049). "
            "Legacy: --source + --output. Estate: --root / review / connect."
        ),
        epilog=(
            "Estate mode examples:\n"
            "  atlas discover --root /authorized/estate --vault /vault\n"
            "  atlas discover --json --root /authorized/estate\n"
            "  atlas discover review --root /authorized/estate --vault /vault\n"
            "When --root is omitted, estate mode scans the current working "
            "directory (filesystem root and home are refused). Traversal is "
            f"bounded to max_depth={DEFAULT_MAX_DEPTH}; the bound is not a CLI "
            "flag. A scan that stops at the bound reports SCAN INCOMPLETE "
            "instead of claiming exhaustive coverage.\n"
            "Root policy:\n"
            "  Normal directories need no override (--root-mode "
            f"{ROOT_MODE_BOUNDED_DIRECTORY}, default).\n"
            "  Windows volume roots such as D:\\ are refused by default.\n"
            "  A non-system Windows volume root requires explicit "
            f"--root-mode {ROOT_MODE_OWNER_AUTHORIZED_VOLUME}.\n"
            "  The Windows system volume (typically C:\\) remains refused.\n"
            "  Linux/macOS filesystem root / remains refused.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    discover_parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="FR-002 source root for manifest discovery (requires --output).",
    )
    discover_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="FR-002 manifest output path, or estate report path with --root.",
    )
    discover_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Authorized root for knowledge estate discovery (D-049). "
            "When omitted, estate mode uses the current working directory. "
            f"Traversal stops at max_depth={DEFAULT_MAX_DEPTH} (not a CLI flag). "
            "Never home. Filesystem roots are refused unless --root-mode "
            f"{ROOT_MODE_OWNER_AUTHORIZED_VOLUME} is set for a non-system "
            "Windows volume (D:\\). System volume C:\\ stays refused."
        ),
    )
    discover_parser.add_argument(
        "--root-mode",
        choices=(ROOT_MODE_BOUNDED_DIRECTORY, ROOT_MODE_OWNER_AUTHORIZED_VOLUME),
        default=ROOT_MODE_BOUNDED_DIRECTORY,
        help=(
            "Authorized-root policy. Default bounded-directory refuses "
            "filesystem roots and home. owner-authorized-volume explicitly "
            "authorizes one non-system Windows drive-volume root (for example "
            "D:\\). It is not --force/--unsafe and does not authorize C:\\, "
            "home, UNC, or /."
        ),
    )
    discover_parser.add_argument(
        "--projects",
        action="store_true",
        help="Estate mode: only project candidates.",
    )
    discover_parser.add_argument(
        "--knowledge",
        action="store_true",
        help="Estate mode: only knowledge / Obsidian candidates.",
    )
    discover_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Optional vault for matching existing Atlas project identities.",
    )
    discover_parser.add_argument(
        "--json",
        action="store_true",
        dest="discover_json",
        help="Emit estate discovery JSON on stdout.",
    )
    discover_sub = discover_parser.add_subparsers(
        dest="discover_command", required=False
    )
    discover_review = discover_sub.add_parser(
        "review",
        help="List estate discovery candidates that require human review (D-049).",
    )
    discover_review.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Authorized root (default: cwd if safe).",
    )
    discover_review.add_argument("--vault", type=Path, default=None)
    discover_review.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Reuse an existing estate-discovery-report.json instead of rescanning.",
    )
    discover_review.add_argument(
        "--json",
        action="store_true",
        dest="discover_json",
    )
    discover_connect = discover_sub.add_parser(
        "connect",
        help=(
            "Connect an accepted project candidate (explicit; discovery alone "
            "never ingests)."
        ),
    )
    discover_connect.add_argument(
        "--candidate",
        required=True,
        help="candidate_id from atlas discover / discover review.",
    )
    discover_connect.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Authorized root used to (re)build the discovery report.",
    )
    discover_connect.add_argument("--vault", type=Path, default=None)
    discover_connect.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Existing estate-discovery-report.json.",
    )
    discover_connect.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan connect without writing.",
    )
    discover_connect.add_argument(
        "--json",
        action="store_true",
        dest="discover_json",
    )

    ingest_parser = subparsers.add_parser(
        "ingest", help="Ingest a source manifest into an OKF Vault (FR-005-FR-008)."
    )
    ingest_parser.add_argument("--manifest", type=Path, required=True)
    ingest_parser.add_argument("--vault", type=Path, required=True)
    ingest_parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help=(
            "Authorized source root (CODEX-SEC-001). Must resolve to the same "
            "directory recorded in the manifest; the manifest cannot self-authorize."
        ),
    )

    indexes_parser = subparsers.add_parser(
        "build-indexes", help="Build deterministic lexical indexes under generated/ (FR-010)."
    )
    indexes_parser.add_argument("--vault", type=Path, required=True)

    portfolio_parser = subparsers.add_parser(
        "build-portfolio",
        help="Build derived portfolio intelligence reports under generated/portfolio/ (AS-MVP-001)",
    )
    portfolio_parser.add_argument("--vault", type=Path, required=True)

    migrate_parser = subparsers.add_parser(
        "migrate-v2",
        help="Migrate claims to Identity v2 and generate an alias map (AS-CORE-003)",
    )
    migrate_parser.add_argument("--vault", type=Path, required=True)
    migrate_parser.add_argument("--project", type=str, required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate Vault structure, provenance links and safety (FR-012)."
    )
    validate_parser.add_argument("--vault", type=Path, required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Diagnose the Atlas environment and, optionally, a Vault (PROD-DOCTOR-001).",
    )
    doctor_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Optional Vault directory to include in the checks.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit a machine-readable JSON report to stdout (sorted keys).",
    )

    connect_parser = subparsers.add_parser(
        "connect",
        help=(
            "Bind a project root to a Vault and run Core compile "
            "(AS-CODER-ALPHA-CONNECT-001; never claims AUTHENTIC_PILOT)."
        ),
    )
    connect_parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path("."),
        help="Project root to connect (default: current directory).",
    )
    connect_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=(
            "Vault directory (default: .atlas/connect.json bind, else "
            "<project>/.atlas-vault)."
        ),
    )
    connect_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the connect plan without writing anything.",
    )
    connect_parser.add_argument(
        "--portfolio",
        action="store_true",
        help="Also run build-portfolio after indexes (optional).",
    )
    connect_parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip atlas validate at the end of connect (not recommended).",
    )
    connect_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the connect receipt JSON to stdout (sorted keys).",
    )

    overview_parser = subparsers.add_parser(
        "overview",
        help=(
            "Materialize Project Overview derived answer lenses from Core "
            "(AS-CODER-ALPHA-OVERVIEW-001; lens!=authority)."
        ),
    )
    overview_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault directory (default: .atlas/connect.json bind / .atlas-vault).",
    )
    overview_parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        default=None,
        help="Limit to one project id (repeatable). Default: all projects/.",
    )
    overview_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the overview receipt JSON to stdout (sorted keys).",
    )

    state_parser = subparsers.add_parser(
        "state",
        help=(
            "Materialize Current State derived answer lenses from Core "
            "(AS-CODER-ALPHA-STATE-001; lens!=authority)."
        ),
    )
    state_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault directory (default: .atlas/connect.json bind / .atlas-vault).",
    )
    state_parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        default=None,
        help="Limit to one project id (repeatable). Default: all projects/.",
    )
    state_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the state receipt JSON to stdout (sorted keys).",
    )

    roadmap_parser = subparsers.add_parser(
        "roadmap",
        help=(
            "Materialize Living Project Roadmap V1 derived lenses "
            "(AS-PROJECT-ROADMAP-001; ROADMAP!=canonical)."
        ),
    )
    roadmap_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault directory (default: .atlas/connect.json bind / .atlas-vault).",
    )
    roadmap_parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        default=None,
        help="Limit to one project id (repeatable). Default: all projects/.",
    )
    roadmap_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the roadmap receipt JSON to stdout (sorted keys).",
    )
    roadmap_parser.add_argument(
        "--read-only",
        action="store_true",
        dest="read_only",
        help="Derive and print without writing generated/answers/ (no vault mutation).",
    )

    next_parser = subparsers.add_parser(
        "next",
        help=(
            "Materialize What Next derived lens from attention/roadmap/"
            "unknown/source-health (AS-CODER-ALPHA-NEXT-001; NEXT!=command)."
        ),
    )
    next_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault directory (default: .atlas/connect.json bind / .atlas-vault).",
    )
    next_parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        default=None,
        help="Limit to one project id (repeatable). Default: all projects/.",
    )
    next_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the next receipt JSON to stdout (sorted keys).",
    )
    next_parser.add_argument(
        "--read-only",
        action="store_true",
        dest="read_only",
        help="Derive and print without writing generated/answers/ (no vault mutation).",
    )

    changed_parser = subparsers.add_parser(
        "changed",
        help=(
            "Materialize What Changed derived answer lenses from last-connect "
            "inventory (AS-CODER-ALPHA-CHANGED-001; lens!=authority)."
        ),
    )
    changed_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault directory (default: .atlas/connect.json bind / .atlas-vault).",
    )
    changed_parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        default=None,
        help="Limit to one project id (repeatable). Default: all projects/.",
    )
    changed_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the changed receipt JSON to stdout (sorted keys).",
    )

    decisions_parser = subparsers.add_parser(
        "decisions",
        help=(
            "Materialize Decision memory derived lenses "
            "(AS-CODER-ALPHA-DECISIONS-001; lens!=authority)."
        ),
    )
    decisions_parser.add_argument("--vault", type=Path, default=None)
    decisions_parser.add_argument(
        "--project", action="append", dest="projects", default=None
    )
    decisions_parser.add_argument(
        "--json", action="store_true", dest="as_json"
    )

    unknown_parser = subparsers.add_parser(
        "unknown",
        help=(
            "Materialize Unknown/conflict honesty lenses "
            "(AS-CODER-ALPHA-UNKNOWN-001; lens!=authority)."
        ),
    )
    unknown_parser.add_argument("--vault", type=Path, default=None)
    unknown_parser.add_argument(
        "--project", action="append", dest="projects", default=None
    )
    unknown_parser.add_argument("--json", action="store_true", dest="as_json")

    attention_parser = subparsers.add_parser(
        "attention",
        help=(
            "Classify unresolved Truth attention without confidence theatre "
            "(AS-CODER-ALPHA-ATTENTION-001; lens!=authority)."
        ),
    )
    attention_parser.add_argument("--vault", type=Path, default=None)
    attention_parser.add_argument("--project", default=None)
    attention_parser.add_argument("--json", action="store_true", dest="as_json")

    source_health_parser = subparsers.add_parser(
        "source-health",
        help=(
            "Explain excluded/quarantined/failed sources "
            "(AS-CODER-ALPHA-SOURCE-HEALTH-001; no secret echo)."
        ),
    )
    source_health_parser.add_argument("--vault", type=Path, default=None)
    source_health_parser.add_argument("--project", default=None)
    source_health_parser.add_argument("--json", action="store_true", dest="as_json")

    brief_parser = subparsers.add_parser(
        "brief",
        help=(
            "Emit a unified Coder Alpha project brief from derived lenses "
            "(AS-CODER-ALPHA-BRIEF-001; UNKNOWN stays UNKNOWN)."
        ),
    )
    brief_parser.add_argument("--vault", type=Path, default=None)
    brief_parser.add_argument(
        "--project", action="append", dest="projects", default=None
    )
    brief_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Do not rematerialize underlying lenses before briefing.",
    )
    brief_parser.add_argument("--json", action="store_true", dest="as_json")

    context_parser = subparsers.add_parser(
        "context",
        help=(
            "Export paste-ready agent context from the project brief "
            "(AS-CODER-ALPHA-CONTEXT-001; lens!=authority)."
        ),
    )
    context_parser.add_argument("--vault", type=Path, default=None)
    context_parser.add_argument("--project", default=None)
    context_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Do not refresh the underlying brief/lenses.",
    )
    context_parser.add_argument("--json", action="store_true", dest="as_json")

    handoff_parser = subparsers.add_parser(
        "handoff",
        help=(
            "Create or resume an agent handoff pack "
            "(AS-CODER-ALPHA-HANDOFF-001)."
        ),
    )
    handoff_sub = handoff_parser.add_subparsers(dest="handoff_command", required=True)
    handoff_create = handoff_sub.add_parser(
        "create", help="Create a durable handoff pack for another agent."
    )
    handoff_create.add_argument("--vault", type=Path, default=None)
    handoff_create.add_argument("--project", default=None)
    handoff_create.add_argument("--note", default=None)
    handoff_create.add_argument("--no-refresh", action="store_true")
    handoff_create.add_argument(
        "--no-capture",
        action="store_true",
        help="Skip semi-auto session capture on handoff create.",
    )
    handoff_create.add_argument("--json", action="store_true", dest="as_json")
    handoff_resume = handoff_sub.add_parser(
        "resume", help="Resume from latest or named handoff pack."
    )
    handoff_resume.add_argument("--vault", type=Path, default=None)
    handoff_resume.add_argument("--handoff-id", default=None)
    handoff_resume.add_argument("--json", action="store_true", dest="as_json")

    next_status_parser = subparsers.add_parser(
        "next-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing What Next answer artifacts "
            "(AS-CODER-ALPHA-NEXT-READ-001; never writes; "
            "NEXT != AUTHORITY; NEXT != COMMAND)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-next-*.json "
            "artifacts. Never writes. Never materializes. "
            "NEXT != AUTHORITY. NEXT != COMMAND."
        ),
        epilog=(
            "Examples:\n"
            "  atlas next-status --vault /path/to/vault\n"
            "  atlas next-status --vault /path/to/vault --json"
        ),
    )
    next_status_parser.add_argument("--vault", type=Path, required=True)
    next_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the What Next REPORT READ JSON to stdout.",
    )

    changed_status_parser = subparsers.add_parser(
        "changed-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing What Changed answer artifacts "
            "(AS-CODER-ALPHA-CHANGED-READ-001; never writes; "
            "CHANGED != AUTHORITY; STALE != CURRENT)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-changed-*.json "
            "artifacts. Never writes. Never materializes. "
            "CHANGED != AUTHORITY. CHANGED != LIVE ESTATE. STALE != CURRENT."
        ),
        epilog=(
            "Examples:\n"
            "  atlas changed-status --vault /path/to/vault\n"
            "  atlas changed-status --vault /path/to/vault --json"
        ),
    )
    changed_status_parser.add_argument("--vault", type=Path, required=True)
    changed_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the What Changed REPORT READ JSON to stdout.",
    )

    overview_status_parser = subparsers.add_parser(
        "overview-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Project Overview answer artifacts "
            "(AS-CODER-ALPHA-OVERVIEW-READ-001; never writes; "
            "OVERVIEW != AUTHORITY; UI != CANONICAL)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-overview-*.json "
            "artifacts. Never writes. Never materializes. "
            "OVERVIEW != AUTHORITY. UI != CANONICAL."
        ),
        epilog=(
            "Examples:\n"
            "  atlas overview-status --vault /path/to/vault\n"
            "  atlas overview-status --vault /path/to/vault --json"
        ),
    )
    overview_status_parser.add_argument("--vault", type=Path, required=True)
    overview_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the Overview REPORT READ JSON to stdout.",
    )

    decisions_status_parser = subparsers.add_parser(
        "decisions-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Decision memory answer artifacts "
            "(AS-CODER-ALPHA-DECISIONS-READ-001; never writes; "
            "DECISIONS != AUTHORITY)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-decisions-*.json "
            "artifacts. Never writes. Never materializes. "
            "DECISIONS != AUTHORITY. MODEL PARAPHRASE != OWNER DECISION."
        ),
        epilog=(
            "Examples:\n"
            "  atlas decisions-status --vault /path/to/vault\n"
            "  atlas decisions-status --vault /path/to/vault --json"
        ),
    )
    decisions_status_parser.add_argument("--vault", type=Path, required=True)
    decisions_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the Decisions REPORT READ JSON to stdout.",
    )

    unknown_status_parser = subparsers.add_parser(
        "unknown-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Unknown/conflict answer artifacts "
            "(AS-CODER-ALPHA-UNKNOWN-READ-001; never writes; "
            "UNKNOWN != AUTHORITY)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-unknown-*.json "
            "artifacts. Never writes. Never materializes. "
            "UNKNOWN != AUTHORITY. UNKNOWN != RESOLVED."
        ),
        epilog=(
            "Examples:\n"
            "  atlas unknown-status --vault /path/to/vault\n"
            "  atlas unknown-status --vault /path/to/vault --json"
        ),
    )
    unknown_status_parser.add_argument("--vault", type=Path, required=True)
    unknown_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the Unknown REPORT READ JSON to stdout.",
    )

    state_status_parser = subparsers.add_parser(
        "state-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Current State answer artifacts "
            "(AS-CODER-ALPHA-STATE-READ-001; never writes; "
            "STATE != AUTHORITY)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-state-*.json "
            "artifacts. Never writes. Never materializes. "
            "STATE != AUTHORITY. STALE != CURRENT."
        ),
        epilog=(
            "Examples:\n"
            "  atlas state-status --vault /path/to/vault\n"
            "  atlas state-status --vault /path/to/vault --json"
        ),
    )
    state_status_parser.add_argument("--vault", type=Path, required=True)
    state_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the State REPORT READ JSON to stdout.",
    )

    architecture_status_parser = subparsers.add_parser(
        "architecture-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Architecture answer artifacts "
            "(AS-CODER-ALPHA-ARCHITECTURE-READ-001; never writes; "
            "ARCHITECTURE != AUTHORITY)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-architecture-*.json "
            "artifacts. Never writes. Never materializes. "
            "ARCHITECTURE != AUTHORITY. README != ARCHITECTURE AUTHORITY."
        ),
        epilog=(
            "Examples:\n"
            "  atlas architecture-status --vault /path/to/vault\n"
            "  atlas architecture-status --vault /path/to/vault --json"
        ),
    )
    architecture_status_parser.add_argument("--vault", type=Path, required=True)
    architecture_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the Architecture REPORT READ JSON to stdout.",
    )

    roadmap_status_parser = subparsers.add_parser(
        "roadmap-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Roadmap answer artifacts "
            "(AS-CODER-ALPHA-ROADMAP-READ-001; never writes; "
            "ROADMAP != AUTHORITY)."
        ),
        description=(
            "Read-only wrap of existing generated/answers/ans-roadmap-*.json "
            "artifacts. Never writes. Never materializes. Never derives. "
            "ROADMAP != AUTHORITY. ROADMAP != CANONICAL_TRUTH."
        ),
        epilog=(
            "Examples:\n"
            "  atlas roadmap-status --vault /path/to/vault\n"
            "  atlas roadmap-status --vault /path/to/vault --json"
        ),
    )
    roadmap_status_parser.add_argument("--vault", type=Path, required=True)
    roadmap_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the Roadmap answers REPORT READ JSON to stdout.",
    )

    portfolio_status_parser = subparsers.add_parser(
        "portfolio-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing Layer C portfolio artifacts "
            "(AS-CODER-ALPHA-PORTFOLIO-READ-001; never writes; "
            "PORTFOLIO != AUTHORITY; LAYER C != TRUTH CORE)."
        ),
        description=(
            "Read-only wrap of existing generated/portfolio/*.json "
            "artifacts. Never writes. Never materializes. "
            "PORTFOLIO != AUTHORITY. LAYER C != TRUTH CORE."
        ),
        epilog=(
            "Examples:\n"
            "  atlas portfolio-status --vault /path/to/vault\n"
            "  atlas portfolio-status --vault /path/to/vault --json"
        ),
    )
    portfolio_status_parser.add_argument("--vault", type=Path, required=True)
    portfolio_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the portfolio REPORT READ JSON to stdout.",
    )

    bitemporal_status_parser = subparsers.add_parser(
        "bitemporal-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing validity-catalog artifacts "
            "(AS-CODER-ALPHA-BITEMPORAL-READ-001; never writes; "
            "CATALOG != AUTHORITY; GRAPH != AUTHORITY)."
        ),
        description=(
            "Read-only wrap of existing generated/ops/bitemporal/"
            "*-validity-catalog.json artifacts. Never writes. "
            "Never materializes. CATALOG != AUTHORITY. GRAPH != AUTHORITY."
        ),
        epilog=(
            "Examples:\n"
            "  atlas bitemporal-status --vault /path/to/vault\n"
            "  atlas bitemporal-status --vault /path/to/vault --json"
        ),
    )
    bitemporal_status_parser.add_argument("--vault", type=Path, required=True)
    bitemporal_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the bitemporal REPORT READ JSON to stdout.",
    )

    index_status_parser = subparsers.add_parser(
        "index-status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help=(
            "Read existing lexical index artifacts "
            "(AS-CODER-ALPHA-INDEX-STATUS-001; never writes; "
            "INDEX_STATUS != AUTHORITY; PRESENCE != VALIDATE)."
        ),
        description=(
            "Read-only wrap of existing generated/indexes/*.json "
            "artifacts. Never writes. Never materializes. "
            "INDEX_STATUS != AUTHORITY. PRESENCE != VALIDATE."
        ),
        epilog=(
            "Examples:\n"
            "  atlas index-status --vault /path/to/vault\n"
            "  atlas index-status --vault /path/to/vault --json"
        ),
    )
    index_status_parser.add_argument("--vault", type=Path, required=True)
    index_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the index-status REPORT READ JSON to stdout.",
    )

    capture_parser = subparsers.add_parser(
        "capture",
        help=(
            "Record session or conversation captures "
            "(CAPTURE-001 ops receipt; CAPTURE-002 quarantined evidence; "
            "neither is Truth Core authority)."
        ),
    )
    capture_sub = capture_parser.add_subparsers(dest="capture_command", required=True)
    capture_record = capture_sub.add_parser(
        "record", help="Record an explicit meaningful session capture."
    )
    capture_record.add_argument("--vault", type=Path, default=None)
    capture_record.add_argument("--project", default=None)
    capture_record.add_argument("--summary", required=True)
    capture_record.add_argument(
        "--kind",
        default="milestone",
        choices=sorted(["milestone", "decision", "blocker", "note", "handoff"]),
    )
    capture_record.add_argument("--decision", action="append", default=[])
    capture_record.add_argument("--change", action="append", default=[])
    capture_record.add_argument("--next", action="append", default=[], dest="next_work")
    capture_record.add_argument("--unknown", action="append", default=[], dest="unknowns")
    capture_record.add_argument("--json", action="store_true", dest="as_json")
    capture_list = capture_sub.add_parser(
        "list",
        help="List session captures (deterministic capture_id order; not time-based).",
    )
    capture_list.add_argument("--vault", type=Path, default=None)
    capture_list.add_argument("--project", default=None)
    capture_list.add_argument("--limit", type=int, default=20)
    capture_list.add_argument("--json", action="store_true", dest="as_json")
    capture_conversation = capture_sub.add_parser(
        "conversation",
        help=(
            "Submit a provider-neutral atlas.conversation-capture.v1 envelope "
            "into Knowledge Inbox quarantine (CAPTURE != TRUTH CORE)."
        ),
        epilog=(
            "Examples:\n"
            "  atlas capture conversation --vault <vault> --input capture.json --json\n"
            "  cat capture.json | atlas capture conversation --vault <vault> --stdin --json\n"
            "  atlas capture conversation --vault <vault> --project harbor-api "
            "--provider cursor --summary \"Session note\" "
            "--item observation=\"Postgres 16 remains unresolved\" --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    capture_conversation.add_argument("--vault", type=Path, default=None)
    capture_conversation.add_argument("--project", default=None)
    capture_conversation.add_argument("--input", type=Path, default=None, dest="input_path")
    capture_conversation.add_argument("--stdin", action="store_true", dest="from_stdin")
    capture_conversation.add_argument("--provider", default=None)
    capture_conversation.add_argument("--summary", default=None)
    capture_conversation.add_argument("--conversation-id", default="", dest="conversation_id")
    capture_conversation.add_argument(
        "--item",
        action="append",
        default=[],
        dest="cli_items",
        help="Compact item as item_type=text (repeatable).",
    )
    capture_conversation.add_argument("--json", action="store_true", dest="as_json")
    capture_review = capture_sub.add_parser(
        "review",
        help=(
            "Set conversation-capture review_state (captured|reviewed|rejected). "
            "REVIEWED != Truth Core promotion."
        ),
    )
    capture_review.add_argument("--vault", type=Path, default=None)
    capture_review.add_argument("--capture-id", required=True, dest="capture_id")
    capture_review.add_argument(
        "--state",
        required=True,
        choices=sorted(["captured", "reviewed", "rejected"]),
        dest="review_state",
    )
    capture_review.add_argument("--json", action="store_true", dest="as_json")

    # AS-OBSIDIAN-CAPTURE-001 — raw capture evidence + Obsidian projection.
    # PRESERVE FIRST: raw evidence is durable before any projection runs.
    capture_text = capture_sub.add_parser(
        "text",
        help=(
            "Capture raw text/conversation evidence and project it to Obsidian "
            "(CAPTURE != AUTHORITY; raw evidence is preserved verbatim)."
        ),
        epilog=(
            "Examples:\n"
            "  atlas capture text --vault <vault> --text 'Important information'\n"
            "  pbpaste | atlas capture text --vault <vault> --stdin\n"
            "  atlas capture text --vault <vault> --clipboard "
            "--source-type conversation --application chatgpt --project harbor-api\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    capture_text.add_argument("--vault", type=Path, default=None)
    capture_text.add_argument("--project", default=None)
    capture_text.add_argument("--text", default=None)
    capture_text.add_argument("--stdin", action="store_true", dest="from_stdin")
    capture_text.add_argument("--clipboard", action="store_true", dest="from_clipboard")
    capture_text.add_argument(
        "--source-type",
        default="text",
        choices=sorted(SOURCE_TYPES),
        dest="source_type",
    )
    capture_text.add_argument(
        "--application",
        default=None,
        dest="source_application",
        help="Originating application token (chatgpt, claude, clipboard, ...).",
    )
    capture_text.add_argument(
        "--classification",
        default=None,
        choices=sorted(CLASSIFICATIONS),
        help="Override the deterministic classification used for routing.",
    )
    capture_text.add_argument("--title", default=None, dest="title_hint")
    capture_text.add_argument("--locator", default="", dest="source_locator")
    capture_text.add_argument(
        "--captured-at",
        default=None,
        dest="captured_at",
        help=(
            "Operator-supplied ISO-8601 capture time. Never generated by Atlas "
            "(NFR-001 determinism); omitted means UNKNOWN, not 'now'."
        ),
    )
    capture_text.add_argument(
        "--obsidian-vault",
        type=Path,
        default=None,
        dest="obsidian_vault",
        help=(
            "External Obsidian vault root for the projection. Default: inside "
            "--vault at generated/obsidian/captures/."
        ),
    )
    capture_text.add_argument(
        "--no-render",
        action="store_true",
        dest="no_render",
        help="Persist raw evidence only; skip the Obsidian projection.",
    )
    capture_text.add_argument("--json", action="store_true", dest="as_json")

    capture_raw_list = capture_sub.add_parser(
        "raw-list",
        help="List raw captures (deterministic capture_id order; not time-based).",
    )
    capture_raw_list.add_argument("--vault", type=Path, default=None)
    capture_raw_list.add_argument("--project", default=None)
    capture_raw_list.add_argument("--limit", type=int, default=20)
    capture_raw_list.add_argument("--json", action="store_true", dest="as_json")

    capture_retry = capture_sub.add_parser(
        "retry",
        help="Re-run the Obsidian projection for a persisted raw capture.",
    )
    capture_retry.add_argument("--vault", type=Path, default=None)
    capture_retry.add_argument("--capture-id", required=True, dest="capture_id")
    capture_retry.add_argument(
        "--obsidian-vault", type=Path, default=None, dest="obsidian_vault"
    )
    capture_retry.add_argument("--json", action="store_true", dest="as_json")

    capture_show = capture_sub.add_parser(
        "show",
        help="Print the verbatim preserved evidence for a raw capture (read-only).",
    )
    capture_show.add_argument("--vault", type=Path, default=None)
    capture_show.add_argument("--capture-id", required=True, dest="capture_id")

    inbox_parser = subparsers.add_parser(
        "inbox",
        help=(
            "Read Knowledge Inbox observations "
            "(INBOX != AUTHORITY; list is read-only and project-scoped)."
        ),
    )
    inbox_sub = inbox_parser.add_subparsers(dest="inbox_command", required=True)
    inbox_list = inbox_sub.add_parser(
        "list",
        help=(
            "List project-scoped inbox items in deterministic receipt_id order. "
            "Requires --project or a single-project bind. No implicit portfolio-all."
        ),
    )
    inbox_list.add_argument("--vault", type=Path, default=None)
    inbox_list.add_argument("--project", default=None)
    inbox_list.add_argument(
        "--status",
        default=None,
        choices=sorted(["quarantined", "accepted-review", "rejected"]),
    )
    inbox_list.add_argument("--limit", type=int, default=20)
    inbox_list.add_argument("--json", action="store_true", dest="as_json")

    obsidian_parser = subparsers.add_parser(
        "obsidian",
        help=(
            "Materialize living Obsidian projections from Core "
            "(AS-CODER-ALPHA-OBSIDIAN-001; derived!=plugin!=authority)."
        ),
    )
    obsidian_sub = obsidian_parser.add_subparsers(dest="obsidian_command", required=True)
    obsidian_project = obsidian_sub.add_parser(
        "project",
        help="Write living project Markdown under generated/obsidian/projects/.",
    )
    obsidian_project.add_argument("--vault", type=Path, default=None)
    obsidian_project.add_argument("--project", default=None)
    obsidian_project.add_argument(
        "--no-refresh",
        action="store_true",
        help="Do not refresh underlying brief/lenses before projecting.",
    )
    obsidian_project.add_argument("--json", action="store_true", dest="as_json")

    review_parser = subparsers.add_parser(
        "review",
        help=(
            "Human review decisions into Truth Core "
            "(AS-CODER-ALPHA-HUMAN-LOOP-001; fail-closed)."
        ),
    )
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)
    review_decide = review_sub.add_parser(
        "decide", help="Accept or reject one pending review entry."
    )
    review_decide.add_argument("--vault", type=Path, default=None)
    review_decide.add_argument("--project", default=None)
    review_decide.add_argument("--review-id", required=True)
    review_decide.add_argument(
        "--decision",
        required=True,
        choices=["accept", "reject"],
    )
    review_decide.add_argument("--reason", required=True)
    review_decide.add_argument(
        "--winner-claim-id",
        default=None,
        help="Required for conflict accept; no silent winners.",
    )
    review_decide.add_argument("--json", action="store_true", dest="as_json")

    accept_graph_parser = subparsers.add_parser(
        "accept-graph",
        help=(
            "Accept inventory-backed Graphify artifacts as derived-only "
            "(AS-GRAPH-001; no authority/claims/relationship store writes)."
        ),
    )
    accept_graph_parser.add_argument("--source", type=Path, required=True)
    accept_graph_parser.add_argument("--manifest", type=Path, required=True)
    accept_graph_parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail closed on first rejection (default: true).",
    )

    resolve_graph_parser = subparsers.add_parser(
        "resolve-graph",
        help=(
            "Resolve accepted Graphify nodes to project-local Atlas entity ids "
            "(AS-GRAPH-002; derived-only; no authority/claims/relationship writes)."
        ),
    )
    resolve_graph_parser.add_argument("--source", type=Path, required=True)
    resolve_graph_parser.add_argument("--manifest", type=Path, required=True)
    resolve_graph_parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="Optional project-local mapping table JSON (deterministic; no remote fetch).",
    )
    resolve_graph_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=(
            "Optional vault root for derived emits under generated/graph/resolved/ "
            "and generated/graph/quarantine-candidates/ only."
        ),
    )
    resolve_graph_parser.add_argument(
        "--write",
        action="store_true",
        help="Write optional derived resolution outputs (requires --vault).",
    )
    resolve_graph_parser.add_argument(
        "--project-uuid",
        type=str,
        default=None,
        help=(
            "Optional local project UUID binding for durable project_uuid hits "
            "(ADV-G2-007; unbound/foreign UUID fails closed)."
        ),
    )
    resolve_graph_parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail closed on first rejection (default: true).",
    )

    store_graph_parser = subparsers.add_parser(
        "store-graph",
        help=(
            "Normalize accepted Graphify edges into derived relationship records "
            "(AS-GRAPH-003; derived-only; no authority/claims/CP relationships writes)."
        ),
    )
    store_graph_parser.add_argument("--source", type=Path, required=True)
    store_graph_parser.add_argument("--manifest", type=Path, required=True)
    store_graph_parser.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="Optional project-local mapping table JSON (deterministic; no remote fetch).",
    )
    store_graph_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=(
            "Optional vault root for derived emits under generated/graph/relationships/ "
            "and generated/graph/relationship-quarantine/ only."
        ),
    )
    store_graph_parser.add_argument(
        "--write",
        action="store_true",
        help="Write optional derived relationship outputs (requires --vault).",
    )
    store_graph_parser.add_argument(
        "--project-uuid",
        type=str,
        default=None,
        help=(
            "Optional local project UUID binding for durable project_uuid hits "
            "(passed through AS-GRAPH-002 resolve)."
        ),
    )
    store_graph_parser.add_argument(
        "--max-edges",
        type=int,
        default=None,
        help="Optional edge capacity gate (fail closed when exceeded; ADV-G3-030).",
    )
    store_graph_parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail closed on first rejection (default: true).",
    )

    query_parser = subparsers.add_parser(
        "query",
        help=(
            "Read-only knowledge query over temporal and authoritative state "
            "(AS-CORE-007 point / AS-CORE-008 multi-field)."
        ),
    )
    query_parser.add_argument("--vault", type=Path, required=True)
    query_parser.add_argument("--project", type=str, required=True)
    query_parser.add_argument("--subject", type=str, default=None)
    query_parser.add_argument(
        "--field",
        action="append",
        default=None,
        dest="field_args",
        help=(
            "Field name. Repeat for AS-CORE-008 multi-field composition; "
            "a single --field keeps the AS-CORE-007 point path."
        ),
    )
    query_parser.add_argument(
        "--fields",
        type=str,
        default=None,
        dest="fields_csv",
        help=(
            "Comma-separated field list for AS-CORE-008 multi-field query "
            "(mutually exclusive with --field)."
        ),
    )
    query_parser.add_argument(
        "--kind",
        choices=["authoritative", "temporal", "explain"],
        default="authoritative",
        help="Query kind (distinct from AS-RET-001 retrieval kinds).",
    )
    query_parser.add_argument(
        "--list",
        action="store_true",
        help=(
            "List kind-scoped records for the project "
            "(--kind authoritative|temporal; AS-QUERY-001)."
        ),
    )
    query_parser.add_argument(
        "--format",
        choices=["json"],
        default="json",
        help="Output format (json).",
    )

    init_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be created without writing anything.",
    )
    init_parser.add_argument(
        "--vault-id",
        default=None,
        help=(
            "Logical Vault ID stamped into .atlas/vault.json during init "
            "(AS-DEMO-2.2-RECOVERY-ID-001). Defaults to atlas-main. "
            "Existing matching identity is preserved; mismatched identity fails closed."
        ),
    )

    # AS-OBS-001 / AS-OBS-002 / AS-OBS-003 - operational observability (ops plane only).
    ops_parser = subparsers.add_parser(
        "ops",
        help=(
            "Operational observability commands "
            "(AS-OBS-001 health / AS-OBS-002 events / AS-OBS-003 report; "
            "health != authority)."
        ),
    )
    ops_sub = ops_parser.add_subparsers(dest="ops_command", required=True)
    health_parser = ops_sub.add_parser(
        "health",
        help=(
            "Emit a regenerable operational health snapshot under "
            "generated/ops/ (AS-OBS-001; consume-only collectors)."
        ),
    )
    health_parser.add_argument("--vault", type=Path, required=True)
    health_parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Optional stable project UUID filter (name-only forbidden by contract).",
    )
    health_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the snapshot JSON to stdout (always schema-bound).",
    )
    health_parser.add_argument(
        "--no-write",
        action="store_true",
        help="Collect/normalize only; do not persist generated/ops/health-snapshot.json.",
    )
    # AS-OBS-002 — append-only OPS-EVT-* stream (thin additive CLI).
    events_parser = ops_sub.add_parser(
        "events",
        help=(
            "Read or update the append-only operational event stream under "
            "generated/ops/events/ (AS-OBS-002; events != authority)."
        ),
    )
    events_parser.add_argument("--vault", type=Path, required=True)
    events_parser.add_argument(
        "--json",
        action="store_true",
        help="Print events JSON array to stdout.",
    )
    events_parser.add_argument(
        "--record-health-transitions",
        action="store_true",
        help=(
            "Diff OBS-001 health snapshot vs stored prior and append "
            "OPS-EVT-HEALTH-TRANSITION only when health changes "
            "(no fabricated bootstrap events)."
        ),
    )
    events_parser.add_argument(
        "--retain",
        action="store_true",
        help="Apply count-based retention to the event stream.",
    )
    events_parser.add_argument(
        "--max-events",
        type=int,
        default=10_000,
        help="Retention cap (newest N events; default 10000).",
    )
    # AS-OBS-003 — regenerable ops-report projection (tip-safe; consume-only).
    report_parser = ops_sub.add_parser(
        "report",
        help=(
            "Emit regenerable ops-report JSON/Markdown under "
            "generated/ops/ops-report.* (AS-OBS-003; ops report != authority)."
        ),
    )
    report_parser.add_argument("--vault", type=Path, required=True)
    report_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the ops-report JSON to stdout.",
    )
    report_parser.add_argument(
        "--no-write",
        action="store_true",
        help="Project only; do not persist generated/ops/ops-report.*",
    )
    report_parser.add_argument(
        "--no-events",
        action="store_true",
        help="Skip optional OBS-002 events panel (snapshot-only report).",
    )
    report_parser.add_argument(
        "--archive",
        action="store_true",
        help="Also copy into generated/ops/archive/ops-report-NNNN.* (last-N).",
    )
    report_parser.add_argument(
        "--max-archive",
        type=int,
        default=50,
        help="Archive retention cap (default 50).",
    )

    # AS-XPROJ-001 - global entity registry (derived; explicit registration only).
    xproj_parser = subparsers.add_parser(
        "register-global-entity",
        help=(
            "Apply explicit global-entity / join registrations "
            "(AS-XPROJ-001; derived-only; no name-merge; no authority writes)."
        ),
    )
    xproj_parser.add_argument(
        "--registrations",
        type=Path,
        required=True,
        help="JSON file: {\"registrations\": [ {kind: entity|join, ...}, ... ] }.",
    )
    xproj_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=(
            "Optional vault root for derived emits under state/global-entities/ "
            "(joins/ and quarantine-candidates/ only)."
        ),
    )
    xproj_parser.add_argument(
        "--write",
        action="store_true",
        help="Write optional derived registry outputs (requires --vault).",
    )

    # AS-XPROJ-002 - cross-project edges (derived; explicit globals only).
    xproj_edge_parser = subparsers.add_parser(
        "register-global-edge",
        help=(
            "Apply explicit cross-project edge registrations "
            "(AS-XPROJ-002; derived-only; no name-merge; no authority writes)."
        ),
    )
    xproj_edge_parser.add_argument(
        "--edges",
        type=Path,
        required=True,
        help='JSON file: {"edges": [ {kind: edge, ...}, ... ] }.',
    )
    xproj_edge_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=(
            "Vault root with XPROJ-001 globals/joins; optional emits under "
            "state/global-entities/edges/ and edge-quarantine/."
        ),
    )
    xproj_edge_parser.add_argument(
        "--write",
        action="store_true",
        help="Write optional derived edge outputs (requires --vault).",
    )

    # AS-XPROJ-003 - duplicate / successor review candidates (derived; no autocollapse).
    xproj_dup_parser = subparsers.add_parser(
        "detect-project-duplicates",
        help=(
            "Detect duplicate / successor / monorepo-overlap review candidates "
            "(AS-XPROJ-003; derived-only; never UUID rewrite / name-merge)."
        ),
    )
    xproj_dup_parser.add_argument(
        "--projects",
        type=Path,
        required=True,
        help='JSON file: {"projects": [ {project_id, ...}, ... ] }.',
    )
    xproj_dup_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help=(
            "Optional vault root for derived emits under "
            "generated/xproj/duplicate-candidates/ only."
        ),
    )
    xproj_dup_parser.add_argument(
        "--write",
        action="store_true",
        help="Write optional derived duplicate-candidate outputs (requires --vault).",
    )
    xproj_dup_parser.add_argument(
        "--approved-monorepo-root",
        action="append",
        default=[],
        dest="approved_monorepo_roots",
        help="Approved monorepo root for path-prefix overlap (repeatable).",
    )

    # AS-BACKUP-001: verified snapshot / fixture restore (ops durability != authority).
    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help=(
            "Create or verify an Atlas recovery bundle (AS-BACKUP-001; "
            "operational durability != project authority)."
        ),
    )
    snapshot_parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault root to snapshot (required unless --verify).",
    )
    snapshot_parser.add_argument(
        "--cp",
        type=Path,
        default=None,
        help="Optional control-plane root for D4 receipts.",
    )
    snapshot_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="External bundle output directory (required unless --verify).",
    )
    snapshot_parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="Existing bundle directory (required with --verify).",
    )
    snapshot_parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify MANIFEST digests for an existing bundle; do not create.",
    )
    snapshot_parser.add_argument(
        "--include-d5",
        action="store_true",
        help="Include optional warm D5 derived cache (not used for cold certification).",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help=(
            "Restore a verified Atlas recovery bundle onto an empty disposable "
            "target (AS-BACKUP-001; fixture certify only)."
        ),
    )
    restore_parser.add_argument("--bundle", type=Path, required=True)
    restore_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Empty disposable restore target (AT-013 path safety).",
    )
    restore_parser.add_argument(
        "--tier",
        choices=["T0", "T1", "T2", "T3", "T4"],
        default="T3",
        help="Restore tier (default T3 governance-complete cold path omits D5).",
    )
    restore_parser.add_argument(
        "--expect-vault-logical-id",
        type=str,
        default=None,
        help="Refuse restore when bundle vault_logical_id disagrees (RS-06).",
    )
    restore_parser.add_argument(
        "--scaffold",
        action="store_true",
        help=(
            "Lay the deterministic vault scaffold onto the empty target before "
            "restoring members, giving full structural parity without a "
            "separate `atlas init` that would trip the empty-target guard."
        ),
    )

    # AS-INT-009 — raw package / receipt retention (operational; not authority).
    retention_parser = subparsers.add_parser(
        "retention",
        help=(
            "Apply deterministic raw-package and receipt retention caps "
            "(AS-INT-009; count/size only; never Layer B / never INT-010 tombstones)."
        ),
    )
    retention_sub = retention_parser.add_subparsers(
        dest="retention_command", required=True
    )
    retention_apply = retention_sub.add_parser(
        "apply",
        help="Apply retention to sources/agent-events and receipts/agent-events.",
    )
    retention_apply.add_argument("--vault", type=Path, required=True)
    retention_apply.add_argument(
        "--max-packages",
        type=int,
        default=None,
        help="Count cap (lexicographic keep-newest); overrides vault policy when set.",
    )
    retention_apply.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="Total byte cap across retained units; overrides vault policy when set.",
    )
    retention_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute victims and write report without deleting.",
    )
    retention_apply.add_argument(
        "--json",
        action="store_true",
        help="Print the retention report JSON to stdout.",
    )

    # AS-INT-011 — receipt revocation / invalidation (operational; not authority).
    revocation_parser = subparsers.add_parser(
        "revocation",
        help=(
            "Record or inspect agent-event receipt revocation / invalidation "
            "(AS-INT-011; never deletes receipt files; never Layer B)."
        ),
    )
    revocation_sub = revocation_parser.add_subparsers(
        dest="revocation_command", required=True
    )
    revocation_revoke = revocation_sub.add_parser(
        "revoke",
        help="Mark a receipt revoked or invalidated (does not delete the file).",
    )
    revocation_revoke.add_argument("--vault", type=Path, required=True)
    revocation_revoke.add_argument("--project", type=str, required=True)
    revocation_revoke.add_argument("--event", type=str, required=True)
    revocation_revoke.add_argument(
        "--reason",
        type=str,
        choices=("operator", "skill_policy", "integrity"),
        default="operator",
        help="Revocation reason (default: operator).",
    )
    revocation_revoke.add_argument(
        "--status",
        type=str,
        choices=("revoked", "invalidated"),
        default=None,
        help="Override default status for the reason.",
    )
    revocation_revoke.add_argument(
        "--detail",
        type=str,
        default=None,
        help="Optional deterministic detail string (no wall-clock).",
    )
    revocation_revoke.add_argument(
        "--json",
        action="store_true",
        help="Print the revocation index JSON to stdout.",
    )
    revocation_list = revocation_sub.add_parser(
        "list",
        help="List receipt revocations from generated/ops/receipt-revocations.json.",
    )
    revocation_list.add_argument("--vault", type=Path, required=True)
    revocation_list.add_argument(
        "--json",
        action="store_true",
        help="Print revocations JSON to stdout.",
    )
    revocation_status = revocation_sub.add_parser(
        "status",
        help="Show active/revoked/invalidated disposition for one receipt.",
    )
    revocation_status.add_argument("--vault", type=Path, required=True)
    revocation_status.add_argument("--project", type=str, required=True)
    revocation_status.add_argument("--event", type=str, required=True)
    revocation_status.add_argument(
        "--json",
        action="store_true",
        help="Print disposition JSON to stdout.",
    )

    # AS-INT-012 — schema compatibility / migration tooling (operational).
    schema_parser = subparsers.add_parser(
        "schema",
        help=(
            "Scan schema compatibility or emit a dry-run migration plan "
            "(AS-INT-012; never mutates scanned artifacts on dry-run)."
        ),
    )
    schema_sub = schema_parser.add_subparsers(dest="schema_command", required=True)
    schema_compat = schema_sub.add_parser(
        "compat",
        help="Scan known ops JSON artifacts against shipped schemas.",
    )
    schema_compat.add_argument("--vault", type=Path, required=True)
    schema_compat.add_argument(
        "--json",
        action="store_true",
        help="Print the schema-compat report JSON to stdout.",
    )
    schema_migrate = schema_sub.add_parser(
        "migrate",
        help="Dry-run migration plan only (no auto-apply).",
    )
    schema_migrate.add_argument("--vault", type=Path, required=True)
    schema_migrate.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Always dry-run (default; apply is not implemented in INT-012).",
    )
    schema_migrate.add_argument(
        "--json",
        action="store_true",
        help="Print the migration plan report JSON to stdout.",
    )

    # AS-CORE2-010 — fixture-safe lifecycle certification (≠ estate PILOT PASS).
    lifecycle_parser = subparsers.add_parser(
        "lifecycle",
        help=(
            "Run fixture-safe source lifecycle certification "
            "(AS-CORE2-010; never claims ESTATE PILOT PASSED)."
        ),
    )
    lifecycle_sub = lifecycle_parser.add_subparsers(
        dest="lifecycle_command", required=True
    )
    lifecycle_certify = lifecycle_sub.add_parser(
        "certify",
        help="Execute the fixture lifecycle matrix and write an ops report.",
    )
    lifecycle_certify.add_argument(
        "--work-root",
        type=Path,
        required=True,
        help="Scratch directory for synthetic sources/vaults (fixture-safe).",
    )
    lifecycle_certify.add_argument(
        "--report-vault",
        type=Path,
        default=None,
        help="Optional vault that receives generated/ops/lifecycle-cert-report.json.",
    )
    lifecycle_certify.add_argument(
        "--json",
        action="store_true",
        help="Print the certification report JSON to stdout.",
    )

    # AS-2.0-COMPAT-001 — verify machine-readable 1.0 compatibility anchor.
    compat_parser = subparsers.add_parser(
        "compat",
        help=(
            "Verify the Atlas 1.0 compatibility anchor "
            "(AS-2.0-COMPAT-001; 1.0 wins conflicts)."
        ),
    )
    compat_sub = compat_parser.add_subparsers(dest="compat_command", required=True)
    compat_verify = compat_sub.add_parser(
        "verify",
        help="Load and pin-check docs/releases/1.0.0/compatibility-anchor.json.",
    )
    compat_verify.add_argument(
        "--anchor",
        type=Path,
        default=None,
        help="Optional explicit anchor path (defaults to shipped repo path).",
    )
    compat_verify.add_argument(
        "--json",
        action="store_true",
        help="Print the verified anchor JSON to stdout.",
    )

    # AS-KF2-* Wave 1 — Knowledge Fabric namespace/entity/relationship (derived).
    kf2_parser = subparsers.add_parser(
        "kf2",
        help=(
            "Knowledge Fabric Wave 1 helpers "
            "(AS-KF2-NS/ENTITY/REL; derived != authority)."
        ),
    )
    kf2_sub = kf2_parser.add_subparsers(dest="kf2_command", required=True)
    kf2_ns = kf2_sub.add_parser("namespace", help="Register a KF2 namespace.")
    kf2_ns.add_argument("--vault", type=Path, required=True)
    kf2_ns.add_argument("--id", required=True, dest="namespace_id")
    kf2_ns.add_argument("--name", required=True, dest="display_name")
    kf2_ns.add_argument("--notes", default=None)
    kf2_ns.add_argument("--json", action="store_true")
    kf2_entity = kf2_sub.add_parser("entity", help="Register a KF2 entity.")
    kf2_entity.add_argument("--vault", type=Path, required=True)
    kf2_entity.add_argument("--id", required=True, dest="entity_id")
    kf2_entity.add_argument("--namespace", required=True, dest="namespace_id")
    kf2_entity.add_argument("--name", required=True, dest="display_name")
    kf2_entity.add_argument("--xproj-global-id", default=None)
    kf2_entity.add_argument("--notes", default=None)
    kf2_entity.add_argument("--json", action="store_true")
    kf2_rel = kf2_sub.add_parser("rel", help="Register a KF2 relationship.")
    kf2_rel.add_argument("--vault", type=Path, required=True)
    kf2_rel.add_argument("--id", required=True, dest="relationship_id")
    kf2_rel.add_argument("--from", required=True, dest="from_entity_id")
    kf2_rel.add_argument("--to", required=True, dest="to_entity_id")
    kf2_rel.add_argument(
        "--type",
        required=True,
        dest="relation_type",
        choices=[
            "depends-on",
            "implements",
            "related-to",
            "supersedes",
            "member-of",
        ],
    )
    kf2_rel.add_argument("--notes", default=None)
    kf2_rel.add_argument("--json", action="store_true")

    # AS-2.0-FED-001 — operator-declared federation join inventory.
    fed_parser = subparsers.add_parser(
        "federation",
        help=(
            "Build an operator-declared federation join inventory "
            "(AS-2.0-FED-001; consume-only; no cross-vault promote)."
        ),
    )
    fed_sub = fed_parser.add_subparsers(dest="federation_command", required=True)
    fed_join = fed_sub.add_parser(
        "join",
        help="Build join inventory from explicit --member rows (no crawl).",
    )
    fed_join.add_argument("--federation-id", required=True)
    fed_join.add_argument(
        "--member",
        action="append",
        required=True,
        dest="members",
        help=(
            "member_id|vault_path|role[|project_id] "
            "(role=primary|member; pipe-separated for Windows paths)."
        ),
    )
    fed_join.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Vault that receives generated/federation/<id>-join-inventory.json.",
    )
    fed_join.add_argument("--json", action="store_true")

    # AS-2.0-PROV-001 — optional provider adapters (disabled by default).
    provider_parser = subparsers.add_parser(
        "provider",
        help=(
            "Provider adapter registry and quarantine helpers "
            "(AS-2.0-PROV-001; disabled-by-default; never authority)."
        ),
    )
    provider_sub = provider_parser.add_subparsers(
        dest="provider_command", required=True
    )
    provider_registry = provider_sub.add_parser(
        "registry",
        help="Write a disabled-by-default provider adapter registry.",
    )
    provider_registry.add_argument("--vault", type=Path, required=True)
    provider_registry.add_argument(
        "--adapter",
        action="append",
        default=None,
        dest="adapters",
        help="adapter_id|provider|cap1,cap2 (enabled forced false).",
    )
    provider_registry.add_argument("--json", action="store_true")
    provider_quarantine = provider_sub.add_parser(
        "quarantine",
        help="Quarantine provider output after secret scan (metadata only).",
    )
    provider_quarantine.add_argument("--vault", type=Path, required=True)
    provider_quarantine.add_argument("--envelope-id", required=True)
    provider_quarantine.add_argument("--adapter-id", required=True)
    provider_quarantine.add_argument(
        "--text",
        required=True,
        help="Provider payload text (scanned; never logged raw on findings).",
    )
    provider_quarantine.add_argument(
        "--enable-adapters",
        action="store_true",
        help="Opt-in scan/quarantine path (still never promotes to authority).",
    )
    provider_quarantine.add_argument("--json", action="store_true")

    # AS-2.0-KCI-001 — consume-only compile request / receipt envelopes.
    kci_parser = subparsers.add_parser(
        "kci",
        help=(
            "Knowledge Compilation Interface envelopes "
            "(AS-2.0-KCI-001; consume-only; != Layer B authority)."
        ),
    )
    kci_sub = kci_parser.add_subparsers(dest="kci_command", required=True)
    kci_req = kci_sub.add_parser(
        "request",
        help="Build a consume-only KCI compile-request record.",
    )
    kci_req.add_argument("--request-id", required=True)
    kci_req.add_argument(
        "--source-ref",
        action="append",
        required=True,
        dest="source_refs",
        help="Provenance/source pointer (repeatable; required).",
    )
    kci_req.add_argument(
        "--subject-ref",
        action="append",
        default=None,
        dest="subject_refs",
        help="Optional subject pointer (repeatable).",
    )
    kci_req.add_argument(
        "--fixture-mode",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    kci_req.add_argument("--notes", default=None)
    kci_req.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Vault that receives generated/kci/<id>-compile-request.json.",
    )
    kci_req.add_argument("--json", action="store_true")
    kci_receipt = kci_sub.add_parser(
        "receipt",
        help="Issue a consume-only KCI compile-receipt (never promotes authority).",
    )
    kci_receipt.add_argument("--receipt-id", required=True)
    kci_receipt.add_argument("--request-id", required=True)
    kci_receipt.add_argument(
        "--status",
        choices=["accepted", "refused"],
        default="accepted",
    )
    kci_receipt.add_argument(
        "--outcome-ref",
        action="append",
        default=None,
        dest="outcome_refs",
    )
    kci_receipt.add_argument("--refusal-reason", default=None)
    kci_receipt.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Vault that receives generated/kci/<id>-compile-receipt.json.",
    )
    kci_receipt.add_argument("--json", action="store_true")

    # AS-2.2-RUNTIME-001 — Hybrid Retrieval + Context Compiler P0 (read-only).
    runtime_parser = subparsers.add_parser(
        "runtime",
        help=(
            "Atlas 2.2 runtime P0: hybrid retrieval + context compiler "
            "(AS-2.2-RUNTIME-001; read-only; no LLM authority)."
        ),
    )
    runtime_sub = runtime_parser.add_subparsers(dest="runtime_command", required=True)
    runtime_hybrid = runtime_sub.add_parser(
        "hybrid-retrieve",
        help="Deterministic hybrid retrieval (lexical; semantic forbidden).",
    )
    runtime_hybrid.add_argument("--vault", type=Path, required=True)
    runtime_hybrid.add_argument(
        "--project",
        required=True,
        dest="project_id",
        help="Project scope (required; cross-project retrieval is denied).",
    )
    runtime_hybrid.add_argument("--kind", required=True)
    runtime_hybrid.add_argument("--value", required=True)
    runtime_hybrid.add_argument(
        "--mode",
        choices=("exact", "prefix"),
        default="exact",
    )
    runtime_hybrid.add_argument("--cap", type=int, default=20)
    runtime_hybrid.add_argument(
        "--include-graph-slot",
        action="store_true",
        help="Attach derived impact-graph summary (GRAPH ≠ AUTHORITY).",
    )
    runtime_hybrid.add_argument("--json", action="store_true")
    runtime_compile = runtime_sub.add_parser(
        "compile-context",
        help="Budgeted context compiler (P0/P2) from hybrid candidates JSON.",
    )
    runtime_compile.add_argument("--vault", type=Path, required=True)
    runtime_compile.add_argument(
        "--project",
        required=True,
        dest="project_id",
        help="Project scope (required; out-of-scope candidates fail closed).",
    )
    runtime_compile.add_argument("--pack-id", required=True)
    runtime_compile.add_argument(
        "--candidates",
        type=Path,
        required=True,
        help="JSON file with {candidates:[...]} from hybrid-retrieve.",
    )
    runtime_compile.add_argument("--budget", type=int, default=20)
    runtime_compile.add_argument("--profile-id", default="p0-readonly")
    runtime_compile.add_argument(
        "--on-overflow",
        choices=("truncate", "fail"),
        default="truncate",
        help="Budget overflow policy (P2; truncate or fail-closed).",
    )
    runtime_compile.add_argument(
        "--exclude-unresolved-conflicts",
        action="store_true",
        help="P2: drop unresolved-conflict claims instead of retaining sidecars.",
    )
    runtime_compile.add_argument(
        "--write",
        action="store_true",
        help="Write derived package under generated/context-compiler/.",
    )
    runtime_compile.add_argument("--json", action="store_true")

    # AS-2.2-KDIFF-001 — Knowledge Diff / Time Machine P0 (read-only; project-scoped).
    kdiff_parser = subparsers.add_parser(
        "kdiff",
        help=(
            "Knowledge Diff / Time Machine P0: read-only as-of read + T1->T2 diff "
            "(AS-2.2-KDIFF-001; project-scoped; derived != authority; no writes)."
        ),
    )
    kdiff_parser.add_argument("--vault", type=Path, required=True)
    kdiff_parser.add_argument(
        "--project",
        type=str,
        required=True,
        help="Project scope (REQUIRED; fail-closed).",
    )
    kdiff_parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        dest="as_of",
        help="Declared valid-time for an as-of read (mutually exclusive with --from/--to).",
    )
    kdiff_parser.add_argument(
        "--from",
        type=str,
        default=None,
        dest="from_ref",
        help="T1 declared valid-time for a T1->T2 diff.",
    )
    kdiff_parser.add_argument(
        "--to",
        type=str,
        default=None,
        dest="to_ref",
        help="T2 declared valid-time for a T1->T2 diff.",
    )
    kdiff_parser.add_argument(
        "--compilation-id",
        type=str,
        default=None,
        dest="compilation_id",
        help="Optional knowledge-compilation boundary (binds knowledge-time).",
    )
    kdiff_parser.add_argument(
        "--subject-cap",
        type=int,
        default=None,
        dest="subject_cap",
        help="Bound subject+field fanout (default 500; max 5000).",
    )
    kdiff_parser.add_argument("--json", action="store_true")
    # AS-2.2-ASK2-001 — Ask Atlas 2 answer lens (project-scoped hybrid + p2 compiler).
    ask2_parser = subparsers.add_parser(
        "ask2",
        help=(
            "Ask Atlas 2 read-only answer lens over project-scoped hybrid "
            "retrieval + p2-readonly context compiler (AS-2.2-ASK2-001; "
            "UNKNOWN stays UNKNOWN; UI != canonical; model != authority)."
        ),
    )
    ask2_parser.add_argument("--vault", type=Path, required=True)
    ask2_parser.add_argument("--question", type=str, required=True)
    ask2_parser.add_argument(
        "--project",
        type=str,
        required=True,
        help="Project scope (structurally required; no cross-project answers).",
    )
    ask2_parser.add_argument(
        "--kind",
        action="append",
        default=None,
        dest="kind_args",
        help="Record kind to probe (repeatable; default: concept, claim).",
    )
    ask2_parser.add_argument(
        "--mode",
        choices=("exact", "prefix"),
        default="exact",
    )
    ask2_parser.add_argument("--budget", type=int, default=20)
    ask2_parser.add_argument("--cap", type=int, default=20)
    ask2_parser.add_argument(
        "--no-legacy-scan",
        action="store_true",
        help="Disable the subordinate legacy substring compatibility scan.",
    )
    ask2_parser.add_argument("--json", action="store_true")

    # AS-2.0-CTX-001 — fixture-safe context packs with provenance pointers.
    ctx_parser = subparsers.add_parser(
        "context-pack",
        help=(
            "Build a fixture-safe context pack "
            "(AS-2.0-CTX-001; provenance pointers required; != estate facts)."
        ),
    )
    ctx_sub = ctx_parser.add_subparsers(dest="context_pack_command", required=True)
    ctx_build = ctx_sub.add_parser(
        "build",
        help="Build a context-pack record (no estate-fact invention).",
    )
    ctx_build.add_argument("--pack-id", required=True)
    ctx_build.add_argument(
        "--provenance",
        action="append",
        required=True,
        dest="provenance",
        help="kind|ref (kind=source|receipt|index|claim|concept|other).",
    )
    ctx_build.add_argument(
        "--entry",
        action="append",
        default=None,
        dest="entries",
        help="entry_id|ref[|label] (optional repeatable).",
    )
    ctx_build.add_argument("--notes", default=None)
    ctx_build.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Vault that receives generated/context/<id>-context-pack.json.",
    )
    ctx_build.add_argument("--json", action="store_true")

    # AS-2.0-TWIN-FIXTURE-001 — disposable twin projection fixtures (≠ TWIN-001 READY).
    twin_fixture_parser = subparsers.add_parser(
        "twin-fixture",
        help=(
            "Build disposable Digital Twin projection fixtures "
            "(AS-2.0-TWIN-FIXTURE-001; never estate PILOT / TWIN production READY)."
        ),
    )
    twin_fixture_sub = twin_fixture_parser.add_subparsers(
        dest="twin_fixture_command", required=True
    )
    twin_build = twin_fixture_sub.add_parser(
        "build",
        help="Write a disposable twin projection fixture under generated/ops/.",
    )
    twin_build.add_argument("--vault", type=Path, required=True)
    twin_build.add_argument("--projection-id", required=True)
    twin_build.add_argument(
        "--project",
        action="append",
        default=None,
        dest="projects",
        help="project_id|display_name[|health] (health defaults to unknown).",
    )
    twin_build.add_argument(
        "--authentic-pilot-roots",
        type=int,
        default=0,
        help="Authentic PILOT root count (0 keeps healthy→unknown demotion).",
    )
    twin_build.add_argument("--json", action="store_true")

    # D-177 — full product demo harness (TECHNICAL DEMO only).
    demo_parser = subparsers.add_parser(
        "demo",
        help=(
            "Technical demo harness (D-177; DEMO_FIXTURE; "
            "NOT RELEASE CERTIFIED; NOT AUTHENTIC PILOT)."
        ),
    )
    demo_sub = demo_parser.add_subparsers(dest="demo_command", required=True)
    demo_full = demo_sub.add_parser(
        "full",
        help="Run disposable full-product demo acts and write receipt JSON.",
    )
    demo_full.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Disposable work root (default: .tmp/d177-full-product-demo).",
    )
    demo_full.add_argument(
        "--receipt",
        type=Path,
        default=None,
        help="Receipt path (default: generated/ops/full-product-demo-receipt.json).",
    )
    demo_full.add_argument(
        "--reset",
        action="store_true",
        help="Force rematerialize estate (always rematerializes today).",
    )
    demo_full.add_argument("--json", action="store_true", help="Print receipt JSON.")

    # AS-2.0-OAI-IMPORT-001 — OpenAI importer fixture harness (no live API).
    oai_parser = subparsers.add_parser(
        "openai-import",
        help=(
            "Parse synthetic OpenAI chat-export fixtures into quarantined "
            "receipts (AS-2.0-OAI-IMPORT-001; no live API)."
        ),
    )
    oai_sub = oai_parser.add_subparsers(dest="openai_import_command", required=True)
    oai_parse = oai_sub.add_parser(
        "parse",
        help="Parse sample-chat-export.md → fixture receipt (+ PROV quarantine).",
    )
    oai_parse.add_argument("--vault", type=Path, required=True)
    oai_parse.add_argument("--receipt-id", required=True)
    oai_parse.add_argument(
        "--sample",
        type=Path,
        default=None,
        help="Path to synthetic chat export (defaults to docs fixture).",
    )
    oai_parse.add_argument("--adapter-id", default="openai-fixture")
    oai_parse.add_argument(
        "--no-quarantine",
        action="store_true",
        help="Skip PROV quarantine write (parse-only receipt).",
    )
    oai_parse.add_argument(
        "--disable-adapters",
        action="store_true",
        help="Force adapters_enabled=false for PROV quarantine path.",
    )
    oai_parse.add_argument("--json", action="store_true")

    # AS-SYNC-001-SCAFFOLD — dry-run registry from explicit roots (≠ production SYNC-001).
    sync_parser = subparsers.add_parser(
        "sync",
        help=(
            "Workspace registry scaffold helpers "
            "(AS-SYNC-001-SCAFFOLD dry-run only; never claims SYNC-001 certified)."
        ),
    )
    sync_sub = sync_parser.add_subparsers(dest="sync_command", required=True)
    sync_registry = sync_sub.add_parser(
        "registry",
        help="Dry-run workspace registry from explicit roots (no whole-machine scan).",
    )
    sync_registry_sub = sync_registry.add_subparsers(
        dest="sync_registry_command", required=True
    )
    sync_dry_run = sync_registry_sub.add_parser(
        "dry-run",
        help="Build a schema-valid dry-run registry from --root paths only.",
    )
    sync_dry_run.add_argument(
        "--root",
        type=Path,
        action="append",
        required=True,
        dest="roots",
        help="Explicit project/workspace root (repeatable). Required; no implied scan.",
    )
    sync_dry_run.add_argument(
        "--vault",
        type=Path,
        required=True,
        help="Vault that receives generated/ops/workspace-registry-dry-run.json.",
    )
    sync_dry_run.add_argument(
        "--vault-identity",
        required=True,
        help="Vault identity binding string (mismatch refuse is future SYNC concern).",
    )
    sync_dry_run.add_argument(
        "--allowed-prefix",
        type=Path,
        action="append",
        default=None,
        dest="allowed_prefixes",
        help="Optional allowed root prefix (repeatable). Defaults to the --root set.",
    )
    sync_dry_run.add_argument(
        "--json",
        action="store_true",
        help="Print the dry-run registry JSON to stdout.",
    )
    # AS-ADV-RELEASE-001 — fixture recovery / determinism / perf (≠ RELEASE CERTIFIED).
    adv_parser = subparsers.add_parser(
        "adv",
        help=(
            "Run fixture-safe advanced release certification "
            "(AS-ADV-RELEASE-001; never stamps RELEASE CERTIFIED)."
        ),
    )
    adv_sub = adv_parser.add_subparsers(dest="adv_command", required=True)
    adv_certify = adv_sub.add_parser(
        "certify",
        help="Execute recovery/determinism/perf fixture matrix and write ops report.",
    )
    adv_certify.add_argument(
        "--work-root",
        type=Path,
        required=True,
        help="Scratch directory for synthetic sources/vaults (fixture-safe).",
    )
    adv_certify.add_argument(
        "--report-vault",
        type=Path,
        default=None,
        help="Optional vault that receives generated/ops/adv-release-cert-report.json.",
    )
    adv_certify.add_argument(
        "--json",
        action="store_true",
        help="Print the certification report JSON to stdout.",
    )
    # AS-2.1 live productionization surfaces (read-first / supervised).
    live_parser = subparsers.add_parser(
        "live",
        help=(
            "Atlas 2.1 live productionization helpers "
            "(API/MCP/OAI/SCHED/PILOT-PREP; never stamps RELEASE CERTIFIED)."
        ),
    )
    live_sub = live_parser.add_subparsers(dest="live_command", required=True)
    live_api = live_sub.add_parser("api-serve", help="Serve LIVE_API read-only (127.0.0.1).")
    live_api.add_argument("--vault", type=Path, required=True)
    live_api.add_argument("--host", default="127.0.0.1")
    live_api.add_argument("--port", type=int, default=8765)
    live_mcp = live_sub.add_parser("mcp-invoke", help="Invoke one MCP_READ allow-listed tool.")
    live_mcp.add_argument("--vault", type=Path, required=True)
    live_mcp.add_argument("--tool", required=True)
    live_mcp.add_argument("--json", action="store_true")
    live_oai = live_sub.add_parser(
        "oai-import",
        help="REAL_OPENAI_EXPORT_IMPORT from an on-disk export file.",
    )
    live_oai.add_argument("--vault", type=Path, required=True)
    live_oai.add_argument("--export", type=Path, required=True)
    live_oai.add_argument("--import-id", required=True)
    live_oai.add_argument("--json", action="store_true")
    live_arm = live_sub.add_parser("sched-arm", help="Arm LIVE_SUPERVISED_SCHEDULER.")
    live_arm.add_argument("--vault", type=Path, required=True)
    live_arm.add_argument("--arm-id", required=True)
    live_arm.add_argument("--json", action="store_true")
    live_disp = live_sub.add_parser(
        "sched-dispatch",
        help=(
            "Dispatch a supervised job (requires ATLAS_CLI_ELEVATE_CAPS "
            "including scheduler.dispatch; no CLI self-grant)."
        ),
    )
    live_disp.add_argument("--vault", type=Path, required=True)
    live_disp.add_argument("--arm-id", required=True)
    live_disp.add_argument(
        "--job",
        choices=("validate", "build-indexes", "version"),
        required=True,
    )
    live_disp.add_argument("--json", action="store_true")
    live_pilot = live_sub.add_parser(
        "pilot-prep",
        help="Scan known roots for authentic PILOT prep (never invent markers).",
    )
    live_pilot.add_argument("--vault", type=Path, required=True)
    live_pilot.add_argument("--report-id", default="pilot-prep")
    live_pilot.add_argument("--json", action="store_true")
    live_oai_poc = live_sub.add_parser(
        "oai-responses-poc",
        help=(
            "EXPERIMENTAL OpenAI Responses POC (non-release-blocking; "
            "quarantine-first; read-only tools)."
        ),
    )
    live_oai_poc.add_argument("--vault", type=Path, required=True)
    live_oai_poc.add_argument("--run-id", required=True)
    live_oai_poc.add_argument("--prompt", required=True)
    live_oai_poc.add_argument("--model", default="gpt-4.1-mini")
    live_oai_poc.add_argument(
        "--force-offline",
        action="store_true",
        help="Skip live API even if OPENAI_API_KEY is set.",
    )
    live_oai_poc.add_argument("--json", action="store_true")
    live_perf = live_sub.add_parser(
        "perf-baseline",
        help="Record deterministic local read performance baselines (non-release-blocking).",
    )
    live_perf.add_argument("--vault", type=Path, required=True)
    live_perf.add_argument("--baseline-id", default="live-read")
    live_perf.add_argument("--iterations", type=int, default=3)
    live_perf.add_argument("--json", action="store_true")
    live_l3 = live_sub.add_parser(
        "l3-loop",
        help=(
            "Run bounded L3 policy-to-dispatch loop "
            "(requires ATLAS_CLI_ELEVATE_CAPS including autonomy.l3 and "
            "scheduler.dispatch; no CLI self-grant)."
        ),
    )
    live_l3.add_argument("--vault", type=Path, required=True)
    live_l3.add_argument("--policy-id", required=True)
    live_l3.add_argument(
        "--job",
        action="append",
        dest="jobs",
        choices=("validate", "build-indexes", "version"),
        required=True,
        help="Job to run (repeatable; capped by policy max_jobs_per_arm).",
    )
    live_l3.add_argument("--json", action="store_true")

    # AS-ORCH-001A/001B/001C — classify, route, Cursor bridge. Not dispatch.
    orch_parser = subparsers.add_parser(
        "orchestrator",
        help=(
            "Agent-result classification, policy routing, and Cursor bridge "
            "(AS-ORCH-001A/001B/001C/001D/AUTONOMY-001; routing != merge; "
            "hook != execution; governor != merge; 001D is single-hop only)."
        ),
    )
    orch_sub = orch_parser.add_subparsers(dest="orchestrator_command", required=True)
    orch_validate = orch_sub.add_parser(
        "validate-result",
        help=(
            "Validate an AgentResultEnvelope and classify the next transition. "
            "Does not execute the transition."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  atlas orchestrator validate-result result.json\n"
            "  atlas orchestrator validate-result --stdin < result.json\n"
            "  cat result.json | atlas orchestrator validate-result -\n"
        ),
    )
    orch_validate.add_argument(
        "result",
        nargs="?",
        type=Path,
        default=None,
        help="Path to AgentResultEnvelope JSON. Use - to read stdin.",
    )
    orch_validate.add_argument(
        "--stdin",
        action="store_true",
        dest="from_stdin",
        help="Read the envelope JSON from stdin.",
    )
    orch_route = orch_sub.add_parser(
        "route-result",
        help=(
            "Validate, classify, and apply the deterministic routing policy. "
            "Prints a machine-readable route. Does not dispatch or execute."
        ),
        description=(
            "Validate an AgentResultEnvelope, classify it with AS-ORCH-001A, "
            "and apply the AS-ORCH-001B routing policy. Does not dispatch or execute."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  atlas orchestrator route-result result.json\n"
            "  atlas orchestrator route-result --stdin < result.json\n"
            "  cat result.json | atlas orchestrator route-result -\n"
        ),
    )
    orch_route.add_argument(
        "result",
        nargs="?",
        type=Path,
        default=None,
        help="Path to AgentResultEnvelope JSON. Use - to read stdin.",
    )
    orch_route.add_argument(
        "--stdin",
        action="store_true",
        dest="from_stdin",
        help="Read the envelope JSON from stdin.",
    )
    orch_stage = orch_sub.add_parser(
        "cursor-stage-result",
        help=(
            "Validate, classify, route, and stage a single-slot Cursor handoff. "
            "Does not dispatch or execute."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  atlas orchestrator cursor-stage-result result.json\n"
            "  atlas orchestrator cursor-stage-result --stdin < result.json\n"
        ),
    )
    orch_stage.add_argument(
        "result",
        nargs="?",
        type=Path,
        default=None,
        help="Path to AgentResultEnvelope JSON. Use - to read stdin.",
    )
    orch_stage.add_argument(
        "--stdin",
        action="store_true",
        dest="from_stdin",
        help="Read the envelope JSON from stdin.",
    )
    orch_stage.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root for ephemeral bridge state (default: cwd).",
    )
    orch_ack = orch_sub.add_parser(
        "cursor-ack",
        help="Acknowledge a pending Cursor handoff by route digest. Not authority.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  atlas orchestrator cursor-ack <route-digest>\n",
    )
    orch_ack.add_argument("route_digest", help="SHA-256 hex digest of the staged route.")
    orch_ack.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root for ephemeral bridge state (default: cwd).",
    )
    orch_cursor_status = orch_sub.add_parser(
        "cursor-status",
        help="Read-only Cursor bridge diagnostics. Does not dispatch.",
    )
    orch_cursor_status.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root for ephemeral bridge state (default: cwd).",
    )
    orch_complete = orch_sub.add_parser(
        "cursor-complete",
        help=(
            "Surface the staged Cursor handoff as a machine-readable "
            "HandoffPacket. Does not require a Cursor stop event. "
            "Does not dispatch or execute."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  atlas orchestrator cursor-complete --root <repo>\n",
    )
    orch_complete.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root for ephemeral bridge state (default: cwd).",
    )
    orch_gov_status = orch_sub.add_parser(
        "governor-status",
        help=(
            "Read-only autonomous governor snapshot (AS-ORCH-AUTONOMY-001). "
            "Does not dispatch, merge, or start successor packages."
        ),
    )
    orch_gov_status.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_gov_status.add_argument(
        "--trust-store",
        type=Path,
        default=None,
        help="Optional trusted-anchor store. When omitted, the shipped record is used.",
    )
    orch_gov_discover = orch_sub.add_parser(
        "governor-discover",
        help=(
            "Discover the next safe non-destructive ready node from live "
            "or injected inventory. Does not execute or merge."
        ),
    )
    orch_gov_discover.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_gov_discover.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help="Optional LiveInventory JSON. When omitted, live git facts are used.",
    )
    orch_gov_discover.add_argument(
        "--trust-store",
        type=Path,
        default=None,
        help="Optional trusted-anchor store. When omitted, the shipped record is used.",
    )
    orch_gov_pilot = orch_sub.add_parser(
        "governor-pilot",
        help=(
            "Run the controlled in-process autonomy pilot through the real "
            "governor APIs. Non-destructive. Does not merge or start 001E."
        ),
    )
    orch_gov_loop = orch_sub.add_parser(
        "governor-loop-tick",
        help=(
            "AS-ORCH-001E: one persistent-loop tick above 001D. "
            "Does not merge, waive, or invent owner authority."
        ),
    )
    orch_gov_loop.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_gov_loop.add_argument(
        "--trust-store",
        type=Path,
        default=None,
        help="Optional trusted-anchor store.",
    )
    orch_gov_loop.add_argument(
        "--loop-store",
        type=Path,
        default=None,
        help="Optional loop state store (default: <root>/.atlas/orchestration/loop).",
    )
    orch_gov_service = orch_sub.add_parser(
        "governor-service-run",
        help=(
            "D-083 durable Atlas supervisor: persistent Cursor SDK "
            "runtime plus CI observer. Owns the DAG; SDK agents are workers. "
            "Does not merge."
        ),
    )
    orch_gov_service.add_argument("--root", type=Path, default=None)
    orch_gov_service.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="Optional cycle cap (default: run until stopped).",
    )
    orch_gov_service.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between schedule cycles.",
    )
    orch_gov_service.add_argument(
        "--fake-backend",
        action="store_true",
        help="Use in-process fake SDK backend (tests / no API key).",
    )
    orch_gov_service.add_argument(
        "--candidate-head",
        default=None,
        help="Optional initial 40-char candidate SHA. Live PR head is refreshed each cycle.",
    )
    orch_gov_service.add_argument(
        "--pr",
        type=int,
        default=429,
        help="Canonical continuation PR to refresh (default: 429).",
    )
    orch_gov_stop = orch_sub.add_parser(
        "governor-service-stop",
        help=(
            "Request graceful stop of a durable SDK supervisor via stop file. "
            "Does not merge."
        ),
    )
    orch_gov_stop.add_argument("--root", type=Path, default=None)
    orch_gov_resident = orch_sub.add_parser(
        "governor-resident-run",
        help=(
            "D-130 resident self-wake driver: owns NEXT_WAKE_AT and "
            "scheduler ticks without chat/owner heartbeats. Does not merge."
        ),
    )
    orch_gov_resident.add_argument("--root", type=Path, default=None)
    orch_gov_resident.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="Optional tick cap (default: run until stop file).",
    )
    orch_gov_resident.add_argument(
        "--detached-worker",
        action="store_true",
        help="Internal: launched as detached resident worker (no extra stdout).",
    )
    orch_sdk_auth = orch_sub.add_parser(
        "sdk-auth-status",
        help=(
            "Detect CURSOR_API_KEY availability without printing the secret. "
            "Records CURSOR_SDK_AUTH_REQUIRED at most once when needed."
        ),
    )
    orch_sdk_auth.add_argument("--root", type=Path, default=None)
    orch_broker = orch_sub.add_parser(
        "continuation-broker",
        help=(
            "AS-ORCH-CONTINUATION-BROKER-001: enqueue/consume/status for "
            "one consume-once successor. Stop-hook fallback only; primary "
            "continuation is governor-service-run. Does not merge."
        ),
    )
    orch_broker.add_argument(
        "broker_action",
        choices=("enqueue", "consume", "status"),
        help="Broker action.",
    )
    orch_broker.add_argument("--root", type=Path, default=None)
    orch_broker.add_argument("--cycle-id", default=None)
    orch_broker.add_argument(
        "--kind",
        default="CHECKPOINT_CONTINUE",
        help="Successor kind (enqueue only).",
    )
    orch_broker.add_argument("--trusted-main", default=None)
    orch_broker.add_argument("--trusted-tree", default=None)
    orch_dispatch_once = orch_sub.add_parser(
        "dispatch-once",
        help=(
            "Start exactly one governed target agent for a HANDOFF_READY "
            "dispatchable task, then stop. Does not auto-dispatch the next hop."
        ),
    )
    orch_dispatch_once.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_dispatch_once.add_argument(
        "--lease-id",
        default=None,
        help="Optional lease identity recorded on the dispatch for result binding.",
    )
    orch_dispatch_once.add_argument(
        "--bound-package-id",
        default=None,
        help="Optional work-package identity recorded for result binding.",
    )
    orch_dispatch_once.add_argument(
        "--base-main",
        default=None,
        help="Optional 40-char base main SHA recorded for result binding.",
    )
    orch_dispatch_once.add_argument(
        "--candidate-head",
        default=None,
        help="Optional 40-char candidate HEAD SHA recorded for result binding.",
    )
    orch_dispatch_once.add_argument(
        "--candidate-tree",
        default=None,
        help="Optional 40-char candidate tree SHA recorded for result binding.",
    )
    orch_dispatch_status = orch_sub.add_parser(
        "dispatch-status",
        help="Read-only dispatcher diagnostics. Does not start a process.",
    )
    orch_dispatch_status.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_dispatch_submit = orch_sub.add_parser(
        "dispatch-submit-result",
        help=(
            "Bind a validated AgentResultEnvelope to an existing dispatch. "
            "Does not stage the 001C slot and does not grant authority."
        ),
    )
    orch_dispatch_submit.add_argument("dispatch_id", help="SHA-256 dispatch identity.")
    orch_dispatch_submit.add_argument(
        "result",
        nargs="?",
        type=Path,
        default=None,
        help="Path to AgentResultEnvelope JSON. Use - to read stdin.",
    )
    orch_dispatch_submit.add_argument(
        "--stdin",
        action="store_true",
        dest="from_stdin",
        help="Read the envelope JSON from stdin.",
    )
    orch_dispatch_submit.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_dispatch_recover = orch_sub.add_parser(
        "dispatch-recover",
        help="Finish finalization after result_received. Never respawns a process.",
    )
    orch_dispatch_recover.add_argument("dispatch_id", help="SHA-256 dispatch identity.")
    orch_dispatch_recover.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_gov_pilot.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: cwd).",
    )
    orch_gov_pilot.add_argument(
        "--inventory",
        type=Path,
        default=None,
        help="Optional LiveInventory JSON. When omitted, live git facts are used.",
    )
    orch_gov_pilot.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="Optional directory for the hashed evidence bundle.",
    )
    orch_gov_pilot.add_argument(
        "--trust-store",
        type=Path,
        default=None,
        help="Optional trusted-anchor store. When omitted, the shipped record is used.",
    )

    # Atlas 3 additive commands (D-191 / D-192). Existing parsers are unchanged.
    from project_atlas.atlas3.cli import register_atlas3_parsers

    register_atlas3_parsers(subparsers)

    # AS-ORCH-PROGRAM-SUPERVISOR-001 additive commands, owner-authorized by
    # ATLAS-SUPERVISOR-OPERATIONAL-INTEGRATION-002. `program` and `agent` are
    # not Atlas 3 owned names and this registration does not touch the Atlas 3
    # seam above; both surfaces attach to the same `subparsers` independently.
    from project_atlas.orchestration.program.cli import (
        register_agent_parser,
        register_program_parser,
    )

    register_program_parser(subparsers)
    register_agent_parser(subparsers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # OG-ATLAS-CLI-LOGFORMAT-BOOTSTRAP-20260906: an explicitly requested
    # --log-format must govern every record this process emits, including the
    # ones produced while loading configuration below. Every module runs
    # `_log = get_logger(...)` at import, which configures logging with the
    # console default long before this point, so applying the flag only after
    # `load_config` left that first record in console format and a
    # JSON-consuming deployment received a stream it could not parse.
    # A config-file-selected format still applies at the call below; it cannot
    # govern the record emitted while reading the very file that selects it.
    if args.log_format is not None:
        configure_logging(log_format=args.log_format)
    try:
        _apply_stranger_defaults(args)
    except ConnectError as exc:
        _log.error("%s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        configure_logging()
        _log.error("configuration error: %s", exc)
        return EXIT_ERROR

    level = args.log_level or config.logging.level
    log_format = args.log_format or config.logging.format
    configure_logging(level=level, log_format=log_format)

    if args.command == "version":
        print(f"project-atlas {__version__}")
        return EXIT_OK

    if args.command == "init":
        from project_atlas.vault_identity import DEFAULT_VAULT_ID

        vault_id = args.vault_id or DEFAULT_VAULT_ID
        try:
            plan = create_scaffold(args.output, dry_run=args.dry_run, vault_id=vault_id)
        except (ScaffoldError, OSError) as exc:
            _log.error("init failed: %s", exc)
            return EXIT_ERROR
        action = "would create" if args.dry_run else "created"
        print(f"{action} vault scaffold at {plan.root}")
        print(f"  directories: {len(plan.directories)}")
        print(f"  files:       {len(plan.files)}")
        if args.dry_run:
            for relative, _ in plan.files:
                print(f"  - {relative}")
        return EXIT_OK

    if args.command == "discover":
        discover_command = getattr(args, "discover_command", None)
        # FR-002 legacy source-manifest path (unchanged contract).
        if (
            discover_command is None
            and getattr(args, "source", None) is not None
            and getattr(args, "output", None) is not None
            and getattr(args, "root", None) is None
        ):
            try:
                manifest = discover(
                    args.source,
                    excludes=config.discovery.exclude_globs,
                    max_file_size=config.discovery.max_file_size_bytes,
                )
                write_manifest(manifest, args.output)
            except (OSError, ValueError) as exc:
                _log.error("discover failed: %s", exc)
                return EXIT_ERROR
            print(f"discovered {len(manifest['sources'])} sources")
            print(f"agent event packages: {len(manifest.get('agent_events', []))}")
            print(f"manifest: {args.output}")
            return EXIT_OK

        # D-049 knowledge estate discovery.
        try:
            report_path = getattr(args, "report", None)
            root_arg = getattr(args, "root", None)
            vault_arg = getattr(args, "vault", None)
            as_json = bool(getattr(args, "discover_json", False))

            def _load_or_scan() -> dict[str, Any]:
                if report_path is not None:
                    raw = Path(report_path).read_text(encoding="utf-8")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise EstateDiscoveryError("report must be a JSON object")
                    return data
                root = Path(root_arg) if root_arg is not None else Path.cwd()
                include_projects = True
                include_knowledge = True
                if getattr(args, "projects", False) and not getattr(
                    args, "knowledge", False
                ):
                    include_knowledge = False
                if getattr(args, "knowledge", False) and not getattr(
                    args, "projects", False
                ):
                    include_projects = False
                cache: dict[str, Any] | None = None
                if vault_arg is not None:
                    cache = load_discovery_cache(
                        Path(vault_arg) / INCREMENTAL_CACHE_RELATIVE
                    )
                return discover_estate(
                    root,
                    vault=Path(vault_arg) if vault_arg is not None else None,
                    include_projects=include_projects,
                    include_knowledge=include_knowledge,
                    prior_cache=cache,
                    root_mode=str(
                        getattr(args, "root_mode", ROOT_MODE_BOUNDED_DIRECTORY)
                    ),
                )

            if discover_command == "review":
                report = _load_or_scan()
                rows = review_candidates(report)
                if as_json:
                    print(json.dumps({"review": rows}, indent=2, sort_keys=True))
                else:
                    if not rows:
                        print("No discovery candidates require review.")
                    else:
                        print(f"{len(rows)} candidate(s) require review:")
                        for row in rows:
                            print(
                                f"  - {row.get('candidate_id')} "
                                f"[{row.get('match_state')}] {row.get('path')}"
                            )
                            why = row.get("why_matched") or []
                            if why:
                                print(f"      why: {why[0]}")
                            conflicts = row.get("conflicting_evidence") or []
                            for conflict in conflicts[:5]:
                                if isinstance(conflict, dict):
                                    print(
                                        f"      conflict: {conflict.get('kind')}: "
                                        f"{conflict.get('detail')}"
                                    )
                            required_action = row.get("required_action")
                            if isinstance(required_action, str) and required_action:
                                print(f"      action: {required_action}")
                return EXIT_OK

            if discover_command == "connect":
                report = _load_or_scan()
                result = connect_discovered_candidate(
                    report,
                    args.candidate,
                    vault=Path(vault_arg) if vault_arg is not None else None,
                    dry_run=bool(getattr(args, "dry_run", False)),
                )
                if as_json:
                    print(json.dumps(result, indent=2, sort_keys=True, default=str))
                else:
                    print(
                        f"connected candidate {args.candidate} "
                        f"(explicit connect; discovery alone never ingests)"
                    )
                return EXIT_OK

            # Default estate scan: atlas discover [--root] [--projects|--knowledge]
            if getattr(args, "source", None) is not None and getattr(
                args, "output", None
            ) is None:
                raise EstateDiscoveryError(
                    "FR-002 discover requires both --source and --output; "
                    "for estate discovery use --root (or omit flags to scan cwd)"
                )
            report = _load_or_scan()
            out = getattr(args, "output", None)
            if out is None and vault_arg is not None:
                out = Path(vault_arg) / REPORT_RELATIVE
            if out is not None:
                write_discovery_report(report, Path(out))
                if vault_arg is not None:
                    write_discovery_cache(
                        report, Path(vault_arg) / INCREMENTAL_CACHE_RELATIVE
                    )
            if as_json:
                printable = {k: v for k, v in report.items() if not k.startswith("_")}
                print(json.dumps(printable, indent=2, sort_keys=True))
            else:
                print(format_discovery_human(report), end="")
                if out is not None:
                    print(f"report: {out}")
            return EXIT_OK
        except (OSError, ValueError, json.JSONDecodeError, EstateDiscoveryError) as exc:
            _log.error("discover failed: %s", exc)
            return EXIT_ERROR

    if args.command == "ingest":
        try:
            result = ingest(
                args.manifest,
                args.vault,
                authorized_source_root=args.source,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _log.error("ingest failed: %s", exc)
            return EXIT_ERROR
        print(f"ingested {result['documents_ingested']} documents")
        print(f"projects: {result['projects']}")
        print(f"agent events: {result.get('events_ingested', 0)}")
        print(f"quarantined events: {result.get('events_quarantined', 0)}")
        return EXIT_OK

    if args.command == "doctor":
        doctor_report = run_doctor(config, args.vault)
        if args.as_json:
            print(json.dumps(doctor_to_dict(doctor_report), indent=2, sort_keys=True))
        else:
            print(doctor_render_text(doctor_report))
        return EXIT_OK if doctor_report.ok else EXIT_ERROR

    if args.command == "connect":
        try:
            report = connect_project(
                args.source,
                vault=args.vault,
                dry_run=args.dry_run,
                include_portfolio=args.portfolio,
                skip_validate=args.skip_validate,
                excludes=config.discovery.exclude_globs,
                max_file_size=config.discovery.max_file_size_bytes,
            )
        except (ConnectError, OSError) as exc:
            _log.error("connect failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            status = report.get("status", "connected")
            print(f"atlas connect [{status}]")
            print(f"  project:   {report.get('project_root')}")
            print(f"  vault:     {report.get('vault')}")
            print(f"  vault_id:  {report.get('vault_id')}")
            print(f"  projects:  {', '.join(report.get('projects') or []) or '(none)'}")
            print(f"  documents: {report.get('documents_ingested', 0)}")
            print(f"  bind:      {report.get('bind_path')}")
            print(f"  receipt:   {report.get('receipt_path')}")
            if status == "connected":
                print(
                    "  next: atlas overview --vault <vault> "
                    "| atlas roadmap --vault <vault> --project <id> "
                    "| atlas next --vault <vault> --project <id> "
                    "| atlas ask2 --vault <vault> --project <id> "
                    "--question 'What is this project?'"
                )
                answers = report.get("overview_answers") or []
                if answers:
                    print(f"  overview: {', '.join(answers)}")
                state_answers = report.get("state_answers") or []
                if state_answers:
                    print(f"  state:    {', '.join(state_answers)}")
                changed_answers = report.get("changed_answers") or []
                if changed_answers:
                    print(f"  changed:  {', '.join(changed_answers)}")
                decisions_answers = report.get("decisions_answers") or []
                if decisions_answers:
                    print(f"  decisions:{', '.join(decisions_answers)}")
                unknown_answers = report.get("unknown_answers") or []
                if unknown_answers:
                    print(f"  unknown:  {', '.join(unknown_answers)}")
                roadmap_answers = report.get("roadmap_answers") or []
                if roadmap_answers:
                    print(f"  roadmap:  {', '.join(roadmap_answers)}")
                next_answers = report.get("next_answers") or []
                if next_answers:
                    print(f"  next:     {', '.join(next_answers)}")
                brief_paths = report.get("brief_paths") or []
                if brief_paths:
                    print(f"  brief:    {', '.join(brief_paths)}")
                obsidian_notes = report.get("obsidian_notes") or []
                if obsidian_notes:
                    print(f"  obsidian: {', '.join(obsidian_notes)}")
        return EXIT_OK

    if args.command == "overview-status":
        from project_atlas.web_api.overview_read import (
            WebOverviewReadError,
            read_overview_view,
            render_overview_status_text,
        )

        try:
            report = read_overview_view(args.vault)
        except (WebOverviewReadError, OSError, ValueError) as exc:
            _log.error("overview-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_overview_status_text(report), end="")
        return EXIT_OK

    if args.command == "overview":
        try:
            report = materialize_overview_lenses(
                args.vault,
                project_ids=args.projects,
            )
        except (OverviewError, OSError) as exc:
            _log.error("overview failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas overview [{report.get('status', 'ok')}]")
            print(f"  vault:    {report.get('vault')}")
            print(f"  projects: {', '.join(report.get('projects') or []) or '(none)'}")
            for path in report.get("answers_written") or []:
                print(f"  answer:   {path}")
            for lens in report.get("lenses") or []:
                summary = lens.get("summary") or "UNKNOWN"
                print(
                    f"  {lens.get('project_id')}: [{lens.get('status')}] {summary}"
                )
        return EXIT_OK

    if args.command == "state-status":
        from project_atlas.web_api.state_read import (
            WebStateReadError,
            read_state_view,
            render_state_status_text,
        )

        try:
            report = read_state_view(args.vault)
        except (WebStateReadError, OSError, ValueError) as exc:
            _log.error("state-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_state_status_text(report), end="")
        return EXIT_OK

    if args.command == "state":
        try:
            report = materialize_state_lenses(
                args.vault,
                project_ids=args.projects,
            )
        except (ProjectStateError, OSError) as exc:
            _log.error("state failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas state [{report.get('status', 'ok')}]")
            print(f"  vault:    {report.get('vault')}")
            print(f"  projects: {', '.join(report.get('projects') or []) or '(none)'}")
            for path in report.get("answers_written") or []:
                print(f"  answer:   {path}")
            for lens in report.get("lenses") or []:
                summary = lens.get("summary") or "UNKNOWN"
                print(
                    f"  {lens.get('project_id')}: "
                    f"[{lens.get('rollup')}/{lens.get('status')}] {summary}"
                )
        return EXIT_OK

    if args.command == "roadmap-status":
        from project_atlas.web_api.roadmap_read import (
            WebRoadmapAnswersReadError,
            read_roadmap_answers_view,
            render_roadmap_status_text,
        )

        try:
            report = read_roadmap_answers_view(args.vault)
        except (WebRoadmapAnswersReadError, OSError, ValueError) as exc:
            _log.error("roadmap-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_roadmap_status_text(report), end="")
        return EXIT_OK

    if args.command == "roadmap":
        try:
            derive = derive_roadmap_lenses if args.read_only else materialize_roadmap_lenses
            report = derive(
                args.vault,
                project_ids=args.projects,
            )
        except (ProjectRoadmapError, OSError) as exc:
            _log.error("roadmap failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas roadmap [{report.get('status', 'ok')}]")
            print(f"  vault:    {report.get('vault')}")
            print(f"  projects: {', '.join(report.get('projects') or []) or '(none)'}")
            for lens in report.get("lenses") or []:
                print(render_roadmap_text(lens))
        return EXIT_OK

    if args.command == "next":
        try:
            derive = derive_next_lenses if args.read_only else materialize_next_lenses
            report = derive(
                args.vault,
                project_ids=args.projects,
            )
        except (ProjectNextError, OSError) as exc:
            _log.error("next failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas next [{report.get('status', 'ok')}]")
            print(f"  vault:    {report.get('vault')}")
            print(f"  projects: {', '.join(report.get('projects') or []) or '(none)'}")
            for lens in report.get("lenses") or []:
                print(render_next_text(lens))
        return EXIT_OK

    if args.command == "next-status":
        from project_atlas.web_api.next_read import (
            WebNextReadError,
            read_next_view,
            render_next_status_text,
        )

        try:
            report = read_next_view(args.vault)
        except (WebNextReadError, OSError, ValueError) as exc:
            _log.error("next-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_next_status_text(report), end="")
        return EXIT_OK

    if args.command == "changed-status":
        from project_atlas.web_api.changed_read import (
            WebChangedReadError,
            read_changed_view,
            render_changed_status_text,
        )

        try:
            report = read_changed_view(args.vault)
        except (WebChangedReadError, OSError, ValueError) as exc:
            _log.error("changed-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_changed_status_text(report), end="")
        return EXIT_OK

    if args.command == "portfolio-status":
        from project_atlas.web_api.portfolio_read import (
            WebPortfolioReadError,
            read_portfolio_view,
            render_portfolio_status_text,
        )

        try:
            report = read_portfolio_view(args.vault)
        except (WebPortfolioReadError, OSError, ValueError) as exc:
            _log.error("portfolio-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_portfolio_status_text(report), end="")
        return EXIT_OK

    if args.command == "bitemporal-status":
        from project_atlas.web_api.bitemporal_read import (
            WebBitemporalReadError,
            read_bitemporal_view,
            render_bitemporal_status_text,
        )

        try:
            report = read_bitemporal_view(args.vault)
        except (WebBitemporalReadError, OSError, ValueError) as exc:
            _log.error("bitemporal-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_bitemporal_status_text(report), end="")
        return EXIT_OK

    if args.command == "index-status":
        from project_atlas.web_api.index_status import (
            WebIndexStatusError,
            read_index_status,
            render_index_status_text,
        )

        try:
            report = read_index_status(args.vault)
        except (WebIndexStatusError, OSError, ValueError) as exc:
            _log.error("index-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_index_status_text(report), end="")
        return EXIT_OK

    if args.command == "changed":
        try:
            report = materialize_changed_lenses(
                args.vault,
                project_ids=args.projects,
            )
        except (ProjectChangedError, OSError) as exc:
            _log.error("changed failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas changed [{report.get('status', 'ok')}]")
            print(f"  vault:    {report.get('vault')}")
            print(f"  projects: {', '.join(report.get('projects') or []) or '(none)'}")
            delta = report.get("delta") or {}
            print(
                "  delta:    "
                f"added={delta.get('added_count', 0)} "
                f"removed={delta.get('removed_count', 0)} "
                f"modified={delta.get('modified_count', 0)} "
                f"prior={delta.get('prior_baseline')}"
            )
            for path in report.get("answers_written") or []:
                print(f"  answer:   {path}")
            for lens in report.get("lenses") or []:
                summary = lens.get("summary") or "UNKNOWN"
                print(
                    f"  {lens.get('project_id')}: "
                    f"[{lens.get('rollup')}/{lens.get('status')}] {summary}"
                )
        return EXIT_OK

    if args.command == "decisions-status":
        from project_atlas.web_api.decisions_read import (
            WebDecisionsReadError,
            read_decisions_view,
            render_decisions_status_text,
        )

        try:
            report = read_decisions_view(args.vault)
        except (WebDecisionsReadError, OSError, ValueError) as exc:
            _log.error("decisions-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_decisions_status_text(report), end="")
        return EXIT_OK

    if args.command == "decisions":
        try:
            report = materialize_decisions_lenses(
                args.vault, project_ids=args.projects
            )
        except (ProjectDecisionsError, OSError) as exc:
            _log.error("decisions failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas decisions [{report.get('status', 'ok')}]")
            for lens in report.get("lenses") or []:
                print(
                    f"  {lens.get('project_id')}: "
                    f"[{lens.get('status')}] {lens.get('summary') or 'UNKNOWN'}"
                )
        return EXIT_OK

    if args.command == "architecture-status":
        from project_atlas.web_api.architecture_read import (
            WebArchitectureReadError,
            read_architecture_view,
            render_architecture_status_text,
        )

        try:
            report = read_architecture_view(args.vault)
        except (WebArchitectureReadError, OSError, ValueError) as exc:
            _log.error("architecture-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_architecture_status_text(report), end="")
        return EXIT_OK

    if args.command == "unknown-status":
        from project_atlas.web_api.unknown_read import (
            WebUnknownReadError,
            read_unknown_view,
            render_unknown_status_text,
        )

        try:
            report = read_unknown_view(args.vault)
        except (WebUnknownReadError, OSError, ValueError) as exc:
            _log.error("unknown-status read failed: %s", exc)
            return EXIT_ERROR
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_unknown_status_text(report), end="")
        return EXIT_OK

    if args.command == "unknown":
        try:
            report = materialize_unknown_lenses(
                args.vault, project_ids=args.projects
            )
        except (ProjectUnknownError, OSError) as exc:
            _log.error("unknown failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas unknown [{report.get('status', 'ok')}]")
            for lens in report.get("lenses") or []:
                print(
                    f"  {lens.get('project_id')}: "
                    f"[{lens.get('rollup')}] {lens.get('summary') or 'UNKNOWN'}"
                )
        return EXIT_OK

    if args.command == "attention":
        try:
            report = classify_attention(args.vault, args.project)
        except (AttentionHygieneError, OSError, ValueError) as exc:
            _log.error("attention failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas attention [{report.get('rollup', 'UNKNOWN')}]")
            care = report.get("care_about") or []
            print(f"care_about ({len(care)}):")
            for item in care:
                human_print(
                    f"  [{item.get('level')}] {item.get('reason_code')}: "
                    f"{item.get('why_seeing_this')} → {item.get('what_to_do')}"
                )
            counts = report.get("level_counts") or {}
            if counts:
                print(
                    "counts: "
                    + ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
                )
            if report.get("source_failure_total"):
                print(
                    f"source_failure_total={report.get('source_failure_total')} "
                    "(collapsed in care_about; inspect via atlas source-health)"
                )
        return EXIT_OK

    if args.command == "source-health":
        try:
            report = explain_source_health(args.vault, args.project)
        except (SourceHealthError, OSError, ValueError) as exc:
            _log.error("source-health failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            summary = report.get("summary") or {}
            print(
                f"atlas source-health [{report.get('health_state', 'UNKNOWN')}] "
                f"action_required={summary.get('action_required', 0)} "
                f"excluded_informational={summary.get('excluded_informational', 0)}"
            )
            print("ACTIONABLE:")
            for code, count in sorted((report.get("reason_counts") or {}).items()):
                print(f"  {code:24} {count}")
            if report.get("noise_groups"):
                print("EXCLUDED / INFORMATIONAL:")
                for group, count in sorted((report.get("noise_groups") or {}).items()):
                    print(f"  {group:24} {count}")
            for row in (report.get("actionable") or report.get("sources") or [])[:12]:
                print(
                    f"  [{row.get('status')}/{row.get('pipeline_stage')}] "
                    f"{row.get('source')}: {row.get('reason_code')}"
                )
        return EXIT_OK

    if args.command == "brief":
        try:
            report = materialize_project_briefs(
                args.vault,
                project_ids=args.projects,
                refresh=not args.no_refresh,
            )
        except (ProjectBriefError, OSError, ValueError) as exc:
            _log.error("brief failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas brief [{report.get('status', 'ok')}]")
            for brief in report.get("briefs") or []:
                print(f"  project:  {brief.get('project_id')}")
                print(f"  purpose:  {brief.get('purpose')}")
                print(f"  stack:    {brief.get('tech_stack')}")
                print(f"  state:    {brief.get('current_state')}")
                print(f"  changed:  {brief.get('recent_meaningful_changes')}")
                print(f"  decisions:{brief.get('important_decisions')}")
                print(f"  unknown:  {brief.get('unknown_or_conflicting')}")
                print(f"  next:     {'; '.join(brief.get('suggested_next_work') or [])}")
        return EXIT_OK

    if args.command == "context":
        try:
            report = export_agent_context(
                args.vault,
                args.project,
                refresh_brief=not args.no_refresh,
            )
        except (AgentHandoffError, OSError, ValueError) as exc:
            _log.error("context failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas context [{report.get('status', 'ok')}]")
            print(f"  project:  {report.get('project_id')}")
            print(f"  markdown: {report.get('markdown_path')}")
            print(f"  json:     {report.get('json_path')}")
            print("  next: paste markdown into Cursor/Claude/Codex/ChatGPT")
        return EXIT_OK

    if args.command == "handoff":
        try:
            if args.handoff_command == "create":
                report = create_handoff(
                    args.vault,
                    args.project,
                    note=args.note,
                    refresh_brief=not args.no_refresh,
                    auto_capture=not args.no_capture,
                )
            else:
                report = resume_handoff(args.vault, handoff_id=args.handoff_id)
        except (AgentHandoffError, SessionCaptureError, OSError, ValueError) as exc:
            _log.error("handoff failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        elif args.handoff_command == "create":
            print(f"atlas handoff create [{report.get('status', 'ok')}]")
            print(f"  handoff:  {report.get('handoff_id')}")
            print(f"  path:     {report.get('path')}")
            print(f"  context:  {report.get('context_markdown')}")
            capture = report.get("session_capture") or {}
            if capture:
                print(f"  capture:  {capture.get('capture_id')}")
        else:
            print(f"atlas handoff resume [{report.get('status', 'resumed')}]")
            print(f"  handoff:  {report.get('handoff_id')}")
            print(f"  project:  {report.get('project_id')}")
            context = report.get("context") or {}
            print(f"  context:  {context.get('markdown_path')}")
            raw_freshness = report.get("freshness")
            freshness: dict[str, Any] = (
                raw_freshness if isinstance(raw_freshness, dict) else {}
            )
            print(f"  freshness: {freshness.get('status', 'UNKNOWN')}")
            warning = report.get("resume_warning")
            if warning:
                print(f"  warning:  {warning}")
            for item in report.get("resume_instructions") or []:
                print(f"  - {item}")
        return EXIT_OK

    if args.command == "inbox":
        try:
            if args.inbox_command != "list":
                raise KnowledgeInboxError(
                    "UNSUPPORTED_SCOPE",
                    f"inbox command not implemented: {args.inbox_command}",
                )
            project_id = args.project
            if project_id in {None, ""}:
                try:
                    project_id = resolve_bound_project_id(vault=args.vault)
                except ConnectError as exc:
                    raise KnowledgeInboxError(
                        "UNSUPPORTED_SCOPE",
                        "inbox list requires --project (no implicit portfolio-all)",
                    ) from exc
            report = list_inbox_items(
                args.vault,
                project_id=project_id,
                status=args.status,
                limit=args.limit,
            )
        except KnowledgeInboxError as exc:
            _log.error("inbox failed: %s", exc)
            inbox_error: dict[str, Any] = {
                "status": "error",
                "error": exc.code,
                "message": str(exc),
                "package": "AS-CODER-ALPHA-INBOX-LIST-001",
                "promoted_to_authority": False,
            }
            if getattr(args, "as_json", False):
                print(json.dumps(inbox_error, indent=2, sort_keys=True))
            else:
                print(f"atlas inbox list error [{exc.code}]: {exc}")
            return EXIT_ERROR
        except (OSError, ValueError) as exc:
            _log.error("inbox failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            items = report.get("items") or []
            print(f"atlas inbox list [{report.get('count', 0)}]")
            print(f"  project:  {report.get('project_id')}")
            print("  boundary: INBOX != AUTHORITY (observations, not Truth Core)")
            if not items:
                print("  UNKNOWN (no inbox items for project)")
            for item in items:
                print(
                    f"  - {item.get('receipt_id')} [{item.get('status')}] "
                    f"{item.get('summary')}"
                )
        return EXIT_OK

    if args.command == "obsidian":
        try:
            report = materialize_obsidian_projection(
                args.vault,
                project_id=args.project,
                refresh_brief=not args.no_refresh,
            )
        except (ObsidianProjectionError, OSError, ValueError) as exc:
            _log.error("obsidian projection failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"atlas obsidian project [{report.get('status', 'ok')}]")
            for path in report.get("notes_written") or []:
                print(f"  note: {path}")
            print(f"  receipt: {report.get('receipt_path')}")
            print("  next: open generated/obsidian/projects/ in Obsidian (plugin!=shipped)")
        return EXIT_OK

    if args.command == "review":
        try:
            report = apply_review_decision(
                args.vault,
                project_id=args.project,
                review_id=args.review_id,
                decision=args.decision,
                reason=args.reason,
                winner_claim_id=args.winner_claim_id,
            )
        except (HumanLoopError, OSError, ValueError) as exc:
            _log.error("review decide failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            decision = report.get("decision") or {}
            print(f"atlas review decide [{report.get('status', 'ok')}]")
            print(f"  review:   {decision.get('review_id')}")
            print(f"  decision: {decision.get('decision')}")
            print(f"  status:   {decision.get('status')}")
            print(f"  receipt:  {report.get('receipt_path')}")
            print("  next: atlas unknown --vault <vault> | re-run atlas connect to recompile")
        return EXIT_OK

    if args.command == "build-indexes":
        try:
            result = build_indexes(args.vault)
        except (OSError, ValueError) as exc:
            _log.error("build-indexes failed: %s", exc)
            return EXIT_ERROR
        print(f"indexed {result['projects']} projects and {result['sources']} sources")
        return EXIT_OK

    if args.command == "build-portfolio":
        try:
            result = build_portfolio(args.vault)
            # AS-2.0-TEMPORAL-001 / AS-2.2-KDIFF-001: derive the validity-window
            # catalog the shipped Time Machine reader consumes, from persisted
            # claims + document-declared valid-time. Derived (D5) and rebuilt on
            # every build, so it survives backup/restore via regeneration.
            catalog = build_bitemporal_catalogs(args.vault)
        except (OSError, ValueError) as exc:
            _log.error("build-portfolio failed: %s", exc)
            return EXIT_ERROR
        print(f"portfolio built for {result['projects']} projects")
        print(f"outputs: {', '.join(result['outputs'])}")
        print(
            "bitemporal catalogs: "
            f"{catalog['catalog_count']} ({catalog['window_count']} windows)"
        )
        return EXIT_OK

    if args.command == "migrate-v2":
        try:
            result = migrate_v2(args.vault, args.project)
        except (OSError, RuntimeError, ValueError) as exc:
            _log.error("migration failed: %s", exc)
            return EXIT_ERROR
        print(f"status: {result['status']}")
        if "migrated_claims" in result:
            print(f"migrated claims: {result['migrated_claims']}")
        if "receipt" in result:
            print(f"receipt: {result['receipt']}")
        return EXIT_OK

    if args.command == "validate":
        try:
            result = validate(args.vault)
        except (OSError, ValueError) as exc:
            _log.error("validate failed: %s", exc)
            return EXIT_ERROR
        # AS-H-010: severity→exit (ERROR→1; WARNING/INFO alone→0; preserve usage→2).
        exit_code = validation_exit_code(result)
        if exit_code != EXIT_OK:
            logged: set[str] = set()
            for error in result["errors"]:
                _log.error("validation: %s", error)
                logged.add(error)
            for finding in result.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                if finding.get("severity") != "error":
                    continue
                message = finding.get("message")
                if isinstance(message, str) and message and message not in logged:
                    _log.error("validation: %s", message)
            return EXIT_ERROR
        for finding in result.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            severity = finding.get("severity")
            message = finding.get("message")
            if not isinstance(message, str) or not message:
                continue
            if severity == "warning":
                _log.warning("validation: %s", message)
            elif severity == "info":
                _log.info("validation: %s", message)
        print(f"validated {result['markdown_files']} Markdown files")
        return EXIT_OK

    if args.command == "accept-graph":
        try:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise GraphAcceptanceError("manifest-not-object")
            receipt = accept_graphify_artifacts(
                project_root=args.source,
                manifest=manifest,
                config=config,
                strict=args.strict,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _log.error("accept-graph failed: %s", exc)
            return EXIT_ERROR
        summary = inspect_acceptance(receipt)
        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"accepted: {receipt.accepted_count}")
        print(f"rejected: {receipt.rejected_count}")
        print(f"nodes: {receipt.node_count}")
        print(f"edges: {receipt.edge_count}")
        print(f"semantic: {receipt.semantic_status}")
        print("authority: derived")
        return EXIT_OK

    if args.command == "resolve-graph":
        try:
            if args.write and args.vault is None:
                raise GraphResolutionError("resolve-graph --write requires --vault")
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise GraphResolutionError("manifest-not-object")
            mapping: dict[str, object] | None = None
            if args.mapping is not None:
                loaded = json.loads(args.mapping.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise GraphResolutionError("mapping-table-malformed")
                mapping = loaded
            _receipt, resolution = resolve_from_acceptance(
                project_root=args.source,
                manifest=manifest,
                mapping_table=mapping,
                config=config,
                local_project_uuid=args.project_uuid,
                strict=args.strict,
            )
            written: list[str] = []
            if args.write:
                assert args.vault is not None
                written = write_resolution_outputs(resolution, vault=args.vault)
            summary = inspect_resolution(resolution)
            print(json.dumps(summary, indent=2, sort_keys=True))
            print(f"resolved: {resolution.resolved_count}")
            print(f"quarantined: {resolution.quarantined_count}")
            print("authority: derived")
            if written:
                print(f"written: {len(written)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _log.error("resolve-graph failed: %s", exc)
            return EXIT_ERROR
        return EXIT_OK

    if args.command == "store-graph":
        try:
            if args.write and args.vault is None:
                raise GraphRelationshipError("store-graph --write requires --vault")
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise GraphRelationshipError("manifest-not-object")
            store_mapping: dict[str, object] | None = None
            if args.mapping is not None:
                loaded = json.loads(args.mapping.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise GraphRelationshipError("mapping-table-malformed")
                store_mapping = loaded
            max_edges = args.max_edges
            _receipt, _resolution, store = store_from_acceptance(
                project_root=args.source,
                manifest=manifest,
                mapping_table=store_mapping,
                config=config,
                local_project_uuid=args.project_uuid,
                strict=args.strict,
                **({"max_edges": max_edges} if max_edges is not None else {}),
            )
            store_written: list[str] = []
            if args.write:
                assert args.vault is not None
                store_written = write_relationship_outputs(store, vault=args.vault)
            summary = inspect_relationship_store(store)
            print(json.dumps(summary, indent=2, sort_keys=True))
            print(f"retained: {store.retained_count}")
            print(f"quarantined: {store.quarantined_count}")
            print("authority: derived")
            if store_written:
                print(f"written: {len(store_written)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _log.error("store-graph failed: %s", exc)
            return EXIT_ERROR
        return EXIT_OK

    if args.command == "query":
        field_args: list[str] | None = args.field_args
        fields_csv: str | None = args.fields_csv
        diag_shape = QueryShape.UNKNOWN
        diag_field: str | None = None
        diag_fields: list[str] | None = None
        try:
            if args.list:
                if field_args or fields_csv:
                    _log.error("query --list cannot be combined with --field/--fields")
                    return EXIT_ERROR
                diag_shape = QueryShape.LIST
                if args.kind == "authoritative":
                    answers = list_authoritative(args.vault, args.project)
                elif args.kind == "temporal":
                    answers = list_temporal(args.vault, args.project)
                else:
                    # AS-QUERY-001: unsupported list kind → fail-closed diagnostic
                    raise KnowledgeQueryError(
                        KnowledgeQueryErrorCode.UNSUPPORTED_KIND,
                        f"query --list does not support --kind {args.kind!r}",
                    )
                print(answer_to_json(answers), end="")
                return EXIT_OK
            if field_args and fields_csv:
                _log.error("query --field and --fields are mutually exclusive")
                return EXIT_ERROR
            if not args.subject:
                _log.error(
                    "query requires --subject and --field/--fields unless --list is set"
                )
                return EXIT_ERROR
            if fields_csv is not None:
                multifield = [part.strip() for part in fields_csv.split(",")]
                diag_shape = QueryShape.MULTIFIELD
                diag_fields = multifield
                csv_answer = query_knowledge_fields(
                    args.vault,
                    args.project,
                    args.subject,
                    multifield,
                    kind=args.kind,
                )
                print(answer_to_json(csv_answer), end="")
                return EXIT_OK
            if not field_args:
                _log.error(
                    "query requires --subject and --field/--fields unless --list is set"
                )
                return EXIT_ERROR
            if len(field_args) == 1:
                # Preserve AS-CORE-007 point-query CLI contract.
                diag_shape = QueryShape.POINT
                diag_field = field_args[0]
                point_answer = query_knowledge(
                    args.vault,
                    args.project,
                    args.subject,
                    field_args[0],
                    kind=args.kind,
                )
                print(answer_to_json(point_answer), end="")
                return EXIT_OK
            diag_shape = QueryShape.MULTIFIELD
            diag_fields = list(field_args)
            multi_answer = query_knowledge_fields(
                args.vault,
                args.project,
                args.subject,
                field_args,
                kind=args.kind,
            )
            print(answer_to_json(multi_answer), end="")
            return EXIT_OK
        except KnowledgeQueryError as exc:
            # AS-QUERY-DIAG-001-FR-009: structured stdout on integrity/request failures.
            _log.error("query failed [%s]: %s", exc.code.value, exc.message)
            diagnostic = query_diagnostic_from_error(
                exc,
                project_id=args.project,
                subject=args.subject,
                field=diag_field,
                fields=diag_fields,
                kind=args.kind,
                query_shape=diag_shape,
            )
            print(diagnostic_to_json(diagnostic), end="")
            return EXIT_ERROR
        except (OSError, ValueError, TypeError) as exc:
            _log.error("query failed: %s", exc)
            return EXIT_ERROR


    if args.command == "capture" and args.capture_command in {
        "text",
        "raw-list",
        "retry",
        "show",
    }:
        return _run_raw_capture(args, config)

    if args.command == "capture":
        try:
            if args.capture_command == "record":
                report = capture_session(
                    args.vault,
                    args.project,
                    summary=args.summary,
                    kind=args.kind,
                    decisions=args.decision,
                    changes=args.change,
                    next_work=args.next_work,
                    unknowns=args.unknowns,
                    source="explicit",
                )
            elif args.capture_command == "conversation":
                envelope = _load_conversation_envelope(args)
                report = capture_conversation(
                    args.vault,
                    envelope,
                    requested_project_id=args.project,
                )
            elif args.capture_command == "review":
                report = set_conversation_review_state(
                    args.vault,
                    args.capture_id,
                    args.review_state,
                )
            else:
                report = {
                    "schema_version": 1,
                    "package": "AS-CODER-ALPHA-CAPTURE-001",
                    "status": "ok",
                    "captures": list_captures(
                        args.vault,
                        project_id=args.project,
                        limit=args.limit,
                    ),
                }
        except ConversationCaptureError as exc:
            _log.error("capture failed: %s", exc)
            error_body = {
                "status": "error",
                "error": exc.code,
                "message": str(exc),
                "package": "AS-CODER-ALPHA-CONVERSATIONAL-CAPTURE-001",
            }
            if getattr(args, "as_json", False):
                print(json.dumps(error_body, indent=2, sort_keys=True))
            else:
                print(f"atlas capture conversation error [{exc.code}]: {exc}")
            return EXIT_ERROR
        except (SessionCaptureError, OSError, ValueError) as exc:
            _log.error("capture failed: %s", exc)
            return EXIT_ERROR
        if args.as_json:
            print(json.dumps(report, indent=2, sort_keys=True))
        elif args.capture_command == "record":
            print(f"atlas capture record [{report.get('status', 'ok')}]")
            print(f"  capture:  {report.get('capture_id')}")
            print(f"  project:  {report.get('project_id')}")
            print(f"  path:     {report.get('path')}")
            print("  next: atlas context / atlas handoff create to surface session memory")
        elif args.capture_command in {"conversation", "review"}:
            print(f"atlas capture {args.capture_command} [{report.get('status', 'ok')}]")
            print(f"  capture:  {report.get('capture_id')}")
            print(f"  project:  {report.get('project_id')}")
            print(f"  review:   {report.get('review_state')}")
            print("  authority: NON_CANONICAL quarantined evidence (not Truth Core)")
        else:
            captures = report.get("captures") or []
            print(f"atlas capture list [{len(captures)}]")
            if not captures:
                print("  UNKNOWN (no session captures yet)")
            for item in captures:
                print(
                    f"  - {item.get('capture_id')} [{item.get('kind')}] "
                    f"{item.get('summary')}"
                )
        return EXIT_OK

    if args.command == "ops":
        if args.ops_command == "health":
            try:
                snapshot = emit_health_snapshot(
                    args.vault,
                    project_filter=args.project,
                    persist=not args.no_write,
                )
            except (OpsHealthError, OSError, ValueError, TypeError) as exc:
                _log.error("ops health failed: %s", exc)
                return EXIT_ERROR
            if args.json or args.no_write:
                print(snapshot_to_json(snapshot), end="")
            else:
                print(f"estate rollup: {snapshot['rollup']['estate']}")
                print(f"signals: {len(snapshot['signals'])}")
                print(
                    f"snapshot: {args.vault / 'generated' / 'ops' / 'health-snapshot.json'}"
                )
            return EXIT_OK
        if args.ops_command == "events":
            try:
                if args.record_health_transitions:
                    record_health_transition(args.vault)
                if args.retain:
                    apply_retention(args.vault, max_events=args.max_events)
                events = read_events(args.vault)
            except (OpsEventError, OSError, ValueError, TypeError) as exc:
                _log.error("ops events failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(
                    json.dumps(events, indent=2, sort_keys=True, ensure_ascii=False)
                    + "\n",
                    end="",
                )
            else:
                print(f"events: {len(events)}")
                print(
                    f"stream: {args.vault / 'generated' / 'ops' / 'events' / 'stream.jsonl'}"
                )
            return EXIT_OK
        if args.ops_command == "report":
            try:
                report = emit_ops_report(
                    args.vault,
                    include_events=not args.no_events,
                    persist=not args.no_write,
                    archive=args.archive and not args.no_write,
                    max_archive=args.max_archive,
                )
            except (OpsReportError, OSError, ValueError, TypeError) as exc:
                _log.error("ops report failed: %s", exc)
                return EXIT_ERROR
            if args.json or args.no_write:
                print(report_to_json(report), end="")
            else:
                print(f"estate rollup: {report['rollup']['estate']}")
                print(f"snapshot_status: {report['snapshot_status']}")
                print(f"signals: {len(report['signals'])}")
                print(
                    f"report: {args.vault / 'generated' / 'ops' / 'ops-report.json'}"
                )
            return EXIT_OK
        parser.error(f"unknown ops command: {args.ops_command}")  # pragma: no cover

    if args.command == "register-global-entity":
        try:
            if args.write and args.vault is None:
                raise XprojRegistryError("register-global-entity --write requires --vault")
            payload = json.loads(args.registrations.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise XprojRegistryError("registrations-not-object")
            raw = payload.get("registrations")
            if not isinstance(raw, list):
                raise XprojRegistryError("registrations-not-array")
            requests: list[dict[str, object]] = []
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    raise XprojRegistryError(f"registration-not-object:{index}")
                requests.append(item)
            prior_entities = None
            prior_joins = None
            if args.vault is not None:
                prior_entities, prior_joins = load_registry_state(args.vault)
            xproj_result = apply_registrations(
                requests,
                prior_entities=prior_entities,
                prior_joins=prior_joins,
            )
            xproj_written: list[str] = []
            if args.write:
                assert args.vault is not None
                xproj_written = write_registry_outputs(xproj_result, vault=args.vault)
            summary = inspect_registry(xproj_result)
            print(json.dumps(summary, indent=2, sort_keys=True))
            print(f"registered: {xproj_result.registered_count}")
            print(f"joined: {xproj_result.joined_count}")
            print(f"quarantined: {xproj_result.quarantined_count}")
            print("authority: derived")
            if xproj_written:
                print(f"written: {len(xproj_written)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _log.error("register-global-entity failed: %s", exc)
            return EXIT_ERROR
        return EXIT_OK

    if args.command == "register-global-edge":
        try:
            if args.write and args.vault is None:
                raise XprojEdgeError("register-global-edge --write requires --vault")
            if args.vault is None:
                raise XprojEdgeError("register-global-edge requires --vault")
            payload = json.loads(args.edges.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise XprojEdgeError("edges-not-object")
            raw = payload.get("edges")
            if not isinstance(raw, list):
                raise XprojEdgeError("edges-not-array")
            edge_requests: list[dict[str, object]] = []
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    raise XprojEdgeError(f"edge-not-object:{index}")
                edge_requests.append(item)
            entities, joins = load_registry_state(args.vault)
            prior_edges = load_edge_registry_state(args.vault)
            edge_result = apply_edge_registrations(
                edge_requests,
                entities=entities,
                joins=joins,
                prior_edges=prior_edges,
            )
            edge_written: list[str] = []
            if args.write:
                edge_written = write_edge_outputs(edge_result, vault=args.vault)
            edge_summary = inspect_edge_registry(edge_result)
            print(json.dumps(edge_summary, indent=2, sort_keys=True))
            print(f"registered: {edge_result.registered_count}")
            print(f"quarantined: {edge_result.quarantined_count}")
            print("authority: derived")
            if edge_written:
                print(f"written: {len(edge_written)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _log.error("register-global-edge failed: %s", exc)
            return EXIT_ERROR
        return EXIT_OK

    if args.command == "detect-project-duplicates":
        try:
            if args.write and args.vault is None:
                raise XprojDuplicateError(
                    "detect-project-duplicates --write requires --vault"
                )
            payload = json.loads(args.projects.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise XprojDuplicateError("projects-not-object")
            raw = payload.get("projects")
            if not isinstance(raw, list):
                raise XprojDuplicateError("projects-not-array")
            projects: list[dict[str, object]] = []
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    raise XprojDuplicateError(f"project-not-object:{index}")
                projects.append(item)
            dup_result = detect_project_duplicates(
                projects,
                approved_monorepo_roots=args.approved_monorepo_roots or None,
            )
            dup_written: list[str] = []
            if args.write:
                assert args.vault is not None
                dup_written = write_duplicate_outputs(dup_result, vault=args.vault)
            summary = inspect_duplicate_detection(dup_result)
            print(json.dumps(summary, indent=2, sort_keys=True))
            print(f"review_candidates: {dup_result.review_count}")
            print(f"rejects: {dup_result.reject_count}")
            print("authority: derived")
            print("autocollapse: false")
            if dup_written:
                print(f"written: {len(dup_written)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _log.error("detect-project-duplicates failed: %s", exc)
            return EXIT_ERROR
        return EXIT_OK

    if args.command == "retention":
        if args.retention_command == "apply":
            try:
                report = apply_event_retention(
                    args.vault,
                    max_packages=args.max_packages,
                    max_bytes=args.max_bytes,
                    dry_run=args.dry_run,
                )
            except (RetentionError, OSError, ValueError, TypeError) as exc:
                _log.error("retention apply failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"status: {report['status']}")
                print(f"units_removed: {report['counts']['units_removed']}")
                print(
                    f"report: {args.vault / 'generated' / 'ops' / 'retention-report.json'}"
                )
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown retention command: {args.retention_command}"
        )

    if args.command == "revocation":
        if args.revocation_command == "revoke":
            try:
                revocation_index = revoke_receipt(
                    args.vault,
                    project_id=args.project,
                    event_id=args.event,
                    reason=args.reason,
                    status=args.status,
                    detail=args.detail,
                )
            except (RevocationError, OSError, ValueError, TypeError) as exc:
                _log.error("revocation revoke failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(
                    json.dumps(revocation_index, indent=2, sort_keys=True) + "\n",
                    end="",
                )
            else:
                entry = revocation_index["revocations"][-1]
                for row in revocation_index["revocations"]:
                    if row["unit_key"] == f"{args.project}/{args.event}":
                        entry = row
                        break
                print(f"status: {entry['status']}")
                print(f"reason: {entry['reason']}")
                print(f"unit_key: {entry['unit_key']}")
                print(
                    "index: "
                    f"{args.vault / 'generated' / 'ops' / 'receipt-revocations.json'}"
                )
            return EXIT_OK
        if args.revocation_command == "list":
            try:
                rows = list_revocations(args.vault)
                if args.json:
                    payload = {
                        "schema": "atlas.receipt_revocation.index.v1",
                        "revocations": rows,
                        "inventory": inventory_with_revocations(args.vault),
                    }
                    print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
                else:
                    print(f"revocations: {len(rows)}")
                    for row in rows:
                        print(
                            f"  {row['unit_key']} {row['status']} ({row['reason']})"
                        )
            except (RevocationError, OSError, ValueError, TypeError) as exc:
                _log.error("revocation list failed: %s", exc)
                return EXIT_ERROR
            return EXIT_OK
        if args.revocation_command == "status":
            try:
                disposition = receipt_trust_disposition(
                    args.vault, project_id=args.project, event_id=args.event
                )
                payload = {
                    "unit_key": f"{args.project}/{args.event}",
                    "disposition": disposition,
                }
                if args.json:
                    print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
                else:
                    print(f"unit_key: {payload['unit_key']}")
                    print(f"disposition: {disposition}")
            except (RevocationError, OSError, ValueError, TypeError) as exc:
                _log.error("revocation status failed: %s", exc)
                return EXIT_ERROR
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown revocation command: {args.revocation_command}"
        )

    if args.command == "schema":
        if args.schema_command == "compat":
            try:
                report = scan_compat(args.vault)
            except (SchemaCompatError, OSError, ValueError, TypeError) as exc:
                _log.error("schema compat failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"status: {report['status']}")
                print(f"scanned: {report['counts']['scanned']}")
                print(
                    "report: "
                    f"{args.vault / 'generated' / 'ops' / 'schema-compat-report.json'}"
                )
            return EXIT_OK if report["status"] in {"ok", "dry-run"} else EXIT_ERROR
        if args.schema_command == "migrate":
            try:
                report = migrate_dry_run(args.vault)
            except (SchemaCompatError, OSError, ValueError, TypeError) as exc:
                _log.error("schema migrate dry-run failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"status: {report['status']}")
                print(f"mode: {report['mode']}")
                print(
                    f"migrate_candidate: {report['counts']['migrate_candidate']}"
                )
                print(
                    "report: "
                    f"{args.vault / 'generated' / 'ops' / 'schema-compat-report.json'}"
                )
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown schema command: {args.schema_command}"
        )

    if args.command == "lifecycle":
        if args.lifecycle_command == "certify":
            try:
                report = run_fixture_lifecycle_certification(
                    args.work_root,
                    report_vault=args.report_vault,
                )
            except (LifecycleCertError, OSError, ValueError, TypeError) as exc:
                _log.error("lifecycle certify failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"status: {report['status']}")
                print(f"passed: {report['counts']['passed']}")
                print(f"failed: {report['counts']['failed']}")
                print("estate_pilot_passed: false")
                if args.report_vault is not None:
                    print(
                        "report: "
                        f"{args.report_vault / 'generated' / 'ops' / 'lifecycle-cert-report.json'}"
                    )
            return EXIT_OK if report["status"] == "certified" else EXIT_ERROR
        parser.error(  # pragma: no cover
            f"unknown lifecycle command: {args.lifecycle_command}"
        )

    if args.command == "adv":
        if args.adv_command == "certify":
            try:
                report = run_fixture_adv_release_certification(
                    args.work_root,
                    report_vault=args.report_vault,
                )
            except (AdvReleaseCertError, OSError, ValueError, TypeError) as exc:
                _log.error("adv certify failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"status: {report['status']}")
                print(f"passed: {report['counts']['passed']}")
                print(f"failed: {report['counts']['failed']}")
                print("release_certified: false")
                print("estate_pilot_passed: false")
                print("web_application_accepted: false")
                if args.report_vault is not None:
                    report_path = (
                        args.report_vault
                        / "generated"
                        / "ops"
                        / "adv-release-cert-report.json"
                    )
                    print(f"report: {report_path}")
            return EXIT_OK if report["status"] == "certified" else EXIT_ERROR
        parser.error(  # pragma: no cover
            f"unknown adv command: {args.adv_command}"
        )

    if args.command == "sync":
        if args.sync_command == "registry":
            if args.sync_registry_command == "dry-run":
                try:
                    document = build_dry_run_registry(
                        explicit_roots=args.roots,
                        vault_identity=args.vault_identity,
                        allowed_root_prefixes=args.allowed_prefixes,
                    )
                    path = write_dry_run_registry(args.vault, document)
                except (WorkspaceRegistryError, OSError, ValueError, TypeError) as exc:
                    _log.error("sync registry dry-run failed: %s", exc)
                    return EXIT_ERROR
                if args.json:
                    print(json.dumps(document, indent=2, sort_keys=True) + "\n", end="")
                else:
                    print(f"projects: {len(document['projects'])}")
                    print(f"quarantine: {len(document['quarantine'])}")
                    print("production_sync_certified: false")
                    print("estate_pilot_passed: false")
                    print(f"report: {path}")
                return EXIT_OK
            parser.error(  # pragma: no cover
                f"unknown sync registry command: {args.sync_registry_command}"
            )
        parser.error(  # pragma: no cover
            f"unknown sync command: {args.sync_command}"
        )

    if args.command == "snapshot":
        try:
            if args.verify:
                if args.bundle is None:
                    raise BackupError("snapshot --verify requires --bundle")
                result = verify_bundle(args.bundle)
                print(f"verified: {result['snapshot_id']}")
                print(f"vault_logical_id: {result['vault_logical_id']}")
                print(f"members: {result['member_count']}")
                return EXIT_OK
            if args.vault is None or args.output is None:
                raise BackupError("snapshot create requires --vault and --output")
            result = create_snapshot(
                args.vault,
                args.output,
                cp=args.cp,
                include_d5=args.include_d5,
            )
        except (BackupError, OSError, ValueError, TypeError) as exc:
            _log.error("snapshot failed: %s", exc)
            return EXIT_ERROR
        print(f"bundle: {result['bundle']}")
        print(f"snapshot_id: {result['snapshot_id']}")
        print(f"vault_logical_id: {result['vault_logical_id']}")
        print(f"members: {result['member_count']}")
        print(f"domains: {','.join(result['domains_included'])}")
        return EXIT_OK

    if args.command == "restore":
        try:
            result = restore_bundle(
                args.bundle,
                args.output,
                tier=args.tier,
                expected_vault_logical_id=args.expect_vault_logical_id,
                scaffold=args.scaffold,
            )
        except (BackupError, OSError, ValueError, TypeError) as exc:
            _log.error("restore failed: %s", exc)
            return EXIT_ERROR
        print(f"restored: {result['target']}")
        print(f"snapshot_id: {result['snapshot_id']}")
        print(f"vault_logical_id: {result['vault_logical_id']}")
        print(f"members: {result['member_count']}")
        print(f"tier: {result['tier']}")
        return EXIT_OK

    if args.command == "compat":
        if args.compat_command == "verify":
            try:
                anchor = load_compatibility_anchor(args.anchor)
            except (CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("compat verify failed: %s", exc)
                return EXIT_ERROR
            payload = anchor.as_dict()
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"snapshot_id: {anchor.snapshot_id}")
                print(f"tag: {anchor.tag}")
                print(f"software_freeze_head: {anchor.software_freeze_head}")
                print(f"software_freeze_tree: {anchor.software_freeze_tree}")
                print(f"release_certified: {anchor.release_certified}")
                print("one_dot_oh_wins_conflicts: true")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown compat command: {args.compat_command}"
        )

    if args.command == "kf2":
        if args.kf2_command == "namespace":
            try:
                ns_record = register_namespace(
                    args.vault,
                    namespace_id=args.namespace_id,
                    display_name=args.display_name,
                    notes=args.notes,
                )
            except (Kf2Error, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("kf2 namespace failed: %s", exc)
                return EXIT_ERROR
            payload = ns_record.as_dict()
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"namespace_id: {ns_record.namespace_id}")
                print(f"compat_snapshot_id: {payload['compat_snapshot_id']}")
            return EXIT_OK
        if args.kf2_command == "entity":
            try:
                entity_record = register_entity(
                    args.vault,
                    entity_id=args.entity_id,
                    namespace_id=args.namespace_id,
                    display_name=args.display_name,
                    xproj_global_entity_id=args.xproj_global_id,
                    notes=args.notes,
                )
            except (Kf2Error, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("kf2 entity failed: %s", exc)
                return EXIT_ERROR
            payload = entity_record.as_dict()
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"entity_id: {entity_record.entity_id}")
                print(f"namespace_id: {entity_record.namespace_id}")
            return EXIT_OK
        if args.kf2_command == "rel":
            try:
                rel_record = register_relationship(
                    args.vault,
                    relationship_id=args.relationship_id,
                    from_entity_id=args.from_entity_id,
                    to_entity_id=args.to_entity_id,
                    relation_type=args.relation_type,
                    notes=args.notes,
                )
            except (Kf2Error, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("kf2 rel failed: %s", exc)
                return EXIT_ERROR
            payload = rel_record.as_dict()
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"relationship_id: {rel_record.relationship_id}")
                print(f"relation_type: {rel_record.relation_type}")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown kf2 command: {args.kf2_command}"
        )

    if args.command == "federation":
        if args.federation_command == "join":
            try:
                parsed_members: list[FederationMember] = []
                for raw in args.members:
                    parts = str(raw).split("|")
                    if len(parts) not in {3, 4}:
                        raise FederationError("federation-member-spec-invalid")
                    mid, root, role = parts[0], parts[1], parts[2]
                    if role not in {"primary", "member"}:
                        raise FederationError("federation-member-role-invalid")
                    project_val = parts[3] if len(parts) == 4 else None
                    parsed_members.append(
                        FederationMember(
                            member_id=mid,
                            vault_root=root,
                            role=role,  # type: ignore[arg-type]
                            project_id=project_val,
                        )
                    )
                report = build_join_inventory(
                    federation_id=args.federation_id,
                    members=parsed_members,
                    output_vault=args.vault,
                )
            except (FederationError, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("federation join failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                inventory_path = (
                    args.vault
                    / "generated"
                    / "federation"
                    / f"{report['federation_id']}-join-inventory.json"
                )
                print(f"federation_id: {report['federation_id']}")
                print(f"status: {report['status']}")
                if report.get("refusal_reason"):
                    print(f"refusal_reason: {report['refusal_reason']}")
                print(f"inventory: {inventory_path}")
            return EXIT_OK if report["status"] == "joined" else EXIT_ERROR
        parser.error(  # pragma: no cover
            f"unknown federation command: {args.federation_command}"
        )

    if args.command == "provider":
        if args.provider_command == "registry":
            try:
                adapters: list[ProviderAdapter] = []
                for raw in args.adapters or []:
                    parts = str(raw).split("|")
                    if len(parts) != 3:
                        raise ProviderError("provider-adapter-spec-invalid")
                    aid, provider, caps_raw = parts
                    caps = tuple(
                        item.strip()
                        for item in caps_raw.split(",")
                        if item.strip()
                    )
                    adapters.append(
                        ProviderAdapter(
                            adapter_id=aid,
                            provider=provider,  # type: ignore[arg-type]
                            capabilities=caps,  # type: ignore[arg-type]
                        )
                    )
                report = build_adapter_registry(args.vault, adapters=adapters)
            except (ProviderError, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("provider registry failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"adapters_enabled: {report['adapters_enabled']}")
                print(f"adapters: {len(report['adapters'])}")
                print(
                    "registry: "
                    f"{args.vault / 'generated' / 'ops' / 'provider-adapter-registry.json'}"
                )
            return EXIT_OK
        if args.provider_command == "quarantine":
            try:
                report = quarantine_provider_output(
                    args.vault,
                    envelope_id=args.envelope_id,
                    adapter_id=args.adapter_id,
                    payload_text=args.text,
                    adapters_enabled=bool(args.enable_adapters),
                )
            except (ProviderError, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("provider quarantine failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"envelope_id: {report['envelope_id']}")
                print(f"status: {report['status']}")
                print(
                    "findings_count: "
                    f"{report['secret_scan']['findings_count']}"
                )
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown provider command: {args.provider_command}"
        )

    if args.command == "kci":
        if args.kci_command == "request":
            try:
                report = build_compile_request(
                    request_id=args.request_id,
                    source_refs=list(args.source_refs),
                    subject_refs=list(args.subject_refs or []),
                    fixture_mode=bool(args.fixture_mode),
                    notes=args.notes,
                    output_vault=args.vault,
                )
            except (KciError, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("kci request failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                request_path = (
                    args.vault
                    / "generated"
                    / "kci"
                    / f"{report['request_id']}-compile-request.json"
                )
                print(f"request_id: {report['request_id']}")
                print(f"operation: {report['operation']}")
                print(f"path: {request_path}")
            return EXIT_OK
        if args.kci_command == "receipt":
            try:
                report = issue_compile_receipt(
                    receipt_id=args.receipt_id,
                    request_id=args.request_id,
                    status=args.status,
                    outcome_refs=list(args.outcome_refs or []),
                    refusal_reason=args.refusal_reason,
                    output_vault=args.vault,
                )
            except (KciError, CompatAnchorError, OSError, ValueError, TypeError) as exc:
                _log.error("kci receipt failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                receipt_path = (
                    args.vault
                    / "generated"
                    / "kci"
                    / f"{report['receipt_id']}-compile-receipt.json"
                )
                print(f"receipt_id: {report['receipt_id']}")
                print(f"status: {report['status']}")
                print(f"authority_promoted: {report['authority_promoted']}")
                print(f"path: {receipt_path}")
            return EXIT_OK if report["status"] == "accepted" else EXIT_ERROR
        parser.error(  # pragma: no cover
            f"unknown kci command: {args.kci_command}"
        )

    if args.command == "runtime":
        if args.runtime_command == "hybrid-retrieve":
            try:
                report = runtime_hybrid_retrieve(
                    args.vault,
                    kind=args.kind,
                    value=args.value,
                    project_id=args.project_id,
                    mode=args.mode,
                    cap=args.cap,
                    include_graph_slot=bool(args.include_graph_slot),
                )
            except Runtime22Error as exc:
                _log.error("runtime hybrid-retrieve failed: %s", exc)
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if args.json:
                print(runtime_package_to_json(report), end="")
            else:
                print(f"package_id: {report['package_id']}")
                print(f"candidates: {report['candidate_count']}")
                print(f"truncated: {report['truncated']}")
                print(f"truth_boundary: {report['truth_boundary']}")
            return EXIT_OK
        if args.runtime_command == "compile-context":
            try:
                raw = json.loads(args.candidates.read_text(encoding="utf-8"))
                cand = raw.get("candidates") if isinstance(raw, dict) else None
                if not isinstance(cand, list):
                    raise Runtime22Error("context-candidates-file-invalid")
                report = runtime_compile_context(
                    args.vault,
                    pack_id=args.pack_id,
                    candidates=cand,
                    project_id=args.project_id,
                    budget=args.budget,
                    profile_id=args.profile_id,
                    write=bool(args.write),
                    on_overflow=args.on_overflow,
                    include_unresolved_conflicts=not bool(
                        args.exclude_unresolved_conflicts
                    ),
                )
            except (OSError, UnicodeError, json.JSONDecodeError, Runtime22Error) as exc:
                _log.error("runtime compile-context failed: %s", exc)
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if args.json:
                print(runtime_package_to_json(report), end="")
            else:
                print(f"package_id: {report['package_id']}")
                print(f"pack_id: {report['pack_id']}")
                print(f"entries: {report['entry_count']}")
                print(f"truncated: {report['truncated']}")
                if report.get("output_path"):
                    print(f"path: {report['output_path']}")
                print(f"truth_boundary: {report['truth_boundary']}")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown runtime command: {args.runtime_command}"
        )

    if args.command == "kdiff":
        as_of: str | None = args.as_of
        from_ref: str | None = args.from_ref
        to_ref: str | None = args.to_ref
        subject_cap = args.subject_cap if args.subject_cap is not None else DEFAULT_SUBJECT_CAP
        if as_of is not None and (from_ref is not None or to_ref is not None):
            _log.error("kdiff --as-of is mutually exclusive with --from/--to")
            return EXIT_ERROR
        try:
            if as_of is not None:
                report = read_as_of(
                    args.vault,
                    project_id=args.project,
                    as_of_valid_time=as_of,
                    knowledge_compilation_id=args.compilation_id,
                    subject_cap=subject_cap,
                )
                serialize = kdiff_snapshot_to_json
            elif from_ref is not None and to_ref is not None:
                report = diff_knowledge(
                    args.vault,
                    project_id=args.project,
                    t1=from_ref,
                    t2=to_ref,
                    knowledge_compilation_id=args.compilation_id,
                    subject_cap=subject_cap,
                )
                serialize = kdiff_diff_to_json
            else:
                _log.error("kdiff requires either --as-of or both --from and --to")
                return EXIT_ERROR
        except KnowledgeDiffError as exc:
            _log.error("kdiff failed: %s", exc)
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if args.json:
            print(serialize(report), end="")
        else:
            print(f"package_id: {report['package_id']}")
            print(f"artifact_kind: {report['artifact_kind']}")
            print(f"status: {report['status']}")
            if report["artifact_kind"] == AS_OF_KIND:
                print(f"cells: {report['cell_count']}")
                print(f"unresolved: {len(report['unresolved'])}")
            else:
                print(f"changes: {report['change_count']}")
                print(f"unresolved_delta: {len(report['unresolved_delta'])}")
            print(f"truncated: {report['truncated']}")
            print(f"truth_boundary: {report['truth_boundary']}")
        return EXIT_OK
    if args.command == "ask2":
        kinds = tuple(args.kind_args) if args.kind_args else ("concept", "claim")
        try:
            answer = ask_atlas_2(
                args.vault,
                question=args.question,
                project_id=args.project,
                kinds=kinds,
                mode=args.mode,
                budget=args.budget,
                retrieval_cap=args.cap,
                legacy_scan=not bool(args.no_legacy_scan),
            )
        except Ask2Error as exc:
            _log.error("ask2 failed: %s", exc)
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if args.json:
            print(ask2_answer_to_json(answer), end="")
        else:
            print(f"status: {answer['status']}")
            print(f"evidence: {answer['evidence_count']}")
            print(f"freshness: {answer['FRESHNESS']['aggregate']}")
            print(f"conflicts: {answer['CONFLICTS']['unresolved_count']}")
            print(f"legacy_subordinate_matches: {answer['legacy_compatibility']['match_count']}")
            print(f"truth_boundary: {answer['truth_boundary']}")
        return EXIT_OK

    if args.command == "demo":
        if args.demo_command == "full":
            from project_atlas.full_product_demo import (
                receipt_to_public_dict,
                run_full_product_demo,
            )

            print(
                "TECHNICAL DEMO — NOT RELEASE CERTIFIED — "
                "NOT AUTHENTIC PILOT — DEMO_FIXTURE",
                file=sys.stderr,
            )
            # --reset currently always rematerializes inside the harness.
            _ = bool(getattr(args, "reset", False))
            demo_receipt = run_full_product_demo(
                Path.cwd(),
                work_root=getattr(args, "work_root", None),
                receipt_path=getattr(args, "receipt", None),
            )
            payload = receipt_to_public_dict(demo_receipt)
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(f"MAIN_HEAD: {demo_receipt.MAIN_HEAD}")
                print(f"ESTATE_FINGERPRINT: {demo_receipt.ESTATE_FINGERPRINT}")
                print(f"DISCOVER: {demo_receipt.DISCOVER}")
                print(f"INGEST: {demo_receipt.INGEST}")
                print(f"BUILD_INDEXES: {demo_receipt.BUILD_INDEXES}")
                print(f"VALIDATE: {demo_receipt.VALIDATE}")
                print(f"DEMO_READINESS_PERCENT: {demo_receipt.DEMO_READINESS_PERCENT}")
                print(f"FULL_LIVE_DEMO_READY: {demo_receipt.FULL_LIVE_DEMO_READY}")
                print(f"P0: {demo_receipt.P0} P1: {demo_receipt.P1}")
            return (
                EXIT_OK
                if (
                    demo_receipt.P0 == 0
                    and demo_receipt.P1 == 0
                    and demo_receipt.DISCOVER == "PASS"
                    and demo_receipt.INGEST == "PASS"
                    and demo_receipt.BUILD_INDEXES == "PASS"
                    and demo_receipt.VALIDATE == "PASS"
                )
                else EXIT_ERROR
            )
        parser.error(f"unknown demo command: {args.demo_command}")

    if args.command == "context-pack":
        if args.context_pack_command == "build":
            try:
                pointers: list[ProvenancePointer] = []
                for raw in args.provenance:
                    parts = str(raw).split("|", 1)
                    if len(parts) != 2:
                        raise ContextPackError("context-provenance-spec-invalid")
                    kind, ref = parts[0], parts[1]
                    allowed = {
                        "source",
                        "receipt",
                        "index",
                        "claim",
                        "concept",
                        "other",
                    }
                    if kind not in allowed:
                        raise ContextPackError("context-provenance-kind-invalid")
                    pointers.append(
                        ProvenancePointer(ref=ref, kind=kind)  # type: ignore[arg-type]
                    )
                parsed_entries: list[ContextEntry] = []
                for raw in args.entries or []:
                    parts = str(raw).split("|")
                    if len(parts) not in {2, 3}:
                        raise ContextPackError("context-entry-spec-invalid")
                    label = parts[2] if len(parts) == 3 else None
                    parsed_entries.append(
                        ContextEntry(
                            entry_id=parts[0],
                            ref=parts[1],
                            label=label,
                        )
                    )
                report = build_context_pack(
                    pack_id=args.pack_id,
                    provenance_pointers=pointers,
                    entries=parsed_entries,
                    notes=args.notes,
                    output_vault=args.vault,
                )
            except (
                ContextPackError,
                CompatAnchorError,
                OSError,
                ValueError,
                TypeError,
            ) as exc:
                _log.error("context-pack build failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                pack_path = (
                    args.vault
                    / "generated"
                    / "context"
                    / f"{report['pack_id']}-context-pack.json"
                )
                print(f"pack_id: {report['pack_id']}")
                print(f"fixture_safe: {report['fixture_safe']}")
                print(f"path: {pack_path}")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown context-pack command: {args.context_pack_command}"
        )

    if args.command == "twin-fixture":
        if args.twin_fixture_command == "build":
            try:
                twin_rows: list[TwinProjectRow] = []
                for raw in args.projects or []:
                    parts = str(raw).split("|")
                    if len(parts) not in {2, 3}:
                        raise TwinFixtureError("twin-fixture-project-spec-invalid")
                    project_id, display_name = parts[0], parts[1]
                    health = parts[2].strip() if len(parts) == 3 else "unknown"
                    if health not in {"unknown", "degraded", "healthy"}:
                        raise TwinFixtureError("twin-fixture-health-invalid")
                    twin_rows.append(
                        TwinProjectRow(
                            project_id=project_id,
                            display_name=display_name,
                            health=health,  # type: ignore[arg-type]
                        )
                    )
                report = build_twin_projection_fixture(
                    args.vault,
                    projection_id=args.projection_id,
                    projects=twin_rows,
                    authentic_pilot_roots=int(args.authentic_pilot_roots),
                )
            except (
                TwinFixtureError,
                CompatAnchorError,
                OSError,
                ValueError,
                TypeError,
            ) as exc:
                _log.error("twin-fixture build failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"projection_id: {report['projection_id']}")
                print(f"estate_pilot_passed: {report['estate_pilot_passed']}")
                print(f"twin_production_ready: {report['twin_production_ready']}")
                print(f"twin_001_status: {report['twin_001_status']}")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown twin-fixture command: {args.twin_fixture_command}"
        )

    if args.command == "openai-import":
        if args.openai_import_command == "parse":
            try:
                sample = args.sample if args.sample is not None else default_sample_path()
                report = build_openai_import_fixture_receipt(
                    args.vault,
                    receipt_id=args.receipt_id,
                    sample_path=sample,
                    adapter_id=args.adapter_id,
                    quarantine=not bool(args.no_quarantine),
                    adapters_enabled=not bool(args.disable_adapters),
                )
            except (
                OpenAIImportFixtureError,
                ProviderError,
                CompatAnchorError,
                OSError,
                ValueError,
                TypeError,
            ) as exc:
                _log.error("openai-import parse failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"receipt_id: {report['receipt_id']}")
                print(f"status: {report['status']}")
                print(f"live_api: {report['live_api']}")
                print(f"turn_count: {report['turn_count']}")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown openai-import command: {args.openai_import_command}"
        )

    if args.command == "live":
        if args.live_command == "api-serve":
            try:
                server = serve_api(args.vault, host=args.host, port=args.port)
            except (ApiServerError, AuthzError, CompatAnchorError, OSError, ValueError) as exc:
                _log.error("live api-serve failed: %s", exc)
                return EXIT_ERROR
            creds = server.atlas_session.credentials
            # SEC-009 / SEC-ADV004-B-002: prefer token file; redact when redirected.
            print(
                f"LIVE_API listening on {args.host}:{args.port}",
                file=sys.stderr,
            )
            print(
                "SEC-009 session auth required: Authorization: Bearer <token>",
                file=sys.stderr,
            )
            publish_api_session_credentials(creds)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                return EXIT_OK
            return EXIT_OK
        if args.live_command == "mcp-invoke":
            try:
                report = invoke_mcp_tool(args.vault, args.tool)
            except (McpServerError, AuthzError, CompatAnchorError, OSError, ValueError) as exc:
                _log.error("live mcp-invoke failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"tool_id: {report['tool_id']}")
                print(f"live_mcp_read: {report['live_mcp_read']}")
            return EXIT_OK
        if args.live_command == "oai-import":
            try:
                report = import_openai_export(
                    args.vault,
                    args.export,
                    import_id=args.import_id,
                )
            except (
                OpenAIRealImportError,
                ProviderError,
                AuthzError,
                CompatAnchorError,
                OSError,
                ValueError,
            ) as exc:
                _log.error("live oai-import failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"import_id: {report['import_id']}")
                print(f"real_openai_export_import: {report['real_openai_export_import']}")
                print(f"live_openai_api: {report['live_openai_api']}")
            return EXIT_OK
        if args.live_command == "sched-arm":
            try:
                report = arm_scheduler(args.vault, arm_id=args.arm_id)
            except (SchedulerLiveError, AuthzError, CompatAnchorError, OSError, ValueError) as exc:
                _log.error("live sched-arm failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"arm_id: {report['arm_id']}")
                print(f"armed: {report['armed']}")
            return EXIT_OK
        if args.live_command == "sched-dispatch":
            try:
                # SEC-ADV004-B-001: no CLI self-grant; require ATLAS_CLI_ELEVATE_CAPS.
                op = require_cli_elevated_operator(
                    "local-operator-dispatch",
                    required={"scheduler.dispatch"},
                )
                report = dispatch_supervised_job(
                    args.vault,
                    arm_id=args.arm_id,
                    job=args.job,
                    operator=op,
                )
            except (SchedulerLiveError, AuthzError, CompatAnchorError, OSError, ValueError) as exc:
                _log.error("live sched-dispatch failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"job: {report['job']}")
                print(f"exit_code: {report['exit_code']}")
                print(
                    "live_supervised_scheduler: "
                    f"{report['live_supervised_scheduler']}"
                )
            return EXIT_OK
        if args.live_command == "pilot-prep":
            try:
                report = write_pilot_prep_report(
                    args.vault,
                    report_id=args.report_id,
                )
            except (
                PilotAuthPrepError,
                AuthzError,
                CompatAnchorError,
                OSError,
                ValueError,
            ) as exc:
                _log.error("live pilot-prep failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"authentic_found: {report['authentic_found']}")
                print(f"escalation_required: {report['escalation_required']}")
                print(f"pilot_pass: {report['pilot_pass']}")
                if report.get("wake_event"):
                    print(f"wake_event: {report['wake_event']}")
            return EXIT_OK
        if args.live_command == "oai-responses-poc":
            try:
                report = run_openai_responses_poc(
                    args.vault,
                    run_id=args.run_id,
                    prompt=args.prompt,
                    model=args.model,
                    force_offline=bool(args.force_offline),
                )
            except (
                OpenAIResponsesPocError,
                AuthzError,
                CompatAnchorError,
                ProviderError,
                OSError,
                ValueError,
            ) as exc:
                _log.error("live oai-responses-poc failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"smoke_status: {report['smoke_status']}")
                print(f"live_smoke: {report['live_smoke']}")
                print(f"llm_authority: {report['llm_authority']}")
                print(f"release_blocking: {report['release_blocking']}")
            return EXIT_OK
        if args.live_command == "perf-baseline":
            try:
                report = run_perf_baselines(
                    args.vault,
                    baseline_id=args.baseline_id,
                    iterations=int(args.iterations),
                )
            except (
                PerfBaselineError,
                CompatAnchorError,
                OSError,
                ValueError,
            ) as exc:
                _log.error("live perf-baseline failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                snap = report["measurements"]["app_service_snapshot_ms"]
                print(f"baseline_id: {report['baseline_id']}")
                print(f"snapshot_max_ms: {snap['max_ms']}")
                print(f"release_blocking: {report['release_blocking']}")
            return EXIT_OK
        if args.live_command == "l3-loop":
            try:
                # SEC-ADV004-B-001: no CLI self-grant; require ATLAS_CLI_ELEVATE_CAPS.
                op = require_cli_elevated_operator(
                    "local-operator-l3",
                    required={"autonomy.l3", "scheduler.dispatch"},
                )
                report = run_bounded_l3_loop(
                    args.vault,
                    policy_id=args.policy_id,
                    jobs=list(args.jobs),
                    operator=op,
                )
            except (
                AutonomyL3Error,
                AuthzError,
                CompatAnchorError,
                SchedulerLiveError,
                OSError,
                ValueError,
            ) as exc:
                _log.error("live l3-loop failed: %s", exc)
                return EXIT_ERROR
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True) + "\n", end="")
            else:
                print(f"policy_id: {report['policy_id']}")
                print(f"jobs_run: {len(report['jobs_run'])}")
                print(f"promoted: {report['promoted']}")
            return EXIT_OK
        parser.error(  # pragma: no cover
            f"unknown live command: {args.live_command}"
        )

    if args.command == "orchestrator":
        if args.orchestrator_command == "validate-result":
            from project_atlas.orchestration import run_validate_result

            decision, exit_code = run_validate_result(
                path=getattr(args, "result", None),
                from_stdin=bool(getattr(args, "from_stdin", False)),
                stdin=sys.stdin,
            )
            print(json.dumps(decision.to_public_dict(), indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "route-result":
            from project_atlas.orchestration import run_route_result

            routed, exit_code = run_route_result(
                path=getattr(args, "result", None),
                from_stdin=bool(getattr(args, "from_stdin", False)),
                stdin=sys.stdin,
            )
            print(json.dumps(routed.to_public_dict(), indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "cursor-stage-result":
            from project_atlas.orchestration.cursor_bridge import run_cursor_stage_result

            report, exit_code = run_cursor_stage_result(
                path=getattr(args, "result", None),
                from_stdin=bool(getattr(args, "from_stdin", False)),
                stdin=sys.stdin,
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "cursor-ack":
            from project_atlas.orchestration.cursor_bridge import run_cursor_ack

            report, exit_code = run_cursor_ack(
                route_digest_value=str(getattr(args, "route_digest", "")),
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "cursor-status":
            from project_atlas.orchestration.cursor_bridge import run_cursor_status

            report, exit_code = run_cursor_status(
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "cursor-complete":
            from project_atlas.orchestration.cursor_bridge import run_cursor_complete

            report, exit_code = run_cursor_complete(
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-status":
            from project_atlas.orchestration.autonomy.cli import run_governor_status

            report, exit_code = run_governor_status(
                root=Path(getattr(args, "root", None) or Path.cwd()),
                trust_store=getattr(args, "trust_store", None),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-discover":
            from project_atlas.orchestration.autonomy.cli import run_governor_discover

            report, exit_code = run_governor_discover(
                root=Path(getattr(args, "root", None) or Path.cwd()),
                inventory_path=getattr(args, "inventory", None),
                trust_store=getattr(args, "trust_store", None),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-pilot":
            from project_atlas.orchestration.autonomy.cli import run_governor_pilot

            report, exit_code = run_governor_pilot(
                root=Path(getattr(args, "root", None) or Path.cwd()),
                evidence_dir=getattr(args, "evidence_dir", None),
                inventory_path=getattr(args, "inventory", None),
                trust_store=getattr(args, "trust_store", None),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-loop-tick":
            from project_atlas.orchestration.autonomy.cli import run_governor_loop_tick

            report, exit_code = run_governor_loop_tick(
                root=Path(getattr(args, "root", None) or Path.cwd()),
                trust_store=getattr(args, "trust_store", None),
                loop_store=getattr(args, "loop_store", None),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "sdk-auth-status":
            from project_atlas.orchestration.sdk.cli import run_auth_status

            report, exit_code = run_auth_status(
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-service-run":
            from project_atlas.orchestration.sdk.cli import run_governor_service

            report, exit_code = run_governor_service(
                root=Path(getattr(args, "root", None) or Path.cwd()),
                max_cycles=getattr(args, "max_cycles", None),
                poll_interval_sec=float(getattr(args, "poll_interval", 2.0) or 2.0),
                use_fake=bool(getattr(args, "fake_backend", False)),
                candidate_head=getattr(args, "candidate_head", None),
                pr_number=int(getattr(args, "pr", 429) or 429),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-service-stop":
            from project_atlas.orchestration.sdk.cli import run_supervisor_stop

            report, exit_code = run_supervisor_stop(
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "governor-resident-run":
            from project_atlas.orchestration.sdk.resident_driver import run_resident_loop
            from project_atlas.orchestration.sdk.resident_mission import persist_mission
            from project_atlas.orchestration.sdk.resident_status import load_status

            resident_root = Path(getattr(args, "root", None) or Path.cwd())
            persist_mission(resident_root)
            status = run_resident_loop(
                resident_root,
                max_ticks=getattr(args, "max_ticks", None),
            )
            if not bool(getattr(args, "detached_worker", False)):
                print(json.dumps(status.model_dump(mode="json"), indent=2, sort_keys=True))
            else:
                final = load_status(resident_root)
                print(
                    json.dumps(
                        {"ok": True, "ticks": final.DETACHED_SCHEDULER_TICK_COUNT},
                        sort_keys=True,
                    )
                )
            return EXIT_OK
        if args.orchestrator_command == "continuation-broker":
            from project_atlas.orchestration.autonomy.continuation_broker import (
                SuccessorKind,
                consume_successor,
                enqueue_successor,
                status_report,
            )

            broker_root = Path(getattr(args, "root", None) or Path.cwd())
            action = str(getattr(args, "broker_action", ""))
            try:
                if action == "status":
                    broker_status = status_report(broker_root)
                    print(json.dumps(broker_status, indent=2, sort_keys=True))
                    return EXIT_OK if broker_status.get("ok") else EXIT_ERROR
                cycle_id = str(getattr(args, "cycle_id", "") or "")
                if action == "consume":
                    broker_state = consume_successor(broker_root, cycle_id)
                    print(
                        json.dumps(
                            broker_state.model_dump(mode="json"),
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    return EXIT_OK
                kind = SuccessorKind(str(getattr(args, "kind", "CHECKPOINT_CONTINUE")))
                broker_enqueue = enqueue_successor(
                    broker_root,
                    cycle_id=cycle_id,
                    kind=kind,
                    trusted_main=str(getattr(args, "trusted_main", "") or ""),
                    trusted_tree=str(getattr(args, "trusted_tree", "") or ""),
                )
                print(
                    json.dumps(
                        broker_enqueue.model_dump(mode="json"),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return EXIT_OK
            except Exception as exc:
                code = getattr(exc, "code", str(exc))
                print(json.dumps({"ok": False, "error": code, "execution_authorized": False}))
                return EXIT_ERROR
        if args.orchestrator_command == "dispatch-once":
            from project_atlas.orchestration.dispatcher import (
                DispatcherConfig,
                run_cli_dispatch_once,
            )

            report, exit_code = run_cli_dispatch_once(
                root=Path(getattr(args, "root", None) or Path.cwd()),
                config=DispatcherConfig(
                    lease_id=getattr(args, "lease_id", None),
                    bound_package_id=getattr(args, "bound_package_id", None),
                    base_main=getattr(args, "base_main", None),
                    candidate_head=getattr(args, "candidate_head", None),
                    candidate_tree=getattr(args, "candidate_tree", None),
                ),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "dispatch-status":
            from project_atlas.orchestration.dispatcher import run_cli_dispatch_status

            report, exit_code = run_cli_dispatch_status(
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "dispatch-submit-result":
            from project_atlas.orchestration.dispatcher import run_cli_submit_result

            report, exit_code = run_cli_submit_result(
                dispatch_id=str(getattr(args, "dispatch_id", "")),
                path=getattr(args, "result", None),
                from_stdin=bool(getattr(args, "from_stdin", False)),
                stdin=sys.stdin,
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        if args.orchestrator_command == "dispatch-recover":
            from project_atlas.orchestration.dispatcher import run_cli_recover

            report, exit_code = run_cli_recover(
                dispatch_id=str(getattr(args, "dispatch_id", "")),
                root=Path(getattr(args, "root", None) or Path.cwd()),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return exit_code
        parser.error(  # pragma: no cover
            f"unknown orchestrator command: {args.orchestrator_command}"
        )

    if args.command in {"program", "agent"}:
        # One lifecycle interface, not a second one: these delegate straight
        # into the same functions `python -m project_atlas.orchestration
        # .program.cli` calls, so `atlas program ...` and the module entry
        # point cannot drift apart.
        from project_atlas.orchestration.program.cli import dispatch_cli, emit

        payload, exit_code = dispatch_cli(args)
        emit(payload)
        return exit_code

    from project_atlas.atlas3.cli import dispatch_atlas3

    atlas3_exit = dispatch_atlas3(args)
    if atlas3_exit is not None:
        return atlas3_exit

    parser.error(f"unknown command: {args.command}")  # pragma: no cover - argparse enforces


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""D-088 six-P1 security closure + CLI execution port + result plane."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_atlas.orchestration.sdk.models import PACKAGE_ID, AgentRole, SdkRuntimeError
from project_atlas.orchestration.sdk.result_plane import (
    ResultEnvelope,
    append_result,
    ingest_pending,
    transport_state,
)
from project_atlas.orchestration.sdk.security_gates import (
    BoundWorkerResult,
    GovernorLease,
    HostHighWater,
    TransientClass,
    WorkerBackend,
    advance_high_water,
    audit_payload,
    bind_worker_lineage,
    classify_transient_failure,
    enforce_allowed_paths,
    normalize_cli_identity,
    recovery_action,
    reject_host_rollback,
    reject_superseded_pr_mutation,
    require_valid_lease,
    six_p1_open_count,
    suppress_stale_directive,
    validate_result_binding,
)

PIN = "7e797468a2eca37c959920912b1fa264df4be638"
TREE = "3cb40645c343edf8f8ab95f6ddf3a819e2110ef2"
HEAD = "aeef781fddce9b92d06fe202b027d7036fd01121"


def _lease(**kwargs: object) -> GovernorLease:
    base = dict(
        lease_id="lease-test-1",
        role=AgentRole.REMEDIATOR,
        dag_generation=88,
        allowed_paths=("src/project_atlas/orchestration/sdk",),
        mutation_authorized=True,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    base.update(kwargs)
    return GovernorLease(**base)  # type: ignore[arg-type]


def test_six_p1_all_closed() -> None:
    # Without runtime proofs, helpers alone are PARTIAL (D-092).
    assert six_p1_open_count() == 6
    from project_atlas.orchestration.sdk.security_gates import SixP1RuntimeProofs

    proofs = SixP1RuntimeProofs(
        result_binding_runtime=True,
        lease_gating_runtime=True,
        allowed_paths_post_run=True,
        host_high_water_recovery=True,
        worker_lineage_persisted=True,
        transient_failure_parked=True,
    )
    assert six_p1_open_count(proofs) == 0
    payload = audit_payload(proofs)
    assert payload["six_p1_open_count"] == 0
    assert all(f["status"] == "CLOSED" for f in payload["findings"])


def test_result_binding_rejects_replay_foreign_wrong_gen() -> None:
    result = BoundWorkerResult(
        worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
        session_or_agent_id="cli-eb663bca-c0ad-4a65-bc20-b417cbffa287",
        run_id="6e9d47c3-219b-4021-855a-c06a1ffc7858",
        package_id=PACKAGE_ID,
        dag_node="IV-LIVE",
        dag_generation=88,
        role=AgentRole.INDEPENDENT_VERIFIER,
        lease_id="lease-iv-live-88",
        attempt=1,
        result_digest="a" * 64,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    ok = validate_result_binding(
        result,
        expected_backend=WorkerBackend.CURSOR_AGENT_CLI,
        expected_session=result.session_or_agent_id,
        expected_run=result.run_id,
        expected_package=PACKAGE_ID,
        expected_node="IV-LIVE",
        expected_generation=88,
        expected_role=AgentRole.INDEPENDENT_VERIFIER,
        expected_lease="lease-iv-live-88",
        expected_attempt=1,
        expected_digest="a" * 64,
        expected_head=HEAD,
        expected_tree=TREE,
    )
    assert ok.run_id == result.run_id
    with pytest.raises(SdkRuntimeError, match="wrong dag generation"):
        validate_result_binding(
            result.model_copy(update={"dag_generation": 1}),
            expected_backend=WorkerBackend.CURSOR_AGENT_CLI,
            expected_session=result.session_or_agent_id,
            expected_run=result.run_id,
            expected_package=PACKAGE_ID,
            expected_node="IV-LIVE",
            expected_generation=88,
            expected_role=AgentRole.INDEPENDENT_VERIFIER,
            expected_lease="lease-iv-live-88",
            expected_attempt=1,
            expected_digest="a" * 64,
        )
    with pytest.raises(SdkRuntimeError, match="replayed"):
        validate_result_binding(
            result,
            expected_backend=WorkerBackend.CURSOR_AGENT_CLI,
            expected_session=result.session_or_agent_id,
            expected_run=result.run_id,
            expected_package=PACKAGE_ID,
            expected_node="IV-LIVE",
            expected_generation=88,
            expected_role=AgentRole.INDEPENDENT_VERIFIER,
            expected_lease="lease-iv-live-88",
            expected_attempt=1,
            expected_digest="a" * 64,
            seen_digests={"a" * 64},
        )
    with pytest.raises(SdkRuntimeError, match="foreign"):
        foreign = result.model_copy(
            update={
                "session_or_agent_id": "cli-deadbeef-0000-0000-0000-000000000000",
            }
        )
        validate_result_binding(
            foreign,
            expected_backend=WorkerBackend.CURSOR_AGENT_CLI,
            expected_session=result.session_or_agent_id,
            expected_run=result.run_id,
            expected_package=PACKAGE_ID,
            expected_node="IV-LIVE",
            expected_generation=88,
            expected_role=AgentRole.INDEPENDENT_VERIFIER,
            expected_lease="lease-iv-live-88",
            expected_attempt=1,
            expected_digest="a" * 64,
        )


def test_lease_gate_rejects_leaseless_and_expired() -> None:
    with pytest.raises(SdkRuntimeError, match="missing lease"):
        require_valid_lease(
            None,
            role=AgentRole.REMEDIATOR,
            dag_generation=88,
            mutating=True,
        )
    lease = _lease(expired=True)
    with pytest.raises(SdkRuntimeError, match="expired"):
        require_valid_lease(lease, role=AgentRole.REMEDIATOR, dag_generation=88, mutating=True)
    good = _lease()
    assert (
        require_valid_lease(
            good, role=AgentRole.REMEDIATOR, dag_generation=88, mutating=True
        ).lease_id
        == "lease-test-1"
    )


def test_allowed_paths_attacks() -> None:
    allowed = ("src/project_atlas/orchestration/sdk",)
    enforce_allowed_paths(
        changed_paths=["src/project_atlas/orchestration/sdk/security_gates.py"],
        allowed_paths=allowed,
    )
    with pytest.raises(SdkRuntimeError, match=r"unauthorized"):
        enforce_allowed_paths(
            changed_paths=["src/project_atlas/orchestration/autonomy/leases.py"],
            allowed_paths=allowed,
        )
    with pytest.raises(SdkRuntimeError, match=r"unauthorized"):
        enforce_allowed_paths(
            changed_paths=["README.md"],
            allowed_paths=allowed,
        )
    with pytest.raises(SdkRuntimeError, match=r"metadata"):
        enforce_allowed_paths(
            changed_paths=[".git/config"],
            allowed_paths=allowed,
        )
    with pytest.raises(SdkRuntimeError, match=r"traversal|escape"):
        enforce_allowed_paths(
            changed_paths=["src/project_atlas/orchestration/sdk/../secrets.py"],
            allowed_paths=allowed,
        )
    with pytest.raises(SdkRuntimeError, match=r"insufficient"):
        enforce_allowed_paths(changed_paths=["x"], allowed_paths=())


def test_host_rollback_rejected(tmp_path: Path) -> None:
    mark = advance_high_water(tmp_path, dag_generation=88, event_sequence=10)
    assert mark.dag_generation == 88
    with pytest.raises(SdkRuntimeError, match=r"rollback"):
        reject_host_rollback(
            current=mark,
            proposed=HostHighWater(dag_generation=87, event_sequence=10),
        )
    with pytest.raises(SdkRuntimeError, match=r"rollback"):
        advance_high_water(tmp_path, dag_generation=80)


def test_agent_lineage_foreign_and_cross_worktree() -> None:
    a = bind_worker_lineage(
        identity="eb663bca-c0ad-4a65-bc20-b417cbffa287",
        backend=WorkerBackend.CURSOR_AGENT_CLI,
        workspace="/wt-a",
        repository="https://github.com/B0LK13/project-atlas",
        package_id=PACKAGE_ID,
        role=AgentRole.INDEPENDENT_VERIFIER,
        branch="feat/as-orch-continuation-broker-001",
        base_main=PIN,
        creation_generation=88,
    )
    assert a.identity.startswith("cli-")
    with pytest.raises(SdkRuntimeError, match=r"cross-worktree"):
        bind_worker_lineage(
            identity=a.identity,
            backend=WorkerBackend.CURSOR_AGENT_CLI,
            workspace="/wt-b",
            repository="https://github.com/B0LK13/project-atlas",
            package_id=PACKAGE_ID,
            role=AgentRole.INDEPENDENT_VERIFIER,
            branch="feat/as-orch-continuation-broker-001",
            base_main=PIN,
            creation_generation=88,
            expected=a,
        )
    expected_sdk = a.model_copy(
        update={"backend": WorkerBackend.CURSOR_SDK, "identity": "agent-expected"}
    )
    with pytest.raises(SdkRuntimeError, match=r"foreign"):
        bind_worker_lineage(
            identity="agent-not-this-session",
            backend=WorkerBackend.CURSOR_SDK,
            workspace="/wt-a",
            repository="https://github.com/B0LK13/project-atlas",
            package_id=PACKAGE_ID,
            role=AgentRole.INDEPENDENT_VERIFIER,
            branch="feat/as-orch-continuation-broker-001",
            base_main=PIN,
            creation_generation=88,
            expected=expected_sdk,
        )


def test_transient_failure_park_vs_auth() -> None:
    assert classify_transient_failure("connection reset by peer") == TransientClass.NETWORK
    assert recovery_action(TransientClass.NETWORK) == "PARK_BACKOFF"
    assert classify_transient_failure("timeout waiting") == TransientClass.TIMEOUT
    assert classify_transient_failure(TimeoutError()) == TransientClass.TIMEOUT
    assert recovery_action(classify_transient_failure(TimeoutError())) == "PARK_BACKOFF"
    assert classify_transient_failure("rate limit", status_code=429) == TransientClass.RATE_LIMIT
    assert classify_transient_failure("boom", status_code=503) == TransientClass.SERVER_5XX
    assert classify_transient_failure("missing_api_key") == TransientClass.AUTH_PERSISTENT
    assert recovery_action(TransientClass.AUTH_PERSISTENT) == "TRY_OTHER_BACKEND"


def test_pr428_mutation_and_stale_directive() -> None:
    with pytest.raises(SdkRuntimeError, match=r"STALE_LINEAGE"):
        reject_superseded_pr_mutation(target_pr=428)
    reject_superseded_pr_mutation(target_pr=429)
    assert (
        suppress_stale_directive(directive_pr=428, directive_head=None, live_pr=429, live_head=HEAD)
        == "STALE_DIRECTIVE_PR428"
    )
    assert (
        suppress_stale_directive(
            directive_pr=429,
            directive_head="0" * 40,
            live_pr=429,
            live_head=HEAD,
        )
        == "STALE_DIRECTIVE_HEAD_MOVED"
    )
    assert (
        suppress_stale_directive(directive_pr=429, directive_head=HEAD, live_pr=429, live_head=HEAD)
        is None
    )


def test_cli_identity_normalize() -> None:
    assert (
        normalize_cli_identity("eb663bca-c0ad-4a65-bc20-b417cbffa287")
        == "cli-eb663bca-c0ad-4a65-bc20-b417cbffa287"
    )


def test_result_plane_ingest_without_owner_relay(tmp_path: Path) -> None:
    binding = BoundWorkerResult(
        worker_backend=WorkerBackend.CURSOR_AGENT_CLI,
        session_or_agent_id="cli-eb663bca-c0ad-4a65-bc20-b417cbffa287",
        run_id="run-1",
        package_id=PACKAGE_ID,
        dag_node="ADV-LIVE",
        dag_generation=88,
        role=AgentRole.SECURITY_REVIEWER,
        lease_id="lease-adv",
        attempt=1,
        result_digest="b" * 64,
        candidate_head=HEAD,
        candidate_tree=TREE,
    )
    append_result(
        tmp_path,
        ResultEnvelope(source="ADV", binding=binding, payload={"new_p0": 0, "new_p1": 0}),
    )
    # File existence alone is not CLOSED (D-092).
    assert transport_state(tmp_path) == "OPEN"
    expected = {
        "worker_backend": WorkerBackend.CURSOR_AGENT_CLI.value,
        "session_or_agent_id": binding.session_or_agent_id,
        "run_id": binding.run_id,
        "package_id": PACKAGE_ID,
        "dag_node": "ADV-LIVE",
        "dag_generation": 88,
        "role": AgentRole.SECURITY_REVIEWER.value,
        "lease_id": "lease-adv",
        "attempt": 1,
        "result_digest": "b" * 64,
        "candidate_head": HEAD,
        "candidate_tree": TREE,
    }
    ingested = ingest_pending(tmp_path, expected=expected)
    assert len(ingested) == 1
    assert ingest_pending(tmp_path, expected=expected) == []
    assert transport_state(tmp_path) == "CLOSED"

"""F-RUNNER-1 `TRANSPORT_GRANT_EXPIRY_OBSERVABILITY` + F-RUNNER-4
`TRANSPORT_GRANT_PRE_EXPIRY_WARNING`.

Specification: docs/global/baseline/2026-10-03-RUNNER-AUTHORITY-INCIDENT.md
sections 4.1 and 4.2. Every transport refusal carries a stable non-secret
reason code in the audit log, the journal (stderr) and the health/status
surface, and still fails closed. OBSERVABILITY != AUTHORITY: nothing here may
make admission more permissive.

Deviation from the record, on purpose: the record proposes that an invalid
transport grant makes `health` exit 1 (degraded). `deploy-release.sh` rolls a
release back on ANY non-zero health exit, so that would let an expired grant
roll back an unrelated deploy. The transport check is therefore informational
and never changes status or exit code; the two record test names that assert a
verdict change are implemented under names that state what is actually true:

- `test_health_reports_transport_admission_expired_and_is_not_healthy`
  -> `test_health_reports_transport_admission_expired_without_changing_exit_code`
- `test_health_degraded_when_transport_grant_expires_within_window`
  -> `test_health_warns_when_transport_grant_expires_within_window`
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import make_config

from controller import cli
from controller import controller as controller_module
from controller.config import ConfigError, parse_config
from controller.controller import (
    JOURNAL_REPEAT_SECONDS,
    JOURNAL_WARNING_REPEAT_SECONDS,
    TRANSPORT_DISABLED,
    TRANSPORT_GRANT_EXHAUSTED,
    TRANSPORT_GRANT_EXPIRED,
    TRANSPORT_GRANT_EXPIRING,
    TRANSPORT_GRANT_INVALID,
    TRANSPORT_GRANT_NOT_CONFIGURED,
    TRANSPORT_GRANT_OK,
    TRANSPORT_GRANT_REGISTRY_UNAVAILABLE,
    TRANSPORT_GRANT_REVOKED,
    TRANSPORT_GRANT_SCOPE_MISMATCH,
    TRANSPORT_GRANT_UNKNOWN,
    TRANSPORT_REFUSAL_CODES,
    AdmissionJournal,
    Controller,
    transport_admission_state,
    transport_reason_code,
)
from controller.grants import (
    GRANT_EXHAUSTED,
    GRANT_REVOKED,
    GrantConsumedError,
    GrantError,
    GrantExpiredError,
    GrantScopeError,
    GrantStore,
    GrantUnknownError,
    ReadOnlyGrantReader,
)
from controller.health import health_json, run_health

INFRA = Path(__file__).resolve().parents[1]
GRANT = "grant-transport-e2"
REPO = "atlas-owner/atlas-repo"
# Markers that must never reach the journal, health or status surfaces.
SCOPE_MARKER = "SCOPE-CONTENT-MARKER"
REVISION_MARKER = "f00dREVISIONMARKER"
FAULT_MARKER = "FAULT-TEXT-MARKER-/var/lib/atlas-runner/state"


def _queued(run_id: int, attempt: int = 1, job_id: int = 1) -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "jobs": [{"id": job_id, "status": "queued", "labels": ["self-hosted"]}],
    }


def _controller(config, store, fake_docker, fake_github, fake_worker_manager, *, clock=None):
    kwargs = {"clock": clock} if clock is not None else {}
    return Controller(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        worker_manager=fake_worker_manager,
        sleeper=lambda *_: None,
        **kwargs,
    )


def _audit(store, event: str) -> list[dict]:
    rows = store._conn.execute(
        "SELECT task_id, detail_json FROM audit_log WHERE event = ? ORDER BY rowid", (event,)
    ).fetchall()
    return [{"task_id": r["task_id"], **json.loads(r["detail_json"] or "{}")} for r in rows]


def _lines(text: str, event: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith(f"atlas-runner {event} ")]


def _assert_fail_closed(store, fake_docker, fake_worker_manager, run_id: int) -> None:
    task_id = f"gh-{run_id}-1-1"
    assert store.get_task(task_id) is None
    assert store.find_execution_by_job(run_id, 1, 1) is None
    assert store.all_executions() == []
    assert store.count_active_workers() == 0
    assert fake_worker_manager.ran == []
    assert "run_worker" not in fake_docker.call_names()
    assert _audit(store, "transport_grant_validated") == []


class _BrokenRegistry:
    """Registry double whose every read fails like a SQLite fault."""

    def get(self, grant_id):
        raise sqlite3.OperationalError(FAULT_MARKER)

    def validate(self, grant_id, **_kwargs):
        raise sqlite3.OperationalError(FAULT_MARKER)


class _UncodedRegistry:
    """Registry double raising a GrantError with no mapped discriminator."""

    def get(self, grant_id):
        return None

    def validate(self, grant_id, **_kwargs):
        raise GrantError(f"unclassified {FAULT_MARKER}")


# -- scenario builders: each returns (config, grants) for one refusal code --------


def _s_disabled(workspace, store):
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000)  # valid grant must not bypass the config gate
    return make_config(workspace, transport_grant_id=GRANT, queued_transport_enabled=False), grants


def _s_not_configured(workspace, store):
    return make_config(workspace, transport_grant_id=None), GrantStore(store.db_path)


def _s_no_registry(workspace, store):
    return make_config(workspace, transport_grant_id=GRANT), None


def _s_registry_error(workspace, store):
    return make_config(workspace, transport_grant_id=GRANT), _BrokenRegistry()


def _s_unknown(workspace, store):
    return make_config(workspace, transport_grant_id=GRANT), GrantStore(store.db_path)


def _s_revoked(workspace, store):
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000)
    grants.revoke(GRANT)
    return make_config(workspace, transport_grant_id=GRANT), grants


def _s_expired(workspace, store):
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000, expires_at=1000.0)
    return make_config(workspace, transport_grant_id=GRANT), grants


def _s_exhausted(workspace, store):
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1)
    grants.consume(GRANT)
    return make_config(workspace, transport_grant_id=GRANT), grants


def _s_scope(workspace, store):
    grants = GrantStore(store.db_path)
    grants.issue(
        GRANT,
        budget=1000,
        repository=f"someone/{SCOPE_MARKER}",
        base_revision=REVISION_MARKER,
        scope={"note": SCOPE_MARKER},
    )
    return make_config(workspace, transport_grant_id=GRANT), grants


def _s_uncoded(workspace, store):
    return make_config(workspace, transport_grant_id=GRANT), _UncodedRegistry()


SCENARIOS = {
    "disabled": (_s_disabled, TRANSPORT_DISABLED),
    "not_configured": (_s_not_configured, TRANSPORT_GRANT_NOT_CONFIGURED),
    "no_registry": (_s_no_registry, TRANSPORT_GRANT_REGISTRY_UNAVAILABLE),
    "registry_error": (_s_registry_error, TRANSPORT_GRANT_REGISTRY_UNAVAILABLE),
    "unknown": (_s_unknown, TRANSPORT_GRANT_UNKNOWN),
    "revoked": (_s_revoked, TRANSPORT_GRANT_REVOKED),
    "expired": (_s_expired, TRANSPORT_GRANT_EXPIRED),
    "exhausted": (_s_exhausted, TRANSPORT_GRANT_EXHAUSTED),
    "scope_mismatch": (_s_scope, TRANSPORT_GRANT_SCOPE_MISMATCH),
}


def _refuse(name, workspace, store, fake_docker, fake_github, fake_worker_manager, run_id=700):
    build, _code = SCENARIOS.get(name, (_s_uncoded, None))
    config, grants = build(workspace, store)
    controller = _controller(config, store, fake_docker, fake_github, fake_worker_manager)
    if grants is not None:
        controller.attach_grants(grants)
    fake_github.queued = [_queued(run_id)]
    admitted = controller.admit_queued_jobs()
    return controller, config, grants, admitted


def test_scenarios_cover_the_whole_refusal_vocabulary():
    covered = {code for _build, code in SCENARIOS.values()} | {TRANSPORT_GRANT_INVALID}
    assert covered == TRANSPORT_REFUSAL_CODES


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_reason_code_reaches_audit_journal_health_and_fails_closed(
    name, workspace, store, fake_docker, fake_github, fake_worker_manager, capsys
):
    code = SCENARIOS[name][1]
    controller, config, grants, admitted = _refuse(
        name, workspace, store, fake_docker, fake_github, fake_worker_manager
    )
    # fail closed: nothing admitted, no task row, no execution row, no worker
    assert admitted == []
    _assert_fail_closed(store, fake_docker, fake_worker_manager, 700)

    # audit: stable code beside the legacy free-text reason
    rows = _audit(store, "blocked_authority")
    assert len(rows) == 1
    assert rows[0]["task_id"] == "gh-700-1-1"
    assert rows[0]["reason_code"] == code
    assert isinstance(rows[0]["reason"], str) and rows[0]["reason"]

    # journal: exactly one line, exactly these fields
    err = capsys.readouterr().err
    grant_field = GRANT if (config.transport_grant_id and name != "disabled") else "-"
    assert _lines(err, "admission_refused") == [
        f"atlas-runner admission_refused reason_code={code} grant_id={grant_field}"
        " task_id=gh-700-1-1"
    ]

    # health: same code, never OK, exit code untouched
    store.heartbeat(1)
    report, exit_code = run_health(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        now=store.last_heartbeat(),
        grants=grants,
    )
    check = report["checks"]["transport_admission"]
    assert check["code"] == code
    assert check["ok"] is (None if name == "disabled" else False)
    assert (report["status"], exit_code) == ("healthy", 0)
    assert report["advisories"] == ([] if name == "disabled" else [f"transport_admission:{code}"])
    # pure read helper agrees with what admission did
    assert controller.transport_admission_state()["code"] == code


# -- record §4.1 named acceptance tests -------------------------------------------


def test_expired_transport_grant_refuses_with_reason_code_expired(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("expired", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason_code"] == "TRANSPORT_GRANT_EXPIRED"
    assert row["grant_id"] == GRANT
    # compatibility: the historical free-text reason is byte-identical
    assert row["reason"] == f"transport_grant_invalid: grant '{GRANT}' expired"


def test_unknown_transport_grant_refuses_with_reason_code_unknown(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("unknown", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason_code"] == "TRANSPORT_GRANT_UNKNOWN"
    assert row["reason"] == f"transport_grant_invalid: unknown grant '{GRANT}'"


def test_revoked_transport_grant_refuses_with_reason_code_revoked(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("revoked", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason_code"] == "TRANSPORT_GRANT_REVOKED"
    assert row["reason"] == f"transport_grant_invalid: grant '{GRANT}' status=revoked"


def test_exhausted_transport_grant_refuses_with_reason_code_exhausted(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("exhausted", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason_code"] == "TRANSPORT_GRANT_EXHAUSTED"
    assert row["reason"] == f"transport_grant_invalid: grant '{GRANT}' budget exhausted"


def test_repository_mismatch_refuses_with_reason_code_scope_mismatch(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("scope_mismatch", workspace, store, fake_docker, fake_github, fake_worker_manager)
    assert _audit(store, "blocked_authority")[0]["reason_code"] == "TRANSPORT_GRANT_SCOPE_MISMATCH"


def test_unconfigured_transport_grant_refuses_with_reason_code_not_configured(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("not_configured", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason_code"] == "TRANSPORT_GRANT_NOT_CONFIGURED"
    assert row["reason"] == "no_transport_grant_configured"
    assert "grant_id" not in row


def test_disabled_transport_keeps_legacy_reason_and_adds_code(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("disabled", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row == {
        "task_id": "gh-700-1-1",
        "reason": "queued_transport_disabled",
        "reason_code": "TRANSPORT_DISABLED",
    }


def test_registry_error_is_not_reported_as_authority_refusal(
    workspace, store, fake_docker, fake_github, fake_worker_manager, capsys
):
    """F-RUNNER-7 slice: a SQLite fault is an infrastructure fault, with its own
    code, and its exception text never reaches any surface."""
    _c, config, grants, admitted = _refuse(
        "registry_error", workspace, store, fake_docker, fake_github, fake_worker_manager
    )
    assert admitted == []
    _assert_fail_closed(store, fake_docker, fake_worker_manager, 700)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason_code"] == "TRANSPORT_GRANT_REGISTRY_UNAVAILABLE"
    assert row["reason"] == "transport_grant_registry_unavailable: OperationalError"
    assert not row["reason"].startswith("transport_grant_invalid")
    err = capsys.readouterr().err
    report, _code = run_health(
        config=config, store=store, docker=fake_docker, github=fake_github, grants=grants
    )
    check = report["checks"]["transport_admission"]
    # UNKNOWN must not render as OK, and is distinguishable from a refusal.
    assert (check["state"], check["ok"]) == ("unknown", False)
    for surface in (err, health_json(report), json.dumps(row)):
        assert FAULT_MARKER not in surface


def test_no_registry_attached_keeps_legacy_reason(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    _refuse("no_registry", workspace, store, fake_docker, fake_github, fake_worker_manager)
    row = _audit(store, "blocked_authority")[0]
    assert row["reason"] == "grant_registry_required"
    assert row["reason_code"] == "TRANSPORT_GRANT_REGISTRY_UNAVAILABLE"


def test_unclassified_grant_error_gets_the_defensive_fallback_code(
    workspace, store, fake_docker, fake_github, fake_worker_manager, capsys
):
    _c, _config, _grants, admitted = _refuse(
        "uncoded", workspace, store, fake_docker, fake_github, fake_worker_manager
    )
    assert admitted == []
    _assert_fail_closed(store, fake_docker, fake_worker_manager, 700)
    assert _audit(store, "blocked_authority")[0]["reason_code"] == "TRANSPORT_GRANT_INVALID"
    err = capsys.readouterr().err
    assert "reason_code=TRANSPORT_GRANT_INVALID" in err
    assert FAULT_MARKER not in err


def test_refusal_still_fails_closed_no_task_no_execution_no_worker(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    controller, _config, _grants, admitted = _refuse(
        "expired", workspace, store, fake_docker, fake_github, fake_worker_manager
    )
    assert admitted == []
    for _ in range(3):  # repeated polls never accumulate state or admit
        assert controller.poll_once() == []
    _assert_fail_closed(store, fake_docker, fake_worker_manager, 700)
    assert "generate_jitconfig" not in fake_github.calls
    # audit cadence is unchanged (one row per refused job per poll; F-RUNNER-6 is separate)
    assert len(_audit(store, "blocked_authority")) == 4


def test_refusal_emits_one_journal_line_per_reason_change(
    workspace, store, fake_docker, fake_github, fake_worker_manager, fake_clock, capsys
):
    """Journal rule: write on a change of (reason_code, grant_id), then at most
    once per JOURNAL_REPEAT_SECONDS; suppressed lines are counted in repeats=N."""
    config = make_config(workspace, transport_grant_id=GRANT)
    grants = GrantStore(store.db_path)
    controller = _controller(
        config, store, fake_docker, fake_github, fake_worker_manager, clock=fake_clock
    )
    controller.attach_grants(grants)
    fake_github.queued = [_queued(701), _queued(702)]

    for _ in range(5):  # 5 polls x 2 queued jobs = 10 refusals
        assert controller.admit_queued_jobs() == []
        fake_clock.advance(10)
    assert _lines(capsys.readouterr().err, "admission_refused") == [
        f"atlas-runner admission_refused reason_code=TRANSPORT_GRANT_UNKNOWN grant_id={GRANT}"
        " task_id=gh-701-1-1"
    ]

    # reason changes (unknown -> expired): a new line immediately
    grants.issue(GRANT, budget=1000, expires_at=1000.0)
    assert controller.admit_queued_jobs() == []
    assert controller.admit_queued_jobs() == []
    assert _lines(capsys.readouterr().err, "admission_refused") == [
        f"atlas-runner admission_refused reason_code=TRANSPORT_GRANT_EXPIRED grant_id={GRANT}"
        " task_id=gh-701-1-1"
    ]

    # same reason: silent until the repeat interval, then one line with the count
    fake_clock.advance(JOURNAL_REPEAT_SECONDS - 1)
    assert controller.admit_queued_jobs() == []
    assert _lines(capsys.readouterr().err, "admission_refused") == []
    fake_clock.advance(1)
    assert controller.admit_queued_jobs() == []
    assert _lines(capsys.readouterr().err, "admission_refused") == [
        f"atlas-runner admission_refused reason_code=TRANSPORT_GRANT_EXPIRED grant_id={GRANT}"
        " task_id=gh-701-1-1 repeats=5"
    ]
    # every refusal is still audited (the rate limit is local to the journal)
    assert len(_audit(store, "blocked_authority")) == 18


def test_poll_journals_expired_grant_even_when_no_job_is_queued(
    workspace, store, fake_docker, fake_github, fake_worker_manager, fake_clock, capsys
):
    config = make_config(workspace, transport_grant_id=GRANT)
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000, expires_at=1_759_503_600.0)  # 2025-10-03T15:00:00Z
    controller = _controller(
        config, store, fake_docker, fake_github, fake_worker_manager, clock=fake_clock
    )
    controller.attach_grants(grants)
    for _ in range(4):
        assert controller.poll_once() == []
    assert capsys.readouterr().err.splitlines() == [
        "atlas-runner transport_admission state=refusing reason_code=TRANSPORT_GRANT_EXPIRED"
        f" grant_id={GRANT} expires_at=2025-10-03T15:00:00Z"
    ]
    assert _audit(store, "blocked_authority") == []  # observation writes no audit row


def test_poll_journals_recovery_once_after_a_refusing_state(
    workspace, store, fake_docker, fake_github, fake_worker_manager, fake_clock, capsys
):
    config = make_config(workspace, transport_grant_id=GRANT)
    grants = GrantStore(store.db_path)
    controller = _controller(
        config, store, fake_docker, fake_github, fake_worker_manager, clock=fake_clock
    )
    controller.attach_grants(grants)
    controller.poll_once()  # unknown grant -> refusing
    grants.issue(GRANT, budget=1000)
    for _ in range(3):
        controller.poll_once()
    assert _lines(capsys.readouterr().err, "transport_admission") == [
        "atlas-runner transport_admission state=refusing reason_code=TRANSPORT_GRANT_UNKNOWN"
        f" grant_id={GRANT} expires_at=-",
        "atlas-runner transport_admission state=ok reason_code=TRANSPORT_GRANT_OK"
        f" grant_id={GRANT}",
    ]


def test_disabled_transport_is_silent_at_poll_level(
    workspace, store, fake_docker, fake_github, fake_worker_manager, capsys
):
    config = make_config(workspace, transport_grant_id=None, queued_transport_enabled=False)
    controller = _controller(config, store, fake_docker, fake_github, fake_worker_manager)
    for _ in range(3):
        controller.poll_once()
    assert capsys.readouterr().err == ""


def test_journal_failure_never_breaks_or_bypasses_admission(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    class _Closed:
        def write(self, _text):
            raise ValueError("I/O operation on closed file")

        def flush(self):
            raise OSError("closed")

    config, grants = _s_expired(workspace, store)
    controller = _controller(config, store, fake_docker, fake_github, fake_worker_manager)
    controller.attach_grants(grants)
    controller.journal = AdmissionJournal(stream=_Closed())
    fake_github.queued = [_queued(700)]
    assert controller.poll_once() == []
    _assert_fail_closed(store, fake_docker, fake_worker_manager, 700)
    assert _audit(store, "blocked_authority")[0]["reason_code"] == TRANSPORT_GRANT_EXPIRED


def test_journal_values_are_single_line_and_bounded(fake_clock):
    import io

    stream = io.StringIO()
    journal = AdmissionJournal(clock=fake_clock, stream=stream)
    journal.emit(
        "admission_refused",
        key=("x",),
        fields=[("grant_id", "g\nINJECTED line=1 " + "a" * 500), ("task_id", None)],
    )
    text = stream.getvalue()
    assert text.count("\n") == 1 and text.endswith("\n")
    assert " INJECTED" not in text
    assert len(text) < 200
    assert text.rstrip().endswith("task_id=-")


def test_health_reports_transport_admission_expired_without_changing_exit_code(
    workspace, store, fake_docker, fake_github
):
    """Record name: test_health_reports_transport_admission_expired_and_is_not_healthy.
    The check is not OK and an advisory is raised, but status/exit are untouched."""
    config, grants = _s_expired(workspace, store)
    store.heartbeat(1)
    now = store.last_heartbeat()
    baseline, baseline_code = run_health(
        config=make_config(workspace, queued_transport_enabled=False, transport_grant_id=None),
        store=store,
        docker=fake_docker,
        github=fake_github,
        now=now,
    )
    report, code = run_health(
        config=config, store=store, docker=fake_docker, github=fake_github, now=now, grants=grants
    )
    assert report["checks"]["transport_admission"] == {
        "ok": False,
        "informational": True,
        "detail": (
            f"state=refusing code=TRANSPORT_GRANT_EXPIRED grant_id={GRANT}"
            " expires_at=1970-01-01T00:16:40Z"
        ),
        "state": "refusing",
        "code": "TRANSPORT_GRANT_EXPIRED",
        "enabled": True,
        "grant_id": GRANT,
        "expires_at": "1970-01-01T00:16:40Z",
        "expires_in_seconds": int(1000.0 - now),
        "warning": None,
    }
    assert report["advisories"] == ["transport_admission:TRANSPORT_GRANT_EXPIRED"]
    assert (report["status"], code) == (baseline["status"], baseline_code) == ("healthy", 0)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("fault", ["none", "github", "config"])
def test_health_exit_code_is_independent_of_transport_state(
    name, fault, workspace, store, fake_docker, fake_github
):
    """The verdict for every other fault is exactly what it was before this
    change, whatever the transport state is (deploy gate safety)."""
    config, grants = SCENARIOS[name][0](workspace, store)
    store.heartbeat(1)
    fake_github.fail_auth = fault == "github"
    config_text = '[worker]\nnetwork = "host"\n' if fault == "config" else None
    report, code = run_health(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        config_text=config_text,
        now=store.last_heartbeat(),
        grants=grants,
    )
    expected = {"none": ("healthy", 0), "github": ("degraded", 1), "config": ("blocked", 2)}
    assert (report["status"], code) == expected[fault]


def test_deploy_gate_treats_any_nonzero_health_exit_as_rollback():
    """Pins the fact the exit-code decision rests on. If this gate ever starts
    to distinguish exit 1 from exit 2, the informational-only decision for
    `transport_admission` should be revisited by the owner."""
    text = (INFRA / "scripts" / "deploy-release.sh").read_text(encoding="utf-8")
    assert 'if ! "${CURRENT_LINK}/scripts/atlas-runner-health.sh" >/dev/null 2>&1; then' in text
    assert (
        (INFRA / "scripts" / "atlas-runner-health.sh")
        .read_text()
        .rstrip()
        .endswith('exec "${BIN}" health')
    )


def test_health_transport_admission_ok_when_grant_valid(workspace, store, fake_docker, fake_github):
    config = make_config(workspace, transport_grant_id=GRANT)
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000, repository=REPO)
    store.heartbeat(1)
    report, code = run_health(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        now=store.last_heartbeat(),
        grants=grants,
    )
    check = report["checks"]["transport_admission"]
    assert (check["ok"], check["state"], check["code"]) == (True, "ok", "TRANSPORT_GRANT_OK")
    assert check["detail"] == f"state=ok code=TRANSPORT_GRANT_OK grant_id={GRANT} expires_at=none"
    assert report["advisories"] == []
    assert (report["status"], code) == ("healthy", 0)


def test_health_transport_admission_neutral_when_transport_disabled(
    workspace, store, fake_docker, fake_github
):
    config = make_config(workspace, transport_grant_id=None, queued_transport_enabled=False)
    store.heartbeat(1)
    report, code = run_health(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        now=store.last_heartbeat(),
    )
    check = report["checks"]["transport_admission"]
    assert check["ok"] is None  # disabled by design is not an error
    assert (check["state"], check["code"], check["enabled"]) == (
        "disabled",
        "TRANSPORT_DISABLED",
        False,
    )
    assert report["advisories"] == []
    assert (report["status"], code) == ("healthy", 0)


def test_health_is_a_pure_read_of_the_registry(workspace, store, fake_docker, fake_github):
    config, grants = _s_exhausted(workspace, store)
    before = grants.get(GRANT)
    audit_before = store._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    for _ in range(3):
        run_health(
            config=config, store=store, docker=fake_docker, github=fake_github, grants=grants
        )
        transport_admission_state(config, grants)
    assert grants.get(GRANT) == before
    assert store._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audit_before


# -- CLI surfaces ----------------------------------------------------------------


def _patch_context(monkeypatch, config, store, fake_docker, fake_github):
    monkeypatch.setattr(
        cli, "_build_context", lambda _path: (config, store, fake_docker, fake_github)
    )


def test_status_json_includes_transport_admission(
    workspace, store, fake_docker, fake_github, monkeypatch, capsys
):
    config, grants = _s_expired(workspace, store)
    grants.close()
    _patch_context(monkeypatch, config, store, fake_docker, fake_github)
    assert cli.main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["transport_admission"]["code"] == "TRANSPORT_GRANT_EXPIRED"
    assert payload["transport_admission"]["state"] == "refusing"
    assert payload["transport_admission"]["grant_id"] == GRANT
    assert payload["transport_admission"]["expires_at"] == "1970-01-01T00:16:40Z"
    assert payload["transport_admission"]["enabled"] is True
    assert payload["active_workers"] == 0 and payload["active"] == []

    assert cli.main(["status"]) == 0
    assert (
        f"transport_admission: refusing TRANSPORT_GRANT_EXPIRED grant_id={GRANT}"
        " expires_at=1970-01-01T00:16:40Z"
    ) in capsys.readouterr().out


def test_cli_health_reads_registry_read_only_and_keeps_exit_code(
    workspace, store, fake_docker, fake_github, monkeypatch, capsys
):
    config, grants = _s_expired(workspace, store)
    grants.close()
    store.heartbeat(1)
    _patch_context(monkeypatch, config, store, fake_docker, fake_github)
    assert cli.main(["--config", str(workspace / "absent.toml"), "health"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "healthy"
    assert report["checks"]["transport_admission"]["code"] == "TRANSPORT_GRANT_EXPIRED"
    assert report["advisories"] == ["transport_admission:TRANSPORT_GRANT_EXPIRED"]


def test_cli_reports_unknown_when_registry_table_is_missing(
    workspace, store, fake_docker, fake_github, monkeypatch, capsys
):
    """A state DB whose `grants` table was never created (controller never ran)
    is UNKNOWN, not OK, and the read-only lens must not create the table."""
    config = make_config(workspace, transport_grant_id=GRANT)
    _patch_context(monkeypatch, config, store, fake_docker, fake_github)
    assert cli.main(["status", "--json"]) == 0
    state = json.loads(capsys.readouterr().out)["transport_admission"]
    assert (state["state"], state["code"]) == ("unknown", "TRANSPORT_GRANT_REGISTRY_UNAVAILABLE")
    tables = {
        r[0] for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "grants" not in tables


def test_read_only_grant_reader_cannot_create_or_write(workspace, store):
    with pytest.raises(sqlite3.OperationalError):
        ReadOnlyGrantReader(workspace / "state" / "does-not-exist.db").get("x")
    assert not (workspace / "state" / "does-not-exist.db").exists()
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000)
    reader = ReadOnlyGrantReader(store.db_path)
    try:
        assert reader.get(GRANT)["grant_id"] == GRANT
        assert reader.get("nope") is None
        with pytest.raises(sqlite3.OperationalError):
            reader._conn.execute("UPDATE grants SET status = 'revoked'")
        assert not hasattr(reader, "issue") and not hasattr(reader, "consume")
    finally:
        reader.close()
    assert grants.get(GRANT)["status"] == "active"


def test_refusal_surfaces_contain_no_scope_json_and_no_token_values(
    workspace, store, fake_docker, fake_github, fake_worker_manager, monkeypatch, capsys
):
    """Journal, health and status expose the grant id and expiry only: no
    scope_json, repository/base_revision binding, budget, or token value."""
    controller, config, grants, admitted = _refuse(
        "scope_mismatch", workspace, store, fake_docker, fake_github, fake_worker_manager
    )
    assert admitted == []
    controller.poll_once()
    journal = capsys.readouterr().err
    report, _code = run_health(
        config=config, store=store, docker=fake_docker, github=fake_github, grants=grants
    )
    _patch_context(monkeypatch, config, store, fake_docker, fake_github)
    cli.main(["status", "--json"])
    cli.main(["status"])
    status_out = capsys.readouterr().out
    token = fake_github.token_provider.token
    for surface in (journal, health_json(report), status_out):
        assert surface
        for forbidden in (SCOPE_MARKER, REVISION_MARKER, "scope_json", "budget", token):
            assert forbidden not in surface
    assert set(controller.transport_admission_state()) == {
        "enabled",
        "grant_id",
        "code",
        "state",
        "expires_at",
        "expires_in_seconds",
        "warning",
    }
    for line in journal.splitlines():
        keys = {part.split("=", 1)[0] for part in line.split()[2:]}
        assert keys <= {"reason_code", "grant_id", "task_id", "repeats", "state", "expires_at"}


def test_reason_code_derives_from_exception_code_not_message_text(
    workspace, store, fake_docker, fake_github, fake_worker_manager
):
    # message says one thing, discriminator says another: the discriminator wins
    assert (
        transport_reason_code(
            GrantConsumedError("grant 'x' expired, unknown grant", code=GRANT_REVOKED)
        )
        == TRANSPORT_GRANT_REVOKED
    )
    assert (
        transport_reason_code(GrantConsumedError("status=revoked", code=GRANT_EXHAUSTED))
        == TRANSPORT_GRANT_EXHAUSTED
    )
    assert transport_reason_code(GrantExpiredError("budget exhausted")) == TRANSPORT_GRANT_EXPIRED
    assert transport_reason_code(GrantUnknownError("expired")) == TRANSPORT_GRANT_UNKNOWN
    assert transport_reason_code(GrantScopeError("expired")) == TRANSPORT_GRANT_SCOPE_MISMATCH
    assert transport_reason_code(GrantError("expired")) == TRANSPORT_GRANT_INVALID
    assert transport_reason_code(RuntimeError("grant 'x' expired")) == (
        TRANSPORT_GRANT_REGISTRY_UNAVAILABLE
    )
    # revoked vs exhausted share a class; validate_row sets distinct codes at the raise site
    row = {"status": "revoked", "expires_at": None, "consumed": 0, "budget": 1}
    with pytest.raises(GrantConsumedError) as revoked:
        GrantStore.validate_row(row, grant_id="g")
    with pytest.raises(GrantConsumedError) as exhausted:
        GrantStore.validate_row({**row, "status": "active", "consumed": 1}, grant_id="g")
    assert (revoked.value.code, exhausted.value.code) == (GRANT_REVOKED, GRANT_EXHAUSTED)

    class _Lying:
        def validate(self, grant_id, **_kwargs):
            raise GrantConsumedError("grant expired; unknown grant", code=GRANT_EXHAUSTED)

    controller = _controller(
        make_config(workspace, transport_grant_id=GRANT),
        store,
        fake_docker,
        fake_github,
        fake_worker_manager,
    )
    controller.attach_grants(_Lying())
    fake_github.queued = [_queued(700)]
    assert controller.admit_queued_jobs() == []
    assert _audit(store, "blocked_authority")[0]["reason_code"] == TRANSPORT_GRANT_EXHAUSTED
    # no message parsing anywhere in the mapping
    source = Path(controller_module.__file__).read_text(encoding="utf-8")
    mapper = source[source.index("def transport_reason_code") : source.index("def _iso_utc")]
    assert "str(exc)" not in mapper and " in exc" not in mapper


def test_success_path_is_unchanged(
    workspace, store, fake_docker, fake_github, fake_worker_manager, capsys
):
    config = make_config(workspace, transport_grant_id=GRANT)
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000, repository=REPO)
    controller = _controller(config, store, fake_docker, fake_github, fake_worker_manager)
    controller.attach_grants(grants)
    fake_github.queued = [_queued(710)]
    assert controller.poll_once() == ["gh-710-1-1"]
    assert _audit(store, "transport_grant_validated") == [
        {"task_id": "gh-710-1-1", "grant_id": GRANT}
    ]
    assert _audit(store, "blocked_authority") == []
    assert store.find_execution_by_job(710, 1, 1)["status"] == "COMPLETE"
    assert grants.get(GRANT)["consumed"] == 0  # standing grant is never consumed
    assert capsys.readouterr().err == ""  # a valid, far-from-expiry grant is silent


# -- F-RUNNER-4: pre-expiry warning ----------------------------------------------

T_EXPIRY = 2_000_000_000.0
WARN = 3600


def _warn_state(workspace, store, *, now, expires_at=T_EXPIRY, warn=WARN):
    config = make_config(workspace, transport_grant_id=GRANT, transport_grant_warn_seconds=warn)
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000, expires_at=expires_at)
    return config, grants, transport_admission_state(config, grants, now=now)


@pytest.mark.parametrize(
    ("offset", "code", "warning"),
    [
        (-(WARN + 1), TRANSPORT_GRANT_OK, None),  # just outside the window
        (-WARN, TRANSPORT_GRANT_OK, TRANSPORT_GRANT_EXPIRING),  # boundary is inclusive
        (-(WARN - 1), TRANSPORT_GRANT_OK, TRANSPORT_GRANT_EXPIRING),
        (-1, TRANSPORT_GRANT_OK, TRANSPORT_GRANT_EXPIRING),  # last valid second
        (0, TRANSPORT_GRANT_EXPIRED, None),  # now >= expires_at: expired, not "expiring"
        (1, TRANSPORT_GRANT_EXPIRED, None),
    ],
)
def test_pre_expiry_warning_boundaries(offset, code, warning, workspace, store):
    _config, _grants, state = _warn_state(workspace, store, now=T_EXPIRY + offset)
    assert (state["code"], state["warning"]) == (code, warning)
    assert state["expires_at"] == "2033-05-18T03:33:20Z"
    assert state["expires_in_seconds"] == -offset


def test_health_warns_when_transport_grant_expires_within_window(
    workspace, store, fake_docker, fake_github
):
    """Record name: test_health_degraded_when_transport_grant_expires_within_window.
    Warning state is shown; status/exit code are deliberately not degraded."""
    config, grants, _state = _warn_state(workspace, store, now=T_EXPIRY - 600)
    store._conn.execute("DELETE FROM heartbeats")
    with store._conn:
        store._conn.execute("INSERT INTO heartbeats(ts, pid) VALUES (?, ?)", (T_EXPIRY - 600, 1))
    report, code = run_health(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        now=T_EXPIRY - 600,
        grants=grants,
    )
    check = report["checks"]["transport_admission"]
    assert check["ok"] is True
    assert check["warning"] == "TRANSPORT_GRANT_EXPIRING"
    assert check["expires_in_seconds"] == 600
    assert check["detail"] == (
        f"state=ok code=TRANSPORT_GRANT_OK grant_id={GRANT} expires_at=2033-05-18T03:33:20Z"
        " warning=TRANSPORT_GRANT_EXPIRING expires_in_seconds=600"
    )
    assert report["advisories"] == ["transport_admission:TRANSPORT_GRANT_EXPIRING"]
    assert (report["status"], code) == ("healthy", 0)


def test_health_healthy_when_transport_grant_expiry_is_far(
    workspace, store, fake_docker, fake_github
):
    config, grants, state = _warn_state(workspace, store, now=T_EXPIRY - 10 * WARN)
    assert state["warning"] is None
    report, _code = run_health(
        config=config,
        store=store,
        docker=fake_docker,
        github=fake_github,
        now=T_EXPIRY - 10 * WARN,
        grants=grants,
    )
    assert report["checks"]["transport_admission"]["warning"] is None
    assert report["advisories"] == []


def test_grant_without_expiry_reports_no_expiry_and_no_warning(workspace, store):
    _config, _grants, state = _warn_state(workspace, store, now=T_EXPIRY, expires_at=None)
    assert state == {
        "enabled": True,
        "grant_id": GRANT,
        "code": "TRANSPORT_GRANT_OK",
        "state": "ok",
        "expires_at": None,
        "expires_in_seconds": None,
        "warning": None,
    }


def test_warning_window_zero_disables_the_warning(workspace, store):
    _config, _grants, state = _warn_state(workspace, store, now=T_EXPIRY - 1, warn=0)
    assert (state["code"], state["warning"]) == (TRANSPORT_GRANT_OK, None)


def test_pre_expiry_warning_does_not_block_admission(
    workspace, store, fake_docker, fake_github, fake_worker_manager, fake_clock, capsys
):
    config = make_config(workspace, transport_grant_id=GRANT, transport_grant_warn_seconds=WARN)
    grants = GrantStore(store.db_path)
    grants.issue(GRANT, budget=1000, expires_at=time.time() + 300)  # inside the window
    controller = _controller(
        config, store, fake_docker, fake_github, fake_worker_manager, clock=fake_clock
    )
    controller.attach_grants(grants)
    fake_github.queued = [_queued(720)]
    assert controller.poll_once() == ["gh-720-1-1"]  # admitted exactly as a far-expiry grant
    assert store.find_execution_by_job(720, 1, 1)["status"] == "COMPLETE"
    assert _audit(store, "blocked_authority") == []
    fake_github.queued = []
    for _ in range(3):
        controller.poll_once()
    lines = _lines(capsys.readouterr().err, "transport_admission")
    assert len(lines) == 1  # one warning per crossing, deduplicated
    assert lines[0].startswith(
        "atlas-runner transport_admission state=expiring reason_code=TRANSPORT_GRANT_EXPIRING"
        f" grant_id={GRANT} expires_at="
    )
    assert "remaining_seconds=" in lines[0]
    fake_clock.advance(JOURNAL_WARNING_REPEAT_SECONDS)
    controller.poll_once()
    assert len(_lines(capsys.readouterr().err, "transport_admission")) == 1


def test_warn_seconds_config_default_and_fail_closed_parse():
    base = {"github": {"owner": "o", "repo": "r"}}
    assert parse_config(base).transport_grant_warn_seconds == 86400
    assert (
        parse_config({**base, "transport_grant_warn_seconds": 0}).transport_grant_warn_seconds == 0
    )
    assert (
        parse_config({**base, "transport_grant_warn_seconds": 7200}).transport_grant_warn_seconds
        == 7200
    )
    for bad in (-1, True, "3600", 1.5):
        with pytest.raises(ConfigError):
            parse_config({**base, "transport_grant_warn_seconds": bad})
    schema = json.loads((INFRA / "schemas" / "controller-config.schema.json").read_text())
    prop = schema["properties"]["transport_grant_warn_seconds"]
    assert (prop["type"], prop["minimum"], prop["default"]) == ("integer", 0, 86400)

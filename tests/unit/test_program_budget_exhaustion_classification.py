"""A configured per-run ceiling is not a transient fault.

Reproduced from a real supervised attempt:

    program atlas-774-ruff-scope / task ruff-scope-774 / attempt run.1.f484186a
    adapter claude-code (claude 2.1.267), supervisor 4c5e0ffc

The runtime stopped itself on `--max-budget-usd`, reporting
`terminal_reason: "budget_exhausted"`. Every message- and status-based branch in
`_classify` was blind to it -- `error`, `api_error_status` and `result` were all
None -- so it fell through to the TRANSIENT_INFRASTRUCTURE default, which is
retryable. A retry would have re-run the same task under the same ceiling to the
same stop.

The payload below is the sanitized terminal event from that attempt.
"""
from __future__ import annotations

import pytest

from project_atlas.orchestration.program.adapters.claude_code import (
    SIGTERM_EXIT_STATUS,
    _classify,
)
from project_atlas.orchestration.program.models import (
    PERMANENT_FAILURES,
    RETRYABLE_FAILURES,
    ExecutionConfidence,
    FailureClass,
)

#: Sanitized from the recorded attempt. No identifiers, no prompt text.
BUDGET_EXHAUSTED = {
    "type": "result",
    "subtype": "error_max_budget_usd",
    "terminal_reason": "budget_exhausted",
    "errors": ["Reached maximum budget ($2)"],
    "is_error": True,
    "stop_reason": "tool_use",
    "num_turns": 2,
    "total_cost_usd": 2.097566,
    "permission_denials": [],
    # present and None on the real record; the point is that nothing else
    # carries the signal
    "result": None,
    "error": None,
    "api_error_status": None,
}

#: A genuinely transient failure, so the repair cannot be a blanket rewrite.
TRANSIENT_SERVER_ERROR = {
    "type": "result",
    "terminal_reason": "api_error",
    "is_error": True,
    "error": "server_error",
    "api_error_status": 503,
    "result": "upstream unavailable",
}


def _classify_result(parsed: dict, *, exit_status: int = 1, stderr: str = ""):
    return _classify(
        terminal="completed", exit_status=exit_status, parsed=parsed, stderr=stderr
    )


def test_budget_exhaustion_is_not_transient_infrastructure():
    _confidence, failure, reason = _classify_result(BUDGET_EXHAUSTED)
    assert failure is not FailureClass.TRANSIENT_INFRASTRUCTURE
    assert failure is FailureClass.LIMIT_EXHAUSTED
    assert reason == "budget_exhausted"


def test_budget_exhaustion_is_not_an_account_quota_or_credential_failure():
    """The account was untouched and had quota; only a per-run ceiling was hit.

    Collapsing the two would lose a distinction an operator needs: one is fixed
    by raising a limit they set, the other by attending to the account.
    """
    _confidence, failure, _reason = _classify_result(BUDGET_EXHAUSTED)
    assert failure is not FailureClass.QUOTA_OR_CREDENTIAL


def test_budget_exhaustion_does_not_permit_retry_under_unchanged_limits():
    _confidence, failure, _reason = _classify_result(BUDGET_EXHAUSTED)
    assert failure not in RETRYABLE_FAILURES, (
        f"{failure} permits a bounded retry that must hit the identical ceiling"
    )
    assert failure in PERMANENT_FAILURES


def test_budget_exhaustion_is_failed_not_uncertain():
    """Partial output stands as evidence; nothing is unresolved.

    UNCERTAIN would summon reconciliation and imply an unobservable side effect.
    The run stopped for a reason we configured, so acceptance simply did not run.
    """
    confidence, _failure, _reason = _classify_result(BUDGET_EXHAUSTED)
    assert confidence is ExecutionConfidence.FAILED
    assert confidence is not ExecutionConfidence.UNCERTAIN
    assert _classify_result(BUDGET_EXHAUSTED)[1] is not FailureClass.UNCERTAIN_OUTCOME


def test_a_genuinely_transient_failure_is_still_retryable():
    """The control. Without it the repair could be a blanket 'never retry'."""
    _confidence, failure, _reason = _classify_result(TRANSIENT_SERVER_ERROR)
    assert failure is FailureClass.TRANSIENT_INFRASTRUCTURE
    assert failure in RETRYABLE_FAILURES


@pytest.mark.parametrize(
    "reason",
    ["api_error", "unknown", "", "budget_exhausted_but_not_really"],
)
def test_only_the_exact_reason_is_treated_as_a_configured_limit(reason: str):
    """A near-miss must not be swept into the permanent class."""
    payload = dict(BUDGET_EXHAUSTED, terminal_reason=reason)
    _confidence, failure, _r = _classify_result(payload)
    assert failure is not FailureClass.LIMIT_EXHAUSTED


def test_a_clean_result_is_still_confirmed():
    confidence, failure, _reason = _classify_result(
        {"type": "result", "is_error": False, "terminal_reason": "done"}, exit_status=0
    )
    assert confidence is ExecutionConfidence.CONFIRMED
    assert failure is None


# --------------------------------------------------------- precedence

# Uncovered by the tests above, and the one thing that would make this repair
# dangerous: a budget reason must never *downgrade* stronger evidence that the
# run's outcome is unknown. UNCERTAIN summons reconciliation; FAILED does not.

@pytest.mark.parametrize(
    "terminal,exit_status",
    [
        ("cancelled", 1),
        ("timeout", 1),
        ("completed", SIGTERM_EXIT_STATUS),
    ],
)
def test_uncertain_evidence_outranks_a_budget_reason(terminal: str, exit_status: int):
    confidence, failure, _reason = _classify(
        terminal=terminal, exit_status=exit_status, parsed=BUDGET_EXHAUSTED, stderr=""
    )
    assert confidence is ExecutionConfidence.UNCERTAIN
    assert failure is FailureClass.UNCERTAIN_OUTCOME
    assert failure is not FailureClass.LIMIT_EXHAUSTED


def test_an_unparseable_result_stays_uncertain():
    """No structured result means we cannot say, budget reason or not."""
    confidence, failure, _reason = _classify(
        terminal="completed", exit_status=1, parsed=None, stderr=""
    )
    assert confidence is ExecutionConfidence.UNCERTAIN
    assert failure is FailureClass.UNCERTAIN_OUTCOME

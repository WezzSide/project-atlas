from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class GuardrailViolation(RuntimeError):
    """Raised when role boundaries are violated in the lab flow."""


@dataclass(frozen=True)
class LaneState:
    lane: str
    state: str


@dataclass(frozen=True)
class AgentEnvelope:
    producer_role: str
    kind: str
    content: dict[str, Any]


@dataclass(frozen=True)
class DecisionInput:
    remote_head_match: bool
    exact_head_ci: bool
    exact_head_iv: bool
    claim_integrity: bool
    p0_count: int
    p1_count: int
    current_main_compatibility: bool
    mergeable: bool
    owner_gate_resolved: bool
    stale_head: bool
    implementer_self_certification_attempt: bool
    verifier_repo_write_attempt: bool
    lane_states: list[LaneState]
    head_moved_after_decision: bool


@dataclass(frozen=True)
class GateResult:
    verdict: str
    reasons: list[str]
    runnable_lanes: list[str]


@dataclass(frozen=True)
class GovernorDecision:
    owner_request: str
    implementer_output: AgentEnvelope
    verifier_report: AgentEnvelope
    verdict: str
    reasons: list[str]


class Implementer:
    role = "implementer"

    def execute(self, owner_request: str) -> AgentEnvelope:
        return AgentEnvelope(
            producer_role=self.role,
            kind="implementation_patch",
            content={"summary": f"Proposed implementation for: {owner_request}"},
        )


class Verifier:
    role = "verifier"

    def review(self, implementer_output: AgentEnvelope) -> AgentEnvelope:
        if implementer_output.producer_role != "implementer":
            raise GuardrailViolation("verifier_requires_implementer_input")
        return AgentEnvelope(
            producer_role=self.role,
            kind="verifier_verdict",
            content={"verdict": "APPROVE", "claim_integrity": "PASS"},
        )


class Governor:
    role = "governor"

    def validate_implementer_output(self, output: dict[str, Any]) -> None:
        if output.get("kind") == "verifier_verdict":
            raise GuardrailViolation("implementer_masquerade_as_verifier")

    def run(self, owner_request: str) -> GovernorDecision:
        implementer = Implementer()
        verifier = Verifier()
        implementer_output = implementer.execute(owner_request)
        self.validate_implementer_output(
            {
                "producer_role": implementer_output.producer_role,
                "kind": implementer_output.kind,
                "content": implementer_output.content,
            }
        )
        verifier_report = verifier.review(implementer_output)
        gate = evaluate_gate(
            DecisionInput(
                remote_head_match=True,
                exact_head_ci=True,
                exact_head_iv=verifier_report.content.get("verdict") == "APPROVE",
                claim_integrity=verifier_report.content.get("claim_integrity") == "PASS",
                p0_count=0,
                p1_count=0,
                current_main_compatibility=True,
                mergeable=True,
                owner_gate_resolved=True,
                stale_head=False,
                implementer_self_certification_attempt=False,
                verifier_repo_write_attempt=False,
                lane_states=[LaneState("lane-owner-request", "RUNNABLE")],
                head_moved_after_decision=False,
            )
        )
        return GovernorDecision(
            owner_request=owner_request,
            implementer_output=implementer_output,
            verifier_report=verifier_report,
            verdict=gate.verdict,
            reasons=gate.reasons,
        )


def evaluate_gate(inputs: DecisionInput) -> GateResult:
    reasons: list[str] = []
    runnable = [lane.lane for lane in inputs.lane_states if lane.state == "RUNNABLE"]

    if inputs.implementer_self_certification_attempt:
        reasons.append("implementer_self_certification")
    if inputs.verifier_repo_write_attempt:
        reasons.append("verifier_write_attempt")
    if inputs.stale_head:
        reasons.append("stale_head")
    if not inputs.remote_head_match:
        reasons.append("remote_head_mismatch")
    if not inputs.exact_head_ci:
        reasons.append("exact_head_ci_missing")
    if not inputs.exact_head_iv:
        reasons.append("exact_head_iv_missing")
    if not inputs.claim_integrity:
        reasons.append("claim_integrity_fail")
    if inputs.p0_count > 0:
        reasons.append("p0_findings")
    if inputs.p1_count > 0:
        reasons.append("p1_findings")
    if not inputs.current_main_compatibility:
        reasons.append("current_main_compatibility_fail")
    if not inputs.mergeable:
        reasons.append("not_mergeable")
    if not inputs.owner_gate_resolved:
        reasons.append("owner_gate_unresolved")
    if inputs.head_moved_after_decision:
        reasons.append("head_moved_after_decision")
    if runnable and not reasons:
        reasons.append("runnable_lanes_nonempty")

    blockers = [reason for reason in reasons if reason != "runnable_lanes_nonempty"]
    verdict = "APPROVE" if not blockers else "BLOCK"
    return GateResult(verdict=verdict, reasons=reasons, runnable_lanes=runnable)

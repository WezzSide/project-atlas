from __future__ import annotations

import json

from experiments.agents_sdk.lab import Governor


def main() -> int:
    governor = Governor()
    decision = governor.run("Harden merge-gate evidence refresh checks.")
    payload = {
        "owner_request": decision.owner_request,
        "implementer_output": {
            "producer_role": decision.implementer_output.producer_role,
            "kind": decision.implementer_output.kind,
            "content": decision.implementer_output.content,
        },
        "verifier_report": {
            "producer_role": decision.verifier_report.producer_role,
            "kind": decision.verifier_report.kind,
            "content": decision.verifier_report.content,
        },
        "governor_verdict": decision.verdict,
        "governor_reasons": decision.reasons,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


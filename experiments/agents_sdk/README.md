# Agents SDK Lab (Isolated, Non-Production)

This lab validates role-separation and merge-gate policy behavior in an
isolated path:

- `OWNER REQUEST -> GOVERNOR -> IMPLEMENTER -> VERIFIER -> GOVERNOR DECISION`

The lab is deterministic and does not require network calls or credentials.

## Safety boundaries

- No production Atlas runtime imports.
- No GitHub merge authority.
- No repository write from verifier role.
- Implementer output cannot masquerade as verifier verdict.

## Run

```bash
.venv/bin/python -m pytest -q experiments/agents_sdk/tests/test_lab.py
.venv/bin/python -m experiments.agents_sdk.run_demo
```

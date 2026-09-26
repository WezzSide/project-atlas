"""ATLAS-RUNNER-FABRIC-001 controller package.

Contract: ephemeral GitHub Actions runner fabric controller. Truth boundary:
EXECUTOR_SUCCESS != VERIFIED, RUNNER_REGISTRATION != AUTHORITY,
EVIDENCE != MERGE AUTHORIZATION, NOT_RUN_REQUIRES_EXTERNAL_AUTHORITY.
PREP != IMPLEMENTED for anything not exercised against live GitHub.
"""

CONTROLLER_VERSION = "0.1.0"
CONFIG_SCHEMA_VERSION = 1

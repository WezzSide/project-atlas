#!/usr/bin/env bash
# atlas-runner-health.sh — single health entrypoint for deploy tooling.
#
# Used by BOTH deploy-release.sh's internal health gate AND the trusted
# deploy workflow's post-deploy health step, so the two cannot drift into
# subtly different credential contexts (INCIDENT-001, defect A).
#
# Contract:
#   - sources the service credential environment from a static trusted path
#     (override ATLAS_RUNNER_ENV_FILE is a test hook; production uses the
#     default and the workflow never sets it);
#   - fails closed (exit 2) when the env file is missing or unreadable;
#   - emits only health JSON on stdout; never prints credential values.
#
# Usage: atlas-runner-health.sh
set -euo pipefail

ENV_FILE="${ATLAS_RUNNER_ENV_FILE:-/etc/atlas-runner/config/atlas-runner.env}"
BIN="${ATLAS_RUNNER_BIN:-/opt/atlas-runner/current/bin/atlas-runner}"

if [ ! -f "${ENV_FILE}" ]; then
    echo "atlas-runner-health: env file missing: ${ENV_FILE}" >&2
    exit 2
fi
if [ ! -r "${ENV_FILE}" ]; then
    echo "atlas-runner-health: env file unreadable: ${ENV_FILE}" >&2
    exit 2
fi

set -a
# shellcheck disable=SC1090,SC1091
. "${ENV_FILE}"
set +a

exec "${BIN}" health

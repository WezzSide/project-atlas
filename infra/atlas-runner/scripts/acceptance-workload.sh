#!/usr/bin/env bash
# acceptance-workload.sh — ATLAS-RUNNER-E2E-001 mandatory demonstration task.
# Contract: deterministically update the acceptance fixture
# (tests/fixtures/acceptance/ATLAS-RUNNER-E2E-001.json): bump the counter,
# chain the previous fixture hash (previous_sha256), mint a run-identity
# nonce, and stamp the UTC time. Offline-only. The result branch carries the
# delta; the fixture on the default branch stays genesis.
# ACCEPTANCE_RAN != VERIFIED: independent verification is
# atlas-runner-verify.yml.
set -euo pipefail

TASK_ID="ATLAS-RUNNER-E2E-001"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
FIXTURE="${SCRIPT_DIR}/../tests/fixtures/acceptance/${TASK_ID}.json"

RUN_ID="${GITHUB_RUN_ID:-0}"
ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"
case "${RUN_ID}" in
    (*[!0-9]*|'') echo "acceptance-workload: GITHUB_RUN_ID must be a positive integer" >&2; exit 2 ;;
esac
case "${ATTEMPT}" in
    (*[!0-9]*|'') echo "acceptance-workload: GITHUB_RUN_ATTEMPT must be a positive integer" >&2; exit 2 ;;
esac
if [ "${RUN_ID}" -lt 1 ] || [ "${ATTEMPT}" -lt 1 ]; then
    echo "acceptance-workload: run id and attempt must be >= 1" >&2
    exit 2
fi

PREVIOUS_SHA256="null"
COUNTER=0
if [ -f "${FIXTURE}" ]; then
    PREVIOUS_SHA256="$(sha256sum "${FIXTURE}" | awk '{print $1}')"
    COUNTER="$(jq -r '.counter // 0' "${FIXTURE}")"
    case "${COUNTER}" in (*[!0-9]*) echo "acceptance-workload: bad counter in fixture" >&2; exit 1 ;; esac
    COUNTER=$((COUNTER + 1))
fi

# Run-identity nonce: binds this execution to (task, run, attempt, chain).
NONCE="$(printf '%s:%s:%s:%s' "${TASK_ID}" "${RUN_ID}" "${ATTEMPT}" "${PREVIOUS_SHA256}" | sha256sum | awk '{print $1}')"
EXECUTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname -- "${FIXTURE}")"
jq -n \
    --argjson schema_version 1 \
    --arg task_id "${TASK_ID}" \
    --argjson github_run_id "${RUN_ID}" \
    --argjson run_attempt "${ATTEMPT}" \
    --arg executed_at "${EXECUTED_AT}" \
    --arg nonce "${NONCE}" \
    --arg previous_sha256_raw "${PREVIOUS_SHA256}" \
    --argjson counter "${COUNTER}" \
    '{schema_version: $schema_version, task_id: $task_id,
      github_run_id: $github_run_id, run_attempt: $run_attempt,
      executed_at: $executed_at, nonce: $nonce,
      previous_sha256: (if $previous_sha256_raw == "null" then null
                         else $previous_sha256_raw end),
      counter: $counter}' \
    > "${FIXTURE}"

FIXTURE_SHA256="$(sha256sum "${FIXTURE}" | awk '{print $1}')"
printf 'ATLAS_ACCEPTANCE_FIXTURE_SHA256=%s\n' "${FIXTURE_SHA256}"
printf 'acceptance-workload: counter=%s previous_sha256=%s\n' "${COUNTER}" "${PREVIOUS_SHA256}"

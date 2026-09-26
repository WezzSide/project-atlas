#!/usr/bin/env bash
# ATLAS-RUNNER-FABRIC-001 worker entrypoint (AS-RUNNER-FABRIC-001).
# Contract: register an ephemeral runner, run exactly one job, deregister on
# exit. RUNNER_REGISTERED != JOB_SUCCESS; EPHEMERAL_EXIT != VERIFIED.
# Logs go to stdout (docker logs) AND to /workspace/worker.log (evidence).
set -euo pipefail

RUNNER_DIR="/opt/runner"
WORK_DIR="/workspace"
LOG_FILE="${WORK_DIR}/worker.log"
EVIDENCE_DIR="${ATLAS_EVIDENCE_DIR:-${WORK_DIR}/evidence}"
mkdir -p "${EVIDENCE_DIR}"

log() {
    local line
    line="[entrypoint $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    printf '%s\n' "${line}" | tee -a "${LOG_FILE}"
}

# Forward SIGTERM to Runner.Listener so `docker stop` deregisters cleanly.
_Listener_PID=""
trap 'log "received SIGTERM, forwarding to Runner.Listener"; if [ -n "${_Listener_PID}" ]; then kill -TERM "${_Listener_PID}" 2>/dev/null || true; fi' TERM INT

cd "${RUNNER_DIR}"

if [ -n "${RUNNER_JIT_CONFIG_FILE:-}" ]; then
    log "starting ephemeral runner via JIT config"
    ./run.sh --jitconfig "$(cat "${RUNNER_JIT_CONFIG_FILE}")" >>"${LOG_FILE}" 2>&1 &
    _Listener_PID=$!
elif [ -n "${RUNNER_REGISTRATION_TOKEN_FILE:-}" ]; then
    log "starting ephemeral runner via registration token"
    ./config.sh \
        --url "https://github.com/${GITHUB_REPOSITORY}" \
        --token "$(cat "${RUNNER_REGISTRATION_TOKEN_FILE}")" \
        --ephemeral --unattended \
        --name "${RUNNER_NAME:?RUNNER_NAME required}" \
        --labels "${RUNNER_LABELS:?RUNNER_LABELS required}" \
        --work "${RUNNER_WORK_FOLDER:-_work}" >>"${LOG_FILE}" 2>&1
    rm -f "${RUNNER_REGISTRATION_TOKEN_FILE}"
    ./run.sh --once >>"${LOG_FILE}" 2>&1 &
    _Listener_PID=$!
else
    log "FATAL: neither RUNNER_JIT_CONFIG_FILE nor RUNNER_REGISTRATION_TOKEN_FILE set"
    exit 64
fi

set +e
wait "${_Listener_PID}"
code=$?
set -e
_Listener_PID=""
log "Runner.Listener exited with code ${code}; ephemeral deregistration expected"

# Best-effort evidence fragment so the controller can attribute artifacts.
if [ -n "${ATLAS_TASK_ID:-}" ]; then
    jq -n \
        --arg task_id "${ATLAS_TASK_ID}" \
        --argjson exit_code "${code}" \
        '{task_id: $task_id, runner_exit_code: $exit_code, artifacts: []}' \
        > "${EVIDENCE_DIR}/fragment.json" 2>/dev/null || true
fi

exit "${code}"

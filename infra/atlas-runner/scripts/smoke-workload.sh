#!/usr/bin/env bash
# smoke-workload.sh — deterministic in-job smoke workload (AS-RUNNER-FABRIC-001).
# Contract: writes a manifest artifact, sha256s it, runs a trivial self-test,
# emits an evidence fragment to $ATLAS_EVIDENCE_DIR. Must pass with zero
# network access. SMOKE_PASS != VERIFIED: this proves the executor plumbing,
# nothing about repository authority.
set -euo pipefail

WORKSPACE="${ATLAS_WORKSPACE_DIR:-/workspace}"
EVIDENCE_DIR="${ATLAS_EVIDENCE_DIR:-${WORKSPACE}/evidence}"
MANIFEST="${WORKSPACE}/smoke-manifest.json"
mkdir -p "${EVIDENCE_DIR}"

REPO_REVISION="unknown"
if git -C "${WORKSPACE}" rev-parse HEAD >/dev/null 2>&1; then
    REPO_REVISION="$(git -C "${WORKSPACE}" rev-parse HEAD)"
fi

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_ID="${GITHUB_RUN_ID:-0}"
ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"

jq -n \
    --arg run_id "${RUN_ID}" \
    --arg attempt "${ATTEMPT}" \
    --arg generated_at "${NOW}" \
    --arg repo_revision "${REPO_REVISION}" \
    --arg task_id "${ATLAS_TASK_ID:-unknown}" \
    --arg execution_id "${ATLAS_EXECUTION_ID:-unknown}" \
    --arg runner_name "${RUNNER_NAME:-unknown}" \
    '{run_id: $run_id, run_attempt: $attempt, generated_at: $generated_at,
      repo_revision: $repo_revision, task_id: $task_id,
      execution_id: $execution_id, runner_name: $runner_name}' \
    > "${MANIFEST}"

SHA256="$(sha256sum "${MANIFEST}" | awk '{print $1}')"

# Self-test: the manifest must parse and carry the expected keys.
jq -e '.run_id and .generated_at and .repo_revision' "${MANIFEST}" >/dev/null

jq -n \
    --arg task_id "${ATLAS_TASK_ID:-unknown}" \
    --arg manifest_sha256 "${SHA256}" \
    --arg result_revision "${REPO_REVISION}" \
    '{task_id: $task_id, result_revision: $result_revision,
      artifacts: ["smoke-manifest.json"],
      tests: {smoke_manifest_parses: true},
      artifact_sha256: {"smoke-manifest.json": $manifest_sha256}}' \
    > "${EVIDENCE_DIR}/fragment.json"

sha256sum "${MANIFEST}" > "${EVIDENCE_DIR}/smoke-manifest.sha256"

printf 'smoke-workload: OK manifest=%s sha256=%s\n' "${MANIFEST}" "${SHA256}"

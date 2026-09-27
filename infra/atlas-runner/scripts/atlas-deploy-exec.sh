#!/usr/bin/env bash
# atlas-deploy-exec — immutable host bootstrap for target-revision deploys.
#
# Closes the deploy-tool self-bootstrap boundary (INCIDENT-001, defect C):
# the trusted deploy workflow transfers deploy-release.sh taken from the
# EXACT validated target revision (via stdin) and this shim verifies its
# identity before executing it. A broken script in the previously deployed
# release can therefore never block deployment of the fix for itself.
#
# Contract:
#   - arguments: <40-hex revision> <64-hex expected sha256 of the tool>;
#   - the tool script arrives on stdin;
#   - staged under a root-owned 0700 directory, hash-verified, syntax-checked;
#   - any mismatch or malformed input fails closed before execution;
#   - executes the staged tool with the authority-bound revision.
#
# Installed once on the host at /usr/local/sbin/atlas-deploy-exec (mode 0755,
# root-owned). The SSH force-command allowlist admits only this invocation
# shape.
#
# Usage: atlas-deploy-exec <revision> <expected-sha256>  < deploy-release.sh
set -euo pipefail

REV="${1:?usage: atlas-deploy-exec <revision> <expected-sha256>}"
EXPECTED="${2:?usage: atlas-deploy-exec <revision> <expected-sha256>}"

if ! printf '%s' "${REV}" | grep -Eq '^[0-9a-f]{40}$'; then
    echo "atlas-deploy-exec: revision must be a 40-hex SHA" >&2
    exit 2
fi
if ! printf '%s' "${EXPECTED}" | grep -Eq '^[0-9a-f]{64}$'; then
    echo "atlas-deploy-exec: expected digest must be a 64-hex SHA-256" >&2
    exit 2
fi

STAGE_DIR="${ATLAS_DEPLOY_STAGING_DIR:-/var/lib/atlas-runner/deploy-staging}"
mkdir -p "${STAGE_DIR}"
chmod 0700 "${STAGE_DIR}"
umask 077

TOOL="${STAGE_DIR}/deploy-release-${REV}.sh"
cat > "${TOOL}"
chmod 0700 "${TOOL}"

# Retain only the current staged tool; staging is transient identity material.
find "${STAGE_DIR}" -maxdepth 1 -type f -name 'deploy-release-*.sh' \
    ! -name "$(basename "${TOOL}")" -delete 2>/dev/null || true

ACTUAL="$(sha256sum "${TOOL}" | awk '{print $1}')"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "atlas-deploy-exec: tool hash mismatch (expected ${EXPECTED}, got ${ACTUAL})" >&2
    rm -f "${TOOL}"
    exit 1
fi
if ! bash -n "${TOOL}"; then
    echo "atlas-deploy-exec: tool failed bash -n" >&2
    rm -f "${TOOL}"
    exit 1
fi

exec bash "${TOOL}" "${REV}"

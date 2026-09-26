#!/usr/bin/env bash
# deploy-release.sh — versioned release deploy with symlink swap + rollback.
# Contract: stage -> validate -> activate -> restart -> health -> rollback on
# failure. Idempotent per revision: re-deploying the same rev is a no-op swap.
# Usage: deploy-release.sh <git-revision> [--skip-tests]
set -euo pipefail

REV="${1:?usage: deploy-release.sh <git-revision> [--skip-tests]}"
SKIP_TESTS=0
for arg in "$@"; do
    case "${arg}" in
        --skip-tests) SKIP_TESTS=1 ;;
    esac
done

RELEASES_DIR="/opt/atlas-runner/releases"
CURRENT_LINK="/opt/atlas-runner/current"
UNIT_NAME="atlas-runner-controller.service"
SERVICE_USER="atlas-runner"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../../" >/dev/null 2>&1 && pwd -P)"

log() { printf '[deploy] %s\n' "$*"; }
die() { log "FATAL: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root"
command -v git >/dev/null 2>&1 || die "git not found"

RELEASE_DIR="${RELEASES_DIR}/${REV}"
PREVIOUS_TARGET=""
if [ -L "${CURRENT_LINK}" ]; then
    PREVIOUS_TARGET="$(readlink -f "${CURRENT_LINK}")"
fi

if [ -d "${RELEASE_DIR}/controller" ]; then
    log "release ${REV} already staged at ${RELEASE_DIR}"
else
    log "staging ${REV} from ${REPO_ROOT}"
    mkdir -p "${RELEASES_DIR}"
    TMP="$(mktemp -d "${RELEASES_DIR}/.stage-XXXXXX")"
    git -C "${REPO_ROOT}" archive "${REV}" infra/atlas-runner | tar -x -C "${TMP}"
    [ -d "${TMP}/infra/atlas-runner/controller" ] || { rm -rf "${TMP}"; die "revision ${REV} has no infra/atlas-runner"; }
    git -C "${REPO_ROOT}" rev-parse "${REV}" > "${TMP}/infra/atlas-runner/.source-revision"
    mv "${TMP}/infra/atlas-runner" "${RELEASE_DIR}"
    rm -rf "${TMP}"
fi

# --- bin wrapper ---------------------------------------------------------------
mkdir -p "${RELEASE_DIR}/bin"
cat > "${RELEASE_DIR}/bin/atlas-runner" <<'WRAPPER'
#!/usr/bin/env bash
# atlas-runner wrapper: PYTHONPATH setup for the deployed release.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
RELEASE="$(cd -- "${HERE}/.." >/dev/null 2>&1 && pwd -P)"
CONTRACTS_SRC=""
# atlas_contracts from a project checkout if present (deploy host pattern).
for candidate in /opt/project-atlas-vault/src /opt/atlas/src; do
    if [ -d "${candidate}/atlas_contracts" ]; then
        CONTRACTS_SRC="${candidate}"
        break
    fi
done
if [ -z "${CONTRACTS_SRC}" ] && [ -d "${RELEASE}/../project-src/src/atlas_contracts" ]; then
    CONTRACTS_SRC="${RELEASE}/../project-src/src"
fi
if [ -z "${CONTRACTS_SRC}" ]; then
    echo "atlas-runner: atlas_contracts sources not found (expected under /opt)" >&2
    exit 1
fi
export PYTHONPATH="${CONTRACTS_SRC}"
exec python3 -m controller --config "${ATLAS_RUNNER_CONFIG:-/etc/atlas-runner/config/atlas-runner.toml}" "$@"
WRAPPER
chmod 0755 "${RELEASE_DIR}/bin/atlas-runner"

# --- validate: run controller tests on the host ---------------------------------
if [ "${SKIP_TESTS}" -eq 0 ]; then
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import pytest' >/dev/null 2>&1; then
        log "running controller test suite"
        if ! PYTHONPATH="${REPO_ROOT}/src" python3 -m pytest "${RELEASE_DIR}/tests" -q --no-cov -p no:cacheprovider; then
            die "tests failed for ${REV}; release NOT activated"
        fi
    else
        log "WARNING: pytest not available on host; skipping tests (deploy --skip-tests to silence)"
    fi
else
    log "tests skipped per --skip-tests"
fi

# --- validate: shell syntax ------------------------------------------------------
if command -v bash >/dev/null 2>&1; then
    bash -n "${RELEASE_DIR}/entrypoint.sh"
    bash -n "${RELEASE_DIR}/scripts/bootstrap-vps.sh"
    bash -n "${RELEASE_DIR}/scripts/smoke-workload.sh" 2>/dev/null || true
    bash -n "${RELEASE_DIR}/bin/atlas-runner"
fi

# --- activate: symlink swap -------------------------------------------------------
ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}.new"
mv -T "${CURRENT_LINK}.new" "${CURRENT_LINK}"
log "activated ${CURRENT_LINK} -> ${RELEASE_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${RELEASE_DIR}" || true

# --- restart + health + rollback ----------------------------------------------------
reload_needed=0
if [ -f "${RELEASE_DIR}/systemd/${UNIT_NAME}" ]; then
    install -m 0644 "${RELEASE_DIR}/systemd/${UNIT_NAME}" "/etc/systemd/system/${UNIT_NAME}"
    systemctl daemon-reload
    reload_needed=1
fi

rollback() {
    log "health check failed; rolling back to ${PREVIOUS_TARGET:-none}"
    if [ -n "${PREVIOUS_TARGET}" ] && [ -d "${PREVIOUS_TARGET}" ]; then
        ln -sfn "${PREVIOUS_TARGET}" "${CURRENT_LINK}.new"
        mv -T "${CURRENT_LINK}.new" "${CURRENT_LINK}"
        systemctl restart "${UNIT_NAME}" || true
        log "rollback complete"
    else
        systemctl stop "${UNIT_NAME}" || true
        log "no previous release; service stopped"
    fi
    exit 1
}

if systemctl is-active --quiet "${UNIT_NAME}" || systemctl is-enabled --quiet "${UNIT_NAME}"; then
    systemctl restart "${UNIT_NAME}"
    sleep 5
    if ! "${CURRENT_LINK}/bin/atlas-runner" health >/dev/null 2>&1; then
        rollback
    fi
    log "service restarted and healthy"
else
    log "service not active/enabled; activation only (start via systemctl when ready)"
fi
log "deploy of ${REV} complete (reload_needed=${reload_needed})"

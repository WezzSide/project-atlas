#!/usr/bin/env bash
# bootstrap-vps.sh — idempotent host bootstrap for the Atlas runner fabric.
# Contract: create the atlas-runner system user, directories, verify docker,
# install the hardened systemd unit. Does NOT install docker (prerequisite),
# does NOT handle secrets (see scripts/gh-token-helper.md). Safe to re-run.
set -euo pipefail

USER_NAME="atlas-runner"
GROUP_NAME="atlas-runner"
STATE_DIR="/var/lib/atlas-runner"
JOBS_DIR="/var/lib/atlas-runner/jobs"
LOG_DIR="/var/log/atlas-runner"
CONFIG_DIR="/etc/atlas-runner/config"
UNIT_SRC=""
UNIT_DST="/etc/systemd/system/atlas-runner-controller.service"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
BASE_DIR="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd -P)"

log() { printf '[bootstrap] %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    log "FATAL: must run as root"
    exit 1
fi

# --- docker prerequisite (verified, never installed here) -------------------
if ! command -v docker >/dev/null 2>&1; then
    log "FATAL: docker not found. Install docker first (see README prerequisite)."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    log "FATAL: docker daemon not reachable. Start docker first."
    exit 1
fi
log "docker present: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"

# --- system user -------------------------------------------------------------
if ! id -u "${USER_NAME}" >/dev/null 2>&1; then
    useradd --system --home-dir "${STATE_DIR}" --shell /usr/sbin/nologin "${USER_NAME}"
    log "created user ${USER_NAME}"
else
    log "user ${USER_NAME} already exists"
fi

# --- directories -------------------------------------------------------------
install -d -m 0750 -o "${USER_NAME}" -g "${GROUP_NAME}" "${STATE_DIR}" "${JOBS_DIR}"
install -d -m 0755 -o "${USER_NAME}" -g "${GROUP_NAME}" "${LOG_DIR}"
install -d -m 0750 -o "${USER_NAME}" -g "${GROUP_NAME}" "${CONFIG_DIR}"

# --- config skeleton (never overwrite operator config) -------------------------
if [ ! -f "${CONFIG_DIR}/atlas-runner.toml" ]; then
    if [ -f "${BASE_DIR}/config/atlas-runner.example.toml" ]; then
        install -m 0600 -o "${USER_NAME}" -g "${GROUP_NAME}" \
            "${BASE_DIR}/config/atlas-runner.example.toml" "${CONFIG_DIR}/atlas-runner.toml"
        log "installed config skeleton (edit ${CONFIG_DIR}/atlas-runner.toml)"
    else
        log "WARNING: example config not found at ${BASE_DIR}/config/; create ${CONFIG_DIR}/atlas-runner.toml manually"
    fi
else
    log "config already present, left untouched"
fi

# --- systemd unit --------------------------------------------------------------
UNIT_SRC="${BASE_DIR}/systemd/atlas-runner-controller.service"
if [ -f "${UNIT_SRC}" ]; then
    install -m 0644 "${UNIT_SRC}" "${UNIT_DST}"
    systemctl daemon-reload
    systemctl enable atlas-runner-controller.service >/dev/null 2>&1 || true
    log "installed and enabled ${UNIT_DST}"
else
    log "WARNING: unit file not found at ${UNIT_SRC}; deploy-release.sh installs releases"
fi

log "bootstrap complete. Next: mint the GitHub token (scripts/gh-token-helper.md),"
log "edit ${CONFIG_DIR}/atlas-runner.toml, then run scripts/deploy-release.sh <rev>."

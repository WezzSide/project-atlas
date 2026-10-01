#!/usr/bin/env bash
# deploy-release.sh — versioned release deploy with symlink swap + rollback.
# Contract: stage -> validate -> build+validate worker image -> activate ->
# restart -> health -> rollback on failure. Idempotent per revision:
# re-deploying the same rev is a no-op swap (the worker image is reused when
# its revision label and Python contract still hold).
#
# Worker image closure (EXECUTOR_PY312_DEPLOYMENT_CLOSURE): one deploy of an
# exact revision makes the image the controller launches correspond to that
# same revision. The image is built from the staged release (revision-specific
# tag atlas-runner-worker:<REV>, never `latest`), the Python >= 3.12 contract
# is proven INSIDE the built image, and only then is the release-bound identity
# (worker-image.json) written and the release activated. Any failure here
# happens before the symlink swap, so the executor is never half-upgraded; a
# later health failure rolls the symlink back and with it the previous
# release's own image binding.
# Usage: deploy-release.sh <git-revision> [--skip-tests]
set -euo pipefail

REV="${1:?usage: deploy-release.sh <git-revision> [--skip-tests]}"
SKIP_TESTS=0
for arg in "$@"; do
    case "${arg}" in
        --skip-tests) SKIP_TESTS=1 ;;
    esac
done

RELEASES_DIR="${ATLAS_RELEASES_DIR:-/opt/atlas-runner/releases}"
CURRENT_LINK="${ATLAS_CURRENT_LINK:-/opt/atlas-runner/current}"
UNIT_NAME="atlas-runner-controller.service"
SERVICE_USER="${ATLAS_SERVICE_USER:-atlas-runner}"
SYSTEMD_SYSTEM_DIR="${ATLAS_SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"
SYSTEMCTL="${ATLAS_SYSTEMCTL:-systemctl}"
POST_RESTART_SLEEP="${ATLAS_POST_RESTART_SLEEP:-5}"
DOCKER="${ATLAS_DOCKER:-docker}"
IMAGE_REPO="atlas-runner-worker"
# The host's DOCKER-USER guard default-DROPs egress from every container bridge
# except the dedicated worker bridge (scripts/atlas-runner-firewall.sh). Image
# build RUN steps (apt, pip, runner download) therefore run on that SAME network:
# no new firewall permit and no host networking. Live run 36764955502 failed
# closed because the default build network could not resolve deb.debian.org.
BUILD_NET="${ATLAS_WORKER_NET:-atlas-runner-net}"

log() { printf '[deploy] %s\n' "$*"; }
die() { log "FATAL: $*"; exit 1; }

if [ "${ATLAS_DEPLOY_SKIP_ROOT_CHECK:-0}" != "1" ]; then
    [ "$(id -u)" -eq 0 ] || die "must run as root"
fi
command -v git >/dev/null 2>&1 || die "git not found"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
# --- archive source selection -------------------------------------------------
# The host-clone copy of this script resolves its own repository via the
# relative default; the release-staged copy (and the shim-invoked copy) fall
# back to known host-clone locations. Override with ATLAS_RUNNER_REPO_ROOT
# (test hook; production never sets it).
REPO_ROOT="${ATLAS_RUNNER_REPO_ROOT:-}"
if [ -z "${REPO_ROOT}" ]; then
    candidate="$(cd -- "${SCRIPT_DIR}/../../../" >/dev/null 2>&1 && pwd -P)"
    if git -C "${candidate}" rev-parse --git-dir >/dev/null 2>&1; then
        REPO_ROOT="${candidate}"
    else
        for candidate in /opt/project-atlas-vault /opt/atlas/src; do
            if [ -d "${candidate}" ] && git -C "${candidate}" rev-parse --git-dir >/dev/null 2>&1; then
                REPO_ROOT="${candidate}"
                break
            fi
        done
    fi
fi
[ -n "${REPO_ROOT}" ] || REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../../" >/dev/null 2>&1 && pwd -P)"
git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1 || die "no git checkout found for archive source (set ATLAS_RUNNER_REPO_ROOT)"

# --- archive source freshness (INCIDENT-001 defect B) --------------------------
# The trusted workflow validates REV against origin/main on GitHub; that does
# not prove this host clone contains REV. Establish it deterministically:
# pinned remote URL only, bounded fetch of the default branch, exact-commit
# existence proof BEFORE anything is staged or activated. git archive reads
# committed objects only, so a dirty host working tree can never enter the
# archive and the checked-out branch position is irrelevant.
TRUSTED_REMOTE_URLS="https://github.com/WezzSide/project-atlas.git https://github.com/B0LK13/project-atlas.git"
remote_url="$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null)" || die "archive source has no origin remote"
case " ${TRUSTED_REMOTE_URLS} " in
    *" ${remote_url} "*) ;;
    *) die "origin remote '${remote_url}' is not a trusted repository URL" ;;
esac
log "fetching origin main (bounded) in ${REPO_ROOT}"
git -C "${REPO_ROOT}" fetch --quiet origin '+refs/heads/main:refs/remotes/origin/main' || die "fetch of trusted remote failed; refusing to deploy"
git -C "${REPO_ROOT}" cat-file -e "${REV}^{commit}" || die "revision ${REV} not present in archive source after fetch; refusing to deploy"
log "archive source verified: ${REPO_ROOT} contains ${REV}"

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
export PYTHONPATH="${RELEASE}:${CONTRACTS_SRC}"
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

# --- validate: health entrypoint present (defect A parity) ---------------------
# The internal health gate invokes this helper; a release without it would
# fail the gate after activation, so refuse before activating.
[ -x "${RELEASE_DIR}/scripts/atlas-runner-health.sh" ] || die "release ${REV} lacks the atlas-runner-health.sh entrypoint"

# --- worker image: build from THIS revision, prove Python >= 3.12, bind ---------
# Fail closed and BEFORE activation: a build failure, a Python contract failure
# or an unreadable image identity leaves the previously active release (and its
# own image binding) untouched.
command -v "${DOCKER}" >/dev/null 2>&1 || die "docker not found; cannot build the worker image for ${REV}; release NOT activated"
IMAGE_TAG="${IMAGE_REPO}:${REV}"
inspect_image() { "${DOCKER}" image inspect --format '{{.Id}} {{index .Config.Labels "atlas.runner.revision"}}' "${IMAGE_TAG}" 2>/dev/null; }
reuse=0
if existing="$(inspect_image)"; then
    # Reuse only an image that provably belongs to this revision; a stale or
    # foreign image under the same tag is rebuilt, never trusted.
    if [ "${existing#* }" = "${REV}" ]; then
        reuse=1
    fi
fi
if [ "${reuse}" -eq 0 ]; then
    # Fast, diagnosable preflight (seconds, not a 12-minute apt timeout): the build
    # network must resolve and reach the Debian mirror the Dockerfile uses.
    BASE_IMAGE="$(awk '/^FROM /{for (i = 2; i <= NF; i++) if ($i !~ /^--/) { print $i; exit }}' "${RELEASE_DIR}/Dockerfile")"
    [ -n "${BASE_IMAGE}" ] || die "cannot determine the Dockerfile base image; release NOT activated"
    if ! probe_out="$(timeout 120 "${DOCKER}" run --rm --network "${BUILD_NET}" --cap-drop ALL --security-opt no-new-privileges \
        --entrypoint python3 "${BASE_IMAGE}" -c 'import socket; [socket.create_connection(t, timeout=15).close() for t in (("deb.debian.org", 80), ("pypi.org", 443), ("github.com", 443))]' 2>&1)"; then
        log "preflight output (tail): $(printf '%s' "${probe_out}" | tail -n 5)"
        die "worker image build preflight failed on network '${BUILD_NET}' (network missing, base image pull failed, or deb.debian.org/pypi.org/github.com unreachable; check docker network inspect ${BUILD_NET} and the atlas-runner-firewall permit); release NOT activated"
    fi
fi
if [ "${reuse}" -eq 1 ]; then
    log "worker image ${IMAGE_TAG} already built for ${REV}; reusing after validation"
else
    # Classic-builder capability probe (TRANSITIONAL compatibility mechanism; docker has
    # deprecated the legacy builder - follow-up ATLAS_BUILDKIT_NETWORK_MODERNIZATION).
    # Fail fast and diagnostically if the host docker no longer supports it: no silent
    # fallback to BuildKit/another network mode, no host networking. (BuildKit itself
    # rejects --network <user-defined> hard, so a BuildKit-only engine fails here too.)
    # The probe Dockerfile needs a real instruction after FROM: the legacy builder
    # rejects a bare "FROM scratch" with "No image was generated". It proves the legacy
    # builder is present; the preflight above and the real build's RUN steps are the
    # network guards.
    log "docker server version: $("${DOCKER}" version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
    if ! builder_out="$(printf 'FROM scratch\nLABEL atlas.probe=1\n' | DOCKER_BUILDKIT=0 "${DOCKER}" build --network "${BUILD_NET}" --quiet - 2>&1)"; then
        log "classic builder probe output (tail): $(printf '%s' "${builder_out}" | tail -n 5)"
        die "host docker does not support the classic (legacy) builder required for build network '${BUILD_NET}'; refusing to fall back to BuildKit or host networking; release NOT activated (see ATLAS_BUILDKIT_NETWORK_MODERNIZATION)"
    fi
    probe_id="$(printf '%s' "${builder_out}" | tail -n 1)"
    printf '%s' "${probe_id}" | grep -Eq '^sha256:[0-9a-f]{64}$' \
        || die "classic builder probe returned an unexpected result '${probe_id}'; refusing to continue; release NOT activated (see ATLAS_BUILDKIT_NETWORK_MODERNIZATION)"
    "${DOCKER}" rmi -f "${probe_id}" >/dev/null 2>&1 || true
    log "building worker image ${IMAGE_TAG} from ${RELEASE_DIR}"
    # Classic builder: BuildKit only accepts default|none|host for --network and
    # cannot join the permitted worker bridge; host networking is never used.
    DOCKER_BUILDKIT=0 "${DOCKER}" build --network "${BUILD_NET}" --tag "${IMAGE_TAG}" \
        --label "atlas.runner.revision=${REV}" "${RELEASE_DIR}" \
        || die "worker image build failed for ${REV}; release NOT activated"
fi
IMAGE_ID_LINE="$(inspect_image)" || die "cannot inspect worker image ${IMAGE_TAG}; release NOT activated"
IMAGE_ID="${IMAGE_ID_LINE%% *}"
[ "${IMAGE_ID_LINE#* }" = "${REV}" ] || die "worker image ${IMAGE_TAG} is not labelled for ${REV}; release NOT activated"
printf '%s' "${IMAGE_ID}" | grep -Eq '^sha256:[0-9a-f]{64}$' || die "worker image identity '${IMAGE_ID}' is not a sha256 content ID; release NOT activated"
PY_VERSION="$("${DOCKER}" run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    --entrypoint python3 "${IMAGE_ID}" -c 'import sys; assert sys.version_info >= (3, 12), sys.version; print("%d.%d.%d" % sys.version_info[:3])')" \
    || die "Python >= 3.12 contract failed in worker image ${IMAGE_TAG}; release NOT activated"
printf '%s' "${PY_VERSION}" | grep -Eq '^3\.(1[2-9]|[2-9][0-9])\.[0-9]+$' || die "worker image reported unusable Python version '${PY_VERSION}'; release NOT activated"
BINDING_TMP="${RELEASE_DIR}/.worker-image.json.tmp"
cat > "${BINDING_TMP}" <<BINDING
{
  "image_id": "${IMAGE_ID}",
  "python_version": "${PY_VERSION}",
  "revision": "${REV}",
  "schema_version": 1,
  "tag": "${IMAGE_TAG}"
}
BINDING
mv -f "${BINDING_TMP}" "${RELEASE_DIR}/worker-image.json"
log "worker-image tag=${IMAGE_TAG} id=${IMAGE_ID} python=${PY_VERSION} revision=${REV} reused=${reuse}"

# --- activate: symlink swap -------------------------------------------------------
ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}.new"
mv -T "${CURRENT_LINK}.new" "${CURRENT_LINK}"
log "activated ${CURRENT_LINK} -> ${RELEASE_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${RELEASE_DIR}" || true

# --- restart + health + rollback ----------------------------------------------------
reload_needed=0
if [ -f "${RELEASE_DIR}/systemd/${UNIT_NAME}" ]; then
    install -m 0644 "${RELEASE_DIR}/systemd/${UNIT_NAME}" "${SYSTEMD_SYSTEM_DIR}/${UNIT_NAME}"
    "${SYSTEMCTL}" daemon-reload
    reload_needed=1
fi

rollback() {
    log "health check failed; rolling back to ${PREVIOUS_TARGET:-none}"
    if [ -n "${PREVIOUS_TARGET}" ] && [ -d "${PREVIOUS_TARGET}" ]; then
        ln -sfn "${PREVIOUS_TARGET}" "${CURRENT_LINK}.new"
        mv -T "${CURRENT_LINK}.new" "${CURRENT_LINK}"
        "${SYSTEMCTL}" restart "${UNIT_NAME}" || true
        log "rollback complete"
    else
        "${SYSTEMCTL}" stop "${UNIT_NAME}" || true
        log "no previous release; service stopped"
    fi
    exit 1
}

if "${SYSTEMCTL}" is-active --quiet "${UNIT_NAME}" || "${SYSTEMCTL}" is-enabled --quiet "${UNIT_NAME}"; then
    "${SYSTEMCTL}" restart "${UNIT_NAME}"
    sleep "${POST_RESTART_SLEEP}"
    # Health context parity (INCIDENT-001 defect A): the internal gate uses the
    # SAME host-side entrypoint as the trusted workflow's post-deploy step, so
    # the two cannot drift. The helper sources the service credential env file
    # itself and fails closed when it is missing/unreadable.
    if ! "${CURRENT_LINK}/scripts/atlas-runner-health.sh" >/dev/null 2>&1; then
        rollback
    fi
    log "service restarted and healthy"
else
    log "service not active/enabled; activation only (start via systemctl when ready)"
fi
log "deploy of ${REV} complete (reload_needed=${reload_needed})"

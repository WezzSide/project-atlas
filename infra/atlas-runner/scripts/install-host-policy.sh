#!/usr/bin/env bash
# install-host-policy.sh — deterministic host bootstrap for the deploy-key
# force-command policy. Idempotent: re-running converges to the repo-defined
# state. Run ONCE on the deploy host as a user that can sudo (bootstrap only;
# the deploy workflow never invokes this — it is part of host provisioning).
#
# Installs atlas-runner-host-policy.sh and points the deploy key's
# authorized_keys entry at it. Backs up any pre-existing dispatcher.
#
# Usage: sudo install-host-policy.sh <deploy-user> <path-to-deploy-pubkey-comment>
set -euo pipefail

POLICY_USER="${1:?usage: install-host-policy.sh <deploy-user>}"
KEY_COMMENT="${2:-atlas-runner-deploy-gha}"

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
SOURCE="${HERE}/atlas-runner-host-policy.sh"
TARGET_DIR="/home/${POLICY_USER}/.ssh"
TARGET="${TARGET_DIR}/atlas-runner-host-policy.sh"
AUTHKEYS="${TARGET_DIR}/authorized_keys"

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "sha256sum required" >&2; exit 1; }
[ -f "${SOURCE}" ] || { echo "missing ${SOURCE}" >&2; exit 1; }
bash -n "${SOURCE}"

src_hash="$(sha256sum "${SOURCE}" | awk '{print $1}')"

if [ -f "${TARGET}" ]; then
    dst_hash="$(sha256sum "${TARGET}" | awk '{print $1}')"
    if [ "${src_hash}" != "${dst_hash}" ]; then
        cp -a "${TARGET}" "${TARGET}.bak-$(date +%Y%m%d%H%M%S)"
        echo "existing dispatcher differs; backed up and replacing"
    fi
fi
install -o "${POLICY_USER}" -g "${POLICY_USER}" -m 0755 "${SOURCE}" "${TARGET}"
echo "dispatcher: ${TARGET} sha256=${src_hash}"

# authorized_keys: point the deploy-key entry at the dispatcher (idempotent).
touch "${AUTHKEYS}"
chown "${POLICY_USER}:${POLICY_USER}" "${AUTHKEYS}"
chmod 0600 "${AUTHKEYS}"
line="command=\"${TARGET}\",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding"
# drop any prior entries for this key comment or policy, then append
grep -v "${KEY_COMMENT}\|atlas-runner-host-policy" "${AUTHKEYS}" > "${AUTHKEYS}.new" || true
PUBKEY=""
while IFS= read -r l; do
    case "$l" in
        *"${KEY_COMMENT}"*) PUBKEY="$(printf '%s' "$l" | sed 's/.*\(ssh-.*\)/\1/')" ;;
    esac
done < "${AUTHKEYS}"
if [ -z "${PUBKEY}" ]; then
    echo "no existing authorized_keys line with comment ${KEY_COMMENT}; add the key first" >&2
    mv "${AUTHKEYS}.new" "${AUTHKEYS}"
    exit 2
fi
cat "${AUTHKEYS}.new" > "${AUTHKEYS}"
echo "${line} ${PUBKEY}" >> "${AUTHKEYS}"
rm -f "${AUTHKEYS}.new"
chmod 0600 "${AUTHKEYS}"
chown "${POLICY_USER}:${POLICY_USER}" "${AUTHKEYS}"
echo "authorized_keys: deploy key bound to dispatcher"

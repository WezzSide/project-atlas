#!/usr/bin/env bash
# atlas-runner-firewall — permits outbound egress for the dedicated Atlas
# runner worker bridge (br-atlas-runner) ABOVE the pre-existing Atlas
# DOCKER-USER guard's default-DROP policy.
#
# Firewall change record (ATLAS-RUNNER-FABRIC-001, 2026-09-26):
#   before:   DOCKER-USER guard ended in DROP -> ALL bridge egress dropped;
#             worker provisioning failed closed (provision_failed:DockerError)
#             because the runner could not reach github.com (DNS+443).
#   reason:   ephemeral workers require outbound HTTPS/DNS to GitHub only.
#   change:   insert `-i br-atlas-runner -j RETURN` at position 4 of
#             DOCKER-USER (above the guard DROP, below its permits).
#             Scope: the atlas-runner worker bridge ONLY. No other network,
#             no inbound permits (workers publish no ports).
#   after:    workers on atlas-runner-net reach github.com/api.github.com;
#             all other container networks remain guard-default-deny.
#   rollback: iptables -D DOCKER-USER -i br-atlas-runner -j RETURN
#             systemctl disable --now atlas-runner-firewall.service
#
# The pre-existing atlas-docker-guard script is NOT modified; its rules stay
# intact and this rule survives its idempotent re-runs (append-only guard).
set -euo pipefail

CHAIN="DOCKER-USER"
BRIDGE="br-atlas-runner"

if ! iptables -n -L "${CHAIN}" >/dev/null 2>&1; then
    printf 'iptables chain %s not present — is docker installed and running?\n' "${CHAIN}" >&2
    exit 1
fi

if iptables -C "${CHAIN}" -i "${BRIDGE}" -j RETURN 2>/dev/null; then
    printf 'present: -A %s -i %s -j RETURN\n' "${CHAIN}" "${BRIDGE}"
else
    # Insert above the guard DROP (last line of the chain).
    iptables -I "${CHAIN}" 4 -i "${BRIDGE}" -j RETURN
    printf 'added:   -I %s 4 -i %s -j RETURN\n' "${CHAIN}" "${BRIDGE}"
fi

iptables -n -L "${CHAIN}" --line-numbers

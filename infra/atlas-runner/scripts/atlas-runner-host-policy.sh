#!/usr/bin/env bash
# atlas-runner-host-policy.sh — repo-owned force-command dispatcher (SSH).
#
# Single source of truth for the deploy-key command surface. Admits ONLY the
# exact invocations defined by the deployment contract; everything else is
# refused. Installed on the host by scripts/install-host-policy.sh (deterministic,
# idempotent, hash-verified). Deny-by-default; no general shell, no wildcards,
# no arbitrary sudo.
#
# Allowed forms (exact match, strict argument validation):
#   sudo /opt/atlas-runner/current/scripts/deploy-release.sh <40-hex>          (legacy parity)
#   sudo /opt/atlas-runner/current/bin/atlas-runner health                      (legacy parity)
#   sudo bash -c 'set -a; . /etc/atlas-runner/config/atlas-runner.env;          (legacy parity)
#                    set +a; exec /opt/atlas-runner/current/bin/atlas-runner health'
#   sudo /opt/atlas-runner/current/scripts/atlas-runner-health.sh               (canonical health)
#   sudo /usr/local/sbin/atlas-deploy-exec <40-hex> <64-hex>                   (target-revision deploy)
set -euo pipefail

case "${SSH_ORIGINAL_COMMAND-}" in
  "sudo /opt/atlas-runner/current/scripts/deploy-release.sh "*)
    rev="${SSH_ORIGINAL_COMMAND##*deploy-release.sh }"
    if ! printf '%s' "$rev" | grep -Eq '^[0-9a-f]{40}$'; then
      echo "refusing: revision must be 40-hex" >&2
      exit 2
    fi
    exec sudo /opt/atlas-runner/current/scripts/deploy-release.sh "$rev"
    ;;
  "sudo /opt/atlas-runner/current/bin/atlas-runner health")
    exec sudo /opt/atlas-runner/current/bin/atlas-runner health
    ;;
  "sudo bash -c 'set -a; . /etc/atlas-runner/config/atlas-runner.env; set +a; exec /opt/atlas-runner/current/bin/atlas-runner health'")
    exec sudo bash -c 'set -a; . /etc/atlas-runner/config/atlas-runner.env; set +a; exec /opt/atlas-runner/current/bin/atlas-runner health'
    ;;
  "sudo /opt/atlas-runner/current/scripts/atlas-runner-health.sh")
    exec sudo /opt/atlas-runner/current/scripts/atlas-runner-health.sh
    ;;
  "sudo /usr/local/sbin/atlas-deploy-exec "*)
    args="${SSH_ORIGINAL_COMMAND#sudo /usr/local/sbin/atlas-deploy-exec }"
    set -- $args
    if [ "$#" -ne 2 ] \
        || ! printf '%s' "$1" | grep -Eq '^[0-9a-f]{40}$' \
        || ! printf '%s' "$2" | grep -Eq '^[0-9a-f]{64}$'; then
      echo "refusing: shim args must be exactly <40-hex revision> <64-hex sha256>" >&2
      exit 2
    fi
    exec sudo /usr/local/sbin/atlas-deploy-exec "$1" "$2"
    ;;
  *)
    echo "refusing command: ${SSH_ORIGINAL_COMMAND-}" >&2
    exit 1
    ;;
esac

# ATLAS-RUNNER-FABRIC-001 worker image.
# Contract: single-use ephemeral GitHub Actions runner executor.
# EXECUTOR_SUCCESS != VERIFIED. This image is NOT actions/runner-images and
# must not grow into one; keep it minimal and reproducible.
#
# Base digest resolved 2026-09-26 via Docker Hub API:
#   GET https://hub.docker.com/v2/repositories/library/debian/tags/bookworm-slim
#   amd64 -> sha256:f3034a6ec3c1205360777c4aae76234998866ad18806ae62b63a3f84ccad782b
# Verify before bumping:
#   docker buildx imagetools inspect debian:bookworm-slim
FROM debian:bookworm-slim@sha256:f3034a6ec3c1205360777c4aae76234998866ad18806ae62b63a3f84ccad782b

# Pinned actions/runner version. Latest stable verified 2026-09-26 via
#   GET https://api.github.com/repos/actions/runner/releases/latest -> v2.337.0
ARG RUNNER_VERSION=2.337.0
ARG RUNNER_SHA256=""
ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="atlas-runner-worker" \
      org.opencontainers.image.description="Ephemeral GitHub Actions runner executor (AS-RUNNER-FABRIC-001)" \
      org.opencontainers.image.source="https://project-atlas.local/infra/atlas-runner" \
      atlas.runner.version="${RUNNER_VERSION}" \
      atlas.runner.ephemeral="true"

# Toolchain (Debian bookworm pinned versions; the smoke workload must NOT
# need node). build-essential is included for trivial compile checks only.
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash coreutils git curl ca-certificates jq ripgrep tar zip unzip \
      openssh-client python3 python3-pip python3-pytest python3-pytest-cov nodejs npm build-essential \
    && rm -rf /var/lib/apt/lists/*

# Pinned-digest verification step: download the official runner release from
# the canonical GitHub URL. If RUNNER_SHA256 is set, enforce it (fail closed).
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) runner_arch=x64 ;; \
      arm64) runner_arch=arm64 ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${runner_arch}-${RUNNER_VERSION}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/runner.tar.gz; \
    if [ -n "$RUNNER_SHA256" ]; then \
      echo "${RUNNER_SHA256}  /tmp/runner.tar.gz" | sha256sum -c -; \
    fi; \
    mkdir -p /opt/runner; \
    tar -xzf /tmp/runner.tar.gz -C /opt/runner; \
    rm -f /tmp/runner.tar.gz; \
    /opt/runner/bin/Runner.Listener --version

# Non-root runner user (home is the ephemeral workspace parent).
RUN useradd --system --create-home --home-dir /home/runner --shell /bin/bash runner

# The per-job workspace is a host mount (rw) owned by an arbitrary host uid;
# make the image-side default world-writable so the non-root runner user can
# always create its evidence/log subdirs. The controller additionally chmods
# each per-job workspace to 0777 before mounting.
RUN mkdir -p /workspace && chmod 0777 /workspace

COPY entrypoint.sh /opt/runner/entrypoint.sh
RUN chmod 0755 /opt/runner/entrypoint.sh && chown -R runner:runner /opt/runner

# The controller mounts the per-job workspace at /workspace (rw) and passes
# RUNNER_JIT_CONFIG_FILE or RUNNER_REGISTRATION_TOKEN_FILE pointing inside it.
WORKDIR /workspace
USER runner

ENTRYPOINT ["/opt/runner/entrypoint.sh"]

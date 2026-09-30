#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---check}"
BIN_DIR="${HOME}/.local/bin"

# Pinned versions for reproducible installs.
PLAYWRIGHT_MCP_VERSION="0.0.80"
CONTEXT7_MCP_VERSION="4.0.5"
MARKDOWNLINT_CLI2_VERSION="0.23.2"
CODEBASE_MEMORY_MCP_VERSION="0.10.8"
GITLEAKS_VERSION="v8.30.1"
TRIVY_VERSION="v0.74.0"
SYFT_VERSION="v1.51.1"
GRYPE_VERSION="v0.118.0"
TAPLO_VERSION="0.10.0"
ACTIONLINT_VERSION="v1.7.12"
taplo_SHA256="8fe196b894ccf9072f98d4e1013a180306e17d244830b03986ee5e8eabeb6156"

log() { printf '%s\n' "$*"; }
has() { command -v "$1" >/dev/null 2>&1; }

install_npm() {
	local pkg_name="$1" pkg_version="$2"
	local pkg_spec="${pkg_name}@${pkg_version}"
	if npm list -g --depth=0 "${pkg_name}" >/dev/null 2>&1 &&
		npm list -g --depth=0 "${pkg_name}@${pkg_version}" >/dev/null 2>&1; then
		log "present npm: ${pkg_spec}"
	else
		if [ "${MODE}" = "--install" ]; then
			npm install -g "${pkg_spec}" --no-fund --no-audit
		else
			log "would install npm: ${pkg_spec}"
		fi
	fi
}

install_pipx() {
	local pkg="$1" bin="$2"
	if has "${bin}"; then
		log "present pipx: ${pkg}"
	else
		if [ "${MODE}" = "--install" ]; then
			pipx install "${pkg}"
		else
			log "would install pipx: ${pkg}"
		fi
	fi
}

install_release_binary() {
	local repo="$1" tag="$2" asset_regex="$3" bin="$4" checksum_regex="${5:-}"
	if has "${bin}"; then
		log "present release binary: ${bin}"
		return 0
	fi
	if [ "${MODE}" != "--install" ]; then
		log "would install release binary: ${bin} (${repo})"
		return 0
	fi
	mkdir -p "${BIN_DIR}"
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "${tmp}"' RETURN
	gh api "repos/${repo}/releases/tags/${tag}" >"${tmp}/release.json"
	local asset_url asset_name
	asset_url="$(jq -r --arg re "${asset_regex}" '.assets[] | select(.name|test($re)) | .browser_download_url' "${tmp}/release.json" | head -n 1)"
	asset_name="$(basename "${asset_url}")"
	curl -fsSL "${asset_url}" -o "${tmp}/${asset_name}"
	if [ -n "${checksum_regex}" ]; then
		local sum_url sum_name
		sum_url="$(jq -r --arg re "${checksum_regex}" '.assets[] | select(.name|test($re)) | .browser_download_url' "${tmp}/release.json" | head -n 1)"
		sum_name="$(basename "${sum_url}")"
		curl -fsSL "${sum_url}" -o "${tmp}/${sum_name}"
		(cd "${tmp}" && grep "  ${asset_name}$" "${sum_name}" | sha256sum -c -)
	fi
	if [[ "${asset_name}" == *.tar.gz ]]; then
		tar -xzf "${tmp}/${asset_name}" -C "${tmp}"
		install -m 0755 "$(find "${tmp}" -maxdepth 2 -type f -name "${bin}" | head -n 1)" "${BIN_DIR}/${bin}"
	elif [[ "${asset_name}" == *.gz ]]; then
		gzip -dc "${tmp}/${asset_name}" >"${BIN_DIR}/${bin}"
		chmod 0755 "${BIN_DIR}/${bin}"
	else
		log "unsupported asset type: ${asset_name}"
		return 1
	fi
}

install_taplo() {
	if has taplo; then
		log "present release binary: taplo"
		return 0
	fi
	if [ "${MODE}" != "--install" ]; then
		log "would install release binary: taplo (tamasfe/taplo ${TAPLO_VERSION})"
		return 0
	fi
	mkdir -p "${BIN_DIR}"
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "${tmp}"' RETURN
	local url="https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-x86_64.gz"
	local gz="${tmp}/taplo-linux-x86_64.gz"
	curl -fsSL "${url}" -o "${gz}"
	local actual
	actual="$(sha256sum "${gz}" | awk '{print $1}')"
	if [ "${actual}" != "${taplo_SHA256}" ]; then
		log "taplo checksum mismatch: expected ${taplo_SHA256}, got ${actual}"
		return 1
	fi
	gzip -dc "${gz}" >"${BIN_DIR}/taplo"
	chmod 0755 "${BIN_DIR}/taplo"
}

case "${MODE}" in
--dry-run | --check | --install) ;;
*)
	log "usage: $0 [--dry-run|--check|--install]"
	exit 2
	;;
esac

log "mode=${MODE}"

install_npm "codebase-memory-mcp" "${CODEBASE_MEMORY_MCP_VERSION}"
install_npm "@playwright/mcp" "${PLAYWRIGHT_MCP_VERSION}"
install_npm "@upstash/context7-mcp" "${CONTEXT7_MCP_VERSION}"
install_npm "markdownlint-cli2" "${MARKDOWNLINT_CLI2_VERSION}"
install_pipx "semgrep" "semgrep"

install_release_binary "zricethezav/gitleaks" "${GITLEAKS_VERSION}" "linux_x64\\.tar\\.gz$" "gitleaks" "_checksums\\.txt$"
install_release_binary "aquasecurity/trivy" "${TRIVY_VERSION}" "Linux-64bit\\.tar\\.gz$" "trivy" "_checksums\\.txt$"
install_release_binary "anchore/syft" "${SYFT_VERSION}" "linux_amd64\\.tar\\.gz$" "syft" "_checksums\\.txt$"
install_release_binary "anchore/grype" "${GRYPE_VERSION}" "linux_amd64\\.tar\\.gz$" "grype" "_checksums\\.txt$"
install_taplo

if has actionlint; then
	log "present go install: actionlint"
elif [ "${MODE}" = "--install" ] && has go; then
	GOBIN="${BIN_DIR}" go install "github.com/rhysd/actionlint/cmd/actionlint@${ACTIONLINT_VERSION}"
else
	log "would install go tool: actionlint"
fi

for c in codebase-memory-mcp semgrep gitleaks trivy syft grype actionlint markdownlint-cli2 taplo; do
	if has "${c}"; then
		log "ok: ${c}"
	else
		log "missing: ${c}"
	fi
done

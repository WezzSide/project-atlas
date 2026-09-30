#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---check}"
BIN_DIR="${HOME}/.local/bin"

EXIT_OK=0
EXIT_USAGE=2
EXIT_REQUIRED_MISSING=3
EXIT_INSTALL_FAILED=4

# Pinned versions for reproducible installs.
PLAYWRIGHT_MCP_VERSION="0.0.80"
CONTEXT7_MCP_VERSION="4.0.5"
MARKDOWNLINT_CLI2_VERSION="0.23.2"
CODEBASE_MEMORY_MCP_VERSION="0.10.8"
GITHUB_MCP_VERSION="1.12.0"
SEMGREP_VERSION="1.176.1"
PIP_AUDIT_VERSION="2.10.1"
YAMLLINT_VERSION="1.38.0"
PRE_COMMIT_VERSION="4.6.2"
GITLEAKS_VERSION="v8.30.1"
TRIVY_VERSION="v0.74.0"
SYFT_VERSION="v1.51.1"
GRYPE_VERSION="v0.118.0"
HADOLINT_VERSION="v2.15.1"
TAPLO_VERSION="0.10.0"
ACTIONLINT_VERSION="v1.7.12"
taplo_SHA256="8fe196b894ccf9072f98d4e1013a180306e17d244830b03986ee5e8eabeb6156"

required_issues=0
optional_issues=0
install_failures=0
mutated=0

log() { printf '%s\n' "$*"; }
has() { command -v "$1" >/dev/null 2>&1; }

mark_result() {
	local name="$1" required="$2" status="$3" message="${4:-}"
	if [ -n "${message}" ]; then
		log "${status}: ${name} (${message})"
	else
		log "${status}: ${name}"
	fi
	case "${status}" in
	healthy | installed) ;;
	*)
		if [ "${required}" = "1" ]; then
			required_issues=$((required_issues + 1))
		else
			optional_issues=$((optional_issues + 1))
		fi
		;;
	esac
	if [ "${status}" = "install_failed" ]; then
		install_failures=$((install_failures + 1))
	fi
}

probe_version() {
	local cmd="$1" expected="$2"
	shift 2
	if ! has "${cmd}"; then
		printf 'missing|command_not_found'
		return 0
	fi
	local output=""
	if ! output="$("${cmd}" "$@" 2>&1 | head -n 1)"; then
		printf 'probe_failed|version_command_failed'
		return 0
	fi
	if [ -z "${output}" ]; then
		printf 'probe_failed|empty_version_output'
		return 0
	fi
	if [[ "${output}" == *"${expected}"* ]]; then
		printf 'healthy|%s' "${output}"
		return 0
	fi
	if echo "${output}" | grep -Eq '[0-9]+(\.[0-9]+){1,3}'; then
		printf 'version_drift|%s' "${output}"
		return 0
	fi
	printf 'probe_failed|unparseable_version_output: %s' "${output}"
}

install_npm() {
	local pkg_name="$1" pkg_version="$2" required="$3"
	local pkg_spec="${pkg_name}@${pkg_version}"
	if ! has npm; then
		if [ "${required}" = "1" ]; then
			mark_result "${pkg_spec}" "1" "missing" "npm_not_found"
		else
			mark_result "${pkg_spec}" "0" "optional_missing" "npm_not_found"
		fi
		return 0
	fi
	if npm list -g --depth=0 "${pkg_name}@${pkg_version}" >/dev/null 2>&1; then
		mark_result "${pkg_spec}" "${required}" "healthy"
		return 0
	fi
	if [ "${MODE}" != "--install" ]; then
		if [ "${required}" = "1" ]; then
			mark_result "${pkg_spec}" "1" "missing" "install_required"
		else
			mark_result "${pkg_spec}" "0" "optional_missing" "install_required"
		fi
		return 0
	fi
	if npm install -g "${pkg_spec}" --no-fund --no-audit; then
		if npm list -g --depth=0 "${pkg_name}@${pkg_version}" >/dev/null 2>&1; then
			mutated=1
			mark_result "${pkg_spec}" "${required}" "installed"
		else
			mark_result "${pkg_spec}" "${required}" "install_failed" "version_not_converged"
		fi
	else
		mark_result "${pkg_spec}" "${required}" "install_failed" "npm_install_failed"
	fi
}

install_pipx() {
	local pkg="$1" bin="$2" required="$3" expected="$4"
	shift 4
	local version_args=("$@")
	local probe
	if has "${bin}"; then
		probe="$(probe_version "${bin}" "${expected}" "${version_args[@]}")"
		local status="${probe%%|*}"
		local message="${probe#*|}"
		if [ "${status}" = "healthy" ]; then
			mark_result "${pkg}" "${required}" "healthy" "${message}"
			return 0
		fi
		if [ "${MODE}" != "--install" ]; then
			mark_result "${pkg}" "${required}" "${status}" "${message}"
			return 0
		fi
	fi
	if ! has pipx; then
		if [ "${required}" = "1" ]; then
			mark_result "${pkg}" "1" "missing" "pipx_not_found"
		else
			mark_result "${pkg}" "0" "optional_missing" "pipx_not_found"
		fi
		return 0
	fi
	if [ "${MODE}" != "--install" ]; then
		if [ "${required}" = "1" ]; then
			mark_result "${pkg}" "1" "missing" "install_required"
		else
			mark_result "${pkg}" "0" "optional_missing" "install_required"
		fi
		return 0
	fi
	if pipx install --force "${pkg}"; then
		probe="$(probe_version "${bin}" "${expected}" "${version_args[@]}")"
		local status="${probe%%|*}"
		local message="${probe#*|}"
		if [ "${status}" = "healthy" ]; then
			mutated=1
			mark_result "${pkg}" "${required}" "installed" "${message}"
		else
			mark_result "${pkg}" "${required}" "install_failed" "${message}"
		fi
	else
		mark_result "${pkg}" "${required}" "install_failed" "pipx_install_failed"
	fi
}

install_release_binary() {
	local repo="$1" tag="$2" asset_regex="$3" bin="$4" required="$5" expected="$6" checksum_regex="${7:-}"
	shift 7 || true
	local version_args=("$@")

	local probe
	if has "${bin}"; then
		probe="$(probe_version "${bin}" "${expected}" "${version_args[@]}")"
		local status="${probe%%|*}"
		local message="${probe#*|}"
		if [ "${status}" = "healthy" ]; then
			mark_result "${bin}" "${required}" "healthy" "${message}"
			return 0
		fi
		if [ "${MODE}" != "--install" ]; then
			mark_result "${bin}" "${required}" "${status}" "${message}"
			return 0
		fi
	fi

	if [ "${MODE}" != "--install" ]; then
		if [ "${required}" = "1" ]; then
			mark_result "${bin}" "1" "missing" "install_required"
		else
			mark_result "${bin}" "0" "optional_missing" "install_required"
		fi
		return 0
	fi

	mkdir -p "${BIN_DIR}"
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "${tmp}"' RETURN
	if ! gh api "repos/${repo}/releases/tags/${tag}" >"${tmp}/release.json"; then
		mark_result "${bin}" "${required}" "install_failed" "release_metadata_fetch_failed"
		return 0
	fi
	local asset_url asset_name
	asset_url="$(jq -r --arg re "${asset_regex}" '.assets[] | select(.name|test($re)) | .browser_download_url' "${tmp}/release.json" | head -n 1)"
	if [ -z "${asset_url}" ] || [ "${asset_url}" = "null" ]; then
		mark_result "${bin}" "${required}" "install_failed" "release_asset_not_found"
		return 0
	fi
	asset_name="$(basename "${asset_url}")"
	if ! curl -fsSL "${asset_url}" -o "${tmp}/${asset_name}"; then
		mark_result "${bin}" "${required}" "install_failed" "asset_download_failed"
		return 0
	fi
	if [ -n "${checksum_regex}" ]; then
		local sum_url sum_name
		sum_url="$(jq -r --arg re "${checksum_regex}" '.assets[] | select(.name|test($re)) | .browser_download_url' "${tmp}/release.json" | head -n 1)"
		if [ -z "${sum_url}" ] || [ "${sum_url}" = "null" ]; then
			mark_result "${bin}" "${required}" "install_failed" "checksum_asset_not_found"
			return 0
		fi
		sum_name="$(basename "${sum_url}")"
		if ! curl -fsSL "${sum_url}" -o "${tmp}/${sum_name}"; then
			mark_result "${bin}" "${required}" "install_failed" "checksum_download_failed"
			return 0
		fi
		if ! (cd "${tmp}" && grep "  ${asset_name}$" "${sum_name}" | sha256sum -c -); then
			mark_result "${bin}" "${required}" "install_failed" "checksum_verification_failed"
			return 0
		fi
	fi

	if [[ "${asset_name}" == *.tar.gz ]]; then
		tar -xzf "${tmp}/${asset_name}" -C "${tmp}"
		install -m 0755 "$(find "${tmp}" -maxdepth 3 -type f -name "${bin}" | head -n 1)" "${BIN_DIR}/${bin}"
	elif [[ "${asset_name}" == *.gz ]]; then
		gzip -dc "${tmp}/${asset_name}" >"${BIN_DIR}/${bin}"
		chmod 0755 "${BIN_DIR}/${bin}"
	else
		install -m 0755 "${tmp}/${asset_name}" "${BIN_DIR}/${bin}"
	fi

	probe="$(probe_version "${bin}" "${expected}" "${version_args[@]}")"
	local status="${probe%%|*}"
	local message="${probe#*|}"
	if [ "${status}" = "healthy" ]; then
		mutated=1
		mark_result "${bin}" "${required}" "installed" "${message}"
	else
		mark_result "${bin}" "${required}" "install_failed" "${message}"
	fi
}

install_taplo() {
	local required="$1"
	local probe
	if has taplo; then
		probe="$(probe_version "taplo" "${TAPLO_VERSION}" --version)"
		local status="${probe%%|*}"
		local message="${probe#*|}"
		if [ "${status}" = "healthy" ]; then
			mark_result "taplo" "${required}" "healthy" "${message}"
			return 0
		fi
		if [ "${MODE}" != "--install" ]; then
			mark_result "taplo" "${required}" "${status}" "${message}"
			return 0
		fi
	fi
	if [ "${MODE}" != "--install" ]; then
		mark_result "taplo" "${required}" "optional_missing" "install_required"
		return 0
	fi
	mkdir -p "${BIN_DIR}"
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "${tmp}"' RETURN
	local url="https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-x86_64.gz"
	local gz="${tmp}/taplo-linux-x86_64.gz"
	if ! curl -fsSL "${url}" -o "${gz}"; then
		mark_result "taplo" "${required}" "install_failed" "asset_download_failed"
		return 0
	fi
	local actual
	actual="$(sha256sum "${gz}" | awk '{print $1}')"
	if [ "${actual}" != "${taplo_SHA256}" ]; then
		mark_result "taplo" "${required}" "install_failed" "checksum_mismatch"
		return 0
	fi
	gzip -dc "${gz}" >"${BIN_DIR}/taplo"
	chmod 0755 "${BIN_DIR}/taplo"
	probe="$(probe_version "taplo" "${TAPLO_VERSION}" --version)"
	local status="${probe%%|*}"
	local message="${probe#*|}"
	if [ "${status}" = "healthy" ]; then
		mutated=1
		mark_result "taplo" "${required}" "installed" "${message}"
	else
		mark_result "taplo" "${required}" "install_failed" "${message}"
	fi
}

install_actionlint() {
	local required="$1"
	local probe
	if has actionlint; then
		probe="$(probe_version "actionlint" "1.7.12" -version)"
		local status="${probe%%|*}"
		local message="${probe#*|}"
		if [ "${status}" = "healthy" ]; then
			mark_result "actionlint" "${required}" "healthy" "${message}"
			return 0
		fi
		if [ "${MODE}" != "--install" ]; then
			mark_result "actionlint" "${required}" "${status}" "${message}"
			return 0
		fi
	fi
	if [ "${MODE}" = "--install" ] && has go; then
		if GOBIN="${BIN_DIR}" go install "github.com/rhysd/actionlint/cmd/actionlint@${ACTIONLINT_VERSION}"; then
			probe="$(probe_version "actionlint" "1.7.12" -version)"
			local status="${probe%%|*}"
			local message="${probe#*|}"
			if [ "${status}" = "healthy" ]; then
				mutated=1
				mark_result "actionlint" "${required}" "installed" "${message}"
			else
				mark_result "actionlint" "${required}" "install_failed" "${message}"
			fi
		else
			mark_result "actionlint" "${required}" "install_failed" "go_install_failed"
		fi
		return 0
	fi
	if [ "${required}" = "1" ]; then
		mark_result "actionlint" "1" "missing" "install_required"
	else
		mark_result "actionlint" "0" "optional_missing" "install_required"
	fi
}

case "${MODE}" in
--dry-run | --check | --install) ;;
*)
	log "usage: $0 [--dry-run|--check|--install]"
	exit "${EXIT_USAGE}"
	;;
esac

log "mode=${MODE}"

install_npm "codebase-memory-mcp" "${CODEBASE_MEMORY_MCP_VERSION}" "1"
install_npm "github-mcp-server" "${GITHUB_MCP_VERSION}" "1"
install_npm "@playwright/mcp" "${PLAYWRIGHT_MCP_VERSION}" "1"
install_npm "@upstash/context7-mcp" "${CONTEXT7_MCP_VERSION}" "0"
install_npm "markdownlint-cli2" "${MARKDOWNLINT_CLI2_VERSION}" "0"

install_pipx "semgrep==${SEMGREP_VERSION}" "semgrep" "1" "${SEMGREP_VERSION}" --version
install_pipx "pip-audit==${PIP_AUDIT_VERSION}" "pip-audit" "1" "${PIP_AUDIT_VERSION}" --version
install_pipx "yamllint==${YAMLLINT_VERSION}" "yamllint" "1" "${YAMLLINT_VERSION}" --version
install_pipx "pre-commit==${PRE_COMMIT_VERSION}" "pre-commit" "0" "${PRE_COMMIT_VERSION}" --version

install_release_binary "zricethezav/gitleaks" "${GITLEAKS_VERSION}" "linux_x64\\.tar\\.gz$" "gitleaks" "1" "8.30.1" "_checksums\\.txt$" version
install_release_binary "aquasecurity/trivy" "${TRIVY_VERSION}" "Linux-64bit\\.tar\\.gz$" "trivy" "1" "0.74.0" "_checksums\\.txt$" --version
install_release_binary "anchore/syft" "${SYFT_VERSION}" "linux_amd64\\.tar\\.gz$" "syft" "1" "1.51.1" "_checksums\\.txt$" version
install_release_binary "anchore/grype" "${GRYPE_VERSION}" "linux_amd64\\.tar\\.gz$" "grype" "1" "0.118.0" "_checksums\\.txt$" version
install_release_binary "hadolint/hadolint" "${HADOLINT_VERSION}" "Linux-x86_64$" "hadolint" "1" "2.15.1" "" --version
install_taplo "0"
install_actionlint "1"

log "required_issue_count=${required_issues}"
log "optional_issue_count=${optional_issues}"
log "install_failure_count=${install_failures}"
log "mutated=${mutated}"

if [ "${MODE}" = "--install" ]; then
	if [ "${install_failures}" -gt 0 ] || [ "${required_issues}" -gt 0 ]; then
		exit "${EXIT_INSTALL_FAILED}"
	fi
	exit "${EXIT_OK}"
fi

if [ "${required_issues}" -gt 0 ]; then
	exit "${EXIT_REQUIRED_MISSING}"
fi

exit "${EXIT_OK}"

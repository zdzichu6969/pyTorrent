#!/usr/bin/env bash
set -euo pipefail

# Bootstrap installer for pyTorrent only.
# Intended usage:
#   curl -fsSL https://git.linuxiarz.pl/gru/pyTorrent/raw/branch/master/scripts/install_pytorrent.sh | sudo bash

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'USAGE'
Usage: curl -fsSL https://git.linuxiarz.pl/gru/pyTorrent/raw/branch/master/scripts/install_pytorrent.sh | sudo bash -s -- [options]

This bootstrap downloads pyTorrent and forwards all options to scripts/install_pytorrent_only.sh.
Run scripts/install_pytorrent_only.sh --help inside the repository for the full option list.
USAGE
    exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root, for example: curl -fsSL <url> | sudo bash" >&2
    exit 1
fi

REPO_URL="${PYTORRENT_REPO_URL:-https://git.linuxiarz.pl/gru/pyTorrent}"
REPO_BRANCH="${PYTORRENT_REPO_BRANCH:-master}"
WORK_DIR="${PYTORRENT_BOOTSTRAP_DIR:-/tmp/pytorrent-only-installer}"
KEEP_WORK_DIR="${PYTORRENT_KEEP_BOOTSTRAP_DIR:-0}"
ARCHIVE_URL="${PYTORRENT_ARCHIVE_URL:-${REPO_URL%/}/archive/${REPO_BRANCH}.tar.gz}"
PROJECT_DIR="${WORK_DIR}/src"
ARCHIVE_PATH="${WORK_DIR}/pytorrent.tar.gz"

log() { printf '[pyTorrent bootstrap] %s\n' "$*"; }
fail() { printf '[pyTorrent bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

prepare_downloader() {
    # Note: Bootstrap installs only tools required to fetch and unpack the repository.
    if command_exists apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl tar gzip python3 sudo
    elif command_exists dnf; then
        dnf install -y ca-certificates curl tar gzip python3 sudo
    elif command_exists yum; then
        yum install -y ca-certificates curl tar gzip python3 sudo
    elif command_exists pacman; then
        pacman -Sy --noconfirm --needed ca-certificates curl tar gzip python sudo
    fi
    if command_exists curl; then DOWNLOADER="curl"; return; fi
    if command_exists wget; then DOWNLOADER="wget"; return; fi
    fail "curl or wget is required."
}

download_file() {
    local url="$1" destination="$2"
    if [[ "${DOWNLOADER}" == "curl" ]]; then
        curl -fL "${url}" -o "${destination}"
    else
        wget -O "${destination}" "${url}"
    fi
}

cleanup() {
    if [[ "${KEEP_WORK_DIR}" != "1" ]]; then
        rm -rf "${WORK_DIR}"
    else
        log "Keeping bootstrap directory: ${WORK_DIR}"
    fi
}
trap cleanup EXIT

prepare_downloader
rm -rf "${WORK_DIR}"
mkdir -p "${PROJECT_DIR}"
log "Downloading pyTorrent from ${ARCHIVE_URL}"
download_file "${ARCHIVE_URL}" "${ARCHIVE_PATH}"
tar -xzf "${ARCHIVE_PATH}" -C "${PROJECT_DIR}" --strip-components=1
[[ -f "${PROJECT_DIR}/scripts/install_pytorrent_only.sh" ]] || fail "Missing scripts/install_pytorrent_only.sh in downloaded repository."
chmod +x "${PROJECT_DIR}/scripts/install_pytorrent_only.sh"
log "Running pyTorrent-only installer"
bash "${PROJECT_DIR}/scripts/install_pytorrent_only.sh" "$@"

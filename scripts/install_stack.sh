#!/usr/bin/env bash
set -euo pipefail

# Bootstrap installer for pyTorrent + rTorrent.
# Intended usage from a clean server:
#   curl -fsSL https://raw.githubusercontent.com/zdzichu6969/pyTorrent/master/scripts/install_stack.sh | sudo bash
#
# The script downloads the current pyTorrent repository, detects the OS family,
# and runs the matching installer from scripts/stack_installers/.

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root, for example: curl -fsSL <url> | sudo bash" >&2
    exit 1
fi

REPO_URL="${PYTORRENT_REPO_URL:-https://github.com/zdzichu6969/pyTorrent}"
REPO_BRANCH="${PYTORRENT_REPO_BRANCH:-master}"
WORK_DIR="${PYTORRENT_BOOTSTRAP_DIR:-/tmp/pytorrent-stack-installer}"
KEEP_WORK_DIR="${PYTORRENT_KEEP_BOOTSTRAP_DIR:-0}"

default_archive_url() {
    case "${REPO_URL%/}" in
        https://github.com/*)
            printf '%s/archive/refs/heads/%s.tar.gz\n' "${REPO_URL%/}" "${REPO_BRANCH}"
            ;;
        *)
            printf '%s/archive/%s.tar.gz\n' "${REPO_URL%/}" "${REPO_BRANCH}"
            ;;
    esac
}

default_raw_base() {
    case "${REPO_URL%/}" in
        https://github.com/*)
            local path
            path="${REPO_URL#https://github.com/}"
            printf 'https://raw.githubusercontent.com/%s/%s\n' "${path%/}" "${REPO_BRANCH}"
            ;;
        *)
            printf '%s/raw/branch/%s\n' "${REPO_URL%/}" "${REPO_BRANCH}"
            ;;
    esac
}

RAW_BASE="${PYTORRENT_RAW_BASE:-$(default_raw_base)}"
ARCHIVE_URL="${PYTORRENT_ARCHIVE_URL:-$(default_archive_url)}"
PROJECT_DIR="${WORK_DIR}/src"
ARCHIVE_PATH="${WORK_DIR}/pytorrent.tar.gz"

log() {
    printf '[pyTorrent stack] %s\n' "$*"
}

fail() {
    printf '[pyTorrent stack] ERROR: %s\n' "$*" >&2
    exit 1
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

prepare_downloader() {
    # Bootstrap needs both a downloader and tar before repository extraction.
    if command_exists apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates tar curl gzip python3 sudo
    elif command_exists dnf; then
        dnf install -y ca-certificates tar curl gzip python3 sudo
    elif command_exists yum; then
        yum install -y ca-certificates tar curl gzip python3 sudo
    fi

    if command_exists curl; then
        DOWNLOADER="curl"
        return
    fi
    if command_exists wget; then
        DOWNLOADER="wget"
        return
    fi

    if command_exists apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends curl ca-certificates tar gzip python3 sudo
        DOWNLOADER="curl"
        return
    fi
    if command_exists dnf; then
        dnf install -y curl ca-certificates tar gzip python3 sudo
        DOWNLOADER="curl"
        return
    fi
    if command_exists yum; then
        yum install -y curl ca-certificates tar gzip python3 sudo
        DOWNLOADER="curl"
        return
    fi
    if command_exists pacman; then
        pacman -Sy --noconfirm --needed curl ca-certificates tar gzip python sudo
        DOWNLOADER="curl"
        return
    fi

    fail "curl or wget is required and no supported package manager was found."
}

download_file() {
    local url="$1"
    local destination="$2"
    if [[ "${DOWNLOADER}" == "curl" ]]; then
        curl -fL "${url}" -o "${destination}"
    else
        wget -O "${destination}" "${url}"
    fi
}

detect_os_family() {
    if [[ ! -f /etc/os-release ]]; then
        fail "Cannot detect OS: /etc/os-release is missing."
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    local os_id="${ID:-}"
    local os_like="${ID_LIKE:-}"

    case "${os_id} ${os_like}" in
        *debian*|*ubuntu*)
            echo "debian"
            ;;
        *rhel*|*fedora*|*centos*|*rocky*|*almalinux*)
            echo "rhel"
            ;;
        *arch*)
            echo "arch"
            ;;
        *)
            fail "Unsupported OS: ID=${ID:-unknown}, ID_LIKE=${ID_LIKE:-unknown}."
            ;;
    esac
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
mkdir -p "${WORK_DIR}"

log "Downloading pyTorrent from ${ARCHIVE_URL}"
if ! download_file "${ARCHIVE_URL}" "${ARCHIVE_PATH}"; then
    log "Archive download failed, trying raw stack installer fallback."
    mkdir -p "${PROJECT_DIR}/scripts/stack_installers"
    for file in \
        install_stack_debian_ubuntu.sh \
        install_stack_rhel.sh \
        install_stack_arch.sh \
        install_pytorrent_rhel.sh \
        install_rtorrent.py \
        install_rtorrent_rhel.py \
        configure_pytorrent_api.py \
        INSTALL.md
    do
        download_file "${RAW_BASE}/scripts/stack_installers/${file}" "${PROJECT_DIR}/scripts/stack_installers/${file}"
    done
    download_file "${RAW_BASE}/scripts/install_debian_ubuntu.sh" "${PROJECT_DIR}/scripts/install_debian_ubuntu.sh"
else
    mkdir -p "${PROJECT_DIR}"
    tar -xzf "${ARCHIVE_PATH}" -C "${PROJECT_DIR}" --strip-components=1
fi

[[ -d "${PROJECT_DIR}/scripts/stack_installers" ]] || fail "Missing scripts/stack_installers in downloaded repository."

OS_FAMILY="$(detect_os_family)"
case "${OS_FAMILY}" in
    debian)
        INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_debian_ubuntu.sh"
        ;;
    rhel)
        INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_rhel.sh"
        ;;
    arch)
        INSTALLER="${PROJECT_DIR}/scripts/stack_installers/install_stack_arch.sh"
        ;;
    *)
        fail "Unsupported OS family: ${OS_FAMILY}."
        ;;
esac

chmod +x "${PROJECT_DIR}/scripts/stack_installers/"*.sh || true
log "Running ${INSTALLER}"
bash "${INSTALLER}" "$@"

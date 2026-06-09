#!/usr/bin/env bash
#
# Cross-build readsb for Raspberry Pi OS / Debian Trixie ARM64.
#
# Run from MSYS2 UCRT64:
#
#   cd ~/sdrdev/RTL-Pi-ADS-B-Tracker
#   bash tools/build_readsb_arm64_cross_v2.sh
#
# Output:
#
#   vendor/readsb/linux-aarch64/readsb
#

set +e
set +u

SCRIPT_NAME="build_readsb_arm64_cross_v2.sh"
APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
LOG_DIR="${APP_ROOT}/logs"
LOG_FILE="${LOG_DIR}/build_readsb_arm64_cross_v2.log"

mkdir -p "${LOG_DIR}"

exec > >(tee "${LOG_FILE}") 2>&1

finish() {
  RC="$1"
  echo
  echo "============================================================"
  echo "${SCRIPT_NAME} finished with exit code: ${RC}"
  echo "Log file:"
  echo "  ${LOG_FILE}"
  echo "============================================================"
  echo

  if [[ -t 0 ]]; then
    echo "Press Enter to return to the shell..."
    read -r _
  fi

  exit "${RC}"
}

fail() {
  echo
  echo "ERROR: $*"
  finish 1
}

echo "============================================================"
echo "${SCRIPT_NAME}"
echo "Started: $(date)"
echo "============================================================"
echo

if [[ -z "${APP_ROOT}" || ! -d "${APP_ROOT}" ]]; then
  fail "Could not determine APP_ROOT."
fi

echo "App root:"
echo "  ${APP_ROOT}"
echo

if [[ -n "${MSYSTEM:-}" ]]; then
  echo "MSYS2 environment detected:"
  echo "  MSYSTEM=${MSYSTEM}"
else
  echo "WARNING: MSYSTEM is not set. This script is intended for MSYS2 UCRT64."
fi

echo
echo "Checking required commands..."

command -v git >/dev/null 2>&1 || fail "git was not found."
command -v docker >/dev/null 2>&1 || fail "docker was not found. Start Docker Desktop or install Docker CLI."
command -v cygpath >/dev/null 2>&1 || fail "cygpath was not found. This should exist in MSYS2."

echo "git:"
git --version

echo
echo "docker:"
docker --version
if [[ "$?" -ne 0 ]]; then
  fail "docker command exists but did not run successfully."
fi

echo
echo "Checking Docker daemon..."
docker info >/tmp/rtl-pi-docker-info.txt 2>&1
if [[ "$?" -ne 0 ]]; then
  cat /tmp/rtl-pi-docker-info.txt
  fail "Docker daemon is not available. Start Docker Desktop, then rerun this script."
fi

READSB_REPO="${READSB_REPO:-https://github.com/wiedehopf/readsb.git}"
READSB_REF="${READSB_REF:-dev}"

BUILD_DIR="${APP_ROOT}/build/readsb-arm64"
SRC_DIR="${BUILD_DIR}/readsb"
OUT_DIR="${APP_ROOT}/vendor/readsb/linux-aarch64"
OUT_BIN="${OUT_DIR}/readsb"

IMAGE_NAME="${IMAGE_NAME:-rtl-pi-arm64-readsb-builder-v2}"

mkdir -p "${BUILD_DIR}" "${OUT_DIR}"

echo
echo "Configuration:"
echo "  READSB_REPO=${READSB_REPO}"
echo "  READSB_REF=${READSB_REF}"
echo "  BUILD_DIR=${BUILD_DIR}"
echo "  SRC_DIR=${SRC_DIR}"
echo "  OUT_DIR=${OUT_DIR}"
echo "  OUT_BIN=${OUT_BIN}"
echo "  IMAGE_NAME=${IMAGE_NAME}"
echo

echo "Writing Dockerfile..."

cat > "${BUILD_DIR}/Dockerfile" <<'DOCKER_EOF'
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN dpkg --add-architecture arm64 && \
    apt-get update && \
    apt-get install -y \
      git \
      ca-certificates \
      build-essential \
      make \
      pkg-config \
      file \
      gcc-aarch64-linux-gnu \
      g++-aarch64-linux-gnu \
      binutils-aarch64-linux-gnu \
      libc6-dev-arm64-cross \
      linux-libc-dev-arm64-cross \
      zlib1g-dev:arm64 \
      libzstd-dev:arm64 \
      libusb-1.0-0-dev:arm64 \
      librtlsdr-dev:arm64 \
      libncurses-dev:arm64 \
      && rm -rf /var/lib/apt/lists/*

WORKDIR /work
DOCKER_EOF

echo
echo "Building Docker image without cache..."
echo "  ${IMAGE_NAME}"

docker build --no-cache -t "${IMAGE_NAME}" "${BUILD_DIR}"
if [[ "$?" -ne 0 ]]; then
  fail "Docker image build failed."
fi

echo
echo "Preparing readsb source..."

rm -rf "${SRC_DIR}"

git clone --depth 50 "${READSB_REPO}" "${SRC_DIR}"
if [[ "$?" -ne 0 ]]; then
  fail "git clone failed."
fi

cd "${SRC_DIR}" || fail "Could not cd to ${SRC_DIR}"

git fetch --all --tags
git checkout "${READSB_REF}"
if [[ "$?" -ne 0 ]]; then
  fail "Could not checkout READSB_REF=${READSB_REF}"
fi

READSB_COMMIT="$(git rev-parse HEAD 2>/dev/null)"

echo
echo "readsb commit:"
echo "  ${READSB_COMMIT}"

echo
echo "Preparing Docker volume paths..."

SRC_WIN="$(cygpath -w "${SRC_DIR}")"
OUT_WIN="$(cygpath -w "${OUT_DIR}")"

echo "  SRC_WIN=${SRC_WIN}"
echo "  OUT_WIN=${OUT_WIN}"

echo
echo "Running ARM64 cross-build inside Docker..."

MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" docker run --rm \
  -v "${SRC_WIN}:/src" \
  -v "${OUT_WIN}:/out" \
  "${IMAGE_NAME}" \
  bash -lc '
    set -euo pipefail

    export PKG_CONFIG_LIBDIR="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
    export PKG_CONFIG_PATH="/usr/lib/aarch64-linux-gnu/pkgconfig"
    export PKG_CONFIG_SYSROOT_DIR=""

    cd /src

    echo "Container architecture:"
    uname -a

    echo
    echo "Compiler:"
    aarch64-linux-gnu-gcc --version | head -3

    echo
    echo "pkg-config search configuration:"
    echo "  PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR}"
    echo "  PKG_CONFIG_PATH=${PKG_CONFIG_PATH}"

    echo
    echo "librtlsdr pkg-config check:"
    find /usr -name "librtlsdr.pc" -print || true
    pkg-config --modversion librtlsdr
    pkg-config --cflags --libs librtlsdr

    echo
    echo "ncurses pkg-config/header check:"
    find /usr -name "ncurses.pc" -print || true
    find /usr -name "curses.h" -print | head -20 || true
    pkg-config --modversion ncurses || true
    pkg-config --cflags --libs ncurses || true

    echo
    echo "Cleaning..."
    make clean || true

    echo
    echo "Building readsb with RTLSDR=yes..."

    make -j"$(nproc)" \
      CC=aarch64-linux-gnu-gcc \
      RTLSDR=yes \
      OPTIMIZE="-O3" \
      LDFLAGS="-L/usr/lib/aarch64-linux-gnu"

    echo
    echo "Binary file info before strip:"
    file readsb

    echo
    echo "Shared library requirements:"
    aarch64-linux-gnu-readelf -d readsb | grep NEEDED || true

    echo
    echo "Stripping..."
    aarch64-linux-gnu-strip readsb || true

    echo
    echo "Installing to /out/readsb..."
    install -m 0755 readsb /out/readsb

    echo
    echo "Installed binary:"
    ls -lh /out/readsb
    file /out/readsb
  '

if [[ "$?" -ne 0 ]]; then
  fail "Docker cross-build failed."
fi

if [[ ! -f "${OUT_BIN}" ]]; then
  fail "Expected output binary was not created: ${OUT_BIN}"
fi

echo
echo "Writing packaged readsb README..."

cat > "${OUT_DIR}/README.md" <<README_EOF
# Packaged readsb binary

This readsb binary is cross-compiled for Raspberry Pi OS / Debian Trixie ARM64.

Source repo:

${READSB_REPO}

Requested source ref:

${READSB_REF}

Resolved commit:

${READSB_COMMIT}

Build flags:

make CC=aarch64-linux-gnu-gcc RTLSDR=yes OPTIMIZE="-O3"

Installed on Pi as:

/opt/rtl-pi-adsb-tracker/bin/readsb

The Debian /usr/bin/readsb binary is not used by this application.
README_EOF

echo
echo "Final packaged binary:"
ls -lh "${OUT_BIN}"
file "${OUT_BIN}" 2>/dev/null || true

echo
echo "SUCCESS: Packaged ARM64 readsb binary created:"
echo "  ${OUT_BIN}"

finish 0

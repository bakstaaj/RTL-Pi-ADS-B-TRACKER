#!/usr/bin/env bash
set -euo pipefail
IMAGE_NAME="rtl-pi-adsb-cross:trixie-arm64"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DOCKER_PATH="$(cygpath -m "${REPO_ROOT}")"
mkdir -p "${REPO_ROOT}/dist"
echo "Building ARM64 rtl_noaa_survey in Docker..."
MSYS_NO_PATHCONV=1 docker run --rm --volume "${REPO_DOCKER_PATH}:/workspace" --workdir /workspace "${IMAGE_NAME}" bash -lc '
set -euo pipefail
mkdir -p dist
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -Werror -o dist/rtl_noaa_survey src/rtl_noaa_survey.c $(pkg-config --cflags --libs librtlsdr) -lm
file dist/rtl_noaa_survey
'
echo "Built: ${REPO_ROOT}/dist/rtl_noaa_survey"

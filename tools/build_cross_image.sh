#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="rtl-pi-adsb-cross:trixie-arm64"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker build \
    --tag "${IMAGE_NAME}" \
    --file "${REPO_ROOT}/docker/Dockerfile.cross" \
    "${REPO_ROOT}"

echo
echo "Built Docker image: ${IMAGE_NAME}"

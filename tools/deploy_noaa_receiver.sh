#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.pi.env"
LOCAL_BINARY="${REPO_ROOT}/dist/rtl_noaa_receiver"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}"
    exit 1
fi

if [[ ! -f "${LOCAL_BINARY}" ]]; then
    echo "Missing ${LOCAL_BINARY}; run ./tools/build_noaa_receiver.sh first."
    exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

echo "Deploying rtl_noaa_receiver to ${PI_USER}@${PI_HOST}:${PI_DEPLOY_DIR}/bin/"

ssh "${PI_USER}@${PI_HOST}" "
    mkdir -p '${PI_DEPLOY_DIR}/bin' '${PI_DEPLOY_DIR}/test_output'
"

scp "${LOCAL_BINARY}" \
    "${PI_USER}@${PI_HOST}:${PI_DEPLOY_DIR}/bin/rtl_noaa_receiver"

ssh "${PI_USER}@${PI_HOST}" "
    chmod +x '${PI_DEPLOY_DIR}/bin/rtl_noaa_receiver'
    file '${PI_DEPLOY_DIR}/bin/rtl_noaa_receiver'
"

echo "Deployment complete."

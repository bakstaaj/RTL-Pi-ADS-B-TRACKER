#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
LOCAL_BINARY="${REPO_ROOT}/dist/rtl_noaa_survey"
[[ -f "${LOCAL_BINARY}" ]] || { echo "Run ./tools/build_noaa_survey.sh first."; exit 1; }
ssh "${PI_USER}@${PI_HOST}" "mkdir -p '${PI_DEPLOY_DIR}/bin' '${PI_DEPLOY_DIR}/test_output'"
scp "${LOCAL_BINARY}" "${PI_USER}@${PI_HOST}:${PI_DEPLOY_DIR}/bin/rtl_noaa_survey"
ssh "${PI_USER}@${PI_HOST}" "chmod +x '${PI_DEPLOY_DIR}/bin/rtl_noaa_survey'; file '${PI_DEPLOY_DIR}/bin/rtl_noaa_survey'"

#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.pi.env"
TEMP_CONFIG="$(mktemp)"

cleanup() {
    rm -f "${TEMP_CONFIG}"
}
trap cleanup EXIT

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}"
    exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

: "${PI_HOST:?Missing PI_HOST in .pi.env}"
: "${PI_USER:?Missing PI_USER in .pi.env}"
: "${PI_DEPLOY_DIR:?Missing PI_DEPLOY_DIR in .pi.env}"
: "${PI_READSB_JSON_DIR:?Missing PI_READSB_JSON_DIR in .pi.env}"
: "${PI_AUDIO_SERIAL:?Missing PI_AUDIO_SERIAL in .pi.env}"
: "${PI_NOAA_STATION:?Missing PI_NOAA_STATION in .pi.env}"
: "${PI_NOAA_FREQ_HZ:?Missing PI_NOAA_FREQ_HZ in .pi.env}"

cat > "${TEMP_CONFIG}" <<CONFIGEOF
RTL_PI_ROOT=${PI_DEPLOY_DIR}
RTL_PI_READSB_JSON_DIR=${PI_READSB_JSON_DIR}
RTL_PI_AUDIO_BINARY=${PI_DEPLOY_DIR}/bin/rtl_noaa_receiver
RTL_PI_AUDIO_SERIAL=${PI_AUDIO_SERIAL}
RTL_PI_NOAA_STATION=${PI_NOAA_STATION}
RTL_PI_NOAA_FREQ_HZ=${PI_NOAA_FREQ_HZ}
RTL_PI_RF_GAIN_DB=40.2
RTL_PI_AUDIO_OUTPUT_GAIN=15000
RTL_PI_BIND=0.0.0.0
RTL_PI_PORT=8080
CONFIGEOF

echo "Deploying API backend to ${PI_USER}@${PI_HOST}..."

ssh "${PI_USER}@${PI_HOST}" "
    mkdir -p \
        '${PI_DEPLOY_DIR}/app' \
        '${PI_DEPLOY_DIR}/web' \
        '${PI_DEPLOY_DIR}/settings' \
        '${PI_DEPLOY_DIR}/test_output'
"

scp "${REPO_ROOT}/src/rtl_pi_api.py" \
    "${PI_USER}@${PI_HOST}:${PI_DEPLOY_DIR}/app/rtl_pi_api.py"

scp "${REPO_ROOT}/web/index.html" \
    "${PI_USER}@${PI_HOST}:${PI_DEPLOY_DIR}/web/index.html"

scp "${TEMP_CONFIG}" \
    "${PI_USER}@${PI_HOST}:${PI_DEPLOY_DIR}/config.env"

scp "${REPO_ROOT}/packaging/systemd/rtl-pi-api.service" \
    "${PI_USER}@${PI_HOST}:/tmp/rtl-pi-api.service"

ssh -tt "${PI_USER}@${PI_HOST}" "
    set -e
    chmod +x '${PI_DEPLOY_DIR}/app/rtl_pi_api.py'
    sudo -v
    sudo cp /tmp/rtl-pi-api.service /etc/systemd/system/rtl-pi-api.service
    sudo systemctl daemon-reload
    sudo systemctl enable rtl-pi-api.service
    sudo systemctl restart rtl-pi-api.service
    sleep 2
    systemctl --no-pager --full status rtl-pi-api.service
"

echo
echo "API status URL:"
echo "  http://${PI_HOST}:8080/api/status"

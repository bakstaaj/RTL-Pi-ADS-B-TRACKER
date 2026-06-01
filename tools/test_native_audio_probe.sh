#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.pi.env"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}"
    exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

CAPTURE_SECONDS="${1:-60}"
NOAA_STATION="${PI_NOAA_STATION:-KGG68_HOUSTON}"
NOAA_FREQ_HZ="${PI_NOAA_FREQ_HZ:-162400000}"
REMOTE_CAPTURE="${PI_DEPLOY_DIR}/test_output/noaa_${NOAA_STATION}_${NOAA_FREQ_HZ}_probe.iq"

BEFORE_MESSAGES="$(
    ssh "${PI_USER}@${PI_HOST}" \
        "jq -r '.messages' '${PI_READSB_JSON_DIR}/aircraft.json'"
)"

echo "NOAA test station: ${NOAA_STATION}"
echo "NOAA frequency:    ${NOAA_FREQ_HZ} Hz"
echo "ADS-B messages before native probe: ${BEFORE_MESSAGES}"
echo

ssh "${PI_USER}@${PI_HOST}" "
    set -e
    mkdir -p '${PI_DEPLOY_DIR}/test_output'
    rm -f '${REMOTE_CAPTURE}'

    '${PI_DEPLOY_DIR}/bin/rtl_audio_probe' \
        --serial '${PI_AUDIO_SERIAL}' \
        --freq-hz '${NOAA_FREQ_HZ}' \
        --sample-rate 1024000 \
        --seconds '${CAPTURE_SECONDS}' \
        --gain-db 40.2 \
        --iq-output '${REMOTE_CAPTURE}'
"

AFTER_MESSAGES="$(
    ssh "${PI_USER}@${PI_HOST}" \
        "jq -r '.messages' '${PI_READSB_JSON_DIR}/aircraft.json'"
)"

echo
echo "ADS-B messages after native probe:  ${AFTER_MESSAGES}"
echo "Messages received while probe ran: $((AFTER_MESSAGES - BEFORE_MESSAGES))"

ssh "${PI_USER}@${PI_HOST}" "
    echo
    echo '=== ADS-B service state ==='
    systemctl is-active rtl-pi-readsb.service

    echo
    echo '=== Latest aircraft summary ==='
    jq '{
      messages: .messages,
      aircraft_count: (.aircraft | length),
      aircraft_with_position: ([.aircraft[] | select(.lat != null and .lon != null)] | length)
    }' '${PI_READSB_JSON_DIR}/aircraft.json'

    echo
    echo '=== Probe I/Q capture ==='
    ls -lh '${REMOTE_CAPTURE}'
"

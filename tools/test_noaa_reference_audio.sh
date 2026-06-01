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

CAPTURE_SECONDS="${1:-30}"
NOAA_STATION="${PI_NOAA_STATION:-KGG68_HOUSTON}"
NOAA_FREQ_HZ="${PI_NOAA_FREQ_HZ:-162400000}"

LOCAL_OUTPUT_DIR="${REPO_ROOT}/test_output"
LOCAL_OUTPUT="${LOCAL_OUTPUT_DIR}/noaa_${NOAA_STATION}_${NOAA_FREQ_HZ}_reference.wav"
REMOTE_OUTPUT="${PI_DEPLOY_DIR}/test_output/noaa_${NOAA_STATION}_${NOAA_FREQ_HZ}_reference.wav"

mkdir -p "${LOCAL_OUTPUT_DIR}"

BEFORE_MESSAGES="$(
    ssh "${PI_USER}@${PI_HOST}" \
        "jq -r '.messages' '${PI_READSB_JSON_DIR}/aircraft.json'"
)"

echo "Capturing NOAA reference audio while ADS-B remains active"
echo "  Station:   ${NOAA_STATION}"
echo "  Frequency: ${NOAA_FREQ_HZ} Hz"
echo "  Receiver:  ${PI_AUDIO_SERIAL}"
echo "  Duration:  ${CAPTURE_SECONDS} seconds"
echo
echo "ADS-B messages before audio capture: ${BEFORE_MESSAGES}"
echo

ssh "${PI_USER}@${PI_HOST}" "
    set -e
    mkdir -p '${PI_DEPLOY_DIR}/test_output'
    rm -f '${REMOTE_OUTPUT}'

    timeout '${CAPTURE_SECONDS}s' rtl_fm \
        -d '${PI_AUDIO_SERIAL}' \
        -f '${NOAA_FREQ_HZ}' \
        -M fm \
        -s 24000 \
        -r 24000 \
        -g 40 \
        -E deemp \
      | sox \
          -t raw \
          -r 24000 \
          -e signed-integer \
          -b 16 \
          -c 1 \
          - \
          '${REMOTE_OUTPUT}'

    echo
    echo '=== Remote WAV information ==='
    soxi '${REMOTE_OUTPUT}'
"

scp "${PI_USER}@${PI_HOST}:${REMOTE_OUTPUT}" "${LOCAL_OUTPUT}"

AFTER_MESSAGES="$(
    ssh "${PI_USER}@${PI_HOST}" \
        "jq -r '.messages' '${PI_READSB_JSON_DIR}/aircraft.json'"
)"

echo
echo "ADS-B messages after audio capture:  ${AFTER_MESSAGES}"
echo "Messages received during capture:    $((AFTER_MESSAGES - BEFORE_MESSAGES))"
echo
echo "Local WAV file:"
ls -lh "${LOCAL_OUTPUT}"
echo
echo "Play this file on Windows:"
cygpath -w "${LOCAL_OUTPUT}"

#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
mkdir -p "${REPO_ROOT}/test_output"

FREQ_HZ="${PI_NOAA_FREQ_HZ:-162400000}"
OUT="${PI_DEPLOY_DIR}/test_output/rf_metric_known_carrier.wav"

echo "Stopping any browser audio use of the receiver..."
curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/live/stop" >/dev/null || true
curl -fsS -X POST "http://${PI_HOST}:8080/api/airband/scan/activity/stop" >/dev/null || true
sleep 1

echo "Testing corrected RF carrier metric against known strong NOAA carrier ${FREQ_HZ} Hz."
echo "The WAV audio is AM-demodulated and is not the quality test; the RF estimated SNR is."
ssh "${PI_USER}@${PI_HOST}" "
    '${PI_DEPLOY_DIR}/bin/rtl_airband_receiver' \
        --serial '${PI_AUDIO_SERIAL}' \
        --freq-hz '${FREQ_HZ}' \
        --duration-ms 1000 \
        --gain-db 40.2 \
        --audio-gain 120000 \
        --wav-output '${OUT}'
"

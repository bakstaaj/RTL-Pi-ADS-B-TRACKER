#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/test_output"
source "${REPO_ROOT}/.pi.env"
SURVEY_SECONDS="${1:-2}"
REMOTE_JSON="${PI_DEPLOY_DIR}/test_output/noaa_survey_results.json"
LOCAL_JSON="${REPO_ROOT}/test_output/noaa_survey_results.json"
curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/live/stop" >/dev/null || true
BEFORE_MESSAGES="$(ssh "${PI_USER}@${PI_HOST}" "jq -r '.messages' '${PI_READSB_JSON_DIR}/aircraft.json'")"
echo "Running seven-channel NOAA survey using ${PI_AUDIO_SERIAL}..."
ssh "${PI_USER}@${PI_HOST}" "rm -f '${REMOTE_JSON}'; '${PI_DEPLOY_DIR}/bin/rtl_noaa_survey' --serial '${PI_AUDIO_SERIAL}' --seconds '${SURVEY_SECONDS}' --gain-db 40.2 --json-output '${REMOTE_JSON}'"
scp "${PI_USER}@${PI_HOST}:${REMOTE_JSON}" "${LOCAL_JSON}"
AFTER_MESSAGES="$(ssh "${PI_USER}@${PI_HOST}" "jq -r '.messages' '${PI_READSB_JSON_DIR}/aircraft.json'")"
echo
python3 -m json.tool "${LOCAL_JSON}"
echo "ADS-B messages while surveying: $((AFTER_MESSAGES - BEFORE_MESSAGES))"
echo "Expected Houston-area winner: 162.400 MHz (KGG68) when received clearly."

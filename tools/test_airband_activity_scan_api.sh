#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/test_output"

# shellcheck disable=SC1090
source "${REPO_ROOT}/.pi.env"

STATUS_FILE="${REPO_ROOT}/test_output/airband_scan_status.json"
DETECTED_WAV="${REPO_ROOT}/test_output/airband_detected_latest.wav"

stop_scan() {
    curl -fsS -X POST \
        "http://${PI_HOST}:8080/api/airband/scan/activity/stop" \
        >/dev/null 2>&1 || true
}

trap stop_scan INT TERM

echo "Stopping any prior Airband activity scan..."
stop_scan
sleep 1

echo "Starting Airband activity scan..."
curl -fsS -X POST \
    "http://${PI_HOST}:8080/api/airband/scan/activity/start" |
python3 -m json.tool

echo
echo "Monitoring for up to 90 seconds..."

for _ in $(seq 1 90); do
    curl -fsS \
        "http://${PI_HOST}:8080/api/airband/scan/status" \
        -o "${STATUS_FILE}"

    python3 - "${STATUS_FILE}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    status = json.load(stream)

channel = status.get("airband_current_channel") or {}
frequency = channel.get("frequency_mhz", "-")

print(
    "state={} cycle={} sampled={} now={}MHz audio={}dBFS carrier_snr={}dB".format(
        status.get("airband_scan_state"),
        status.get("airband_scan_cycles"),
        status.get("airband_channels_scanned"),
        frequency,
        status.get("airband_last_measurement_dbfs"),
        status.get("airband_last_signal_snr_db"),
    )
)
PY

    RUNNING="$(
        python3 - "${STATUS_FILE}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    status = json.load(stream)

print("true" if status.get("airband_scan_running") else "false")
PY
    )"

    if [[ "${RUNNING}" != "true" ]]; then
        echo
        echo "=== Final scan status ==="
        python3 -m json.tool "${STATUS_FILE}"

        HAS_DETECTION="$(
            python3 - "${STATUS_FILE}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    status = json.load(stream)

print("true" if status.get("airband_last_detection") else "false")
PY
        )"

        if [[ "${HAS_DETECTION}" == "true" ]]; then
            curl -fsS \
                "http://${PI_HOST}:8080/api/airband/scan/last_audio.wav" \
                -o "${DETECTED_WAV}"

            echo
            file "${DETECTED_WAV}"
            ls -lh "${DETECTED_WAV}"
            echo
            echo "Play captured possible activity:"
            cygpath -w "${DETECTED_WAV}"
        else
            echo "The scan stopped without a detected audio sample."
        fi

        exit 0
    fi

    sleep 1
done

echo
echo "No activity detected during the 90-second monitoring window."
echo "Stopping scan."
stop_scan

curl -fsS \
    "http://${PI_HOST}:8080/api/airband/scan/status" |
python3 -m json.tool

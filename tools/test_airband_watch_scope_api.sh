#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/test_output"
source "${REPO_ROOT}/.pi.env"

SCOPE="${1:-continuous}"
SECONDS="${2:-180}"
STATUS_FILE="${REPO_ROOT}/test_output/airband_watch_status.json"

stop_scan() {
    curl -fsS -X POST "http://${PI_HOST}:8080/api/airband/scan/activity/stop" >/dev/null 2>&1 || true
}

trap stop_scan INT TERM
stop_scan
sleep 1

echo "Starting Airband watch scan: scope=${SCOPE}, monitor=${SECONDS} seconds"
curl -fsS -X POST "http://${PI_HOST}:8080/api/airband/scan/activity/start?scope=${SCOPE}" |
python3 -m json.tool

for _ in $(seq 1 "${SECONDS}"); do
    curl -fsS "http://${PI_HOST}:8080/api/airband/scan/status" -o "${STATUS_FILE}"

    python3 - "${STATUS_FILE}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    s = json.load(stream)
c = s.get("airband_current_channel") or {}
b = s.get("airband_best_candidate") or {}
bc = b.get("channel") or {}
print(
    "state={} scope={} cycle={} sampled={} now={}MHz carrier_snr={}dB best={}MHz/{}dB".format(
        s.get("airband_scan_state"),
        s.get("airband_scan_scope"),
        s.get("airband_scan_cycles"),
        s.get("airband_channels_scanned"),
        c.get("frequency_mhz", "-"),
        s.get("airband_last_signal_snr_db"),
        bc.get("frequency_mhz", "-"),
        b.get("rf_estimated_snr_db", "-"),
    )
)
PY

    RUNNING="$(python3 - "${STATUS_FILE}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print("true" if json.load(stream).get("airband_scan_running") else "false")
PY
)"
    if [[ "${RUNNING}" != "true" ]]; then
        break
    fi
    sleep 1
done

stop_scan
sleep 1
curl -fsS "http://${PI_HOST}:8080/api/airband/scan/status" -o "${STATUS_FILE}"

echo
echo "=== Final scan/watch status ==="
python3 -m json.tool "${STATUS_FILE}"

HAS_DETECTION="$(python3 - "${STATUS_FILE}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print("true" if json.load(stream).get("airband_last_detection") else "false")
PY
)"
HAS_BEST="$(python3 - "${STATUS_FILE}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    print("true" if json.load(stream).get("airband_best_candidate") else "false")
PY
)"

if [[ "${HAS_DETECTION}" == "true" ]]; then
    curl -fsS "http://${PI_HOST}:8080/api/airband/scan/last_audio.wav" \
        -o "${REPO_ROOT}/test_output/airband_detected_latest.wav"
    echo "Threshold detection captured:"
    cygpath -w "${REPO_ROOT}/test_output/airband_detected_latest.wav"
elif [[ "${HAS_BEST}" == "true" ]]; then
    curl -fsS "http://${PI_HOST}:8080/api/airband/scan/best_audio.wav" \
        -o "${REPO_ROOT}/test_output/airband_best_candidate.wav"
    echo "No threshold detection. Strongest candidate captured for review:"
    cygpath -w "${REPO_ROOT}/test_output/airband_best_candidate.wav"
fi

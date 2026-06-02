#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/test_output"
source "${REPO_ROOT}/.pi.env"

curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/live/stop" >/dev/null || true

echo "Starting NOAA Auto Select survey and live listening..."
curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/auto/start" \
    -o "${REPO_ROOT}/test_output/noaa_auto_start_status.json"

python3 - <<'PYJSON'
import json
from pathlib import Path
data = json.loads(Path("test_output/noaa_auto_start_status.json").read_text())
print(json.dumps({
    "audio_mode": data.get("audio_mode"),
    "live_audio_running": data.get("live_audio_running"),
    "selected_frequency_hz": data.get("noaa_frequency_hz"),
    "survey_best_frequency_hz": (data.get("last_noaa_survey") or {}).get("best_frequency_hz"),
    "ranked_channels": (data.get("last_noaa_survey") or {}).get("channels"),
}, indent=2))
PYJSON

sleep 2
echo
echo "Fetching live audio block from auto-selected frequency..."
curl -fsS -D "${REPO_ROOT}/test_output/noaa_auto_block_headers.txt" \
    "http://${PI_HOST}:8080/api/noaa/live/audio.wav?from=0&samples=12000" \
    -o "${REPO_ROOT}/test_output/noaa_auto_block.wav"
cat "${REPO_ROOT}/test_output/noaa_auto_block_headers.txt"
file "${REPO_ROOT}/test_output/noaa_auto_block.wav"
ls -lh "${REPO_ROOT}/test_output/noaa_auto_block.wav"

echo
echo "Stopping auto-selected live listening..."
curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/live/stop" | python3 -m json.tool

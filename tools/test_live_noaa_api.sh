#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/test_output"
# shellcheck disable=SC1090
source "${REPO_ROOT}/.pi.env"
echo "Starting live NOAA backend..."
curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/live/start" | python3 -m json.tool
sleep 2
echo
echo "Fetching first sequential one-half second WAV block..."
curl -fsS -D "${REPO_ROOT}/test_output/live_block_headers.txt" "http://${PI_HOST}:8080/api/noaa/live/audio.wav?from=0&samples=12000" -o "${REPO_ROOT}/test_output/live_block_000.wav"
cat "${REPO_ROOT}/test_output/live_block_headers.txt"
file "${REPO_ROOT}/test_output/live_block_000.wav"
ls -lh "${REPO_ROOT}/test_output/live_block_000.wav"
SOURCE_SAMPLES="$(tr -d '\r' < "${REPO_ROOT}/test_output/live_block_headers.txt" | awk -F': ' 'tolower($1) == "x-source-samples" {print $2}')"
echo
echo "Fetching next sequential block from source cursor ${SOURCE_SAMPLES}..."
curl -fsS "http://${PI_HOST}:8080/api/noaa/live/audio.wav?from=${SOURCE_SAMPLES}&samples=12000" -o "${REPO_ROOT}/test_output/live_block_001.wav"
file "${REPO_ROOT}/test_output/live_block_001.wav"
ls -lh "${REPO_ROOT}/test_output/live_block_001.wav"
echo
echo "Stopping live NOAA backend..."
curl -fsS -X POST "http://${PI_HOST}:8080/api/noaa/live/stop" | python3 -m json.tool

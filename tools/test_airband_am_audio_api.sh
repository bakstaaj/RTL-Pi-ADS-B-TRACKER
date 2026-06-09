#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
mkdir -p "${REPO_ROOT}/test_output"

FREQUENCY_HZ="${1:-}"
SECONDS="${2:-10}"

if [[ -z "${FREQUENCY_HZ}" ]]; then
    echo "Usage: $0 <frequency_hz> [seconds]"
    echo
    echo "Nearby channels available for testing:"
    curl -fsS "http://${PI_HOST}:8080/api/airband/channels" |
    python3 -c 'import json,sys; data=json.load(sys.stdin); [print(f"{c[\"frequency_hz\"]}  {c[\"frequency_mhz\"]:.3f} MHz  {c.get(\"airport_id\") or \"\"}  {c.get(\"use\") or \"\"}  {c[\"distance_miles\"]} mi") for c in data.get("channels", [])[:30]]'
    exit 2
fi

OUT="${REPO_ROOT}/test_output/airband_${FREQUENCY_HZ}_api.wav"
echo "Capturing ${SECONDS} seconds of AM audio on ${FREQUENCY_HZ} Hz..."
curl -fS "http://${PI_HOST}:8080/api/airband/capture.wav?frequency_hz=${FREQUENCY_HZ}&seconds=${SECONDS}" -o "${OUT}"
file "${OUT}"
ls -lh "${OUT}"
echo "Play:"
cygpath -w "${OUT}"

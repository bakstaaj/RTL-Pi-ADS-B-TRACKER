#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
mkdir -p "${REPO_ROOT}/test_output"

echo "=== Nearby Airband channels ==="
HTTP_CODE="$(
    curl -sS -o "${REPO_ROOT}/test_output/nearby_airband_channels.json" \
        -w '%{http_code}' "http://${PI_HOST}:8080/api/airband/channels"
)"
echo "HTTP response code: ${HTTP_CODE}"

if [[ "${HTTP_CODE}" != "200" ]]; then
    python3 -m json.tool "${REPO_ROOT}/test_output/nearby_airband_channels.json"
    exit 1
fi

python3 - "${REPO_ROOT}/test_output/nearby_airband_channels.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as stream:
    data = json.load(stream)
print("Receiver:", data["receiver_location"])
print("Radius miles:", data["radius_miles"])
print("Channel count:", data["channel_count"])
print("Nearest 20:")
for channel in data["channels"][:20]:
    print(
        f'  {channel["frequency_mhz"]:8.3f} MHz  '
        f'{channel["distance_miles"]:6.1f} mi  '
        f'{str(channel.get("airport_id") or ""):8}  '
        f'{channel.get("use") or ""}'
    )
PY

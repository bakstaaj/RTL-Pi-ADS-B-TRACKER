#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
mkdir -p "${REPO_ROOT}/test_output"

echo "=== Trail collector service ==="
ssh "${PI_USER}@${PI_HOST}" "systemctl is-active rtl-pi-trail-collector.service"

echo
echo "=== Waiting for collector samples ==="
sleep 5

curl -fsS "http://${PI_HOST}:8080/api/trails/history" \
    -o "${REPO_ROOT}/test_output/pi_aircraft_trails_history.json"

python3 - "${REPO_ROOT}/test_output/pi_aircraft_trails_history.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
trails = data.get("trails", {})
point_count = sum(len(points) for points in trails.values())
print("Source:", data.get("source"))
print("Updated UTC:", data.get("updated_utc"))
print("Tracked aircraft:", len(trails))
print("Stored points:", point_count)
for key, points in list(trails.items())[:8]:
    print(f"  {key}: {len(points)} points")
PY

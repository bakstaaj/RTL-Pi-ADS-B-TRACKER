#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
mkdir -p "${REPO_ROOT}/test_output"

BEFORE="${REPO_ROOT}/test_output/trails_before_pi_clear.json"
CLEAR_RESPONSE="${REPO_ROOT}/test_output/trails_clear_response.json"
AFTER="${REPO_ROOT}/test_output/trails_after_pi_clear.json"

curl -fsS "http://${PI_HOST}:8080/api/trails/history" -o "${BEFORE}"

echo "=== Before clear ==="
python3 - "${BEFORE}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
trails = data.get("trails", {})
print("Tracked aircraft:", len(trails))
print("Stored points:", sum(len(points) for points in trails.values()))
PY

echo
echo "Clearing Pi trail history..."
curl -fsS -X POST "http://${PI_HOST}:8080/api/trails/clear" -o "${CLEAR_RESPONSE}"
python3 -m json.tool "${CLEAR_RESPONSE}"

echo
echo "Waiting for the running collector to accept the clear watermark and gather new post-clear points..."
sleep 4
curl -fsS "http://${PI_HOST}:8080/api/trails/history" -o "${AFTER}"

python3 - "${CLEAR_RESPONSE}" "${AFTER}" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    cleared = json.load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    after = json.load(stream)

watermark = int(cleared["cleared_utc_ms"])
trails = after.get("trails", {})
points = [point for aircraft_points in trails.values() for point in aircraft_points]
old_points = [point for point in points if int(point.get("time", 0)) < watermark]

print("=== After clear and collector cycle ===")
print("Clear watermark ms:", watermark)
print("Tracked aircraft:", len(trails))
print("Post-clear stored points:", len(points))
print("Pre-clear points returned:", len(old_points))

if old_points:
    raise SystemExit("FAIL: Pi history still contains pre-clear trail points.")
print("PASS: Pi history contains no pre-clear trail points.")
PY

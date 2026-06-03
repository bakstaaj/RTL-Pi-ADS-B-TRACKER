#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"

echo "=== Saved receiver location and Airband radius ==="
curl -fsS "http://${PI_HOST}:8080/api/status" |
python3 -c 'import json,sys; s=json.load(sys.stdin); print(json.dumps({"receiver_location": s.get("receiver_location"), "saved_noaa_selection_available": s.get("saved_noaa_selection_available"), "airband_scan_running": s.get("airband_scan_running"), "airband_current_channel": s.get("airband_current_channel")}, indent=2))'

echo
echo "=== Nearby Airband channel distance summary ==="
curl -fsS "http://${PI_HOST}:8080/api/airband/channels" |
python3 -c 'import json,sys; d=json.load(sys.stdin); channels=d.get("channels", []); print(f"radius_miles={d.get('"'radius_miles'"')} channel_count={d.get('"'channel_count'"')}"); print(f"maximum_channel_distance_miles={max((float(c.get('"'distance_miles'"', 0)) for c in channels), default=0):.1f}")'

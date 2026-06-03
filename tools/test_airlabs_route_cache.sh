#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
FLIGHT="${1:-}"

if [[ -z "${FLIGHT}" ]]; then
  echo "Usage: ./tools/test_airlabs_route_cache.sh UAL1234"
  echo "Use a currently active airline callsign from the aircraft list."
  exit 1
fi

mkdir -p test_output

echo "=== Clear existing cache before test ==="
curl -fsS -X POST "http://${PI_HOST}:8080/api/diagnostics/airlabs/cache/clear" | python3 -m json.tool
echo
echo "=== First lookup: should be fresh and cached if matched ==="
curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/route?flight=${FLIGHT}" | tee test_output/airlabs_cache_first.json | python3 -m json.tool
echo
echo "=== Second lookup: should show cache_hit true when first matched ==="
curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/route?flight=${FLIGHT}" | tee test_output/airlabs_cache_second.json | python3 -m json.tool
echo
echo "=== Current cache status ==="
curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/status" | python3 -m json.tool

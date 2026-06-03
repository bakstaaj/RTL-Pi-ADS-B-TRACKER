#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
FLIGHT="${1:-}"

echo "=== AirLabs backend diagnostic configuration ==="
curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/status" | python3 -m json.tool
echo

if [[ -z "${FLIGHT}" ]]; then
  echo "Provide a currently visible commercial ICAO callsign for route testing."
  echo "Example: ./tools/test_airlabs_backend_diagnostic.sh UAL1234"
  exit 0
fi

echo "=== AirLabs route diagnostic for ${FLIGHT} ==="
curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/route?flight=${FLIGHT}" | python3 -m json.tool

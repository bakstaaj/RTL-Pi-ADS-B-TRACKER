#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"
FLIGHT="${1:-}"

echo "=== AirLabs configuration ==="
curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/status" | python3 -m json.tool
echo

if [[ -n "${FLIGHT}" ]]; then
  echo "=== AirLabs route test for ${FLIGHT} ==="
  curl -fsS "http://${PI_HOST}:8080/api/diagnostics/airlabs/route?flight=${FLIGHT}" | python3 -m json.tool
else
  echo "Provide an active callsign to test a route:"
  echo "  ./tools/test_airlabs_ui_integration.sh UAL1234"
fi

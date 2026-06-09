#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/test_output"
source "${REPO_ROOT}/.pi.env"
echo "=== Current receiver-location setting ==="
curl -fsS "http://${PI_HOST}:8080/api/settings/receiver" | python3 -m json.tool
echo
echo "=== Airband prerequisite enforcement ==="
HTTP_CODE="$(curl -sS -o "${REPO_ROOT}/test_output/airband_start_response.json" -w '%{http_code}' -X POST "http://${PI_HOST}:8080/api/airband/scan/start")"
echo "HTTP response code: ${HTTP_CODE}"
python3 -m json.tool "${REPO_ROOT}/test_output/airband_start_response.json"

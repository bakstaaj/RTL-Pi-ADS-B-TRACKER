#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.pi.env"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing ${ENV_FILE}"
    exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

echo "Testing Pi connection at ${PI_USER}@${PI_HOST}..."

ssh -o BatchMode=yes -o ConnectTimeout=8 "${PI_USER}@${PI_HOST}" "
set -e
echo 'Hostname:' \$(hostname)
echo 'Architecture:' \$(dpkg --print-architecture)
echo 'ADS-B service:' \$(systemctl is-active rtl-pi-readsb.service)
echo
echo 'ADS-B receiver JSON:'
ls -lh '${PI_READSB_JSON_DIR}/aircraft.json'
echo
echo 'Current aircraft summary:'
jq '{
  messages: .messages,
  aircraft_count: (.aircraft | length),
  positioned: ([.aircraft[] | select(.lat != null and .lon != null)] | length)
}' '${PI_READSB_JSON_DIR}/aircraft.json'
"

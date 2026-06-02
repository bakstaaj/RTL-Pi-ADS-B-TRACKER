#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_ROOT}/.pi.env"

: "${PI_USER:?PI_USER must be set in .pi.env}"
: "${PI_HOST:?PI_HOST must be set in .pi.env}"
: "${PI_DEPLOY_DIR:?PI_DEPLOY_DIR must be set in .pi.env}"
: "${PI_READSB_JSON_DIR:?PI_READSB_JSON_DIR must be set in .pi.env}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

sed "s|@DEPLOY_DIR@|${PI_DEPLOY_DIR}|g" \
    "${REPO_ROOT}/packaging/systemd/rtl-pi-trail-collector.service.template" \
    > "${TMP_DIR}/rtl-pi-trail-collector.service"

cat > "${TMP_DIR}/trail_collector.env" <<EOF
RTL_PI_READSB_AIRCRAFT_JSON=${PI_READSB_JSON_DIR}/aircraft.json
RTL_PI_TRAIL_HISTORY_PATH=${PI_DEPLOY_DIR}/settings/aircraft_trails_history.json
RTL_PI_TRAIL_SAMPLE_SECONDS=2
RTL_PI_TRAIL_RETENTION_MINUTES=240
RTL_PI_TRAIL_MAX_POINTS_PER_AIRCRAFT=7200
EOF

echo "Deploying Pi-side aircraft trail collector..."
ssh "${PI_USER}@${PI_HOST}" "mkdir -p /tmp/rtl-pi-trail-collector-upload"
scp "${REPO_ROOT}/src/rtl_trail_collector.py" \
    "${TMP_DIR}/rtl-pi-trail-collector.service" \
    "${TMP_DIR}/trail_collector.env" \
    "${PI_USER}@${PI_HOST}:/tmp/rtl-pi-trail-collector-upload/"

ssh -t "${PI_USER}@${PI_HOST}" "
    sudo install -d -o pi -g pi '${PI_DEPLOY_DIR}/bin' '${PI_DEPLOY_DIR}/config' '${PI_DEPLOY_DIR}/settings'
    sudo install -m 0755 /tmp/rtl-pi-trail-collector-upload/rtl_trail_collector.py '${PI_DEPLOY_DIR}/bin/rtl_trail_collector.py'
    sudo install -m 0644 /tmp/rtl-pi-trail-collector-upload/trail_collector.env '${PI_DEPLOY_DIR}/config/trail_collector.env'
    sudo install -m 0644 /tmp/rtl-pi-trail-collector-upload/rtl-pi-trail-collector.service /etc/systemd/system/rtl-pi-trail-collector.service
    sudo chown -R pi:pi '${PI_DEPLOY_DIR}/settings'
    sudo systemctl daemon-reload
    sudo systemctl enable --now rtl-pi-trail-collector.service
    sudo systemctl --no-pager --full status rtl-pi-trail-collector.service | sed -n '1,14p'
"

#!/usr/bin/env bash
#
# Fresh Raspberry Pi bootstrap installer for RTL-Pi-ADS-B-Tracker.
#
# Run on the Raspberry Pi AFTER completing the pre-install apt setup:
#
#   sudo apt-get update
#   sudo apt-get -y full-upgrade
#   sudo reboot
#
# Then:
#
#   curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh
#   sudo bash /tmp/bootstrap_fresh_pi_install.sh
#
# This bootstrap intentionally does not run apt update. Apt repository
# repair/update is a pre-install Pi setup task.
#
# Optional:
#
#   APP_REF=v3.3.0 sudo bash /tmp/bootstrap_fresh_pi_install.sh
#   SKIP_SDR_SERIAL_SETUP=1 sudo bash /tmp/bootstrap_fresh_pi_install.sh
#

set -Eeuo pipefail

APP_REPO="${APP_REPO:-https://github.com/bakstaaj/RTL-Pi-ADS-B-Tracker.git}"
APP_REF="${APP_REF:-main}"
APP_ROOT="${APP_ROOT:-/opt/rtl-pi-adsb-tracker}"
APP_USER="${APP_USER:-rtladsb}"
APP_GROUP="${APP_GROUP:-rtladsb}"

ADSB_SERIAL="${ADSB_SERIAL:-00001090}"
AUDIO_SERIAL="${AUDIO_SERIAL:-00000162}"

WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8090}"

PACKAGED_READSB_REL="vendor/readsb/linux-aarch64/readsb"
PACKAGED_READSB="${APP_ROOT}/${PACKAGED_READSB_REL}"
INSTALLED_READSB="${APP_ROOT}/bin/readsb"

API_SCRIPT="${APP_ROOT}/src/rtl_pi_api.py"

LOG_DIR="/var/log/rtl-pi-adsb-tracker"
LOG_FILE="${LOG_DIR}/fresh_pi_install.log"

on_error() {
  local line="$1"
  echo
  echo "ERROR: bootstrap_fresh_pi_install.sh failed at line ${line}."
  echo "Log file:"
  echo "  ${LOG_FILE}"
  echo
  echo "Useful diagnostics:"
  echo "  journalctl -u rtl-pi-readsb.service -n 100 --no-pager"
  echo "  journalctl -u rtl-pi-adsb-tracker.service -n 100 --no-pager"
}

trap 'on_error $LINENO' ERR

prompt_enter() {
  local message="$1"
  echo
  read -r -p "${message} Press Enter to continue..." _
}

rtl_device_count() {
  local output
  local count

  output="$(rtl_eeprom 2>&1 || true)"
  count="$(printf '%s\n' "${output}" | sed -nE 's/^Found ([0-9]+) device.*/\1/p' | head -n 1)"

  if [[ -z "${count}" ]]; then
    echo "0"
  else
    echo "${count}"
  fi
}

show_rtl_devices() {
  echo
  echo "Current RTL-SDR devices:"
  rtl_eeprom 2>&1 || true
}

wait_for_rtl_count() {
  local expected="$1"
  local instruction="$2"
  local count

  while true; do
    show_rtl_devices
    count="$(rtl_device_count)"

    if [[ "${count}" == "${expected}" ]]; then
      echo
      echo "Detected expected RTL-SDR count: ${expected}"
      return 0
    fi

    echo
    echo "Expected ${expected} RTL-SDR device(s), but detected ${count}."
    echo "${instruction}"
    read -r -p "Press Enter to re-check..." _
  done
}

program_single_rtl_serial() {
  local label="$1"
  local serial="$2"

  echo
  echo "------------------------------------------------------------"
  echo "Programming ${label} RTL-SDR serial number"
  echo "Target serial: ${serial}"
  echo "------------------------------------------------------------"
  echo
  echo "Only the ${label} RTL-SDR should be plugged in for this step."

  wait_for_rtl_count "1" "Plug in ONLY the ${label} RTL-SDR and unplug all others."

  echo
  echo "Programming device 0 with serial ${serial}..."
  echo "rtl_eeprom requires confirmation; sending yes automatically."

  set +e
  printf 'y\n' | rtl_eeprom -d 0 -s "${serial}"
  local rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo
    echo "ERROR: rtl_eeprom failed while programming ${label} serial ${serial}."
    exit 1
  fi

  echo
  echo "${label} serial programming command completed."
  echo "The dongle must be unplugged and replugged for the new serial to appear."

  prompt_enter "Unplug the ${label} RTL-SDR now."
  wait_for_rtl_count "0" "Unplug all RTL-SDR dongles."
}

verify_required_rtl_serials() {
  local output

  echo
  echo "Verifying both required RTL-SDR serial numbers are visible..."

  wait_for_rtl_count "2" "Plug in BOTH RTL-SDR dongles."

  output="$(rtl_eeprom 2>&1 || true)"
  printf '%s\n' "${output}"

  if ! printf '%s\n' "${output}" | grep -q "${ADSB_SERIAL}"; then
    echo
    echo "ERROR: ADS-B RTL-SDR serial was not found: ${ADSB_SERIAL}"
    exit 1
  fi

  if ! printf '%s\n' "${output}" | grep -q "${AUDIO_SERIAL}"; then
    echo
    echo "ERROR: NOAA/Airband RTL-SDR serial was not found: ${AUDIO_SERIAL}"
    exit 1
  fi

  echo
  echo "OK: Found ADS-B RTL-SDR serial ${ADSB_SERIAL}"
  echo "OK: Found NOAA/Airband RTL-SDR serial ${AUDIO_SERIAL}"
}

prompt_for_rtl_sdr_serial_setup() {
  if [[ "${SKIP_SDR_SERIAL_SETUP:-0}" == "1" ]]; then
    echo
    echo "Skipping RTL-SDR serial provisioning because SKIP_SDR_SERIAL_SETUP=1."
    return 0
  fi

  if ! command -v rtl_eeprom >/dev/null 2>&1; then
    echo
    echo "ERROR: rtl_eeprom was not found after installing rtl-sdr."
    exit 1
  fi

  echo
  echo "============================================================"
  echo "RTL-SDR serial number provisioning"
  echo "============================================================"
  echo
  echo "This installer will program the two RTL-SDR dongles:"
  echo
  echo "  ADS-B 1090 MHz receiver:       ${ADSB_SERIAL}"
  echo "  NOAA/Airband audio receiver:   ${AUDIO_SERIAL}"
  echo
  echo "IMPORTANT:"
  echo "  - Plug in only ONE dongle when prompted."
  echo "  - The first dongle will become the ADS-B 1090 receiver."
  echo "  - The second dongle will become the NOAA/Airband receiver."
  echo "  - After each EEPROM write, unplug the dongle before continuing."
  echo
  echo "To skip this step on a system where serials are already programmed:"
  echo "  SKIP_SDR_SERIAL_SETUP=1 sudo bash /tmp/bootstrap_fresh_pi_install.sh"
  echo

  prompt_enter "Unplug ALL RTL-SDR dongles now."
  wait_for_rtl_count "0" "Unplug all RTL-SDR dongles."

  echo
  echo "Step 1 of 2: ADS-B receiver"
  echo "Plug in the RTL-SDR dongle that will be used for ADS-B / 1090 MHz."
  prompt_enter "Plug in the ADS-B RTL-SDR only."
  program_single_rtl_serial "ADS-B 1090 MHz" "${ADSB_SERIAL}"

  echo
  echo "Step 2 of 2: NOAA/Airband receiver"
  echo "Plug in the RTL-SDR dongle that will be used for NOAA and Airband audio."
  prompt_enter "Plug in the NOAA/Airband RTL-SDR only."
  program_single_rtl_serial "NOAA/Airband" "${AUDIO_SERIAL}"

  echo
  echo "Now plug in BOTH RTL-SDR dongles."
  prompt_enter "Plug in both RTL-SDR dongles."

  verify_required_rtl_serials

  echo
  echo "RTL-SDR serial provisioning complete."
  echo "============================================================"
}

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  echo "ERROR: Do not source this script."
  echo
  echo "Run it as:"
  echo "  sudo bash /tmp/bootstrap_fresh_pi_install.sh"
  return 2 2>/dev/null || exit 2
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: This installer must run on Linux/Raspberry Pi."
  echo "Detected: $(uname -s)"
  exit 2
fi

if [[ -n "${MSYSTEM:-}" ]]; then
  echo "ERROR: This installer is running under MSYS2/Windows."
  echo "Run it on the Raspberry Pi."
  exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: Run this installer with sudo."
  echo
  echo "Example:"
  echo "  sudo bash /tmp/bootstrap_fresh_pi_install.sh"
  exit 2
fi

mkdir -p "${LOG_DIR}"
touch "${LOG_FILE}"
chmod 0644 "${LOG_FILE}"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "============================================================"
echo "RTL-Pi ADS-B Tracker fresh Raspberry Pi bootstrap installer"
echo "Started: $(date)"
echo "============================================================"
echo
echo "APP_REPO=${APP_REPO}"
echo "APP_REF=${APP_REF}"
echo "APP_ROOT=${APP_ROOT}"
echo "APP_USER=${APP_USER}"
echo "ADSB_SERIAL=${ADSB_SERIAL}"
echo "AUDIO_SERIAL=${AUDIO_SERIAL}"
echo "WEB_HOST=${WEB_HOST}"
echo "WEB_PORT=${WEB_PORT}"
echo

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "ERROR: This installer expects Raspberry Pi OS / Debian ARM64."
  echo "Detected architecture: $(uname -m)"
  echo
  echo "Install the 64-bit Raspberry Pi OS / Debian Trixie Lite image."
  exit 2
fi

echo "Stopping existing services if present..."

systemctl stop rtl-pi-adsb-tracker.service 2>/dev/null || true
systemctl stop rtl-pi-readsb.service 2>/dev/null || true

echo
echo
echo "Installing runtime operating system dependencies..."
echo
echo "NOTE: This installer intentionally does not run apt update."
echo "The Pi pre-install setup must complete apt repository repair/update first."
echo "Expected pre-install command:"
echo "  sudo apt-get update"
echo
echo "This is a runtime install. Compiler/build packages are not installed."
echo
apt-get install -y \
  ca-certificates \
  curl \
  wget \
  git \
  jq \
  unzip \
  gzip \
  tar \
  nano \
  htop \
  lsof \
  net-tools \
  iproute2 \
  file \
  rsync \
  python3 \
  python3-full \
  python3-venv \
  python3-pip \
  libusb-1.0-0 \
  librtlsdr0 \
  rtl-sdr \
  zlib1g \
  libzstd1 \
  libncurses6 \
  libtinfo6 \
  sox \
  alsa-utils

echo
echo "Disabling Debian-packaged readsb if it exists..."

systemctl disable --now readsb 2>/dev/null || true

echo
echo "Creating application user and group..."

if ! getent group "${APP_GROUP}" >/dev/null 2>&1; then
  groupadd --system "${APP_GROUP}"
fi

mkdir -p "${APP_ROOT}"

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --home-dir "${APP_ROOT}" \
    --shell /bin/bash \
    --gid "${APP_GROUP}" \
    "${APP_USER}"
fi

usermod -aG plugdev,video,dialout "${APP_USER}" || true

echo
echo "Installing RTL-SDR DVB blacklist..."

cat > /etc/modprobe.d/blacklist-rtl-sdr-dvb.conf <<'BLACKLIST_EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
BLACKLIST_EOF

echo
echo "Installing RTL-SDR udev rules..."

cat > /etc/udev/rules.d/20-rtl-sdr.rules <<'UDEV_EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0666", SYMLINK+="rtl_sdr"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0666", SYMLINK+="rtl_sdr"
UDEV_EOF

udevadm control --reload-rules || true
udevadm trigger || true
modprobe -r dvb_usb_rtl28xxu rtl2832 rtl2830 2>/dev/null || true

echo
echo "Starting RTL-SDR serial provisioning before application install..."

prompt_for_rtl_sdr_serial_setup

echo
echo "Configuring git safe.directory for root-run installer..."

git config --global --add safe.directory "${APP_ROOT}" 2>/dev/null || true

echo
echo "Cloning or updating application repository..."

if [[ -d "${APP_ROOT}/.git" ]]; then
  echo "Existing git repo found at ${APP_ROOT}; updating."
  git -C "${APP_ROOT}" fetch --all --tags
else
  if [[ -n "$(find "${APP_ROOT}" -mindepth 1 -maxdepth 1 2>/dev/null)" ]]; then
    BACKUP_DIR="${APP_ROOT}.backup.$(date +%Y%m%d_%H%M%S)"
    echo "APP_ROOT exists and is not empty. Moving it to:"
    echo "  ${BACKUP_DIR}"
    mv "${APP_ROOT}" "${BACKUP_DIR}"
    mkdir -p "${APP_ROOT}"
  fi

  git clone "${APP_REPO}" "${APP_ROOT}"
  git -C "${APP_ROOT}" fetch --all --tags
fi

git -C "${APP_ROOT}" checkout "${APP_REF}"
git -C "${APP_ROOT}" pull --ff-only origin "${APP_REF}" 2>/dev/null || true

echo
echo "Creating runtime directories..."

mkdir -p \
  "${APP_ROOT}/bin" \
  "${APP_ROOT}/settings" \
  "${APP_ROOT}/runtime" \
  "${APP_ROOT}/runtime/settings" \
  "${APP_ROOT}/runtime/audio" \
  "${APP_ROOT}/runtime/noaa" \
  "${APP_ROOT}/runtime/airband" \
  "${APP_ROOT}/sessions" \
  "${APP_ROOT}/logs" \
  /var/lib/rtl-pi-adsb-tracker

chown -R "${APP_USER}:${APP_GROUP}" "${APP_ROOT}"
chown -R "${APP_USER}:${APP_GROUP}" "${LOG_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" /var/lib/rtl-pi-adsb-tracker

echo
echo "Installing packaged app-owned readsb..."

if [[ ! -f "${PACKAGED_READSB}" ]]; then
  echo "ERROR: Packaged readsb binary not found:"
  echo "  ${PACKAGED_READSB}"
  echo
  echo "Expected repo path:"
  echo "  ${PACKAGED_READSB_REL}"
  echo
  echo "Build and commit the ARM64 readsb binary from the dev machine first."
  exit 1
fi

install -m 0755 "${PACKAGED_READSB}" "${INSTALLED_READSB}"
chown "${APP_USER}:${APP_GROUP}" "${INSTALLED_READSB}"

echo "Installed readsb:"
ls -lh "${INSTALLED_READSB}"
file "${INSTALLED_READSB}" || true

echo
echo "Verifying app-owned readsb binary..."

if [[ ! -x "${INSTALLED_READSB}" ]]; then
  echo "ERROR: Installed readsb is not executable:"
  echo "  ${INSTALLED_READSB}"
  exit 1
fi

set +e
"${INSTALLED_READSB}" --usage >/tmp/rtl-pi-readsb-check.txt 2>&1
READSB_USAGE_RC=$?
set -e

if [[ "${READSB_USAGE_RC}" -ne 0 ]]; then
  echo "WARNING: readsb --usage returned exit code ${READSB_USAGE_RC}."
  echo "Continuing because some readsb builds return non-zero for usage output."
fi

if grep -Eiq 'rtlsdr|rtl-sdr|device-type' /tmp/rtl-pi-readsb-check.txt 2>/dev/null; then
  echo "OK: readsb usage output includes RTL-SDR/device-type references."
else
  echo "WARNING: readsb usage output did not include an RTL-SDR marker."
  echo "The packaged binary was still installed successfully; service startup will be the real validation."
fi

echo "Checking readsb runtime library dependencies..."

ldd "${INSTALLED_READSB}" || true

echo
echo "Skipping native helper builds on the Pi."
echo "This installer is runtime-only; native binaries must be packaged before release."


echo
echo "Creating Python virtual environment and installing Python dependencies..."

cd "${APP_ROOT}"

if [[ ! -d "${APP_ROOT}/.venv" ]]; then
  sudo -u "${APP_USER}" python3 -m venv "${APP_ROOT}/.venv"
fi

sudo -u "${APP_USER}" "${APP_ROOT}/.venv/bin/python" -m pip install --upgrade pip wheel setuptools

if [[ -f "${APP_ROOT}/requirements.txt" ]]; then
  sudo -u "${APP_USER}" "${APP_ROOT}/.venv/bin/pip" install -r "${APP_ROOT}/requirements.txt"
else
  sudo -u "${APP_USER}" "${APP_ROOT}/.venv/bin/pip" install flask flask-cors requests psutil
fi

echo
echo "Creating default local config if missing..."

if [[ ! -f "${APP_ROOT}/settings/local_config.json" ]]; then
  cat > "${APP_ROOT}/settings/local_config.json" <<CONFIG_EOF
{
  "adsb_receiver_serial": "${ADSB_SERIAL}",
  "audio_receiver_serial": "${AUDIO_SERIAL}",
  "readsb_aircraft_json": "/run/readsb/aircraft.json",
  "host": "${WEB_HOST}",
  "port": ${WEB_PORT},
  "noaa_rf_gain_db": 40.2,
  "airband_rf_gain_db": 40.2,
  "noaa_squelch_rms": 1300,
  "airband_search_mode": "fast_spectrum",
  "airband_activity_threshold_snr_db": 6.0,
  "airband_activity_threshold_rms": 650.0,
  "airband_playback_squelch_rms": 1500.0,
  "airband_sample_ms": 250,
  "airband_silence_resume_seconds": 7.0,
  "airband_radius_miles": 150.0
}
CONFIG_EOF
  chown "${APP_USER}:${APP_GROUP}" "${APP_ROOT}/settings/local_config.json"
fi

echo
echo "Importing aircraft hex/operator database if importer exists..."

if [[ -f "${APP_ROOT}/tools/import_aircraft_hex_db.py" ]]; then
  set +e
  sudo -u "${APP_USER}" "${APP_ROOT}/.venv/bin/python" "${APP_ROOT}/tools/import_aircraft_hex_db.py"
  IMPORT_RC=$?
  set -e

  if [[ "${IMPORT_RC}" -ne 0 ]]; then
    echo "WARNING: aircraft hex DB import failed. Continuing install."
  fi
else
  echo "No aircraft DB importer found; skipping."
fi

echo
echo "Writing rtl-pi-readsb.service..."

cat > /etc/systemd/system/rtl-pi-readsb.service <<SERVICE_EOF
[Unit]
Description=RTL Pi ADS-B readsb receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
SupplementaryGroups=plugdev video dialout
RuntimeDirectory=readsb
RuntimeDirectoryMode=0755
ExecStart=${INSTALLED_READSB} \\
  --device-type rtlsdr \\
  --device ${ADSB_SERIAL} \\
  --gain -10 \\
  --ppm 0 \\
  --net \\
  --net-heartbeat 60 \\
  --net-ri-port 30001 \\
  --net-ro-port 30002 \\
  --net-sbs-port 30003 \\
  --net-bi-port 30004 \\
  --net-bo-port 30005 \\
  --write-json /run/readsb \\
  --write-json-every 1 \\
  --quiet
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

echo
echo "Writing rtl-pi-adsb-tracker.service..."

if [[ ! -f "${API_SCRIPT}" ]]; then
  echo "ERROR: API script not found:"
  echo "  ${API_SCRIPT}"
  echo
  echo "Update API_SCRIPT in the installer if the app entry point changed."
  exit 1
fi

cat > /etc/systemd/system/rtl-pi-adsb-tracker.service <<SERVICE_EOF
[Unit]
Description=RTL ADS-B Tracker Web/API Service
After=network-online.target rtl-pi-readsb.service
Wants=network-online.target
Requires=rtl-pi-readsb.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
SupplementaryGroups=plugdev video dialout
WorkingDirectory=${APP_ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=RTL_PI_APP_ROOT=${APP_ROOT}
Environment=RTL_PI_READSB_JSON=/run/readsb/aircraft.json
Environment=RTL_PI_ADSB_SERIAL=${ADSB_SERIAL}
Environment=RTL_PI_AUDIO_SERIAL=${AUDIO_SERIAL}
Environment=RTL_PI_HOST=${WEB_HOST}
Environment=RTL_PI_PORT=${WEB_PORT}
ExecStart=${APP_ROOT}/.venv/bin/python ${API_SCRIPT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF

echo
echo "Reloading systemd and enabling services..."

systemctl daemon-reload
systemctl enable rtl-pi-readsb.service
systemctl enable rtl-pi-adsb-tracker.service

echo
echo "Starting services..."

systemctl restart rtl-pi-readsb.service
sleep 3

systemctl restart rtl-pi-adsb-tracker.service
sleep 3

echo
echo "Service status summary:"

systemctl is-active rtl-pi-readsb.service || true
systemctl is-active rtl-pi-adsb-tracker.service || true

echo
echo "Recent readsb service log:"
journalctl -u rtl-pi-readsb.service -n 40 --no-pager || true

echo
echo "Recent app service log:"
journalctl -u rtl-pi-adsb-tracker.service -n 40 --no-pager || true

echo
echo "ADS-B JSON check:"

if [[ -f /run/readsb/aircraft.json ]]; then
  ls -lh /run/readsb/aircraft.json
  cat /run/readsb/aircraft.json | jq '{messages, aircraft_count:(.aircraft|length)}' || true
else
  echo "WARNING: /run/readsb/aircraft.json not found yet."
fi

echo
echo "API check:"

set +e
curl -s "http://127.0.0.1:${WEB_PORT}/api/status" | jq . || true
set -e

echo
echo "Network addresses:"

hostname -I || true

echo
echo "============================================================"
echo "Fresh Pi install complete."
echo
echo "Open the UI from another machine:"
echo "  http://<pi-ip-address>:${WEB_PORT}"
echo
echo "Useful commands:"
echo "  journalctl -u rtl-pi-readsb.service -f"
echo "  journalctl -u rtl-pi-adsb-tracker.service -f"
echo "  systemctl status rtl-pi-readsb.service --no-pager"
echo "  systemctl status rtl-pi-adsb-tracker.service --no-pager"
echo
echo "Install log:"
echo "  ${LOG_FILE}"
echo
echo "Recommended: reboot once after first install:"
echo "  sudo reboot"
echo "============================================================"

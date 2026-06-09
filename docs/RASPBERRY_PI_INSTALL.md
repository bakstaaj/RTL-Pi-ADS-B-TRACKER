# Raspberry Pi Fresh Install Guide

This guide deploys **RTL-Pi-ADS-B-Tracker** onto a fresh Raspberry Pi running **Raspberry Pi OS / Debian Trixie Lite 64-bit**.

The intended deployment flow is:

1. Start with a fresh Trixie Lite 64-bit Pi.
2. Download one bootstrap installer from GitHub.
3. Run it locally on the Pi with `sudo bash`.
4. Follow the prompts to program the two RTL-SDR serial numbers.
5. Reboot once.
6. Open the web UI at `http://<pi-ip-address>:8090`.

The Pi does **not** build `readsb` from source. The application repo includes an app-owned ARM64 `readsb` binary that was cross-compiled on the development machine with RTL-SDR support enabled.

---

## Hardware assumptions

Recommended hardware:

- Raspberry Pi 5 or Raspberry Pi 4.
- Raspberry Pi OS / Debian Trixie Lite 64-bit.
- Two RTL-SDR dongles, such as NooElec NESDR Nano 3.
- ADS-B antenna connected to the ADS-B dongle.
- NOAA/Airband antenna connected to the audio dongle.
- Network access from the Pi to GitHub and Debian apt repositories.

Default RTL-SDR role assignment:

| Role | Serial number |
|---|---:|
| ADS-B / 1090 MHz | `00001090` |
| NOAA / Airband audio | `00000162` |

---

## Operating system assumptions

Start with Raspberry Pi OS / Debian Trixie Lite 64-bit already installed.

The install assumes:

- SSH is enabled.
- The Pi has internet access.
- The login user has sudo rights.
- The Pi architecture is ARM64 / `aarch64`.

Check the architecture:

```bash
uname -m
```

Expected result:

```text
aarch64
```

---

## App-owned readsb design

This application does **not** rely on the Debian `/usr/bin/readsb` package.

The repo includes a packaged ARM64 `readsb` binary here:

```text
vendor/readsb/linux-aarch64/readsb
```

The installer copies it to:

```text
/opt/rtl-pi-adsb-tracker/bin/readsb
```

The ADS-B systemd service runs that app-owned binary.

This avoids relying on the Debian Trixie `readsb` package, which may not have RTL-SDR support enabled.

The service must use:

```text
/opt/rtl-pi-adsb-tracker/bin/readsb
```

not:

```text
/usr/bin/readsb
```

---

## Maintainer step: build the packaged ARM64 readsb binary

Before publishing a release or fresh-install branch, build and commit the ARM64 `readsb` binary from the Windows/MSYS2 development machine.

From MSYS2 UCRT64:

```bash
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker

bash tools/build_readsb_arm64_cross.sh
```

Verify the packaged binary:

```bash
ls -lh vendor/readsb/linux-aarch64/readsb
file vendor/readsb/linux-aarch64/readsb
git ls-files -s vendor/readsb/linux-aarch64/readsb
```

Expected file type:

```text
ELF 64-bit LSB pie executable, ARM aarch64
```

Expected git mode:

```text
100755
```

If needed, fix the executable bit:

```bash
chmod +x vendor/readsb/linux-aarch64/readsb
git add --chmod=+x vendor/readsb/linux-aarch64/readsb
```

Commit and push:

```bash
git add tools/build_readsb_arm64_cross.sh
git add vendor/readsb/linux-aarch64/readsb
git add vendor/readsb/linux-aarch64/README.md

git commit -m "Package ARM64 readsb with RTL-SDR support"
git push
```

---

## Required pre-install Pi setup

Before running the application bootstrap installer, the Pi's apt repositories must already be working.

Run this on the Pi first:

```bash
sudo apt-get update
sudo apt-get -y full-upgrade
sudo reboot
```

After reboot, confirm apt is healthy:

```bash
sudo apt-get update
```

The application bootstrap installer intentionally does **not** run `apt update`. If apt repositories, DNS, mirrors, proxy, captive portal, or package signatures are broken, fix those before running the application installer.

Once `sudo apt-get update` completes cleanly, continue with the fresh Pi install below.

---

## Fresh Pi one-command install

On the fresh Raspberry Pi, run:

```bash
curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh

sudo bash /tmp/bootstrap_fresh_pi_install.sh
```

The installer handles dependency package installation, RTL-SDR serial setup, repo clone, app install, systemd services, and startup validation. It does not run `apt update`; that is a required pre-install Pi setup task.

After the installer completes, reboot once:

```bash
sudo reboot
```

---

## RTL-SDR serial provisioning flow

During installation, the bootstrap script programs the two RTL-SDR dongles.

The flow is interactive:

1. Unplug all RTL-SDR dongles.
2. Plug in the ADS-B dongle only.
3. The installer programs it to serial `00001090`.
4. Unplug the ADS-B dongle.
5. Plug in the NOAA/Airband dongle only.
6. The installer programs it to serial `00000162`.
7. Unplug/replug as prompted.
8. Plug in both RTL-SDR dongles.
9. The installer verifies both serial numbers before continuing.

The first dongle you plug in becomes the ADS-B receiver. The second dongle becomes the NOAA/Airband receiver.

---

## Skip RTL-SDR serial programming

If the dongles are already programmed, skip the interactive EEPROM step:

```bash
curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh

SKIP_SDR_SERIAL_SETUP=1 sudo bash /tmp/bootstrap_fresh_pi_install.sh
```

---

## Install a specific release tag

To install a tagged release:

```bash
curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh

APP_REF=v3.3.0 sudo bash /tmp/bootstrap_fresh_pi_install.sh
```

For already-programmed dongles:

```bash
APP_REF=v3.3.0 SKIP_SDR_SERIAL_SETUP=1 sudo bash /tmp/bootstrap_fresh_pi_install.sh
```

---

## Default install locations

| Item | Path |
|---|---|
| Application root | `/opt/rtl-pi-adsb-tracker` |
| App-owned readsb | `/opt/rtl-pi-adsb-tracker/bin/readsb` |
| Aircraft JSON | `/run/readsb/aircraft.json` |
| App config | `/opt/rtl-pi-adsb-tracker/settings/local_config.json` |
| Runtime data | `/opt/rtl-pi-adsb-tracker/runtime` |
| Installer log | `/var/log/rtl-pi-adsb-tracker/fresh_pi_install.log` |
| Web UI | `http://<pi-ip-address>:8090` |

---

## Apt packages installed by the bootstrap script

After the pre-install apt update has completed successfully, the bootstrap installer installs the required operating system dependencies in one apt-get install step, including:

```text
ca-certificates
curl
wget
git
jq
unzip
gzip
tar
nano
htop
lsof
net-tools
iproute2
file
rsync
python3
python3-full
python3-venv
python3-pip
python3-dev
build-essential
make
cmake
pkg-config
gcc
g++
libusb-1.0-0
libusb-1.0-0-dev
librtlsdr0
librtlsdr-dev
rtl-sdr
zlib1g
libzstd1
libncurses6
libtinfo6
sox
alsa-utils
```

The installer also disables any Debian-packaged `readsb` service if present.

---

## Services installed

The installer creates two systemd services.

### ADS-B decoder service

```text
rtl-pi-readsb.service
```

This service runs:

```text
/opt/rtl-pi-adsb-tracker/bin/readsb
```

It writes aircraft JSON to:

```text
/run/readsb/aircraft.json
```

### Web/API service

```text
rtl-pi-adsb-tracker.service
```

This service runs the Python backend and serves the web UI on port `8090`.

---

## Post-install validation

After reboot, reconnect to the Pi and run:

```bash
systemctl status rtl-pi-readsb.service --no-pager
systemctl status rtl-pi-adsb-tracker.service --no-pager
```

Check the API:

```bash
curl -s http://127.0.0.1:8090/api/status | jq .
```

Check ADS-B JSON:

```bash
cat /run/readsb/aircraft.json | jq '{messages, aircraft_count:(.aircraft|length)}'
```

Check RTL-SDR serial numbers:

```bash
rtl_eeprom
```

Expected serials:

```text
00001090
00000162
```

Check which processes are using RTL-SDR devices:

```bash
sudo lsof /dev/bus/usb/*/* 2>/dev/null | grep -E 'rtl|readsb|python' || true
```

---

## Open the web UI

Find the Pi IP address:

```bash
hostname -I
```

Open from another computer:

```text
http://<pi-ip-address>:8090
```

Example:

```text
http://192.168.1.50:8090
```

---

## Updating an existing install

To update an existing install from `main`:

```bash
cd /opt/rtl-pi-adsb-tracker

sudo systemctl stop rtl-pi-adsb-tracker.service
sudo systemctl stop rtl-pi-readsb.service

sudo git fetch --all --tags
sudo git checkout main
sudo git pull --ff-only

SKIP_SDR_SERIAL_SETUP=1 sudo bash tools/bootstrap_fresh_pi_install.sh

sudo reboot
```

To update to a specific tag:

```bash
cd /opt/rtl-pi-adsb-tracker

APP_REF=v3.3.0 SKIP_SDR_SERIAL_SETUP=1 sudo bash tools/bootstrap_fresh_pi_install.sh

sudo reboot
```

---

## Useful service commands

Restart both services:

```bash
sudo systemctl restart rtl-pi-readsb.service
sudo systemctl restart rtl-pi-adsb-tracker.service
```

View ADS-B decoder logs:

```bash
journalctl -u rtl-pi-readsb.service -f
```

View app logs:

```bash
journalctl -u rtl-pi-adsb-tracker.service -f
```

Check service status:

```bash
systemctl status rtl-pi-readsb.service --no-pager
systemctl status rtl-pi-adsb-tracker.service --no-pager
```

---

## Troubleshooting

### Bootstrap script returns 404

Confirm the installer exists and has been pushed:

```bash
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker

git ls-files tools/bootstrap_fresh_pi_install.sh
git status
git push
```

The raw GitHub URL should exist:

```text
https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh
```

### Packaged readsb binary not found

The installer expects:

```text
vendor/readsb/linux-aarch64/readsb
```

Build and commit it from the dev machine:

```bash
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker

bash tools/build_readsb_arm64_cross.sh

chmod +x vendor/readsb/linux-aarch64/readsb
git add --chmod=+x vendor/readsb/linux-aarch64/readsb
git add vendor/readsb/linux-aarch64/README.md

git commit -m "Package ARM64 readsb with RTL-SDR support"
git push
```

### readsb does not support RTL-SDR

Check the service uses the app-owned binary:

```bash
grep ExecStart /etc/systemd/system/rtl-pi-readsb.service
```

Expected path:

```text
/opt/rtl-pi-adsb-tracker/bin/readsb
```

Validate the binary:

```bash
/opt/rtl-pi-adsb-tracker/bin/readsb --device-type rtlsdr --help | head -40
```

### readsb cannot open the ADS-B SDR

Check serials:

```bash
rtl_eeprom
```

Expected serials:

```text
00001090
00000162
```

Check logs:

```bash
journalctl -u rtl-pi-readsb.service -n 100 --no-pager
```

Check for competing SDR processes:

```bash
sudo lsof /dev/bus/usb/*/* 2>/dev/null | grep -E 'rtl|readsb|python' || true
```

### App service fails to start

Check logs:

```bash
journalctl -u rtl-pi-adsb-tracker.service -n 100 --no-pager
```

Check Python environment:

```bash
/opt/rtl-pi-adsb-tracker/.venv/bin/python --version
/opt/rtl-pi-adsb-tracker/.venv/bin/pip list
```

Check the backend entry point:

```bash
ls -lh /opt/rtl-pi-adsb-tracker/src/rtl_pi_api.py
```

### Web UI does not load from another computer

Check local API:

```bash
curl -s http://127.0.0.1:8090/api/status | jq .
```

Check listener:

```bash
sudo lsof -iTCP:8090 -sTCP:LISTEN
```

Check Pi IP address:

```bash
hostname -I
```

---

## One-command summary

Brand-new Pi:

```bash
sudo apt-get update
sudo apt-get -y full-upgrade
sudo reboot
```

After reboot:

```bash
curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh

sudo bash /tmp/bootstrap_fresh_pi_install.sh

sudo reboot
```

Pi with already-programmed dongles:

```bash
sudo apt-get update
sudo apt-get -y full-upgrade
sudo reboot
```

After reboot:

```bash
curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh

SKIP_SDR_SERIAL_SETUP=1 sudo bash /tmp/bootstrap_fresh_pi_install.sh

sudo reboot
```

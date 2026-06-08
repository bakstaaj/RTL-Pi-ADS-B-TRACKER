# RTL Pi ADS-B Tracker

A Raspberry Pi based ADS-B aircraft tracker with a browser map UI, local aircraft detail enrichment, NOAA/WX audio listening, Airband scanning, AirLabs route lookup, and best-guess aircraft photo fallback support.

This project is designed for a Raspberry Pi with two RTL-SDR receivers:

- one RTL-SDR dedicated to ADS-B / 1090 MHz aircraft tracking
- one RTL-SDR dedicated to NOAA weather audio and Airband scanning/listening

## Current release

**v3.1.0**

Major v3.1.0 additions:

- AirLabs route lookup backend and UI integration
- local AirLabs API key storage on the Pi
- successful AirLabs route lookup caching
- aircraft details route source display
- best-guess aircraft photo fallback when ADSBDB has no image
- representative make/model/type image fallback
- local aircraft photo fallback caching
- cleaner aircraft details photo display with quiet source credit

## Hardware requirements

Recommended hardware:

- Raspberry Pi 5
- Raspberry Pi OS / Debian based 64-bit install
- two RTL-SDR compatible USB receivers
- ADS-B antenna tuned for 1090 MHz
- VHF antenna for NOAA Weather Radio and Civil Airband
- reliable 5V USB-C power supply for the Raspberry Pi
- network access from your browser to the Pi

Known tested receiver layout:

```text
ADS-B receiver serial:        00001090
NOAA/Airband receiver serial: 00000162
```

Using fixed serial numbers is strongly recommended so the ADS-B and audio roles do not swap after reboot.

## Software components

The application uses:

- `readsb` for ADS-B decoding
- a Python backend API service
- a browser based map and control UI
- native helper binaries for RTL-SDR audio and Airband scanning
- local JSON files for runtime settings and caches

Primary local deployment path:

```text
/opt/rtl-pi-adsb-tracker
```

Important Pi-side paths:

```text
/opt/rtl-pi-adsb-tracker/app/rtl_pi_api.py
/opt/rtl-pi-adsb-tracker/web/index.html
/opt/rtl-pi-adsb-tracker/settings
/opt/rtl-pi-adsb-tracker/test_output
```

The API service normally listens on:

```text
http://<PI_HOST>:8080
```

## Development environment

The current development workflow uses MSYS2 UCRT64 on Windows with the local repository under:

```text
~/sdrdev/RTL-Pi-ADS-B-Tracker
```

Typical setup:

```bash
cd ~/sdrdev
git clone git@github-bakstaaj:bakstaaj/RTL-Pi-ADS-B-TRACKER.git RTL-Pi-ADS-B-Tracker
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker
```

The repository uses a local `.pi.env` file for deployment variables.

Example `.pi.env`:

```bash
export PI_HOST=10.12.194.1
export PI_USER=pi
export PI_DEPLOY_DIR=/opt/rtl-pi-adsb-tracker
```

Load it before deployment or API tests:

```bash
source .pi.env
```

Check the API:

```bash
curl -sS "http://${PI_HOST}:8080/api/status" | python3 -m json.tool
```

## Build and deployment

Typical deployment:

```bash
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker
source .pi.env

python3 -m py_compile src/rtl_pi_api.py
./tools/deploy_api_backend.sh
```

After deployment, hard-refresh the browser:

```text
Ctrl+F5
```

Check service status:

```bash
ssh "${PI_USER}@${PI_HOST}"   "systemctl status rtl-pi-api.service --no-pager"
```

Check API status:

```bash
curl -sS "http://${PI_HOST}:8080/api/status" | python3 -m json.tool
```

## Web UI overview

The browser UI provides:

- live aircraft map
- aircraft detail popup
- aircraft trails
- altitude-colored trail rendering
- restore history support
- aircraft photo and metadata enrichment
- AirLabs route information when configured
- NOAA/WX live listening
- Airband fast-spectrum scanner
- Airband skip/block/squelch controls
- receiver location and Airband radius settings
- diagnostics/configuration panels

Open the UI at:

```text
http://<PI_HOST>:8080/
```

## ADS-B aircraft tracking

The ADS-B receiver is used by `readsb` to decode aircraft messages. The backend reads the local readsb JSON and serves aircraft data to the browser map.

Typical status fields:

```text
messages
aircraft_count
aircraft_with_position
readsb_json_available
```

Check status:

```bash
curl -sS "http://${PI_HOST}:8080/api/status" | python3 -m json.tool
```

## NOAA/WX audio

The UI includes NOAA Weather Radio controls for live browser audio.

Typical behavior:

- start WX from the UI
- live audio plays in the browser
- stop WX from the UI
- starting the Airband scanner stops WX automatically
- starting WX while Airband is running stops Airband first, then starts WX

Known local test channel used during development:

```text
162.500 MHz
```

## Airband scanner

The Airband scanner is intended to operate like a normal scanner:

1. Fast-spectrum search across Airband candidates.
2. Lock onto likely active frequencies.
3. Play live AM audio through the browser.
4. Resume scanning after activity ends.
5. Allow user skip/block control.

Under-map controls include:

```text
WX
Start Scanner
Stop Scanner
Skip Open Frequency
Block Frequency
Squelch - / value / Squelch +
```

### Skip

Skip temporarily avoids the currently locked/open frequency so the scanner can continue to the next candidate.

### Block

Block adds the current frequency to the local blocked-frequency list so future locks avoid it.

### Squelch

The squelch control is displayed in dBFS. The current stable behavior uses squelch as part of scanner control/display. Earlier experimental browser-audio muting was intentionally rolled back because it could break the live audio stream.

## AirLabs route lookup

v3.1.0 adds AirLabs route lookup support.

The tracker can use the AirLabs Flight API to enrich aircraft details with departure and destination route information when an airline-style callsign is available.

The API key is stored locally on the Raspberry Pi and is not committed to the repository.

Default Pi-side storage:

```text
/opt/rtl-pi-adsb-tracker/settings/airlabs_api.json
/opt/rtl-pi-adsb-tracker/settings/airlabs_route_cache.json
```

Successful AirLabs route lookups are cached so repeated popup openings do not repeatedly query the external API.

Default route cache TTL:

```text
7200 seconds / 2 hours
```

### Configure AirLabs from the web UI

1. Open the tracker web UI.
2. Open the configuration or diagnostics area.
3. Locate **AirLabs route lookup**.
4. Paste the AirLabs API key.
5. Click **Save Key**.
6. Use **Test Lookup** with an airline-style flight number such as `UAL1234`.

When configured correctly, aircraft details show route source information such as:

```text
Route Source: AirLabs Flight Information API
Route Source: AirLabs Flight Information API (cached)
Route Source: AirLabs — no route match
```

A no-match result is normal when AirLabs does not currently have route data for a callsign.

### Configure AirLabs from the shell

Load the Pi deployment environment:

```bash
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker
source .pi.env
```

Save the API key:

```bash
printf 'Paste AirLabs API key: '
read -r -s AIRLABS_KEY
printf '\n'

curl -sS -X POST "http://${PI_HOST}:8080/api/diagnostics/airlabs/settings"   -H "Content-Type: application/json"   --data-binary "{\"api_key\":\"${AIRLABS_KEY}\"}"   | python3 -m json.tool

unset AIRLABS_KEY
```

Check AirLabs status:

```bash
curl -sS "http://${PI_HOST}:8080/api/diagnostics/airlabs/status"   | python3 -m json.tool
```

Clear cached route results:

```bash
curl -sS -X POST "http://${PI_HOST}:8080/api/diagnostics/airlabs/cache/clear"   | python3 -m json.tool
```

Test a route lookup:

```bash
curl -sS "http://${PI_HOST}:8080/api/diagnostics/airlabs/route?flight_icao=UAL123"   | python3 -m json.tool
```

## Aircraft photo fallback

Aircraft details first attempt to show the normal ADSBDB aircraft photograph. If no exact photograph is available, v3.1.0 adds a best-guess fallback.

Lookup order:

1. Exact registration or tail number.
2. ICAO hex where useful.
3. Callsign or flight number when useful.
4. Representative make/model/type image if no exact aircraft image is found.

Representative type images are used only as a fallback and are credited as representative images rather than exact airframe photographs.

Example detail note:

```text
Photo: best guess from JetPhotos.
Photo: representative aircraft type image from JetPhotos.
```

The photo area itself is intentionally kept clean and shows only the image.

Successful photo fallback results are cached locally on the Pi.

Default Pi-side storage:

```text
/opt/rtl-pi-adsb-tracker/settings/aircraft_photo_fallback_cache.json
```

Default photo fallback cache TTL:

```text
86400 seconds / 24 hours
```

### Test aircraft photo fallback from the shell

```bash
source .pi.env

curl -sS   "http://${PI_HOST}:8080/api/aircraft/photo/fallback?reg=N8523W&hex=ABB19C&type=737NG%20800%2FW&model=Boeing"   | python3 -m json.tool
```

The response includes either:

```text
match_level: exact
```

or:

```text
match_level: type
representative: true
```

## New v3.1.0 API endpoints

AirLabs:

```text
GET  /api/diagnostics/airlabs/status
GET  /api/diagnostics/airlabs/route?flight_icao=<CALLSIGN>
POST /api/diagnostics/airlabs/settings
POST /api/diagnostics/airlabs/cache/clear
```

Aircraft photo fallback:

```text
GET /api/aircraft/photo/fallback?reg=<TAIL>&hex=<ICAO_HEX>&type=<TYPE>&model=<MODEL>
```

## Git workflow

Typical feature workflow:

```bash
cd ~/sdrdev/RTL-Pi-ADS-B-Tracker

git status --short
git checkout main
git pull --ff-only origin main

git checkout -b feature/my-feature
```

After changes:

```bash
python3 -m py_compile src/rtl_pi_api.py
./tools/deploy_api_backend.sh
```

Commit only intended files:

```bash
git status --short
git add src/rtl_pi_api.py web/index.html README.md
git commit -m "Describe the feature"
git push
```

Avoid committing:

```text
tools/patch_*.py
*.before_*
src/__pycache__/
test_output/
runtime/generated cache files
```

## Release process

For v3.1.0:

```bash
git status --short
git pull --ff-only origin main

git tag -a v3.1.0 -m "Release v3.1.0 - AirLabs routes and aircraft photo fallback"

git push origin main
git push origin v3.1.0
```

Verify:

```bash
git tag --list 'v3.1.0'
git ls-remote --tags origin v3.1.0
```

## Troubleshooting

### API status

```bash
source .pi.env
curl -sS "http://${PI_HOST}:8080/api/status" | python3 -m json.tool
```

### AirLabs reports unknown API key

Clear and re-save the key:

```bash
curl -sS -X POST "http://${PI_HOST}:8080/api/diagnostics/airlabs/settings"   -H "Content-Type: application/json"   -d '{"action":"clear"}'   | python3 -m json.tool
```

Then save the key again from the UI or shell.

### Browser UI does not show new behavior

Deploy the latest API/web files:

```bash
./tools/deploy_api_backend.sh
```

Then hard-refresh:

```text
Ctrl+F5
```

### Airband or WX audio does not start

Check current audio mode and scanner state:

```bash
curl -sS "http://${PI_HOST}:8080/api/status" | python3 -m json.tool |   grep -E '"audio_mode"|"audio_busy"|"airband_scan_state"|"airband_live_audio_running"'
```

Stop Airband scanner if needed:

```bash
curl -sS -X POST "http://${PI_HOST}:8080/api/airband/scan/activity/stop"   | python3 -m json.tool
```

### Service status

```bash
ssh "${PI_USER}@${PI_HOST}"   "systemctl status rtl-pi-api.service --no-pager"
```

## Notes

- API keys and caches are stored locally on the Pi under `/opt/rtl-pi-adsb-tracker/settings`.
- Runtime settings and caches should not be committed to Git.
- Photo fallback images are best-effort. Exact registration images are preferred; representative type images are labeled as representative.
- AirLabs route data depends on AirLabs API availability, plan limits, and whether a current flight match exists.

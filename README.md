# RTL Pi ADS-B Tracker

<!-- RPI_INSTALL_DOC_START -->
## Fresh Raspberry Pi installation

For a fresh Raspberry Pi OS / Debian Trixie Lite 64-bit install, use the bootstrap installer.

```bash
curl -fsSL https://raw.githubusercontent.com/bakstaaj/RTL-Pi-ADS-B-Tracker/main/tools/bootstrap_fresh_pi_install.sh -o /tmp/bootstrap_fresh_pi_install.sh

sudo bash /tmp/bootstrap_fresh_pi_install.sh

sudo reboot
```

The installer performs apt dependency installation, RTL-SDR serial provisioning, app clone/update, packaged ARM64 `readsb` installation, Python environment setup, systemd service creation, and validation.

Full instructions:

[docs/RASPBERRY_PI_INSTALL.md](docs/RASPBERRY_PI_INSTALL.md)

For already-programmed RTL-SDR dongles:

```bash
SKIP_SDR_SERIAL_SETUP=1 sudo bash /tmp/bootstrap_fresh_pi_install.sh
```
<!-- RPI_INSTALL_DOC_END -->


<!-- V3_3_0_FUNCTIONAL_UPDATE_START -->
## v3.3.0 Functional Update

Version 3.3.0 adds aircraft-display, enrichment, route-classification, and map-interaction improvements that bring the Raspberry Pi app closer to the newer Windows-style user experience.

### Web UI Structure

The web UI is now maintained as split assets:

- `web/index.html` - page structure
- `web/app.css` - styling
- `web/app.js` - browser-side application logic

The backend serves `app.css` and `app.js` directly, and `tools/deploy_api_backend.sh` deploys all three web assets.

### Aircraft Map and Trail Behavior

The live map removes active aircraft markers and active trail layers when aircraft leave the live receiver range. Browser-side trail history is retained separately, so Restore History can still recover retained tracks without leaving stale active trails on the live map.

### Aircraft Details from the Map

Aircraft icons on the map can now be double-clicked to open the same full aircraft details dialog used by the aircraft list. This opens the populated details dialog for the aircraft record associated with the marker rather than a small Leaflet popup.

### Aircraft Metadata and Photo Fallback

Aircraft enrichment now uses layered fallback behavior:

1. ADSBDB lookup when available.
2. Local tar1090-db aircraft cache by ICAO hex when ADSBDB is incomplete.
3. Representative aircraft photo fallback by manufacturer/model/operator/type.
4. Filtering to avoid logos, placeholders, icons, SVGs, and other non-aircraft images.
5. Local fallback cache for photo decisions.

### Local tar1090 Aircraft Cache

The Pi backend includes a local aircraft lookup endpoint:

```text
/api/aircraft/local?hex=<ICAO_HEX>
```

It reads the local aircraft cache from:

```text
/opt/rtl-pi-adsb-tracker/settings/aircraft_hex_db.json
```

The UI uses this fallback when ADSBDB does not provide aircraft data.

### AirLabs Route Enrichment

AirLabs route enrichment remains the scheduled-route source. The UI supports normalized airline callsign retries for route lookups where AirLabs omits leading zeros or uses a simplified ICAO flight number.

Example:

```text
KAL032 -> KAL32
```

### Private, Charter, and Tail-Number Callsign Handling

The route-source display now gives clearer messages for callsigns that are not expected to have scheduled airline routes.

Examples:

```text
KOW523  -> Private/charter callsign - Baker Aviation / Rodeo; route not available from AirLabs
LYM3583 -> Private/charter callsign - Key Lime Air; route not available from AirLabs
N653JC  -> Private/general aviation tail-number callsign - N653JC; route not available from AirLabs
```

This prevents private, charter, regional, or registration-style callsigns from looking like broken scheduled airline route lookups.

### Functional Porting Document

A detailed functional change summary for porting these changes into the Windows version is included at:

```text
docs/RTL-Pi-ADS-B-Tracker_Functional_Changes_Since_v3.0.md
```
<!-- V3_3_0_FUNCTIONAL_UPDATE_END -->

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

<!-- RTL_PI_ADSB_V3_2_0_FEATURE_DOCS_START -->

## v3.2.0 Enhancements: Trail Cleanup and Improved Aircraft Photo Fallback

Version **v3.2.0** improves map trail behavior and aircraft photo fallback reliability.

### Active trail cleanup when aircraft leave range

The map now clears the currently visible active trail for an aircraft when that aircraft drops out of the live receiver feed or leaves range.

Behavior:

```text
Live aircraft visible:
- aircraft marker is shown
- active trail is shown

Aircraft leaves live range:
- aircraft marker is removed
- active visible trail is removed from the map
- retained trail history remains available for Restore History
```

This keeps the active map cleaner while preserving historical trail data for later review.

### Trail history remains available

Clearing active trails from the live map does **not** erase browser/Pi trail history.

Use **Restore History** to redraw retained trail history after aircraft have left the live range.

### Trail hover popup

Trail segments can show hover information including:

```text
Call Sign / ICAO
Last seen date/time
From → To route when available
```

Route information is shown when AirLabs route enrichment has a matching route.

### Native aircraft photo fallback

v3.2.0 moves aircraft photo fallback into the native aircraft photo render path.

The UI no longer uses broad MutationObserver-based photo fallback logic. This avoids popup reloads, duplicate photo boxes, and duplicate photo credit text.

Photo lookup order:

1. ADSBDB aircraft photo thumbnail.
2. ADSBDB full aircraft photo.
3. Local backend best-guess aircraft photo fallback.
4. Representative aircraft type photo fallback.

The existing aircraft photo frame is reused, so fallback photos appear in the normal photo location.

### Photo lookup spinner

The photo frame now shows a spinner while all photo lookup attempts are still running.

Expected behavior:

```text
Looking up aircraft photo… [spinner]

Then either:
[picture]

or, only after all attempts fail:
No photograph available for this aircraft.
```

This avoids briefly showing a no-photo message before the fallback image appears.

### Representative aircraft type fallback

When no exact aircraft image is available, the backend can show a representative aircraft type image.

Representative fallback is clearly credited, for example:

```text
Photo: representative aircraft type image from Wikimedia.
Photo: representative aircraft type image from Wikimedia Commons.
```

Representative images should be treated as best-effort type examples, not exact tail-number photos.

### Model synonym handling

v3.2.0 improves model/type query generation for common aircraft strings returned by aircraft databases.

Examples handled:

```text
Airbus A320 214       -> Airbus A320 / A320-214 / A320ceo / A320 family
Airbus A321-271NX     -> Airbus A321neo / A321 family
Boeing 737-8          -> Boeing 737 MAX 8
Boeing 737-9          -> Boeing 737 MAX 9
Boeing 737NG 900ER/W  -> Boeing 737-900ER / 737NG 900ER / winglets
Boeing 757 26D/W      -> Boeing 757-200 / 757-200 winglets
Embraer EMB-175 LL    -> Embraer E175 / ERJ-175
Embraer ERJ 170-200 LR -> Embraer E175 / 170-200
Bombardier CRJ 900 LR NG -> Bombardier CRJ 900
```

The fallback also uses operator context when available, such as:

```text
Delta Connection Embraer E175
Alaska Airlines Boeing 737-900ER
Delta Air Lines Boeing 757-200
American Eagle CRJ 900
```

### Image sources and filtering

Fallback image sources include:

```text
JetPhotos
Planespotters
Wikimedia
Wikimedia Commons
```

The backend filters out common non-aircraft images such as:

```text
logos
social cards
icons
SVG logo thumbnails
placeholder images
default/no-photo images
```

### Photo fallback cache

Successful fallback photo results are cached locally on the Pi.

Default cache path:

```text
/opt/rtl-pi-adsb-tracker/settings/aircraft_photo_fallback_cache.json
```

Clear the cache during testing:

```bash
source .pi.env

ssh "${PI_USER}@${PI_HOST}" \
  "rm -f ${PI_DEPLOY_DIR}/settings/aircraft_photo_fallback_cache.json"
```

### Test photo fallback from the shell

Examples:

```bash
curl -sS \
  "http://${PI_HOST}:8080/api/aircraft/photo/fallback?manufacturer=Boeing&type=757%2026D%2FW&model=757%2026D%2FW&operator=Delta%20Air%20Lines" \
  | python3 -m json.tool

curl -sS \
  "http://${PI_HOST}:8080/api/aircraft/photo/fallback?manufacturer=Boeing&type=737NG%20900ER%2FW&model=737NG%20900ER%2FW&operator=Alaska%20Airlines" \
  | python3 -m json.tool

curl -sS \
  "http://${PI_HOST}:8080/api/aircraft/photo/fallback?manufacturer=Bombardier&type=CRJ%20900%20LR%20NG&model=CRJ%20900%20LR%20NG&operator=American%20Eagle" \
  | python3 -m json.tool
```

A successful representative fallback includes:

```text
found: true
match_level: type
representative: true
source: Wikimedia / Wikimedia Commons / JetPhotos / Planespotters
```

### v3.2.0 operational notes

- ADSBDB photos remain the first choice.
- Representative fallback photos are only used when exact photos are unavailable.
- Runtime caches should not be committed to Git.
- Patch scripts and backup files should not be committed.
- Hard-refresh the browser after deploying web UI changes.

<!-- RTL_PI_ADSB_V3_2_0_FEATURE_DOCS_END -->
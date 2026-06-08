# RTL-Pi ADS-B Tracker Functional Change Summary Since v3.0

Target use: guide porting the Raspberry Pi application changes into the Windows version of the app.

This document intentionally focuses on functional requirements and implemented changes. It omits troubleshooting history and failed approaches.

---

## Release Scope

This summary covers functional work completed after the v3.0 baseline through the v3.3.0 checkpoint.

The major themes were:

- Align the Raspberry Pi UI with the newer Windows-style web UI behavior.
- Improve aircraft identity, operator, route, and photo enrichment.
- Improve aircraft map usability and trail handling.
- Improve AirLabs route lookup behavior and classify callsigns that are not scheduled airline routes.
- Split monolithic web assets into maintainable files.
- Add direct aircraft details access from map aircraft icons.

---

## 1. Web UI Asset Split

### Functional Requirement

Make the web UI easier to maintain by splitting the monolithic `index.html` into separate HTML, CSS, and JavaScript assets.

### Change Made

The web UI was split into:

- `web/index.html`
- `web/app.css`
- `web/app.js`

The HTML now loads:

- `/app.css?v=3.2.1`
- `/app.js?v=3.2.1`

The backend static file handler was updated so the Pi API service can serve `app.css` and `app.js` directly.

The deployment script was updated so the split assets are copied to the Pi deployment directory.

### Porting Notes for Windows

The Windows app should use the same split file structure if it does not already:

- Keep HTML layout in `index.html`.
- Keep styling in `app.css`.
- Keep UI behavior in `app.js`.
- Ensure the Windows backend or service packaging serves all three files.
- Avoid loading `app.js` with `defer` unless the initialization order is retested.

---

## 2. Active Trail Cleanup While Preserving History

### Functional Requirement

When an aircraft leaves the live receiver range, remove its active visible trail from the map so stale trails do not clutter the live display. Preserve browser-side trail history so Restore History can still show older tracks.

### Change Made

The map update path now tracks which aircraft are currently visible in the live readsb aircraft set. When a marker is removed because the aircraft is no longer visible:

- The marker is removed from the map.
- Active trail layers for that aircraft are cleared.
- Last-position tracking for that aircraft is removed.
- Stored browser trail history remains available for Restore History.

### Porting Notes for Windows

The same behavior should be implemented in the Windows map refresh loop:

- Maintain a `visibleIds` set for the current aircraft refresh.
- Remove markers not in the current set.
- Clear active trail layers for removed markers.
- Preserve retained trail-history storage separately from active live trail layers.

---

## 3. Aircraft Photo Fallback Improvements

### Functional Requirement

Improve aircraft photo display when the primary ADSBDB photo lookup does not return a useful image.

### Change Made

The aircraft details popup now supports layered aircraft photo enrichment:

1. ADSBDB aircraft data and photo lookup first.
2. Local or derived aircraft metadata fallback when ADSBDB is incomplete.
3. Representative aircraft image fallback based on manufacturer/model/operator/type.
4. Filtering to avoid bad image matches such as logos, icons, placeholders, SVGs, and social-media images.
5. Local fallback cache for aircraft-photo results.

The fallback logic was tuned for:

- Boeing model synonyms, including MAX variants.
- Airbus model synonyms.
- Bombardier, Embraer, and other common business/regional aircraft model naming.
- Operator-aware image searches.
- Cleaner UI behavior while a photo lookup is in progress.

### Porting Notes for Windows

Port the same functional order:

- Keep ADSBDB as the preferred source.
- Add type/model/operator representative image fallback.
- Add strict filtering against logos/icons/placeholders.
- Cache fallback image decisions locally.
- Avoid endless UI retry loops. The popup should show a stable fallback or a clean unavailable state.

---

## 4. Local tar1090 Aircraft Cache Fallback

### Functional Requirement

When ADSBDB cannot provide aircraft registration, manufacturer, model, or operator data, use a local tar1090-db aircraft cache by ICAO hex.

### Change Made

A local aircraft endpoint was added:

- `/api/aircraft/local?hex=<ICAO_HEX>`

It reads the local tar1090 aircraft cache, expected at:

- `/opt/rtl-pi-adsb-tracker/settings/aircraft_hex_db.json`

The endpoint normalizes records into an ADSBDB-like response shape when possible:

- `registration`
- `manufacturer`
- `type`
- `icao_type`
- `registered_owner`
- source marker: `local tar1090-db aircraft cache`

The UI attempts the local fallback when ADSBDB does not return aircraft data.

### Porting Notes for Windows

The Windows app should provide an equivalent endpoint or local lookup function:

- Use the same aircraft cache file shape where practical.
- Lookup by normalized uppercase ICAO hex.
- Return the same normalized field names so the shared UI logic works unchanged.
- Ensure the local cache import produces a full aircraft list, not just metadata.

---

## 5. AirLabs Route Lookup Normalization

### Functional Requirement

Improve route lookup for airline callsigns that are valid but do not always match AirLabs using the exact broadcast callsign.

### Change Made

The UI route lookup now tries normalized ICAO-style variants for callsigns with leading zeros.

Examples:

- `KAL032` can retry as `KAL32`.
- `AAL1757` remains `AAL1757`.
- Similar numeric normalization is supported for other airline-style callsigns.

The route popup continues to show:

- From airport
- To airport
- Route source
- Cached/fresh AirLabs status when applicable

### Porting Notes for Windows

Port the callsign normalization helper and make sure the UI calls the same route API path used successfully by the backend.

Use this general rule:

- Try original ICAO callsign first.
- Then try no-leading-zero numeric variant.
- Do not replace the primary lookup with an untested alternate endpoint unless the backend supports it fully.

---

## 6. Private, Charter, Regional, and Operator Callsign Classification

### Functional Requirement

Avoid showing a misleading generic `AirLabs - no route match` message for private, charter, regional, or operator callsigns that are often not scheduled airline flights.

### Change Made

A local callsign-prefix classification table was added for known non-standard route cases.

Examples:

- `KOW` -> Baker Aviation / Rodeo
- `LYM` -> Key Lime Air
- `LXJ` -> Flexjet
- `EJA` -> NetJets
- `XOJ` -> XOJET
- `FTH` -> Mountain Aviation
- `GAJ` / `WUP` -> Wheels Up
- `JTL` -> Jet Linx
- `TWY` -> Solairus Aviation
- `PEG` -> Pegasus Elite Aviation
- `XSR` -> Executive Flight Services

When AirLabs does not return a route for one of these prefixes, the route source now shows a clearer classification such as:

- `Private/charter callsign - Baker Aviation / Rodeo; route not available from AirLabs`
- `Private/charter callsign - Key Lime Air; route not available from AirLabs`

### Porting Notes for Windows

Port the prefix table and the `routeNoMatchSourceMessage()` style behavior.

Important distinction:

- Do not override real aircraft owner/operator data.
- Use the callsign-prefix classification specifically for route-source explanation.
- Keep the aircraft operator/photo operator and route callsign operator separate when they differ.

---

## 7. Tail-Number / Registration Callsign Classification

### Functional Requirement

Handle aircraft broadcasting a registration or tail number as the flight ID, such as `N653JC`.

These are not scheduled airline flight numbers, so AirLabs route lookup should not be expected to return a route.

### Change Made

Tail-number callsign detection was added for common registration-style patterns, including:

- United States N-numbers such as `N653JC`, `N523FX`, `N12345`
- Conservative support for several non-US civil registration patterns, such as:
  - `C-FABC`
  - `G-ABCD`
  - `D-ABCD`
  - `F-ABCD`
  - `VH-ABC`
  - `ZK-ABC`
  - `JA1234`
  - `HL8085`

When a route is not found and the callsign is a tail-number-style callsign, the UI now shows:

- `Private/general aviation tail-number callsign - <CALLSIGN>; route not available from AirLabs`

### Porting Notes for Windows

Port the same tail-number detection helper and route-source message behavior.

This should be applied only to route-source display, not aircraft identity display.

---

## 8. Aircraft Details Popup from Map Marker Double-Click

### Functional Requirement

Allow the user to open the full aircraft details dialog directly from the aircraft icon on the map.

The dialog must be the same aircraft details popup used by the aircraft list, not the small Leaflet popup.

### Change Made

The aircraft map update flow now stores the current aircraft record on each map marker/icon. A map-container capture-level double-click handler detects double-clicks on Leaflet marker icons and opens the existing aircraft details dialog with the stored aircraft record.

Implemented behavior:

- Double-click aircraft icon on map.
- Prevent Leaflet double-click zoom from consuming the interaction.
- Open the populated aircraft details dialog using the same data object used by the list.
- Do not open a blank dialog if no aircraft record is available.

### Porting Notes for Windows

The Windows app should implement this directly in the marker refresh loop:

- Keep each marker associated with the latest aircraft object.
- Disable or intercept map double-click zoom for aircraft markers.
- Use the same detail-popup function called by the aircraft list.
- Avoid generic marker monkey-patches.
- Avoid dispatching fake DOM row events unless absolutely necessary.

---

## 9. Improved Route Source Messages

### Functional Requirement

Make route-source status clearer and less misleading.

### Change Made

The route source field now distinguishes among:

- Real AirLabs route result
- AirLabs cached route result
- AirLabs no route match
- Private/charter callsign with route unavailable
- Tail-number/general aviation callsign with route unavailable
- AirLabs unavailable or not configured

This improves interpretation of popups for airline, charter, private, and GA traffic.

### Porting Notes for Windows

Use the same route-source classification order:

1. If route found, show AirLabs source.
2. If tail-number callsign, show tail-number/general aviation route unavailable.
3. If known private/charter prefix, show private/charter route unavailable with operator label.
4. Otherwise show generic AirLabs no-match.

---

## 10. README Updates for v3.3.0

### Functional Requirement

Update repository documentation to describe the current v3.3.0 feature set.

### Change Made

The README should now describe:

- Split web asset structure.
- Aircraft map and trail behavior.
- Aircraft details popup from map double-click.
- Aircraft photo and metadata fallback behavior.
- Local tar1090 aircraft cache fallback.
- AirLabs route enrichment and classification behavior.
- Private/charter and tail-number route-source handling.
- Updated deployment expectations for `web/index.html`, `web/app.css`, and `web/app.js`.

### Porting Notes for Windows

The Windows README should mirror these functional descriptions where the same features exist or are being ported.

---

## Suggested Windows Port Order

Use this order to reduce risk:

1. Confirm Windows web UI is split into `index.html`, `app.css`, and `app.js`.
2. Port active-trail cleanup while preserving history.
3. Port local tar1090 aircraft cache fallback.
4. Port aircraft photo fallback/cache logic.
5. Port AirLabs normalized ICAO callsign route lookup.
6. Port private/charter callsign classification.
7. Port tail-number callsign classification.
8. Port aircraft map double-click details popup.
9. Update Windows README and release notes.

---

## Validation Checklist for Windows Port

After porting, validate with examples:

### Scheduled Airline Route

- `AAL1757`
- Expected: AirLabs route found when live/available, such as DEN to PHX.

### Leading-Zero Airline Callsign

- `KAL032`
- Expected: Retry normalized `KAL32` if original does not match.

### Private/Charter Prefix

- `KOW523`
- Expected: Private/charter route-source explanation for Baker Aviation / Rodeo when AirLabs has no route.

### Regional/Charter Prefix

- `LYM3583`
- Expected: Private/charter route-source explanation for Key Lime Air when AirLabs has no route.

### Tail-Number Callsign

- `N653JC`
- Expected: Private/general aviation tail-number route-source explanation.

### Map Interaction

- Double-click aircraft icon.
- Expected: Full aircraft details dialog opens with populated data.
- Expected: Map does not zoom instead of opening details.

### Trail Cleanup

- Aircraft leaves live range.
- Expected: Marker and active trail disappear.
- Expected: Restore History can still recover retained trail history.

---

## Files Commonly Changed in the Pi Implementation

The main functional changes were concentrated in:

- `web/app.js`
- `web/app.css`
- `web/index.html`
- `src/rtl_pi_api.py`
- `tools/deploy_api_backend.sh`
- `README.md`
- `docs/RTL-Pi-ADS-B-Tracker_Functional_Changes_Since_v3.0.md`

---

## v3.3.0 Functional Summary

v3.3.0 represents the checkpoint where the Pi app has:

- Split maintainable web assets.
- Improved live map trail cleanup.
- Improved aircraft identity/photo fallback.
- Local tar1090 aircraft metadata fallback.
- Better AirLabs route lookup behavior.
- Clearer private/charter/tail-number route-source messaging.
- Map aircraft double-click opening the full aircraft details dialog.

This is the recommended functional baseline for porting equivalent changes into the Windows ADS-B Tracker app.

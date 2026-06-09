// RTL Pi ADS-B Tracker app.js - split from index.html for v3.2.1

// Extracted from index.html <script> block 1
(function () {
  "use strict";

  if (window.__airbandManualStartGuardV2Installed) {
    return;
  }

  window.__airbandManualStartGuardV2Installed = true;
  window.__airbandManualScannerAllowStartUntil = 0;

  function textOfCallback(callback) {
    try {
      return String(callback || "");
    } catch (_error) {
      return "";
    }
  }

  function looksLikeOldAirbandAutoStart(callback) {
    const text = textOfCallback(callback);
    return (
      text.indexOf("/api/airband/scan/activity/start") !== -1 ||
      text.indexOf("Starting Airband Background") !== -1 ||
      text.indexOf("Start Airband Background") !== -1 ||
      text.indexOf("startAirbandBackground") !== -1 ||
      text.indexOf("ensureAirbandBackground") !== -1 ||
      text.indexOf("autoStartAirband") !== -1
    );
  }

  function isAirbandScannerStart(input) {
    const url = typeof input === "string" ? input : (input && input.url ? input.url : "");
    return url.indexOf("/api/airband/scan/activity/start") !== -1;
  }

  function allowManualAirbandStartForGesture() {
    window.__airbandManualScannerAllowStartUntil = Date.now() + 5000;
  }

  document.addEventListener("click", function (event) {
    const target = event.target && event.target.closest ? event.target.closest("button") : null;
    const text = target ? (target.textContent || "").trim() : "";
    if (/^Start Scanner$/i.test(text)) {
      allowManualAirbandStartForGesture();
    }
  }, true);

  const originalSetInterval = window.setInterval.bind(window);
  window.setInterval = function (callback, delay) {
    if (looksLikeOldAirbandAutoStart(callback)) {
      console.log("Blocked old Airband auto-start interval. Use Start Scanner.");
      return 0;
    }
    return originalSetInterval.apply(window, arguments);
  };

  const originalSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = function (callback, delay) {
    if (looksLikeOldAirbandAutoStart(callback)) {
      console.log("Blocked old Airband auto-start timeout. Use Start Scanner.");
      return 0;
    }
    return originalSetTimeout.apply(window, arguments);
  };

  const originalFetch = window.fetch ? window.fetch.bind(window) : null;
  if (originalFetch) {
    window.fetch = function (input, init) {
      if (isAirbandScannerStart(input) && Date.now() > window.__airbandManualScannerAllowStartUntil) {
        console.log("Blocked automatic Airband scanner start. Use Start Scanner.");
        return Promise.resolve(new Response(JSON.stringify({
          service: "rtl-pi-api",
          manual_start_required: true,
          airband_scan_running: false,
          airband_scan_state: "manual_start_required",
          airband_scanner_message: "Airband scanner is stopped. Click Start Scanner to arm browser audio and begin scanning."
        }), {
          status: 200,
          headers: { "Content-Type": "application/json; charset=utf-8" }
        }));
      }
      return originalFetch(input, init);
    };
  }

  function hideOldAirbandBusyPopup() {
    const candidates = Array.from(document.querySelectorAll("dialog, [role='dialog'], .modal, .popup, .overlay, div, section"));
    for (const node of candidates) {
      const text = node.textContent || "";
      if (
        text.indexOf("Starting Airband Background") !== -1 ||
        text.indexOf("Start Airband Background") !== -1
      ) {
        const style = window.getComputedStyle(node);
        const isOverlayLike =
          style.position === "fixed" ||
          style.position === "absolute" ||
          node.getAttribute("role") === "dialog" ||
          node.tagName.toLowerCase() === "dialog" ||
          /modal|popup|overlay|busy/i.test(node.className || node.id || "");
        if (isOverlayLike) {
          node.style.setProperty("display", "none", "important");
          node.style.setProperty("visibility", "hidden", "important");
          node.setAttribute("aria-hidden", "true");
        }
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hideOldAirbandBusyPopup);
  } else {
    hideOldAirbandBusyPopup();
  }

  document.addEventListener("DOMContentLoaded", function () {
    const observer = new MutationObserver(hideOldAirbandBusyPopup);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
})();


// Extracted from index.html <script> block 2
'use strict';

let audioObjectUrl = null;
let liveListening = false;
let liveAudioContext = null;
let liveNextCursor = 0;
let liveNextPlayTime = 0;
let livePumpTimer = null;
let airbandTestPlayedEventId = 0;
let aircraftMap = null;
let receiverMapMarker = null;
let receiverRangeRings = null;
let receiverMapLocation = null;
let aircraftMapMarkers = new Map();
let aircraftLastPositions = new Map();
let aircraftTrailSegments = new Map();
let aircraftMapFirstFit = true;
let receiverLocationPickActive = false;
let receiverLocationPreview = null;
const TRAIL_STORAGE_KEY = 'rtlPiAdsbTrailHistoryV1';
const TRAIL_RETENTION_KEY = 'rtlPiAdsbTrailRetentionMinutes';
const TRAIL_CLEARED_AT_KEY = 'rtlPiAdsbTrailClearedAtV1';
let aircraftTrailHistory = new Map();
let aircraftTrailRetentionMinutes = Number(localStorage.getItem(TRAIL_RETENTION_KEY) || '60');
let aircraftTrailClearedAt = Number(localStorage.getItem(TRAIL_CLEARED_AT_KEY) || '0');

function el(id) { return document.getElementById(id); }
function setText(id, value) { const node = el(id); if (node) node.textContent = value; }
function formattedNumber(value) { return typeof value === 'number' ? value.toLocaleString() : '—'; }
function setMessage(id, message, kind) {
  const node = el(id);
  if (!node) return;
  node.textContent = message;
  node.className = 'message ' + (kind || '');
}
async function jsonRequest(url, options) {
  const response = await fetch(url, Object.assign({cache: 'no-store'}, options || {}));
  let result = null;
  try { result = await response.json(); } catch (_) {}
  if (!response.ok) {
    throw new Error((result && result.error) ? result.error : `Request failed: HTTP ${response.status}`);
  }
  return result;
}

// AIRLABS_ROUTE2_IATA_VARIANTS_UI_PATCH_V1

// PRIVATE_CHARTER_ROUTE_SOURCE_PATCH_V1
const PRIVATE_CHARTER_CALLSIGN_PREFIXES = {
  KOW: 'Baker Aviation / Rodeo',
  LYM: 'Key Lime Air',
  LXJ: 'Flexjet',
  EJA: 'NetJets',
  NJE: 'NetJets Europe',
  XOJ: 'XOJET',
  FTH: 'Mountain Aviation',
  GAJ: 'Wheels Up',
  WUP: 'Wheels Up',
  DPJ: 'Delta Private Jets',
  JTL: 'Jet Linx',
  PEG: 'Pegasus Elite Aviation',
  TWY: 'Solairus Aviation',
  XSR: 'Executive Flight Services',
  OPT: 'Flight Options'
};

function callsignPrefix(callsign) {
  const match = String(callsign || '').trim().toUpperCase().match(/^([A-Z]{2,4})/);
  return match ? match[1] : '';
}

function privateCharterOperatorForCallsign(callsign) {
  const prefix = callsignPrefix(callsign);
  return PRIVATE_CHARTER_CALLSIGN_PREFIXES[prefix] || '';
}


// TAIL_NUMBER_ROUTE_SOURCE_PATCH_V1
function isTailNumberCallsign(callsign) {
  const value = String(callsign || '').trim().toUpperCase().replace(/\s+/g, '');
  if (!value) return false;

  // United States N-numbers: N + 1-5 digits with optional one/two trailing letters.
  if (/^N[0-9]{1,5}[A-Z]{0,2}$/.test(value)) return true;

  // Conservative non-US civil registration patterns sometimes seen as ADS-B callsigns.
  if (/^C-[FGI][A-Z]{3}$/.test(value)) return true;
  if (/^G-[A-Z]{4}$/.test(value)) return true;
  if (/^D-[A-Z]{4}$/.test(value)) return true;
  if (/^F-[A-Z]{4}$/.test(value)) return true;
  if (/^VH-[A-Z]{3}$/.test(value)) return true;
  if (/^ZK-[A-Z]{3}$/.test(value)) return true;
  if (/^JA[0-9]{4}[A-Z]{0,2}$/.test(value)) return true;
  if (/^HL[0-9]{4}$/.test(value)) return true;

  return false;
}

function tailNumberRouteSourceMessage(callsign) {
  const value = String(callsign || '').trim().toUpperCase().replace(/\s+/g, '');
  if (value) {
    return `Private/general aviation tail-number callsign - ${value}; route not available from AirLabs`;
  }
  return 'Private/general aviation tail-number callsign - route not available from AirLabs';
}

// /TAIL_NUMBER_ROUTE_SOURCE_PATCH_V1

function routeNoMatchSourceMessage(callsign) {
  if (isTailNumberCallsign(callsign)) {
    return tailNumberRouteSourceMessage(callsign);
  }

  const operator = privateCharterOperatorForCallsign(callsign);
  if (operator) {
    return `Private/charter callsign - ${operator}; route not available from AirLabs`;
  }

  return 'AirLabs - no route match';
}

// /PRIVATE_CHARTER_ROUTE_SOURCE_PATCH_V1

const AIRLABS_ICAO_TO_IATA = {
  AAL: 'AA',
  ACA: 'AC',
  AFR: 'AF',
  ASA: 'AS',
  BAW: 'BA',
  CPA: 'CX',
  DAL: 'DL',
  DLH: 'LH',
  FFT: 'F9',
  JAL: 'JL',
  JBU: 'B6',
  KAL: 'KE',
  KLM: 'KL',
  LYM: 'KG',
  NKS: 'NK',
  QTR: 'QR',
  SWA: 'WN',
  THY: 'TK',
  UAE: 'EK',
  UAL: 'UA',
  VOI: 'Y4'
};

function airlabsFlightRouteCandidates(callsign) {
  const raw = String(callsign || '').trim().toUpperCase().replace(/\s+/g, '');
  if (!raw) return [];

  const candidates = [];
  const add = (kind, flight, source) => {
    const value = String(flight || '').trim().toUpperCase();
    if (!value) return;
    const key = `${kind}:${value}`;
    if (!candidates.some(candidate => candidate.key === key)) {
      candidates.push({kind, flight: value, source, key});
    }
  };

  add('flight_icao', raw, 'original');

  const match = raw.match(/^([A-Z]{2,4})([0-9]{1,5}[A-Z]?)$/);
  if (!match) return candidates;

  const icaoPrefix = match[1];
  const suffix = match[2];
  const suffixMatch = suffix.match(/^0*([0-9]+)([A-Z]?)$/);
  if (!suffixMatch) return candidates;

  const normalizedNumber = String(parseInt(suffixMatch[1], 10)) + (suffixMatch[2] || '');
  const normalizedIcao = `${icaoPrefix}${normalizedNumber}`;
  if (normalizedIcao !== raw) add('flight_icao', normalizedIcao, 'normalized_icao');

  const iataPrefix = AIRLABS_ICAO_TO_IATA[icaoPrefix];
  if (iataPrefix) {
    add('flight_iata', `${iataPrefix}${suffix}`, 'iata');
    add('flight_iata', `${iataPrefix}${normalizedNumber}`, 'normalized_iata');
  }

  return candidates;
}

async function fetchAirLabsRouteWithVariants(callsign) {
  const candidates = airlabsFlightRouteCandidates(callsign);
  let firstResponse = null;

  for (const candidate of candidates) {
    try {
      const parameter = candidate.kind === 'flight_iata' ? 'flight_iata' : 'flight_icao';
      const response = await jsonRequest(`/api/diagnostics/airlabs/route2?${parameter}=${encodeURIComponent(candidate.flight)}`);
      if (!firstResponse) firstResponse = response;

      if (response && response.route && response.route.found) {
        if (candidate.flight !== String(callsign || '').trim().toUpperCase()) {
          response.route.flight_original = String(callsign || '').trim().toUpperCase();
          response.route.flight_lookup_used = candidate.flight;
          response.route.flight_lookup_kind = candidate.kind;
          response.route.flight_lookup_source = candidate.source;
        }
        return response;
      }
    } catch (_error) {
      // Try the next candidate.
    }
  }

  return firstResponse || {
    route: {
      found: false,
      flight_icao: String(callsign || '').trim().toUpperCase(),
      source: 'airlabs',
      message: 'AirLabs returned no matching flights.'
    }
  };
}

// /AIRLABS_ROUTE2_IATA_VARIANTS_UI_PATCH_V1


function altitudeFeet(aircraft) {
  const value = aircraft.alt_baro != null ? aircraft.alt_baro : aircraft.alt_geom;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function trailColor(altitude) {
  if (altitude == null) return '#a0aab8';
  if (altitude < 5001) return '#39ff14';
  if (altitude < 10001) return '#087830';
  if (altitude < 20001) return '#39cfff';
  if (altitude < 30001) return '#1851b5';
  if (altitude < 40001) return '#d4a600';
  return '#ff3030';
}
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}
function initializeAircraftMap() {
  if (typeof L === 'undefined') {
    setMessage('mapMessage', 'Map library could not load. The browser workstation must have internet access for Leaflet and map tiles.', 'error');
    return;
  }
  aircraftMap = L.map('aircraftMap').setView([29.7604, -95.3698], 9);
  
  aircraftMap.on('click', finishReceiverLocationPick);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(aircraftMap);
}
function setReceiverPreviewOnMap(latitude, longitude) {
  if (!aircraftMap) return;
  const position = [Number(latitude), Number(longitude)];
  receiverLocationPreview = position;
  if (!receiverMapMarker) {
    receiverMapMarker = L.circleMarker(position, {
      radius: 8, color: '#f2c35c', weight: 3, fillColor: '#2778d4', fillOpacity: 0.95
    }).addTo(aircraftMap).bindPopup('');
  } else {
    receiverMapMarker.setLatLng(position);
    if (receiverMapMarker.setStyle) receiverMapMarker.setStyle({color: '#f2c35c', weight: 3});
  }
  receiverMapMarker.setPopupContent(
    `<strong>Receiver Location Preview</strong><br>${position[0].toFixed(6)}, ${position[1].toFixed(6)}<br>Click Save Receiver Location to confirm.`
  );
  receiverMapMarker.openPopup();
  drawReceiverRangeRings(position);
}
function beginReceiverLocationPick() {
  if (!aircraftMap) {
    setMessage('locationMessage', 'Map is not available for location selection.', 'error');
    return;
  }
  receiverLocationPickActive = true;
  el('pickLocationOnMap').disabled = true;
  el('cancelLocationPick').disabled = false;
  el('aircraftMap').classList.add('map-location-pick-active');
  if (typeof closeMenu === 'function') closeMenu();
  setMessage(
    'mapMessage',
    'Location selection active: click the map at the physical antenna location.',
    'warning'
  );
}
function finishReceiverLocationPick(event) {
  if (!receiverLocationPickActive) return;
  const latitude = Number(event.latlng.lat);
  const longitude = Number(event.latlng.lng);

  el('locationLatitude').value = latitude.toFixed(6);
  el('locationLongitude').value = longitude.toFixed(6);
  if (el('locationName')) el('locationName').dataset.edited = 'true';
  setReceiverPreviewOnMap(latitude, longitude);

  receiverLocationPickActive = false;
  el('pickLocationOnMap').disabled = false;
  el('cancelLocationPick').disabled = true;
  el('aircraftMap').classList.remove('map-location-pick-active');

  if (typeof openMenu === 'function') openMenu();
  const pickerButton = el('pickLocationOnMap');
  const settings = el('configurationDetails') || (pickerButton ? pickerButton.closest('details') : null);
  if (settings) settings.open = true;

  setMessage(
    'locationMessage',
    `Map selection preview: ${latitude.toFixed(6)}, ${longitude.toFixed(6)}. Click Save Receiver Location to confirm.`,
    'warning'
  );
  setMessage('mapMessage', 'Receiver location preview selected. Save it from Configuration to apply.', 'warning');
}
function cancelReceiverLocationPick() {
  receiverLocationPickActive = false;
  el('pickLocationOnMap').disabled = false;
  el('cancelLocationPick').disabled = true;
  el('aircraftMap').classList.remove('map-location-pick-active');
  setMessage('locationMessage', 'Map location selection cancelled. Saved receiver location is unchanged.', '');
  setMessage('mapMessage', 'Map location selection cancelled.', '');
}

function drawReceiverRangeRings(position) {
  if (!aircraftMap || !position) return;
  if (receiverRangeRings) aircraftMap.removeLayer(receiverRangeRings);
  receiverRangeRings = L.layerGroup().addTo(aircraftMap);

  const milesToMeters = 1609.344;
  const center = [Number(position[0]), Number(position[1])];

  for (let miles = 5; miles <= 100; miles += 5) {
    const major = miles % 25 === 0;
    L.circle(center, {
      radius: miles * milesToMeters,
      color: major ? '#3e4650' : '#1d232a',
      weight: major ? 2.0 : 0.75,
      opacity: major ? 0.82 : 0.62,
      fill: false,
      interactive: false,
      dashArray: major ? null : '3 5'
    }).addTo(receiverRangeRings);

    if (major) {
      const labelPoint = L.latLng(center[0] + (miles / 69.0), center[1]);
      L.marker(labelPoint, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: '',
          html: `<div class="range-ring-major-label">${miles} mi</div>`,
          iconSize: [45, 18],
          iconAnchor: [22, 9]
        })
      }).addTo(receiverRangeRings);
    }
  }
}

function setReceiverOnMap(location) {
  if (!aircraftMap || !location) return;
  const position = [Number(location.latitude), Number(location.longitude)];
  receiverMapLocation = position;
  if (!receiverMapMarker) {
    receiverMapMarker = L.circleMarker(position, {
      radius: 8, color: '#ffffff', weight: 2, fillColor: '#2778d4', fillOpacity: 0.95
    }).addTo(aircraftMap).bindPopup('');
  } else {
    receiverMapMarker.setLatLng(position);
  }
  receiverMapMarker.setPopupContent(`<strong>Receiver</strong><br>${escapeHtml(location.name || '')}<br>${position[0].toFixed(5)}, ${position[1].toFixed(5)}`);
  if (aircraftMapFirstFit) aircraftMap.setView(position, 10);
  if (receiverMapMarker && receiverMapMarker.setStyle) {
    receiverMapMarker.setStyle({color: '#ffffff', weight: 2});
  }
  receiverLocationPreview = null;
  drawReceiverRangeRings(position);
}
function aircraftMapIcon(aircraft) {
  const color = '#5f6670';
  const track = Number.isFinite(Number(aircraft.track)) ? Number(aircraft.track) : 0;
  const flight = aircraft.flight ? String(aircraft.flight).trim() : '';
  const label = flight || String(aircraft.hex || '').toUpperCase();
  const html = `<div class="aircraft-icon-wrap">` +
    `<svg width="40" height="40" viewBox="0 0 40 40" style="transform:rotate(${track}deg)" aria-hidden="true">` +
    `<path d="M20 2 L23 15 L36 20 L36 23 L23 21 L22 34 L27 37 L27 39 L20 37 L13 39 L13 37 L18 34 L17 21 L4 23 L4 20 L17 15 Z"` +
    ` fill="${color}" stroke="#ffffff" stroke-width="1.25" stroke-linejoin="round"/>` +
    `</svg>` +
    `<span class="aircraft-icon-label">${escapeHtml(label)}</span>` +
    `</div>`;
  return L.divIcon({
    className: '',
    html: html,
    iconSize: [46, 42],
    iconAnchor: [20, 20],
    popupAnchor: [0, -18]
  });
}

function aircraftPopup(aircraft) {
  const flight = aircraft.flight ? String(aircraft.flight).trim() : '';
  const altitude = altitudeFeet(aircraft);
  const speed = aircraft.gs == null ? '—' : `${aircraft.gs} kt`;
  const track = aircraft.track == null ? '—' : `${aircraft.track}°`;
  return `<strong>${escapeHtml(flight || aircraft.hex || 'Unknown')}</strong><br>` +
    `ICAO: ${escapeHtml(aircraft.hex || '')}<br>` +
    `Altitude: ${altitude == null ? '—' : altitude.toLocaleString() + ' ft'}<br>` +
    `Speed: ${escapeHtml(speed)} &nbsp; Track: ${escapeHtml(track)}`;
}
function trailCutoffTime() {
  const retentionCutoff = aircraftTrailRetentionMinutes > 0
    ? Date.now() - aircraftTrailRetentionMinutes * 60000
    : 0;
  return Math.max(retentionCutoff, aircraftTrailClearedAt);
}
function pruneTrailHistory() {
  const cutoff = trailCutoffTime();
  let allPoints = [];
  for (const [key, points] of aircraftTrailHistory.entries()) {
    const retained = points.filter(point => !cutoff || point.time >= cutoff).slice(-1440);
    if (retained.length) {
      aircraftTrailHistory.set(key, retained);
      allPoints.push(...retained.map(point => ({key: key, point: point})));
    } else {
      aircraftTrailHistory.delete(key);
    }
  }
  if (allPoints.length > 12000) {
    allPoints.sort((a, b) => a.point.time - b.point.time);
    const removeCount = allPoints.length - 12000;
    for (const entry of allPoints.slice(0, removeCount)) {
      const points = aircraftTrailHistory.get(entry.key) || [];
      const index = points.indexOf(entry.point);
      if (index >= 0) points.splice(index, 1);
      if (!points.length) aircraftTrailHistory.delete(entry.key);
    }
  }
}
function saveTrailHistory() {
  try {
    pruneTrailHistory();
    const serializable = Object.fromEntries(aircraftTrailHistory.entries());
    if (aircraftTrailHistory.size) {
      localStorage.setItem(TRAIL_STORAGE_KEY, JSON.stringify(serializable));
    } else {
      localStorage.removeItem(TRAIL_STORAGE_KEY);
    }
    localStorage.setItem(TRAIL_RETENTION_KEY, String(aircraftTrailRetentionMinutes));
  } catch (_) {
    setMessage('mapMessage', 'Map is live, but browser storage could not save aircraft trails.', 'warning');
  }
}
function loadTrailHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(TRAIL_STORAGE_KEY) || '{}');
    aircraftTrailHistory = new Map(Object.entries(saved).filter(([, points]) => Array.isArray(points)));
    pruneTrailHistory();
  } catch (_) {
    aircraftTrailHistory = new Map();
    localStorage.removeItem(TRAIL_STORAGE_KEY);
  }
}
function removeTrailLayers() {
  if (!aircraftMap) return;
  for (const segments of aircraftTrailSegments.values()) {
    for (const segment of segments) aircraftMap.removeLayer(segment);
  }
  aircraftTrailSegments.clear();
}

// ACTIVE_TRAILS_CLEAR_WHEN_AIRCRAFT_EXITS_PATCH_V2
// Remove only currently displayed active trail segments for one aircraft.
// This intentionally does not delete aircraftTrailHistory, so Restore History
// can bring the old trail back later.
function removeActiveTrailLayersForAircraft(key) {
  if (!aircraftMap || !key) return;
  const segments = aircraftTrailSegments.get(key) || [];
  for (const segment of segments) {
    try {
      aircraftMap.removeLayer(segment);
    } catch (_error) {
      // Ignore stale Leaflet layer references.
    }
  }
  aircraftTrailSegments.delete(key);
}

// /ACTIVE_TRAILS_CLEAR_WHEN_AIRCRAFT_EXITS_PATCH_V2

function formatTrailLastSeen(timestamp) {
  const milliseconds = Number(timestamp);
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return 'Unknown';
  return new Intl.DateTimeFormat([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short'
  }).format(new Date(milliseconds));
}

const trailRouteHoverCache = new Map();

async function updateTrailTooltipRoute(segment, tooltipHeader, flight) {
  const callsign = String(flight || '').trim().toUpperCase();
  if (!callsign) return;

  segment.setTooltipContent(`${tooltipHeader}<br>From / To: Looking up…`);

  let routePromise = trailRouteHoverCache.get(callsign);
  if (!routePromise) {
    routePromise = requestAirlabsDiagnosticRoute(callsign);
    trailRouteHoverCache.set(callsign, routePromise);
  }

  try {
    const result = await routePromise;
    if (!result.matched) {
      trailRouteHoverCache.delete(callsign);
      segment.setTooltipContent(
        `${tooltipHeader}<br>From / To: Unavailable`
      );
      return;
    }

    const origin = escapeHtml(formatAirlabsAirport(result.departure_iata, result.departure_icao));
    const destination = escapeHtml(formatAirlabsAirport(result.arrival_iata, result.arrival_icao));
    const source = result.cache_hit ? 'AirLabs (cached)' : 'AirLabs';
    segment.setTooltipContent(
      `${tooltipHeader}<br>From: ${origin}<br>To: ${destination}<br>Source: ${source}`
    );
  } catch (error) {
    trailRouteHoverCache.delete(callsign);
    segment.setTooltipContent(`${tooltipHeader}<br>From / To: Lookup unavailable`);
  }
}

function addTrailSegment(key, prior, current) {
  const segment = L.polyline(
    [[prior.lat, prior.lon], [current.lat, current.lon]],
    {color: trailColor(current.altitude), weight: 3, opacity: 0.86}
  ).addTo(aircraftMap);

  const flight = String(current.flight || prior.flight || '').trim();
  const identifier = flight || String(current.hex || prior.hex || key || '').toUpperCase();
  const identifierLabel = flight ? 'Call Sign' : 'ICAO';
  const lastSeen = formatTrailLastSeen(current.time || prior.time);
  if (identifier) {
    const tooltipHeader =
      `<strong>${identifierLabel}: ${escapeHtml(identifier)}</strong><br>` +
      `Last Seen: ${escapeHtml(lastSeen)}`;
    const tooltipHtml = tooltipHeader +
      (flight ? '<br>From / To: Hover to look up' : '<br>From / To: Unavailable without callsign');
    segment.bindTooltip(tooltipHtml, {
      sticky: true,
      direction: 'top',
      opacity: 0.94,
      className: 'trail-hover-label'
    });
    if (flight) {
      segment.on('tooltipopen', () => updateTrailTooltipRoute(segment, tooltipHeader, flight));
    }
  }

  const segments = aircraftTrailSegments.get(key) || [];
  segments.push(segment);
  while (segments.length > 1440) aircraftMap.removeLayer(segments.shift());
  aircraftTrailSegments.set(key, segments);
}
function renderStoredTrails() {
  if (!aircraftMap) return;
  removeTrailLayers();
  aircraftLastPositions.clear();
  pruneTrailHistory();
  for (const [key, points] of aircraftTrailHistory.entries()) {
    for (let index = 1; index < points.length; index += 1) {
      addTrailSegment(key, points[index - 1], points[index]);
    }
    if (points.length) {
      const last = points[points.length - 1];
      aircraftLastPositions.set(key, [last.lat, last.lon]);
    }
  }
}
function recordTrailPoint(key, point, altitude, aircraft) {
  const points = aircraftTrailHistory.get(key) || [];
  const previous = points.length ? points[points.length - 1] : null;
  if (previous && previous.lat === point[0] && previous.lon === point[1]) return;
  const current = {
    lat: point[0],
    lon: point[1],
    altitude: altitude,
    time: Date.now(),
    flight: aircraft && aircraft.flight ? String(aircraft.flight).trim() : '',
    hex: aircraft && aircraft.hex ? String(aircraft.hex).toUpperCase() : String(key).toUpperCase(),
    track: aircraft ? aircraft.track : null
  };
  if (previous) addTrailSegment(key, previous, current);
  points.push(current);
  aircraftTrailHistory.set(key, points);
  aircraftLastPositions.set(key, point);
  saveTrailHistory();
}
async function loadPiTrailHistory(restoreCleared = false) {
  try {
    const response = await fetch('/api/trails/history', {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const serverTrails = data.trails || {};
    const serverPointCount = Object.values(serverTrails)
      .reduce((total, points) => total + (Array.isArray(points) ? points.length : 0), 0);

    if (restoreCleared && serverPointCount > 0) {
      aircraftTrailClearedAt = 0;
      localStorage.removeItem(TRAIL_CLEARED_AT_KEY);
    }

    let restoredPointCount = 0;
    let hiddenByClearCount = 0;
    const beforeCount = Array.from(aircraftTrailHistory.values())
      .reduce((total, points) => total + points.length, 0);

    for (const [key, points] of Object.entries(serverTrails)) {
      if (!Array.isArray(points)) continue;
      const eligible = points.filter(point => {
        const allowed = restoreCleared || Number(point.time) >= aircraftTrailClearedAt;
        if (!allowed) hiddenByClearCount += 1;
        return allowed;
      });
      const existing = aircraftTrailHistory.get(key) || [];
      const merged = [...existing, ...eligible]
        .sort((left, right) => Number(left.time) - Number(right.time));
      const unique = [];
      const seen = new Set();
      for (const point of merged) {
        const signature = `${point.time}|${point.lat}|${point.lon}`;
        if (!seen.has(signature)) {
          seen.add(signature);
          unique.push(point);
        }
      }
      if (unique.length) {
        aircraftTrailHistory.set(key, unique);
      }
    }

    saveTrailHistory();
    renderStoredTrails();

    const afterCount = Array.from(aircraftTrailHistory.values())
      .reduce((total, points) => total + points.length, 0);
    restoredPointCount = Math.max(0, afterCount - beforeCount);

    if (!serverPointCount) {
      setMessage('mapMessage', 'Pi trail collector has no saved positioned aircraft yet.', '');
    } else if (restoreCleared) {
      setMessage('mapMessage',
        `Restored Pi history: ${serverPointCount} stored points across ${Object.keys(serverTrails).length} aircraft; ${restoredPointCount} points were newly added to this map.`,
        'good');
    } else if (hiddenByClearCount) {
      setMessage('mapMessage',
        `Pi has ${serverPointCount} stored points, but ${hiddenByClearCount} pre-clear points remain hidden. Click Restore Pi History to display them again.`,
        'warning');
    } else {
      setMessage('mapMessage',
        `Loaded Pi history: ${serverPointCount} stored points across ${Object.keys(serverTrails).length} aircraft; ${restoredPointCount} points were newly added.`,
        'good');
    }
  } catch (error) {
    setMessage('mapMessage', `Pi trail history unavailable: ${error.message}`, 'error');
  }
}

function changeTrailRetention() {
  aircraftTrailRetentionMinutes = Number(el('trailRetention').value);
  saveTrailHistory();
  renderStoredTrails();
  setMessage('mapMessage',
    aircraftTrailRetentionMinutes ? `Trail history retained for ${aircraftTrailRetentionMinutes} minutes in this browser.` : 'Trail history retained in this browser until cleared.',
    'good');
}


  // AIRCRAFT_MAP_DBLCLICK_CAPTURE_DETAILS_PATCH_V6
  function ensureAircraftMapDoubleClickDetailsHandler() {
    if (!aircraftMap) return;

    if (aircraftMap.doubleClickZoom) {
      aircraftMap.doubleClickZoom.disable();
    }

    if (aircraftMap.__aircraftDetailsDblClickCaptureV6) return;

    const container = aircraftMap.getContainer ? aircraftMap.getContainer() : null;
    if (!container) return;

    container.addEventListener('dblclick', event => {
      const target = event.target;
      const icon = target && target.closest ? target.closest('.leaflet-marker-icon') : null;
      if (!icon || !icon.__aircraftDetailsMarkerV6) return;

      event.preventDefault();
      event.stopPropagation();
      if (event.stopImmediatePropagation) event.stopImmediatePropagation();

      if (aircraftMap.doubleClickZoom) {
        aircraftMap.doubleClickZoom.disable();
      }

      const marker = icon.__aircraftDetailsMarkerV6;
      const aircraft = marker && marker.__aircraftDetailsRecordV6;
      if (aircraft) {
        showAircraftDetails(aircraft);
      }
    }, true);

    aircraftMap.__aircraftDetailsDblClickCaptureV6 = true;
  }

  function attachAircraftMarkerDetailsRecord(marker, aircraft) {
    if (!marker || !aircraft) return;

    marker.__aircraftDetailsRecordV6 = aircraft;

    const attachIcon = () => {
      const icon = marker.getElement ? marker.getElement() : marker._icon;
      if (!icon) return;
      icon.__aircraftDetailsMarkerV6 = marker;
      icon.title = icon.title || 'Double-click for aircraft details';
    };

    attachIcon();

    if (typeof marker.on === 'function' && !marker.__aircraftDetailsAddHookV6) {
      marker.on('add', attachIcon);
      marker.__aircraftDetailsAddHookV6 = true;
    }

    window.setTimeout(attachIcon, 0);
  }
  // /AIRCRAFT_MAP_DBLCLICK_CAPTURE_DETAILS_PATCH_V6

function updateAircraftMap(aircraftRecords) {
  if (!aircraftMap) return;
    ensureAircraftMapDoubleClickDetailsHandler();
  const positioned = aircraftRecords.filter(item => Number.isFinite(Number(item.lat)) && Number.isFinite(Number(item.lon)));
  const visibleIds = new Set();

  for (const aircraft of positioned) {
    const key = String(aircraft.hex || aircraft.flight || `${aircraft.lat},${aircraft.lon}`);
    const point = [Number(aircraft.lat), Number(aircraft.lon)];
    visibleIds.add(key);
    const altitude = altitudeFeet(aircraft);
    const color = trailColor(altitude);
    recordTrailPoint(key, point, altitude, aircraft);

    let marker = aircraftMapMarkers.get(key);
    if (!marker) {
      marker = L.marker(point, {
        icon: aircraftMapIcon(aircraft),
        keyboard: false,
        riseOnHover: true
      }).addTo(aircraftMap);

      
      aircraftMapMarkers.set(key, marker);
    } else {
      marker.setLatLng(point);
      marker.setIcon(aircraftMapIcon(aircraft));
    }
    marker.bindPopup(aircraftPopup(aircraft));
      attachAircraftMarkerDetailsRecord(marker, aircraft);
  }

  for (const [key, marker] of aircraftMapMarkers.entries()) {
    if (!visibleIds.has(key)) {
      aircraftMap.removeLayer(marker);
      removeActiveTrailLayersForAircraft(key);
      aircraftMapMarkers.delete(key);
      aircraftLastPositions.delete(key);
    }
  }

  setMessage('mapMessage',
    positioned.length ? `Displaying ${positioned.length} aircraft with positions. Click an aircraft for details.` : 'No positioned aircraft currently reported by readsb.',
    positioned.length ? 'good' : '');

  if (aircraftMapFirstFit && positioned.length) {
    const points = positioned.map(item => [Number(item.lat), Number(item.lon)]);
    if (receiverMapLocation) points.push(receiverMapLocation);
    aircraftMap.fitBounds(points, {padding: [30, 30], maxZoom: 11});
    aircraftMapFirstFit = false;
  }
}
function fitAircraftMap() {
  if (!aircraftMap) return;
  const points = Array.from(aircraftMapMarkers.values()).map(marker => marker.getLatLng());
  if (receiverMapLocation) points.push(L.latLng(receiverMapLocation[0], receiverMapLocation[1]));
  if (points.length) aircraftMap.fitBounds(points, {padding: [30, 30], maxZoom: 12});
}
function centerReceiverMap() {
  if (aircraftMap && receiverMapLocation) aircraftMap.setView(receiverMapLocation, 10);
}
function clearAircraftTrails() {
  if (!aircraftMap) return;

  removeTrailLayers();
  aircraftLastPositions.clear();
  aircraftTrailHistory.clear();

  aircraftTrailClearedAt = Date.now();
  localStorage.setItem(TRAIL_CLEARED_AT_KEY, String(aircraftTrailClearedAt));
  localStorage.removeItem(TRAIL_STORAGE_KEY);

  setMessage(
    'mapMessage',
    'Display cleared. Pi history is still stored; click Restore Pi History to show it again.',
    'good'
  );
}

async function erasePiTrailHistory() {
  const confirmed = window.confirm(
    'Erase all Pi-stored aircraft trail history collected so far? This cannot be restored.'
  );
  if (!confirmed) return;

  removeTrailLayers();
  aircraftLastPositions.clear();
  aircraftTrailHistory.clear();
  aircraftTrailClearedAt = Date.now();
  localStorage.setItem(TRAIL_CLEARED_AT_KEY, String(aircraftTrailClearedAt));
  localStorage.removeItem(TRAIL_STORAGE_KEY);

  setMessage('mapMessage', 'Erasing browser and Pi-stored aircraft trails...', 'warning');
  try {
    const response = await fetch('/api/trails/clear', {method: 'POST', cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    aircraftTrailClearedAt = Number(result.cleared_utc_ms || aircraftTrailClearedAt);
    localStorage.setItem(TRAIL_CLEARED_AT_KEY, String(aircraftTrailClearedAt));
    setMessage(
      'mapMessage',
      'Pi history erased. Only new post-erase movement will be retained.',
      'good'
    );
  } catch (error) {
    setMessage(
      'mapMessage',
      `Display cleared, but Pi history erase failed: ${error.message}`,
      'error'
    );
  }
}

function updateAudioButtons(status) {
  const live = Boolean(status && status.live_audio_running);
  const busy = Boolean(status && status.audio_busy);
  el('startLive').disabled = busy || live;
  el('stopLive').disabled = !live;
  el('capture10').disabled = busy || live;
  el('autoNoaa').disabled = busy || live;
  const state = el('audioState');
  state.textContent = live ? 'Live' : (busy ? 'Busy' : 'Ready');
  state.className = 'value ' + ((busy || live) ? 'busy' : 'ready');
}

function renderLocation(location) {
  if (!location) {
    setMessage('locationMessage', 'No receiver location configured. Enter the antenna location and save it.', 'warning');
    return;
  }
  el('locationName').value = location.name || '';
  el('locationLatitude').value = location.latitude;
  el('locationLongitude').value = location.longitude;
  el('locationRadius').value = location.airband_radius_miles;
  setMessage('locationMessage',
    `Saved: ${location.name} (${Number(location.latitude).toFixed(6)}, ${Number(location.longitude).toFixed(6)}) · ${location.airband_radius_miles} mile radius.`,
    'good');
  if (el('airbandRadiusMessage')) {
    setMessage('airbandRadiusMessage', `Current Airband scan radius: ${Number(location.airband_radius_miles).toFixed(1)} miles.`, 'good');
  }
  setReceiverOnMap(location);
}

async function updateStatus() {
  try {
    const status = await jsonRequest('/api/status');
    setText('stationName', status.noaa_station || 'NOAA Weather');
    setText('stationDetail',
      `NOAA: ${(Number(status.noaa_frequency_hz) / 1000000).toFixed(3)} MHz NFM · Audio RTL-SDR S/N: ${status.audio_receiver_serial || '—'}`);
    setText('messageCount', formattedNumber(status.messages));
    setText('aircraftCount', formattedNumber(status.aircraft_count));
    setText('positionCount', formattedNumber(status.aircraft_with_position));
    updateAudioButtons(status);
    if (!el('locationName').dataset.edited) renderLocation(status.receiver_location);
  } catch (error) {
    setMessage('audioMessage', `Status failed: ${error.message}`, 'error');
  }
}

function detailValue(id, value) {
  el(id).textContent = value == null || String(value).trim() === '' ? '—' : String(value);
}
function formatAirport(airport) {
  if (!airport) return 'Unavailable';
  const code = airport.iata_code || airport.icao_code || '';
  const name = airport.name || airport.municipality || '';
  const place = airport.municipality && airport.name ? ` — ${airport.municipality}` : '';
  return `${name}${code ? ` (${code})` : ''}${place}` || 'Unavailable';
}
function closeAircraftDetails() {
  el('aircraftDetailOverlay').classList.remove('open');
}
function createDetailLink(text, url) {
  const link = document.createElement('a');
  link.textContent = text;
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}
function updateTailNumberActions(registration) {
  const actions = el('aircraftLookupActions');
  actions.replaceChildren();
  const tail = String(registration || '').trim().toUpperCase();
  if (!tail) return;

  actions.appendChild(createDetailLink(
    `ADSBDB Tail Lookup: ${tail}`,
    `https://api.adsbdb.com/v0/aircraft/${encodeURIComponent(tail)}`
  ));

  if (/^N[0-9A-Z]{1,5}$/.test(tail)) {
    actions.appendChild(createDetailLink(
      'FAA N-Number Inquiry',
      'https://registry.faa.gov/aircraftinquiry/search/nnumberinquiry'
    ));
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.textContent = `Copy ${tail} for FAA Search`;
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(tail);
        setMessage('aircraftDetailStatus', `${tail} copied. Paste it into the FAA N-Number Inquiry form.`, 'good');
      } catch (_) {
        setMessage('aircraftDetailStatus', `FAA search tail number: ${tail}`, 'warning');
      }
    });
    actions.appendChild(copy);
  }
}
function likelyTailNumber(value) {
  const text = String(value || '').trim().toUpperCase().replace(/\s+/g, '');
  return /^N[0-9A-Z]{1,5}$/.test(text) ||
    /^[A-Z]{1,2}-[A-Z0-9]{2,6}$/.test(text) ||
    /^[A-Z]{2,5}[0-9]{1,5}$/.test(text);
}
// AIRCRAFT_PHOTO_NATIVE_RENDER_FALLBACK_PATCH_V1
function normalizeAircraftPhotoTerm(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (!text || text === '—') return '';
  const lowered = text.toLowerCase();
  if (lowered === 'unavailable' || lowered === 'not broadcast' || lowered.startsWith('loading')) return '';
  return text;
}

function setNativeAircraftFallbackCredit(result) {
  const note = document.querySelector('.photo-source-note');
  if (!note) return;
  if (!note.dataset.originalText) note.dataset.originalText = note.textContent;
  const source = result && result.source ? result.source : 'fallback source';
  const credit = result && (result.representative || result.match_level === 'type')
    ? `Photo: representative aircraft type image from ${source}.`
    : `Photo: best guess from ${source}.`;
  note.textContent = `${note.dataset.originalText} ${credit}`;
}

function clearNativeAircraftFallbackCredit() {
  const note = document.querySelector('.photo-source-note');
  if (note && note.dataset.originalText) {
    note.textContent = note.dataset.originalText;
  }
}

// AIRCRAFT_PHOTO_SPINNER_UNTIL_COMPLETE_PATCH_V1
function setAircraftPhotoLoading(message, text = 'Looking up aircraft photo…') {
  if (!message) return;
  message.style.display = 'block';
  message.classList.add('aircraft-photo-loading');
  message.textContent = text;
}

function setAircraftPhotoFinalMessage(message, text) {
  if (!message) return;
  message.classList.remove('aircraft-photo-loading');
  message.style.display = 'block';
  message.textContent = text || 'No photograph available for this aircraft.';
}

// /AIRCRAFT_PHOTO_SPINNER_UNTIL_COMPLETE_PATCH_V1

function buildAircraftPhotoFallbackUrl(fallbackTerms) {
  const params = new URLSearchParams();
  const terms = fallbackTerms || {};
  for (const [key, value] of Object.entries(terms)) {
    const cleaned = normalizeAircraftPhotoTerm(value);
    if (cleaned) params.set(key, cleaned);
  }
  return params.toString() ? `/api/aircraft/photo/fallback?${params.toString()}` : '';
}

async function loadNativeAircraftFallbackPhoto(fallbackTerms) {
  const url = buildAircraftPhotoFallbackUrl(fallbackTerms);
  if (!url) return false;

  const image = el('aircraftPhoto');
  const message = el('aircraftPhotoMessage');
  const actions = el('aircraftPhotoActions');

  try {
    const response = await fetch(url, {cache: 'no-store'});
    const payload = await response.json();
    if (!response.ok) return false;

    const result = payload.result || {};
    if (!result.found || !result.image_url) return false;

    return await new Promise(resolve => {
      image.onload = () => {
        // AIRCRAFT_PHOTO_HIDE_SPINNER_AFTER_FALLBACK_LOAD_PATCH_V1
        message.classList.remove('aircraft-photo-loading');
        message.textContent = '';
        message.style.display = 'none';
        image.style.display = 'block';
        actions.replaceChildren();
        if (result.page_url) {
          actions.appendChild(createDetailLink(`Open ${result.source || 'Photo Source'}`, result.page_url));
        }
        setNativeAircraftFallbackCredit(result);
        resolve(true);
      };
      image.onerror = () => {
        message.classList.remove('aircraft-photo-loading');
        image.style.display = 'none';
        image.removeAttribute('src');
        resolve(false);
      };
      image.referrerPolicy = 'no-referrer';
      image.decoding = 'async';
      image.src = result.image_url;
    });
  } catch (_) {
    return false;
  }
}

function makeAircraftPhotoFallbackTerms(values) {
  const input = values || {};
  return {
    reg: input.reg || input.registration || '',
    callsign: input.callsign || input.flight || '',
    hex: input.hex || '',
    manufacturer: input.manufacturer || input.make || '',
    model: input.model || '',
    type: input.type || input.aircraft_type || '',
    operator: input.operator || input.airline || ''
  };
}

// /AIRCRAFT_PHOTO_NATIVE_RENDER_FALLBACK_PATCH_V1

function setAircraftPhotoCandidates(urls, description, fallbackTerms = null) {
  const image = el('aircraftPhoto');
  const message = el('aircraftPhotoMessage');
  const actions = el('aircraftPhotoActions');
  const candidates = Array.from(new Set(
    (Array.isArray(urls) ? urls : [])
      .map(value => String(value || '').trim())
      .filter(value => /^https?:\/\//i.test(value))
  ));

  actions.replaceChildren();
  clearNativeAircraftFallbackCredit();
  image.style.display = 'none';
  image.removeAttribute('src');
  image.referrerPolicy = 'no-referrer';
  image.decoding = 'async';
  message.classList.remove('aircraft-photo-loading');
  message.style.display = 'block';
  if (fallbackTerms || candidates.length) {
    setAircraftPhotoLoading(message, 'Looking up aircraft photo…');
  } else {
    setAircraftPhotoFinalMessage(message, description || 'No photograph available for this aircraft.');
  }

  if (!candidates.length) {
    if (fallbackTerms) {
      setAircraftPhotoLoading(message, 'Looking up aircraft photo…');
      loadNativeAircraftFallbackPhoto(fallbackTerms).then(found => {
        if (!found) setAircraftPhotoFinalMessage(message, description || 'No photograph available for this aircraft.');
      });
    } else {
      setAircraftPhotoFinalMessage(message, description || 'No photograph available for this aircraft.');
    }
    return;
  }

  let index = 0;
  const tryNext = () => {
    if (index >= candidates.length) {
      if (fallbackTerms) {
        setAircraftPhotoLoading(message, 'Looking up fallback aircraft photo…');
        loadNativeAircraftFallbackPhoto(fallbackTerms).then(found => {
          if (!found) {
            setAircraftPhotoFinalMessage(message, 'Embedded photograph could not be loaded. Open the available photo source below.');
            actions.appendChild(createDetailLink('Open Aircraft Photograph', candidates[0]));
          }
        });
        return;
      }
      setAircraftPhotoFinalMessage(message, 'Embedded photograph could not be loaded. Open the available photo source below.');
      actions.appendChild(createDetailLink('Open Aircraft Photograph', candidates[0]));
      return;
    }
    const candidate = candidates[index++];
    setAircraftPhotoLoading(message, 'Loading aircraft photograph…');
    image.onload = () => {
      message.classList.remove('aircraft-photo-loading');
      message.style.display = 'none';
      image.style.display = 'block';
      actions.replaceChildren(createDetailLink('Open Full Photograph', candidates[candidates.length - 1]));
    };
    image.onerror = tryNext;
    image.src = candidate;
  };
  tryNext();
}


// LOCAL_TAR1090_AIRCRAFT_CACHE_UI_FALLBACK_SAFE_V2
async function fetchLocalAircraftCacheEnrichment(identifier) {
  const hex = String(identifier || '').replace(/^~/, '').trim().toUpperCase();
  if (!/^[0-9A-F]{6}$/.test(hex)) return null;

  try {
    const response = await jsonRequest(`/api/aircraft/local?hex=${encodeURIComponent(hex)}`);
    if (!response || !response.found || !response.aircraft) return null;

    return {
      aircraft: response.aircraft,
      flightroute: null,
      source: response.source || 'local tar1090-db aircraft cache'
    };
  } catch (_error) {
    return null;
  }
}

// /LOCAL_TAR1090_AIRCRAFT_CACHE_UI_FALLBACK_SAFE_V2

async function fetchAircraftEnrichment(identifier, callsign = '') {
  if (!identifier) return null;

  let adsbdbPayload = null;
  try {
    let url = `https://api.adsbdb.com/v0/aircraft/${encodeURIComponent(identifier)}`;
    if (callsign) url += `?callsign=${encodeURIComponent(callsign)}`;
    const response = await fetch(url, {cache: 'no-store'});
    if (response.ok) {
      const result = await response.json();
      adsbdbPayload = result.response && typeof result.response === 'object' ? result.response : null;
    }
  } catch (_error) {
    adsbdbPayload = null;
  }

  if (adsbdbPayload && adsbdbPayload.aircraft) return adsbdbPayload;

  const localPayload = await fetchLocalAircraftCacheEnrichment(identifier);
  if (localPayload && localPayload.aircraft) return localPayload;

  return adsbdbPayload;
}
async function applyAirlabsRouteToDetails(flight) {
  const callsign = String(flight || '').trim().toUpperCase();
  if (!callsign) {
    detailValue('detailRouteSource', 'No callsign broadcast');
    return;
  }
  try {
    const result = await requestAirlabsDiagnosticRoute(callsign);
    if (!result.configured) {
      detailValue('detailRouteSource', 'AirLabs not configured');
      return;
    }
    if (!result.matched) {
      detailValue('detailRouteSource', routeNoMatchSourceMessage(flight));
      return;
    }
    detailValue('detailOrigin', formatAirlabsAirport(result.departure_iata, result.departure_icao));
    detailValue('detailDestination', formatAirlabsAirport(result.arrival_iata, result.arrival_icao));
    detailValue('detailRouteSource', result.cache_hit ? 'AirLabs Flight Information API (cached)' : 'AirLabs Flight Information API');
    if (result.flight_iata) detailValue('detailFlight', `${callsign} / ${result.flight_iata}`);
  } catch (error) {
    detailValue('detailRouteSource', `AirLabs unavailable: ${error.message}`);
  }
}


async function showAircraftDetails(aircraft) {
  const flight = aircraft.flight ? String(aircraft.flight).trim().toUpperCase() : '';
  const rawHex = String(aircraft.hex || '').replace(/^~/, '').toUpperCase();
  const broadcastTail = likelyTailNumber(flight) ? flight : '';
  const title = flight || rawHex || 'Aircraft Details';

  el('aircraftDetailTitle').textContent = title;
  el('aircraftDetailOverlay').classList.add('open');
  detailValue('detailFlight', flight || 'Not broadcast');
  detailValue('detailHex', rawHex || 'Not broadcast');
  detailValue('detailRouteSource', 'Checking AirLabs…');
  detailValue('detailRegistration', broadcastTail || 'Loading…');
  detailValue('detailManufacturer', 'Loading…');
  detailValue('detailModel', 'Loading…');
  detailValue('detailOperator', 'Loading…');
  detailValue('detailOrigin', flight && !broadcastTail ? 'Loading…' : 'Route unavailable');
  detailValue('detailDestination', flight && !broadcastTail ? 'Loading…' : 'Route unavailable');
  detailValue('detailAltitude', aircraft.alt_baro == null ? '—' : `${aircraft.alt_baro} ft`);
  const speed = aircraft.gs == null ? '—' : `${aircraft.gs} kt`;
  const track = aircraft.track == null ? '—' : `${aircraft.track}°`;
  detailValue('detailMovement', `${speed} / ${track}`);
  updateTailNumberActions(broadcastTail);
  setAircraftPhotoCandidates([], 'Loading available aircraft photograph…');
  setMessage('aircraftDetailStatus', 'Loading aircraft and route lookup information…', 'warning');

  if (!rawHex && !broadcastTail) {
    setMessage('aircraftDetailStatus', 'No ICAO hex or tail number is available for aircraft lookup.', 'warning');
    detailValue('detailRegistration', 'Unavailable');
    detailValue('detailManufacturer', 'Unavailable');
    detailValue('detailModel', 'Unavailable');
    detailValue('detailOperator', 'Unavailable');
    setAircraftPhotoCandidates([], 'No aircraft photograph available.');
    return;
  }

  try {
    let payload = await fetchAircraftEnrichment(rawHex || broadcastTail, flight && !broadcastTail ? flight : '');
    let enrichedAircraft = payload ? payload.aircraft || null : null;
    let route = payload ? payload.flightroute || null : null;

    // For private/general aviation, readsb often broadcasts the tail number as
    // flight. Use it as a second public aircraft-record lookup when Mode-S
    // did not yield an aircraft match.
    if (!enrichedAircraft && broadcastTail && broadcastTail !== rawHex) {
      payload = await fetchAircraftEnrichment(broadcastTail, '');
      enrichedAircraft = payload ? payload.aircraft || null : null;
      route = route || (payload ? payload.flightroute || null : null);
    }

    if (enrichedAircraft) {
      const registration = enrichedAircraft.registration || broadcastTail || '';
      detailValue('detailRegistration', registration || 'Unavailable');
      detailValue('detailManufacturer', enrichedAircraft.manufacturer || 'Unavailable');
      detailValue('detailModel', enrichedAircraft.type || enrichedAircraft.icao_type || 'Unavailable');
      detailValue('detailOperator', enrichedAircraft.registered_owner || 'Unavailable');
      updateTailNumberActions(registration);
      setAircraftPhotoCandidates(
        [enrichedAircraft.url_photo_thumbnail, enrichedAircraft.url_photo],
        'No photograph available for this aircraft.',
        makeAircraftPhotoFallbackTerms({
          reg: registration || broadcastTail,
          callsign: flight,
          hex: rawHex,
          manufacturer: enrichedAircraft.manufacturer,
          model: enrichedAircraft.type || enrichedAircraft.icao_type,
          type: enrichedAircraft.type || enrichedAircraft.icao_type,
          operator: enrichedAircraft.registered_owner
        })
      );
    } else {
      detailValue('detailRegistration', broadcastTail || 'Unavailable');
      detailValue('detailManufacturer', 'Unavailable');
      detailValue('detailModel', 'Unavailable');
      detailValue('detailOperator', 'Unavailable');
      updateTailNumberActions(broadcastTail);
      setAircraftPhotoCandidates(
        [],
        'No photograph available for this aircraft.',
        makeAircraftPhotoFallbackTerms({
          reg: broadcastTail,
          callsign: flight,
          hex: rawHex
        })
      );
    }

    if (route) {
      detailValue('detailFlight', route.callsign || flight || 'Unavailable');
      detailValue('detailOrigin', formatAirport(route.origin));
      detailValue('detailDestination', formatAirport(route.destination));
    } else {
      detailValue('detailOrigin', 'Route unavailable');
      detailValue('detailDestination', 'Route unavailable');
    }

    if (enrichedAircraft || route) {
      setMessage('aircraftDetailStatus', 'Aircraft lookup complete. Tail-number actions are available below the photo.', 'good');
    } else {
      setMessage('aircraftDetailStatus', 'No public aircraft or route match was found for this target.', 'warning');
    }
  } catch (error) {
    detailValue('detailRegistration', broadcastTail || 'Lookup unavailable');
    detailValue('detailManufacturer', 'Lookup unavailable');
    detailValue('detailModel', 'Lookup unavailable');
    detailValue('detailOperator', 'Lookup unavailable');
    detailValue('detailOrigin', 'Lookup unavailable');
    detailValue('detailDestination', 'Lookup unavailable');
    updateTailNumberActions(broadcastTail);
    setAircraftPhotoCandidates([], 'Photograph lookup unavailable.');
    setMessage('aircraftDetailStatus', `Public detail lookup unavailable: ${error.message}`, 'error');
  }
  await applyAirlabsRouteToDetails(flight);
}

async function updateAircraft() {
  try {
    const data = await jsonRequest('/api/aircraft.json');
    const body = el('aircraftRows');
    const aircraft = Array.isArray(data.aircraft) ? data.aircraft : [];
    updateAircraftMap(aircraft);
    const visibleAircraft = aircraft.slice(0, 20);
    body.replaceChildren();
    if (!visibleAircraft.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty">No current aircraft records.</td></tr>';
      return;
    }
    for (const item of visibleAircraft) {
      const row = document.createElement('tr');
      row.className = 'active-plane-row';
      row.title = 'Click for aircraft and flight details';
      row.addEventListener('click', () => showAircraftDetails(item));
      const flight = item.flight ? item.flight.trim() : '';
      const values = [flight || String(item.hex || '').toUpperCase(), item.alt_baro, item.gs, item.seen];
      for (const value of values) {
        const cell = document.createElement('td');
        cell.textContent = value == null ? '' : value;
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
  } catch (error) {
    el('aircraftRows').innerHTML = `<tr><td colspan="8" class="empty">Aircraft data failed: ${error.message}</td></tr>`;
  }
}

function isAirlabsConfiguredStatus(status) {
  return !!(status && (status.configured || status.enabled));
}

function airlabsKeyHint(status) {
  if (!status) return 'configured';
  return status.key_hint || status.masked_key || 'configured';
}

function airlabsCacheCount(status) {
  if (!status) return 0;
  return status.route_cache_entries || status.cache_count || 0;
}

function normalizeAirlabsRoutePayload(payload, status) {
  const route = payload && payload.route ? payload.route : payload;
  const configured = isAirlabsConfiguredStatus(status);
  if (!configured) {
    return {configured: false, matched: false, message: 'AirLabs not configured'};
  }
  if (!route) {
    return {configured: true, matched: false, message: 'AirLabs route lookup returned no payload.'};
  }
  const matched = !!(route.found || route.matched);
  return {
    configured: true,
    matched,
    cache_hit: !!(payload && payload.cached) || !!route.cached || !!route.cache_hit,
    message: route.message || (matched ? '' : 'No AirLabs route match'),
    departure_iata: route.departure_iata || route.dep_iata || route.from || '',
    departure_icao: route.departure_icao || route.dep_icao || '',
    arrival_iata: route.arrival_iata || route.arr_iata || route.to || '',
    arrival_icao: route.arrival_icao || route.arr_icao || '',
    flight_iata: route.flight_iata || '',
    flight_icao: route.flight_icao || (payload && payload.flight_icao) || ''
  };
}

async function loadAirlabsSettings() {
  try {
    const status = await jsonRequest('/api/diagnostics/airlabs/status');
    el('airlabsApiKey').value = '';
    setMessage(
      'airlabsMessage',
      isAirlabsConfiguredStatus(status)
        ? `AirLabs route lookup configured (${airlabsKeyHint(status)}). Cached successful routes: ${airlabsCacheCount(status)}; expires after ${Math.round((status.cache_ttl_seconds || 7200) / 3600)} hours.`
        : 'AirLabs is not configured. Paste an API key and save it here.',
      isAirlabsConfiguredStatus(status) ? 'good' : 'warning'
    );
  } catch (error) {
    setMessage('airlabsMessage', `Unable to load AirLabs status: ${error.message}`, 'error');
  }
}
async function saveAirlabsKey() {
  const apiKey = el('airlabsApiKey').value.trim();
  if (!apiKey) {
    setMessage('airlabsMessage', 'Paste the AirLabs API key before saving.', 'warning');
    return;
  }
  try {
    const result = await jsonRequest('/api/diagnostics/airlabs/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({api_key: apiKey})
    });
    const savedStatus = result.status || result;
    if (!isAirlabsConfiguredStatus(savedStatus)) throw new Error('Pi could not read back the saved AirLabs key.');
    el('airlabsApiKey').value = '';
    setMessage('airlabsMessage', `AirLabs key saved on the Pi (${airlabsKeyHint(savedStatus)}).`, 'good');
  } catch (error) {
    setMessage('airlabsMessage', `AirLabs key save failed: ${error.message}`, 'error');
  }
}
async function clearAirlabsKey() {
  try {
    await jsonRequest('/api/diagnostics/airlabs/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'clear'})
    });
    el('airlabsApiKey').value = '';
    setMessage('airlabsMessage', 'AirLabs key removed from the Pi.', 'good');
  } catch (error) {
    setMessage('airlabsMessage', `AirLabs key clear failed: ${error.message}`, 'error');
  }
}
function formatAirlabsAirport(iata, icao) {
  if (iata && icao) return `${iata} (${icao})`;
  return iata || icao || 'Unavailable';
}
async function requestAirlabsDiagnosticRoute(flight) {
  const status = await jsonRequest('/api/diagnostics/airlabs/status');
  if (!isAirlabsConfiguredStatus(status)) {
    return normalizeAirlabsRoutePayload(null, status);
  }
  const payload = await fetchAirLabsRouteWithVariants(flight || '');
  return normalizeAirlabsRoutePayload(payload, status);
}
async function clearAirlabsRouteCache() {
  try {
    await jsonRequest('/api/diagnostics/airlabs/cache/clear', {method: 'POST'});
    setMessage('airlabsMessage', 'AirLabs successful-route cache cleared. The next popup lookup will query AirLabs again.', 'good');
  } catch (error) {
    setMessage('airlabsMessage', `AirLabs cache clear failed: ${error.message}`, 'error');
  }
}

async function testAirlabsKey() {
  const flight = el('airlabsTestFlight').value.trim().toUpperCase();
  if (!flight) {
    setMessage('airlabsMessage', 'Enter an active airline callsign such as UAL1234.', 'warning');
    return;
  }
  try {
    setMessage('airlabsMessage', `Testing AirLabs route lookup for ${flight}…`, 'warning');
    const result = await requestAirlabsDiagnosticRoute(flight);
    if (result.matched) {
      setMessage(
        'airlabsMessage',
        `${flight}: ${formatAirlabsAirport(result.departure_iata, result.departure_icao)} → ${formatAirlabsAirport(result.arrival_iata, result.arrival_icao)}.${result.cache_hit ? ' Cached result.' : ' Fresh AirLabs result cached for reuse.'}`,
        'good'
      );
    } else {
      setMessage('airlabsMessage', result.message || `No AirLabs route matched ${flight}.`, 'warning');
    }
  } catch (error) {
    setMessage('airlabsMessage', `Route lookup test failed: ${error.message}`, 'error');
  }
}

async function saveAirbandRadius() {
  const radiusMiles = Number(el('locationRadius').value);
  if (!Number.isFinite(radiusMiles) || radiusMiles <= 0 || radiusMiles > 500) {
    setMessage('airbandRadiusMessage', 'Enter an Airband radius greater than 0 and no more than 500 miles.', 'error');
    return;
  }

  const originalAirband = await readAirbandStatus();
  const restartScan = Boolean(originalAirband.airband_scan_running) && airbandBackgroundWanted && !liveListening;

  try {
    if (restartScan) {
      await showBusyAndPaint(
        'Applying Airband scan radius…',
        `Stopping background scan before rebuilding channels within ${radiusMiles.toFixed(1)} miles.`
      );
      airbandRestartSuspended = true;
      const stopped = await stopAirbandBackground(false, false);
      if (!stopped) throw new Error('Airband scanner did not release the receiver.');
    }

    const result = await jsonRequest('/api/settings/airband-radius', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({airband_radius_miles: radiusMiles})
    });
    renderLocation(result.receiver_location);
    setMessage(
      'airbandRadiusMessage',
      `Airband scan radius set to ${Number(result.receiver_location.airband_radius_miles).toFixed(1)} miles. Saved NOAA selection preserved.`,
      'good'
    );

    if (restartScan) {
      airbandRestartSuspended = false;
      await startAirbandBackground(false);
      setMessage(
        'airbandRadiusMessage',
        `Airband scan radius set to ${Number(result.receiver_location.airband_radius_miles).toFixed(1)} miles; background scan restarted with nearby channels only.`,
        'good'
      );
    }
  } catch (error) {
    setMessage('airbandRadiusMessage', `Airband radius update failed: ${error.message}`, 'error');
    if (restartScan && airbandBackgroundWanted && !liveListening) {
      airbandRestartSuspended = false;
      await startAirbandBackground(false);
    }
  } finally {
    airbandRestartSuspended = false;
    hideBusy();
    await refreshOperationMenu();
  }
}

async function saveLocation() {
  try {
    const payload = {
      name: el('locationName').value,
      latitude: el('locationLatitude').value,
      longitude: el('locationLongitude').value,
      airband_radius_miles: el('locationRadius').value
    };
    const result = await jsonRequest('/api/settings/receiver', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    delete el('locationName').dataset.edited;
    renderLocation(result.receiver_location);
  } catch (error) {
    setMessage('locationMessage', `Location save failed: ${error.message}`, 'error');
  }
}

async function captureNoaa() {
  setMessage('audioMessage', 'Capturing 10 seconds of NOAA audio while ADS-B continues…', 'warning');
  try {
    const response = await fetch(`/api/noaa/capture.wav?seconds=10&request=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    if (audioObjectUrl) URL.revokeObjectURL(audioObjectUrl);
    audioObjectUrl = URL.createObjectURL(blob);
    el('audioPlayer').src = audioObjectUrl;
    el('audioPlayer').load();
    setMessage('audioMessage', 'NOAA capture complete. Press Play to listen.', 'good');
  } catch (error) {
    setMessage('audioMessage', `NOAA capture failed: ${error.message}`, 'error');
  }
}

async function pumpLiveAudio() {
  if (!liveListening || !liveAudioContext) return;
  try {
    const response = await fetch(`/api/noaa/live/audio.wav?from=${liveNextCursor}&samples=12000&request=${Date.now()}`, {cache: 'no-store'});
    if (response.status === 204) {
      livePumpTimer = window.setTimeout(pumpLiveAudio, 120);
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const sourceSamples = Number(response.headers.get('X-Source-Samples') || 0);
    const data = await response.arrayBuffer();
    const decoded = await liveAudioContext.decodeAudioData(data.slice(0));
    const source = liveAudioContext.createBufferSource();
    source.buffer = decoded;
    source.connect(liveAudioContext.destination);
    const startAt = Math.max(liveAudioContext.currentTime + 0.04, liveNextPlayTime);
    source.start(startAt);
    liveNextPlayTime = startAt + decoded.duration;
    liveNextCursor += sourceSamples;
    const bufferedSeconds = liveNextPlayTime - liveAudioContext.currentTime;
    livePumpTimer = window.setTimeout(pumpLiveAudio, bufferedSeconds > 1.4 ? 250 : 20);
  } catch (error) {
    setMessage('audioMessage', `Live audio failed: ${error.message}`, 'error');
    await stopLive();
  }
}

async function rescanSavedNoaaChannel() {
  if (operationTransitionActive) return;
  operationTransitionActive = true;
  setOperationButtonsDisabled(true);
  airbandRestartSuspended = true;
  closeMenu();

  try {
    const status = await jsonRequest('/api/status');
    const airband = await readAirbandStatus();

    await showBusyAndPaint(
      'Rescanning NOAA Weather channels…',
      'Clearing the saved local channel and surveying all NOAA frequencies.'
    );

    if (status.live_audio_running || liveListening) {
      await stopLive();
    }
    if (airband.airband_scan_running) {
      const released = await stopAirbandBackground(false, false);
      if (!released) throw new Error('Airband did not release the shared receiver.');
    }

    await autoNoaa(true);
    await hideBusyAfterMinimum(550);
    setMessage('locationMessage', 'NOAA local channel rescanned and saved for the current receiver location.', 'good');
  } catch (error) {
    hideBusy();
    airbandRestartSuspended = false;
    setMessage('locationMessage', `NOAA rescan failed: ${error.message}`, 'error');
    if (airbandBackgroundWanted && !liveListening) await startAirbandBackground(true);
  } finally {
    operationTransitionActive = false;
    setOperationButtonsDisabled(false);
    await refreshOperationMenu();
  }
}

async function startLive() {
  try {
    await jsonRequest('/api/noaa/live/start', {method: 'POST'});
    liveAudioContext = new (window.AudioContext || window.webkitAudioContext)();
    await liveAudioContext.resume();
    liveListening = true;
    liveNextCursor = 0;
    liveNextPlayTime = liveAudioContext.currentTime + 0.20;
    setMessage('audioMessage', 'Live NOAA listening active. ADS-B continues.', 'good');
    await updateStatus();
    pumpLiveAudio();
  } catch (error) {
    setMessage('audioMessage', `Unable to start NOAA: ${error.message}`, 'error');
  }
}

async function stopLive() {
  liveListening = false;
  if (livePumpTimer) window.clearTimeout(livePumpTimer);
  try { await jsonRequest('/api/noaa/live/stop', {method: 'POST'}); } catch (_) {}
  if (liveAudioContext) {
    await liveAudioContext.close();
    liveAudioContext = null;
  }
  setMessage('audioMessage', 'NOAA listening stopped.', '');
  await updateStatus();
}

async function autoNoaa(forceRescan = false) {
  const noaaEndpoint = forceRescan ? '/api/noaa/auto/rescan' : '/api/noaa/auto/start';
  setMessage(
    'surveyResult',
    forceRescan ? 'Rescanning all seven NOAA channels…' : 'Starting saved local NOAA channel, or surveying if none is saved…',
    'warning'
  );

  try {
    const status = await jsonRequest(noaaEndpoint, {method: 'POST'});
    liveAudioContext = new (window.AudioContext || window.webkitAudioContext)();
    await liveAudioContext.resume();
    liveListening = true;
    liveNextCursor = 0;
    liveNextPlayTime = liveAudioContext.currentTime + 0.20;
    const selected = (Number(status.noaa_frequency_hz) / 1000000).toFixed(3);
    setMessage('surveyResult', `Selected ${selected} MHz and started live listening.`, 'good');
    setMessage('audioMessage', `NOAA listening active on ${selected} MHz.`, 'good');
    pumpLiveAudio();
    await updateStatus();
    return status;
  } catch (error) {
    setMessage('surveyResult', `NOAA start failed: ${error.message}`, 'error');
    throw error;
  }
}

async function loadAirbandChannels() {
  try {
    const result = await jsonRequest('/api/airband/channels');
    const rows = el('airbandRows');
    rows.replaceChildren();
    setMessage('airbandListMessage',
      `Loaded ${result.channel_count} unique AM frequencies within ${result.radius_miles} miles; ${result.duplicate_records_removed || 0} duplicate records removed.`,
      'good');
    if (!result.channels.length) {
      rows.innerHTML = '<tr><td colspan="5" class="empty">No channels found within configured radius.</td></tr>';
      return;
    }
    for (const channel of result.channels.slice(0, 40)) {
      const row = document.createElement('tr');
      const values = [
        Number(channel.frequency_mhz).toFixed(3),
        channel.airport_id || channel.airport_name || '',
        channel.use || '',
        channel.distance_miles
      ];
      for (const value of values) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      }
      const action = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Test 10 sec';
      button.addEventListener('click', () => testAirbandCapture(channel));
      action.appendChild(button);
      row.appendChild(action);
      rows.appendChild(row);
    }
  } catch (error) {
    setMessage('airbandListMessage', `Airband list failed: ${error.message}`, 'error');
  }
}

async function testAirbandCapture(channel) {
  setMessage('airbandAudioMessage', `Capturing ${Number(channel.frequency_mhz).toFixed(3)} MHz AM diagnostic audio…`, 'warning');
  try {
    const response = await fetch(`/api/airband/capture.wav?frequency_hz=${channel.frequency_hz}&seconds=10&request=${Date.now()}`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    el('airbandPlayer').src = url;
    el('airbandPlayer').load();
    setMessage('airbandAudioMessage', 'AM diagnostic capture complete. Live RF detection remains unvalidated.', 'good');
  } catch (error) {
    setMessage('airbandAudioMessage', `AM diagnostic failed: ${error.message}`, 'error');
  }
}

async function startExperimentalScan() {
  try {
    const scope = el('airbandScanScope').value;
    const result = await jsonRequest(`/api/airband/scan/activity/start?scope=${encodeURIComponent(scope)}`, {method: 'POST'});
    el('activityScanStart').disabled = true;
    el('activityScanStop').disabled = false;
    setMessage('airbandScanStatus',
      `Experimental scan started: ${result.channel_count} channels (${result.scan_scope || scope}). RF validation deferred.`,
      'warning');
    pollExperimentalScan();
  } catch (error) {
    setMessage('airbandScanStatus', `Experimental scan failed: ${error.message}`, 'error');
  }
}

async function stopExperimentalScan() {
  try { await jsonRequest('/api/airband/scan/activity/stop', {method: 'POST'}); } catch (_) {}
  el('activityScanStart').disabled = false;
  el('activityScanStop').disabled = true;
  setMessage('airbandScanStatus', 'Experimental RF scan stopped.', 'warning');
}

async function pollExperimentalScan() {
  try {
    const status = await jsonRequest('/api/airband/scan/status');
    if (!status.airband_scan_running) {
      el('activityScanStart').disabled = false;
      el('activityScanStop').disabled = true;
      if (airbandBackgroundWanted && !liveListening) window.setTimeout(startAirbandBackground, 1500);
      refreshOperationMenu();
      return;
    }
    const channel = status.airband_current_channel;
    setMessage('airbandScanStatus',
      `Experimental scan (${status.airband_scan_scope || 'priority'}): samples ${status.airband_channels_scanned || 0}` +
      (channel ? `; ${Number(channel.frequency_mhz).toFixed(3)} MHz` : '') +
      `; carrier SNR ${status.airband_last_signal_snr_db == null ? '—' : status.airband_last_signal_snr_db} dB.`,
      'warning');
    window.setTimeout(pollExperimentalScan, 1000);
  } catch (error) {
    setMessage('airbandScanStatus', `Experimental scan status failed: ${error.message}`, 'error');
  }
}

async function startAirbandTest() {
  try {
    await jsonRequest('/api/airband/test/start', {method: 'POST'});
    airbandTestPlayedEventId = 0;
    setMessage('airbandTestStatus', 'SIMULATED: Test scanner starting.', 'warning');
    pollAirbandTest();
  } catch (error) {
    setMessage('airbandTestStatus', `SIMULATED test failed: ${error.message}`, 'error');
  }
}

async function airbandTestCommand(command) {
  try {
    await jsonRequest(`/api/airband/test/${command}`, {method: 'POST'});
    pollAirbandTest();
  } catch (error) {
    setMessage('airbandTestStatus', `SIMULATED command failed: ${error.message}`, 'error');
  }
}

async function pollAirbandTest() {
  try {
    const status = await jsonRequest('/api/airband/test/status');
    const running = Boolean(status.airband_test_running);
    const state = status.airband_test_state || 'idle';
    el('airbandTestStart').disabled = running;
    el('airbandTestStop').disabled = !running;
    el('airbandTestHold').disabled = !running || state === 'held';
    el('airbandTestSkip').disabled = !running;
    el('airbandTestResume').disabled = !running || state !== 'held';

    let message = status.airband_test_message || 'SIMULATED: Test scanner idle.';
    if (status.airband_test_silence_remaining != null) {
      message += ` Silence remaining ${status.airband_test_silence_remaining} seconds.`;
    }
    setMessage('airbandTestStatus', message,
      (state === 'listening_simulated_activity' || state === 'held') ? 'good' : 'warning');

    if (state === 'listening_simulated_activity' && status.airband_test_event_id !== airbandTestPlayedEventId) {
      airbandTestPlayedEventId = status.airband_test_event_id;
      el('airbandTestPlayer').src = `/api/airband/test/audio.wav?event=${airbandTestPlayedEventId}`;
      el('airbandTestPlayer').load();
      try { await el('airbandTestPlayer').play(); } catch (_) {}
    }
    if (running) window.setTimeout(pollAirbandTest, 300);
  } catch (error) {
    setMessage('airbandTestStatus', `SIMULATED status failed: ${error.message}`, 'error');
  }
}

let airbandBackgroundWanted = true;
let airbandPausedForNoaa = false;
let airbandRestartSuspended = false;
let operationTransitionActive = false;
let operationsRefreshTimer = null;
let busyStartedAt = 0;
let busyElapsedTimer = null;

function showBusy(title, detail = '') {
  const overlay = el('busyOverlay');
  if (!overlay) return;
  el('busyTitle').textContent = title;
  el('busyDetail').textContent = detail || 'Preparing the shared audio receiver.';
  busyStartedAt = Date.now();
  if (el('busyElapsed')) el('busyElapsed').textContent = 'Elapsed: 0 seconds';
  overlay.classList.add('open');
  if (busyElapsedTimer) window.clearInterval(busyElapsedTimer);
  busyElapsedTimer = window.setInterval(() => {
    if (el('busyElapsed')) {
      const seconds = Math.floor((Date.now() - busyStartedAt) / 1000);
      el('busyElapsed').textContent = `Elapsed: ${seconds} second${seconds === 1 ? '' : 's'}`;
    }
  }, 250);
}
function updateBusy(title, detail = '') {
  if (el('busyTitle')) el('busyTitle').textContent = title;
  if (el('busyDetail')) el('busyDetail').textContent = detail;
}
function nextPaintFrame() {
  return new Promise(resolve => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
  });
}
async function showBusyAndPaint(title, detail = '') {
  showBusy(title, detail);
  await nextPaintFrame();
}
async function hideBusyAfterMinimum(milliseconds = 500) {
  const remaining = milliseconds - (Date.now() - busyStartedAt);
  if (remaining > 0) await new Promise(resolve => window.setTimeout(resolve, remaining));
  hideBusy();
}
function hideBusy() {
  if (busyElapsedTimer) {
    window.clearInterval(busyElapsedTimer);
    busyElapsedTimer = null;
  }
  const overlay = el('busyOverlay');
  if (overlay) overlay.classList.remove('open');
}
function setOperationButtonsDisabled(disabled) {
  el('noaaMenuToggle').disabled = disabled;
  el('airbandMenuToggle').disabled = disabled;
}
function delay(milliseconds) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}
function openMenu() {
  el('appMenu').classList.add('open');
  el('menuBackdrop').classList.add('open');
  el('menuToggle').setAttribute('aria-expanded', 'true');
}
function closeMenu() {
  el('appMenu').classList.remove('open');
  el('menuBackdrop').classList.remove('open');
  el('menuToggle').setAttribute('aria-expanded', 'false');
}
function toggleMenu() {
  if (el('appMenu').classList.contains('open')) closeMenu(); else openMenu();
}
async function readAirbandStatus() {
  try {
    return await jsonRequest('/api/airband/scan/status');
  } catch (_) {
    return {};
  }
}
async function waitForAirbandStopped(timeoutMilliseconds = 15000) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const status = await readAirbandStatus();
    if (!status.airband_scan_running) return true;
    updateBusy(
      'Stopping Airband background scan…',
      'Waiting for RTL-SDR audio receiver 00000162 to be released for NOAA Weather.'
    );
    await delay(250);
  }
  return false;
}
async function waitForNoaaRunning(timeoutMilliseconds = 12000) {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    const status = await jsonRequest('/api/status');
    if (status.live_audio_running) return status;
    updateBusy(
      'Starting NOAA Weather audio…',
      'The local NOAA channel has been selected. Waiting for live audio buffering to begin.'
    );
    await delay(250);
  }
  return null;
}

function updateAirbandTuningDetail(airband, noaaActive) {
  const target = el('airbandTuningDetail');
  if (!target) return;

  target.className = '';
  if (noaaActive) {
    target.textContent = 'Airband Tuning: paused while NOAA is active';
    target.className = 'tuning-paused';
    return;
  }

  if (!airband || !airband.airband_scan_running) {
    target.textContent = 'Airband Tuning: stopped';
    return;
  }

  const channel = airband.airband_current_channel;
  if (channel && Number.isFinite(Number(channel.frequency_mhz))) {
    const description = channel.use ? ` · ${channel.use}` : '';
    target.textContent = `Airband Tuning: ${Number(channel.frequency_mhz).toFixed(3)} MHz AM${description}`;
    target.className = 'tuning-active';
  } else {
    target.textContent = 'Airband Tuning: scanning…';
    target.className = 'tuning-active';
  }
}

async function refreshOperationMenu() {
  try {
    const status = await jsonRequest('/api/status');
    const airband = await readAirbandStatus();
    const noaaActive = Boolean(status.live_audio_running || liveListening);
    const airbandActive = Boolean(airband.airband_scan_running);
    updateAirbandTuningDetail(airband, noaaActive);

    el('noaaMenuToggle').textContent = noaaActive ? 'Stop NOAA Weather' : 'Start NOAA Weather';
    el('noaaMenuToggle').className = noaaActive ? 'stop' : '';
    el('airbandMenuToggle').textContent = airbandActive ? 'Stop Airband Background Scan' : 'Start Airband Background Scan';
    el('airbandMenuToggle').className = airbandActive ? 'stop' : '';

    if (operationTransitionActive) return;
    if (noaaActive) {
      setMessage('operationsMessage', 'NOAA Weather listening active. Background Airband scan is paused while receiver 00000162 is in use.', 'good');
    } else if (airbandActive) {
      setMessage('operationsMessage', 'Airband background scan active. It is silent unless real RF activity is detected. Use Diagnostics > Airband Scanner Test Mode for audible simulated audio.', 'warning');
    } else if (airbandBackgroundWanted) {
      setMessage('operationsMessage', 'Airband background scan is enabled but is not currently running.', 'warning');
    } else {
      setMessage('operationsMessage', 'NOAA and Airband background scanning are stopped.', '');
    }
  } catch (error) {
    setMessage('operationsMessage', `Operation status failed: ${error.message}`, 'error');
  }
}
async function startAirbandBackground(showOverlay = true) {
  if (!airbandBackgroundWanted || airbandRestartSuspended || liveListening) return false;
  if (showOverlay) {
    await showBusyAndPaint(
      'Starting Airband background scan…',
      'Preparing the shared audio receiver for background AM scanning.'
    );
  }
  try {
    const existing = await readAirbandStatus();
    if (existing.airband_scan_running) return true;
    const scope = el('airbandScanScope').value || 'priority';
    const result = await jsonRequest(
      `/api/airband/scan/activity/start?scope=${encodeURIComponent(scope)}`,
      {method: 'POST'}
    );
    setMessage(
      'airbandScanStatus',
      `Background scan active: ${result.channel_count || '—'} frequencies (${result.scan_scope || scope}); real RF detection remains experimental.`,
      'warning'
    );
    pollExperimentalScan();
    return true;
  } catch (error) {
    setMessage('airbandScanStatus', `Background Airband scan could not start: ${error.message}`, 'error');
    return false;
  } finally {
    if (showOverlay) await hideBusyAfterMinimum(550);
    await refreshOperationMenu();
  }
}
async function stopAirbandBackground(changePreference = true, showOverlay = true) {
  if (changePreference) airbandBackgroundWanted = false;
  if (showOverlay) {
    await showBusyAndPaint(
      'Stopping Airband background scan…',
      'Waiting for the shared audio receiver to be released.'
    );
  }
  try {
    await jsonRequest('/api/airband/scan/activity/stop', {method: 'POST'});
  } catch (_) {}
  const released = await waitForAirbandStopped();
  if (showOverlay && !operationTransitionActive) await hideBusyAfterMinimum(550);
  await refreshOperationMenu();
  return released;
}
async function toggleNoaaMenuOperation() {
  if (operationTransitionActive) return;
  operationTransitionActive = true;
  setOperationButtonsDisabled(true);

  try {
    const current = await jsonRequest('/api/status');
    if (current.live_audio_running || liveListening) {
      await showBusyAndPaint('Stopping NOAA Weather…', 'Releasing the shared audio receiver.');
      await stopLive();
      airbandRestartSuspended = false;
      if (airbandPausedForNoaa && airbandBackgroundWanted) {
        airbandPausedForNoaa = false;
        updateBusy('Restarting Airband background scan…', 'Returning receiver 00000162 to background AM scanning.');
        await startAirbandBackground(false);
      }
      await hideBusyAfterMinimum(550);
      return;
    }

    closeMenu();
    airbandRestartSuspended = true;
    const airband = await readAirbandStatus();
    airbandPausedForNoaa = airbandBackgroundWanted || Boolean(airband.airband_scan_running);

    await showBusyAndPaint(
      'Preparing NOAA Weather…',
      'Stopping Airband background scanning before searching NOAA frequencies.'
    );

    if (airband.airband_scan_running) {
      const released = await stopAirbandBackground(false, false);
      if (!released) throw new Error('Airband did not release the shared receiver.');
    }

    updateBusy(
      'Locating local NOAA Weather channel…',
      'Scanning NOAA frequencies and selecting the strongest local channel.'
    );
    await autoNoaa();

    const started = await waitForNoaaRunning();
    if (!started) {
      throw new Error('NOAA live audio did not report running within 12 seconds.');
    }
    updateBusy(
      'NOAA Weather listening active',
      `Tuned to ${(Number(started.noaa_frequency_hz) / 1000000).toFixed(3)} MHz.`
    );
    await hideBusyAfterMinimum(550);
  } catch (error) {
    hideBusy();
    airbandRestartSuspended = false;
    setMessage('operationsMessage', `NOAA operation failed: ${error.message}`, 'error');
    if (airbandBackgroundWanted && !airbandRestartSuspended && !liveListening) {
      await startAirbandBackground(true);
    }
  } finally {
    operationTransitionActive = false;
    setOperationButtonsDisabled(false);
    await refreshOperationMenu();
  }
}
async function toggleAirbandMenuOperation() {
  if (operationTransitionActive) return;
  operationTransitionActive = true;
  setOperationButtonsDisabled(true);

  try {
    const airband = await readAirbandStatus();
    if (airband.airband_scan_running) {
      airbandRestartSuspended = true;
      await stopAirbandBackground(true, true);
      airbandRestartSuspended = false;
      return;
    }

    closeMenu();
    const status = await jsonRequest('/api/status');
    if (status.live_audio_running || liveListening) {
      await showBusyAndPaint('Stopping NOAA Weather…', 'Releasing the shared receiver for Airband scanning.');
      await stopLive();
    }

    airbandBackgroundWanted = true;
    airbandRestartSuspended = false;
    await startAirbandBackground(true);
  } finally {
    hideBusy();
    operationTransitionActive = false;
    setOperationButtonsDisabled(false);
    await refreshOperationMenu();
  }
}
async function startDefaultBackgroundAirband() {
  airbandBackgroundWanted = true;
  airbandRestartSuspended = false;
  const status = await jsonRequest('/api/status');
  if (!status.live_audio_running && !liveListening) {
    await startAirbandBackground(true);
  }
}

function bindControls() {
  el('aircraftDetailClose').addEventListener('click', closeAircraftDetails);
  el('aircraftDetailOverlay').addEventListener('click', event => {
    if (event.target === el('aircraftDetailOverlay')) closeAircraftDetails();
  });
  el('pickLocationOnMap').addEventListener('click', beginReceiverLocationPick);
  el('cancelLocationPick').addEventListener('click', cancelReceiverLocationPick);
  el('menuToggle').addEventListener('click', toggleMenu);
  el('menuBackdrop').addEventListener('click', closeMenu);
  el('noaaMenuToggle').addEventListener('click', toggleNoaaMenuOperation);
  el('airbandMenuToggle').addEventListener('click', toggleAirbandMenuOperation);
  el('startLive').addEventListener('click', startLive);
  el('stopLive').addEventListener('click', stopLive);
  el('capture10').addEventListener('click', captureNoaa);
  el('autoNoaa').addEventListener('click', autoNoaa);
  el('saveLocation').addEventListener('click', saveLocation);
  el('saveAirbandRadius').addEventListener('click', saveAirbandRadius);
  el('saveAirlabsKey').addEventListener('click', saveAirlabsKey);
  el('clearAirlabsKey').addEventListener('click', clearAirlabsKey);
  el('testAirlabsKey').addEventListener('click', testAirlabsKey);
  el('clearAirlabsRouteCache').addEventListener('click', clearAirlabsRouteCache);
  el('rescanNoaaChannel').addEventListener('click', rescanSavedNoaaChannel);
  el('loadAirbandChannels').addEventListener('click', loadAirbandChannels);
  el('activityScanStart').addEventListener('click', startExperimentalScan);
  el('activityScanStop').addEventListener('click', stopExperimentalScan);
  el('airbandTestStart').addEventListener('click', startAirbandTest);
  el('airbandTestStop').addEventListener('click', () => airbandTestCommand('stop'));
  el('airbandTestHold').addEventListener('click', () => airbandTestCommand('hold'));
  el('airbandTestSkip').addEventListener('click', () => airbandTestCommand('skip'));
  el('airbandTestResume').addEventListener('click', () => airbandTestCommand('resume'));
  el('fitAircraftMap').addEventListener('click', fitAircraftMap);
  el('centerReceiverMap').addEventListener('click', centerReceiverMap);
  el('clearAircraftTrails').addEventListener('click', clearAircraftTrails);
  el('erasePiTrails').addEventListener('click', erasePiTrailHistory);
  el('loadPiTrails').addEventListener('click', () => loadPiTrailHistory(true));
  el('trailRetention').addEventListener('change', changeTrailRetention);
  for (const id of ['locationName', 'locationLatitude', 'locationLongitude', 'locationRadius']) {
    el(id).addEventListener('input', () => { el('locationName').dataset.edited = 'true'; });
  }
}

window.addEventListener('storage', event => {
  if (event.key === TRAIL_CLEARED_AT_KEY) {
    aircraftTrailClearedAt = Number(event.newValue || '0');
    loadTrailHistory();
    renderStoredTrails();
    setMessage('mapMessage', 'Stored aircraft trails were cleared in another tracker tab.', 'good');
  }
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && el('aircraftDetailOverlay').classList.contains('open')) {
    closeAircraftDetails();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  initializeAircraftMap();
  loadTrailHistory();
  el('trailRetention').value = String(aircraftTrailRetentionMinutes);
  renderStoredTrails();
  bindControls();
  loadAirlabsSettings();
  loadPiTrailHistory();
  updateStatus();
  updateAircraft();
  window.setInterval(updateStatus, 2000);
  window.setInterval(updateAircraft, 2000);
  window.setInterval(refreshOperationMenu, 2500);
  window.setTimeout(startDefaultBackgroundAirband, 900);
});


// Extracted from index.html <script> block 3
(function () {
  "use strict";

  const PATCH_ID = "airband-normal-scanner-ui";
  const STATUS_URL = "/api/airband/scan/status";
  const SETTINGS_URL = "/api/airband/scan/settings";
  const START_URL = "/api/airband/scan/activity/start?scope=all";
  const STOP_URL = "/api/airband/scan/activity/stop";
  const NOAA_LIVE_STOP_URL = "/api/noaa/live/stop";
  const SKIP_URL = "/api/airband/scan/activity/skip";
  const BLOCK_URL = "/api/airband/scan/activity/block";
  const SQUELCH_UP_URL = "/api/airband/scan/squelch/up";
  const SQUELCH_DOWN_URL = "/api/airband/scan/squelch/down";
  const LIVE_AUDIO_URL = "/api/airband/scan/live/audio.wav";

  let audioContext = null;
  let audioCursorSamples = 0;
  let audioNextStartTime = 0;
  let audioLoopRunning = false;
  let audioEnabled = false;
  let lastStatus = null;
  let lastAirbandLockKey = null;
  let pollTimer = null;

  function byId(id) {
    return document.getElementById(id);
  }

  function fmt(value, digits) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "—";
    }
    return Number(value).toFixed(digits);
  }

  function channelLabel(channel) {
    if (!channel) {
      return "—";
    }
    const mhz = channel.frequency_mhz || (channel.frequency_hz ? channel.frequency_hz / 1000000 : null);
    const use = channel.use || "";
    const airport = channel.airport_id || "";
    const miles = channel.distance_miles !== undefined ? `${fmt(channel.distance_miles, 1)} mi` : "";
    return `${fmt(mhz, 3)} MHz ${airport} ${use} ${miles}`.trim();
  }

  async function postJson(url, body) {
    const options = { method: "POST" };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    if (!response.ok) {
      throw new Error(`${url} failed: HTTP ${response.status}`);
    }
    return response.json();
  }

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`${url} failed: HTTP ${response.status}`);
    }
    return response.json();
  }

  function scannerPanelHtml() {
    return `
      <div id="${PATCH_ID}-panel" class="airband-normal-scanner-panel">
        <h3>Airband Scanner</h3>
        <div class="airband-normal-scanner-grid">
          <div>State: <span id="${PATCH_ID}-state" class="airband-normal-scanner-readout">loading</span></div>
          <div>Audio: <span id="${PATCH_ID}-audio" class="airband-normal-scanner-readout airband-normal-scanner-audio-off">off</span></div>
          <div>Frequency: <span id="${PATCH_ID}-frequency" class="airband-normal-scanner-readout">—</span></div>
          <div>SNR: <span id="${PATCH_ID}-snr" class="airband-normal-scanner-readout">—</span></div>
          <div>Level: <span id="${PATCH_ID}-level" class="airband-normal-scanner-readout">—</span></div>
          <div>Silence: <span id="${PATCH_ID}-silence" class="airband-normal-scanner-readout">—</span></div>
        </div>
        <div class="airband-normal-scanner-actions">
          <button id="${PATCH_ID}-start">Start Scanner</button>
          <button id="${PATCH_ID}-stop">Stop Scanner</button>
          <button id="${PATCH_ID}-skip">Skip Open Frequency</button>
          <button id="${PATCH_ID}-block">Block Frequency</button>
          <button id="${PATCH_ID}-sqdown">Squelch −</button>
          <span id="${PATCH_ID}-squelch-value" class="airband-normal-scanner-readout" data-patch="AIRBAND_RESTORE_SQUELCH_READOUT_PATCH_V1-span">— dBFS</span>
          <button id="${PATCH_ID}-squp">Squelch +</button>
          <button id="${PATCH_ID}-audio-toggle" style="display:none" aria-hidden="true" tabindex="-1">Browser Audio Armed By Start</button>
        </div>
        <div id="${PATCH_ID}-channel" class="airband-normal-scanner-small">Channel: —</div>
        <div id="${PATCH_ID}-message" class="airband-normal-scanner-status">Scanner status loading…</div>
      </div>
    `;
  }

  function settingsPanelHtml() {
    return `
      <div id="${PATCH_ID}-settings" class="airband-normal-scanner-panel">
        <h4>Airband Scanner Tuning</h4>
        <div class="airband-normal-scanner-small">
          Tune the normal scanner lock behavior. Lower SNR or lower squelch makes the scanner more sensitive.
        </div>
        <div class="airband-normal-scanner-actions">
          <label>SNR dB <input id="${PATCH_ID}-snr-input" type="number" step="0.5"></label>
          <label>Squelch dBFS <input id="${PATCH_ID}-squelch-input" type="number" step="1"></label>
          <label>RF Gain dB <input id="${PATCH_ID}-gain-input" type="number" step="0.1"></label>
          <label>Sample ms <input id="${PATCH_ID}-sample-input" type="number" step="50"></label>
          <label>Silence s <input id="${PATCH_ID}-silence-input" type="number" step="0.5"></label>
          <button id="${PATCH_ID}-save-settings">Apply Scanner Tuning</button>
        </div>
        <div id="${PATCH_ID}-settings-message" class="airband-normal-scanner-small">Loading scanner tuning…</div>
      </div>
    `;
  }

  function insertPanels() {
    if (byId(`${PATCH_ID}-panel`)) {
      return;
    }

    const panel = document.createElement("div");
    panel.innerHTML = scannerPanelHtml();

    const map = byId("map") || document.querySelector(".leaflet-container");
    if (map && map.parentNode) {
      map.parentNode.insertBefore(panel.firstElementChild, map.nextSibling);
    } else {
      document.body.insertBefore(panel.firstElementChild, document.body.firstChild);
    }

    const settings = document.createElement("div");
    settings.innerHTML = settingsPanelHtml();

    const configAnchor =
      byId("airband-radius-status") ||
      byId("receiverLocationStatus") ||
      Array.from(document.querySelectorAll("h3,h4,summary,div,p")).find((node) =>
        /Airband Scan Radius|Configuration/i.test(node.textContent || "")
      );

    if (configAnchor && configAnchor.parentNode) {
      configAnchor.parentNode.insertBefore(settings.firstElementChild, configAnchor.nextSibling);
    } else {
      byId(`${PATCH_ID}-panel`).insertAdjacentElement("afterend", settings.firstElementChild);
    }
  }

  function enforceLiveOnlyAirbandControls(status) {
    // AIRBAND_CONTROLS_LIVE_ONLY_ENFORCE_PATCH_V1
    // Skip, Block, and Squelch +/- apply to the currently open live latch only.
    // Keep them disabled when stopped, idle, or spectrum-searching.
    const liveLatchActive = !!(status && status.airband_live_audio_running);
    const current = status ? (status.airband_locked_channel || status.airband_current_channel) : null;

    const skipButton = byId(`${PATCH_ID}-skip`);
    const blockButton = byId(`${PATCH_ID}-block`);
    const squelchDownButton = byId(`${PATCH_ID}-sqdown`);
    const squelchUpButton = byId(`${PATCH_ID}-squp`);

    if (skipButton) {
      skipButton.disabled = !liveLatchActive;
    }
    if (blockButton) {
      blockButton.disabled = !liveLatchActive || !current;
    }
    if (squelchDownButton) {
      squelchDownButton.disabled = !liveLatchActive;
    }
    if (squelchUpButton) {
      squelchUpButton.disabled = !liveLatchActive;
    }
  }

  function updateStatusUi(status) {
    lastStatus = status;

    const state = status.airband_scan_state || "idle";
    const locked = status.airband_locked_channel;
    const current = locked || status.airband_current_channel;
    const settings = status.airband_scanner_settings || {};

    byId(`${PATCH_ID}-state`).textContent = state;
    byId(`${PATCH_ID}-frequency`).textContent = current ? `${fmt(current.frequency_mhz || current.frequency_hz / 1000000, 3)} MHz` : "—";
    byId(`${PATCH_ID}-snr`).textContent = `${fmt(status.airband_last_signal_snr_db, 2)} dB`;
    byId(`${PATCH_ID}-level`).textContent = `${fmt(status.airband_last_measurement_dbfs, 1)} dBFS`;
    byId(`${PATCH_ID}-silence`).textContent =
      status.airband_silence_remaining === null || status.airband_silence_remaining === undefined
        ? "—"
        : `${fmt(status.airband_silence_remaining, 1)} s`;

    const audioEl = byId(`${PATCH_ID}-audio`);
    if (status.airband_live_audio_running) {
      audioEl.textContent = audioEnabled ? "live" : "armed";
      audioEl.className = "airband-normal-scanner-readout airband-normal-scanner-audio-on";
    } else {
      audioEl.textContent = "off";
      audioEl.className = "airband-normal-scanner-readout airband-normal-scanner-audio-off";
    }

    byId(`${PATCH_ID}-channel`).textContent = `Channel: ${channelLabel(current)}`;
    byId(`${PATCH_ID}-message`).textContent = status.airband_scanner_message || "Airband scanner ready.";

    byId(`${PATCH_ID}-skip`).disabled = !status.airband_scan_running;
    byId(`${PATCH_ID}-block`).disabled = !current;
    byId(`${PATCH_ID}-stop`).disabled = !status.airband_scan_running;

    if (settings.snr_threshold_db !== undefined) {
      byId(`${PATCH_ID}-snr-input`).value = settings.snr_threshold_db;
      byId(`${PATCH_ID}-squelch-input`).value = settings.squelch_dbfs;
      const squelchReadout = byId(`${PATCH_ID}-squelch-value`);
      if (squelchReadout) {
        squelchReadout.textContent = `${fmt(settings.squelch_dbfs, 1)} dBFS`;
      }
      byId(`${PATCH_ID}-gain-input`).value = settings.rf_gain_db;
      byId(`${PATCH_ID}-sample-input`).value = settings.sample_ms;
      byId(`${PATCH_ID}-silence-input`).value = settings.silence_resume_seconds;
    }

    const liveChannel = status.airband_locked_channel || null;
    const liveFrequency = liveChannel && liveChannel.frequency_hz ? Number(liveChannel.frequency_hz) : 0;
    const lockKey = status.airband_live_audio_running
      ? `${liveFrequency}:${status.airband_lock_reason || ""}:${status.airband_scanner_message || ""}`
      : null;

    if (lockKey && lockKey !== lastAirbandLockKey) {
      lastAirbandLockKey = lockKey;
      if (audioContext) {
        resetAudioCursor(status);
      } else {
        audioCursorSamples = 0;
      }
      audioNextStartTime = audioContext ? audioContext.currentTime + 0.10 : 0;
    }

    if (!lockKey) {
      lastAirbandLockKey = null;
    }

    if (status.airband_live_audio_running && audioEnabled && !audioLoopRunning) {
      startAudioLoop();
    }

    enforceLiveOnlyAirbandControls(status);
  }

  async function refreshStatus() {
    try {
      const status = await getJson(STATUS_URL);
      updateStatusUi(status);
    } catch (error) {
      const message = byId(`${PATCH_ID}-message`);
      if (message) {
        message.textContent = `Airband scanner status error: ${error.message}`;
      }
    }
  }

  function resetAudioCursor(status) {
    const available = Number(status && status.airband_live_available_samples ? status.airband_live_available_samples : 0);
    // On a new lock, start very near the live edge so audio begins quickly.
    // Keep a small cushion so the next chunk request has data available.
    audioCursorSamples = Math.max(0, available - 6000);
    audioNextStartTime = audioContext ? audioContext.currentTime + 0.10 : 0;
  }

  async function ensureAudioContext() {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === "suspended") {
      await audioContext.resume();
    }
  }

  async function startAudioLoop() {
    if (!audioEnabled || audioLoopRunning) {
      return;
    }

    await ensureAudioContext();
    resetAudioCursor(lastStatus);
    audioLoopRunning = true;

    while (audioEnabled) {
      try {
        const status = lastStatus || {};
        if (!status.airband_live_audio_running) {
          await new Promise((resolve) => setTimeout(resolve, 350));
          continue;
        }

        const url = `${LIVE_AUDIO_URL}?from=${audioCursorSamples}&samples=12000&_=${Date.now()}`;
        const response = await fetch(url, { cache: "no-store" });

        if (response.status === 204) {
          await new Promise((resolve) => setTimeout(resolve, 200));
          continue;
        }

        if (response.status === 416) {
          resetAudioCursor(lastStatus);
          await new Promise((resolve) => setTimeout(resolve, 150));
          continue;
        }

        if (!response.ok) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          continue;
        }

        const sourceSamples = Number(response.headers.get("X-Source-Samples") || "0");
        const wavData = await response.arrayBuffer();
        if (!wavData.byteLength || sourceSamples <= 0) {
          await new Promise((resolve) => setTimeout(resolve, 200));
          continue;
        }

        const decoded = await audioContext.decodeAudioData(wavData.slice(0));
        const source = audioContext.createBufferSource();
        source.buffer = decoded;
        source.connect(audioContext.destination);

        const startAt = Math.max(audioContext.currentTime + 0.03, audioNextStartTime);
        source.start(startAt);
        audioNextStartTime = startAt + decoded.duration;
        audioCursorSamples += sourceSamples;

        if (audioNextStartTime - audioContext.currentTime > 1.5) {
          await new Promise((resolve) => setTimeout(resolve, 300));
        }
      } catch (error) {
        const message = byId(`${PATCH_ID}-message`);
        if (message) {
          message.textContent = `Browser audio error: ${error.message}`;
        }
        await new Promise((resolve) => setTimeout(resolve, 650));
      }
    }

    audioLoopRunning = false;
  }

  function wireControls() {
    byId(`${PATCH_ID}-start`).addEventListener("click", async () => {
      // AIRBAND_START_ONLY_AUDIO_PATCH_V1
      // The Start Scanner button is the one browser audio permission gesture.
      // Do as much audio setup as possible before any network await.
      audioEnabled = true;
      const audioToggle = byId(`${PATCH_ID}-audio-toggle`);
      if (audioToggle) {
        audioToggle.textContent = "Browser Audio Armed By Start";
      }

      if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
      }
      try {
        audioContext.resume();
      } catch (_error) {
        // Status panel will show playback errors if the browser rejects audio.
      }
      audioNextStartTime = audioContext.currentTime + 0.15;

      // AIRBAND_START_STOPS_WX_FIRST_PATCH_V1
      // WX/NOAA and Airband share the same audio RTL-SDR. Stop NOAA live first
      // so Start Scanner can switch directly from WX to Airband without a 409.
      try {
        await postJson(NOAA_LIVE_STOP_URL);
        await new Promise((resolve) => setTimeout(resolve, 500));
      } catch (_error) {
        // NOAA/WX may already be stopped; continue with Airband start.
      }

      await postJson(START_URL);
      await refreshStatus();
      resetAudioCursor(lastStatus);
      startAudioLoop();
    });

    byId(`${PATCH_ID}-stop`).addEventListener("click", async () => {
      audioEnabled = false;
      await postJson(STOP_URL);
      await refreshStatus();
      setTimeout(() => window.dispatchEvent(new Event("airband-started")), 50);
    });

    byId(`${PATCH_ID}-skip`).addEventListener("click", async () => {
      await postJson(SKIP_URL);
      await refreshStatus();
    });

    byId(`${PATCH_ID}-block`).addEventListener("click", async () => {
      await postJson(BLOCK_URL);
      await refreshStatus();
    });

    byId(`${PATCH_ID}-sqdown`).addEventListener("click", async () => {
      await postJson(SQUELCH_DOWN_URL);
      await refreshStatus();
    });

    byId(`${PATCH_ID}-squp`).addEventListener("click", async () => {
      await postJson(SQUELCH_UP_URL);
      await refreshStatus();
    });

    const audioToggleButton = byId(`${PATCH_ID}-audio-toggle`);
    if (audioToggleButton) {
      audioToggleButton.addEventListener("click", async () => {
        // Hidden compatibility control. Browser audio is intentionally started only by Start Scanner.
        audioEnabled = true;
        await ensureAudioContext();
        resetAudioCursor(lastStatus);
        startAudioLoop();
      });
    }

    byId(`${PATCH_ID}-save-settings`).addEventListener("click", async () => {
      const payload = {
        snr_threshold_db: Number(byId(`${PATCH_ID}-snr-input`).value),
        squelch_dbfs: Number(byId(`${PATCH_ID}-squelch-input`).value),
        rf_gain_db: Number(byId(`${PATCH_ID}-gain-input`).value),
        sample_ms: Number(byId(`${PATCH_ID}-sample-input`).value),
        silence_resume_seconds: Number(byId(`${PATCH_ID}-silence-input`).value)
      };
      const result = await postJson(SETTINGS_URL, payload);
      byId(`${PATCH_ID}-settings-message`).textContent = "Scanner tuning saved.";
      updateStatusUi(result);
    });
  }

  function init() {
    if (byId(`${PATCH_ID}-panel`)) {
      return;
    }

    insertPanels();
    wireControls();
    enforceLiveOnlyAirbandControls({ airband_live_audio_running: false });
    refreshStatus();

    if (pollTimer) {
      clearInterval(pollTimer);
    }
    pollTimer = setInterval(refreshStatus, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();


// Extracted from index.html <script> block 4
(function () {
  "use strict";

  function hideAirbandAudioToggle() {
    const button = document.getElementById("airband-normal-scanner-ui-audio-toggle");
    if (button) {
      button.style.setProperty("display", "none", "important");
      button.style.setProperty("visibility", "hidden", "important");
      button.setAttribute("aria-hidden", "true");
      button.setAttribute("tabindex", "-1");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hideAirbandAudioToggle);
  } else {
    hideAirbandAudioToggle();
  }

  setTimeout(hideAirbandAudioToggle, 250);
  setTimeout(hideAirbandAudioToggle, 1000);
})();


// Extracted from index.html <script> block 5
(function () {
  "use strict";

  // This patch reaches into the normal Airband scanner UI closure indirectly by
  // intercepting fetch responses for the live audio endpoint. The main UI code
  // is also patched below by text replacement when this script is applied.
})();


// Extracted from index.html <script> block 6
(function () {
  "use strict";

  const PATCH_ID = "airband-normal-scanner-ui";
  const WX_BUTTON_ID = `${PATCH_ID}-wx`;
  let lastKnownWxRunning = false;
  let wxToggleInFlight = false;
  let wxSuppressUntil = 0;
  // AIRBAND_INITIAL_WX_DOUBLE_TOGGLE_DEBOUNCE_PATCH_V1

  function byId(id) {
    return document.getElementById(id);
  }

  function setAirbandMessage(text) {
    const message = byId(`${PATCH_ID}-message`);
    if (message) {
      message.textContent = text;
    }
  }

  function allButtons() {
    return Array.from(document.querySelectorAll("button"));
  }

  function normalizeText(node) {
    return (node && node.textContent ? node.textContent : "").replace(/\s+/g, " ").trim();
  }

  function isThisPatchButton(button) {
    return button && button.id === WX_BUTTON_ID;
  }

  function isNoaaWxButton(button) {
    if (!button || isThisPatchButton(button)) {
      return false;
    }
    const text = normalizeText(button).toLowerCase();
    const id = String(button.id || "").toLowerCase();
    const title = String(button.title || "").toLowerCase();
    const aria = String(button.getAttribute("aria-label") || "").toLowerCase();
    const combined = `${text} ${id} ${title} ${aria}`;

    return (
      combined.includes("noaa") ||
      combined.includes("weather") ||
      /\bwx\b/.test(combined)
    );
  }

  function findOperationsWxToggle(wantStop) {
    const candidates = allButtons().filter(isNoaaWxButton);

    // Prefer an explicit start/stop button matching desired state.
    const explicit = candidates.find((button) => {
      const text = normalizeText(button).toLowerCase();
      const id = String(button.id || "").toLowerCase();
      const title = String(button.title || "").toLowerCase();
      const aria = String(button.getAttribute("aria-label") || "").toLowerCase();
      const combined = `${text} ${id} ${title} ${aria}`;

      if (wantStop) {
        return combined.includes("stop") || combined.includes("off");
      }
      return combined.includes("start") || combined.includes("listen") || combined.includes("on");
    });

    if (explicit) {
      return explicit;
    }

    // Fallback to any NOAA/WX-looking button that is not our convenience button.
    return candidates[0] || null;
  }

  const AIRBAND_STOP_URL_FOR_WX = "/api/airband/scan/activity/stop";

  function delayForWxSwitch(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function postJson(url) {
    const response = await fetch(url, { method: "POST", cache: "no-store" });
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { raw: text };
      }
    }
    if (!response.ok) {
      throw new Error(payload.error || `${url} failed with HTTP ${response.status}`);
    }
    return payload;
  }

  async function getStatus() {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`/api/status failed with HTTP ${response.status}`);
    }
    return response.json();
  }

  function updateWxButtonFromStatus(status) {
    const button = byId(WX_BUTTON_ID);
    if (!button || !status) {
      return;
    }

    lastKnownWxRunning = status.audio_mode === "noaa_live" || !!status.live_audio_running;
    button.textContent = lastKnownWxRunning ? "WX Off" : "WX";
    button.title = lastKnownWxRunning ? "Stop NOAA Weather audio" : "Start NOAA Weather audio";
  }

  async function refreshWxState() {
    try {
      const status = await getStatus();
      updateWxButtonFromStatus(status);
    } catch (_error) {
      // Existing page status polling continues independently.
    }
  }

  function insertWxButton() {
    const startButton = byId(`${PATCH_ID}-start`);
    if (!startButton || byId(WX_BUTTON_ID)) {
      return;
    }

    const wxButton = document.createElement("button");
    wxButton.id = WX_BUTTON_ID;
    wxButton.type = "button";
    wxButton.textContent = "WX";
    wxButton.title = "Start NOAA Weather audio";

    startButton.parentNode.insertBefore(wxButton, startButton);
  }

  function clickOperationsWxButtonLater(button) {
    window.setTimeout(() => {
      try {
        button.click();
      } finally {
        window.setTimeout(() => {
          wxToggleInFlight = false;
        }, 1200);
      }
    }, 100);
  }

  async function handleWxClick(event) {
    const button = event.target && event.target.closest ? event.target.closest("button") : null;
    if (!button || button.id !== WX_BUTTON_ID) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const now = Date.now();
    if (wxToggleInFlight || now < wxSuppressUntil) {
      return;
    }

    wxToggleInFlight = true;
    wxSuppressUntil = now + 2500;

    try {
      const status = await getStatus();
      updateWxButtonFromStatus(status);
    } catch (_error) {
      // Use last known state.
    }

    const wantStop = lastKnownWxRunning;
    const operationsButton = findOperationsWxToggle(wantStop);

    if (operationsButton) {
      setAirbandMessage(wantStop ? "Stopping WX audio…" : "Starting WX audio…");

      // AIRBAND_WX_STOPS_AIRBAND_FIRST_PATCH_V1
      // WX and Airband share the same audio RTL-SDR. If WX is currently off,
      // this click is a WX start request, so stop Airband first and give the
      // backend a moment to release the receiver before delegating to the
      // existing Operations NOAA/WX control.
      if (!wantStop) {
        try {
          await postJson(AIRBAND_STOP_URL_FOR_WX);
          await delayForWxSwitch(700);
        } catch (_error) {
          // Airband may already be stopped; continue with WX start.
        }
      }

      clickOperationsWxButtonLater(operationsButton);
      setTimeout(refreshWxState, 700);
      setTimeout(refreshWxState, 1500);
      return;
    }

    // Fallback only. The preferred path above uses the validated Operations menu
    // control so browser audio behavior remains identical to the existing UI.
    try {
      setAirbandMessage(wantStop ? "Stopping WX audio…" : "Starting WX audio…");
      if (!wantStop) {
        try {
          await postJson(AIRBAND_STOP_URL_FOR_WX);
          await delayForWxSwitch(700);
        } catch (_error) {
          // Airband may already be stopped.
        }
      }
      await postJson(wantStop ? "/api/noaa/live/stop" : "/api/noaa/live/start");
      await refreshWxState();
    } catch (error) {
      setAirbandMessage(`WX toggle failed: ${error.message}`);
    } finally {
      window.setTimeout(() => {
        wxToggleInFlight = false;
      }, 1200);
    }
  }

  function initWxDuplicateButton() {
    insertWxButton();
    refreshWxState();

    // The Airband panel is dynamically inserted by another patch, so try again
    // shortly after load in case this patch runs first.
    setTimeout(insertWxButton, 500);
    setTimeout(refreshWxState, 750);
    setTimeout(insertWxButton, 1500);
    setTimeout(refreshWxState, 1750);
  }

  document.addEventListener("click", handleWxClick, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initWxDuplicateButton);
  } else {
    initWxDuplicateButton();
  }

  setInterval(refreshWxState, 2500);
})();


// Extracted from index.html <script> block 7
(function () {
  "use strict";

  if (window.__airbandWxWaitsForIdleInstalled) {
    return;
  }
  window.__airbandWxWaitsForIdleInstalled = true;

  const PATCH_ID = "airband-normal-scanner-ui";
  const WX_BUTTON_ID = `${PATCH_ID}-wx`;
  const AIRBAND_STOP_URL = "/api/airband/scan/activity/stop";
  const NOAA_START_URL = "/api/noaa/live/start";
  const NOAA_STOP_URL = "/api/noaa/live/stop";

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function setAirbandMessage(text) {
    const message = byId(`${PATCH_ID}-message`);
    if (message) {
      message.textContent = text;
    }
  }

  async function postJson(url) {
    const response = await fetch(url, { method: "POST", cache: "no-store" });
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { raw: text };
      }
    }
    if (!response.ok) {
      throw new Error(payload.error || `${url} failed with HTTP ${response.status}`);
    }
    return payload;
  }

  async function getStatus() {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`/api/status failed with HTTP ${response.status}`);
    }
    return response.json();
  }

  function normalizedText(node) {
    return (node && node.textContent ? node.textContent : "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function isOurWxButton(button) {
    return button && button.id === WX_BUTTON_ID;
  }

  function isOperationsNoaaWxButton(button) {
    if (!button || isOurWxButton(button)) {
      return false;
    }

    const combined = [
      normalizedText(button),
      String(button.id || "").toLowerCase(),
      String(button.title || "").toLowerCase(),
      String(button.getAttribute("aria-label") || "").toLowerCase()
    ].join(" ");

    return (
      combined.includes("noaa") ||
      combined.includes("weather") ||
      /\bwx\b/.test(combined)
    );
  }

  function findOperationsWxButton(wantStop) {
    const candidates = Array.from(document.querySelectorAll("button"))
      .filter(isOperationsNoaaWxButton);

    const enabled = candidates.filter((button) => !button.disabled);
    const searchList = enabled.length ? enabled : candidates;

    const explicit = searchList.find((button) => {
      const combined = [
        normalizedText(button),
        String(button.id || "").toLowerCase(),
        String(button.title || "").toLowerCase(),
        String(button.getAttribute("aria-label") || "").toLowerCase()
      ].join(" ");

      if (wantStop) {
        return combined.includes("stop") || combined.includes("off");
      }
      return combined.includes("start") || combined.includes("listen") || combined.includes("on");
    });

    return explicit || searchList[0] || null;
  }

  async function waitForAirbandIdle(maxMs) {
    const deadline = Date.now() + maxMs;

    while (Date.now() < deadline) {
      let status = null;
      try {
        status = await getStatus();
      } catch (_error) {
        await delay(250);
        continue;
      }

      const airbandStopped = !status.airband_scan_running &&
        status.airband_scan_state !== "locked" &&
        status.airband_scan_state !== "spectrum_search" &&
        status.audio_mode !== "airband_scan" &&
        status.audio_mode !== "airband_live";

      if (airbandStopped) {
        return status;
      }

      await delay(250);
    }

    return await getStatus().catch(() => null);
  }

  function updateLocalWxButton(status) {
    const button = byId(WX_BUTTON_ID);
    if (!button || !status) {
      return;
    }
    const wxRunning = status.audio_mode === "noaa_live" || !!status.live_audio_running;
    button.textContent = wxRunning ? "WX Off" : "WX";
    button.title = wxRunning ? "Stop NOAA Weather audio" : "Start NOAA Weather audio";
  }

  async function handleUnderMapWx(event) {
    const button = event.target && event.target.closest ? event.target.closest("button") : null;
    if (!button || button.id !== WX_BUTTON_ID) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    let status = null;
    try {
      status = await getStatus();
      updateLocalWxButton(status);
    } catch (_error) {
      status = null;
    }

    const wxRunning = !!status && (status.audio_mode === "noaa_live" || !!status.live_audio_running);

    if (wxRunning) {
      setAirbandMessage("Stopping WX audio…");
      const operationsButton = findOperationsWxButton(true);
      if (operationsButton) {
        operationsButton.click();
      } else {
        try {
          await postJson(NOAA_STOP_URL);
        } catch (error) {
          setAirbandMessage(`WX stop failed: ${error.message}`);
          return;
        }
      }
      setTimeout(async () => updateLocalWxButton(await getStatus().catch(() => null)), 700);
      return;
    }

    setAirbandMessage("Stopping Airband before WX…");

    try {
      await postJson(AIRBAND_STOP_URL);
    } catch (_error) {
      // Airband may already be stopped.
    }

    const idleStatus = await waitForAirbandIdle(5000);
    updateLocalWxButton(idleStatus);

    setAirbandMessage("Starting WX audio…");

    // Re-find after Airband has actually stopped; the Operations control may
    // have been disabled or stale before the status refresh.
    const operationsButton = findOperationsWxButton(false);
    if (operationsButton) {
      operationsButton.click();
      setTimeout(async () => updateLocalWxButton(await getStatus().catch(() => null)), 700);
      setTimeout(async () => updateLocalWxButton(await getStatus().catch(() => null)), 1800);
      return;
    }

    // Fallback if the Operations control cannot be found.
    try {
      await postJson(NOAA_START_URL);
      updateLocalWxButton(await getStatus().catch(() => null));
      setAirbandMessage("WX audio started.");
    } catch (error) {
      setAirbandMessage(`WX start failed: ${error.message}`);
    }
  }

  document.addEventListener("click", handleUnderMapWx, true);
})();

/*
 * RTL_PI_RECEIVER_LOCATION_MAP_PICK_PLACEMENT_V3
 *
 * Keep the receiver-location "pick location on map" control grouped with the
 * receiver name / latitude / longitude controls. This uses runtime DOM
 * placement so it works with both static and generated menu markup.
 */
(function ensureReceiverLocationMapPickControlPlacement() {
  let observerStarted = false;
  let lastRun = 0;

  function textOf(el) {
    if (!el) return "";
    const parts = [
      el.textContent,
      el.value,
      el.title,
      el.ariaLabel,
      el.getAttribute && el.getAttribute("aria-label"),
      el.getAttribute && el.getAttribute("data-action"),
      el.id,
      el.name,
      el.className
    ];
    return parts.filter(Boolean).join(" ").toLowerCase().replace(/\s+/g, " ").trim();
  }

  function isMapPickControl(el) {
    const t = textOf(el);
    return /\bmap\b/.test(t)
      && /\b(pick|select|choose|set|place|drop|locate)\b/.test(t)
      && /\b(location|receiver|lat|latitude|lon|lng|longitude)\b/.test(t);
  }

  function rowFor(el) {
    if (!el) return null;
    const selectors = [
      ".form-row",
      ".field-row",
      ".control-row",
      ".setting-row",
      ".menu-row",
      ".button-row",
      ".form-group",
      ".input-group",
      ".location-row",
      ".receiver-location-row",
      "label",
      "p",
      "li",
      "div"
    ];

    for (const selector of selectors) {
      const row = el.closest(selector);
      if (row && row !== document.body && row !== document.documentElement && textOf(row).length < 500) {
        return row;
      }
    }

    return el;
  }

  function scoreLocationContainer(el) {
    const t = textOf(el);
    if (!t) return 0;

    let score = 0;
    if (/\breceiver location\b/.test(t)) score += 12;
    if (/\blocation\b/.test(t)) score += 3;
    if (/\blatitude\b|\blat\b/.test(t)) score += 4;
    if (/\blongitude\b|\blon\b|\blng\b/.test(t)) score += 4;
    if (/\bname\b/.test(t)) score += 1;
    if (/\baircraft\b|\btrail\b|\bnoaa\b|\bairband\b|\bscanner\b/.test(t)) score -= 3;

    const fields = Array.from(el.querySelectorAll("input, select, textarea, label"));
    for (const field of fields) {
      const ft = textOf(field);
      if (/\blatitude\b|\blat\b/.test(ft)) score += 2;
      if (/\blongitude\b|\blon\b|\blng\b/.test(ft)) score += 2;
      if (/\breceiver location\b|\blocation name\b/.test(ft)) score += 2;
    }

    return score;
  }

  function findLocationContainer() {
    const selectors = [
      "fieldset",
      "details",
      "section",
      ".card",
      ".panel",
      ".menu-section",
      ".settings-section",
      ".config-section",
      ".control-section",
      ".control-group",
      ".settings-group",
      ".form-section",
      "div"
    ];

    return Array.from(document.querySelectorAll(selectors.join(",")))
      .map((el) => ({ el, score: scoreLocationContainer(el), size: el.querySelectorAll("*").length }))
      .filter((x) => x.score >= 10)
      .sort((a, b) => (b.score - a.score) || (a.size - b.size))[0]?.el || null;
  }

  function findInsertionRow(container) {
    if (!container) return null;

    const fields = Array.from(container.querySelectorAll("input, select, textarea"));
    const locationFields = fields.filter((field) => {
      const t = textOf(field);
      return /\blocation\b|\blatitude\b|\blat\b|\blongitude\b|\blon\b|\blng\b|\bname\b/.test(t);
    });

    if (!locationFields.length) return null;
    return rowFor(locationFields[locationFields.length - 1]);
  }

  function moveControl() {
    const now = Date.now();
    if (now - lastRun < 200) return;
    lastRun = now;

    const container = findLocationContainer();
    if (!container) return;

    const control = Array.from(document.querySelectorAll("button, a, input[type='button'], input[type='submit']"))
      .find(isMapPickControl);

    if (!control) return;

    const controlRow = rowFor(control);
    if (!controlRow || container.contains(controlRow)) return;

    const insertionRow = findInsertionRow(container);

    if (insertionRow && insertionRow.parentNode === container) {
      insertionRow.insertAdjacentElement("afterend", controlRow);
    } else {
      container.appendChild(controlRow);
    }

    controlRow.classList.add("receiver-location-map-pick-control--moved");
    control.setAttribute("data-placement", "receiver-location-controls");
  }

  function scheduleMove() {
    window.requestAnimationFrame(moveControl);
  }

  function startObserver() {
    if (observerStarted || !document.body || !window.MutationObserver) return;
    observerStarted = true;
    new MutationObserver(scheduleMove).observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    scheduleMove();
    setTimeout(scheduleMove, 250);
    setTimeout(scheduleMove, 1000);
    startObserver();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BIND_ADDRESS = os.environ.get("RTL_PI_BIND", "0.0.0.0")
PORT = int(os.environ.get("RTL_PI_PORT", "8080"))
ROOT = Path(os.environ.get("RTL_PI_ROOT", "/opt/rtl-pi-adsb-tracker"))
WEB_ROOT = ROOT / "web"
OUTPUT_DIR = ROOT / "test_output"
SETTINGS_DIR = ROOT / "settings"
RECEIVER_LOCATION_PATH = SETTINGS_DIR / "receiver_location.json"
AIRLABS_DIAGNOSTIC_SETTINGS_PATH = SETTINGS_DIR / "airlabs_api.json"
AIRLABS_ROUTE_CACHE_PATH = SETTINGS_DIR / "airlabs_route_cache.json"
AIRLABS_ROUTE_CACHE_TTL_SECONDS = int(os.environ.get("RTL_PI_AIRLABS_ROUTE_CACHE_TTL_SECONDS", "7200"))
NOAA_SELECTION_PATH = SETTINGS_DIR / "selected_noaa_channel.json"
TRAIL_HISTORY_PATH = SETTINGS_DIR / "aircraft_trails_history.json"
TRAIL_CONTROL_PATH = SETTINGS_DIR / "aircraft_trails_control.json"
DATA_DIR = ROOT / "data"
AIRBAND_DATA_PATH = DATA_DIR / "airband_frequencies_full.json"
READSB_JSON_DIR = Path(os.environ.get("RTL_PI_READSB_JSON_DIR", "/run/rtl-pi-readsb"))
AIRCRAFT_JSON = READSB_JSON_DIR / "aircraft.json"
READSB_STATUS_JSON = READSB_JSON_DIR / "status.json"
AUDIO_BINARY = Path(os.environ.get("RTL_PI_AUDIO_BINARY", "/opt/rtl-pi-adsb-tracker/bin/rtl_noaa_receiver"))
AUDIO_SERIAL = os.environ.get("RTL_PI_AUDIO_SERIAL", "00000162")
NOAA_STATION = os.environ.get("RTL_PI_NOAA_STATION", "KGG68_HOUSTON")
NOAA_FREQ_HZ = int(os.environ.get("RTL_PI_NOAA_FREQ_HZ", "162400000"))
RF_GAIN_DB = os.environ.get("RTL_PI_RF_GAIN_DB", "40.2")
AUDIO_OUTPUT_GAIN = os.environ.get("RTL_PI_AUDIO_OUTPUT_GAIN", "15000")
SURVEY_BINARY = Path(
    os.environ.get("RTL_PI_NOAA_SURVEY_BINARY", "/opt/rtl-pi-adsb-tracker/bin/rtl_noaa_survey")
)
AIRBAND_BINARY = Path(
    os.environ.get("RTL_PI_AIRBAND_BINARY", "/opt/rtl-pi-adsb-tracker/bin/rtl_airband_receiver")
)
AIRBAND_AUDIO_OUTPUT_GAIN = os.environ.get("RTL_PI_AIRBAND_AUDIO_OUTPUT_GAIN", "120000")
AIRBAND_ACTIVITY_THRESHOLD_SNR_DB = float(os.environ.get("RTL_PI_AIRBAND_ACTIVITY_THRESHOLD_SNR_DB", "6.0"))
AIRBAND_SCAN_SAMPLE_MILLISECONDS = int(os.environ.get("RTL_PI_AIRBAND_SCAN_SAMPLE_MILLISECONDS", "500"))
SURVEY_SECONDS = int(os.environ.get("RTL_PI_NOAA_SURVEY_SECONDS", "2"))
AUDIO_RATE_HZ = 24000

selected_noaa_frequency_hz = NOAA_FREQ_HZ
selected_noaa_station = NOAA_STATION
CAPTURE_WAV_PATH = OUTPUT_DIR / "api_last_noaa_capture.wav"
AIRBAND_CAPTURE_WAV_PATH = OUTPUT_DIR / "api_last_airband_capture.wav"
AIRBAND_SCAN_SAMPLE_PATH = OUTPUT_DIR / "airband_scan_sample.wav"
AIRBAND_DETECTED_WAV_PATH = OUTPUT_DIR / "airband_detected_latest.wav"
AIRBAND_BEST_WAV_PATH = OUTPUT_DIR / "airband_best_candidate.wav"
LIVE_WAV_PATH = OUTPUT_DIR / "live_noaa_source.wav"
LIVE_LOG_PATH = OUTPUT_DIR / "live_noaa_receiver.log"

receiver_lock = threading.Lock()
state_lock = threading.RLock()
live_process: subprocess.Popen[str] | None = None
live_log_handle = None
live_holds_receiver_lock = False
airband_scan_stop_event = threading.Event()
airband_scan_thread: threading.Thread | None = None
airband_test_stop_event = threading.Event()
airband_test_command_event = threading.Event()
airband_test_thread: threading.Thread | None = None
runtime_state: dict[str, object] = {
    "last_capture_time": None,
    "last_capture_seconds": None,
    "last_capture_error": None,
    "live_start_time": None,
    "live_stop_time": None,
    "live_error": None,
    "last_noaa_survey": None,
    "last_noaa_survey_time": None,
    "airband_scan_running": False,
    "airband_scan_state": "idle",
    "airband_scan_cycles": 0,
    "airband_channels_scanned": 0,
    "airband_current_channel": None,
    "airband_last_measurement_dbfs": None,
    "airband_last_signal_snr_db": None,
    "airband_last_detection": None,
    "airband_scan_error": None,
    "airband_test_running": False,
    "airband_test_state": "idle",
    "airband_test_cycle": 0,
    "airband_test_current_channel": None,
    "airband_test_active_channel": None,
    "airband_test_silence_remaining": None,
    "airband_test_hold": False,
    "airband_test_command": None,
    "airband_test_event_id": 0,
    "airband_test_message": "SIMULATED: Test scanner is idle.",
    "airband_scan_scope": "priority",
    "airband_watch_frequency_hz": None,
    "airband_best_candidate": None,
}

def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

def read_receiver_location() -> dict | None:
    data = read_json(RECEIVER_LOCATION_PATH)
    if not data:
        return None
    if (
        isinstance(data.get("name"), str)
        and isinstance(data.get("latitude"), (int, float))
        and isinstance(data.get("longitude"), (int, float))
        and isinstance(data.get("airband_radius_miles"), (int, float))
    ):
        return data
    return None


def validate_receiver_location(payload: dict) -> tuple[dict | None, str | None]:
    name = str(payload.get("name", "")).strip()
    try:
        latitude = float(payload.get("latitude"))
        longitude = float(payload.get("longitude"))
        radius = float(payload.get("airband_radius_miles", 100))
    except (TypeError, ValueError):
        return None, "Latitude, longitude, and radius must be numeric."

    if not name:
        return None, "Receiver location name is required."
    if latitude < -90.0 or latitude > 90.0:
        return None, "Latitude must be between -90 and 90."
    if longitude < -180.0 or longitude > 180.0:
        return None, "Longitude must be between -180 and 180."
    if radius <= 0.0 or radius > 500.0:
        return None, "Airband radius must be greater than 0 and no more than 500 miles."

    return {
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
        "airband_radius_miles": radius,
        "updated_utc": int(time.time()),
    }, None


def save_receiver_location(location: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = RECEIVER_LOCATION_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(location, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(RECEIVER_LOCATION_PATH)
    # Changing or re-saving receiver location invalidates the local NOAA cache.
    clear_saved_noaa_selection()


def save_airband_radius_only(radius_miles: float) -> dict | None:
    location = read_receiver_location()
    if location is None:
        return None

    updated = dict(location)
    updated["airband_radius_miles"] = float(radius_miles)
    updated["updated_utc"] = int(time.time())

    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    location_temporary = RECEIVER_LOCATION_PATH.with_suffix(".json.tmp")
    location_temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    location_temporary.replace(RECEIVER_LOCATION_PATH)

    # Radius affects only Airband frequency selection. Keep a valid NOAA
    # selection by advancing its stored receiver-location cache key.
    saved_noaa = read_json(NOAA_SELECTION_PATH)
    if isinstance(saved_noaa, dict) and saved_noaa.get("frequency_hz") is not None:
        saved_noaa["receiver_location_key"] = receiver_location_cache_key(updated)
        noaa_temporary = NOAA_SELECTION_PATH.with_suffix(".json.tmp")
        noaa_temporary.write_text(json.dumps(saved_noaa, indent=2) + "\n", encoding="utf-8")
        noaa_temporary.replace(NOAA_SELECTION_PATH)

    return updated


def receiver_location_cache_key(location: dict | None) -> dict | None:
    if location is None:
        return None
    return {
        "latitude": round(float(location["latitude"]), 6),
        "longitude": round(float(location["longitude"]), 6),
        "airband_radius_miles": round(float(location["airband_radius_miles"]), 2),
    }


def read_saved_noaa_selection() -> dict | None:
    saved = read_json(NOAA_SELECTION_PATH)
    location = read_receiver_location()
    if not saved or location is None:
        return None
    if saved.get("receiver_location_key") != receiver_location_cache_key(location):
        return None
    frequency_hz = saved.get("frequency_hz")
    if not isinstance(frequency_hz, int) or frequency_hz <= 0:
        return None
    if not isinstance(saved.get("station"), str):
        return None
    return saved


def save_noaa_selection(frequency_hz: int, station: str, survey: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    location = read_receiver_location()
    if location is None:
        return
    selection = {
        "frequency_hz": int(frequency_hz),
        "station": station,
        "receiver_location_key": receiver_location_cache_key(location),
        "saved_utc": int(time.time()),
        "survey": survey,
    }
    temporary_path = NOAA_SELECTION_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(NOAA_SELECTION_PATH)


def clear_saved_noaa_selection() -> None:
    try:
        NOAA_SELECTION_PATH.unlink()
    except FileNotFoundError:
        pass


def read_airlabs_diagnostic_key() -> str:
    settings = read_json(AIRLABS_DIAGNOSTIC_SETTINGS_PATH)
    if not isinstance(settings, dict):
        return ""
    return str(settings.get("api_key", "")).strip()


def airlabs_diagnostic_status() -> dict:
    key = read_airlabs_diagnostic_key()
    return {
        "provider": "AirLabs",
        "diagnostic_only": True,
        "configured": bool(key),
        "key_hint": ("Ending in " + key[-4:]) if key else None,
        "settings_file": AIRLABS_DIAGNOSTIC_SETTINGS_PATH.name,
        "route_cache_entries": active_airlabs_route_cache_entries(),
        "cache_ttl_seconds": AIRLABS_ROUTE_CACHE_TTL_SECONDS,
    }


def save_airlabs_diagnostic_key(api_key: str) -> dict:
    key = str(api_key or "").strip()
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    if not key:
        try:
            AIRLABS_DIAGNOSTIC_SETTINGS_PATH.unlink()
        except FileNotFoundError:
            pass
        return airlabs_diagnostic_status()

    temporary_path = AIRLABS_DIAGNOSTIC_SETTINGS_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps({"api_key": key, "updated_utc": int(time.time())}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(AIRLABS_DIAGNOSTIC_SETTINGS_PATH)
    AIRLABS_DIAGNOSTIC_SETTINGS_PATH.chmod(0o600)

    reread = airlabs_diagnostic_status()
    if not reread["configured"]:
        raise RuntimeError("AirLabs key was written but could not be read back.")
    return reread


def read_airlabs_route_cache() -> dict:
    cache = read_json(AIRLABS_ROUTE_CACHE_PATH)
    if not isinstance(cache, dict):
        return {"entries": {}}
    entries = cache.get("entries")
    if not isinstance(entries, dict):
        return {"entries": {}}
    return {"entries": entries}


def active_airlabs_route_cache_entries() -> int:
    now = int(time.time())
    entries = read_airlabs_route_cache()["entries"]
    return sum(
        1 for entry in entries.values()
        if isinstance(entry, dict)
        and now - int(entry.get("cached_utc", 0)) < AIRLABS_ROUTE_CACHE_TTL_SECONDS
    )


def load_cached_airlabs_route(callsign: str) -> dict | None:
    entry = read_airlabs_route_cache()["entries"].get(callsign)
    if not isinstance(entry, dict):
        return None
    cached_utc = int(entry.get("cached_utc", 0))
    age_seconds = max(0, int(time.time()) - cached_utc)
    if age_seconds >= AIRLABS_ROUTE_CACHE_TTL_SECONDS:
        return None
    result = entry.get("result")
    if not isinstance(result, dict) or not result.get("matched"):
        return None
    cached_result = dict(result)
    cached_result.update({
        "cache_hit": True,
        "cache_age_seconds": age_seconds,
        "cache_ttl_seconds": AIRLABS_ROUTE_CACHE_TTL_SECONDS,
        "message": "Route fields returned from cache.",
    })
    return cached_result


def save_cached_airlabs_route(callsign: str, result: dict) -> None:
    if not result.get("matched"):
        return
    now = int(time.time())
    entries = read_airlabs_route_cache()["entries"]
    entries = {
        key: value for key, value in entries.items()
        if isinstance(value, dict)
        and now - int(value.get("cached_utc", 0)) < AIRLABS_ROUTE_CACHE_TTL_SECONDS
    }
    stored_result = dict(result)
    stored_result["cache_hit"] = False
    stored_result["cache_age_seconds"] = 0
    stored_result["cache_ttl_seconds"] = AIRLABS_ROUTE_CACHE_TTL_SECONDS
    entries[callsign] = {"cached_utc": now, "result": stored_result}
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = AIRLABS_ROUTE_CACHE_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps({"entries": entries, "updated_utc": now}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.chmod(0o600)
    temporary_path.replace(AIRLABS_ROUTE_CACHE_PATH)
    AIRLABS_ROUTE_CACHE_PATH.chmod(0o600)


def clear_airlabs_route_cache() -> dict:
    try:
        AIRLABS_ROUTE_CACHE_PATH.unlink()
    except FileNotFoundError:
        pass
    return {
        "provider": "AirLabs",
        "cleared": True,
        "route_cache_entries": 0,
        "cache_ttl_seconds": AIRLABS_ROUTE_CACHE_TTL_SECONDS,
    }


def clean_airlabs_callsign(value: str) -> str:
    return "".join(character for character in str(value or "").upper().strip() if character.isalnum())


def test_airlabs_route_diagnostic(flight: str) -> dict:
    status = airlabs_diagnostic_status()
    key = read_airlabs_diagnostic_key()
    base = {
        "provider": "AirLabs",
        "diagnostic_only": True,
        "configured": status["configured"],
        "key_hint": status["key_hint"],
    }
    if not key:
        return {
            **base,
            "matched": False,
            "message": "No readable AirLabs key was found in settings/airlabs_api.json.",
        }

    callsign = clean_airlabs_callsign(flight)
    if not callsign:
        return {
            **base,
            "matched": False,
            "message": "Provide a commercial flight ICAO callsign, for example UAL1234.",
        }

    cached_result = load_cached_airlabs_route(callsign)
    if cached_result is not None:
        return cached_result

    query = urllib.parse.urlencode({"flight_icao": callsign, "api_key": key})
    request_url = "https://airlabs.co/api/v9/flight?" + query
    request = urllib.request.Request(
        request_url,
        headers={"Accept": "application/json", "User-Agent": "RTL-Pi-ADS-B-Tracker/diagnostic"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:240]
        return {
            **base,
            "matched": False,
            "message": f"AirLabs HTTP {exc.code}: {body}",
        }
    except Exception as exc:
        return {
            **base,
            "matched": False,
            "message": f"AirLabs request failed: {exc}",
        }

    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        return {
            **base,
            "matched": False,
            "message": f"AirLabs error: {message}",
        }

    record = payload.get("response") if isinstance(payload, dict) and isinstance(payload.get("response"), dict) else payload
    if not isinstance(record, dict):
        return {
            **base,
            "matched": False,
            "message": f"No route record returned for {callsign}.",
        }

    fields = {
        "flight_icao": record.get("flight_icao") or callsign,
        "flight_iata": record.get("flight_iata"),
        "departure_iata": record.get("dep_iata"),
        "departure_icao": record.get("dep_icao"),
        "arrival_iata": record.get("arr_iata"),
        "arrival_icao": record.get("arr_icao"),
        "registration": record.get("reg_number"),
        "aircraft_icao": record.get("aircraft_icao"),
        "status": record.get("status"),
    }
    matched = any(fields[name] for name in ("departure_iata", "departure_icao", "arrival_iata", "arrival_icao"))
    result = {
        **base,
        "matched": matched,
        **fields,
        "cache_hit": False,
        "cache_age_seconds": 0,
        "cache_ttl_seconds": AIRLABS_ROUTE_CACHE_TTL_SECONDS,
        "message": "Route fields returned." if matched else f"AirLabs returned no origin/destination for {callsign}.",
    }
    if matched:
        save_cached_airlabs_route(callsign, result)
    return result


def first_present(record: dict, names: tuple[str, ...]) -> object | None:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return None


def as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def haversine_miles(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    earth_radius_miles = 3958.7613
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = math.radians(latitude_b - latitude_a)
    delta_lon = math.radians(longitude_b - longitude_a)
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2.0) ** 2
    )
    return earth_radius_miles * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def load_airband_dataset() -> tuple[list[dict], dict]:
    data = read_json(AIRBAND_DATA_PATH)
    channels = data.get("channels", [])
    if not isinstance(channels, list):
        channels = []
    metadata = data.get("metadata", {})
    return [item for item in channels if isinstance(item, dict)], metadata if isinstance(metadata, dict) else {}


def normalize_airband_channel(record: dict, location: dict) -> dict | None:
    category = str(record.get("category", "")).upper()
    demodulation = str(record.get("demodulation", record.get("mode", "AM"))).upper()

    if category == "NOAA_WEATHER" or demodulation not in ("AM", ""):
        return None

    latitude = as_float(first_present(record, ("latitude", "lat", "airport_latitude", "facility_latitude", "latitude_decimal")))
    longitude = as_float(first_present(record, ("longitude", "lon", "airport_longitude", "facility_longitude", "longitude_decimal")))
    if latitude is None or longitude is None:
        return None

    frequency_hz = as_float(first_present(record, ("frequency_hz",)))
    frequency_mhz = as_float(first_present(record, ("frequency_mhz", "frequency", "freq_mhz")))
    if frequency_hz is None and frequency_mhz is not None:
        frequency_hz = round(frequency_mhz * 1000000.0)
    if frequency_mhz is None and frequency_hz is not None:
        frequency_mhz = frequency_hz / 1000000.0
    if frequency_hz is None or frequency_mhz is None:
        return None

    distance_miles = haversine_miles(
        float(location["latitude"]),
        float(location["longitude"]),
        latitude,
        longitude,
    )

    return {
        "frequency_hz": int(round(frequency_hz)),
        "frequency_mhz": round(frequency_mhz, 3),
        "demodulation": "AM",
        "category": category or "AIRBAND",
        "airport_id": first_present(record, ("airport_id", "icao", "icao_id", "facility_id", "identifier", "site_number")),
        "airport_name": first_present(record, ("airport_name", "facility_name", "name", "airport")),
        "use": first_present(record, ("use", "description", "frequency_use", "service", "type")),
        "latitude": latitude,
        "longitude": longitude,
        "distance_miles": round(distance_miles, 1),
    }


def airband_channel_scan_priority(channel: dict) -> tuple[int, float, int]:
    label = " ".join(
        str(channel.get(field) or "")
        for field in ("use", "category", "airport_name", "airport_id")
    ).upper()

    if any(term in label for term in ("ATIS", "AWOS", "ASOS")):
        priority = 0
    elif any(term in label for term in ("TOWER", "TWR", "APPROACH", "DEP", "GROUND", "GND", "CTAF", "UNICOM", "CLEARANCE")):
        priority = 1
    else:
        priority = 2

    return priority, float(channel["distance_miles"]), int(channel["frequency_hz"])


def select_airband_scan_channels(channels: list[dict], scope: str) -> tuple[list[dict], str]:
    ordered = sorted(channels, key=airband_channel_scan_priority)

    if scope == "continuous":
        selected = [channel for channel in ordered if airband_channel_scan_priority(channel)[0] == 0]
        return (selected if selected else ordered, "continuous" if selected else "all_fallback")

    if scope == "priority":
        selected = [channel for channel in ordered if airband_channel_scan_priority(channel)[0] < 2]
        return (selected if selected else ordered, "priority" if selected else "all_fallback")

    return ordered, "all"


def nearby_airband_channels(location: dict) -> dict:
    raw_channels, metadata = load_airband_dataset()
    radius_miles = float(location["airband_radius_miles"])
    selected: list[dict] = []

    for record in raw_channels:
        channel = normalize_airband_channel(record, location)
        if channel is not None and channel["distance_miles"] <= radius_miles:
            selected.append(channel)

    selected.sort(key=lambda item: (item["distance_miles"], item["frequency_hz"], str(item.get("airport_id") or "")))

    # FAA records can contain duplicate service labels on one RF channel.
    # Scan each frequency only once, keeping the first/nearest sorted record.
    unique_by_frequency: dict[int, dict] = {}
    for channel in selected:
        if channel["frequency_hz"] not in unique_by_frequency:
            unique_by_frequency[channel["frequency_hz"]] = channel
    unique_channels = list(unique_by_frequency.values())

    return {
        "data_available": AIRBAND_DATA_PATH.exists(),
        "data_path": str(AIRBAND_DATA_PATH),
        "data_metadata": metadata,
        "receiver_location": location,
        "radius_miles": radius_miles,
        "raw_record_count": len(selected),
        "duplicate_records_removed": len(selected) - len(unique_channels),
        "channel_count": len(unique_channels),
        "channels": unique_channels,
    }


def pcm_wav_rms_dbfs(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
                return -120.0
            frames = wav_file.readframes(wav_file.getnframes())
    except (FileNotFoundError, wave.Error, OSError):
        return -120.0

    count = len(frames) // 2
    if count == 0:
        return -120.0

    total_square = 0.0
    for (sample,) in struct.iter_unpack("<h", frames[:count * 2]):
        normalized = sample / 32768.0
        total_square += normalized * normalized

    rms = math.sqrt(total_square / count)
    return 20.0 * math.log10(max(rms, 1.0e-12))


def airband_scan_worker(channels: list[dict]) -> None:
    global airband_scan_thread
    detection_found = False
    try:
        while not airband_scan_stop_event.is_set() and not detection_found:
            with state_lock:
                runtime_state["airband_scan_cycles"] = int(runtime_state["airband_scan_cycles"]) + 1
                runtime_state["airband_scan_state"] = "searching"

            for channel in channels:
                if airband_scan_stop_event.is_set():
                    break

                safe_unlink(AIRBAND_SCAN_SAMPLE_PATH)
                command = [
                    str(AIRBAND_BINARY),
                    "--serial", AUDIO_SERIAL,
                    "--freq-hz", str(channel["frequency_hz"]),
                    "--duration-ms", str(AIRBAND_SCAN_SAMPLE_MILLISECONDS),
                    "--gain-db", RF_GAIN_DB,
                    "--audio-gain", AIRBAND_AUDIO_OUTPUT_GAIN,
                    "--wav-output", str(AIRBAND_SCAN_SAMPLE_PATH),
                ]
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=(AIRBAND_SCAN_SAMPLE_MILLISECONDS / 1000.0) + 20,
                    check=False,
                )
                if result.returncode != 0:
                    with state_lock:
                        runtime_state["airband_scan_error"] = result.stderr.strip() or "AM scan sample failed."
                    continue

                rms_dbfs = pcm_wav_rms_dbfs(AIRBAND_SCAN_SAMPLE_PATH)
                signal_match = re.search(r"RF estimated SNR:\s+(-?[0-9]+(?:\.[0-9]+)?) dB", result.stdout)
                signal_snr_db = float(signal_match.group(1)) if signal_match else -30.0
                candidate = {
                    "channel": channel,
                    "audio_rms_dbfs": round(rms_dbfs, 2),
                    "rf_estimated_snr_db": round(signal_snr_db, 2),
                    "observed_utc": int(time.time()),
                    "audio_url": "/api/airband/scan/best_audio.wav",
                }
                with state_lock:
                    runtime_state["airband_channels_scanned"] = int(runtime_state["airband_channels_scanned"]) + 1
                    runtime_state["airband_current_channel"] = channel
                    runtime_state["airband_last_measurement_dbfs"] = round(rms_dbfs, 2)
                    runtime_state["airband_last_signal_snr_db"] = round(signal_snr_db, 2)
                    previous_best = runtime_state.get("airband_best_candidate")
                    if (
                        previous_best is None
                        or signal_snr_db > float(previous_best.get("rf_estimated_snr_db", -999.0))
                    ):
                        shutil.copyfile(AIRBAND_SCAN_SAMPLE_PATH, AIRBAND_BEST_WAV_PATH)
                        runtime_state["airband_best_candidate"] = candidate

                if signal_snr_db >= AIRBAND_ACTIVITY_THRESHOLD_SNR_DB:
                    shutil.copyfile(AIRBAND_SCAN_SAMPLE_PATH, AIRBAND_DETECTED_WAV_PATH)
                    detection = {
                        "channel": channel,
                        "audio_rms_dbfs": round(rms_dbfs, 2),
                        "rf_estimated_snr_db": round(signal_snr_db, 2),
                        "threshold_snr_db": AIRBAND_ACTIVITY_THRESHOLD_SNR_DB,
                        "detected_utc": int(time.time()),
                        "audio_url": "/api/airband/scan/last_audio.wav",
                    }
                    with state_lock:
                        runtime_state["airband_last_detection"] = detection
                        runtime_state["airband_scan_state"] = "activity_detected"
                    detection_found = True
                    break
    except Exception as exc:
        with state_lock:
            runtime_state["airband_scan_error"] = str(exc)
            runtime_state["airband_scan_state"] = "error"
    finally:
        with state_lock:
            if airband_scan_stop_event.is_set():
                runtime_state["airband_scan_state"] = "stopped"
            elif not detection_found and runtime_state["airband_scan_state"] != "error":
                runtime_state["airband_scan_state"] = "idle"
            runtime_state["airband_scan_running"] = False
        if receiver_lock.locked():
            receiver_lock.release()
        airband_scan_thread = None


def simulated_airband_tone_wav() -> bytes:
    # Obvious non-received test tone encoded as a short WAV block.
    pcm_data = bytearray()
    total_samples = int(AUDIO_RATE_HZ * 2.4)
    for sample_index in range(total_samples):
        time_seconds = sample_index / AUDIO_RATE_HZ
        burst_index = int(time_seconds / 0.30)
        active = burst_index % 2 == 0
        frequency_hz = 750.0 if (burst_index // 2) % 2 == 0 else 1050.0
        amplitude = 0.22 if active else 0.0
        value = int(amplitude * 32767.0 * math.sin(2.0 * math.pi * frequency_hz * time_seconds))
        pcm_data.extend(struct.pack("<h", value))
    return wav_block_from_pcm(bytes(pcm_data))


def set_airband_test_state(**updates: object) -> None:
    with state_lock:
        runtime_state.update(updates)


def airband_test_wait(seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if airband_test_stop_event.wait(timeout=min(0.10, max(0.0, deadline - time.monotonic()))):
            return False
        if airband_test_command_event.is_set():
            return True
    return True


def airband_test_get_command() -> str | None:
    with state_lock:
        command = runtime_state.get("airband_test_command")
        runtime_state["airband_test_command"] = None
    airband_test_command_event.clear()
    return command if isinstance(command, str) else None


def airband_test_worker(channels: list[dict]) -> None:
    global airband_test_thread

    simulated_index = 2 if len(channels) > 2 else 0
    cycle = 0
    try:
        while not airband_test_stop_event.is_set():
            cycle += 1
            set_airband_test_state(
                airband_test_cycle=cycle,
                airband_test_state="scanning",
                airband_test_active_channel=None,
                airband_test_silence_remaining=None,
                airband_test_hold=False,
                airband_test_message="SIMULATED: Scanning test channels.",
            )

            detected_channel = None
            for index, channel in enumerate(channels):
                if airband_test_stop_event.is_set():
                    break
                set_airband_test_state(
                    airband_test_current_channel=channel,
                    airband_test_message=f"SIMULATED: Scanning {channel['frequency_mhz']:.3f} MHz.",
                )
                if not airband_test_wait(0.70):
                    break
                command = airband_test_get_command()
                if command == "skip":
                    continue
                if index == simulated_index:
                    detected_channel = channel
                    break

            if airband_test_stop_event.is_set():
                break
            if detected_channel is None:
                continue

            with state_lock:
                event_id = int(runtime_state.get("airband_test_event_id", 0)) + 1
            set_airband_test_state(
                airband_test_event_id=event_id,
                airband_test_state="listening_simulated_activity",
                airband_test_current_channel=detected_channel,
                airband_test_active_channel=detected_channel,
                airband_test_message=(
                    f"SIMULATED ACTIVITY: Listening on {detected_channel['frequency_mhz']:.3f} MHz."
                ),
            )

            skipped = False
            activity_end = time.monotonic() + 3.0
            while time.monotonic() < activity_end and not airband_test_stop_event.is_set():
                airband_test_wait(0.10)
                command = airband_test_get_command()
                if command == "hold":
                    set_airband_test_state(airband_test_hold=True)
                elif command == "skip":
                    skipped = True
                    break

            if airband_test_stop_event.is_set():
                break
            if skipped:
                set_airband_test_state(
                    airband_test_state="scanning",
                    airband_test_message="SIMULATED: Channel skipped; resuming scan.",
                )
                continue

            remaining = 7
            while remaining > 0 and not airband_test_stop_event.is_set():
                with state_lock:
                    held = bool(runtime_state.get("airband_test_hold"))
                if held:
                    set_airband_test_state(
                        airband_test_state="held",
                        airband_test_silence_remaining=remaining,
                        airband_test_message=(
                            f"SIMULATED HOLD: Remaining on {detected_channel['frequency_mhz']:.3f} MHz."
                        ),
                    )
                    airband_test_wait(0.20)
                    command = airband_test_get_command()
                    if command == "skip":
                        remaining = 0
                        break
                    if command == "resume":
                        set_airband_test_state(airband_test_hold=False)
                    continue

                set_airband_test_state(
                    airband_test_state="silence_countdown",
                    airband_test_silence_remaining=remaining,
                    airband_test_message=(
                        f"SIMULATED SILENCE: Resuming scan in {remaining} seconds."
                    ),
                )
                if not airband_test_wait(1.0):
                    break
                command = airband_test_get_command()
                if command == "hold":
                    set_airband_test_state(airband_test_hold=True)
                elif command == "skip":
                    remaining = 0
                    break
                remaining -= 1

            if airband_test_stop_event.is_set():
                break
            set_airband_test_state(
                airband_test_state="scanning",
                airband_test_silence_remaining=None,
                airband_test_hold=False,
                airband_test_message="SIMULATED: Silence interval complete; scan resumed.",
            )
    finally:
        set_airband_test_state(
            airband_test_running=False,
            airband_test_state="stopped",
            airband_test_silence_remaining=None,
            airband_test_hold=False,
            airband_test_message="SIMULATED: Test scan stopped.",
        )
        airband_test_thread = None


def clear_pi_trail_history() -> dict:
    cleared_utc_ms = int(time.time() * 1000)
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)

    control = {
        "cleared_utc_ms": cleared_utc_ms,
        "cleared_utc": int(time.time()),
    }
    empty_history = {
        "updated_utc": int(time.time()),
        "retention_minutes": 240,
        "source": "readsb_pi_background_collector",
        "cleared_utc_ms": cleared_utc_ms,
        "trails": {},
    }

    control_temp = TRAIL_CONTROL_PATH.with_suffix(".json.tmp")
    control_temp.write_text(json.dumps(control, indent=2) + "\n", encoding="utf-8")
    control_temp.replace(TRAIL_CONTROL_PATH)

    history_temp = TRAIL_HISTORY_PATH.with_suffix(".json.tmp")
    history_temp.write_text(json.dumps(empty_history, separators=(",", ":")) + "\n", encoding="utf-8")
    history_temp.replace(TRAIL_HISTORY_PATH)

    return {
        "cleared": True,
        "cleared_utc_ms": cleared_utc_ms,
        "message": "Pi-stored trail history cleared. New post-clear movement will be collected.",
    }


def safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass

def live_available_samples() -> int:
    try:
        return max(0, (LIVE_WAV_PATH.stat().st_size - 44) // 2)
    except FileNotFoundError:
        return 0

def release_live_resources_locked() -> None:
    global live_process, live_log_handle, live_holds_receiver_lock
    live_process = None
    if live_log_handle is not None:
        live_log_handle.close()
        live_log_handle = None
    if live_holds_receiver_lock:
        receiver_lock.release()
        live_holds_receiver_lock = False

def refresh_live_process_locked() -> bool:
    global live_process
    if live_process is None:
        return False
    return_code = live_process.poll()
    if return_code is None:
        return True
    if return_code != 0:
        runtime_state["live_error"] = f"Native NOAA receiver exited with code {return_code}."
    release_live_resources_locked()
    return False

def build_status() -> dict:
    aircraft_data = read_json(AIRCRAFT_JSON)
    aircraft = aircraft_data.get("aircraft", [])
    if not isinstance(aircraft, list):
        aircraft = []
    positioned = sum(
        1 for record in aircraft
        if isinstance(record, dict) and record.get("lat") is not None and record.get("lon") is not None
    )
    with state_lock:
        live_running = refresh_live_process_locked()
        state = dict(runtime_state)
    receiver_location = read_receiver_location()
    saved_noaa_selection = read_saved_noaa_selection()
    return {
        "service": "rtl-pi-api",
        "readsb_json_available": AIRCRAFT_JSON.exists(),
        "messages": aircraft_data.get("messages", 0),
        "aircraft_count": len(aircraft),
        "aircraft_with_position": positioned,
        "audio_busy": receiver_lock.locked(),
        "audio_mode": "noaa_live" if live_running else "idle",
        "live_audio_running": live_running,
        "live_audio_available_samples": live_available_samples() if live_running else 0,
        "audio_receiver_serial": AUDIO_SERIAL,
        "noaa_station": selected_noaa_station,
        "noaa_frequency_hz": selected_noaa_frequency_hz,
        "configured_noaa_station": NOAA_STATION,
        "configured_noaa_frequency_hz": NOAA_FREQ_HZ,
        "saved_noaa_selection_available": saved_noaa_selection is not None,
        "saved_noaa_frequency_hz": saved_noaa_selection.get("frequency_hz") if saved_noaa_selection else None,
        "saved_noaa_station": saved_noaa_selection.get("station") if saved_noaa_selection else None,
        "rf_gain_db": RF_GAIN_DB,
        "audio_output_gain": AUDIO_OUTPUT_GAIN,
        "receiver_location_configured": receiver_location is not None,
        "receiver_location": receiver_location,
        **state,
    }

def wav_block_from_pcm(pcm_data: bytes) -> bytes:
    data_size = len(pcm_data)
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1, 1,
        AUDIO_RATE_HZ, AUDIO_RATE_HZ * 2, 2, 16, b"data", data_size
    ) + pcm_data

class Handler(BaseHTTPRequestHandler):
    server_version = "RTL-Pi-API/0.2"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"{self.client_address[0]} [{self.log_date_time_string()}] {format_string % args}", flush=True)

    def send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK,
                   extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(payload, indent=2).encode("utf-8"), "application/json; charset=utf-8", status)

    def read_request_json(self) -> dict | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def send_existing_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_json({"error": f"File not found: {path}"}, HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(body, content_type)

    def capture_noaa_audio(self, seconds: int) -> None:
        if not receiver_lock.acquire(blocking=False):
            self.send_json({"error": "Audio receiver is already in use. Stop live listening first."}, HTTPStatus.CONFLICT)
            return
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            safe_unlink(CAPTURE_WAV_PATH)
            command = [str(AUDIO_BINARY), "--serial", AUDIO_SERIAL, "--freq-hz", str(selected_noaa_frequency_hz),
                       "--seconds", str(seconds), "--gain-db", RF_GAIN_DB, "--audio-gain", AUDIO_OUTPUT_GAIN,
                       "--wav-output", str(CAPTURE_WAV_PATH)]
            result = subprocess.run(command, text=True, capture_output=True, timeout=seconds + 20, check=False)
            if result.returncode != 0 or not CAPTURE_WAV_PATH.exists():
                error_text = result.stderr.strip() or result.stdout.strip() or "Native receiver failed."
                with state_lock:
                    runtime_state["last_capture_error"] = error_text
                self.send_json({"error": "NOAA audio capture failed.", "details": error_text},
                               HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            with state_lock:
                runtime_state["last_capture_time"] = int(time.time())
                runtime_state["last_capture_seconds"] = seconds
                runtime_state["last_capture_error"] = None
            self.send_existing_file(CAPTURE_WAV_PATH, "audio/wav")
        except subprocess.TimeoutExpired:
            with state_lock:
                runtime_state["last_capture_error"] = "Capture timed out."
            self.send_json({"error": "NOAA capture timed out."}, HTTPStatus.GATEWAY_TIMEOUT)
        finally:
            receiver_lock.release()

    def start_airband_activity_scan(self, scope: str = "priority", watch_frequency_hz: int | None = None) -> None:
        global airband_scan_thread

        location = read_receiver_location()
        if location is None:
            self.send_json(
                {"error": "Set the receiver location before starting Airband Scan."},
                HTTPStatus.CONFLICT,
            )
            return

        nearby = nearby_airband_channels(location)
        if watch_frequency_hz is not None:
            channels = [
                channel for channel in nearby.get("channels", [])
                if channel["frequency_hz"] == watch_frequency_hz
            ]
            effective_scope = "single_channel_watch"
        else:
            channels, effective_scope = select_airband_scan_channels(nearby.get("channels", []), scope)
        if not channels:
            self.send_json(
                {"error": "No nearby Airband channels are available for scanning."},
                HTTPStatus.CONFLICT,
            )
            return

        with state_lock:
            if runtime_state["airband_scan_running"]:
                self.send_json({"started": False, **build_status()})
                return

        if not receiver_lock.acquire(blocking=False):
            self.send_json(
                {"error": "Audio receiver is in use. Stop NOAA listening or AM playback first."},
                HTTPStatus.CONFLICT,
            )
            return

        safe_unlink(AIRBAND_DETECTED_WAV_PATH)
        safe_unlink(AIRBAND_BEST_WAV_PATH)
        airband_scan_stop_event.clear()
        with state_lock:
            runtime_state["airband_scan_running"] = True
            runtime_state["airband_scan_state"] = "starting"
            runtime_state["airband_scan_cycles"] = 0
            runtime_state["airband_channels_scanned"] = 0
            runtime_state["airband_current_channel"] = None
            runtime_state["airband_last_measurement_dbfs"] = None
            runtime_state["airband_last_signal_snr_db"] = None
            runtime_state["airband_last_detection"] = None
            runtime_state["airband_best_candidate"] = None
            runtime_state["airband_scan_scope"] = effective_scope
            runtime_state["airband_watch_frequency_hz"] = watch_frequency_hz
            runtime_state["airband_scan_error"] = None

        airband_scan_thread = threading.Thread(
            target=airband_scan_worker,
            args=(channels,),
            daemon=True,
            name="airband-activity-scan",
        )
        airband_scan_thread.start()
        self.send_json({
            "started": True,
            "channel_count": len(channels),
            "scan_scope": effective_scope,
            "duplicate_records_removed": nearby.get("duplicate_records_removed", 0),
            "priority_channel_count": sum(1 for item in channels if airband_channel_scan_priority(item)[0] < 2),
            "activity_threshold_snr_db": AIRBAND_ACTIVITY_THRESHOLD_SNR_DB,
            **build_status(),
        })

    def stop_airband_activity_scan(self) -> None:
        airband_scan_stop_event.set()
        with state_lock:
            running = bool(runtime_state["airband_scan_running"])
        self.send_json({"stopping": running, **build_status()})

    def start_airband_test_mode(self) -> None:
        global airband_test_thread

        location = read_receiver_location()
        if location is None:
            self.send_json(
                {"error": "Set the receiver location before starting Airband Scanner Test Mode."},
                HTTPStatus.CONFLICT,
            )
            return

        nearby = nearby_airband_channels(location)
        channels = nearby.get("channels", [])[:8]
        if not channels:
            self.send_json(
                {"error": "No nearby channels are available for Airband Scanner Test Mode."},
                HTTPStatus.CONFLICT,
            )
            return

        with state_lock:
            if runtime_state.get("airband_test_running"):
                self.send_json({"started": False, "already_running": True, **build_status()})
                return
            runtime_state["airband_test_running"] = True
            runtime_state["airband_test_state"] = "starting"
            runtime_state["airband_test_cycle"] = 0
            runtime_state["airband_test_current_channel"] = None
            runtime_state["airband_test_active_channel"] = None
            runtime_state["airband_test_silence_remaining"] = None
            runtime_state["airband_test_hold"] = False
            runtime_state["airband_test_command"] = None
            runtime_state["airband_test_message"] = "SIMULATED: Starting Airband Scanner Test Mode."

        airband_test_stop_event.clear()
        airband_test_command_event.clear()
        airband_test_thread = threading.Thread(
            target=airband_test_worker,
            args=(channels,),
            daemon=True,
            name="airband-test-mode",
        )
        airband_test_thread.start()
        self.send_json(
            {
                "started": True,
                "simulated": True,
                "test_channel_count": len(channels),
                "silence_resume_seconds": 7,
                **build_status(),
            }
        )

    def command_airband_test_mode(self, command: str) -> None:
        with state_lock:
            if not runtime_state.get("airband_test_running"):
                self.send_json({"error": "Airband Scanner Test Mode is not running."}, HTTPStatus.CONFLICT)
                return
            runtime_state["airband_test_command"] = command
        airband_test_command_event.set()
        self.send_json({"accepted": True, "command": command, **build_status()})

    def stop_airband_test_mode(self) -> None:
        airband_test_stop_event.set()
        airband_test_command_event.set()
        self.send_json({"stopping": True, **build_status()})

    def capture_airband_audio(self, frequency_hz: int, seconds: int) -> None:
        location = read_receiver_location()
        if location is None:
            self.send_json(
                {"error": "Set the receiver location before listening to Airband channels."},
                HTTPStatus.CONFLICT,
            )
            return

        available = nearby_airband_channels(location)
        channel = next(
            (item for item in available["channels"] if item["frequency_hz"] == frequency_hz),
            None,
        )
        if channel is None:
            self.send_json(
                {"error": "Requested Airband channel is not in the configured nearby channel list."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not receiver_lock.acquire(blocking=False):
            self.send_json(
                {"error": "Audio receiver is currently in use. Stop NOAA listening first."},
                HTTPStatus.CONFLICT,
            )
            return

        try:
            safe_unlink(AIRBAND_CAPTURE_WAV_PATH)
            command = [
                str(AIRBAND_BINARY),
                "--serial", AUDIO_SERIAL,
                "--freq-hz", str(frequency_hz),
                "--seconds", str(seconds),
                "--gain-db", RF_GAIN_DB,
                "--audio-gain", AIRBAND_AUDIO_OUTPUT_GAIN,
                "--wav-output", str(AIRBAND_CAPTURE_WAV_PATH),
            ]
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=seconds + 20,
                check=False,
            )
            if result.returncode != 0 or not AIRBAND_CAPTURE_WAV_PATH.exists():
                error_text = result.stderr.strip() or result.stdout.strip() or "Native AM receiver failed."
                self.send_json(
                    {"error": "Airband AM capture failed.", "details": error_text},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self.send_existing_file(AIRBAND_CAPTURE_WAV_PATH, "audio/wav")
        finally:
            receiver_lock.release()

    def auto_select_and_start_noaa(self, force_rescan: bool = False) -> None:
        global selected_noaa_frequency_hz, selected_noaa_station

        if not force_rescan:
            saved_selection = read_saved_noaa_selection()
            if saved_selection is not None:
                selected_noaa_frequency_hz = int(saved_selection["frequency_hz"])
                selected_noaa_station = str(saved_selection["station"])
                with state_lock:
                    runtime_state["last_noaa_survey"] = saved_selection.get("survey")
                    runtime_state["last_noaa_survey_time"] = saved_selection.get("saved_utc")
                self.start_live_noaa()
                return

        if not receiver_lock.acquire(blocking=False):
            self.send_json(
                {"error": "Audio receiver is currently busy. Stop listening before rescanning NOAA."},
                HTTPStatus.CONFLICT,
            )
            return

        survey_path = OUTPUT_DIR / "noaa_auto_select_results.json"
        survey = {}
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            safe_unlink(survey_path)
            command = [
                str(SURVEY_BINARY),
                "--serial", AUDIO_SERIAL,
                "--seconds", str(SURVEY_SECONDS),
                "--gain-db", RF_GAIN_DB,
                "--json-output", str(survey_path),
            ]
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=(SURVEY_SECONDS * 7) + 30,
                check=False,
            )
            if result.returncode != 0 or not survey_path.exists():
                error_text = result.stderr.strip() or result.stdout.strip() or "NOAA survey failed."
                self.send_json(
                    {"error": "NOAA auto-select survey failed.", "details": error_text},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            survey = read_json(survey_path)
            best_frequency = survey.get("best_frequency_hz")
            if not isinstance(best_frequency, int) or best_frequency <= 0:
                self.send_json(
                    {"error": "NOAA survey did not report a valid best frequency."},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            selected_noaa_frequency_hz = best_frequency
            selected_noaa_station = f"AUTO SELECT — {best_frequency / 1000000.0:.3f} MHz"
            save_noaa_selection(selected_noaa_frequency_hz, selected_noaa_station, survey)
            with state_lock:
                runtime_state["last_noaa_survey"] = survey
                runtime_state["last_noaa_survey_time"] = int(time.time())
        finally:
            receiver_lock.release()

        self.start_live_noaa()

    def start_live_noaa(self) -> None:
        global live_process, live_log_handle, live_holds_receiver_lock
        with state_lock:
            if refresh_live_process_locked():
                self.send_json({"started": False, "already_running": True, **build_status()})
                return
            if not receiver_lock.acquire(blocking=False):
                self.send_json({"error": "Audio receiver is currently busy."}, HTTPStatus.CONFLICT)
                return
            live_holds_receiver_lock = True
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            safe_unlink(LIVE_WAV_PATH)
            safe_unlink(LIVE_LOG_PATH)
            command = [str(AUDIO_BINARY), "--serial", AUDIO_SERIAL, "--freq-hz", str(selected_noaa_frequency_hz),
                       "--seconds", "3600", "--gain-db", RF_GAIN_DB, "--audio-gain", AUDIO_OUTPUT_GAIN,
                       "--wav-output", str(LIVE_WAV_PATH)]
            try:
                live_log_handle = LIVE_LOG_PATH.open("w", encoding="utf-8")
                live_process = subprocess.Popen(command, stdout=live_log_handle, stderr=subprocess.STDOUT, text=True)
            except OSError as exc:
                runtime_state["live_error"] = str(exc)
                release_live_resources_locked()
                self.send_json({"error": "Unable to start live NOAA receiver.", "details": str(exc)},
                               HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            runtime_state["live_start_time"] = int(time.time())
            runtime_state["live_stop_time"] = None
            runtime_state["live_error"] = None
        self.send_json({"started": True, **build_status()})

    def stop_live_noaa(self) -> None:
        with state_lock:
            process = live_process if refresh_live_process_locked() else None
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        with state_lock:
            if live_process is not None:
                release_live_resources_locked()
            runtime_state["live_stop_time"] = int(time.time())
        self.send_json({"stopped": True, **build_status()})

    def send_live_audio_block(self, from_sample: int, requested_samples: int) -> None:
        with state_lock:
            running = refresh_live_process_locked()
        if not running:
            self.send_json({"error": "Live NOAA audio is not running."}, HTTPStatus.CONFLICT)
            return
        available_samples = live_available_samples()
        if from_sample > available_samples:
            self.send_json({"error": "Requested cursor is beyond available audio.",
                            "available_samples": available_samples},
                           HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        sample_count = min(requested_samples, available_samples - from_sample)
        if sample_count < 1200:
            self.send_bytes(b"", "audio/wav", HTTPStatus.NO_CONTENT,
                            {"X-Audio-Available-Samples": str(available_samples)})
            return
        with LIVE_WAV_PATH.open("rb") as audio_file:
            audio_file.seek(44 + from_sample * 2)
            pcm_data = audio_file.read(sample_count * 2)
        actual_samples = len(pcm_data) // 2
        if actual_samples == 0:
            self.send_bytes(b"", "audio/wav", HTTPStatus.NO_CONTENT)
            return
        self.send_bytes(wav_block_from_pcm(pcm_data), "audio/wav",
                        extra_headers={"X-Source-Samples": str(actual_samples),
                                       "X-Audio-From-Sample": str(from_sample),
                                       "X-Audio-Available-Samples": str(available_samples)})

    def do_POST(self) -> None:
        request = urlparse(self.path)

        if request.path == "/api/airband/test/start":
            self.start_airband_test_mode()
            return

        if request.path == "/api/airband/test/stop":
            self.stop_airband_test_mode()
            return

        if request.path == "/api/airband/test/hold":
            self.command_airband_test_mode("hold")
            return

        if request.path == "/api/airband/test/skip":
            self.command_airband_test_mode("skip")
            return

        if request.path == "/api/airband/test/resume":
            self.command_airband_test_mode("resume")
            return


        if request.path == "/api/trails/clear":
            self.send_json(clear_pi_trail_history())
            return

        if request.path == "/api/diagnostics/airlabs/cache/clear":
            self.send_json(clear_airlabs_route_cache())
            return

        if request.path == "/api/diagnostics/airlabs/settings":
            payload = self.read_request_json()
            if payload is None:
                self.send_json({"error": "A JSON settings body is required."}, HTTPStatus.BAD_REQUEST)
                return
            if bool(payload.get("clear")):
                self.send_json(save_airlabs_diagnostic_key(""))
                return
            api_key = str(payload.get("api_key", "")).strip()
            if len(api_key) < 8:
                self.send_json({"error": "Enter a valid AirLabs API key before saving."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(save_airlabs_diagnostic_key(api_key))
            return

        if request.path == "/api/settings/airband-radius":
            payload = self.read_request_json()
            if payload is None:
                self.send_json({"error": "A JSON Airband radius body is required."}, HTTPStatus.BAD_REQUEST)
                return
            try:
                radius_miles = float(payload.get("airband_radius_miles"))
            except (TypeError, ValueError):
                self.send_json({"error": "Airband radius must be numeric."}, HTTPStatus.BAD_REQUEST)
                return
            if radius_miles <= 0.0 or radius_miles > 500.0:
                self.send_json({"error": "Airband radius must be greater than 0 and no more than 500 miles."}, HTTPStatus.BAD_REQUEST)
                return
            updated_location = save_airband_radius_only(radius_miles)
            if updated_location is None:
                self.send_json({"error": "Set the receiver location before changing Airband scan radius."}, HTTPStatus.CONFLICT)
                return
            self.send_json({
                "saved": True,
                "receiver_location": updated_location,
                "noaa_selection_preserved": read_saved_noaa_selection() is not None,
            })
            return

        if request.path == "/api/settings/receiver":
            payload = self.read_request_json()
            if payload is None:
                self.send_json({"error": "A JSON receiver-location body is required."}, HTTPStatus.BAD_REQUEST)
                return
            location, error = validate_receiver_location(payload)
            if error is not None or location is None:
                self.send_json({"error": error}, HTTPStatus.BAD_REQUEST)
                return
            save_receiver_location(location)
            self.send_json({"saved": True, "receiver_location": location})
            return

        if request.path == "/api/airband/scan/activity/start":
            parameters = parse_qs(request.query)
            scope = parameters.get("scope", ["priority"])[0]
            if scope not in ("continuous", "priority", "all"):
                scope = "priority"
            try:
                frequency_text = parameters.get("frequency_hz", [""])[0]
                watch_frequency_hz = int(frequency_text) if frequency_text else None
            except ValueError:
                self.send_json({"error": "Invalid watch frequency."}, HTTPStatus.BAD_REQUEST)
                return
            self.start_airband_activity_scan(scope, watch_frequency_hz)
            return

        if request.path == "/api/airband/scan/activity/stop":
            self.stop_airband_activity_scan()
            return

        if request.path == "/api/airband/scan/start":
            location = read_receiver_location()
            if location is None:
                self.send_json(
                    {
                        "error": "Set the receiver location before starting Airband Scan.",
                        "receiver_location_required": True,
                    },
                    HTTPStatus.CONFLICT,
                )
                return
            self.send_json(
                {
                    "error": "Airband scanning backend is the next development milestone.",
                    "receiver_location_required": False,
                    "receiver_location": location,
                },
                HTTPStatus.NOT_IMPLEMENTED,
            )
            return

        if request.path == "/api/noaa/auto/rescan":
            self.auto_select_and_start_noaa(force_rescan=True)
            return

        if request.path == "/api/noaa/auto/start":
            self.auto_select_and_start_noaa()
            return

        if request.path == "/api/noaa/live/start":
            self.start_live_noaa()
            return
        if request.path == "/api/noaa/live/stop":
            self.stop_live_noaa()
            return
        self.send_json({"error": "POST endpoint not found."}, HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        # AIRLABS_ROUTE2_IATA_VARIANTS_BACKEND_PATCH_V1 route
        import urllib.parse as _airlabs_route2_urlparse
        _airlabs_route2_parsed = _airlabs_route2_urlparse.urlparse(self.path)
        if _airlabs_route2_parsed.path == "/api/diagnostics/airlabs/route2":
            payload, status = _airlabs_route2_response(_airlabs_route2_parsed.query)
            self.send_json(payload, status)
            return

        # LOCAL_TAR1090_AIRCRAFT_CACHE_FALLBACK_SAFE_V2 route
        import urllib.parse as _local_tar1090_urlparse
        _local_tar1090_parsed = _local_tar1090_urlparse.urlparse(self.path)
        if _local_tar1090_parsed.path == "/api/aircraft/local":
            payload, status = _local_tar1090_lookup_response(_local_tar1090_parsed.query)
            self.send_json(payload, status)
            return

                # WEB_SPLIT_STATIC_ASSET_ROUTE_PATCH_V2
        import urllib.parse as _asset_urlparse
        import pathlib as _asset_pathlib

        _asset_parsed = _asset_urlparse.urlparse(self.path)
        if _asset_parsed.path in ("/app.css", "/app.js"):
            _asset_name = _asset_parsed.path.lstrip("/")
            _asset_candidates = [
                _asset_pathlib.Path("/opt/rtl-pi-adsb-tracker/web") / _asset_name,
                _asset_pathlib.Path(__file__).resolve().parent.parent / "web" / _asset_name,
                _asset_pathlib.Path(__file__).resolve().parent / "web" / _asset_name,
                _asset_pathlib.Path.cwd() / "web" / _asset_name,
            ]

            for _asset_file in _asset_candidates:
                try:
                    if _asset_file.exists():
                        _asset_data = _asset_file.read_bytes()
                        _asset_type = "text/css; charset=utf-8" if _asset_name.endswith(".css") else "application/javascript; charset=utf-8"
                        self.send_response(HTTPStatus.OK)
                        self.send_header("Content-Type", _asset_type)
                        self.send_header("Content-Length", str(len(_asset_data)))
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        self.wfile.write(_asset_data)
                        return
                except Exception as _asset_error:
                    self.send_json({
                        "error": "Static asset read failed",
                        "asset": _asset_name,
                        "path": str(_asset_file),
                        "detail": str(_asset_error),
                    }, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return

            self.send_json({
                "error": "Static asset not found",
                "asset": _asset_name,
                "checked": [str(candidate) for candidate in _asset_candidates],
            }, HTTPStatus.NOT_FOUND)
            return

        request = urlparse(self.path)
        if request.path in ("/", "/index.html"):
            self.send_existing_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return
        if request.path == "/api/airband/scan/status":
            self.send_json(build_status())
            return

        if request.path == "/api/airband/scan/last_audio.wav":
            self.send_existing_file(AIRBAND_DETECTED_WAV_PATH, "audio/wav")
            return

        if request.path == "/api/airband/scan/best_audio.wav":
            self.send_existing_file(AIRBAND_BEST_WAV_PATH, "audio/wav")
            return

        if request.path == "/api/airband/channels":
            location = read_receiver_location()
            if location is None:
                self.send_json(
                    {
                        "error": "Set the receiver location before loading nearby Airband channels.",
                        "receiver_location_required": True,
                    },
                    HTTPStatus.CONFLICT,
                )
                return
            result = nearby_airband_channels(location)
            if not result["data_available"]:
                self.send_json(
                    {
                        "error": "Airband frequency data has not been deployed.",
                        "data_available": False,
                        "receiver_location": location,
                    },
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self.send_json(result)
            return

        if request.path == "/api/settings/receiver":
            location = read_receiver_location()
            self.send_json(
                {
                    "configured": location is not None,
                    "receiver_location": location,
                    "default_airband_radius_miles": 100,
                }
            )
            return

        if request.path == "/api/airband/test/status":
            self.send_json(build_status())
            return

        if request.path == "/api/airband/test/audio.wav":
            self.send_bytes(simulated_airband_tone_wav(), "audio/wav")
            return

        if request.path == "/api/trails/history":
            history = read_json(TRAIL_HISTORY_PATH)
            if not history:
                history = {
                    "updated_utc": None,
                    "retention_minutes": 240,
                    "source": "readsb_pi_background_collector",
                    "trails": {},
                }
            self.send_json(history)
            return

        if request.path == "/api/diagnostics/airlabs/status":
            self.send_json(airlabs_diagnostic_status())
            return

        if request.path == "/api/diagnostics/airlabs/route":
            parameters = parse_qs(request.query)
            flight = parameters.get("flight", [""])[0]
            self.send_json(test_airlabs_route_diagnostic(flight))
            return

        if request.path == "/api/status":
            self.send_json(build_status())
            return
        if request.path == "/api/aircraft.json":
            self.send_existing_file(AIRCRAFT_JSON, "application/json")
            return
        if request.path == "/api/readsb/status.json":
            self.send_existing_file(READSB_STATUS_JSON, "application/json")
            return
        if request.path == "/api/airband/capture.wav":
            parameters = parse_qs(request.query)
            try:
                frequency_hz = int(parameters.get("frequency_hz", ["0"])[0])
                seconds = int(parameters.get("seconds", ["10"])[0])
            except ValueError:
                self.send_json({"error": "Invalid Airband capture parameters."}, HTTPStatus.BAD_REQUEST)
                return
            self.capture_airband_audio(frequency_hz, min(max(seconds, 2), 60))
            return

        if request.path == "/api/noaa/capture.wav":
            parameters = parse_qs(request.query)
            try:
                seconds = int(parameters.get("seconds", ["10"])[0])
            except ValueError:
                seconds = 10
            self.capture_noaa_audio(min(max(seconds, 2), 60))
            return
        if request.path == "/api/noaa/live/audio.wav":
            parameters = parse_qs(request.query)
            try:
                from_sample = max(0, int(parameters.get("from", ["0"])[0]))
                requested_samples = int(parameters.get("samples", ["12000"])[0])
            except ValueError:
                self.send_json({"error": "Invalid audio cursor."}, HTTPStatus.BAD_REQUEST)
                return
            self.send_live_audio_block(from_sample, min(max(requested_samples, 1200), 48000))
            return
        self.send_json({"error": "Endpoint not found."}, HTTPStatus.NOT_FOUND)

def stop_child_on_shutdown() -> None:
    with state_lock:
        process = live_process
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler)
    print(f"RTL Pi API listening on http://{BIND_ADDRESS}:{PORT}", flush=True)
    print(f"Aircraft JSON source: {AIRCRAFT_JSON}", flush=True)
    print(f"NOAA source: {NOAA_STATION} at {NOAA_FREQ_HZ} Hz using receiver {AUDIO_SERIAL}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_child_on_shutdown()
        server.server_close()


# --- Airband normal scanner patch v1 ---
# This block is intentionally self-contained. It wraps the existing v3.0.0
# activity-scan implementation to behave like a normal scanner: search, lock,
# stream live browser audio, skip, block, and resume after squelch silence.

AIRBAND_SETTINGS_PATH = SETTINGS_DIR / "airband_scanner_settings.json"
AIRBAND_BLOCKED_PATH = SETTINGS_DIR / "airband_blocked_frequencies.json"
AIRBAND_LIVE_WAV_PATH = OUTPUT_DIR / "airband_live_source.wav"
AIRBAND_LIVE_LOG_PATH = OUTPUT_DIR / "airband_live_receiver.log"

airband_skip_event = threading.Event()
airband_live_process: subprocess.Popen[str] | None = None
airband_live_log_handle = None

runtime_state.setdefault("airband_locked_channel", None)
runtime_state.setdefault("airband_live_audio_running", False)
runtime_state.setdefault("airband_live_available_samples", 0)
runtime_state.setdefault("airband_silence_remaining", None)
runtime_state.setdefault("airband_lock_reason", None)
runtime_state.setdefault("airband_scanner_message", "Airband scanner idle.")

DEFAULT_AIRBAND_SCANNER_SETTINGS = {
    "snr_threshold_db": AIRBAND_ACTIVITY_THRESHOLD_SNR_DB,
    "squelch_dbfs": -38.0,
    "rf_gain_db": float(RF_GAIN_DB),
    "sample_ms": 250,
    "silence_resume_seconds": 7.0,
}


def _airband_clamp_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _airband_clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def read_airband_scanner_settings() -> dict:
    saved = read_json(AIRBAND_SETTINGS_PATH)
    if not isinstance(saved, dict):
        saved = {}
    defaults = DEFAULT_AIRBAND_SCANNER_SETTINGS
    return {
        "snr_threshold_db": _airband_clamp_float(saved.get("snr_threshold_db"), float(defaults["snr_threshold_db"]), -30.0, 60.0),
        "squelch_dbfs": _airband_clamp_float(saved.get("squelch_dbfs"), float(defaults["squelch_dbfs"]), -90.0, -5.0),
        "rf_gain_db": _airband_clamp_float(saved.get("rf_gain_db"), float(defaults["rf_gain_db"]), 0.0, 49.6),
        "sample_ms": _airband_clamp_int(saved.get("sample_ms"), int(defaults["sample_ms"]), 100, 2000),
        "silence_resume_seconds": _airband_clamp_float(saved.get("silence_resume_seconds"), float(defaults["silence_resume_seconds"]), 1.0, 30.0),
    }


def save_airband_scanner_settings(settings: dict) -> dict:
    current = read_airband_scanner_settings()
    current.update({
        "snr_threshold_db": _airband_clamp_float(settings.get("snr_threshold_db", current["snr_threshold_db"]), current["snr_threshold_db"], -30.0, 60.0),
        "squelch_dbfs": _airband_clamp_float(settings.get("squelch_dbfs", current["squelch_dbfs"]), current["squelch_dbfs"], -90.0, -5.0),
        "rf_gain_db": _airband_clamp_float(settings.get("rf_gain_db", current["rf_gain_db"]), current["rf_gain_db"], 0.0, 49.6),
        "sample_ms": _airband_clamp_int(settings.get("sample_ms", current["sample_ms"]), current["sample_ms"], 100, 2000),
        "silence_resume_seconds": _airband_clamp_float(settings.get("silence_resume_seconds", current["silence_resume_seconds"]), current["silence_resume_seconds"], 1.0, 30.0),
        "updated_utc": int(time.time()),
    })
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    temp = AIRBAND_SETTINGS_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    temp.replace(AIRBAND_SETTINGS_PATH)
    return current


def read_airband_blocked_frequencies() -> list[int]:
    saved = read_json(AIRBAND_BLOCKED_PATH)
    values = saved.get("blocked_frequencies_hz", []) if isinstance(saved, dict) else []
    blocked: list[int] = []
    if isinstance(values, list):
        for value in values:
            try:
                frequency_hz = int(value)
            except (TypeError, ValueError):
                continue
            if 118000000 <= frequency_hz <= 137000000 and frequency_hz not in blocked:
                blocked.append(frequency_hz)
    return sorted(blocked)


def save_airband_blocked_frequencies(frequencies_hz: list[int]) -> list[int]:
    clean = sorted({int(value) for value in frequencies_hz if 118000000 <= int(value) <= 137000000})
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    temp = AIRBAND_BLOCKED_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps({"blocked_frequencies_hz": clean, "updated_utc": int(time.time())}, indent=2) + "\n", encoding="utf-8")
    temp.replace(AIRBAND_BLOCKED_PATH)
    return clean


def airband_live_available_samples() -> int:
    try:
        return max(0, (AIRBAND_LIVE_WAV_PATH.stat().st_size - 44) // 2)
    except FileNotFoundError:
        return 0


def recent_airband_pcm_rms_dbfs(path: Path, seconds: float = 1.0) -> float:
    try:
        file_size = path.stat().st_size
        available_bytes = max(0, file_size - 44)
        if available_bytes < 2400:
            return -120.0
        samples_to_read = max(1200, int(AUDIO_RATE_HZ * seconds))
        bytes_to_read = min(available_bytes, samples_to_read * 2)
        with path.open("rb") as audio_file:
            audio_file.seek(44 + available_bytes - bytes_to_read)
            pcm_data = audio_file.read(bytes_to_read)
    except OSError:
        return -120.0

    count = len(pcm_data) // 2
    if count <= 0:
        return -120.0
    total_square = 0.0
    for (sample,) in struct.iter_unpack("<h", pcm_data[: count * 2]):
        normalized = float(sample) / 32768.0
        total_square += normalized * normalized
    if total_square <= 0.0:
        return -120.0
    return 20.0 * math.log10(math.sqrt(total_square / count))


def refresh_airband_live_process_locked() -> bool:
    global airband_live_process, airband_live_log_handle
    process = airband_live_process
    if process is None:
        runtime_state["airband_live_audio_running"] = False
        runtime_state["airband_live_available_samples"] = 0
        return False
    return_code = process.poll()
    if return_code is None:
        runtime_state["airband_live_audio_running"] = True
        runtime_state["airband_live_available_samples"] = airband_live_available_samples()
        return True
    if airband_live_log_handle is not None:
        airband_live_log_handle.close()
        airband_live_log_handle = None
    airband_live_process = None
    runtime_state["airband_live_audio_running"] = False
    runtime_state["airband_live_available_samples"] = 0
    runtime_state["airband_lock_reason"] = f"receiver_exited_{return_code}"
    return False


def stop_airband_live_process_locked() -> None:
    global airband_live_process, airband_live_log_handle
    process = airband_live_process
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if airband_live_log_handle is not None:
        airband_live_log_handle.close()
        airband_live_log_handle = None
    airband_live_process = None
    runtime_state["airband_live_audio_running"] = False
    runtime_state["airband_live_available_samples"] = 0


def start_airband_live_process_locked(channel: dict, settings: dict) -> None:
    global airband_live_process, airband_live_log_handle
    stop_airband_live_process_locked()
    safe_unlink(AIRBAND_LIVE_WAV_PATH)
    safe_unlink(AIRBAND_LIVE_LOG_PATH)
    command = [
        str(AIRBAND_BINARY),
        "--serial", AUDIO_SERIAL,
        "--freq-hz", str(channel["frequency_hz"]),
        "--seconds", "3600",
        "--gain-db", f"{settings['rf_gain_db']:.1f}",
        "--audio-gain", AIRBAND_AUDIO_OUTPUT_GAIN,
        "--wav-output", str(AIRBAND_LIVE_WAV_PATH),
    ]
    airband_live_log_handle = AIRBAND_LIVE_LOG_PATH.open("w", encoding="utf-8")
    airband_live_process = subprocess.Popen(command, stdout=airband_live_log_handle, stderr=subprocess.STDOUT, text=True)


def monitor_airband_locked_channel(channel: dict, settings: dict) -> str:
    silence_started: float | None = None
    reason = "receiver_exited"
    while not airband_scan_stop_event.is_set():
        with state_lock:
            running = refresh_airband_live_process_locked()
        if not running:
            break
        if airband_skip_event.is_set():
            airband_skip_event.clear()
            reason = "skipped"
            break

        available_samples = airband_live_available_samples()
        recent_rms = recent_airband_pcm_rms_dbfs(AIRBAND_LIVE_WAV_PATH, seconds=1.0)
        now = time.monotonic()
        if available_samples < AUDIO_RATE_HZ:
            remaining = None
        elif recent_rms >= float(settings["squelch_dbfs"]):
            silence_started = None
            remaining = None
        else:
            if silence_started is None:
                silence_started = now
            remaining = max(0.0, float(settings["silence_resume_seconds"]) - (now - silence_started))
            if remaining <= 0.0:
                reason = "silence_timeout"
                break

        with state_lock:
            runtime_state["airband_last_measurement_dbfs"] = round(recent_rms, 2)
            runtime_state["airband_silence_remaining"] = round(remaining, 1) if remaining is not None else None
            runtime_state["airband_live_available_samples"] = available_samples
            runtime_state["airband_scanner_message"] = (
                f"Listening on {channel['frequency_mhz']:.3f} MHz"
                if remaining is None
                else f"Silence countdown on {channel['frequency_mhz']:.3f} MHz: {remaining:.1f}s"
            )
        time.sleep(0.25)

    with state_lock:
        stop_airband_live_process_locked()
        runtime_state["airband_silence_remaining"] = None
        runtime_state["airband_lock_reason"] = reason
    return reason


def normal_airband_scan_worker(channels: list[dict]) -> None:
    global airband_scan_thread
    try:
        while not airband_scan_stop_event.is_set():
            settings = read_airband_scanner_settings()
            blocked = set(read_airband_blocked_frequencies())
            active_channels = [channel for channel in channels if int(channel["frequency_hz"]) not in blocked]

            with state_lock:
                runtime_state["airband_scan_cycles"] = int(runtime_state["airband_scan_cycles"]) + 1
                runtime_state["airband_scan_state"] = "searching"
                runtime_state["airband_locked_channel"] = None
                runtime_state["airband_silence_remaining"] = None
                runtime_state["airband_scanner_message"] = f"Scanning {len(active_channels)} Airband frequencies."

            if not active_channels:
                with state_lock:
                    runtime_state["airband_scan_state"] = "blocked_all"
                    runtime_state["airband_scanner_message"] = "All candidate Airband frequencies are blocked."
                time.sleep(1.0)
                continue

            for channel in active_channels:
                if airband_scan_stop_event.is_set():
                    break
                safe_unlink(AIRBAND_SCAN_SAMPLE_PATH)
                command = [
                    str(AIRBAND_BINARY),
                    "--serial", AUDIO_SERIAL,
                    "--freq-hz", str(channel["frequency_hz"]),
                    "--duration-ms", str(settings["sample_ms"]),
                    "--gain-db", f"{settings['rf_gain_db']:.1f}",
                    "--audio-gain", AIRBAND_AUDIO_OUTPUT_GAIN,
                    "--wav-output", str(AIRBAND_SCAN_SAMPLE_PATH),
                ]
                with state_lock:
                    runtime_state["airband_current_channel"] = channel
                    runtime_state["airband_scan_state"] = "searching"
                    runtime_state["airband_scanner_message"] = f"Scanning {channel['frequency_mhz']:.3f} MHz."

                result = subprocess.run(command, text=True, capture_output=True, timeout=(float(settings["sample_ms"]) / 1000.0) + 20, check=False)
                if result.returncode != 0:
                    with state_lock:
                        runtime_state["airband_scan_error"] = result.stderr.strip() or result.stdout.strip() or "AM scan sample failed."
                    continue

                rms_dbfs = pcm_wav_rms_dbfs(AIRBAND_SCAN_SAMPLE_PATH)
                signal_match = re.search(r"RF estimated SNR:\s+(-?[0-9]+(?:\.[0-9]+)?) dB", result.stdout)
                signal_snr_db = float(signal_match.group(1)) if signal_match else -30.0
                candidate = {
                    "channel": channel,
                    "audio_rms_dbfs": round(rms_dbfs, 2),
                    "rf_estimated_snr_db": round(signal_snr_db, 2),
                    "observed_utc": int(time.time()),
                    "audio_url": "/api/airband/scan/best_audio.wav",
                }

                with state_lock:
                    runtime_state["airband_channels_scanned"] = int(runtime_state["airband_channels_scanned"]) + 1
                    runtime_state["airband_current_channel"] = channel
                    runtime_state["airband_last_measurement_dbfs"] = round(rms_dbfs, 2)
                    runtime_state["airband_last_signal_snr_db"] = round(signal_snr_db, 2)
                    previous_best = runtime_state.get("airband_best_candidate")
                    if previous_best is None or signal_snr_db > float(previous_best.get("rf_estimated_snr_db", -999.0)):
                        shutil.copyfile(AIRBAND_SCAN_SAMPLE_PATH, AIRBAND_BEST_WAV_PATH)
                        runtime_state["airband_best_candidate"] = candidate

                detected = signal_snr_db >= float(settings["snr_threshold_db"]) and rms_dbfs >= float(settings["squelch_dbfs"])
                if not detected:
                    continue

                shutil.copyfile(AIRBAND_SCAN_SAMPLE_PATH, AIRBAND_DETECTED_WAV_PATH)
                detection = {
                    "channel": channel,
                    "audio_rms_dbfs": round(rms_dbfs, 2),
                    "rf_estimated_snr_db": round(signal_snr_db, 2),
                    "threshold_snr_db": settings["snr_threshold_db"],
                    "squelch_dbfs": settings["squelch_dbfs"],
                    "detected_utc": int(time.time()),
                    "audio_url": "/api/airband/scan/live/audio.wav",
                }
                with state_lock:
                    runtime_state["airband_last_detection"] = detection
                    runtime_state["airband_locked_channel"] = channel
                    runtime_state["airband_scan_state"] = "locked"
                    runtime_state["airband_lock_reason"] = "activity_detected"
                    runtime_state["airband_scanner_message"] = f"Activity detected; locked on {channel['frequency_mhz']:.3f} MHz."
                    start_airband_live_process_locked(channel, settings)

                monitor_airband_locked_channel(channel, settings)
                with state_lock:
                    if not airband_scan_stop_event.is_set():
                        runtime_state["airband_scan_state"] = "searching"
                        runtime_state["airband_locked_channel"] = None
                        runtime_state["airband_scanner_message"] = "Resuming Airband scan."

    except Exception as exc:
        with state_lock:
            runtime_state["airband_scan_error"] = str(exc)
            runtime_state["airband_scan_state"] = "error"
            runtime_state["airband_scanner_message"] = f"Airband scanner error: {exc}"
    finally:
        with state_lock:
            stop_airband_live_process_locked()
            if airband_scan_stop_event.is_set():
                runtime_state["airband_scan_state"] = "stopped"
                runtime_state["airband_scanner_message"] = "Airband scanner stopped."
            elif runtime_state["airband_scan_state"] != "error":
                runtime_state["airband_scan_state"] = "idle"
                runtime_state["airband_scanner_message"] = "Airband scanner idle."
            runtime_state["airband_scan_running"] = False
            runtime_state["airband_locked_channel"] = None
            runtime_state["airband_silence_remaining"] = None
        if receiver_lock.locked():
            receiver_lock.release()
        airband_scan_thread = None


# Replace the original activity worker. The existing start method will call this
# name because globals are looked up at call time.
airband_scan_worker = normal_airband_scan_worker

_original_build_status = build_status

def build_status() -> dict:
    payload = _original_build_status()
    with state_lock:
        airband_live_running = refresh_airband_live_process_locked()
    payload.update({
        "airband_live_audio_running": airband_live_running,
        "airband_live_available_samples": airband_live_available_samples() if airband_live_running else 0,
        "airband_scanner_settings": read_airband_scanner_settings(),
        "airband_blocked_frequencies_hz": read_airband_blocked_frequencies(),
        "airband_locked_channel": runtime_state.get("airband_locked_channel"),
        "airband_silence_remaining": runtime_state.get("airband_silence_remaining"),
        "airband_lock_reason": runtime_state.get("airband_lock_reason"),
        "airband_scanner_message": runtime_state.get("airband_scanner_message"),
    })
    if airband_live_running:
        payload["audio_mode"] = "airband_live"
    elif runtime_state.get("airband_scan_running"):
        payload["audio_mode"] = "airband_scan"
    return payload


def _send_airband_live_audio_block(handler: Handler, from_sample: int, requested_samples: int) -> None:
    with state_lock:
        running = refresh_airband_live_process_locked()
        if not running:
            handler.send_json({"error": "Live Airband audio is not running."}, HTTPStatus.CONFLICT)
            return
        available_samples = airband_live_available_samples()
    if from_sample > available_samples:
        handler.send_json({"error": "Requested cursor is beyond available Airband audio.", "available_samples": available_samples}, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        return
    sample_count = min(requested_samples, available_samples - from_sample)
    if sample_count < 1200:
        handler.send_bytes(b"", "audio/wav", HTTPStatus.NO_CONTENT, {"X-Audio-Available-Samples": str(available_samples)})
        return
    with AIRBAND_LIVE_WAV_PATH.open("rb") as audio_file:
        audio_file.seek(44 + from_sample * 2)
        pcm_data = audio_file.read(sample_count * 2)
    actual_samples = len(pcm_data) // 2
    if actual_samples == 0:
        handler.send_bytes(b"", "audio/wav", HTTPStatus.NO_CONTENT)
        return
    handler.send_bytes(wav_block_from_pcm(pcm_data), "audio/wav", extra_headers={
        "X-Source-Samples": str(actual_samples),
        "X-Audio-From-Sample": str(from_sample),
        "X-Audio-Available-Samples": str(available_samples),
    })


_original_do_get = Handler.do_GET
_original_do_post = Handler.do_POST
_original_stop_child_on_shutdown = stop_child_on_shutdown


def _normal_scanner_do_get(self: Handler) -> None:
    request = urlparse(self.path)
    if request.path == "/api/airband/scan/settings":
        self.send_json({
            "airband_scanner_settings": read_airband_scanner_settings(),
            "blocked_frequencies_hz": read_airband_blocked_frequencies(),
        })
        return
    if request.path == "/api/airband/scan/live/audio.wav":
        parameters = parse_qs(request.query)
        try:
            from_sample = max(0, int(parameters.get("from", ["0"])[0]))
            requested_samples = int(parameters.get("samples", ["12000"])[0])
        except ValueError:
            self.send_json({"error": "Invalid Airband audio cursor."}, HTTPStatus.BAD_REQUEST)
            return
        _send_airband_live_audio_block(self, from_sample, min(max(requested_samples, 1200), 48000))
        return
    _original_do_get(self)


def _normal_scanner_do_post(self: Handler) -> None:
    request = urlparse(self.path)
    if request.path == "/api/airband/scan/settings":
        payload = self.read_request_json()
        if payload is None:
            self.send_json({"error": "A JSON Airband scanner settings body is required."}, HTTPStatus.BAD_REQUEST)
            return
        settings = save_airband_scanner_settings(payload)
        self.send_json({"saved": True, "airband_scanner_settings": settings, **build_status()})
        return
    if request.path == "/api/airband/scan/squelch/up":
        settings = read_airband_scanner_settings()
        settings["squelch_dbfs"] = float(settings["squelch_dbfs"]) + 2.0
        settings = save_airband_scanner_settings(settings)
        self.send_json({"saved": True, "airband_scanner_settings": settings, **build_status()})
        return
    if request.path == "/api/airband/scan/squelch/down":
        settings = read_airband_scanner_settings()
        settings["squelch_dbfs"] = float(settings["squelch_dbfs"]) - 2.0
        settings = save_airband_scanner_settings(settings)
        self.send_json({"saved": True, "airband_scanner_settings": settings, **build_status()})
        return
    if request.path == "/api/airband/scan/activity/skip":
        airband_skip_event.set()
        self.send_json({"accepted": True, "command": "skip", **build_status()})
        return
    if request.path == "/api/airband/scan/activity/block":
        payload = self.read_request_json() or {}
        frequency_hz = None
        try:
            if payload.get("frequency_hz") is not None:
                frequency_hz = int(payload["frequency_hz"])
        except (TypeError, ValueError):
            frequency_hz = None
        if frequency_hz is None:
            with state_lock:
                channel = runtime_state.get("airband_locked_channel") or runtime_state.get("airband_current_channel")
            if isinstance(channel, dict):
                frequency_hz = int(channel.get("frequency_hz", 0))
        if frequency_hz is None or not (118000000 <= frequency_hz <= 137000000):
            self.send_json({"error": "No valid Airband frequency is currently selected to block."}, HTTPStatus.CONFLICT)
            return
        blocked = read_airband_blocked_frequencies()
        if frequency_hz not in blocked:
            blocked.append(frequency_hz)
        saved = save_airband_blocked_frequencies(blocked)
        airband_skip_event.set()
        self.send_json({"blocked": True, "frequency_hz": frequency_hz, "blocked_frequencies_hz": saved, **build_status()})
        return
    if request.path == "/api/airband/scan/activity/start":
        parameters = parse_qs(request.query)
        scope = parameters.get("scope", ["all"])[0]
        if scope not in ("continuous", "priority", "all"):
            scope = "all"
        try:
            frequency_text = parameters.get("frequency_hz", [""])[0]
            watch_frequency_hz = int(frequency_text) if frequency_text else None
        except ValueError:
            self.send_json({"error": "Invalid watch frequency."}, HTTPStatus.BAD_REQUEST)
            return
        airband_skip_event.clear()
        self.start_airband_activity_scan(scope, watch_frequency_hz)
        return
    if request.path == "/api/airband/scan/activity/stop":
        airband_skip_event.set()
        self.stop_airband_activity_scan()
        return
    _original_do_post(self)


def stop_child_on_shutdown() -> None:
    airband_scan_stop_event.set()
    airband_skip_event.set()
    with state_lock:
        stop_airband_live_process_locked()
    _original_stop_child_on_shutdown()


Handler.do_GET = _normal_scanner_do_get
Handler.do_POST = _normal_scanner_do_post
# --- end Airband normal scanner patch v1 ---



# AIRBAND_SPECTRUM_CANDIDATES_PATCH_V1
# Experimental fast RF spectrum candidate scanner. This is intentionally kept
# separate from the proven sequential live scanner until the candidate data is
# validated on real hardware.
AIRBAND_SPECTRUM_BINARY = Path(
    os.environ.get(
        "RTL_PI_AIRBAND_SPECTRUM_BINARY",
        "/opt/rtl-pi-adsb-tracker/bin/rtl_airband_spectrum_scan",
    )
)
AIRBAND_SPECTRUM_JSON_PATH = OUTPUT_DIR / "airband_spectrum_candidates.json"


def _airband_spectrum_float_param(parameters: dict, name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(parameters.get(name, [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _airband_spectrum_int_param(parameters: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(parameters.get(name, [str(default)])[0])
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _airband_spectrum_match_channel(candidate: dict, channels: list[dict]) -> dict | None:
    try:
        frequency_hz = int(candidate.get("frequency_hz"))
    except (TypeError, ValueError):
        return None

    best_channel = None
    best_delta = 999999999
    for channel in channels:
        try:
            channel_frequency_hz = int(channel.get("frequency_hz"))
        except (TypeError, ValueError):
            continue
        delta = abs(channel_frequency_hz - frequency_hz)
        if delta < best_delta:
            best_delta = delta
            best_channel = channel

    if best_channel is not None and best_delta <= 12500:
        return best_channel
    return None


def _airband_spectrum_candidates_payload(query: str) -> tuple[dict, HTTPStatus]:
    parameters = parse_qs(query)

    if not AIRBAND_SPECTRUM_BINARY.exists():
        return {
            "error": "Airband spectrum scanner binary is not deployed.",
            "binary": str(AIRBAND_SPECTRUM_BINARY),
        }, HTTPStatus.SERVICE_UNAVAILABLE

    start_hz = _airband_spectrum_int_param(parameters, "start_hz", 118000000, 118000000, 136000000)
    end_hz = _airband_spectrum_int_param(parameters, "end_hz", 137000000, start_hz + 100000, 137000000)
    sample_rate = _airband_spectrum_int_param(parameters, "sample_rate", 2048000, 1000000, 3200000)
    step_hz = _airband_spectrum_int_param(parameters, "step_hz", 25000, 5000, 100000)
    dwell_ms = _airband_spectrum_int_param(parameters, "dwell_ms", 80, 20, 2000)
    top_n = _airband_spectrum_int_param(parameters, "top_n", 20, 1, 200)
    gain_db = _airband_spectrum_float_param(parameters, "gain_db", float(RF_GAIN_DB), 0.0, 49.6)

    if not receiver_lock.acquire(blocking=False):
        return {"error": "Audio receiver is currently busy."}, HTTPStatus.CONFLICT

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_unlink(AIRBAND_SPECTRUM_JSON_PATH)

        command = [
            str(AIRBAND_SPECTRUM_BINARY),
            "--serial",
            AUDIO_SERIAL,
            "--start-hz",
            str(start_hz),
            "--end-hz",
            str(end_hz),
            "--sample-rate",
            str(sample_rate),
            "--step-hz",
            str(step_hz),
            "--dwell-ms",
            str(dwell_ms),
            "--gain-db",
            f"{gain_db:.1f}",
            "--top-n",
            str(top_n),
            "--json-output",
            str(AIRBAND_SPECTRUM_JSON_PATH),
        ]

        started_utc = int(time.time())
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=max(30, int(((end_hz - start_hz) / max(1, sample_rate - 150000)) + 2) * (dwell_ms / 1000.0 + 2.0) + 30),
            check=False,
        )

        if result.returncode != 0:
            return {
                "error": "Airband spectrum scan failed.",
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
                "command": command,
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        payload = read_json(AIRBAND_SPECTRUM_JSON_PATH)
        if not payload:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                return {
                    "error": "Airband spectrum scanner did not return JSON.",
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                }, HTTPStatus.INTERNAL_SERVER_ERROR

        location = read_receiver_location()
        nearby_channels = []
        if location is not None:
            nearby = nearby_airband_channels(location)
            if isinstance(nearby, dict) and isinstance(nearby.get("channels"), list):
                nearby_channels = nearby["channels"]

        blocked = set()
        try:
            blocked = set(read_airband_blocked_frequencies())
        except NameError:
            blocked = set()

        enriched_candidates = []
        for candidate in payload.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            try:
                candidate_frequency_hz = int(candidate.get("frequency_hz"))
            except (TypeError, ValueError):
                continue

            enriched = dict(candidate)
            enriched["blocked"] = candidate_frequency_hz in blocked
            matched_channel = _airband_spectrum_match_channel(enriched, nearby_channels)
            enriched["matched_channel"] = matched_channel
            enriched["known_channel_match"] = matched_channel is not None
            enriched_candidates.append(enriched)

        payload["candidates"] = enriched_candidates
        payload["candidate_count"] = len(enriched_candidates)
        payload["started_utc"] = started_utc
        payload["completed_utc"] = int(time.time())
        payload["receiver_location"] = location
        payload["audio_receiver_serial"] = AUDIO_SERIAL
        payload["binary"] = str(AIRBAND_SPECTRUM_BINARY)

        return payload, HTTPStatus.OK

    finally:
        receiver_lock.release()


_original_airband_spectrum_do_get = Handler.do_GET


def _airband_spectrum_do_get(self) -> None:
    request = urlparse(self.path)
    if request.path == "/api/airband/spectrum/candidates":
        payload, status = _airband_spectrum_candidates_payload(request.query)
        self.send_json(payload, status)
        return
    return _original_airband_spectrum_do_get(self)


Handler.do_GET = _airband_spectrum_do_get
# /AIRBAND_SPECTRUM_CANDIDATES_PATCH_V1


# AIRBAND_FAST_SPECTRUM_WORKER_PATCH_V1
import urllib.parse  # AIRBAND_FAST_SPECTRUM_URLENCODE_FIX_V1
# Replace the normal Airband scanner worker with a spectrum-first worker.
# Keep the previous worker as fallback.
_airband_sequential_scan_worker_fallback = airband_scan_worker


def _fast_spectrum_channel_for_candidate(candidate: dict, channels: list[dict]) -> dict | None:
    matched = candidate.get("matched_channel")
    if isinstance(matched, dict) and matched.get("frequency_hz") is not None:
        return matched

    try:
        candidate_frequency_hz = int(candidate.get("frequency_hz"))
    except (TypeError, ValueError):
        return None

    best_channel = None
    best_delta = 999999999
    for channel in channels:
        try:
            channel_frequency_hz = int(channel.get("frequency_hz"))
        except (TypeError, ValueError):
            continue
        delta = abs(channel_frequency_hz - candidate_frequency_hz)
        if delta < best_delta:
            best_delta = delta
            best_channel = channel

    if best_channel is not None and best_delta <= 12500:
        return best_channel
    return None


# AIRBAND_FAST_SPECTRUM_LOCK_CONTENTION_FIX_V1
def _fast_spectrum_candidates_once(settings: dict, channels: list[dict]) -> dict:
    # Run one fast-spectrum sweep from inside the scanner worker.
    #
    # This intentionally does not call _airband_spectrum_candidates_payload(),
    # because the worker already owns receiver_lock and that API helper tries to
    # acquire the same lock again.
    if not AIRBAND_SPECTRUM_BINARY.exists():
        return {
            "ok": False,
            "error": f"Spectrum binary is not deployed: {AIRBAND_SPECTRUM_BINARY}",
            "payload": {},
        }

    start_hz = 118000000
    end_hz = 137000000

    frequencies = []
    for channel in channels:
        try:
            frequencies.append(int(channel.get("frequency_hz")))
        except (TypeError, ValueError):
            continue

    if frequencies:
        start_hz = max(118000000, min(frequencies) - 250000)
        end_hz = min(137000000, max(frequencies) + 250000)

    dwell_ms = max(40, int(settings.get("sample_ms", 250)) // 3)
    gain_db = float(settings.get("rf_gain_db", RF_GAIN_DB))
    top_n = 40

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_unlink(AIRBAND_SPECTRUM_JSON_PATH)

    command = [
        str(AIRBAND_SPECTRUM_BINARY),
        "--serial",
        AUDIO_SERIAL,
        "--start-hz",
        str(start_hz),
        "--end-hz",
        str(end_hz),
        "--sample-rate",
        "2048000",
        "--step-hz",
        "25000",
        "--dwell-ms",
        str(dwell_ms),
        "--gain-db",
        f"{gain_db:.1f}",
        "--top-n",
        str(top_n),
        "--json-output",
        str(AIRBAND_SPECTRUM_JSON_PATH),
    ]

    started_utc = int(time.time())
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=max(
                30,
                int(((end_hz - start_hz) / max(1, 2048000 - 150000)) + 2)
                * (dwell_ms / 1000.0 + 2.0)
                + 30,
            ),
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "payload": {"command": command}}

    if result.returncode != 0:
        return {
            "ok": False,
            "error": result.stderr.strip() or result.stdout.strip() or "Spectrum scan failed.",
            "payload": {
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
                "command": command,
            },
        }

    payload = read_json(AIRBAND_SPECTRUM_JSON_PATH)
    if not payload:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "Spectrum scanner did not return JSON.",
                "payload": {
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                    "command": command,
                },
            }

    blocked = set(read_airband_blocked_frequencies())

    enriched_candidates = []
    for candidate in payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue

        try:
            frequency_hz = int(candidate.get("frequency_hz"))
        except (TypeError, ValueError):
            continue

        enriched = dict(candidate)
        enriched["blocked"] = frequency_hz in blocked
        matched_channel = _fast_spectrum_channel_for_candidate(enriched, channels)
        enriched["matched_channel"] = matched_channel
        enriched["known_channel_match"] = matched_channel is not None
        enriched_candidates.append(enriched)

    payload["candidates"] = enriched_candidates
    payload["candidate_count"] = len(enriched_candidates)
    payload["started_utc"] = started_utc
    payload["completed_utc"] = int(time.time())
    payload["audio_receiver_serial"] = AUDIO_SERIAL
    payload["binary"] = str(AIRBAND_SPECTRUM_BINARY)
    payload["source"] = "scanner_worker_no_relock"

    return {"ok": True, "payload": payload}


def _select_fast_spectrum_candidate(spectrum_payload: dict, settings: dict, channels: list[dict]) -> tuple[dict | None, dict | None]:
    blocked = set(read_airband_blocked_frequencies())
    snr_threshold = float(settings.get("snr_threshold_db", 6.0))
    channel_frequency_set = set()
    for channel in channels:
        try:
            channel_frequency_set.add(int(channel.get("frequency_hz")))
        except (TypeError, ValueError):
            continue

    best_channel = None
    best_candidate = None

    for candidate in spectrum_payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        try:
            candidate_frequency_hz = int(candidate.get("frequency_hz"))
            candidate_snr = float(candidate.get("estimated_snr_db", -999.0))
        except (TypeError, ValueError):
            continue

        if candidate_frequency_hz in blocked or candidate_snr < snr_threshold:
            continue

        channel = _fast_spectrum_channel_for_candidate(candidate, channels)
        if channel is None:
            continue

        try:
            channel_frequency_hz = int(channel.get("frequency_hz"))
        except (TypeError, ValueError):
            continue

        if channel_frequency_set and channel_frequency_hz not in channel_frequency_set:
            continue
        if channel_frequency_hz in blocked:
            continue

        best_channel = channel
        best_candidate = candidate
        break

    return best_channel, best_candidate


def airband_scan_worker(channels: list[dict]) -> None:
    global airband_scan_thread

    # If the spectrum binary/helper is not present for any reason, use the proven
    # sequential scanner without breaking normal operation.
    if "AIRBAND_SPECTRUM_BINARY" not in globals() or not AIRBAND_SPECTRUM_BINARY.exists():
        _airband_sequential_scan_worker_fallback(channels)
        return

    try:
        with state_lock:
            runtime_state["airband_scan_state"] = "spectrum_search"
            runtime_state["airband_scanner_message"] = "Fast spectrum Airband scanner starting."

        while not airband_scan_stop_event.is_set():
            settings = read_airband_scanner_settings()
            blocked = set(read_airband_blocked_frequencies())
            active_channels = [
                channel for channel in channels
                if int(channel.get("frequency_hz", 0)) not in blocked
            ]

            if not active_channels:
                with state_lock:
                    runtime_state["airband_scan_state"] = "blocked_all"
                    runtime_state["airband_scanner_message"] = "All candidate Airband frequencies are blocked."
                time.sleep(1.0)
                continue

            with state_lock:
                runtime_state["airband_scan_running"] = True
                runtime_state["airband_scan_state"] = "spectrum_search"
                runtime_state["airband_scan_cycles"] = int(runtime_state.get("airband_scan_cycles", 0)) + 1
                runtime_state["airband_locked_channel"] = None
                runtime_state["airband_silence_remaining"] = None
                runtime_state["airband_scanner_message"] = (
                    f"Fast spectrum sweep across {len(active_channels)} Airband frequencies."
                )

            spectrum_result = _fast_spectrum_candidates_once(settings, active_channels)
            if not spectrum_result.get("ok"):
                with state_lock:
                    runtime_state["airband_scan_state"] = "spectrum_error"
                    runtime_state["airband_scan_error"] = spectrum_result.get("error")
                    runtime_state["airband_scanner_message"] = (
                        f"Spectrum scan failed; using sequential fallback: {spectrum_result.get('error')}"
                    )
                _airband_sequential_scan_worker_fallback(channels)
                return

            spectrum_payload = spectrum_result["payload"]
            best_channel, best_candidate = _select_fast_spectrum_candidate(
                spectrum_payload,
                settings,
                active_channels,
            )

            with state_lock:
                runtime_state["airband_spectrum_last_scan"] = spectrum_payload
                runtime_state["airband_channels_scanned"] = int(runtime_state.get("airband_channels_scanned", 0)) + int(spectrum_payload.get("candidate_count", 0))

            if best_channel is None or best_candidate is None:
                top = None
                for candidate in spectrum_payload.get("candidates", []):
                    if isinstance(candidate, dict):
                        top = candidate
                        break
                with state_lock:
                    runtime_state["airband_scan_state"] = "spectrum_search"
                    runtime_state["airband_current_channel"] = None
                    runtime_state["airband_last_signal_snr_db"] = (
                        round(float(top.get("estimated_snr_db")), 2)
                        if isinstance(top, dict) and top.get("estimated_snr_db") is not None
                        else None
                    )
                    runtime_state["airband_scanner_message"] = (
                        "Fast spectrum sweep found no known unblocked channel above threshold."
                    )
                time.sleep(0.25)
                continue

            candidate_snr = float(best_candidate.get("estimated_snr_db", 0.0))
            candidate_power = float(best_candidate.get("power_db", -120.0))

            detection = {
                "channel": best_channel,
                "audio_rms_dbfs": None,
                "rf_estimated_snr_db": round(candidate_snr, 2),
                "rf_power_db": round(candidate_power, 2),
                "threshold_snr_db": settings["snr_threshold_db"],
                "squelch_dbfs": settings["squelch_dbfs"],
                "detected_utc": int(time.time()),
                "source": "fast_spectrum",
                "audio_url": "/api/airband/scan/live/audio.wav",
            }

            with state_lock:
                runtime_state["airband_current_channel"] = best_channel
                runtime_state["airband_last_detection"] = detection
                runtime_state["airband_last_signal_snr_db"] = round(candidate_snr, 2)
                runtime_state["airband_last_measurement_dbfs"] = round(candidate_power, 2)
                runtime_state["airband_locked_channel"] = best_channel
                runtime_state["airband_scan_state"] = "locked"
                runtime_state["airband_lock_reason"] = "fast_spectrum_activity_detected"
                runtime_state["airband_scanner_message"] = (
                    f"Fast spectrum locked on {best_channel['frequency_mhz']:.3f} MHz."
                )
                start_airband_live_process_locked(best_channel, settings)

            monitor_airband_locked_channel(best_channel, settings)

            with state_lock:
                if not airband_scan_stop_event.is_set():
                    runtime_state["airband_scan_state"] = "spectrum_search"
                    runtime_state["airband_locked_channel"] = None
                    runtime_state["airband_scanner_message"] = "Resuming fast spectrum Airband scan."

    except Exception as exc:
        with state_lock:
            runtime_state["airband_scan_error"] = str(exc)
            runtime_state["airband_scan_state"] = "error"
            runtime_state["airband_scanner_message"] = f"Fast spectrum scanner error: {exc}"
    finally:
        with state_lock:
            stop_airband_live_process_locked()
            if airband_scan_stop_event.is_set():
                runtime_state["airband_scan_state"] = "stopped"
                runtime_state["airband_scanner_message"] = "Airband scanner stopped."
            elif runtime_state.get("airband_scan_state") != "error":
                runtime_state["airband_scan_state"] = "idle"
                runtime_state["airband_scanner_message"] = "Airband scanner idle."
            runtime_state["airband_scan_running"] = False
            runtime_state["airband_locked_channel"] = None
            runtime_state["airband_silence_remaining"] = None
        if receiver_lock.locked():
            receiver_lock.release()
        airband_scan_thread = None

# /AIRBAND_FAST_SPECTRUM_WORKER_PATCH_V1


# AIRBAND_FAST_SPECTRUM_SKIP_LOCKOUT_PATCH_V1
# Temporary skip lockout for fast-spectrum Airband scanner.
#
# Skip should behave like a scanner SKIP/Nuisance Delete action: leave the
# current open frequency and do not immediately re-lock it on the next spectrum
# sweep. Block remains the permanent blacklist action.
AIRBAND_SKIP_LOCKOUT_SECONDS = int(os.environ.get("RTL_PI_AIRBAND_SKIP_LOCKOUT_SECONDS", "120"))
runtime_state.setdefault("airband_skip_lockouts", {})


def _airband_skip_lockout_cleanup() -> dict:
    now = time.time()
    lockouts = runtime_state.get("airband_skip_lockouts")
    if not isinstance(lockouts, dict):
        lockouts = {}
    clean = {}
    for key, expires_at in lockouts.items():
        try:
            frequency_hz = int(key)
            expires = float(expires_at)
        except (TypeError, ValueError):
            continue
        if expires > now:
            clean[str(frequency_hz)] = expires
    runtime_state["airband_skip_lockouts"] = clean
    return clean


def _airband_skip_lockout_frequencies() -> set[int]:
    lockouts = _airband_skip_lockout_cleanup()
    frequencies = set()
    for key in lockouts:
        try:
            frequencies.add(int(key))
        except (TypeError, ValueError):
            continue
    return frequencies


def _airband_skip_lockout_add(frequency_hz: int, seconds: int | None = None) -> dict:
    if seconds is None:
        seconds = AIRBAND_SKIP_LOCKOUT_SECONDS
    lockouts = _airband_skip_lockout_cleanup()
    lockouts[str(int(frequency_hz))] = time.time() + max(1, int(seconds))
    runtime_state["airband_skip_lockouts"] = lockouts
    return lockouts


def _airband_current_frequency_for_skip() -> int | None:
    candidates = []
    with state_lock:
        candidates.append(runtime_state.get("airband_locked_channel"))
        candidates.append(runtime_state.get("airband_current_channel"))
        detection = runtime_state.get("airband_last_detection")
        if isinstance(detection, dict):
            candidates.append(detection.get("channel"))

    for channel in candidates:
        if not isinstance(channel, dict):
            continue
        try:
            frequency_hz = int(channel.get("frequency_hz"))
        except (TypeError, ValueError):
            continue
        if 118000000 <= frequency_hz <= 137000000:
            return frequency_hz
    return None


# Replace candidate selection so temporary skip lockouts are treated like a
# short-lived block list during fast spectrum selection.
def _select_fast_spectrum_candidate(spectrum_payload: dict, settings: dict, channels: list[dict]) -> tuple[dict | None, dict | None]:
    blocked = set(read_airband_blocked_frequencies())
    skipped = _airband_skip_lockout_frequencies()
    snr_threshold = float(settings.get("snr_threshold_db", 6.0))
    channel_frequency_set = set()
    for channel in channels:
        try:
            channel_frequency_set.add(int(channel.get("frequency_hz")))
        except (TypeError, ValueError):
            continue

    for candidate in spectrum_payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        try:
            candidate_frequency_hz = int(candidate.get("frequency_hz"))
            candidate_snr = float(candidate.get("estimated_snr_db", -999.0))
        except (TypeError, ValueError):
            continue

        if candidate_frequency_hz in blocked or candidate_frequency_hz in skipped:
            continue
        if candidate_snr < snr_threshold:
            continue

        channel = _fast_spectrum_channel_for_candidate(candidate, channels)
        if channel is None:
            continue

        try:
            channel_frequency_hz = int(channel.get("frequency_hz"))
        except (TypeError, ValueError):
            continue

        if channel_frequency_set and channel_frequency_hz not in channel_frequency_set:
            continue
        if channel_frequency_hz in blocked or channel_frequency_hz in skipped:
            continue

        return channel, candidate

    return None, None


_original_airband_skip_lockout_do_post = Handler.do_POST


def _airband_skip_lockout_do_post(self) -> None:
    request = urlparse(self.path)

    if request.path == "/api/airband/scan/activity/skip":
        frequency_hz = _airband_current_frequency_for_skip()
        airband_skip_event.set()

        payload = {
            "accepted": True,
            "command": "skip",
            "skip_lockout_seconds": AIRBAND_SKIP_LOCKOUT_SECONDS,
            "skip_frequency_hz": frequency_hz,
        }

        if frequency_hz is not None:
            lockouts = _airband_skip_lockout_add(frequency_hz)
            payload["airband_skip_lockouts"] = lockouts
            payload["message"] = (
                f"Skipped {frequency_hz / 1000000.0:.3f} MHz for "
                f"{AIRBAND_SKIP_LOCKOUT_SECONDS} seconds."
            )
            with state_lock:
                runtime_state["airband_scanner_message"] = payload["message"]
        else:
            payload["message"] = "Skip accepted; no current Airband frequency was available to lock out."

        payload.update(build_status())
        self.send_json(payload)
        return

    return _original_airband_skip_lockout_do_post(self)


Handler.do_POST = _airband_skip_lockout_do_post


_original_airband_skip_lockout_build_status = build_status


def build_status() -> dict:
    payload = _original_airband_skip_lockout_build_status()
    payload["airband_skip_lockouts"] = _airband_skip_lockout_cleanup()
    payload["airband_skip_lockout_seconds"] = AIRBAND_SKIP_LOCKOUT_SECONDS
    return payload

# /AIRBAND_FAST_SPECTRUM_SKIP_LOCKOUT_PATCH_V1


# AIRLABS_BACKEND_ROUTE_LOOKUP_PATCH_V2
# AirLabs route lookup + local key/cache storage.
import hashlib
import urllib.parse
import urllib.request


AIRLABS_CACHE_TTL_SECONDS = int(os.environ.get("RTL_PI_AIRLABS_CACHE_TTL_SECONDS", "7200"))


def _airlabs_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _airlabs_read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _airlabs_safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _airlabs_settings_dir() -> Path:
    candidates = [
        globals().get("SETTINGS_DIR"),
        globals().get("RUNTIME_SETTINGS_DIR"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Path):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate

    deploy_dir = globals().get("DEPLOY_DIR")
    if isinstance(deploy_dir, Path):
        settings_dir = deploy_dir / "settings"
    else:
        settings_dir = Path("runtime/settings")

    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir


AIRLABS_API_PATH = _airlabs_settings_dir() / "airlabs_api.json"
AIRLABS_CACHE_PATH = _airlabs_settings_dir() / "airlabs_route_cache.json"


def _airlabs_now() -> int:
    return int(time.time())


def _airlabs_normalize_flight(value: object) -> str:
    flight = str(value or "").strip().upper()
    flight = "".join(ch for ch in flight if ch.isalnum())
    return flight


def _airlabs_mask_key(api_key: str) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}…{api_key[-4:]}"


def _airlabs_key_fingerprint(api_key: str) -> str | None:
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def _airlabs_read_api_config() -> dict:
    data = _airlabs_read_json(AIRLABS_API_PATH)
    if not isinstance(data, dict):
        return {}
    return data


def _airlabs_write_api_config(api_key: str) -> dict:
    config = {
        "api_key": api_key,
        "updated_utc": _airlabs_now(),
    }
    _airlabs_write_json(AIRLABS_API_PATH, config)
    try:
        os.chmod(AIRLABS_API_PATH, 0o600)
    except OSError:
        pass
    return config


def _airlabs_clear_api_config() -> None:
    _airlabs_safe_unlink(AIRLABS_API_PATH)


def _airlabs_get_api_key() -> str:
    config = _airlabs_read_api_config()
    return str(config.get("api_key") or "").strip()


def _airlabs_read_cache() -> dict:
    data = _airlabs_read_json(AIRLABS_CACHE_PATH)
    if not isinstance(data, dict):
        return {}
    return data


def _airlabs_write_cache(cache: dict) -> None:
    _airlabs_write_json(AIRLABS_CACHE_PATH, cache)


def _airlabs_public_status() -> dict:
    config = _airlabs_read_api_config()
    api_key = str(config.get("api_key") or "").strip()
    cache = _airlabs_read_cache()

    return {
        "enabled": bool(api_key),
        "configured": bool(api_key),
        "masked_key": _airlabs_mask_key(api_key),
        "key_fingerprint": _airlabs_key_fingerprint(api_key),
        "updated_utc": config.get("updated_utc"),
        "cache_count": len(cache),
        "cache_ttl_seconds": AIRLABS_CACHE_TTL_SECONDS,
        "settings_path": str(AIRLABS_API_PATH),
        "cache_path": str(AIRLABS_CACHE_PATH),
    }


def _airlabs_cache_get(flight_icao: str) -> dict | None:
    cache = _airlabs_read_cache()
    entry = cache.get(flight_icao)
    if not isinstance(entry, dict):
        return None

    cached_utc = int(entry.get("cached_utc") or 0)
    if cached_utc <= 0 or _airlabs_now() - cached_utc > AIRLABS_CACHE_TTL_SECONDS:
        cache.pop(flight_icao, None)
        _airlabs_write_cache(cache)
        return None

    result = dict(entry.get("result") or {})
    if result:
        result["cached"] = True
        result["cached_utc"] = cached_utc
        return result
    return None


def _airlabs_cache_put(flight_icao: str, result: dict) -> None:
    cache = _airlabs_read_cache()
    cache[flight_icao] = {
        "cached_utc": _airlabs_now(),
        "result": result,
    }
    _airlabs_write_cache(cache)


def _airlabs_clear_cache() -> int:
    cache = _airlabs_read_cache()
    count = len(cache)
    _airlabs_safe_unlink(AIRLABS_CACHE_PATH)
    return count


def _airlabs_extract_route(record: dict) -> dict:
    dep_iata = record.get("dep_iata") or record.get("departure_iata")
    dep_icao = record.get("dep_icao") or record.get("departure_icao")
    arr_iata = record.get("arr_iata") or record.get("arrival_iata")
    arr_icao = record.get("arr_icao") or record.get("arrival_icao")

    result = {
        "found": bool(dep_iata or dep_icao or arr_iata or arr_icao),
        "from": dep_iata or dep_icao,
        "to": arr_iata or arr_icao,
        "dep_iata": dep_iata,
        "dep_icao": dep_icao,
        "dep_name": record.get("dep_name") or record.get("departure_name"),
        "arr_iata": arr_iata,
        "arr_icao": arr_icao,
        "arr_name": record.get("arr_name") or record.get("arrival_name"),
        "airline_iata": record.get("airline_iata"),
        "airline_icao": record.get("airline_icao"),
        "flight_iata": record.get("flight_iata"),
        "flight_icao": record.get("flight_icao"),
        "source": "airlabs",
        "cached": False,
        "looked_up_utc": _airlabs_now(),
        "raw": record,
    }
    return result


def _airlabs_lookup_route_live(flight_icao: str, api_key: str) -> dict:
    params = urllib.parse.urlencode({
        "flight_icao": flight_icao,
        "api_key": api_key,
    })

    endpoint = os.environ.get("RTL_PI_AIRLABS_FLIGHTS_ENDPOINT", "https://airlabs.co/api/v9/flights")
    url = f"{endpoint}?{params}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "RTL-Pi-ADS-B-Tracker/airlabs-route-lookup",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=8) as response:
        raw_body = response.read().decode("utf-8", errors="replace")

    payload = json.loads(raw_body)
    response_data = payload.get("response")

    if isinstance(response_data, list):
        if not response_data:
            return {
                "found": False,
                "flight_icao": flight_icao,
                "source": "airlabs",
                "cached": False,
                "looked_up_utc": _airlabs_now(),
                "message": "AirLabs returned no matching flights.",
            }
        record = response_data[0]
    elif isinstance(response_data, dict):
        record = response_data
    else:
        return {
            "found": False,
            "flight_icao": flight_icao,
            "source": "airlabs",
            "cached": False,
            "looked_up_utc": _airlabs_now(),
            "message": "AirLabs response did not include route data.",
            "raw": payload,
        }

    result = _airlabs_extract_route(record)
    result["flight_icao_requested"] = flight_icao
    return result


def _airlabs_route_payload(query: str) -> tuple[dict, HTTPStatus]:
    parameters = urllib.parse.parse_qs(query, keep_blank_values=True)
    flight_icao = _airlabs_normalize_flight(
        (parameters.get("flight_icao") or parameters.get("callsign") or parameters.get("flight") or [""])[0]
    )

    if not flight_icao:
        return {"error": "Missing flight_icao/callsign parameter."}, HTTPStatus.BAD_REQUEST

    cached = _airlabs_cache_get(flight_icao)
    if cached is not None:
        return {"flight_icao": flight_icao, "route": cached, "cached": True}, HTTPStatus.OK

    api_key = _airlabs_get_api_key()
    if not api_key:
        return {
            "error": "AirLabs API key is not configured.",
            "flight_icao": flight_icao,
            "status": _airlabs_public_status(),
        }, HTTPStatus.CONFLICT

    try:
        route = _airlabs_lookup_route_live(flight_icao, api_key)
    except Exception as exc:
        return {
            "error": f"AirLabs route lookup failed: {exc}",
            "flight_icao": flight_icao,
        }, HTTPStatus.BAD_GATEWAY

    if route.get("found"):
        _airlabs_cache_put(flight_icao, route)

    return {
        "flight_icao": flight_icao,
        "route": route,
        "cached": False,
    }, HTTPStatus.OK


_original_airlabs_do_get = Handler.do_GET


def _airlabs_do_get(self) -> None:
    request = urlparse(self.path)

    if request.path == "/api/diagnostics/airlabs/status":
        self.send_json(_airlabs_public_status())
        return

    if request.path == "/api/diagnostics/airlabs/route":
        payload, status = _airlabs_route_payload(request.query)
        self.send_json(payload, status)
        return

    return _original_airlabs_do_get(self)


Handler.do_GET = _airlabs_do_get


_original_airlabs_do_post = Handler.do_POST


def _airlabs_do_post(self) -> None:
    request = urlparse(self.path)

    if request.path == "/api/diagnostics/airlabs/settings":
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else "{}"

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON body."}, HTTPStatus.BAD_REQUEST)
            return

        action = str(payload.get("action") or "save").strip().lower()
        if action in {"clear", "delete", "remove"}:
            _airlabs_clear_api_config()
            self.send_json({"saved": False, "cleared": True, "status": _airlabs_public_status()})
            return

        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:
            self.send_json({"error": "Missing api_key."}, HTTPStatus.BAD_REQUEST)
            return

        _airlabs_write_api_config(api_key)
        self.send_json({"saved": True, "status": _airlabs_public_status()})
        return

    if request.path == "/api/diagnostics/airlabs/cache/clear":
        cleared = _airlabs_clear_cache()
        self.send_json({"cleared": cleared, "status": _airlabs_public_status()})
        return

    return _original_airlabs_do_post(self)


Handler.do_POST = _airlabs_do_post

# /AIRLABS_BACKEND_ROUTE_LOOKUP_PATCH_V2


# AIRCRAFT_PHOTO_BEST_GUESS_BACKEND_PATCH_V1
# Best-guess aircraft photo fallback lookup.
import html as _aircraft_photo_html
import urllib.parse as _aircraft_photo_urlparse
import urllib.request as _aircraft_photo_urlrequest


AIRCRAFT_PHOTO_FALLBACK_CACHE_TTL_SECONDS = int(
    os.environ.get("RTL_PI_AIRCRAFT_PHOTO_FALLBACK_CACHE_TTL_SECONDS", "86400")
)


def _aircraft_photo_settings_dir() -> Path:
    for candidate in (globals().get("SETTINGS_DIR"), globals().get("RUNTIME_SETTINGS_DIR")):
        if isinstance(candidate, Path):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate

    deploy_dir = globals().get("DEPLOY_DIR")
    if isinstance(deploy_dir, Path):
        settings_dir = deploy_dir / "settings"
    else:
        settings_dir = Path("runtime/settings")
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir


AIRCRAFT_PHOTO_FALLBACK_CACHE_PATH = _aircraft_photo_settings_dir() / "aircraft_photo_fallback_cache.json"


def _aircraft_photo_read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _aircraft_photo_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _aircraft_photo_normalize_token(value: object) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"[^A-Z0-9\- ]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _aircraft_photo_cache_key(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]


def _aircraft_photo_cache_get(key: str) -> dict | None:
    cache = _aircraft_photo_read_json(AIRCRAFT_PHOTO_FALLBACK_CACHE_PATH)
    if not isinstance(cache, dict):
        return None

    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None

    cached_utc = int(entry.get("cached_utc") or 0)
    if cached_utc <= 0 or int(time.time()) - cached_utc > AIRCRAFT_PHOTO_FALLBACK_CACHE_TTL_SECONDS:
        cache.pop(key, None)
        _aircraft_photo_write_json(AIRCRAFT_PHOTO_FALLBACK_CACHE_PATH, cache)
        return None

    result = dict(entry.get("result") or {})
    if result:
        result["cached"] = True
        result["cached_utc"] = cached_utc
        return result
    return None


def _aircraft_photo_cache_put(key: str, result: dict) -> None:
    cache = _aircraft_photo_read_json(AIRCRAFT_PHOTO_FALLBACK_CACHE_PATH)
    if not isinstance(cache, dict):
        cache = {}
    cache[key] = {
        "cached_utc": int(time.time()),
        "result": result,
    }
    _aircraft_photo_write_json(AIRCRAFT_PHOTO_FALLBACK_CACHE_PATH, cache)


def _aircraft_photo_fetch_text(url: str, timeout: int = 8) -> str:
    request = _aircraft_photo_urlrequest.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 RTL-Pi-ADS-B-Tracker aircraft photo fallback",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with _aircraft_photo_urlrequest.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _aircraft_photo_absolute_url(src: str, page_url: str) -> str:
    src = _aircraft_photo_html.unescape(str(src or "").strip())
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        parsed = _aircraft_photo_urlparse.urlparse(page_url)
        return f"{parsed.scheme}://{parsed.netloc}{src}"
    return src


def _aircraft_photo_first_image_from_html(html_text: str, page_url: str, source: str) -> str | None:
    candidates: list[str] = []

    # Prefer meta og:image first if available.
    for match in re.finditer(r'''property=["']og:image["'][^>]+content=["']([^"']+)["']''', html_text, re.I):
        src = _aircraft_photo_absolute_url(match.group(1), page_url)
        if any(ext in src.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")):
            candidates.append(src)

    # Then common lazy image attributes.
    for match in re.finditer(r'''(?:src|data-src|data-lazy|data-original)=["']([^"']+)["']''', html_text, re.I):
        src = _aircraft_photo_absolute_url(match.group(1), page_url)
        low = src.lower()
        if not any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp")):
            continue
        if source == "jetphotos" and "jetphotos" not in low:
            continue
        if source == "planespotters" and "planespotters" not in low:
            continue
        if any(skip in low for skip in ("logo", "icon", "avatar", "sprite", "blank", "placeholder")):
            continue
        candidates.append(src)

    return candidates[0] if candidates else None


def _aircraft_photo_lookup_live(query: str, reg: str) -> dict:
    encoded_query = _aircraft_photo_urlparse.quote(query)
    encoded_primary = _aircraft_photo_urlparse.quote(reg or query)

    attempts = [
        {"source": "JetPhotos", "page_url": f"https://www.jetphotos.com/photo/keyword/{encoded_primary}", "parse_source": "jetphotos"},
        {"source": "Planespotters", "page_url": f"https://www.planespotters.net/search?q={encoded_primary}", "parse_source": "planespotters"},
        {"source": "JetPhotos", "page_url": f"https://www.jetphotos.com/photo/keyword/{encoded_query}", "parse_source": "jetphotos"},
        {"source": "Planespotters", "page_url": f"https://www.planespotters.net/search?q={encoded_query}", "parse_source": "planespotters"},
    ]

    errors: list[str] = []
    for attempt in attempts:
        try:
            html_text = _aircraft_photo_fetch_text(attempt["page_url"])
            image_url = _aircraft_photo_first_image_from_html(html_text, attempt["page_url"], attempt["parse_source"])
            if image_url:
                return {
                    "found": True,
                    "source": attempt["source"],
                    "page_url": attempt["page_url"],
                    "image_url": image_url,
                    "query": query,
                    "cached": False,
                    "match_level": "exact",
                    "representative": False,
                    "looked_up_utc": int(time.time()),
                }
        except Exception as exc:
            errors.append(f"{attempt['source']}: {exc}")

    return {
        "found": False,
        "source": "best_guess",
        "query": query,
        "cached": False,
        "match_level": "none",
        "representative": False,
        "looked_up_utc": int(time.time()),
        "message": "No fallback aircraft image found.",
        "errors": errors[-4:],
        "search_urls": {
            "jetphotos": f"https://www.jetphotos.com/photo/keyword/{encoded_primary}",
            "planespotters": f"https://www.planespotters.net/search?q={encoded_primary}",
            "google_images": f"https://www.google.com/search?tbm=isch&q={encoded_query}",
        },
    }



def _aircraft_photo_type_query(aircraft_type: str, model: str) -> str:
    terms: list[str] = []
    for term in (model, aircraft_type):
        normalized = _aircraft_photo_normalize_token(term)
        if normalized and normalized not in terms:
            terms.append(normalized)
    return " ".join(terms)


def _aircraft_photo_lookup_representative_type(aircraft_type: str, model: str) -> dict:
    type_query = _aircraft_photo_type_query(aircraft_type, model)
    if not type_query:
        return {
            "found": False,
            "source": "type_fallback",
            "query": "",
            "cached": False,
            "match_level": "none",
            "representative": False,
            "looked_up_utc": int(time.time()),
            "message": "No aircraft type/model available for representative photo lookup.",
        }

    cache_key = _aircraft_photo_cache_key("type:" + type_query)
    cached = _aircraft_photo_cache_get(cache_key)
    if cached:
        cached["match_level"] = cached.get("match_level") or "type"
        cached["representative"] = True
        return cached

    result = _aircraft_photo_lookup_live(type_query, type_query)
    result["match_level"] = "type" if result.get("found") else "none"
    result["representative"] = bool(result.get("found"))
    result["type_query"] = type_query

    if result.get("found"):
        _aircraft_photo_cache_put(cache_key, result)

    return result


def _aircraft_photo_fallback_payload(query_string: str) -> tuple[dict, HTTPStatus]:
    parameters = urllib.parse.parse_qs(query_string, keep_blank_values=True)

    reg = _aircraft_photo_normalize_token((parameters.get("reg") or parameters.get("tail") or [""])[0])
    callsign = _aircraft_photo_normalize_token((parameters.get("callsign") or parameters.get("flight") or [""])[0])
    hex_value = _aircraft_photo_normalize_token((parameters.get("hex") or parameters.get("icao") or [""])[0])
    aircraft_type = _aircraft_photo_normalize_token((parameters.get("type") or [""])[0])
    model = _aircraft_photo_normalize_token((parameters.get("model") or [""])[0])
    operator = _aircraft_photo_normalize_token((parameters.get("operator") or [""])[0])

    terms = [term for term in (reg, callsign, hex_value, aircraft_type, model, operator) if term]
    if not terms:
        return {"error": "Missing aircraft photo search terms."}, HTTPStatus.BAD_REQUEST

    query = " ".join(dict.fromkeys(terms))
    cache_key = _aircraft_photo_cache_key(query)

    cached = _aircraft_photo_cache_get(cache_key)
    if cached:
        return {"result": cached}, HTTPStatus.OK

    result = _aircraft_photo_lookup_live(query, reg or callsign or hex_value or query)
    if result.get("found"):
        result["match_level"] = result.get("match_level") or "exact"
        result["representative"] = False
        _aircraft_photo_cache_put(cache_key, result)
        return {"result": result}, HTTPStatus.OK

    # Exact aircraft lookup failed. Try a representative type/model lookup and
    # cache it separately so future aircraft of the same type avoid repeated
    # external photo-site lookups.
    type_result = _aircraft_photo_lookup_representative_type(aircraft_type, model)
    if type_result.get("found"):
        type_result["exact_lookup_message"] = result.get("message")
        return {"result": type_result}, HTTPStatus.OK

    return {"result": result}, HTTPStatus.OK


_original_aircraft_photo_do_get = Handler.do_GET


def _aircraft_photo_do_get(self) -> None:
    request = urlparse(self.path)
    if request.path == "/api/aircraft/photo/fallback":
        payload, status = _aircraft_photo_fallback_payload(request.query)
        self.send_json(payload, status)
        return

    return _original_aircraft_photo_do_get(self)


Handler.do_GET = _aircraft_photo_do_get

# AIRCRAFT_PHOTO_TYPE_FALLBACK_CACHE_PATCH_V1
# Representative make/model/type photo fallback cache enabled.
# /AIRCRAFT_PHOTO_TYPE_FALLBACK_CACHE_PATCH_V1
# /AIRCRAFT_PHOTO_BEST_GUESS_BACKEND_PATCH_V1


# AIRCRAFT_PHOTO_MODEL_FALLBACK_IMPROVEMENT_PATCH_V1
# Improve representative make/model/type photo fallback.
#
# This override keeps exact registration/hex lookup first, then improves
# representative model/type fallback using manufacturer terms and Wikimedia.

def _aircraft_photo_expand_model_terms(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    maker = _aircraft_photo_normalize_token(manufacturer)
    aircraft_type = _aircraft_photo_normalize_token(aircraft_type)
    model = _aircraft_photo_normalize_token(model)

    terms: list[str] = []

    def add(value: str) -> None:
        value = _aircraft_photo_normalize_token(value)
        if value and value not in terms:
            terms.append(value)

    add(" ".join(part for part in (maker, model or aircraft_type) if part).strip())
    add(" ".join(part for part in (maker, aircraft_type) if part).strip())
    add(model)
    add(aircraft_type)

    shorthand = (model or aircraft_type).upper()
    compact = " ".join(part for part in (maker, model or aircraft_type) if part).upper()

    if maker == "BOEING":
        if shorthand in {"737-8", "B737-8", "B38M", "737 MAX 8", "737-8 MAX"} or "737-8" in compact:
            add("Boeing 737 MAX 8")
            add("Boeing 737-8 MAX")
        if shorthand in {"737-9", "B737-9", "B39M", "737 MAX 9", "737-9 MAX"} or "737-9" in compact:
            add("Boeing 737 MAX 9")
            add("Boeing 737-9 MAX")
        if shorthand in {"737-7", "B737-7", "B37M", "737 MAX 7", "737-7 MAX"} or "737-7" in compact:
            add("Boeing 737 MAX 7")
        if shorthand in {"737-10", "B737-10", "B3XM", "737 MAX 10", "737-10 MAX"} or "737-10" in compact:
            add("Boeing 737 MAX 10")

    if maker in {"EMBRAER", "EMB"} and ("175" in shorthand or "E175" in shorthand):
        add("Embraer E175")
        add("Embraer 175")

    if maker == "AIRBUS" and shorthand.startswith("A"):
        add("Airbus " + shorthand)

    expanded: list[str] = []
    for term in terms:
        if not term:
            continue
        value = term if "AIRCRAFT" in term.upper() else term + " aircraft"
        if value not in expanded:
            expanded.append(value)

    return expanded


def _aircraft_photo_wikimedia_thumbnail(query: str) -> dict | None:
    search = _aircraft_photo_urlparse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "5",
        "prop": "pageimages|info",
        "pithumbsize": "900",
        "inprop": "url",
        "origin": "*",
    })
    url = "https://en.wikipedia.org/w/api.php?" + search

    try:
        request = _aircraft_photo_urlrequest.Request(
            url,
            headers={
                "User-Agent": "RTL-Pi-ADS-B-Tracker aircraft representative photo fallback",
                "Accept": "application/json",
            },
        )
        with _aircraft_photo_urlrequest.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))

        pages = list((payload.get("query") or {}).get("pages", {}).values())
        pages.sort(key=lambda page: int(page.get("index") or 9999))

        for page in pages:
            thumbnail = page.get("thumbnail") or {}
            image_url = thumbnail.get("source")
            if image_url:
                return {
                    "found": True,
                    "source": "Wikimedia",
                    "page_url": page.get("fullurl") or f"https://en.wikipedia.org/?curid={page.get('pageid')}",
                    "image_url": image_url,
                    "query": query,
                    "cached": False,
                    "match_level": "type",
                    "representative": True,
                    "type_query": query,
                    "looked_up_utc": int(time.time()),
                }
    except Exception:
        return None

    return None


def _aircraft_photo_lookup_representative_type(aircraft_type: str, model: str, manufacturer: str = "") -> dict:
    queries = _aircraft_photo_expand_model_terms(manufacturer, aircraft_type, model)
    if not queries:
        return {
            "found": False,
            "source": "type_fallback",
            "query": "",
            "cached": False,
            "match_level": "none",
            "representative": False,
            "looked_up_utc": int(time.time()),
            "message": "No aircraft manufacturer/type/model available for representative photo lookup.",
        }

    for query in queries:
        cache_key = _aircraft_photo_cache_key("type:" + query)
        cached = _aircraft_photo_cache_get(cache_key)
        if cached:
            cached["match_level"] = cached.get("match_level") or "type"
            cached["representative"] = True
            cached["type_query"] = cached.get("type_query") or query
            return cached

        result = _aircraft_photo_lookup_live(query, query)
        result["match_level"] = "type" if result.get("found") else "none"
        result["representative"] = bool(result.get("found"))
        result["type_query"] = query

        if result.get("found"):
            _aircraft_photo_cache_put(cache_key, result)
            return result

        wiki_result = _aircraft_photo_wikimedia_thumbnail(query)
        if wiki_result and wiki_result.get("found"):
            _aircraft_photo_cache_put(cache_key, wiki_result)
            return wiki_result

    return {
        "found": False,
        "source": "type_fallback",
        "query": queries[0],
        "queries_tried": queries,
        "cached": False,
        "match_level": "none",
        "representative": False,
        "looked_up_utc": int(time.time()),
        "message": "No representative aircraft type image found.",
    }


def _aircraft_photo_fallback_payload(query_string: str) -> tuple[dict, HTTPStatus]:
    parameters = urllib.parse.parse_qs(query_string, keep_blank_values=True)

    reg = _aircraft_photo_normalize_token((parameters.get("reg") or parameters.get("tail") or [""])[0])
    callsign = _aircraft_photo_normalize_token((parameters.get("callsign") or parameters.get("flight") or [""])[0])
    hex_value = _aircraft_photo_normalize_token((parameters.get("hex") or parameters.get("icao") or [""])[0])
    aircraft_type = _aircraft_photo_normalize_token((parameters.get("type") or [""])[0])
    model = _aircraft_photo_normalize_token((parameters.get("model") or [""])[0])
    manufacturer = _aircraft_photo_normalize_token((parameters.get("manufacturer") or parameters.get("make") or [""])[0])
    operator = _aircraft_photo_normalize_token((parameters.get("operator") or [""])[0])

    terms = [term for term in (reg, callsign, hex_value, manufacturer, aircraft_type, model, operator) if term]
    if not terms:
        return {"error": "Missing aircraft photo search terms."}, HTTPStatus.BAD_REQUEST

    query = " ".join(dict.fromkeys(terms))
    cache_key = _aircraft_photo_cache_key("exact:" + query)

    cached = _aircraft_photo_cache_get(cache_key)
    if cached:
        cached["match_level"] = cached.get("match_level") or "exact"
        cached["representative"] = bool(cached.get("representative"))
        return {"result": cached}, HTTPStatus.OK

    result = _aircraft_photo_lookup_live(query, reg or callsign or hex_value or query)
    if result.get("found"):
        result["match_level"] = result.get("match_level") or "exact"
        result["representative"] = False
        _aircraft_photo_cache_put(cache_key, result)
        return {"result": result}, HTTPStatus.OK

    type_result = _aircraft_photo_lookup_representative_type(aircraft_type, model, manufacturer)
    if type_result.get("found"):
        type_result["exact_lookup_message"] = result.get("message")
        return {"result": type_result}, HTTPStatus.OK

    return {"result": result}, HTTPStatus.OK

# /AIRCRAFT_PHOTO_MODEL_FALLBACK_IMPROVEMENT_PATCH_V1


# AIRCRAFT_PHOTO_REJECT_LOGO_IMAGES_PATCH_V2
# Reject site logos/social cards from aircraft photo fallback parsing.

def _aircraft_photo_is_rejected_image_url(url: str) -> bool:
    low = str(url or "").lower()
    if not low:
        return True

    if not any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return True

    reject_tokens = (
        "logo",
        "social",
        "icon",
        "avatar",
        "sprite",
        "blank",
        "placeholder",
        "favicon",
        "apple-touch",
        "loading",
        "spinner",
        "watermark",
        "no-photo",
        "no_photo",
        "default",
    )
    return any(token in low for token in reject_tokens)


def _aircraft_photo_first_image_from_html(html_text: str, page_url: str, source: str) -> str | None:
    candidates: list[str] = []

    # Inspect normal/lazy image attributes first.
    for match in re.finditer(r'''(?:src|data-src|data-lazy|data-original)=["\']([^"\']+)["\']''', html_text, re.I):
        src = _aircraft_photo_absolute_url(match.group(1), page_url)
        low = src.lower()

        if _aircraft_photo_is_rejected_image_url(src):
            continue

        if source == "jetphotos":
            if "jetphotos" not in low and "cdn.jetphotos" not in low:
                continue
            if "/assets/" in low:
                continue

        if source == "planespotters":
            if "planespotters" not in low and "cdn.planespotters" not in low:
                continue
            if "/assets/" in low:
                continue

        candidates.append(src)

    # Meta og:image is a fallback only if it is not a site logo/social card.
    for match in re.finditer(r'''property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']''', html_text, re.I):
        src = _aircraft_photo_absolute_url(match.group(1), page_url)
        low = src.lower()

        if _aircraft_photo_is_rejected_image_url(src):
            continue
        if source == "jetphotos" and "/assets/" in low:
            continue
        if source == "planespotters" and "/assets/" in low:
            continue

        candidates.append(src)

    return candidates[0] if candidates else None

# /AIRCRAFT_PHOTO_REJECT_LOGO_IMAGES_PATCH_V2

# AIRCRAFT_PHOTO_PRIORITIZE_BOEING_MAX_TERMS_PATCH_V2
# Prioritize normalized Boeing 737 MAX model terms for representative photo fallback.

def _aircraft_photo_expand_model_terms(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    maker = _aircraft_photo_normalize_token(manufacturer)
    aircraft_type = _aircraft_photo_normalize_token(aircraft_type)
    model = _aircraft_photo_normalize_token(model)

    raw_values = " ".join(part for part in (maker, model, aircraft_type) if part).upper()
    shorthand_values = {value for value in (model.upper(), aircraft_type.upper()) if value}
    terms: list[str] = []

    def add(value: str) -> None:
        value = _aircraft_photo_normalize_token(value)
        if not value:
            return
        if "AIRCRAFT" not in value.upper():
            value = value + " aircraft"
        if value not in terms:
            terms.append(value)

    # Put normalized Boeing MAX terms first, before shorthand like 737-9.
    if maker == "BOEING":
        if (
            "737-7" in raw_values or "737 MAX 7" in raw_values or
            "737-7 MAX" in raw_values or "B37M" in shorthand_values
        ):
            add("Boeing 737 MAX 7")
            add("Boeing 737-7 MAX")

        if (
            "737-8" in raw_values or "737 MAX 8" in raw_values or
            "737-8 MAX" in raw_values or "B38M" in shorthand_values
        ):
            add("Boeing 737 MAX 8")
            add("Boeing 737-8 MAX")

        if (
            "737-9" in raw_values or "737 MAX 9" in raw_values or
            "737-9 MAX" in raw_values or "B39M" in shorthand_values
        ):
            add("Boeing 737 MAX 9")
            add("Boeing 737-9 MAX")

        if (
            "737-10" in raw_values or "737 MAX 10" in raw_values or
            "737-10 MAX" in raw_values or "B3XM" in shorthand_values
        ):
            add("Boeing 737 MAX 10")
            add("Boeing 737-10 MAX")

    if maker in {"EMBRAER", "EMB"} and ("175" in raw_values or "E175" in raw_values):
        add("Embraer E175")
        add("Embraer 175")

    if maker == "AIRBUS":
        if model.startswith("A") or aircraft_type.startswith("A"):
            add("Airbus " + (model or aircraft_type))

    # Generic terms after normalized terms.
    add(" ".join(part for part in (maker, model or aircraft_type) if part).strip())
    add(" ".join(part for part in (maker, aircraft_type) if part).strip())
    add(model)
    add(aircraft_type)

    return terms

# /AIRCRAFT_PHOTO_PRIORITIZE_BOEING_MAX_TERMS_PATCH_V2

# AIRCRAFT_PHOTO_SMART_MODEL_QUERY_PATCH_V1
# Smart aircraft model query expansion for representative photo fallback.
# This avoids per-aircraft hardcoding by normalizing messy model strings
# such as 737MAX 8, B737MAX8, A320NEO, EMB175, etc.

def _aircraft_photo_compact_model_tokens(value: str) -> str:
    text = _aircraft_photo_normalize_token(value).upper()
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _aircraft_photo_smart_model_queries(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    maker = _aircraft_photo_normalize_token(manufacturer)
    aircraft_type = _aircraft_photo_normalize_token(aircraft_type)
    model = _aircraft_photo_normalize_token(model)
    raw = " ".join(part for part in (maker, model, aircraft_type) if part).strip()
    compact = _aircraft_photo_compact_model_tokens(raw)
    compact_no_space = compact.replace(" ", "")
    queries: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        value = re.sub(r"\s+", " ", value)
        if not value:
            return
        if "aircraft" not in value.lower() and "airliner" not in value.lower():
            value = value + " aircraft"
        if value not in queries:
            queries.append(value)

    # Generic Boeing 737 MAX recognizer. Handles 737MAX8, 737MAX 8, 737 MAX-8, B38M, etc.
    max_match = re.search(r"(?:B?737|BOEING737)\s*(?:MAX)?\s*([789]|10)\b", compact_no_space)
    if "737MAX" in compact_no_space or "B37M" in compact_no_space or "B38M" in compact_no_space or "B39M" in compact_no_space or "B3XM" in compact_no_space:
        if "B37M" in compact_no_space:
            variant = "7"
        elif "B38M" in compact_no_space:
            variant = "8"
        elif "B39M" in compact_no_space:
            variant = "9"
        elif "B3XM" in compact_no_space:
            variant = "10"
        else:
            m = re.search(r"737MAX(10|[789])", compact_no_space)
            variant = m.group(1) if m else ""
        if variant:
            add(f"Boeing 737 MAX {variant}")
            add(f"737 MAX {variant}")

    # Generic Boeing 737 dash shorthand. Handles 737-8 as 737 MAX 8 but without a hardcoded table.
    dash_match = re.search(r"(?:B?737|BOEING737)\s*(10|[789])\b", compact_no_space)
    if maker == "BOEING" and dash_match:
        variant = dash_match.group(1)
        add(f"Boeing 737 MAX {variant}")
        add(f"737 MAX {variant}")

    # Airbus neo/ceo style normalization.
    airbus_match = re.search(r"A(318|319|320|321|330|340|350|380)(NEO|CEO)?", compact_no_space)
    if maker == "AIRBUS" and airbus_match:
        family = airbus_match.group(1)
        suffix = airbus_match.group(2) or ""
        add(f"Airbus A{family}{suffix}")
        if suffix:
            add(f"Airbus A{family} {suffix}")

    # Embraer E-Jet shorthand normalization.
    emb_match = re.search(r"(?:E|EMB|ERJ)?\s*(170|175|190|195)", compact_no_space)
    if maker in {"EMBRAER", "EMB", "ERJ"} and emb_match:
        variant = emb_match.group(1)
        add(f"Embraer E{variant}")
        add(f"Embraer {variant}")

    # Generic phrase expansion. These are still useful for unusual aircraft.
    add(" ".join(part for part in (maker, model or aircraft_type) if part).strip())
    add(" ".join(part for part in (maker, aircraft_type) if part).strip())
    add(raw)
    add(model)
    add(aircraft_type)

    # Add explicit photo/search intent versions for sites that rank better with these terms.
    expanded: list[str] = []
    for query in queries:
        for candidate in (query, query.replace(" aircraft", " airliner"), query.replace(" aircraft", " photo")):
            candidate = re.sub(r"\s+", " ", candidate).strip()
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded

def _aircraft_photo_expand_model_terms(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    return _aircraft_photo_smart_model_queries(manufacturer, aircraft_type, model)

# /AIRCRAFT_PHOTO_SMART_MODEL_QUERY_PATCH_V1

# AIRCRAFT_PHOTO_MODEL_SYNONYM_QUERY_PATCH_V1
# Add synonym-style aircraft model query expansion for representative photo fallback.
# This handles common database/model text like ERJ 170-200 LR -> Embraer E175
# and messy Boeing MAX strings without relying on aircraft tail numbers.

def _aircraft_photo_smart_model_queries(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    maker = _aircraft_photo_normalize_token(manufacturer)
    aircraft_type = _aircraft_photo_normalize_token(aircraft_type)
    model = _aircraft_photo_normalize_token(model)
    raw = ' '.join(part for part in (maker, model, aircraft_type) if part).strip()
    raw_upper = raw.upper()
    compact = re.sub(r'[^A-Z0-9]+', '', raw_upper)
    queries: list[str] = []

    def add(value: str) -> None:
        value = str(value or '').strip()
        value = re.sub(r'\s+', ' ', value)
        if not value:
            return
        if 'aircraft' not in value.lower() and 'airliner' not in value.lower() and 'photo' not in value.lower():
            value = value + ' aircraft'
        if value not in queries:
            queries.append(value)

    # Boeing 737 MAX family. Handles 737-8, 737MAX 8, 737MAX8, B38M, 737-9, etc.
    if maker == 'BOEING' or 'BOEING' in raw_upper or '737' in raw_upper:
        variant = ''
        if 'B37M' in compact:
            variant = '7'
        elif 'B38M' in compact:
            variant = '8'
        elif 'B39M' in compact:
            variant = '9'
        elif 'B3XM' in compact:
            variant = '10'
        else:
            m = re.search(r'737(?:MAX)?(10|[789])', compact)
            if m:
                variant = m.group(1)
        if variant:
            add(f'Boeing 737 MAX {variant}')
            add(f'737 MAX {variant}')
            add(f'Boeing 737-{variant} MAX')

    # Embraer E-Jet family. Many databases report E175 as ERJ 170-200 LR/LL.
    if maker in {'EMBRAER', 'EMB', 'ERJ'} or 'EMBRAER' in raw_upper or 'ERJ' in raw_upper or 'EMB' in raw_upper:
        if any(token in compact for token in ('ERJ170200', 'ERJ175', 'EMB175', 'E175', '170200')):
            add('Embraer E175')
            add('Embraer 175')
            add('Embraer ERJ-175')
            add('Embraer 170-200')
        elif any(token in compact for token in ('ERJ170100', 'EMB170', 'E170')):
            add('Embraer E170')
            add('Embraer 170')
        elif any(token in compact for token in ('ERJ190', 'EMB190', 'E190')):
            add('Embraer E190')
            add('Embraer 190')
        elif any(token in compact for token in ('ERJ195', 'EMB195', 'E195')):
            add('Embraer E195')
            add('Embraer 195')

    # Airbus generic recognition.
    if maker == 'AIRBUS' or 'AIRBUS' in raw_upper:
        m = re.search(r'A(318|319|320|321|330|340|350|380)(NEO|CEO)?', compact)
        if m:
            family = m.group(1)
            suffix = m.group(2) or ''
            add(f'Airbus A{family}{suffix}')
            if suffix:
                add(f'Airbus A{family} {suffix}')

    # Generic phrase expansion after synonyms.
    add(' '.join(part for part in (maker, model or aircraft_type) if part).strip())
    add(' '.join(part for part in (maker, aircraft_type) if part).strip())
    add(raw)
    add(model)
    add(aircraft_type)

    # Add intent variants. Wikimedia tends to do well with aircraft/airliner,
    # while photo-site searches often rank better with photo.
    expanded: list[str] = []
    for query in queries:
        variants = [
            query,
            query.replace(' aircraft', ' airliner'),
            query.replace(' aircraft', ' photo'),
        ]
        for candidate in variants:
            candidate = re.sub(r'\s+', ' ', candidate).strip()
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded

def _aircraft_photo_expand_model_terms(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    return _aircraft_photo_smart_model_queries(manufacturer, aircraft_type, model)

# /AIRCRAFT_PHOTO_MODEL_SYNONYM_QUERY_PATCH_V1

# AIRCRAFT_PHOTO_AIRBUS_MODEL_SYNONYM_PATCH_V1
# Add stronger Airbus model synonym/query expansion for representative photo fallback.
# Handles strings such as A320 214, A320-214, A321 271N, A319 112, etc.

def _aircraft_photo_smart_model_queries(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    maker = _aircraft_photo_normalize_token(manufacturer)
    aircraft_type = _aircraft_photo_normalize_token(aircraft_type)
    model = _aircraft_photo_normalize_token(model)
    raw = " ".join(part for part in (maker, model, aircraft_type) if part).strip()
    raw_upper = raw.upper()
    compact = re.sub(r"[^A-Z0-9]+", "", raw_upper)
    queries: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        value = re.sub(r"\s+", " ", value)
        if not value:
            return
        if "aircraft" not in value.lower() and "airliner" not in value.lower() and "photo" not in value.lower():
            value = value + " aircraft"
        if value not in queries:
            queries.append(value)

    # Boeing 737 MAX family normalization.
    if maker == "BOEING" or "BOEING" in raw_upper or "737" in raw_upper:
        variant = ""
        if "B37M" in compact:
            variant = "7"
        elif "B38M" in compact:
            variant = "8"
        elif "B39M" in compact:
            variant = "9"
        elif "B3XM" in compact:
            variant = "10"
        else:
            m = re.search(r"737(?:MAX)?(10|[789])", compact)
            if m:
                variant = m.group(1)
        if variant:
            add(f"Boeing 737 MAX {variant}")
            add(f"737 MAX {variant}")
            add(f"Boeing 737-{variant} MAX")

    # Embraer E-Jet family synonyms.
    if maker in {"EMBRAER", "EMB", "ERJ"} or "EMBRAER" in raw_upper or "ERJ" in raw_upper or "EMB" in raw_upper:
        if any(token in compact for token in ("ERJ170200", "ERJ175", "EMB175", "E175", "170200")):
            add("Embraer E175")
            add("Embraer 175")
            add("Embraer ERJ-175")
            add("Embraer 170-200")
        elif any(token in compact for token in ("ERJ170100", "EMB170", "E170")):
            add("Embraer E170")
            add("Embraer 170")
        elif any(token in compact for token in ("ERJ190", "EMB190", "E190")):
            add("Embraer E190")
            add("Embraer 190")
        elif any(token in compact for token in ("ERJ195", "EMB195", "E195")):
            add("Embraer E195")
            add("Embraer 195")

    # Airbus family and subtype synonyms. A320 214 = A320-214 / A320ceo family.
    airbus_like = maker == "AIRBUS" or "AIRBUS" in raw_upper or compact.startswith("A3")
    if airbus_like:
        m = re.search(r"A(318|319|320|321|330|340|350|380)([0-9A-Z]{0,4})", compact)
        if m:
            family = m.group(1)
            subtype = m.group(2) or ""
            add(f"Airbus A{family}")
            if subtype:
                add(f"Airbus A{family}-{subtype}")
                add(f"Airbus A{family} {subtype}")
            if family in {"318", "319", "320", "321"}:
                add(f"Airbus A{family} family")
                # 3-digit non-N suffixes are usually ceo-family variants, e.g. A320-214.
                if subtype and not subtype.endswith("N"):
                    add(f"Airbus A{family}ceo")
                if subtype.endswith("N") or "NEO" in compact:
                    add(f"Airbus A{family}neo")
            elif family == "350":
                add("Airbus A350")
            elif family == "380":
                add("Airbus A380")

    # Generic phrase expansion after synonyms.
    add(" ".join(part for part in (maker, model or aircraft_type) if part).strip())
    add(" ".join(part for part in (maker, aircraft_type) if part).strip())
    add(raw)
    add(model)
    add(aircraft_type)

    expanded: list[str] = []
    for query in queries:
        variants = [query, query.replace(" aircraft", " airliner"), query.replace(" aircraft", " photo")]
        for candidate in variants:
            candidate = re.sub(r"\s+", " ", candidate).strip()
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded

def _aircraft_photo_expand_model_terms(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    return _aircraft_photo_smart_model_queries(manufacturer, aircraft_type, model)

# /AIRCRAFT_PHOTO_AIRBUS_MODEL_SYNONYM_PATCH_V1

# AIRCRAFT_PHOTO_COMMONS_OPERATOR_FALLBACK_PATCH_V1
# Add Wikimedia Commons + operator-aware representative photo fallback.

def _aircraft_photo_wikimedia_commons_thumbnail(query: str) -> dict | None:
    search = _aircraft_photo_urlparse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "8",
        "prop": "imageinfo|info",
        "iiprop": "url|mime",
        "iiurlwidth": "1000",
        "inprop": "url",
        "origin": "*",
    })
    url = "https://commons.wikimedia.org/w/api.php?" + search
    try:
        request = _aircraft_photo_urlrequest.Request(
            url,
            headers={
                "User-Agent": "RTL-Pi-ADS-B-Tracker aircraft representative photo fallback",
                "Accept": "application/json",
            },
        )
        with _aircraft_photo_urlrequest.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        pages = list((payload.get("query") or {}).get("pages", {}).values())
        pages.sort(key=lambda page: int(page.get("index") or 9999))
        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = str(info.get("mime") or "").lower()
            image_url = info.get("thumburl") or info.get("url")
            if not image_url:
                continue
            if mime and not mime.startswith("image/"):
                continue
            if _aircraft_photo_is_rejected_image_url(image_url):
                continue
            return {
                "found": True,
                "source": "Wikimedia Commons",
                "page_url": page.get("fullurl") or f"https://commons.wikimedia.org/?curid={page.get('pageid')}",
                "image_url": image_url,
                "query": query,
                "cached": False,
                "match_level": "type",
                "representative": True,
                "type_query": query,
                "looked_up_utc": int(time.time()),
            }
    except Exception:
        return None
    return None

def _aircraft_photo_operator_queries(operator: str, manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    operator = _aircraft_photo_normalize_token(operator)
    base_queries = _aircraft_photo_expand_model_terms(manufacturer, aircraft_type, model)
    if not operator:
        return base_queries
    expanded: list[str] = []
    for query in base_queries:
        cleaned = re.sub(r"\b(aircraft|airliner|photo)\b", "", query, flags=re.I).strip()
        for candidate in (
            f"{operator} {cleaned} aircraft",
            f"{operator} {cleaned} airliner",
            f"{cleaned} {operator} aircraft",
            query,
        ):
            candidate = re.sub(r"\s+", " ", candidate).strip()
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded

def _aircraft_photo_lookup_representative_type(aircraft_type: str, model: str, manufacturer: str = "", operator: str = "") -> dict:
    queries = _aircraft_photo_operator_queries(operator, manufacturer, aircraft_type, model)
    if not queries:
        return {
            "found": False,
            "source": "type_fallback",
            "query": "",
            "cached": False,
            "match_level": "none",
            "representative": False,
            "looked_up_utc": int(time.time()),
            "message": "No aircraft manufacturer/type/model available for representative photo lookup.",
        }
    for query in queries:
        cache_key = _aircraft_photo_cache_key("type:" + query)
        cached = _aircraft_photo_cache_get(cache_key)
        if cached:
            cached["match_level"] = cached.get("match_level") or "type"
            cached["representative"] = True
            cached["type_query"] = cached.get("type_query") or query
            return cached
        result = _aircraft_photo_lookup_live(query, query)
        result["match_level"] = "type" if result.get("found") else "none"
        result["representative"] = bool(result.get("found"))
        result["type_query"] = query
        if result.get("found"):
            _aircraft_photo_cache_put(cache_key, result)
            return result
        wiki_result = _aircraft_photo_wikimedia_thumbnail(query)
        if wiki_result and wiki_result.get("found"):
            _aircraft_photo_cache_put(cache_key, wiki_result)
            return wiki_result
        commons_result = _aircraft_photo_wikimedia_commons_thumbnail(query)
        if commons_result and commons_result.get("found"):
            _aircraft_photo_cache_put(cache_key, commons_result)
            return commons_result
    return {
        "found": False,
        "source": "type_fallback",
        "query": queries[0],
        "queries_tried": queries[:20],
        "cached": False,
        "match_level": "none",
        "representative": False,
        "looked_up_utc": int(time.time()),
        "message": "No representative aircraft type image found.",
    }

def _aircraft_photo_fallback_payload(query_string: str) -> tuple[dict, HTTPStatus]:
    parameters = urllib.parse.parse_qs(query_string, keep_blank_values=True)
    reg = _aircraft_photo_normalize_token((parameters.get("reg") or parameters.get("tail") or [""])[0])
    callsign = _aircraft_photo_normalize_token((parameters.get("callsign") or parameters.get("flight") or [""])[0])
    hex_value = _aircraft_photo_normalize_token((parameters.get("hex") or parameters.get("icao") or [""])[0])
    aircraft_type = _aircraft_photo_normalize_token((parameters.get("type") or [""])[0])
    model = _aircraft_photo_normalize_token((parameters.get("model") or [""])[0])
    manufacturer = _aircraft_photo_normalize_token((parameters.get("manufacturer") or parameters.get("make") or [""])[0])
    operator = _aircraft_photo_normalize_token((parameters.get("operator") or [""])[0])
    terms = [term for term in (reg, callsign, hex_value, manufacturer, aircraft_type, model, operator) if term]
    if not terms:
        return {"error": "Missing aircraft photo search terms."}, HTTPStatus.BAD_REQUEST
    query = " ".join(dict.fromkeys(terms))
    cache_key = _aircraft_photo_cache_key("exact:" + query)
    cached = _aircraft_photo_cache_get(cache_key)
    if cached:
        cached["match_level"] = cached.get("match_level") or "exact"
        cached["representative"] = bool(cached.get("representative"))
        return {"result": cached}, HTTPStatus.OK
    result = _aircraft_photo_lookup_live(query, reg or callsign or hex_value or query)
    if result.get("found"):
        result["match_level"] = result.get("match_level") or "exact"
        result["representative"] = False
        _aircraft_photo_cache_put(cache_key, result)
        return {"result": result}, HTTPStatus.OK
    type_result = _aircraft_photo_lookup_representative_type(aircraft_type, model, manufacturer, operator)
    if type_result.get("found"):
        type_result["exact_lookup_message"] = result.get("message")
        return {"result": type_result}, HTTPStatus.OK
    return {"result": result}, HTTPStatus.OK

# /AIRCRAFT_PHOTO_COMMONS_OPERATOR_FALLBACK_PATCH_V1

# AIRCRAFT_PHOTO_STRICT_IMAGE_FILTER_PATCH_V1
# Final strict filter for representative aircraft photo fallback results.
# Rejects logos, SVG-derived logo thumbnails, icons, and other non-aircraft assets.

def _aircraft_photo_is_rejected_image_url(url: str) -> bool:
    low = str(url or "").lower()
    if not low:
        return True
    if not any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return True
    reject_tokens = (
        "logo", "svg", "icon", "avatar", "sprite", "blank", "placeholder",
        "favicon", "apple-touch", "loading", "spinner", "watermark",
        "no-photo", "no_photo", "default", "seal", "emblem", "wordmark"
    )
    return any(token in low for token in reject_tokens)

def _aircraft_photo_page_looks_like_logo(page: dict, image_url: str) -> bool:
    title = str(page.get("title") or "").lower()
    fullurl = str(page.get("fullurl") or "").lower()
    low = str(image_url or "").lower()
    reject_tokens = ("logo", "seal", "emblem", "wordmark", "svg")
    return any(token in title or token in fullurl or token in low for token in reject_tokens)

def _aircraft_photo_wikimedia_thumbnail(query: str) -> dict | None:
    search = _aircraft_photo_urlparse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "8",
        "prop": "pageimages|info",
        "pithumbsize": "900",
        "inprop": "url",
        "origin": "*",
    })
    url = "https://en.wikipedia.org/w/api.php?" + search
    try:
        request = _aircraft_photo_urlrequest.Request(
            url,
            headers={
                "User-Agent": "RTL-Pi-ADS-B-Tracker aircraft representative photo fallback",
                "Accept": "application/json",
            },
        )
        with _aircraft_photo_urlrequest.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        pages = list((payload.get("query") or {}).get("pages", {}).values())
        pages.sort(key=lambda page: int(page.get("index") or 9999))
        for page in pages:
            thumbnail = page.get("thumbnail") or {}
            image_url = thumbnail.get("source")
            if not image_url:
                continue
            if _aircraft_photo_is_rejected_image_url(image_url):
                continue
            if _aircraft_photo_page_looks_like_logo(page, image_url):
                continue
            return {
                "found": True,
                "source": "Wikimedia",
                "page_url": page.get("fullurl") or f"https://en.wikipedia.org/?curid={page.get('pageid')}",
                "image_url": image_url,
                "query": query,
                "cached": False,
                "match_level": "type",
                "representative": True,
                "type_query": query,
                "looked_up_utc": int(time.time()),
            }
    except Exception:
        return None
    return None

def _aircraft_photo_wikimedia_commons_thumbnail(query: str) -> dict | None:
    search = _aircraft_photo_urlparse.urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "12",
        "prop": "imageinfo|info",
        "iiprop": "url|mime",
        "iiurlwidth": "1000",
        "inprop": "url",
        "origin": "*",
    })
    url = "https://commons.wikimedia.org/w/api.php?" + search
    try:
        request = _aircraft_photo_urlrequest.Request(
            url,
            headers={
                "User-Agent": "RTL-Pi-ADS-B-Tracker aircraft representative photo fallback",
                "Accept": "application/json",
            },
        )
        with _aircraft_photo_urlrequest.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        pages = list((payload.get("query") or {}).get("pages", {}).values())
        pages.sort(key=lambda page: int(page.get("index") or 9999))
        for page in pages:
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = str(info.get("mime") or "").lower()
            image_url = info.get("thumburl") or info.get("url")
            if not image_url:
                continue
            if mime and not mime.startswith("image/"):
                continue
            if _aircraft_photo_is_rejected_image_url(image_url):
                continue
            if _aircraft_photo_page_looks_like_logo(page, image_url):
                continue
            return {
                "found": True,
                "source": "Wikimedia Commons",
                "page_url": page.get("fullurl") or f"https://commons.wikimedia.org/?curid={page.get('pageid')}",
                "image_url": image_url,
                "query": query,
                "cached": False,
                "match_level": "type",
                "representative": True,
                "type_query": query,
                "looked_up_utc": int(time.time()),
            }
    except Exception:
        return None
    return None

# /AIRCRAFT_PHOTO_STRICT_IMAGE_FILTER_PATCH_V1

# AIRCRAFT_PHOTO_MORE_MODEL_SYNONYMS_PATCH_V1
# Add broader model synonym/query expansion for common missing representative aircraft photos.
# Covers Airbus A321-271NX, Boeing 737NG 900ER/W, Boeing 757 26D/W, and related variants.

def _aircraft_photo_smart_model_queries(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    maker = _aircraft_photo_normalize_token(manufacturer)
    aircraft_type = _aircraft_photo_normalize_token(aircraft_type)
    model = _aircraft_photo_normalize_token(model)
    raw = " ".join(part for part in (maker, model, aircraft_type) if part).strip()
    raw_upper = raw.upper()
    compact = re.sub(r"[^A-Z0-9]+", "", raw_upper)
    queries: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        value = re.sub(r"\s+", " ", value)
        if not value:
            return
        if "aircraft" not in value.lower() and "airliner" not in value.lower() and "photo" not in value.lower():
            value = value + " aircraft"
        if value not in queries:
            queries.append(value)

    operator_context = ""  # operator remains handled by _aircraft_photo_operator_queries()

    # Airbus A320/A321 family, including subtype strings like A321-271NX and A320 214.
    airbus_like = maker == "AIRBUS" or "AIRBUS" in raw_upper or compact.startswith("A3")
    if airbus_like:
        m = re.search(r"A(318|319|320|321|330|340|350|380)([0-9A-Z]{0,5})", compact)
        if m:
            family = m.group(1)
            subtype = m.group(2) or ""
            add(f"Airbus A{family}")
            if subtype:
                add(f"Airbus A{family}-{subtype}")
                add(f"Airbus A{family} {subtype}")
            if family in {"318", "319", "320", "321"}:
                add(f"Airbus A{family} family")
                if subtype.endswith("N") or "NEO" in compact or "NX" in subtype:
                    add(f"Airbus A{family}neo")
                    add(f"Airbus A{family} neo")
                else:
                    add(f"Airbus A{family}ceo")
            elif family == "350":
                add("Airbus A350")
            elif family == "380":
                add("Airbus A380")

    # Boeing 737 MAX and 737NG family.
    boeing_like = maker == "BOEING" or "BOEING" in raw_upper or "737" in raw_upper or "757" in raw_upper
    if boeing_like:
        # 737 MAX variants: 737-8, 737MAX8, B38M, etc.
        max_variant = ""
        if "B37M" in compact:
            max_variant = "7"
        elif "B38M" in compact:
            max_variant = "8"
        elif "B39M" in compact:
            max_variant = "9"
        elif "B3XM" in compact:
            max_variant = "10"
        else:
            m = re.search(r"737(?:MAX)?(10|[789])", compact)
            if m and ("MAX" in compact or re.search(r"737[- ]?(10|[789])", raw_upper)):
                max_variant = m.group(1)
        if max_variant:
            add(f"Boeing 737 MAX {max_variant}")
            add(f"737 MAX {max_variant}")
            add(f"Boeing 737-{max_variant} MAX")

        # 737NG / 900ER / winglet variants, e.g. 737NG 900ER/W.
        if "737NG" in compact or "737900" in compact or "900ER" in compact or "B739" in compact:
            add("Boeing 737-900ER")
            add("Boeing 737-900 ER")
            add("Boeing 737-900")
            add("Boeing 737NG 900ER")
            add("Boeing 737-900ER winglets")
        elif "737800" in compact or "738" == compact or "B738" in compact:
            add("Boeing 737-800")
            add("Boeing 737NG 800")
            add("Boeing 737-800 winglets")
        elif "737700" in compact or "B737700" in compact or "B737" in compact:
            add("Boeing 737-700")
            add("Boeing 737NG 700")

        # Boeing 757 subtype strings: 757 26D/W -> 757-200 with winglets.
        if "757" in compact:
            add("Boeing 757-200")
            add("Boeing 757-200 winglets")
            add("Boeing 757-26D")
            add("Boeing 757")
            if "300" in compact:
                add("Boeing 757-300")

    # Embraer E-Jet family. EMB-175 LL and ERJ 170-200 LR/LL are E175-family.
    if maker in {"EMBRAER", "EMB", "ERJ"} or "EMBRAER" in raw_upper or "ERJ" in raw_upper or "EMB" in raw_upper:
        if any(token in compact for token in ("ERJ170200", "ERJ175", "EMB175", "E175", "170200", "EMB175LL", "EMB175LR")):
            add("Embraer E175")
            add("Embraer 175")
            add("Embraer ERJ-175")
            add("Embraer 170-200")
        elif any(token in compact for token in ("ERJ170100", "EMB170", "E170")):
            add("Embraer E170")
            add("Embraer 170")
        elif any(token in compact for token in ("ERJ190", "EMB190", "E190")):
            add("Embraer E190")
            add("Embraer 190")
        elif any(token in compact for token in ("ERJ195", "EMB195", "E195")):
            add("Embraer E195")
            add("Embraer 195")

    # Generic phrase expansion after known synonyms.
    add(" ".join(part for part in (maker, model or aircraft_type) if part).strip())
    add(" ".join(part for part in (maker, aircraft_type) if part).strip())
    add(raw)
    add(model)
    add(aircraft_type)

    expanded: list[str] = []
    for query in queries:
        variants = [query, query.replace(" aircraft", " airliner"), query.replace(" aircraft", " photo")]
        for candidate in variants:
            candidate = re.sub(r"\s+", " ", candidate).strip()
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded

def _aircraft_photo_expand_model_terms(manufacturer: str, aircraft_type: str, model: str) -> list[str]:
    return _aircraft_photo_smart_model_queries(manufacturer, aircraft_type, model)

# /AIRCRAFT_PHOTO_MORE_MODEL_SYNONYMS_PATCH_V1


# LOCAL_TAR1090_AIRCRAFT_CACHE_FALLBACK_SAFE_V2
# Local tar1090-db aircraft.csv.gz cache fallback for ADSBDB misses.

_LOCAL_TAR1090_CACHE = {
    "path": None,
    "mtime": None,
    "records": None,
}


def _local_tar1090_candidate_paths() -> list:
    import os as _os
    from pathlib import Path as _Path

    candidates = []
    for env_name in ("RTL_PI_SETTINGS_DIR", "SETTINGS_DIR"):
        env_value = _os.environ.get(env_name)
        if env_value:
            candidates.append(_Path(env_value) / "aircraft_hex_db.json")

    here = _Path(__file__).resolve()
    candidates.extend([
        _Path("/opt/rtl-pi-adsb-tracker/settings/aircraft_hex_db.json"),
        here.parent.parent / "settings" / "aircraft_hex_db.json",
        here.parent / "settings" / "aircraft_hex_db.json",
        _Path("runtime/settings/aircraft_hex_db.json"),
        _Path("settings/aircraft_hex_db.json"),
    ])

    unique = []
    seen = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


_LOCAL_TAR1090_CACHE = {"path": None, "mtime": None, "records": None}


def _local_tar1090_norm_hex(value: str) -> str:
    return str(value or "").strip().upper().replace("~", "")


def _local_tar1090_load_records() -> tuple[dict, str | None]:
    import json as _json

    for path in _local_tar1090_candidate_paths():
        try:
            if not path.exists():
                continue
            mtime = path.stat().st_mtime
            if (
                _LOCAL_TAR1090_CACHE.get("records") is not None
                and _LOCAL_TAR1090_CACHE.get("path") == str(path)
                and _LOCAL_TAR1090_CACHE.get("mtime") == mtime
            ):
                return _LOCAL_TAR1090_CACHE["records"], str(path)

            with path.open("r", encoding="utf-8") as handle:
                payload = _json.load(handle)

            records = {}
            if isinstance(payload, dict) and isinstance(payload.get("records"), dict):
                for key, record in payload["records"].items():
                    hex_key = _local_tar1090_norm_hex(key)
                    if hex_key:
                        records[hex_key] = record
            elif isinstance(payload, dict) and isinstance(payload.get("aircraft"), list):
                for record in payload["aircraft"]:
                    if isinstance(record, dict):
                        hex_key = _local_tar1090_norm_hex(record.get("hex") or record.get("icao") or record.get("icao_hex") or record.get("mode_s"))
                        if hex_key:
                            records[hex_key] = record
            elif isinstance(payload, dict):
                for key, record in payload.items():
                    hex_key = _local_tar1090_norm_hex(key)
                    if hex_key:
                        records[hex_key] = record
            elif isinstance(payload, list):
                for record in payload:
                    if isinstance(record, dict):
                        hex_key = _local_tar1090_norm_hex(record.get("hex") or record.get("icao") or record.get("icao_hex") or record.get("mode_s"))
                        if hex_key:
                            records[hex_key] = record

            _LOCAL_TAR1090_CACHE.update({"path": str(path), "mtime": mtime, "records": records})
            return records, str(path)
        except Exception:
            continue

    _LOCAL_TAR1090_CACHE.update({"path": None, "mtime": None, "records": {}})
    return {}, None


def _local_tar1090_value(record, *names):
    if isinstance(record, dict):
        for name in names:
            if name in record and record[name] not in ("", None):
                return record[name]
    elif isinstance(record, list):
        index_by_name = {
            "registration": 1, "reg": 1,
            "type": 2, "icao_type": 2,
            "operator": 3, "owner": 3, "registered_owner": 3,
            "description": 4, "desc": 4,
        }
        for name in names:
            index = index_by_name.get(name)
            if index is not None and len(record) > index and record[index] not in ("", None):
                return record[index]
    elif isinstance(record, str):
        return record
    return ""


def _local_tar1090_manufacturer_from_desc(description: str, icao_type: str) -> str:
    text = str(description or "").strip()
    if not text:
        return ""

    upper = text.upper()
    known = [
        "AIRBUS", "BOEING", "EMBRAER", "BOMBARDIER", "CANADAIR",
        "CESSNA", "PIPER", "BEECH", "BEECHCRAFT", "GULFSTREAM",
        "DASSAULT", "MCDONNELL DOUGLAS", "ROBINSON", "SIKORSKY",
        "LEARJET", "DIAMOND", "CIRRUS",
    ]
    for maker in known:
        if upper.startswith(maker):
            return maker.title() if maker != "MCDONNELL DOUGLAS" else "McDonnell Douglas"

    first = text.split()[0] if text.split() else ""
    if first and first.upper() != str(icao_type or "").upper():
        return first.title()
    return ""


def _local_tar1090_record_to_aircraft(hex_value: str, record) -> dict:
    registration = _local_tar1090_value(record, "registration", "reg", "r", "tail", "n_number", "nnumber")
    icao_type = _local_tar1090_value(record, "icao_type", "type", "t", "icaoType", "icao_type_code")
    description = _local_tar1090_value(record, "description", "desc", "model", "aircraft", "aircraft_type", "long_type")
    operator = _local_tar1090_value(record, "registered_owner", "owner", "operator", "ownop", "op", "o", "airline")
    manufacturer = _local_tar1090_value(record, "manufacturer", "make", "mfr", "builder") or _local_tar1090_manufacturer_from_desc(description, icao_type)

    return {
        "hex": hex_value,
        "registration": str(registration or "").strip(),
        "manufacturer": str(manufacturer or "").strip(),
        "type": str((description or icao_type or "")).strip(),
        "icao_type": str(icao_type or "").strip(),
        "registered_owner": str(operator or "").strip(),
        "source": "local tar1090-db aircraft cache",
    }


def _local_tar1090_lookup_response(query_string: str) -> tuple[dict, HTTPStatus]:
    import re as _re
    import urllib.parse as _urlparse

    params = _urlparse.parse_qs(query_string, keep_blank_values=True)
    hex_value = _local_tar1090_norm_hex((params.get("hex") or params.get("icao") or params.get("icao_hex") or [""])[0])

    if not _re.fullmatch(r"[0-9A-F]{6}", hex_value or ""):
        return {"error": "A 6-character ICAO hex is required."}, HTTPStatus.BAD_REQUEST

    records, cache_path = _local_tar1090_load_records()
    record = records.get(hex_value)

    if not record:
        return {
            "found": False,
            "hex": hex_value,
            "source": "local tar1090-db aircraft cache",
            "cache_path": cache_path,
            "cache_count": len(records),
            "message": "Aircraft not found in local tar1090-db cache.",
        }, HTTPStatus.OK

    return {
        "found": True,
        "hex": hex_value,
        "aircraft": _local_tar1090_record_to_aircraft(hex_value, record),
        "flightroute": None,
        "source": "local tar1090-db aircraft cache",
        "cache_path": cache_path,
        "cache_count": len(records),
    }, HTTPStatus.OK

# /LOCAL_TAR1090_AIRCRAFT_CACHE_FALLBACK_SAFE_V2


# AIRLABS_ROUTE2_IATA_VARIANTS_BACKEND_PATCH_V1
# Alternate AirLabs route endpoint with flight_icao and flight_iata support.

def _airlabs_route2_get_api_key() -> str:
    import os as _os
    import json as _json
    from pathlib import Path as _Path

    for env_name in ("AIRLABS_API_KEY", "AIRLABS_KEY"):
        value = _os.environ.get(env_name)
        if value:
            return str(value).strip()

    candidates = []
    settings_dir = _os.environ.get("RTL_PI_SETTINGS_DIR") or _os.environ.get("SETTINGS_DIR")
    if settings_dir:
        candidates.append(_Path(settings_dir) / "airlabs.json")
        candidates.append(_Path(settings_dir) / "airlabs_settings.json")

    here = _Path(__file__).resolve()
    candidates.extend([
        _Path("/opt/rtl-pi-adsb-tracker/settings/airlabs.json"),
        _Path("/opt/rtl-pi-adsb-tracker/settings/airlabs_settings.json"),
        here.parent.parent / "settings" / "airlabs.json",
        here.parent.parent / "settings" / "airlabs_settings.json",
        here.parent / "settings" / "airlabs.json",
        here.parent / "settings" / "airlabs_settings.json",
        _Path("settings/airlabs.json"),
        _Path("settings/airlabs_settings.json"),
    ])

    for path in candidates:
        try:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            if text.startswith("{"):
                payload = _json.loads(text)
                for key_name in ("api_key", "key", "airlabs_api_key", "AIRLABS_API_KEY"):
                    if payload.get(key_name):
                        return str(payload[key_name]).strip()
            else:
                return text.strip()
        except Exception:
            continue

    for helper_name in ("_airlabs_get_api_key", "_get_airlabs_api_key", "get_airlabs_api_key", "_load_airlabs_api_key"):
        helper = globals().get(helper_name)
        if callable(helper):
            try:
                value = helper()
                if value:
                    return str(value).strip()
            except Exception:
                pass

    return ""


_AIRLABS_ROUTE2_CACHE = {}


def _airlabs_route2_cache_key(kind: str, flight: str) -> str:
    return f"{kind}:{str(flight or '').strip().upper()}"


def _airlabs_route2_extract_route(item: dict, requested_kind: str, requested_flight: str) -> dict:
    import time as _time

    dep_iata = item.get("dep_iata") or item.get("departure_iata")
    dep_icao = item.get("dep_icao") or item.get("departure_icao")
    arr_iata = item.get("arr_iata") or item.get("arrival_iata")
    arr_icao = item.get("arr_icao") or item.get("arrival_icao")

    return {
        "found": bool(dep_iata or dep_icao or arr_iata or arr_icao),
        "from": dep_iata or dep_icao or "Route unavailable",
        "to": arr_iata or arr_icao or "Route unavailable",
        "dep_iata": dep_iata,
        "dep_icao": dep_icao,
        "dep_name": item.get("dep_name") or item.get("departure_name"),
        "arr_iata": arr_iata,
        "arr_icao": arr_icao,
        "arr_name": item.get("arr_name") or item.get("arrival_name"),
        "airline_iata": item.get("airline_iata"),
        "airline_icao": item.get("airline_icao"),
        "flight_iata": item.get("flight_iata"),
        "flight_icao": item.get("flight_icao"),
        "source": "airlabs",
        "cached": False,
        "looked_up_utc": int(_time.time()),
        "raw": item,
        f"{requested_kind}_requested": requested_flight,
    }


def _airlabs_route2_lookup(kind: str, flight: str) -> dict:
    import json as _json
    import time as _time
    import urllib.parse as _urlparse
    import urllib.request as _urlrequest

    kind = "flight_iata" if kind == "flight_iata" else "flight_icao"
    flight = str(flight or "").strip().upper()

    if not flight:
        return {"found": False, kind: flight, "source": "airlabs", "message": "Missing flight identifier."}

    cache_key = _airlabs_route2_cache_key(kind, flight)
    now = int(_time.time())
    cached = _AIRLABS_ROUTE2_CACHE.get(cache_key)
    if cached and now - int(cached.get("cached_utc", 0)) < 7200:
        route = dict(cached)
        route["cached"] = True
        return route

    api_key = _airlabs_route2_get_api_key()
    if not api_key:
        return {"found": False, kind: flight, "source": "airlabs", "message": "AirLabs is not configured."}

    url = "https://airlabs.co/api/v9/flights?" + _urlparse.urlencode({"api_key": api_key, kind: flight})

    try:
        request = _urlrequest.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "RTL-Pi-ADS-B-Tracker/airlabs-route2",
            },
        )
        with _urlrequest.urlopen(request, timeout=8) as response:
            payload = _json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"found": False, kind: flight, "source": "airlabs", "message": f"AirLabs lookup failed: {exc}"}

    response_items = payload.get("response")
    if isinstance(response_items, dict):
        response_items = [response_items]
    if not isinstance(response_items, list):
        response_items = []

    for item in response_items:
        if not isinstance(item, dict):
            continue
        returned = str(item.get(kind) or "").strip().upper()
        if returned and returned != flight:
            continue
        route = _airlabs_route2_extract_route(item, kind, flight)
        if route.get("found"):
            route["cached_utc"] = now
            _AIRLABS_ROUTE2_CACHE[cache_key] = dict(route)
            return route

    return {
        "found": False,
        kind: flight,
        "source": "airlabs",
        "cached": False,
        "looked_up_utc": now,
        "message": "AirLabs returned no matching flights.",
    }


def _airlabs_route2_response(query_string: str) -> tuple[dict, HTTPStatus]:
    import urllib.parse as _urlparse

    params = _urlparse.parse_qs(query_string, keep_blank_values=True)
    flight_iata = str((params.get("flight_iata") or [""])[0]).strip().upper()
    flight_icao = str((params.get("flight_icao") or params.get("callsign") or [""])[0]).strip().upper()

    if flight_iata:
        route = _airlabs_route2_lookup("flight_iata", flight_iata)
        return {"flight_iata": flight_iata, "route": route, "cached": bool(route.get("cached"))}, HTTPStatus.OK

    if flight_icao:
        route = _airlabs_route2_lookup("flight_icao", flight_icao)
        return {"flight_icao": flight_icao, "route": route, "cached": bool(route.get("cached"))}, HTTPStatus.OK

    return {"error": "Missing flight_icao, callsign, or flight_iata parameter."}, HTTPStatus.BAD_REQUEST

# /AIRLABS_ROUTE2_IATA_VARIANTS_BACKEND_PATCH_V1

if __name__ == "__main__":
    main()

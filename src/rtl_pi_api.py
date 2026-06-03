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

if __name__ == "__main__":
    main()

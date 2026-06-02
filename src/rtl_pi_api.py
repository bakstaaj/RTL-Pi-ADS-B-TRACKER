#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import threading
import time
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
SURVEY_SECONDS = int(os.environ.get("RTL_PI_NOAA_SURVEY_SECONDS", "2"))
AUDIO_RATE_HZ = 24000

selected_noaa_frequency_hz = NOAA_FREQ_HZ
selected_noaa_station = NOAA_STATION
CAPTURE_WAV_PATH = OUTPUT_DIR / "api_last_noaa_capture.wav"
AIRBAND_CAPTURE_WAV_PATH = OUTPUT_DIR / "api_last_airband_capture.wav"
LIVE_WAV_PATH = OUTPUT_DIR / "live_noaa_source.wav"
LIVE_LOG_PATH = OUTPUT_DIR / "live_noaa_receiver.log"

receiver_lock = threading.Lock()
state_lock = threading.RLock()
live_process: subprocess.Popen[str] | None = None
live_log_handle = None
live_holds_receiver_lock = False
runtime_state: dict[str, object] = {
    "last_capture_time": None,
    "last_capture_seconds": None,
    "last_capture_error": None,
    "live_start_time": None,
    "live_stop_time": None,
    "live_error": None,
    "last_noaa_survey": None,
    "last_noaa_survey_time": None,
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


def nearby_airband_channels(location: dict) -> dict:
    raw_channels, metadata = load_airband_dataset()
    radius_miles = float(location["airband_radius_miles"])
    selected: list[dict] = []

    for record in raw_channels:
        channel = normalize_airband_channel(record, location)
        if channel is not None and channel["distance_miles"] <= radius_miles:
            selected.append(channel)

    selected.sort(key=lambda item: (item["distance_miles"], item["frequency_hz"], str(item.get("airport_id") or "")))

    return {
        "data_available": AIRBAND_DATA_PATH.exists(),
        "data_path": str(AIRBAND_DATA_PATH),
        "data_metadata": metadata,
        "receiver_location": location,
        "radius_miles": radius_miles,
        "channel_count": len(selected),
        "channels": selected,
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

    def auto_select_and_start_noaa(self) -> None:
        global selected_noaa_frequency_hz, selected_noaa_station

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
                timeout=SURVEY_SECONDS * 7 + 20,
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
            if not isinstance(best_frequency, int):
                self.send_json(
                    {"error": "NOAA survey did not report a valid best frequency."},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            selected_noaa_frequency_hz = best_frequency
            selected_noaa_station = f"AUTO SELECT — {best_frequency / 1000000.0:.3f} MHz"
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

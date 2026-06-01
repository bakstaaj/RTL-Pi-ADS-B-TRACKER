#!/usr/bin/env python3
from __future__ import annotations

import json
import os
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

READSB_JSON_DIR = Path(
    os.environ.get("RTL_PI_READSB_JSON_DIR", "/run/rtl-pi-readsb")
)
AIRCRAFT_JSON = READSB_JSON_DIR / "aircraft.json"
READSB_STATUS_JSON = READSB_JSON_DIR / "status.json"

AUDIO_BINARY = Path(
    os.environ.get(
        "RTL_PI_AUDIO_BINARY",
        "/opt/rtl-pi-adsb-tracker/bin/rtl_noaa_receiver",
    )
)
AUDIO_SERIAL = os.environ.get("RTL_PI_AUDIO_SERIAL", "00000162")
NOAA_STATION = os.environ.get("RTL_PI_NOAA_STATION", "KGG68_HOUSTON")
NOAA_FREQ_HZ = int(os.environ.get("RTL_PI_NOAA_FREQ_HZ", "162400000"))
RF_GAIN_DB = os.environ.get("RTL_PI_RF_GAIN_DB", "40.2")
AUDIO_OUTPUT_GAIN = os.environ.get("RTL_PI_AUDIO_OUTPUT_GAIN", "15000")

capture_lock = threading.Lock()
state_lock = threading.Lock()

runtime_state: dict[str, object] = {
    "last_capture_time": None,
    "last_capture_seconds": None,
    "last_capture_error": None,
}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def build_status() -> dict:
    aircraft_data = read_json(AIRCRAFT_JSON)
    aircraft = aircraft_data.get("aircraft", [])

    if not isinstance(aircraft, list):
        aircraft = []

    positioned = sum(
        1
        for aircraft_record in aircraft
        if isinstance(aircraft_record, dict)
        and aircraft_record.get("lat") is not None
        and aircraft_record.get("lon") is not None
    )

    with state_lock:
        state = dict(runtime_state)

    return {
        "service": "rtl-pi-api",
        "readsb_json_available": AIRCRAFT_JSON.exists(),
        "messages": aircraft_data.get("messages", 0),
        "aircraft_count": len(aircraft),
        "aircraft_with_position": positioned,
        "audio_busy": capture_lock.locked(),
        "audio_receiver_serial": AUDIO_SERIAL,
        "noaa_station": NOAA_STATION,
        "noaa_frequency_hz": NOAA_FREQ_HZ,
        "rf_gain_db": RF_GAIN_DB,
        "audio_output_gain": AUDIO_OUTPUT_GAIN,
        **state,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "RTL-Pi-API/0.1"

    def log_message(self, format_string: str, *args: object) -> None:
        print(
            f"{self.client_address[0]} "
            f"[{self.log_date_time_string()}] "
            f"{format_string % args}",
            flush=True,
        )

    def send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(
        self,
        payload: object,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_existing_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_json(
                {"error": f"File not found: {path}"},
                HTTPStatus.NOT_FOUND,
            )
            return

        self.send_bytes(body, content_type)

    def capture_noaa_audio(self, seconds: int) -> None:
        if not capture_lock.acquire(blocking=False):
            self.send_json(
                {"error": "NOAA audio capture is already running."},
                HTTPStatus.CONFLICT,
            )
            return

        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / "api_last_noaa_capture.wav"

            try:
                output_path.unlink()
            except FileNotFoundError:
                pass

            command = [
                str(AUDIO_BINARY),
                "--serial",
                AUDIO_SERIAL,
                "--freq-hz",
                str(NOAA_FREQ_HZ),
                "--seconds",
                str(seconds),
                "--gain-db",
                RF_GAIN_DB,
                "--audio-gain",
                AUDIO_OUTPUT_GAIN,
                "--wav-output",
                str(output_path),
            ]

            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=seconds + 20,
                check=False,
            )

            if result.returncode != 0 or not output_path.exists():
                error_text = (
                    result.stderr.strip()
                    or result.stdout.strip()
                    or "Native NOAA receiver failed."
                )

                with state_lock:
                    runtime_state["last_capture_error"] = error_text

                self.send_json(
                    {
                        "error": "NOAA audio capture failed.",
                        "details": error_text,
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            with state_lock:
                runtime_state["last_capture_time"] = int(time.time())
                runtime_state["last_capture_seconds"] = seconds
                runtime_state["last_capture_error"] = None

            self.send_existing_file(output_path, "audio/wav")

        except subprocess.TimeoutExpired:
            with state_lock:
                runtime_state["last_capture_error"] = "Capture timed out."

            self.send_json(
                {"error": "NOAA capture timed out."},
                HTTPStatus.GATEWAY_TIMEOUT,
            )
        finally:
            capture_lock.release()

    def do_GET(self) -> None:
        request = urlparse(self.path)

        if request.path in ("/", "/index.html"):
            self.send_existing_file(
                WEB_ROOT / "index.html",
                "text/html; charset=utf-8",
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

        if request.path == "/api/noaa/capture.wav":
            parameters = parse_qs(request.query)

            try:
                seconds = int(parameters.get("seconds", ["10"])[0])
            except ValueError:
                seconds = 10

            seconds = min(max(seconds, 2), 60)
            self.capture_noaa_audio(seconds)
            return

        self.send_json(
            {
                "error": "Endpoint not found.",
                "available_endpoints": [
                    "/api/status",
                    "/api/aircraft.json",
                    "/api/readsb/status.json",
                    "/api/noaa/capture.wav?seconds=10",
                ],
            },
            HTTPStatus.NOT_FOUND,
        )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((BIND_ADDRESS, PORT), Handler)

    print(f"RTL Pi API listening on http://{BIND_ADDRESS}:{PORT}", flush=True)
    print(f"Aircraft JSON source: {AIRCRAFT_JSON}", flush=True)
    print(
        f"NOAA source: {NOAA_STATION} at {NOAA_FREQ_HZ} Hz "
        f"using receiver {AUDIO_SERIAL}",
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

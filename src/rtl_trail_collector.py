#!/usr/bin/env python3
import json
import os
import signal
import tempfile
import time
from pathlib import Path

AIRCRAFT_JSON_PATH = Path(os.environ.get(
    "RTL_PI_READSB_AIRCRAFT_JSON",
    "/run/readsb/aircraft.json",
))
TRAIL_HISTORY_PATH = Path(os.environ.get(
    "RTL_PI_TRAIL_HISTORY_PATH",
    "/opt/rtl-pi-adsb-tracker/settings/aircraft_trails_history.json",
))
TRAIL_CONTROL_PATH = Path(os.environ.get(
    "RTL_PI_TRAIL_CONTROL_PATH",
    "/opt/rtl-pi-adsb-tracker/settings/aircraft_trails_control.json",
))
SAMPLE_SECONDS = float(os.environ.get("RTL_PI_TRAIL_SAMPLE_SECONDS", "2"))
RETENTION_MINUTES = int(os.environ.get("RTL_PI_TRAIL_RETENTION_MINUTES", "240"))
MAX_POINTS_PER_AIRCRAFT = int(os.environ.get("RTL_PI_TRAIL_MAX_POINTS_PER_AIRCRAFT", "7200"))

running = True

def handle_signal(_number, _frame):
    global running
    running = False

def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}

def altitude_feet(aircraft: dict):
    value = aircraft.get("alt_baro", aircraft.get("alt_geom"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def clear_watermark_ms() -> int:
    data = read_json(TRAIL_CONTROL_PATH)
    try:
        return int(data.get("cleared_utc_ms", 0))
    except (TypeError, ValueError):
        return 0

def load_history() -> dict:
    data = read_json(TRAIL_HISTORY_PATH)
    trails = data.get("trails", {})
    return trails if isinstance(trails, dict) else {}

def write_history(trails: dict) -> None:
    TRAIL_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_utc": int(time.time()),
        "retention_minutes": RETENTION_MINUTES,
        "source": "readsb_pi_background_collector",
        "cleared_utc_ms": clear_watermark_ms(),
        "trails": trails,
    }
    temporary = TRAIL_HISTORY_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(TRAIL_HISTORY_PATH)

def prune_history(trails: dict, now_ms: int) -> None:
    cutoff_ms = now_ms - RETENTION_MINUTES * 60 * 1000 if RETENTION_MINUTES > 0 else 0
    remove = []
    for key, points in trails.items():
        if not isinstance(points, list):
            remove.append(key)
            continue
        retained = [
            point for point in points
            if isinstance(point, dict) and (not cutoff_ms or int(point.get("time", 0)) >= cutoff_ms)
        ][-MAX_POINTS_PER_AIRCRAFT:]
        if retained:
            trails[key] = retained
        else:
            remove.append(key)
    for key in remove:
        trails.pop(key, None)

def collect_once(trails: dict, applied_clear_ms: int) -> int:
    current_clear_ms = clear_watermark_ms()
    if current_clear_ms > applied_clear_ms:
        trails.clear()
        applied_clear_ms = current_clear_ms

    data = read_json(AIRCRAFT_JSON_PATH)
    aircraft_list = data.get("aircraft", [])
    if not isinstance(aircraft_list, list):
        return applied_clear_ms

    now_ms = int(time.time() * 1000)
    for aircraft in aircraft_list:
        if not isinstance(aircraft, dict):
            continue
        key = str(aircraft.get("hex") or "").strip().lower()
        if not key:
            continue
        try:
            latitude = float(aircraft["lat"])
            longitude = float(aircraft["lon"])
        except (KeyError, TypeError, ValueError):
            continue

        point = {
            "lat": latitude,
            "lon": longitude,
            "altitude": altitude_feet(aircraft),
            "time": now_ms,
            "flight": str(aircraft.get("flight") or "").strip(),
            "track": aircraft.get("track"),
        }
        points = trails.setdefault(key, [])
        previous = points[-1] if points else None
        if previous and previous.get("lat") == latitude and previous.get("lon") == longitude:
            continue
        points.append(point)

    prune_history(trails, now_ms)
    write_history(trails)
    return applied_clear_ms

def main() -> int:
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    trails = load_history()
    applied_clear_ms = clear_watermark_ms()

    print("RTL Pi ADS-B background trail collector")
    print(f"  readsb source:       {AIRCRAFT_JSON_PATH}")
    print(f"  trail output:        {TRAIL_HISTORY_PATH}")
    print(f"  sample interval:     {SAMPLE_SECONDS:.1f} seconds")
    print(f"  retention:           {RETENTION_MINUTES} minutes")
    print(f"  max points/aircraft: {MAX_POINTS_PER_AIRCRAFT}", flush=True)

    while running:
        applied_clear_ms = collect_once(trails, applied_clear_ms)
        deadline = time.monotonic() + SAMPLE_SECONDS
        while running and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

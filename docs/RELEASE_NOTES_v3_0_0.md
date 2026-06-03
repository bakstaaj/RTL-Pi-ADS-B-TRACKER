# RTL Pi ADS-B Tracker v3.0.0

## Major Release Summary

This release establishes the Raspberry Pi RTL-SDR ADS-B tracker application with a map-first web interface and a dual-receiver workflow that keeps ADS-B tracking active while using a second RTL-SDR for NOAA Weather and Airband monitoring.

## Included Capabilities

- Local ADS-B tracking from readsb with active-aircraft list and interactive map.
- Aircraft trails with altitude-based colors, Pi-restored history, callsign/ICAO fallback, last-seen timestamps, and route-aware hover information.
- Aircraft detail lookup with ADSBDB enrichment and AirLabs origin/destination support.
- AirLabs API-key configuration within the application and successful-route caching.
- NOAA NFM listening with automatic strongest-channel selection and saved local NOAA channel.
- Background Airband AM scanning, live currently tuned frequency display, and configurable scan radius.
- Receiver-location configuration including map-based location selection and range rings.
- Diagnostics for NOAA audio, Airband scanning, AirLabs lookup and route-cache validation.

## Hardware Baseline

- Raspberry Pi 5
- RTL-SDR ADS-B receiver serial: `00001090`
- RTL-SDR NOAA/Airband receiver serial: `00000162`

## Security Notes

API keys and runtime receiver settings are stored only on the deployed Pi and are not included in this repository release.

# RTL Pi ADS-B Tracker — Verified Hardware Baseline

## Platform

- Raspberry Pi 5
- Debian Trixie, ARM64
- USB gadget Ethernet enabled for SSH/SCP development access

## Receivers

Two NooElec NESDR Nano 3 RTL-SDR receivers are connected and assigned permanent serial numbers:

| Role | Serial Number | Purpose |
|---|---:|---|
| ADS-B receiver | `00001090` | Dedicated 1090 MHz aircraft decoding |
| Audio receiver | `00000162` | NOAA NFM and civil-airband AM reception |

## ADS-B Baseline

ADS-B is provided by an RTL-SDR-enabled `readsb` build installed as:

```text
/usr/bin/readsb

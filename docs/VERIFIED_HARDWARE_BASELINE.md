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

## Houston Native NOAA Receiver Baseline

Development testing in the Houston area uses:

| Item | Value |
|---|---|
| NOAA station | KGG68 Houston |
| Frequency | 162.400 MHz NFM |
| Audio receiver serial | `00000162` |
| ADS-B receiver serial | `00001090` |

The native Docker-cross-compiled NOAA receiver was verified while the `rtl-pi-readsb.service` ADS-B service remained active.

Native receiver operating baseline:

```text
Station frequency:   162400000 Hz
Tuner frequency:     162652000 Hz
Tuning offset:       252000 Hz
Input sample rate:   1008000 Hz
Audio sample rate:   24000 Hz
RF gain:             40.2 dB
Audio output gain:   15000

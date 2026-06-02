# Audio Receiver Modes and Future Scan Use Cases

## Hardware Architecture

The Raspberry Pi application uses two dedicated RTL-SDR receivers:

| Receiver Role | Serial Number | Responsibility |
|---|---:|---|
| ADS-B receiver | `00001090` | Continuous 1090 MHz ADS-B decoding through readsb |
| Audio receiver | `00000162` | NOAA NFM and civil-airband AM listening/scanning |

ADS-B decoding must continue while any audio mode is active.

Only one audio-listening or audio-scanning mode can use receiver `00000162` at one time.

## Audio Modes

### Direct NOAA Listen

Purpose: manually test or listen to a selected NOAA Weather Radio frequency.

Current verified test baseline:

| Item | Value |
|---|---|
| Station | KGG68 Houston |
| Frequency | 162.400 MHz NFM |
| Receiver serial | `00000162` |

### NOAA Auto Select

When the user selects Listen to NOAA Weather, the application shall be capable of scanning the seven NOAA Weather Radio channels:

| Frequency MHz |
|---:|
| 162.400 |
| 162.425 |
| 162.450 |
| 162.475 |
| 162.500 |
| 162.525 |
| 162.550 |

Required behavior:

1. Scan each NOAA channel using the audio receiver in NFM mode.
2. Measure channel quality/SNR for each candidate.
3. Select the strongest usable channel.
4. Tune to the selected channel and begin NOAA listening.
5. Display the selected frequency, station identity when known, and measured quality.
6. Provide a Rescan NOAA Channels control.
7. Allow the user to stop listening.

A receiver location is not required for NOAA Auto Select because all NOAA channels can be directly surveyed.

### Airband Scan

When the user selects Airband Scan, the application shall scan civil-airband AM frequencies associated with airports and facilities within a configured radius of the receiver installation.

Default radius:

| Setting | Default |
|---|---:|
| Airband scan radius | 100 miles |

Required behavior:

1. Verify that the receiver location is configured.
2. If no receiver location exists, do not start scanning and prompt the user to set it.
3. Load or filter airband frequencies within 100 miles of the configured location.
4. Scan eligible civil-airband AM channels for detected audio activity.
5. When activity is detected, tune and play the detected audio.
6. Remain on the active channel while transmissions continue.
7. After 7 seconds with no detected audio, resume scanning.
8. Display the tuned frequency, airport/facility name, channel use, and scanning/listening state.
9. Provide Stop, Hold Current Channel, and Skip Current Channel controls.

## Receiver Location Requirement

The receiver location represents the physical location of the Raspberry Pi and attached antennas.

Required configurable values:

| Setting | Requirement |
|---|---|
| Location name | Human-readable label |
| Latitude | Decimal degrees |
| Longitude | Decimal degrees |
| Airband scan radius | Miles; default 100 |

If Airband Scan is selected without a configured receiver location, display:

Set the receiver location before starting Airband Scan.

The application shall provide a Settings action for entering or changing this location.

## Backend/API Design Implication

The application API should evolve from a NOAA-specific capture endpoint into an audio-mode controller supporting:

| Future Function | Purpose |
|---|---|
| Status | Report current audio mode, tuned frequency, station/channel, scan state, and receiver location state |
| NOAA direct listen | Listen on manually chosen NOAA frequency |
| NOAA scan/select | Survey NOAA frequencies and lock on strongest usable signal |
| Airband scan start | Begin radius-filtered AM scanning |
| Stop | Stop audio/scanning and release audio receiver |
| Hold | Remain tuned to current airband channel |
| Skip | Leave current channel and continue scanning |
| Location settings | Set or update receiver installation location |

## Implementation Sequence

1. Complete the initial browser page using the verified completed-WAV NOAA endpoint.
2. Convert the NOAA receiver into a long-running audio backend suitable for browser listening.
3. Add a general audio-mode controller and status API.
4. Add NOAA channel survey/SNR measurement and auto-select.
5. Add receiver-location settings.
6. Add FAA-derived airband frequency data filtered by configured radius.
7. Add AM activity detection, listen-until-silent logic, and 7-second scan resume behavior.

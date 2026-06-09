#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "${REPO_ROOT}/data"

CANDIDATES=(
    "${HOME}/sdrdev/Pluto-ADS-B-Tracker/data/airband_frequencies_full.json"
    "${HOME}/sdrdev/Pluto_ADS_B_Tracker/data/airband_frequencies_full.json"
    "${HOME}/sdrdev/pluto_ads_b_tracker/data/airband_frequencies_full.json"
)

SOURCE_FILE=""
for candidate in "${CANDIDATES[@]}"; do
    if [[ -f "${candidate}" ]]; then
        SOURCE_FILE="${candidate}"
        break
    fi
done

if [[ -z "${SOURCE_FILE}" ]]; then
    echo "No existing Pluto full FAA-derived airband dataset was found."
    echo "Checked:"
    printf '  %s\n' "${CANDIDATES[@]}"
    echo
    echo "The next fallback is to generate a new dataset from the official FAA NASR APT and FRQ CSV groups."
    exit 2
fi

cp "${SOURCE_FILE}" "${REPO_ROOT}/data/airband_frequencies_full.json"

echo "Imported FAA-derived airband dataset:"
echo "  From: ${SOURCE_FILE}"
echo "  To:   ${REPO_ROOT}/data/airband_frequencies_full.json"

python3 - "${REPO_ROOT}/data/airband_frequencies_full.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as stream:
    data = json.load(stream)
channels = data.get("channels", [])
print("  Records:", len(channels))
print("  Metadata:", data.get("metadata", {}))
print("  Sample:")
for channel in channels[:3]:
    print("   ", channel)
PY

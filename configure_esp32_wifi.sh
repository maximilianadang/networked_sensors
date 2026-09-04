#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROFILE=${1:-Mars_2p4Ghz}
SSID=${2:-$PROFILE}
OUTPUT="$SCRIPT_DIR/wifi_credentials.h"

if ! command -v nmcli >/dev/null 2>&1; then
    echo "nmcli is required to import a saved Wi-Fi credential." >&2
    exit 1
fi

PASSWORD=$(nmcli --show-secrets \
    -g 802-11-wireless-security.psk \
    connection show "$PROFILE")

if [[ -z "$SSID" || -z "$PASSWORD" ]]; then
    echo "The saved profile must have a non-empty SSID and WPA password." >&2
    exit 1
fi
if [[ "$SSID" == *$'\n'* || "$PASSWORD" == *$'\n'* ]]; then
    echo "Newlines are not supported in ESP32 Wi-Fi credentials." >&2
    exit 1
fi

escape_cpp_string() {
    local value=$1
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    printf '%s' "$value"
}

TEMP_FILE=$(mktemp "$SCRIPT_DIR/.wifi_credentials.h.XXXXXX")
trap 'rm -f "$TEMP_FILE"; unset PASSWORD' EXIT
chmod 600 "$TEMP_FILE"

{
    printf '#pragma once\n\n'
    printf '// Generated locally by configure_esp32_wifi.sh; never commit this file.\n'
    printf 'constexpr char WIFI_SSID[] = "%s";\n' "$(escape_cpp_string "$SSID")"
    printf 'constexpr char WIFI_PASS[] = "%s";\n' "$(escape_cpp_string "$PASSWORD")"
} >"$TEMP_FILE"

mv "$TEMP_FILE" "$OUTPUT"
unset PASSWORD
trap - EXIT

echo "Wrote $OUTPUT from saved NetworkManager profile '$PROFILE'."

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

CONTROLLINO_URL=${CONTROLLINO_URL:-http://10.77.0.10}
DXMR90_SOURCE=${DXMR90_SOURCE:-real}
DXMR90_HOST=${DXMR90_HOST:-192.168.0.1}
ESP32_SOURCE=${ESP32_SOURCE:-real}
ESP32_URL=${ESP32_URL:-http://testbench.local}
DASHBOARD_HOST=${DASHBOARD_HOST:-0.0.0.0}
DASHBOARD_PORT=${DASHBOARD_PORT:-8000}

exec python3 "$SCRIPT_DIR/dashboard-lean.py" \
    --esp32-source "$ESP32_SOURCE" \
    --esp32-url "$ESP32_URL" \
    --dxmr90-source "$DXMR90_SOURCE" \
    --dxmr90-host "$DXMR90_HOST" \
    --dxmr90-data-path direct \
    --dxmr90-rate-hz 10 \
    --stepper-source controllino \
    --stepper-url "$CONTROLLINO_URL" \
    --stepper-timeout 0.75 \
    --host "$DASHBOARD_HOST" \
    --port "$DASHBOARD_PORT" \
    "$@"

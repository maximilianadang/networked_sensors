#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./provision_yun.sh CURRENT_YUN_HOST [TARGET_WIFI_SSID]

One-time provisioning for cold-start operation:
  1. installs a dedicated laptop SSH key (one root-password prompt at most),
  2. deploys the Yún bridge service and init wrapper,
  3. verifies health/status, and enables the service at boot,
  4. optionally stores a target Wi-Fi configuration for the next power cycle.

Examples:
  ./provision_yun.sh 192.168.20.133
  ./provision_yun.sh 192.168.20.133 GL-MT3000-b3a

Environment:
  YUN_SSH_KEY  dedicated key path (default: ~/.ssh/yun_stepper)
EOF
}

if [[ $# -lt 1 || $# -gt 2 || ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    [[ ${1:-} == "-h" || ${1:-} == "--help" ]] && exit 0
    exit 2
fi

YUN_HOST=$1
TARGET_WIFI_SSID=${2:-}
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
KEY_FILE=${YUN_SSH_KEY:-"$HOME/.ssh/yun_stepper"}

for command in ssh scp ssh-keygen curl; do
    command -v "$command" >/dev/null || {
        echo "Required command not found: $command" >&2
        exit 1
    }
done

SSH_COMPAT=(
    -o HostKeyAlgorithms=+ssh-rsa
    -o PubkeyAcceptedAlgorithms=+ssh-rsa
    -o StrictHostKeyChecking=accept-new
)

if [[ ! -f "$KEY_FILE" || ! -f "$KEY_FILE.pub" ]]; then
    mkdir -p "$(dirname -- "$KEY_FILE")"
    ssh-keygen -q -t rsa -b 3072 -N '' -f "$KEY_FILE"
    echo "Created dedicated Yún SSH key: $KEY_FILE"
fi

if ! ssh "${SSH_COMPAT[@]}" -i "$KEY_FILE" -o BatchMode=yes \
    "root@$YUN_HOST" true 2>/dev/null; then
    echo "One final Yún root-password prompt will install the dedicated SSH key."
    ssh "${SSH_COMPAT[@]}" \
        -o PubkeyAuthentication=no \
        -o PreferredAuthentications=password \
        "root@$YUN_HOST" \
        'umask 077; mkdir -p /etc/dropbear; touch /etc/dropbear/authorized_keys; chmod 600 /etc/dropbear/authorized_keys; key=$(cat); grep -qxF "$key" /etc/dropbear/authorized_keys || printf "%s\n" "$key" >> /etc/dropbear/authorized_keys' \
        < "$KEY_FILE.pub"
fi

SSH_KEY=("${SSH_COMPAT[@]}" -i "$KEY_FILE")
if ! ssh "${SSH_KEY[@]}" -o BatchMode=yes "root@$YUN_HOST" true; then
    echo "Dedicated Yún SSH key authentication failed after installation." >&2
    exit 1
fi

ssh "${SSH_KEY[@]}" "root@$YUN_HOST" /etc/init.d/yun-stepper-bridge stop \
    >/dev/null 2>&1 || true
scp -q -O "${SSH_KEY[@]}" "$SCRIPT_DIR/yun_stepper_bridge.py" \
    "root@$YUN_HOST:/root/yun_stepper_bridge.py"
scp -q -O "${SSH_KEY[@]}" "$SCRIPT_DIR/yun-stepper-bridge.init" \
    "root@$YUN_HOST:/etc/init.d/yun-stepper-bridge"

ssh "${SSH_KEY[@]}" "root@$YUN_HOST" \
    'chmod 700 /root/yun_stepper_bridge.py /etc/init.d/yun-stepper-bridge; /usr/bin/python -m py_compile /root/yun_stepper_bridge.py; /etc/init.d/yun-stepper-bridge start'

BRIDGE_HEALTH=
BRIDGE_STATUS=
for attempt in 1 2 3 4 5; do
    if BRIDGE_HEALTH=$(curl --fail --silent --show-error --max-time 2 \
        "http://$YUN_HOST:8080/v1/health") && \
       BRIDGE_STATUS=$(curl --fail --silent --show-error --max-time 2 \
        "http://$YUN_HOST:8080/v1/status"); then
        break
    fi
    if [[ $attempt -eq 5 ]]; then
        echo "Yún bridge health did not become ready." >&2
        exit 1
    fi
    sleep 1
done

printf '%s\n%s\n' "$BRIDGE_HEALTH" "$BRIDGE_STATUS"

ssh "${SSH_KEY[@]}" "root@$YUN_HOST" \
    '/etc/init.d/yun-stepper-bridge enable'
echo "Yún bridge enabled for cold-start operation."

if [[ -n "$TARGET_WIFI_SSID" ]]; then
    if [[ $TARGET_WIFI_SSID == *"'"* ]]; then
        echo "Target SSID may not contain a single quote." >&2
        exit 2
    fi
    printf "Wi-Fi password for %s (visible, entered once): " "$TARGET_WIFI_SSID"
    IFS= read -r TARGET_WIFI_PASSWORD
    if [[ -z "$TARGET_WIFI_PASSWORD" || $TARGET_WIFI_PASSWORD == *"'"* ]]; then
        echo "Password must be non-empty and may not contain a single quote." >&2
        exit 2
    fi

    {
        echo "cp /etc/config/wireless /root/wireless-before-provision"
        echo "uci set wireless.@wifi-iface[0].mode='sta'"
        echo "uci set wireless.@wifi-iface[0].network='lan'"
        printf "uci set wireless.@wifi-iface[0].ssid='%s'\n" "$TARGET_WIFI_SSID"
        echo "uci set wireless.@wifi-iface[0].encryption='psk2'"
        printf "uci set wireless.@wifi-iface[0].key='%s'\n" "$TARGET_WIFI_PASSWORD"
        echo "uci commit wireless"
    } | ssh "${SSH_KEY[@]}" "root@$YUN_HOST" /bin/ash
    unset TARGET_WIFI_PASSWORD

    echo "Stored target Wi-Fi: $TARGET_WIFI_SSID"
    echo "Power-cycle the Yún; it will join that network and start the bridge automatically."
fi

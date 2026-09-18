#!/usr/bin/env python3
"""Flow dashboard entry point; minimal Controllino/Yun distribution.

Run ./dashboard-lean.py for Controllino Ethernet. For Yun LAN, add
--stepper-source network --stepper-url http://arduino.local:8080.
For Yun USB, use --stepper-source usb --stepper-port /dev/ttyACM0.
Upload via tools/firmware_upload.ipynb; provision Yun LAN with
devices/yun/provision_yun.sh. Full tests and guides live on dev-organized.

This entry point owns CLI validation
and server lifecycle. Browser assets use the shared dashboard_app API and
runtime.
"""

import argparse
import math
import os
import sys
from pathlib import Path

import supervisor_core as core
from dashboard_app import DashboardRuntime, DashboardServer, build_handler
from dashboard_app.runtime import DEFAULT_HISTORY_LIMIT
from dashboard_app.system_config import DEFAULT_SYSTEM_CONFIG_PATH
from recorder import DEFAULT_RECORD_DIR

# CLI name, default, choices. Types come from defaults; names normally map
# directly to runtime keyword arguments. Keep exceptions in RUNTIME_NAMES.
OPTIONS = (
    ("host", "0.0.0.0", None),
    ("port", 8000, None),
    ("rate-hz", 10.0, None),
    ("scenario", "healthy", core.SIMULATION_SCENARIOS),
    ("esp32-source", "real", core.SOURCE_MODES),
    ("esp32-url", core.DEFAULT_ESP32_BASE_URL, None),
    ("esp32-timeout", core.DEFAULT_ESP32_TIMEOUT_S, None),
    ("dxmr90-source", "real", core.SOURCE_MODES),
    ("stepper-source", "controllino", core.STEPPER_SOURCE_MODES),
    ("stepper-port", core.DEFAULT_STEPPER_USB_PORT, None),
    ("stepper-baud", core.DEFAULT_STEPPER_USB_BAUD, None),
    ("stepper-url", "http://10.77.0.10", None),
    ("stepper-timeout", core.DEFAULT_STEPPER_NETWORK_TIMEOUT_S, None),
    ("dxmr90-host", core.DEFAULT_DXMR90_HOST, None),
    ("dxmr90-port", core.DEFAULT_DXMR90_PORT, None),
    ("dxmr90-unit-id", core.DEFAULT_DXMR90_UNIT_ID, None),
    ("dxmr90-timeout", 1.0, None),
    ("dxmr90-addressing", "one-based", ("one-based", "zero-based")),
    ("dxmr90-word-order", "high-low", ("high-low", "low-high")),
    ("dxmr90-data-path", "direct", core.DXMR90_DATA_PATHS),
    ("dxmr90-rate-hz", core.DEFAULT_DXMR90_REAL_RATE_HZ, None),
    ("drop-after-s", core.DEFAULT_DROP_AFTER_S, None),
    ("stale-after-s", core.DEFAULT_STALE_AFTER_S, None),
    ("history-limit", DEFAULT_HISTORY_LIMIT, None),
    ("record-dir", Path(DEFAULT_RECORD_DIR), None),
    ("system-config", Path(DEFAULT_SYSTEM_CONFIG_PATH), None),
)
RUNTIME_NAMES = {
    "esp32_url": "esp32_base_url",
    "stepper_url": "stepper_network_url",
    "stepper_timeout": "stepper_network_timeout",
    "system_config": "system_config_path",
}
# Preserve launcher's environment overrides. CLI arguments win;
# unset or empty environment variables use the defaults in OPTIONS.
ENV_OPTIONS = {
    "host": "DASHBOARD_HOST", "port": "DASHBOARD_PORT",
    "esp32-source": "ESP32_SOURCE", "esp32-url": "ESP32_URL",
    "dxmr90-source": "DXMR90_SOURCE", "dxmr90-host": "DXMR90_HOST",
    "stepper-url": "CONTROLLINO_URL",
}


def parse_args(argv=None):
    """Preserve CLI options and reject invalid numeric settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default, choices in OPTIONS:
        parser.add_argument(
            f"--{name}", default=default, choices=choices,
            type=Path if isinstance(default, Path) else type(default),
            help=f"{name.replace('-', ' ')} (default: {default})",
        )
    parser.add_argument("--verbose-http", action="store_true", help="log HTTP requests")
    env_args = [
        f"--{name}={os.environ[env]}"
        for name, env in ENV_OPTIONS.items() if os.environ.get(env)
    ]
    args = parser.parse_args(env_args + list(sys.argv[1:] if argv is None else argv))
    for name, value in vars(args).items():
        if isinstance(value, float) and not math.isfinite(value):
            parser.error(f"--{name.replace('_', '-')} must be finite")
        if name in ("port", "dxmr90_port") and not 1 <= value <= 65535:
            parser.error(f"--{name.replace('_', '-')} must be between 1 and 65535")
        if name in ("drop_after_s", "stale_after_s") and value < 0:
            parser.error(f"--{name.replace('_', '-')} must be non-negative")
        if (name.endswith(("timeout", "rate_hz")) or name == "history_limit") and value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    return args


def main(argv=None):
    """Bind before starting workers; always close workers and the HTTP socket."""
    settings = vars(parse_args(argv))
    host, port = settings.pop("host"), settings.pop("port")
    quiet = not settings.pop("verbose_http")
    runtime = None
    try:
        runtime = DashboardRuntime(**{RUNTIME_NAMES.get(k, k): v for k, v in settings.items()})
        with DashboardServer((host, port), build_handler(runtime, quiet=quiet)) as server:
            runtime.start()
            print(f"Serving flow-management dashboard at http://{host}:{port}/", file=sys.stderr)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (NotImplementedError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if runtime is not None:
            runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

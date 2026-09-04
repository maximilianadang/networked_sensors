#!/usr/bin/env python3
"""Flow dashboard entry point; panel editing guide: DASHBOARD-LEAN.md.

The original dashboard.py is preserved. This entry point owns CLI validation
and server lifecycle. Lean browser assets use the shared dashboard_app API and
runtime, keeping recording and device support consistent with the original.
"""

import argparse
import math
import runpy
import sys
from pathlib import Path

import supervisor_core as core
from dashboard_app import DashboardRuntime, DashboardServer
from dashboard_app.config import DEFAULT_HISTORY_LIMIT, DEFAULT_SYSTEM_CONFIG_PATH
from recorder import DEFAULT_RECORD_DIR

# Hyphens are intentional for lean variants; load this local adapter explicitly.
build_handler = runpy.run_path(
    str(Path(__file__).with_name("dashboard_app") / "http-lean.py")
)["build_handler"]


# CLI name, default, choices. Types come from defaults; names normally map
# directly to runtime keyword arguments. Keep exceptions in RUNTIME_NAMES.
OPTIONS = (
    ("host", "127.0.0.1", None),
    ("port", 8000, None),
    ("rate-hz", 10.0, None),
    ("scenario", "healthy", core.SIMULATION_SCENARIOS),
    ("esp32-source", "sim", core.SOURCE_MODES),
    ("esp32-url", core.DEFAULT_ESP32_BASE_URL, None),
    ("esp32-timeout", core.DEFAULT_ESP32_TIMEOUT_S, None),
    ("dxmr90-source", "sim", core.SOURCE_MODES),
    ("stepper-source", "sim", core.STEPPER_SOURCE_MODES),
    ("stepper-port", core.DEFAULT_STEPPER_USB_PORT, None),
    ("stepper-baud", core.DEFAULT_STEPPER_USB_BAUD, None),
    ("stepper-url", core.DEFAULT_STEPPER_NETWORK_URL, None),
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


def parse_args(argv=None):
    """Preserve every original CLI option and reject invalid numeric settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default, choices in OPTIONS:
        parser.add_argument(
            f"--{name}", default=default, choices=choices,
            type=Path if isinstance(default, Path) else type(default),
            help=f"{name.replace('-', ' ')} (default: {default})",
        )
    parser.add_argument("--verbose-http", action="store_true", help="log HTTP requests")
    args = parser.parse_args(argv)
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

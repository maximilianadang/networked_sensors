#!/usr/bin/env python3
"""Local web dashboard and API for the flow-management supervisor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

try:
    from .dashboard_app import DashboardRuntime, DashboardServer, build_handler, parse_body
    from .dashboard_app.config import (
        DEFAULT_HISTORY_LIMIT,
        DEFAULT_SYSTEM_CONFIG_PATH,
        INDEX_HTML,
        load_dashboard_asset,
    )
    from .supervisor_core import (
        DEFAULT_DXMR90_HOST,
        DEFAULT_DXMR90_PORT,
        DEFAULT_DXMR90_REAL_RATE_HZ,
        DEFAULT_DXMR90_UNIT_ID,
        DEFAULT_DROP_AFTER_S,
        DEFAULT_ESP32_BASE_URL,
        DEFAULT_ESP32_TIMEOUT_S,
        DEFAULT_STEPPER_USB_BAUD,
        DEFAULT_STEPPER_USB_PORT,
        DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        DEFAULT_STEPPER_NETWORK_URL,
        DEFAULT_STALE_AFTER_S,
        SIMULATION_SCENARIOS,
        DXMR90_DATA_PATHS,
        SOURCE_MODES,
        STEPPER_SOURCE_MODES,
    )
    from .recorder import DEFAULT_RECORD_DIR
except ImportError:  # pragma: no cover - direct script execution fallback
    from dashboard_app import DashboardRuntime, DashboardServer, build_handler, parse_body
    from dashboard_app.config import (
        DEFAULT_HISTORY_LIMIT,
        DEFAULT_SYSTEM_CONFIG_PATH,
        INDEX_HTML,
        load_dashboard_asset,
    )
    from supervisor_core import (
        DEFAULT_DXMR90_HOST,
        DEFAULT_DXMR90_PORT,
        DEFAULT_DXMR90_REAL_RATE_HZ,
        DEFAULT_DXMR90_UNIT_ID,
        DEFAULT_DROP_AFTER_S,
        DEFAULT_ESP32_BASE_URL,
        DEFAULT_ESP32_TIMEOUT_S,
        DEFAULT_STEPPER_USB_BAUD,
        DEFAULT_STEPPER_USB_PORT,
        DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        DEFAULT_STEPPER_NETWORK_URL,
        DEFAULT_STALE_AFTER_S,
        SIMULATION_SCENARIOS,
        DXMR90_DATA_PATHS,
        SOURCE_MODES,
        STEPPER_SOURCE_MODES,
    )
    from recorder import DEFAULT_RECORD_DIR


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_RATE_HZ = 10.0




def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the selected-source flow-management dashboard and JSON API."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port")
    parser.add_argument("--rate-hz", type=float, default=DEFAULT_RATE_HZ, help="sample rate")
    parser.add_argument(
        "--scenario",
        choices=SIMULATION_SCENARIOS,
        default="healthy",
        help="simulated source scenario",
    )
    parser.add_argument(
        "--esp32-source",
        choices=SOURCE_MODES,
        default="sim",
        help="ESP32 source mode",
    )
    parser.add_argument(
        "--esp32-url",
        default=DEFAULT_ESP32_BASE_URL,
        help="base URL for the headless ESP32 API when --esp32-source real",
    )
    parser.add_argument(
        "--esp32-timeout",
        type=float,
        default=DEFAULT_ESP32_TIMEOUT_S,
        help="ESP32 SSE connection/read and command timeout in seconds",
    )
    parser.add_argument(
        "--dxmr90-source",
        choices=SOURCE_MODES,
        default="sim",
        help="SICK/DXMR90 source mode",
    )
    parser.add_argument(
        "--stepper-source",
        choices=STEPPER_SOURCE_MODES,
        default="sim",
        help="Yún stepper source mode",
    )
    parser.add_argument(
        "--stepper-port",
        default=DEFAULT_STEPPER_USB_PORT,
        help="USB Serial device used when --stepper-source usb",
    )
    parser.add_argument(
        "--stepper-baud",
        type=int,
        default=DEFAULT_STEPPER_USB_BAUD,
        help="USB Serial baud used when --stepper-source usb",
    )
    parser.add_argument(
        "--stepper-url",
        default=DEFAULT_STEPPER_NETWORK_URL,
        help="Yún Linux bridge base URL when --stepper-source network",
    )
    parser.add_argument(
        "--stepper-timeout",
        type=float,
        default=DEFAULT_STEPPER_NETWORK_TIMEOUT_S,
        help="Yún network status/command timeout in seconds",
    )
    parser.add_argument(
        "--dxmr90-host",
        default=DEFAULT_DXMR90_HOST,
        help="SICK/DXMR90 Modbus host",
    )
    parser.add_argument(
        "--dxmr90-port",
        type=int,
        default=DEFAULT_DXMR90_PORT,
        help="SICK/DXMR90 Modbus TCP port",
    )
    parser.add_argument(
        "--dxmr90-unit-id",
        type=int,
        default=DEFAULT_DXMR90_UNIT_ID,
        help="SICK/DXMR90 Modbus unit id",
    )
    parser.add_argument(
        "--dxmr90-timeout",
        type=float,
        default=1.0,
        help="SICK/DXMR90 socket timeout in seconds",
    )
    parser.add_argument(
        "--dxmr90-addressing",
        choices=("one-based", "zero-based"),
        default="one-based",
        help="SICK/DXMR90 register addressing convention",
    )
    parser.add_argument(
        "--dxmr90-word-order",
        choices=("high-low", "low-high"),
        default="high-low",
        help="SICK/DXMR90 float32 word order",
    )
    parser.add_argument(
        "--dxmr90-data-path",
        choices=DXMR90_DATA_PATHS,
        default="direct",
        help="direct SICK process data or ScriptBasic republished registers",
    )
    parser.add_argument(
        "--dxmr90-rate-hz",
        type=float,
        default=DEFAULT_DXMR90_REAL_RATE_HZ,
        help="real SICK/DXMR90 Modbus polling rate",
    )
    parser.add_argument(
        "--drop-after-s",
        type=float,
        default=DEFAULT_DROP_AFTER_S,
        help="elapsed seconds before stale scenarios stop emitting source updates",
    )
    parser.add_argument(
        "--stale-after-s",
        type=float,
        default=DEFAULT_STALE_AFTER_S,
        help="seconds after the latest source update before connected flips false",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=DEFAULT_HISTORY_LIMIT,
        help="number of samples to keep in memory for the dashboard",
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        default=DEFAULT_RECORD_DIR,
        help="directory for disk-backed run artifacts",
    )
    parser.add_argument(
        "--system-config",
        type=Path,
        default=DEFAULT_SYSTEM_CONFIG_PATH,
        help="persistent machine-level dashboard settings JSON",
    )
    parser.add_argument(
        "--verbose-http",
        action="store_true",
        help="print HTTP access logs",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.port < 1 or args.port > 65535:
        print("--port must be between 1 and 65535", file=sys.stderr)
        return 2
    if args.drop_after_s < 0:
        print("--drop-after-s must be non-negative", file=sys.stderr)
        return 2
    if args.stale_after_s < 0:
        print("--stale-after-s must be non-negative", file=sys.stderr)
        return 2
    if args.esp32_timeout <= 0:
        print("--esp32-timeout must be positive", file=sys.stderr)
        return 2
    if args.dxmr90_port < 1 or args.dxmr90_port > 65535:
        print("--dxmr90-port must be between 1 and 65535", file=sys.stderr)
        return 2
    if args.dxmr90_timeout <= 0:
        print("--dxmr90-timeout must be positive", file=sys.stderr)
        return 2
    if args.stepper_timeout <= 0:
        print("--stepper-timeout must be positive", file=sys.stderr)
        return 2
    if args.dxmr90_rate_hz <= 0:
        print("--dxmr90-rate-hz must be positive", file=sys.stderr)
        return 2

    try:
        runtime = DashboardRuntime(
            scenario=args.scenario,
            rate_hz=args.rate_hz,
            drop_after_s=args.drop_after_s,
            stale_after_s=args.stale_after_s,
            history_limit=args.history_limit,
            record_dir=args.record_dir,
            esp32_source=args.esp32_source,
            esp32_base_url=args.esp32_url,
            esp32_timeout=args.esp32_timeout,
            dxmr90_source=args.dxmr90_source,
            stepper_source=args.stepper_source,
            stepper_port=args.stepper_port,
            stepper_baud=args.stepper_baud,
            stepper_network_url=args.stepper_url,
            stepper_network_timeout=args.stepper_timeout,
            dxmr90_host=args.dxmr90_host,
            dxmr90_port=args.dxmr90_port,
            dxmr90_unit_id=args.dxmr90_unit_id,
            dxmr90_timeout=args.dxmr90_timeout,
            dxmr90_addressing=args.dxmr90_addressing,
            dxmr90_word_order=args.dxmr90_word_order,
            dxmr90_data_path=args.dxmr90_data_path,
            dxmr90_rate_hz=args.dxmr90_rate_hz,
            system_config_path=args.system_config,
        )
    except (NotImplementedError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    runtime.start()
    handler = build_handler(runtime, quiet=not args.verbose_http)
    server = DashboardServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Serving flow-management dashboard at {url}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server", file=sys.stderr)
    finally:
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Step-1 supervisor smoke CLI for merged simulated bench samples."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Sequence

try:
    from .supervisor_core import (
        DEFAULT_DROP_AFTER_S,
        DEFAULT_STALE_AFTER_S,
        SIMULATION_SCENARIOS,
        iter_merged_samples,
    )
except ImportError:  # pragma: no cover - direct script execution fallback
    from supervisor_core import (
        DEFAULT_DROP_AFTER_S,
        DEFAULT_STALE_AFTER_S,
        SIMULATION_SCENARIOS,
        iter_merged_samples,
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit merged simulated ESP32 + DXMR90 supervisor samples."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=12,
        help="number of merged samples to emit",
    )
    parser.add_argument(
        "--rate-hz",
        type=float,
        default=10.0,
        help="merged sample cadence in Hz",
    )
    parser.add_argument(
        "--scenario",
        choices=SIMULATION_SCENARIOS,
        default="healthy",
        help="simulated source scenario",
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
        "--realtime",
        action="store_true",
        help="sleep between samples to approximate the selected cadence",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.samples < 1:
        print("--samples must be at least 1", file=sys.stderr)
        return 2
    if args.rate_hz <= 0:
        print("--rate-hz must be positive", file=sys.stderr)
        return 2
    if args.drop_after_s < 0:
        print("--drop-after-s must be non-negative", file=sys.stderr)
        return 2
    if args.stale_after_s < 0:
        print("--stale-after-s must be non-negative", file=sys.stderr)
        return 2

    period_s = 1.0 / args.rate_hz
    for index, sample in enumerate(
        iter_merged_samples(
            args.samples,
            args.rate_hz,
            scenario=args.scenario,
            drop_after_s=args.drop_after_s,
            stale_after_s=args.stale_after_s,
        )
    ):
        print(json.dumps(sample, sort_keys=True, separators=(",", ":")))
        if args.realtime and index + 1 < args.samples:
            time.sleep(period_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

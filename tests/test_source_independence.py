#!/usr/bin/env python3
"""Regression coverage for independently failing supervisor source arms."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from networked_sensors import supervisor_core
from networked_sensors.supervisor_core import (
    RealDxmr90Source,
    SimulatedEsp32Source,
    SourceMerger,
)


class RealDxmr90IndependenceTests(unittest.TestCase):
    def test_blocked_modbus_read_does_not_stall_esp32_merge(self) -> None:
        read_started = threading.Event()
        release_read = threading.Event()

        class BlockingModbusClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def __enter__(self) -> "BlockingModbusClient":
                read_started.set()
                release_read.wait(2.0)
                return self

            def __exit__(self, *args: object) -> None:
                return None

        with (
            patch.object(supervisor_core, "ModbusTcpClient", BlockingModbusClient),
            patch.object(
                supervisor_core,
                "read_direct_sick_values",
                return_value={"port1_mass_flow_g_min": 12.5},
            ),
        ):
            dxmr90 = RealDxmr90Source(timeout=1.0, rate_hz=10.0)
            merger = SourceMerger([SimulatedEsp32Source(), dxmr90])
            try:
                started_at = time.monotonic()
                sample = merger.poll(0.0, datetime.now(timezone.utc))
                poll_duration_s = time.monotonic() - started_at

                self.assertLess(poll_duration_s, 0.1)
                self.assertTrue(sample["esp32_connected"])
                self.assertFalse(sample["dxmr90_connected"])
                self.assertTrue(read_started.wait(1.0))

                next_sample = merger.poll(0.1, datetime.now(timezone.utc))
                self.assertTrue(next_sample["esp32_connected"])
                self.assertEqual(next_sample["esp32_sample_ms"], 100)

                release_read.set()
                deadline = time.monotonic() + 1.0
                elapsed_s = 0.2
                recovered = next_sample
                while time.monotonic() < deadline:
                    recovered = merger.poll(elapsed_s, datetime.now(timezone.utc))
                    if recovered["dxmr90_connected"]:
                        break
                    elapsed_s += 0.01
                    time.sleep(0.01)
                self.assertTrue(recovered["dxmr90_connected"])
                self.assertEqual(recovered["dxmr90_port1_mass_flow_g_min"], 12.5)
            finally:
                release_read.set()
                dxmr90.close()


if __name__ == "__main__":
    unittest.main()

"""Three SICK channels through real parsing and recording, without device I/O."""
import csv
import math
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import supervisor_core as core
from dashboard_app.runtime import SourceMerger
from read_dxmr90_modbus import ModbusError, read_direct_sick_values
from recorder import FlowRunRecorder


def process_words(kg_h, order="high-low"):
    words = []
    for value in (kg_h, 4, 5, .06, 7, 8, 23, 1.2):
        pair = list(struct.unpack(">HH", struct.pack(">f", value)))
        words.extend(pair if order == "high-low" else pair[::-1])
    return words


class Master:
    def __init__(self, status=3, flow=3, error=None, order="high-low"):
        self.status, self.flow, self.error, self.order = status, flow, error, order
        self.calls = []

    def read_holding_registers(self, register, quantity):
        self.calls.append((register, quantity))
        if register == 12001:
            return [12]
        if register in (1002, 2002):
            return process_words(.6 if register == 1002 else 1.2, self.order)
        if register == 3001:
            if self.error:
                raise self.error
            return [self.status, *process_words(self.flow, self.order)]
        raise AssertionError(f"Unexpected register {register}")


class SickFlowTests(unittest.TestCase):
    def test_third_is_independent_of_pair_total_and_uses_same_float_profile(self):
        for order in ("high-low", "low-high"):
            with self.subTest(order=order):
                client = Master(order=order)
                values = read_direct_sick_values(client, order)
                self.assertAlmostEqual(values["port1_mass_flow_g_min"], 10, places=5)
                self.assertAlmostEqual(values["port2_mass_flow_g_min"], 20, places=5)
                self.assertAlmostEqual(values["total_mass_flow_g_min"], 30, places=5)
                self.assertEqual(values["port3_mass_flow_g_min"], 50)
                self.assertTrue(values["port3_flow_valid"])
                self.assertIsNone(values["port3_error"])
                self.assertEqual(client.calls, [(12001, 1), (1002, 16), (2002, 16), (3001, 17)])

    def test_invalid_port_status_does_not_turn_retained_data_into_live_flow(self):
        for status in (0, 1, 2, 4):
            with self.subTest(status=status):
                values = read_direct_sick_values(Master(status=status, flow=99), "high-low")
                self.assertIsNone(values["port3_mass_flow_g_min"])
                self.assertFalse(values["port3_flow_valid"])
                self.assertEqual(values["port3_status"], status)
                self.assertTrue(values["port3_error"])
                self.assertAlmostEqual(values["total_mass_flow_g_min"], 30, places=5)
        zero = read_direct_sick_values(Master(status=3, flow=0), "high-low")
        self.assertEqual(zero["port3_mass_flow_g_min"], 0)
        self.assertTrue(zero["port3_flow_valid"])

    def test_third_timeout_exception_or_nonfinite_value_does_not_lose_pair(self):
        truncated = Master()
        original = truncated.read_holding_registers
        truncated.read_holding_registers = lambda register, count: (
            [3, 0] if register == 3001 else original(register, count))
        for client in (Master(error=TimeoutError("port 3 timeout")),
                       Master(error=ModbusError("illegal address")),
                       Master(flow=math.nan), Master(flow=math.inf), truncated):
            with self.subTest(client=client):
                values = read_direct_sick_values(client, "high-low")
                self.assertIsNone(values["port3_mass_flow_g_min"])
                self.assertFalse(values["port3_flow_valid"])
                self.assertTrue(values["port3_error"])
                self.assertAlmostEqual(values["total_mass_flow_g_min"], 30, places=5)

    def test_optional_failure_updates_live_pair_and_clears_prior_third(self):
        source = core.RealDxmr90Source()
        source._ensure_started = Mock()  # This test never starts transport threads.
        self.addCleanup(source.close)
        with patch.object(core, "ModbusTcpClient") as factory:
            factory.return_value.__enter__.return_value = Master()
            source._fetch()
            self.assertEqual(source.poll(0).values["dxmr90_port3_mass_flow_g_min"], 50)
            factory.return_value.__enter__.return_value = Master(error=TimeoutError("optional timeout"))
            source._fetch()
            values = source.poll(.1).values
            factory.return_value.__enter__.return_value = Master(flow=6)
            source._fetch()
            recovered = source.poll(.2).values
        self.assertIsNone(source.last_error)
        self.assertAlmostEqual(values["dxmr90_total_mass_flow_g_min"], 30, places=5)
        self.assertIsNone(values["dxmr90_port3_mass_flow_g_min"])
        self.assertFalse(values["dxmr90_port3_flow_valid"])
        self.assertEqual(values["dxmr90_port3_error"], "optional timeout")
        self.assertTrue(recovered["dxmr90_port3_flow_valid"])
        self.assertEqual(recovered["dxmr90_port3_mass_flow_g_min"], 100)
        self.assertIsNone(recovered["dxmr90_port3_error"])

    def test_republished_mode_has_no_invented_third_sensor_register(self):
        source = core.RealDxmr90Source(data_path="republished")
        source._ensure_started = Mock()
        self.addCleanup(source.close)
        with patch.object(core, "ModbusTcpClient"), patch.object(core, "read_direct_sick_values") as direct:
            with patch.object(core, "read_metrics", return_value=[
                    {"name": "total_mass_flow_g_min", "value": 30}]) as republished:
                source._fetch()
            direct.assert_not_called()
            self.assertFalse(any(metric.name.startswith("port3") for metric in republished.call_args.args[1]))
        values = source.poll(0).values
        self.assertEqual(values["dxmr90_total_mass_flow_g_min"], 30)
        self.assertIsNone(values["dxmr90_port3_mass_flow_g_min"])
        self.assertFalse(values["dxmr90_port3_flow_valid"])

    def test_csv_declares_third_before_first_reading_and_keeps_late_arrival(self):
        source = core.RealDxmr90Source()
        source._ensure_started = Mock()
        self.addCleanup(source.close)
        merger = SourceMerger([source], stale_after_s=.5)
        now = datetime.now(timezone.utc)
        first = merger.poll(0, now)
        self.assertIn("dxmr90_port3_mass_flow_g_min", first)
        self.assertIsNone(first["dxmr90_port3_mass_flow_g_min"])
        with tempfile.TemporaryDirectory() as directory:
            recorder = FlowRunRecorder(record_dir=Path(directory), metadata={}, run_config={},
                source_fields={source.name: source.expected_fields}, first_sample=first)
            with patch.object(core, "ModbusTcpClient") as factory:
                factory.return_value.__enter__.return_value = Master()
                source._fetch()
            current = merger.poll(.1, now)
            recorder.record_sample(current, merger.fresh_readings())
            stale = merger.poll(1, now)
            self.assertFalse(stale["dxmr90_connected"])
            recorder.finish()
            with recorder.paths.merged_csv.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["dxmr90_port3_mass_flow_g_min"], "")
            self.assertEqual(float(rows[1]["dxmr90_port3_mass_flow_g_min"]), 50)
            with recorder.paths.source_csvs["dxmr90"].open() as stream:
                raw = next(csv.DictReader(stream))
            self.assertEqual(float(raw["dxmr90_port3_mass_flow_g_min"]), 50)
            self.assertEqual(raw["dxmr90_port3_flow_valid"], "True")


if __name__ == "__main__":
    unittest.main()

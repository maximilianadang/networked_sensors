"""Three SICK channels through real parsing and recording, without device I/O."""
import csv
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
        if register == 3002:
            if self.error:
                raise self.error
            return process_words(self.flow, self.order)
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
                self.assertEqual(client.calls, [(12001, 1), (1002, 16), (2002, 16), (3002, 16)])

    def test_third_uses_process_data_without_a_separate_status_gate(self):
        for status in (0, 1, 2, 3, 4):
            with self.subTest(status=status):
                values = read_direct_sick_values(Master(status=status, flow=0), "high-low")
                self.assertEqual(values["port3_mass_flow_g_min"], 0)
                self.assertTrue(values["port3_flow_valid"])
                self.assertIsNone(values["port3_status"])
                self.assertIsNone(values["port3_error"])

    def test_each_port_read_failure_uses_the_same_source_error_path_and_recovers(self):
        for register in (1002, 2002, 3002):
            for error in (TimeoutError("read timeout"), ModbusError("illegal address"),
                          ValueError("invalid process data")):
                with self.subTest(register=register, error=error):
                    source = core.RealDxmr90Source()
                    source._ensure_started = Mock()
                    self.addCleanup(source.close)
                    master = Master()
                    read = master.read_holding_registers
                    def failing_read(address, count):
                        if address == register:
                            raise error
                        return read(address, count)
                    with patch.object(core, "ModbusTcpClient") as factory:
                        factory.return_value.__enter__.return_value = master
                        source._fetch()
                        self.assertEqual(source.poll(0).values["dxmr90_port3_mass_flow_g_min"], 50)
                        master.read_holding_registers = failing_read
                        source._fetch()
                        self.assertEqual(source.last_error, str(error))
                        self.assertIsNone(source.poll(.1))
                        master.read_holding_registers = read
                        master.flow = 6
                        source._fetch()
                        self.assertIsNone(source.last_error)
                        self.assertEqual(source.poll(.2).values["dxmr90_port3_mass_flow_g_min"], 100)

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

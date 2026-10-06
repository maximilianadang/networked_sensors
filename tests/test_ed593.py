"""ED-593 protocol, freshness and worksheet boundaries without live hardware."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import supervisor_core as core
from read_ed593_ascii import Ed593Client, RealEd593Source, channel_values
from dashboard_app.experiment import ExperimentRunner, parse_program
from dashboard_app.runtime import SourceMerger
from datetime import datetime, timezone

EXAMPLE = (Path(__file__).resolve().parents[1] / "examples/temperatures.csv").read_text()


class FakeSocket:
    def __init__(self, replies, checksum=False, fragment=False):
        self.replies, self.checksum, self.fragment = replies, checksum, fragment
        self.pending = b""
        self.commands = []
        self.closed = False

    def sendall(self, packet):
        self.commands.append(packet)
        command = packet[:-3] if self.checksum else packet[:-1]
        reply = self.replies[command.decode()].encode()
        if self.checksum:
            reply += f"{sum(reply) & 255:02X}".encode()
        self.pending = reply + b"\r"

    def recv(self, n):
        n = 2 if self.fragment else n
        part, self.pending = self.pending[:n], self.pending[n:]
        return part

    def settimeout(self, value):
        pass

    def close(self):
        self.closed = True


def replies(**changes):
    values = {"$01M0": "!01ED-593", "$012": "!01600680", "$016": "!010F", "$01B": "!0100",
              "#01": ">-001.50+025.00+030.00+100.00+000.00+000.00+000.00+000.00"}
    values.update(changes)
    return values


def sample(**changes):
    values = channel_values([25.0] * 8, 0x0F, 0)
    values.update(esp32_age_ms=100, esp32_transport_error=None,
                  esp32_pressure_adc_ready=True, esp32_flow_adc_ready=True,
                  ed593_age_ms=100, ed593_transport_error=None, ed593_connected=False)
    values.update(changes)
    return values


class Ed593Tests(unittest.TestCase):
    def test_read_only_protocol_handles_fragments_masks_and_negative_temperatures(self):
        connection = FakeSocket(replies(**{"$01B": "!0104"}), fragment=True)
        with patch("read_ed593_ascii.socket.create_connection", return_value=connection):
            with Ed593Client("device") as client:
                values = client.read()
                with self.assertRaisesRegex(ValueError, "read command"):
                    client.query("$015FF")
        self.assertEqual(connection.commands, [b"$01M0\r", b"$012\r", b"$016\r", b"$01B\r", b"#01\r"])
        self.assertEqual(values["ed593_tc0_temperature_c"], -1.5)
        self.assertTrue(values["ed593_tc0_ready"])
        self.assertTrue(values["ed593_tc2_fault"])
        self.assertIsNone(values["ed593_tc2_temperature_c"])
        self.assertFalse(values["ed593_tc7_enabled"])
        self.assertIsNone(values["ed593_tc7_temperature_c"])
        self.assertTrue(connection.closed)

    def test_rejects_wrong_units_checksum_config_and_ambiguous_replies(self):
        for changes in ({"$01M0": "!01ED-549"}, {"$012": "!01600684"}, {"$012": "!01600683"},
                        {"$012": "!016006C0"}, {"$012": "!02600680"},
                        {"$016": "!01100"}, {"$01B": "?01"},
                        {"#01": ">+025.00"}, {"#01": ">nan"},
                        {"#01": ">" + "+025.00" * 8 + "junk"}):
            with self.subTest(changes=changes):
                connection = FakeSocket(replies(**changes))
                with patch("read_ed593_ascii.socket.create_connection", return_value=connection):
                    with Ed593Client("device") as client, self.assertRaises(ValueError):
                        client.read()

    def test_checksum_enabled_and_corrupt_response(self):
        connection = FakeSocket(replies(**{"$012": "!016006C0"}), checksum=True)
        with patch("read_ed593_ascii.socket.create_connection", return_value=connection):
            with Ed593Client("device", checksum=True) as client:
                self.assertTrue(client.read()["ed593_tc0_ready"])
        command = b"$012"
        self.assertEqual(connection.commands[1], command + f"{sum(command) & 255:02X}\r".encode())
        client = Ed593Client("device", checksum=True)
        client.connection = Mock()
        client.connection.recv.return_value = b"!016006C000\r"
        with self.assertRaisesRegex(ValueError, "checksum"):
            client.query("$012")

    def test_disconnect_and_oversized_response_fail(self):
        client = Ed593Client("device")
        client.connection = Mock()
        client.connection.recv.return_value = b""
        with self.assertRaises(ConnectionError):
            client.query("$012")
        client.connection.recv.return_value = b"x" * 2049
        with self.assertRaisesRegex(ValueError, "2048"):
            client.query("$012")

    def test_background_success_error_and_recovery_preserve_response_age(self):
        source = RealEd593Source()
        source._thread = Mock()  # Suppress thread start; exercise one fetch at a time.
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.read.return_value = channel_values([25.0] * 8, 0x0F, 0)
        with patch("read_ed593_ascii.Ed593Client", return_value=client):
            with patch("read_ed593_ascii.time.monotonic", return_value=10):
                source._fetch()
            with patch("read_ed593_ascii.time.monotonic", return_value=12):
                reading = source.poll(5)
            self.assertEqual(reading.elapsed_s, 3)
            self.assertIsNone(source.poll(5.1))
            client.read.side_effect = OSError("disconnected")
            source._fetch()
            self.assertIn("disconnected", source.last_error)
            self.assertIsNone(source.poll(6))
            client.read.side_effect = None
            source._fetch()
            self.assertIsNone(source.last_error)
            self.assertIsNotNone(source.poll(7))

    def test_factory_default_off_and_simulation_merges_channels(self):
        sources = core.make_sources()
        self.assertEqual(sources[-1].name, "ed593")
        self.assertEqual(sources[-1].mode, "off")
        sources = core.make_sources(ed593_source="sim", esp32_auto_sequence=False)
        merger = SourceMerger(sources, stale_after_s=0.5)
        row = merger.poll(0, datetime.now(timezone.utc))
        self.assertTrue(row["ed593_connected"])
        self.assertTrue(row["ed593_tc0_ready"])
        self.assertFalse(row["ed593_tc7_ready"])
        row = merger.poll(0.6, datetime.now(timezone.utc))
        self.assertFalse(row["ed593_connected"])
        self.assertEqual(row["ed593_age_ms"], 600)

    def test_health_only_requires_selected_channels_and_worksheet_freshness(self):
        action = parse_program(EXAMPLE)[1]
        checks = action.evaluate(sample(ed593_tc7_fault=True, ed593_age_ms=3000))
        self.assertTrue(all(check["ok"] for check in checks.values()))
        checks = action.evaluate(sample(ed593_tc2_ready=False, ed593_tc2_fault=True))
        self.assertFalse(checks["tc2"]["ok"])
        self.assertTrue(checks["tc2"]["observed"]["ed593_tc2_fault"])

    def test_reading_inclusive_bounds_and_immediate_failure_no_commands(self):
        lines = EXAMPLE.splitlines()
        program = "\n".join((lines[0], lines[1], lines[3])) + "\n"
        for changes, outcome in (({"ed593_tc0_temperature_c": 0}, "completed"),
                                 ({"ed593_tc0_temperature_c": 100}, "completed"),
                                 ({"ed593_tc0_temperature_c": 100.01}, "failed"),
                                 ({"ed593_tc0_temperature_c": None}, "failed"),
                                 ({"ed593_tc0_temperature_c": True}, "failed"),
                                 ({"ed593_tc0_temperature_c": float("nan")}, "failed"),
                                 ({"ed593_tc0_enabled": False}, "completed"),
                                 ({"ed593_tc0_fault": True}, "completed"),
                                 ({"ed593_age_ms": 3001}, "failed"),
                                 ({"ed593_transport_error": "lost"}, "failed")):
            with self.subTest(changes=changes):
                callback = Mock()
                runner = ExperimentRunner(callback)
                runner.load(program, "temperatures.csv", 0)
                runner.start(0)
                runner.on_sample(sample(), 0.1)
                runner.on_sample(sample(**changes), 0.2)
                self.assertEqual(runner.state, outcome)
                callback.assert_not_called()
                if outcome == "failed":
                    self.assertEqual(runner.results[-1]["reason"], "reading_check_failed")
        action = parse_program(program)[1]
        self.assertTrue(action.evaluate(sample(ed593_tc7_fault=True))["temperature"]["ok"])

    def test_reading_parser_negative_bounds_and_rejects_ignored_operands(self):
        self.assertEqual(parse_program(EXAMPLE.replace(",0,100", ",-20,100"))[2].min_c, -20)
        for text in (EXAMPLE.replace(",0,100", ",101,100"),
                     EXAMPLE.replace(",0,100", ",nan,100"),
                     EXAMPLE.replace(",0,100", ",0,"), EXAMPLE.replace("tc0,", "tc8,"),
                     EXAMPLE.replace("reading_state,ed593", "reading_state,esp32"),
                     EXAMPLE.replace("reading_state,ed593,3000,ok,10,tc0,", "reading_state,ed593,3000,ok,10,tc0;tc1,"),
                     EXAMPLE.replace("10,pressure_adc;flow_adc,,", "10,pressure_adc;flow_adc,0,100", 1)):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_program(text)


if __name__ == "__main__":
    unittest.main()

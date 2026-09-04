import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from networked_sensors import firmware_upload
except ModuleNotFoundError:  # Direct execution from this directory.
    import firmware_upload

FirmwareUploadError = firmware_upload.FirmwareUploadError
TARGETS = firmware_upload.TARGETS
command_preview = firmware_upload.command_preview
collect_local_dependencies = firmware_upload.collect_local_dependencies
parse_board_list_json = firmware_upload.parse_board_list_json
resolve_payload = firmware_upload.resolve_payload
select_port = firmware_upload.select_port
sha256_file = firmware_upload.sha256_file
stage_sketch = firmware_upload.stage_sketch


class FirmwareUploadTests(unittest.TestCase):
    def test_target_defaults_match_repository_sketches(self) -> None:
        self.assertEqual(
            TARGETS["controllino"].default_payload,
            "controllino_ethernet_diagnostic.ino",
        )
        self.assertEqual(
            TARGETS["esp32"].default_payload,
            "Flow_management_unit_sch1.ino",
        )
        self.assertEqual(TARGETS["yun"].default_payload, "limit_switch_palas.ino")
        for target in TARGETS.values():
            payload = resolve_payload(None, target=target)
            self.assertTrue(payload.is_file())
            self.assertEqual(payload.suffix, ".ino")

    def test_controllino_target_uses_official_maxi_automation_board(self) -> None:
        target = TARGETS["controllino"]
        self.assertEqual(
            target.fqbn,
            "CONTROLLINO_Boards:avr:controllino_maxi_automation",
        )
        self.assertEqual(target.monitor_baud, 9600)

    def test_payload_requires_an_existing_ino_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text_file = root / "payload.txt"
            text_file.write_text("not a sketch", encoding="utf-8")
            with self.assertRaisesRegex(FirmwareUploadError, "must be a main"):
                resolve_payload(
                    text_file,
                    target=TARGETS["yun"],
                    repository_root=root,
                )

    def test_board_list_parser_accepts_current_cli_shape(self) -> None:
        ports = parse_board_list_json(
            {
                "detected_ports": [
                    {
                        "port": {
                            "address": "/dev/ttyACM0",
                            "protocol": "serial",
                        },
                        "matching_boards": [
                            {"name": "Arduino Yún", "fqbn": "arduino:avr:yun"}
                        ],
                    }
                ]
            }
        )
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0].address, "/dev/ttyACM0")
        self.assertEqual(ports[0].fqbns, ("arduino:avr:yun",))
        self.assertEqual(
            select_port(TARGETS["yun"], "auto", ports),
            "/dev/ttyACM0",
        )

    def test_auto_port_selection_refuses_a_different_board(self) -> None:
        ports = parse_board_list_json(
            [
                {
                    "address": "/dev/ttyACM1",
                    "protocol": "serial",
                    "boards": [
                        {
                            "name": "Adafruit Feather ESP32-S3",
                            "fqbn": TARGETS["esp32"].fqbn,
                        }
                    ],
                }
            ]
        )
        with self.assertRaisesRegex(FirmwareUploadError, "No connected serial/USB"):
            select_port(TARGETS["yun"], "auto", ports)
        self.assertEqual(
            select_port(TARGETS["yun"], "/dev/serial/by-id/manual", ports),
            "/dev/serial/by-id/manual",
        )

    def test_auto_port_selection_refuses_a_matching_network_board(self) -> None:
        ports = parse_board_list_json(
            {
                "detected_ports": [
                    {
                        "port": {
                            "address": "arduino.local",
                            "protocol": "network",
                        },
                        "matching_boards": [
                            {
                                "name": "Arduino Yún",
                                "fqbn": TARGETS["yun"].fqbn,
                            }
                        ],
                    }
                ]
            }
        )
        with self.assertRaisesRegex(FirmwareUploadError, "serial/USB"):
            select_port(TARGETS["yun"], "auto", ports)

    def test_preview_is_fixed_to_the_selected_fqbn(self) -> None:
        payload = resolve_payload(None, target=TARGETS["esp32"])
        preview = command_preview(TARGETS["esp32"], payload, "/dev/ttyACM7")
        self.assertIn(TARGETS["esp32"].fqbn, preview)
        self.assertIn("--verify", preview)
        self.assertIn("/dev/ttyACM7", preview)
        self.assertEqual(len(sha256_file(payload)), 64)

    def test_upload_is_blocked_without_safety_confirmation(self) -> None:
        with self.assertRaisesRegex(FirmwareUploadError, "Upload blocked"):
            firmware_upload.compile_and_upload(
                "yun",
                safety_confirmed=False,
            )

    def test_compile_only_stages_a_hash_identical_payload(self) -> None:
        calls = []

        def fake_run(arguments, **_kwargs):
            calls.append(list(arguments))
            sketch_dir = Path(arguments[-1])
            staged = sketch_dir / "limit_switch_palas.ino"
            self.assertTrue(staged.is_file())
            self.assertEqual(
                sha256_file(staged),
                sha256_file(resolve_payload(None, target=TARGETS["yun"])),
            )
            return mock.Mock(returncode=0)

        with mock.patch.object(
            firmware_upload,
            "run_arduino_cli",
            side_effect=fake_run,
        ):
            result = firmware_upload.compile_and_upload(
                "yun",
                compile_only=True,
                executable="/bin/true",
            )

        self.assertTrue(result.compiled)
        self.assertFalse(result.uploaded)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "compile")
        self.assertIn(TARGETS["yun"].fqbn, calls[0])

    def test_staging_copies_quoted_local_includes_without_other_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload.ino"
            header = root / "wifi_credentials.h"
            unrelated = root / "other.ino"
            source.write_text(
                '#include "wifi_credentials.h"\nvoid setup() {}\n',
                encoding="utf-8",
            )
            header.write_text(
                'const char* WIFI_SSID = "secret";\n',
                encoding="utf-8",
            )
            unrelated.write_text("do not stage me", encoding="utf-8")
            sketch_dir = root / "stage" / "payload"
            sketch_dir.mkdir(parents=True)

            dependencies = collect_local_dependencies(source)
            staged = stage_sketch(source, sketch_dir)

            self.assertEqual(
                {path.name for path in dependencies},
                {"payload.ino", "wifi_credentials.h"},
            )
            self.assertEqual(
                set(staged),
                {"payload.ino", "wifi_credentials.h"},
            )
            self.assertFalse((sketch_dir / unrelated.name).exists())
            self.assertEqual(
                sha256_file(sketch_dir / header.name),
                sha256_file(header),
            )


if __name__ == "__main__":
    unittest.main()

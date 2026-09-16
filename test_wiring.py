"""Compile wiring configurations and verify the real upload staging path."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    from networked_sensors.firmware_upload import stage_sketch
except ModuleNotFoundError:
    from firmware_upload import stage_sketch

ROOT = Path(__file__).parent
HEADERS = ("wiring_controllino.h", "wiring_esp32.h", "wiring_yun.h")


class WiringTests(unittest.TestCase):
    def compile(self, header, replacements=(), *, preamble=None):
        # Use the direct macOS compiler to avoid invoking the Xcode license shim.
        apple = Path("/Library/Developer/CommandLineTools/usr/bin/clang++")
        compiler = os.environ.get("CXX") or (
            str(apple) if apple.exists() else shutil.which("c++")
        )
        if not compiler:
            self.skipTest("C++11 compiler unavailable")
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for name in (*HEADERS, "wiring_checks.h", "controllino_firmware.h"):
                text = (ROOT / name).read_text()
                if name == header:
                    for old, new in replacements:
                        self.assertIn(old, text)
                        text = text.replace(old, new)
                (stage / name).write_text(text)
            (stage / "Arduino.h").write_text(
                "#pragma once\nusing byte = unsigned char;\n"
                "using uint8_t = unsigned char;\n"
                "#define HIGH 1\n#define LOW 0\n#define LED_BUILTIN 13\n"
                "#define PIN_SPI_SS_ETHERNET_LIB 70\n"
                "#define NOT_AN_INTERRUPT -1\n"
                "#define digitalPinToInterrupt(p) ((p)==2 || (p)==3 || ((p)>=18 && (p)<=21) ? 0 : NOT_AN_INTERRUPT)\n"
                "constexpr int MISO=50, MOSI=51, SCK=52, SS=53;\n"
            )
            for name in ("SPI.h", "Ethernet.h"):
                (stage / name).write_text("")
            source = stage / "check.cpp"
            source.write_text(preamble or f'#include "{header}"\n')
            return subprocess.run(
                [compiler, "-std=c++11", "-fsyntax-only", "-I", str(stage), str(source)],
                capture_output=True, text=True,
            )

    def test_default_maps_compile(self):
        for header in HEADERS:
            with self.subTest(header=header):
                result = self.compile(header)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_alternate_wiring_compiles_without_sketch_edits(self):
        cases = (
            (HEADERS[0], (("PIN_DIR = 5", "PIN_DIR = 8"),)),
            (HEADERS[1], (("{5, 6, 9, 10}", "{10, 9, 6, 5}"),
                          ("{0, 1, 2}", "{2, 0, 3}"))),
            (HEADERS[2], (("PIN_RUN = 4", "PIN_RUN = 5"),
                          ("PIN_DIR = 5", "PIN_DIR = 4"))),
        )
        for header, edits in cases:
            with self.subTest(header=header):
                result = self.compile(header, edits)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_maps_fail_at_compile_time(self):
        cases = (
            (HEADERS[0], "PIN_DIR = 5", "PIN_DIR = 3", "pin collision"),
            (HEADERS[0], "PIN_DIR = 5", "PIN_DIR = 70", "pin collision"),
            (HEADERS[0], "PIN_DRO_CLOCK = 21", "PIN_DRO_CLOCK = 4", "external interrupt"),
            (HEADERS[0], "PIN_DRO_DATA = 6", "PIN_DRO_DATA = 3", "pin collision"),
            (HEADERS[1], "I2C_SDA = 3", "I2C_SDA = 5", "pin collision"),
            (HEADERS[1], "FLOW_ADC_ADDRESS = 0x49", "FLOW_ADC_ADDRESS = 0x48", "distinct I2C"),
            (HEADERS[1], "{0, 1, 2}", "{0, 1, 4}", "channels must"),
            (HEADERS[2], "PIN_RUN = 4", "PIN_RUN = 7", "pin collision"),
            (HEADERS[2], "PIN_DRO_CLOCK = 10", "PIN_DRO_CLOCK = 13", "DRO capture requires"),
        )
        for header, old, new, message in cases:
            with self.subTest(header=header, edit=new):
                result = self.compile(header, ((old, new),))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_bringup_rejects_remapping_fixed_timer_output(self):
        preamble = (ROOT / "controllino_stepper_bringup.ino").read_text().split("namespace {", 1)[0]
        result = self.compile(HEADERS[0], preamble=preamble)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.compile(HEADERS[0], (("PIN_STEP = 3", "PIN_STEP = 2"),), preamble=preamble)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OC3C requires STEP", result.stderr)

    def test_upload_stages_wiring_and_checks_unchanged(self):
        for sketch, header in (
            ("controllino_motion_control.ino", HEADERS[0]),
            ("controllino_stepper_bringup.ino", HEADERS[0]),
            ("Flow_management_unit_sch1.ino", HEADERS[1]),
            ("limit_switch_palas.ino", HEADERS[2]),
        ):
            with self.subTest(sketch=sketch), tempfile.TemporaryDirectory() as directory:
                stage = Path(directory)
                files = stage_sketch(ROOT / sketch, stage)
                for name in (header, "wiring_checks.h"):
                    self.assertIn(name, files)
                    self.assertEqual((stage / name).read_bytes(), (ROOT / name).read_bytes())


if __name__ == "__main__":
    unittest.main()

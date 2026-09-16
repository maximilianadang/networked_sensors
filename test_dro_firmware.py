"""Execute the real C++ DRO reader with injected edges, then decode its telemetry."""
import json
import os
import runpy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from networked_sensors.supervisor_core import ControllinoStepperSource

ROOT = Path(__file__).parent


class DroFirmwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apple = Path("/Library/Developer/CommandLineTools/usr/bin/clang++")
        compiler = os.environ.get("CXX") or (
            str(apple) if apple.exists() else shutil.which("c++")
        )
        if not compiler:
            raise unittest.SkipTest("C++11 compiler unavailable")
        sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
        sdk_flags = ["-isysroot", str(sdk)] if compiler == str(apple) and sdk.exists() else []
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "dro-test"
            subprocess.run([
                compiler, *sdk_flags, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "tests/dro_stubs"), "-I", str(ROOT),
                str(ROOT / "tests/absolute_dro_test.cpp"), "-o", str(binary),
            ], check=True, capture_output=True, text=True)
            output = subprocess.check_output([str(binary)], text=True)
        cls.samples = dict(
            (name, json.loads(payload))
            for name, payload in (line.split("\t", 1) for line in output.splitlines())
        )

    def test_reader_edges_validation_freshness_and_overrun(self):
        # C++ assertions cover every transition; these are the emitted test cases.
        self.assertEqual(len(self.samples), 13)
        self.assertEqual(self.samples["recovered"]["dr"], -100)
        self.assertEqual(self.samples["rollover"]["da"], 31)

    def test_real_reader_telemetry_reaches_dashboard_schema(self):
        from networked_sensors.test_stepper_control import ControllinoStepperSourceTests

        source = ControllinoStepperSource("http://127.0.0.1:1")
        base = json.loads(ControllinoStepperSourceTests.STATUS)
        try:
            for name, sample in self.samples.items():
                with self.subTest(sample=name):
                    values = source._decode_network_status(json.dumps({**base, **sample}))
                    self.assertTrue(values["stepper_dro_capable"])
                    self.assertEqual(values["stepper_dro_fresh"], bool(sample["df"]))
                    self.assertEqual(values["stepper_dro_position_mm"], sample["dr"] / 100)
                    self.assertEqual(values["stepper_dro_valid_frame_count"], sample["dq"])
                    self.assertEqual(values["stepper_dro_dropped_frame_count"], sample["dx"] & 255)
                    self.assertEqual(values["stepper_dro_rejected_frame_count"], sample["dx"] >> 8)
        finally:
            source.close()


    def test_lean_runtime_zero_and_velocity_use_real_reader_samples(self):
        from networked_sensors.test_stepper_control import ControllinoStepperSourceTests

        lean = runpy.run_path(str(ROOT / "dashboard-lean.py"))
        source = ControllinoStepperSource("http://127.0.0.1:1")
        base = json.loads(ControllinoStepperSourceTests.STATUS)
        with tempfile.TemporaryDirectory() as directory:
            settings = vars(lean["parse_args"]([
                "--system-config", str(Path(directory) / "system.json"),
                "--record-dir", str(Path(directory) / "recordings"),
                "--esp32-source", "off", "--dxmr90-source", "off",
            ]))
            for key in ("host", "port", "verbose_http"):
                settings.pop(key)
            runtime = lean["DashboardRuntime"](**{
                lean["RUNTIME_NAMES"].get(key, key): value
                for key, value in settings.items()
            })
            def sample(name):
                return {**source._decode_network_status(json.dumps({**base, **self.samples[name]})),
                        "stepper_connected": True}
            try:
                first = sample("first")
                runtime.latest = first
                runtime.set_stepper_dro_zero()
                self.assertEqual(first["stepper_dro_zeroed_position_mm"], 0)
                runtime._apply_stepper_dro_velocity_locked(first, 0.1)
                second = sample("second")
                runtime._apply_stepper_dro_zero_locked(second)
                runtime._apply_stepper_dro_velocity_locked(second, 0.3)
                self.assertAlmostEqual(second["stepper_dro_zeroed_position_mm"], 0.2)
                self.assertEqual(second["stepper_dro_velocity_mm_s"], 1.0)
                self.assertEqual(runtime.system_config.stepper_dro_zero_raw_mm, 123.45)
                stale = sample("stale")
                runtime._apply_stepper_dro_velocity_locked(stale, 0.551)
                self.assertIsNone(stale["stepper_dro_velocity_mm_s"])
                runtime.latest = stale
                with self.assertRaisesRegex(RuntimeError, "fresh connected"):
                    runtime.set_stepper_dro_zero()
            finally:
                runtime.stop()
                source.close()


if __name__ == "__main__":
    unittest.main()

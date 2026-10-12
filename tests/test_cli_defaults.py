"""Installation defaults and explicit source overrides; no device I/O."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import supervisor_core as core


_spec = importlib.util.spec_from_file_location(
    "dashboard_lean_cli", Path(__file__).resolve().parents[1] / "dashboard-lean.py")
dashboard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dashboard)


class CliDefaultsTests(unittest.TestCase):
    PROFILES = (("controllino", "off"), ("controllino-usb", "off"),
                ("usb", "real"), ("network", "real"),
                ("sim", "sim"), ("off", "real"))

    def parse(self, arguments=(), environment=None):
        # The developer's shell/launcher settings must not affect these cases.
        with patch.dict(os.environ, environment or {}, clear=True):
            return dashboard.parse_args(list(arguments))

    def test_current_installation_defaults_to_controllino_without_esp32(self):
        settings = self.parse()
        self.assertEqual(settings.stepper_source, "controllino")
        self.assertEqual(settings.esp32_source, "off")

    def test_automatic_source_matches_current_legacy_and_simulation_profiles(self):
        for profile, expected in self.PROFILES:
            with self.subTest(profile=profile):
                settings = self.parse(("--stepper-source", profile))
                self.assertEqual(settings.esp32_source, expected)

    def test_explicit_cli_source_preserves_independent_source_selection(self):
        for profile, _ in self.PROFILES:
            for source in ("real", "sim", "off"):
                with self.subTest(profile=profile, source=source):
                    settings = self.parse(("--stepper-source", profile, "--esp32-source", source))
                    self.assertEqual(settings.esp32_source, source)

    def test_environment_overrides_default_and_cli_overrides_environment(self):
        for profile, _ in self.PROFILES:
            for source in ("real", "sim", "off"):
                with self.subTest(profile=profile, environment=source):
                    environment = {"ESP32_SOURCE": source}
                    base = ("--stepper-source", profile)
                    self.assertEqual(self.parse(base, environment).esp32_source, source)
                    for explicit in ("real", "sim", "off"):
                        self.assertEqual(self.parse(
                            (*base, "--esp32-source", explicit), environment).esp32_source, explicit)

    def test_auto_and_empty_environment_resolve_after_profile_selection(self):
        for profile, expected in self.PROFILES:
            with self.subTest(profile=profile):
                base = ("--stepper-source", profile)
                for environment in ({"ESP32_SOURCE": "auto"}, {"ESP32_SOURCE": ""}):
                    self.assertEqual(self.parse(base, environment).esp32_source, expected)
                # Explicit CLI auto also wins over an older launcher's real setting.
                self.assertEqual(self.parse((*base, "--esp32-source", "auto"),
                                            {"ESP32_SOURCE": "real"}).esp32_source, expected)

    def test_library_factory_keeps_its_offline_simulation_defaults(self):
        with patch.object(core, "RealEsp32Source", side_effect=AssertionError("unexpected real source")):
            sources = core.make_sources()
        self.assertEqual({source.name: source.mode for source in sources},
                         {"esp32": "sim", "dxmr90": "sim", "stepper": "sim", "ed593": "off"})


if __name__ == "__main__":
    unittest.main()

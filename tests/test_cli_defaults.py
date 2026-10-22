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
        self.assertEqual(settings.ed593_source, "real")
        self.assertEqual(settings.ed593_host, "10.77.0.11")
        self.assertEqual(settings.stepper_timeout, 5.0)

    def test_network_address_defaults_follow_selected_transport(self):
        for kind, address, adapter in (
                ('controllino', core.DEFAULT_CONTROLLINO_NETWORK_URL, core.ControllinoStepperSource),
                ('network', core.DEFAULT_STEPPER_NETWORK_URL, core.NetworkStepperSource)):
            with self.subTest(source=kind):
                settings = self.parse(('--stepper-source', kind))
                sources = core.make_sources(stepper_source=settings.stepper_source,
                                            stepper_network_url=settings.stepper_url)
                source = next(source for source in sources if source.name=='stepper')
                self.assertIsInstance(source, adapter)
                self.assertEqual(source.base_url, address)
                source.close()

    def test_explicit_network_addresses_survive_cli_and_environment(self):
        self.assertEqual(self.parse(environment={'CONTROLLINO_URL':'tcp://10.77.0.12:80'}).stepper_url,
                         'tcp://10.77.0.12:80')
        self.assertEqual(self.parse(('--stepper-url', 'tcp://10.77.0.13:81'),
                                    {'CONTROLLINO_URL':'http://10.77.0.10'}).stepper_url,
                         'tcp://10.77.0.13:81')

    def test_controllino_usb_discovers_by_default_and_preserves_explicit_port(self):
        self.assertEqual(self.parse(("--stepper-source", "controllino-usb")).stepper_port, "auto")
        self.assertEqual(self.parse(("--stepper-source", "usb")).stepper_port, core.DEFAULT_STEPPER_USB_PORT)
        self.assertEqual(self.parse(("--stepper-source", "controllino-usb", "--stepper-port", "/dev/ttyACM3")).stepper_port,
                         "/dev/ttyACM3")

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

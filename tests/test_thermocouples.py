"""Component names persist independently of source telemetry and channel numbering."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard_app.system_config import SystemConfig


class ThermocoupleConfigTests(unittest.TestCase):
    def test_existing_config_supports_all_inputs_without_rewriting_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'system.json'
            original = json.dumps({'version': 1, 'stepper': {'dro_home': 25, 'dro_kind': 'linace'}})
            path.write_text(original)
            config = SystemConfig(path)
            self.assertEqual([(item['device'], item['label'], item['component'])
                              for item in config.thermocouple_descriptors()],
                             [(f'tc{i}', f'TC-{i}', '') for i in range(8)])
            self.assertEqual(path.read_text(), original)
            config.set_thermocouple_components({'tc0': '  Heater  ', 'tc1': 'Chamber'})
            config.set_thermocouple_components({'tc1': 'Outlet', 'tc7': 'Extra probe'})
            reloaded = SystemConfig(path)
            self.assertEqual(reloaded.thermocouple_components,
                             {'tc0':'Heater', 'tc1':'Outlet', 'tc2':'', 'tc3':'',
                              'tc4':'', 'tc5':'', 'tc6':'', 'tc7':'Extra probe'})
            self.assertEqual(reloaded.thermocouple_descriptors()[-1]['label'], 'TC-7')
            self.assertEqual(reloaded.stepper_dro_zero_raw_mm, 25)
            self.assertEqual(reloaded.stepper_dro_kind, 'linace')
            reloaded.set_thermocouple_components({'tc0':''})
            self.assertEqual(SystemConfig(path).thermocouple_components['tc0'], '')

    def test_legacy_saved_names_do_not_limit_available_channel_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'system.json'
            original = json.dumps({'version':1, 'stepper':{'dro_home':None},
                                   'thermocouples':{'tc0':'Heater', 'tc3':'Outlet'}})
            path.write_text(original)
            config = SystemConfig(path)
            descriptors = config.thermocouple_descriptors()
            self.assertEqual([item['device'] for item in descriptors], [f'tc{i}' for i in range(8)])
            self.assertEqual(descriptors[0]['component'], 'Heater')
            self.assertEqual(descriptors[3]['component'], 'Outlet')
            self.assertEqual(descriptors[6], {'device':'tc6', 'label':'TC-6', 'component':''})
            self.assertEqual(path.read_text(), original)
            config.set_thermocouple_components({'tc6':'Added probe'})
            self.assertEqual(SystemConfig(path).thermocouple_descriptors()[6]['component'], 'Added probe')

    def test_invalid_names_and_failed_writes_preserve_the_saved_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'system.json'
            config = SystemConfig(path)
            config.set_thermocouple_components({'tc0':'Inlet'})
            before = path.read_text()
            for values in ([], None, {'tc8':'Outside'}, {'TC-1':'Wrong identity'},
                           {'tc0':None}, {'tc0':True}, {'tc0':'x'*81}):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    config.set_thermocouple_components(values)
                self.assertEqual(path.read_text(), before)
                self.assertEqual(config.thermocouple_components['tc0'], 'Inlet')
            with patch.object(config, '_write_atomic', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    config.set_thermocouple_components({'tc0':'Unsaved'})
            self.assertEqual(config.thermocouple_components['tc0'], 'Inlet')
            self.assertEqual(path.read_text(), before)

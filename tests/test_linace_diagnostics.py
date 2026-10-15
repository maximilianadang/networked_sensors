"""Read-only LinACE input diagnostics and measured-position availability."""
import json
import unittest

from test_backend_boundary import Controller


class LinaceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()
        self.payload = dict(self.controller.payload, fw='1.5.5', dkind='linace', dc=1,
                            dpwm=0, dstat=0, dedge=0)

    def decode(self, **changes):
        return self.controller.decode_status_line(json.dumps(dict(self.payload, **changes)))

    def test_input_levels_distinguish_no_pwm_from_status_low(self):
        value = self.decode(dpwm=0, dstat=1, dedge=0)
        self.assertEqual(value['stepper_dro_pwm_raw'], 'LOW')
        self.assertEqual(value['stepper_dro_status_raw'], 'HIGH')
        self.assertEqual(value['stepper_dro_edge_count'], 0)
        value = self.decode(dpwm=1, dstat=0, dedge=300)
        self.assertEqual(value['stepper_dro_status_raw'], 'LOW')
        self.assertEqual(value['stepper_dro_edge_count'], 300)

    def test_no_frame_does_not_report_the_initial_zero_as_a_measurement(self):
        value = self.decode()
        self.assertIsNone(value['stepper_dro_position_mm'])
        self.assertIsNone(value['stepper_dro_displacement_mm'])
        self.assertFalse(value['stepper_dro_fresh'])

    def test_real_zero_position_is_available_after_a_valid_frame(self):
        value = self.decode(df=1, da=0, dq=1, dedge=3, dstat=1)
        self.assertEqual(value['stepper_dro_position_mm'], 0)
        self.assertTrue(value['stepper_dro_fresh'])

    def test_older_firmware_remains_compatible_without_input_diagnostics(self):
        value = self.controller.decode_status_line(json.dumps(self.controller.payload))
        self.assertIsNone(value['stepper_dro_pwm_raw'])
        self.assertIsNone(value['stepper_dro_status_raw'])
        self.assertIsNone(value['stepper_dro_edge_count'])

    def test_input_diagnostics_reject_missing_or_invalid_fields(self):
        for changes in ({'dpwm': None}, {'dstat': 2}, {'dpwm': True},
                        {'dedge': -1}, {'dedge': True}, {'dedge': 2**32}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.decode(**changes)
        missing = dict(self.payload)
        missing.pop('dstat')
        with self.assertRaises(ValueError):
            self.controller.decode_status_line(json.dumps(missing))

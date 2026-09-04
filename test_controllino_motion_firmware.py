from pathlib import Path
import unittest

FIRMWARE = (Path(__file__).parent / "controllino_motion_control.ino").read_text()

class ControllinoMotionFirmwareTests(unittest.TestCase):
    def test_x1_pin_map_and_direction_are_explicit(self):
        for item in ("PIN_STEP = 3", "PIN_DIR = 5", "PIN_ENABLE = 7",
                     "DIR_FORWARD = HIGH", "DIR_REVERSE = LOW"):
            self.assertIn(item, FIRMWARE)

    def test_each_timer_has_one_owner(self):
        for vector in ("TIMER1_COMPA_vect", "TIMER3_OVF_vect", "TIMER3_COMPA_vect"):
            self.assertEqual(FIRMWARE.count(f"ISR({vector})"), 1)

    def test_complete_command_surface_is_retained(self):
        for command in ("V1 S", "V1 M0", "V1 M1", "V1 H", "V1 G", "V1 X",
                        "V1 E0", "V1 E1", "V1 B0", "V1 B1", "V1 P", "V1 R"):
            self.assertIn(command, FIRMWARE)

    def test_absent_hardware_is_not_reported_clear_or_fresh(self):
        self.assertIn('reject("home_switch_unavailable")', FIRMWARE)
        self.assertIn('\\"d6\\":-1,\\"d8\\":-1', FIRMWARE)
        self.assertIn('\\"dc\\":0,\\"df\\":0', FIRMWARE)

    def test_priority_stops_precede_transport_ownership(self):
        ownership = FIRMWARE.index("claimable(source)")
        self.assertLess(FIRMWARE.index('"V1 E1"'), ownership)
        self.assertLess(FIRMWARE.index('"V1 X"'), ownership)

    def test_safe_levels_precede_output_enable(self):
        setup = FIRMWARE[FIRMWARE.index("void setup()") :]
        for pin, level in (("PIN_ENABLE", "DRIVER_DISABLED"),
                           ("PIN_STEP", "STEP_IDLE"), ("PIN_DIR", "DIR_REVERSE")):
            self.assertLess(setup.index(f"digitalWrite({pin}, {level})"),
                            setup.index(f"pinMode({pin}, OUTPUT)"))

    def test_yun_only_interfaces_are_absent(self):
        self.assertNotIn("Serial1", FIRMWARE)
        self.assertNotIn("PCINT0_vect", FIRMWARE)

if __name__ == "__main__":
    unittest.main()

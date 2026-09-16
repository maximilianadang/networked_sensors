"""Version/capability selection and confirmed position-servo commands."""
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from networked_sensors.supervisor_core import ControllinoStepperSource, ControllinoUsbStepperSource
from networked_sensors import test_solenoid_routing as fixtures

ROOT = Path(__file__).parent


def servo_payload():
    payload = dict(fixtures.FakeControllino().payload)
    payload.pop('bo', None)
    payload.pop('bp', None)
    payload.update(fw='1.1.0', aux='servo', apin=4, sv=0, sp=1500, smin=1000, smax=2000)
    return payload


class ServoTests(unittest.TestCase):
    def test_usb_and_network_support_identical_servo_contract(self):
        for cls in (ControllinoStepperSource, ControllinoUsbStepperSource):
            source = cls('http://unused.invalid')
            values = source.decode_status_line(json.dumps(servo_payload()))
            self.assertEqual(values['stepper_firmware_version'], '1.1.0')
            self.assertTrue(values['stepper_servo_capable'])
            self.assertFalse(values['stepper_brushless_motor_capable'])
            source._require_connected = Mock(return_value=values)
            source._write_command = Mock()
            source.set_servo_pulse(1700)
            self.assertEqual(source._write_command.call_args.args[0], b'V1 A1700\n')
            for invalid in (True, None, 999, 2001, 1500.5, '1500'):
                with self.assertRaises(ValueError):
                    source.set_servo_pulse(invalid)
            values['stepper_estop_latched'] = True
            with self.assertRaisesRegex(RuntimeError, 'E-STOP'):
                source.set_servo_pulse(1700)
            source.set_servo_pulse(0)
            self.assertEqual(source._write_command.call_args.args[0], b'V1 A0\n')
            old = source.decode_status_line(json.dumps(fixtures.FakeControllino().payload))
            self.assertFalse(old['stepper_servo_capable'])
            self.assertTrue(old['stepper_brushless_motor_capable'])
            source._require_connected.return_value = old
            with self.assertRaisesRegex(RuntimeError, 'does not support'):
                source.set_servo_pulse(1500)

    def test_bad_capabilities_never_enable_controls(self):
        source = ControllinoStepperSource('http://unused.invalid')
        for changes in ({'fw':None}, {'aux':'unknown'}, {'bo':0}, {'sv':2},
                        {'smin':2001}, {'sp':None}, {'sp':2500}, {'e':1,'sv':1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                source.decode_status_line(json.dumps(dict(servo_payload(), **changes)))

    def test_runtime_confirms_position_and_disable(self):
        test = fixtures.SolenoidRoutingTests()
        test.setUp()
        test.stepper.payload = servo_payload()
        test.stepper.refresh()
        def write(command, description):
            test.stepper.commands.append(command)
            pulse = int(command[4:])
            test.stepper.payload['sv'] = int(pulse != 0)
            if pulse: test.stepper.payload['sp'] = pulse
            test.stepper.refresh()
        test.stepper._write_command = write
        with TemporaryDirectory() as tmp:
            runtime = test.runtime(tmp)
            result = runtime.set_stepper_servo({'pulse_us':1700})
            self.assertTrue(result['confirmed'])
            self.assertEqual(result['stepper']['stepper_servo_pulse_us'], 1700)
            self.assertTrue(result['stepper']['stepper_servo_enabled'])
            result = runtime.set_stepper_servo({'pulse_us':0})
            self.assertFalse(result['stepper']['stepper_servo_enabled'])
            runtime.stop()

    def test_actual_firmware_parser_and_timer_in_both_build_modes(self):
        firmware = (ROOT/'controllino_motion_control.ino').read_text()
        globals_and_servo = firmware[firmware.index('volatile unsigned int auxTicks'):firmware.index('uint16_t compareFor(')]
        esc = firmware[firmware.index('void setEsc('):firmware.index('ISR(TIMER1_COMPA_vect)')]
        timers = firmware[firmware.index('ISR(TIMER3_OVF_vect)'):firmware.index('// ---------- 3.')]
        state = firmware[firmware.index('enum Mode :'):firmware.index('void halt(')]
        parser = firmware[firmware.index('bool parseLong('):firmware.index('void writeStatus(')]
        harness = r'''
#include <string.h>
#include <stdlib.h>
#include "controllino_firmware.h"
using byte=unsigned char;
#define ATOMIC_BLOCK(x) for(bool once=true;once;once=false)
#define ISR(name) void name()
#define HIGH 1
#define LOW 0
const int PIN_AUX=4, PIN_SOLENOID4=27, SOLENOID_ON=1, SOLENOID_OFF=0;
const int ESC_OFF_US=1000, ESC_MAX_US=2000, ESC_TICKS_PER_US=2;
const int MIN_SPS=25, MAX_SPS=2520;
const long MAX_RELATIVE_PULSES=34565;
int auxLevel=0, OCR3A=0;
long cruiseSps=1000,pulsePosition=0;
void digitalWrite(int pin,int value){if(pin==PIN_AUX)auxLevel=value;}
unsigned long millis(){return 0;}
bool moving(){return false;}
''' + globals_and_servo + esc + timers + state + r'''
void halt(State next,const char*,bool=true){state=next;}
''' + parser + r'''
void command(const char* text){char line[48];strcpy(line,text);processCommand(line,OWNER_USB);}
int main(){
 if(AUX_IS_SERVO){
  TIMER3_OVF_vect();if(auxLevel||auxPulseEnabled)return 1;
  command("V1 A1700");if(!accepted||servoPulseUs!=1700||!auxPulseEnabled)return 2;
  TIMER3_OVF_vect();if(auxLevel!=1||OCR3A!=3400)return 3;
  TIMER3_COMPA_vect();if(auxLevel)return 4;
  command("V1 A2001");if(accepted||servoPulseUs!=1700)return 5;
  command("V1 A1500x");if(accepted||servoPulseUs!=1700)return 6;
  command("V1 B1");if(accepted||escOn)return 7;
  command("V1 P1200");if(accepted)return 8;
  command("V1 E1");if(auxPulseEnabled||auxLevel)return 9;
  command("V1 A1500");if(accepted||auxPulseEnabled)return 10;
  command("V1 E0");if(auxPulseEnabled)return 11;
  command("V1 A1000");if(!accepted||!auxPulseEnabled)return 12;
  command("V1 A0");if(!accepted||auxPulseEnabled||auxLevel)return 13;
 } else {
  command("V1 A1500");if(accepted)return 14;
  command("V1 B1");if(!accepted||!escOn||auxTicks!=2400)return 15;
  command("V1 E1");if(escOn||auxTicks!=2000)return 16;
 }
 return 0;
}
'''
        compiler='/Library/Developer/CommandLineTools/usr/bin/clang++'
        if not Path(compiler).exists(): compiler=shutil.which('c++')
        if not compiler: self.skipTest('C++ compiler unavailable')
        with TemporaryDirectory() as tmp:
            source,binary=Path(tmp)/'servo.cpp',Path(tmp)/'servo'
            source.write_text(harness)
            sdk=Path('/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk')
            flags=['-isysroot',str(sdk)] if sdk.exists() else []
            for mode in (0,1):
                result=subprocess.run([compiler,*flags,'-std=c++11',f'-DCONTROLLINO_AUX_SERVO={mode}','-I',str(ROOT),str(source),'-o',str(binary)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                subprocess.run([str(binary)],check=True)


if __name__=='__main__':
    unittest.main()

"""Execute the actual Controllino command parser with fake GPIO."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parent


class SolenoidFirmwareTests(unittest.TestCase):
    def test_relay_commands_ownership_and_estop(self):
        firmware = (ROOT / 'controllino_motion_control.ino').read_text()
        setter = firmware[firmware.index('void setSolenoid4('):firmware.index('uint16_t compareFor(')]
        state = firmware[firmware.index('enum Mode :'):firmware.index('void halt(')]
        parser = firmware[firmware.index('bool parseLong('):firmware.index('void writeStatus(')]
        harness = r'''
#include <string.h>
#include <stdlib.h>
using byte = unsigned char;
#define ATOMIC_BLOCK(x) for(bool once=true;once;once=false)
const int PIN_SOLENOID4=27, SOLENOID_ON=1, SOLENOID_OFF=0;
const int ESC_OFF_US=1000, ESC_MAX_US=2000, MIN_SPS=25, MAX_SPS=2520;
const long MAX_RELATIVE_PULSES=34565;
const bool AUX_IS_SERVO=false;
const int SERVO_MIN_US=1000, SERVO_MAX_US=2000;
void setServoPulse(unsigned int, byte=0){}
bool solenoid4On=false, escOn=false;
int gpio=0, escOnUs=1200;
long cruiseSps=1000, pulsePosition=0;
void digitalWrite(int pin,int value){if(pin!=27)__builtin_trap();gpio=value;}
unsigned long millis(){return 0;}
bool moving(){return false;}
void setEsc(bool value){escOn=value;}
''' + setter + state + r'''
void halt(State next,const char*,bool=true){state=next;}
''' + parser + r'''
void command(const char* text,byte source=OWNER_USB){char line[48];strcpy(line,text);processCommand(line,source);}
int main(){
 command("V1 L4,1");if(!accepted||!solenoid4On||gpio!=1)return 1;
 command("V1 L4,1");if(!accepted||!solenoid4On)return 2;
 command("V1 L4,0",OWNER_NETWORK);if(accepted||!solenoid4On)return 3;
 command("V1 E1",OWNER_NETWORK);if(!accepted||solenoid4On||gpio||!estop)return 4;
 command("V1 L4,1");if(accepted||solenoid4On||gpio)return 5;
 command("V1 L4,0");if(!accepted||solenoid4On)return 6;
 command("V1 E0");command("V1 L4,1");if(!accepted||!solenoid4On)return 7;
 command("V1 L4,0");if(!accepted||solenoid4On||gpio)return 8;
 for(const char* bad:{"V1 L4,2","V1 L3,1","V1 L4,1junk"}){
  command(bad);if(accepted||solenoid4On||gpio)return 9;
 }
 return 0;
}
'''
        harness = '#include <initializer_list>\n' + harness
        compiler = '/Library/Developer/CommandLineTools/usr/bin/clang++'
        if not Path(compiler).exists():
            compiler = shutil.which('c++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        with tempfile.TemporaryDirectory() as directory:
            source, binary = Path(directory)/'relay.cpp', Path(directory)/'relay'
            source.write_text(harness)
            sdk = Path('/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk')
            flags = ['-isysroot', str(sdk)] if sdk.exists() else []
            result = subprocess.run([compiler, *flags, '-std=c++11', str(source), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True)
        setup = firmware.split('void setup() {', 1)[1]
        self.assertLess(setup.index('digitalWrite(PIN_SOLENOID4, SOLENOID_OFF)'),
                        setup.index('pinMode(PIN_SOLENOID4, OUTPUT)'))


if __name__ == '__main__':
    unittest.main()

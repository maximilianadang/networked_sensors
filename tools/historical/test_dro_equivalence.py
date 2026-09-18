"""Execute both sketches' actual DRO routines against identical bit streams.

This tests capture/decoding, not the board's electrical input or ISR scheduling.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which('g++'), 'native compiler required')
class DroEquivalenceTests(unittest.TestCase):
    def test_yun_and_controllino_capture_and_decode_match(self):
        yun = (ROOT / 'firmware/limit_switch_palas.ino').read_text()
        maxi = (ROOT / 'firmware/controllino_motion_control.ino').read_text()
        declarations = yun.split('const byte DRO_FRAME_BITS', 1)[1].split('// All four inputs', 1)[0]
        capture = yun.split('ISR(PCINT0_vect)', 1)[1].split('ISR(TIMER3_OVF_vect)', 1)[0]
        decode = yun.split('byte droNibble(', 1)[1].split('bool droIsFresh(', 1)[0]
        other = maxi.split('// ---------- 4b.', 1)[1]
        other = other[other.index('constexpr byte DRO_FRAME_BYTES'):].split('void writeStatus(', 1)[0]
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
using byte = uint8_t;
#define _BV(n) (1U << (n))
#define PB6 6
#define PB7 7
#define PH3 3
#define ATOMIC_RESTORESTATE 0
#define ATOMIC_BLOCK(x) for(bool once=true; once; once=false)
unsigned long now=0;
unsigned long millis() { return now; }
namespace yun {
byte PINB=0;
bool statusDirty=false;
''' + 'const byte DRO_FRAME_BITS' + declarations + 'void capture()' + capture + 'byte droNibble(' + decode + r'''
}
namespace maxi {
byte PINH=0;
''' + other + r'''
}
void edge(bool high) {
  yun::PINB = (high ? _BV(PB7) : 0) | _BV(PB6);
  yun::capture(); // rising clock is ignored by Yun's pin-change ISR
  yun::PINB &= ~_BV(PB6);
  maxi::PINH = high ? _BV(PH3) : 0;
  yun::capture(); maxi::captureDroClock();
}
void frame(long value, bool malformed=false) {
  byte digits[13] = {15,15,15,15,0,0,0,0,0,0,0,2,0};
  digits[4] = value < 0 ? 8 : 0;
  value = labs(value);
  for(int n=10;n>=5;--n) { digits[n]=value%10; value/=10; }
  if(malformed) digits[11]=3;
  for(byte digit: digits) for(int bit=0;bit<4;++bit) edge(digit & _BV(bit));
}
void compare() {
  yun::pollDroFrames(); maxi::pollDroFrames();
  assert(yun::droValidFrames == maxi::droValidFrames);
  assert(yun::droRejectedFrames == maxi::droRejectedFrames);
  assert(yun::droDroppedFrames == maxi::droDroppedFrames);
  assert(yun::droHasPosition == maxi::droHasPosition);
  assert(yun::droPositionHundredthsMm == maxi::droPositionHundredthsMm);
  assert(yun::droReferenceHundredthsMm == maxi::droReferenceHundredthsMm);
}
int main() {
  assert(!yun::droHasPosition && !maxi::droHasPosition);
  edge(false); edge(true); edge(false); // incomplete-header noise
  for(int i=0;i<1000;++i) {
    now += 100;
    long value = (i*997)%999999;
    if(i%2) value=-value;
    frame(value); compare();
    assert(maxi::droPositionHundredthsMm == value);
    frame(value, true); compare(); // both reject nonmetric/decimal mismatch
  }
  frame(10700); frame(10800); compare(); // pending-buffer overrun
  assert(yun::droDroppedFrames == 1);
  assert(maxi::droPositionHundredthsMm == 10700);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'dro'
            subprocess.run(['g++', '-std=c++11', '-x', 'c++', '-', '-o', str(binary)],
                           input=source, text=True, check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)

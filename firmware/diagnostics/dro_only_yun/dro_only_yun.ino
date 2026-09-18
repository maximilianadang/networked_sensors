// DRO-only extraction from limit_switch_palas.ino; no motion or Ethernet.
// Arduino Yun: blue CLOCK D10/PB6, purple DATA D11/PB7.
// USB Serial: 115200 baud. Disconnect actuator power before uploading.
// Original capture and decoder retained; standalone reporting, no saved zero.
#include <Arduino.h>
#include <util/atomic.h>
#if !defined(__AVR_ATmega32U4__)
#error "Select Arduino Yun (ATmega32U4)"
#endif
const int PIN_DRO_CLOCK = 10, PIN_DRO_DATA = 11;

const byte DRO_FRAME_BITS = 52, DRO_HEADER_BITS = 16, DRO_FRAME_BYTES = 7;
const unsigned long DRO_STALE_MS = 250UL;

volatile byte droCaptureFrame[DRO_FRAME_BYTES];
volatile byte droCompletedFrame[DRO_FRAME_BYTES];
volatile byte droCaptureBit = 0;
volatile byte droHeaderOnes = 0;
volatile bool droFrameReady = false;
volatile byte droDroppedFrames = 0;

bool droHasPosition = false;
long droPositionHundredthsMm = 0L;      
unsigned long droLastValidAtMs = 0UL;
unsigned long droValidFrames = 0UL;
byte droRejectedFrames = 0;

// 1. Capture: synchronize on 16 ones, then collect the remaining frame bits.
ISR(PCINT0_vect) {
  // PCINT0_vect fires on both D10 edges. AbsoluteDRO/Digimatic data is valid
  // on the falling clock edge, so rising edges return immediately. Direct
  // port access samples clock and data together.
  byte portB = PINB;
  if (portB & _BV(PB6)) return;
  bool dataHigh = (portB & _BV(PB7)) != 0;

  if (droCaptureBit == 0) {
    if (!dataHigh) {
      droHeaderOnes = 0;
      return;
    }
    if (droHeaderOnes < DRO_HEADER_BITS) ++droHeaderOnes;
    if (droHeaderOnes == DRO_HEADER_BITS) {
      // The first two bytes are the known 16-one header. Clear the remainder
      // before collecting bits 16..51 LSB-first.
      droCaptureFrame[0] = 0xFF;
      droCaptureFrame[1] = 0xFF;
      for (byte index = 2; index < DRO_FRAME_BYTES; ++index) {
        droCaptureFrame[index] = 0;
      }
      droCaptureBit = DRO_HEADER_BITS;
    }
    return;
  }

  if (dataHigh) {
    droCaptureFrame[droCaptureBit >> 3] |=
        _BV(droCaptureBit & 0x07);
  }
  ++droCaptureBit;
  if (droCaptureBit < DRO_FRAME_BITS) return;

  if (!droFrameReady) {
    for (byte index = 0; index < DRO_FRAME_BYTES; ++index) {
      droCompletedFrame[index] = droCaptureFrame[index];
    }
    droFrameReady = true;
  } else if (droDroppedFrames < 255) {
    ++droDroppedFrames;
  }
  droCaptureBit = 0;
  droHeaderOnes = 0;
}

// 2. Decode: validate the metric frame and extract signed hundredths of a mm.
byte droNibble(const byte frame[DRO_FRAME_BYTES], byte digitIndex) {
  byte packed = frame[digitIndex >> 1];
  if (digitIndex & 0x01) packed >>= 4;
  return packed & 0x0F;
}

bool decodeDroFrame(
    const byte frame[DRO_FRAME_BYTES],
    long *positionHundredthsMm) {
  // AbsoluteDRO Plus follows the 13-nibble Digimatic ordering, with every
  // nibble transmitted least-significant bit first:
  //   d1..d4=F, d5=sign, d6..d11=xxxx.xx, d12=2 decimals, d13=millimetres.
  for (byte digit = 0; digit < 4; ++digit) {
    if (droNibble(frame, digit) != 0x0F) return false;
  }
  byte sign = droNibble(frame, 4);
  if (sign != 0 && sign != 8) return false;

  long magnitudeHundredthsMm = 0L;
  for (byte digit = 5; digit <= 10; ++digit) {
    byte value = droNibble(frame, digit);
    if (value > 9) return false;
    magnitudeHundredthsMm = magnitudeHundredthsMm * 10L + value;
  }
  if (droNibble(frame, 11) != 2) return false;
  if (droNibble(frame, 12) != 0) return false;

  *positionHundredthsMm =
      sign == 8 ? -magnitudeHundredthsMm : magnitudeHundredthsMm;
  return true;
}

// 3. Consume the interrupt mailbox; never decode inside the ISR.
void pollDroFrames() {
  byte frame[DRO_FRAME_BYTES];
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (!droFrameReady) return;
    for (byte index = 0; index < DRO_FRAME_BYTES; ++index) {
      frame[index] = droCompletedFrame[index];
    }
    droFrameReady = false;
  }

  long decodedHundredthsMm = 0L;
  if (!decodeDroFrame(frame, &decodedHundredthsMm)) {
    if (droRejectedFrames < 255) ++droRejectedFrames;
    return;
  }

  unsigned long nowMs = millis();
  droHasPosition = true;
  droPositionHundredthsMm = decodedHundredthsMm;
  droLastValidAtMs = nowMs;
  if (droValidFrames < 0xFFFFFFFFUL) ++droValidFrames;
}

long droSampleAgeMs(unsigned long nowMs) {
  if (!droHasPosition) return -1L;
  unsigned long ageMs = nowMs - droLastValidAtMs;
  return ageMs > 0x7FFFFFFFUL ? 0x7FFFFFFFL : (long)ageMs;
}

// 4. Board-specific input setup and USB serial reporting (every 200 ms).
void setup() {
  Serial.begin(115200);
  pinMode(PIN_DRO_CLOCK, INPUT);
  pinMode(PIN_DRO_DATA, INPUT);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    PCMSK0 |= _BV(PCINT6);
    PCIFR = _BV(PCIF0);
    PCICR |= _BV(PCIE0);
  }
}

void reportDro() {
  long age = droSampleAgeMs(millis());
  Serial.print(F("{\"board\":\"yun\",\"clock\":"));
  Serial.print(digitalRead(PIN_DRO_CLOCK));
  Serial.print(F(",\"data\":")); Serial.print(digitalRead(PIN_DRO_DATA));
  Serial.print(F(",\"fresh\":")); Serial.print(age >= 0 && age <= long(DRO_STALE_MS));
  Serial.print(F(",\"raw_mm\":"));
  if (droHasPosition) Serial.print(droPositionHundredthsMm / 100.0, 2);
  else Serial.print(F("null"));
  Serial.print(F(",\"age_ms\":")); Serial.print(age);
  Serial.print(F(",\"valid\":")); Serial.print(droValidFrames);
  Serial.print(F(",\"rejected\":")); Serial.print(droRejectedFrames);
  Serial.print(F(",\"dropped\":")); Serial.print(droDroppedFrames);
  Serial.println('}');
}

void loop() {
  pollDroFrames();
  static unsigned long lastReport = 0;
  if (millis() - lastReport >= 200) {
    lastReport = millis();
    reportDro();
  }
}

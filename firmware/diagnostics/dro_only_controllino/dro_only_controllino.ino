// DRO-only extraction from controllino_motion_control.ino; no motion or Ethernet.
// MAXI Automation: blue CLOCK X1 DO0 / D2 / PE4;
// purple DATA X1 DO4 / D6 / PH3. These are 5V X1 pins, not 24V terminals.
// USB Serial: 115200 baud. Disconnect actuator power before uploading.
// Original capture, decoder and capture-time freshness retained; no saved zero.
#include <Arduino.h>
#include <util/atomic.h>
#if !defined(__AVR_ATmega2560__)
#error "Select CONTROLLINO MAXI Automation (ATmega2560)"
#endif
constexpr byte PIN_DRO_CLOCK = 2, PIN_DRO_DATA = 6;

constexpr byte DRO_FRAME_BYTES = 7, DRO_FRAME_BITS = 52, DRO_HEADER_BITS = 16;
constexpr unsigned long DRO_STALE_MS = 250;
volatile byte droCaptureFrame[DRO_FRAME_BYTES], droCompletedFrame[DRO_FRAME_BYTES];
volatile byte droCaptureBit = 0, droHeaderOnes = 0, droDroppedFrames = 0;
volatile bool droFrameReady = false;
volatile unsigned long droCompletedAtMs = 0;
volatile unsigned long droEdges = 0;  // wraps; diagnostic only, not a sample count
bool droHasPosition = false;
long droPositionHundredthsMm = 0;
unsigned long droLastValidAtMs = 0, droValidFrames = 0;
byte droRejectedFrames = 0;

// 1. Capture: synchronize on 16 ones, then collect the remaining frame bits.
void captureDroClock() {
  // D6 is PH3 on the MAXI Automation. Sample directly on D2's falling edge;
  // no parsing or printing inside this interrupt.
  bool high = (PINH & _BV(PH3)) != 0;
  ++droEdges;
  if (!droCaptureBit) {
    if (!high) { droHeaderOnes = 0; return; }
    if (++droHeaderOnes == DRO_HEADER_BITS) {
      droCaptureFrame[0] = droCaptureFrame[1] = 0xFF;
      for (byte i = 2; i < DRO_FRAME_BYTES; ++i) droCaptureFrame[i] = 0;
      droCaptureBit = DRO_HEADER_BITS;
    }
    return;
  }
  if (high) droCaptureFrame[droCaptureBit >> 3] |= _BV(droCaptureBit & 7);
  if (++droCaptureBit < DRO_FRAME_BITS) return;
  if (!droFrameReady) {
    for (byte i = 0; i < DRO_FRAME_BYTES; ++i) droCompletedFrame[i] = droCaptureFrame[i];
    droCompletedAtMs = millis();
    droFrameReady = true;
  } else if (droDroppedFrames < 255) ++droDroppedFrames;
  droCaptureBit = droHeaderOnes = 0;
}

// 2. Decode: validate the metric frame and extract signed hundredths of a mm.
byte droNibble(const byte *frame, byte digit) {
  return (frame[digit >> 1] >> ((digit & 1) * 4)) & 15;
}

bool decodeDroFrame(const byte *frame, long *positionMm100) {
  // Four F nibbles, sign (0/8), six BCD digits, two decimals, metric units.
  for (byte i = 0; i < 4; ++i) if (droNibble(frame, i) != 15) return false;
  byte sign = droNibble(frame, 4);
  if ((sign != 0 && sign != 8) || droNibble(frame, 11) != 2 ||
      droNibble(frame, 12) != 0) return false;
  long value = 0;
  for (byte i = 5; i <= 10; ++i) {
    byte digit = droNibble(frame, i);
    if (digit > 9) return false;
    value = value * 10 + digit;
  }
  *positionMm100 = sign == 8 ? -value : value;
  return true;
}

// 3. Consume the interrupt mailbox; never decode inside the ISR.
void pollDroFrames() {
  byte frame[DRO_FRAME_BYTES]; unsigned long capturedAt;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (!droFrameReady) return;
    for (byte i = 0; i < DRO_FRAME_BYTES; ++i) frame[i] = droCompletedFrame[i];
    capturedAt = droCompletedAtMs;
    droFrameReady = false;
  }
  long value;
  if (!decodeDroFrame(frame, &value)) {
    if (droRejectedFrames < 255) ++droRejectedFrames;
    return;
  }
  droHasPosition = true;
  droPositionHundredthsMm = value;
  droLastValidAtMs = capturedAt;  // delayed processing must not make old data fresh
  if (droValidFrames < 0xFFFFFFFFUL) ++droValidFrames;
}

long droSampleAgeMs() {
  if (!droHasPosition) return -1;
  unsigned long age = millis() - droLastValidAtMs;
  return age > 0x7FFFFFFFUL ? 0x7FFFFFFFL : long(age);
}

unsigned long droClockEdges() {
  unsigned long edges;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { edges = droEdges; }
  return edges;
}

// 4. Board-specific input setup and USB serial reporting (every 200 ms).
void setup() {
  Serial.begin(115200);
  pinMode(PIN_DRO_CLOCK, INPUT);
  pinMode(PIN_DRO_DATA, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_DRO_CLOCK), captureDroClock, FALLING);
}

void reportDro() {
  long age = droSampleAgeMs();
  Serial.print(F("{\"board\":\"controllino\",\"clock\":"));
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
  Serial.print(F(",\"edges\":")); Serial.print(droClockEdges());
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

#pragma once
#include <Arduino.h>
#include <util/atomic.h>
#include "absolute_dro_protocol.h"

// AVR adapter: interrupt captures bits; loop validates frames and owns telemetry.
// A one-frame mailbox keeps the newest reading if loop/network service falls behind.
class AbsoluteDroReader {
 public:
  void begin(byte clockPin, byte dataPin, void (*onClock)()) {
    // The external level shifter supplies pull-ups. Never drive either DRO line.
    pinMode(clockPin, INPUT);
    pinMode(dataPin, INPUT);
    dataPort = portInputRegister(digitalPinToPort(dataPin));
    dataMask = digitalPinToBitMask(dataPin);
    attachInterrupt(digitalPinToInterrupt(clockPin), onClock, FALLING);
  }

  void onClock() {
    if (!capture.feed((*dataPort & dataMask) != 0)) return;
    if (ready && dropped < 255) ++dropped;
    for (byte i = 0; i < DRO_FRAME_BYTES; ++i) pending[i] = capture.frame[i];
    capturedAtMs = millis();  // Acquisition time, not the later poll time.
    ready = true;
  }

  void poll() {
    byte frame[DRO_FRAME_BYTES];
    unsigned long sampleMs;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (!ready) return;
      for (byte i = 0; i < DRO_FRAME_BYTES; ++i) frame[i] = pending[i];
      sampleMs = capturedAtMs;
      ready = false;
    }
    long decoded;
    if (!decodeDroFrame(frame, &decoded)) {
      if (rejected < 255) ++rejected;
      return;
    }
    if (!validFrames) reference = decoded;
    position = decoded;
    lastValidMs = sampleMs;
    if (validFrames < 0xffffffffUL) ++validFrames;
  }

  // Existing V1 contract: capability, freshness, position/displacement in 0.01 mm,
  // sample age, valid count, then packed rejected/dropped counts. Zero is host-owned.
  void writeStatus(Print &out, unsigned long nowMs) const {
    unsigned long age = nowMs - lastValidMs;
    out.print(F(",\"dc\":1,\"df\":")); out.print(validFrames && age <= DRO_STALE_MS ? 1 : 0);
    out.print(F(",\"dr\":")); out.print(position);
    out.print(F(",\"dd\":")); out.print(position - reference);
    out.print(F(",\"da\":")); out.print(!validFrames ? -1L : long(min(age, 0x7fffffffUL)));
    out.print(F(",\"dq\":")); out.print(validFrames);
    out.print(F(",\"dx\":")); out.print((static_cast<unsigned int>(rejected) << 8) | dropped);
  }

 private:
  DroFrameCapture capture;
  volatile uint8_t *dataPort = nullptr;
  uint8_t dataMask = 0;
  volatile byte pending[DRO_FRAME_BYTES] = {};
  volatile bool ready = false;
  volatile byte dropped = 0;
  volatile unsigned long capturedAtMs = 0;
  byte rejected = 0;
  long position = 0, reference = 0;
  unsigned long lastValidMs = 0, validFrames = 0;
};

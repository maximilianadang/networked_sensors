#pragma once
#include <Arduino.h>
#include <util/atomic.h>
#include "linace_protocol.h"

// Timer5 is exclusive to LinACE. Timers1/3 remain available to existing motion.
// No blocking pulseIn, polling loop, SPI or serial output occurs in this ISR.
class LinaceReader {
 public:
  void begin(byte pwmPin, byte statusPin, void (*onEdge)()) {
    static_assert(F_CPU == 16000000UL, "LinACE timer requires 16 MHz AVR");
    pinMode(pwmPin, INPUT); pinMode(statusPin, INPUT);
    pwmPort = portInputRegister(digitalPinToPort(pwmPin));
    statusPort = portInputRegister(digitalPinToPort(statusPin));
    pwmMask = digitalPinToBitMask(pwmPin); statusMask = digitalPinToBitMask(statusPin);
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      TCCR5A = TCCR5B = 0; TCNT5 = 0; overflows = 0;
      TIFR5 = _BV(TOV5); TIMSK5 = _BV(TOIE5); TCCR5B = _BV(CS50);
    }
    attachInterrupt(digitalPinToInterrupt(pwmPin), onEdge, CHANGE);
  }

  void onOverflow() { ++overflows; }
  void onClock() {
    // Called with interrupts disabled. Account for an overflow whose ISR has
    // not run yet; the counter is sampled before checking the pending flag.
    uint16_t high = overflows, low = TCNT5;
    if ((TIFR5 & _BV(TOV5)) && low < 0x8000) ++high;
    const uint32_t ticks = (uint32_t(high) << 16) | low;
    Linace::Sample sample;
    if (!capture.edge((*pwmPort & pwmMask) != 0, (*statusPort & statusMask) != 0,
                      ticks, millis(), sample)) return;
    if (ready && dropped < 255) ++dropped;
    pendingHigh = sample.highTicks; pendingPeriod = sample.periodTicks;
    pendingMs = sample.sampledMs; pendingStatus = sample.statusOk; ready = true;
  }

  void poll() {
    Linace::Sample sample;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (!(*statusPort & statusMask)) {
        // Status LOW removes freshness immediately, including when PWM stops.
        ready = false; capture.reset(); healthy = false; return;
      }
      if (!ready) return;
      sample.highTicks = pendingHigh; sample.periodTicks = pendingPeriod;
      sample.sampledMs = pendingMs; sample.statusOk = pendingStatus; ready = false;
    }
    int32_t decoded;
    if (!Linace::decode(sample, decoded)) {
      if (rejected < 255) ++rejected;
      healthy = false; return;
    }
    if (!validFrames) reference = decoded;
    position = decoded; lastValidMs = sample.sampledMs; healthy = true;
    if (validFrames < 0xffffffffUL) ++validFrames;
  }

  void writeStatus(Print &out, unsigned long nowMs) const {
    // ServicedPrint can call poll() between bytes. Freeze the entire reading.
    const uint32_t count = validFrames, age = uint32_t(nowMs - lastValidMs);
    const int32_t pos = position, displacement = position - reference;
    const bool fresh = healthy && count && age <= Linace::STALE_MS;
    const uint16_t diagnostics = (uint16_t(rejected) << 8) | dropped;
    out.print(F(",\"dc\":1,\"df\":")); out.print(fresh ? 1 : 0);
    out.print(F(",\"dr\":")); out.print(pos);
    out.print(F(",\"dd\":")); out.print(displacement);
    out.print(F(",\"da\":")); out.print(!count ? -1L : long(min(age, 0x7fffffffUL)));
    out.print(F(",\"dq\":")); out.print(count);
    out.print(F(",\"dx\":")); out.print(diagnostics);
  }

 private:
  Linace::Capture capture;
  volatile uint8_t *pwmPort = nullptr, *statusPort = nullptr;
  uint8_t pwmMask = 0, statusMask = 0;
  volatile uint16_t overflows = 0;
  volatile uint32_t pendingHigh = 0, pendingPeriod = 0, pendingMs = 0;
  volatile bool pendingStatus = false, ready = false;
  volatile uint8_t dropped = 0;
  uint8_t rejected = 0;
  bool healthy = false;
  int32_t position = 0, reference = 0;
  uint32_t lastValidMs = 0, validFrames = 0;
};

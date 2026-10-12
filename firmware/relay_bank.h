#pragma once
#include <Arduino.h>

// Serial IDs4/5/6 operate physical R5/R3/R4; dashboard numbering is independent.
// The same table drives initialization, writes and telemetry.
class RelayBank {
 public:
  static constexpr byte FIRST_ID = 4, COUNT = 3;
  void begin(const byte (&pins)[COUNT], byte onLevel, byte offLevel) {
    channels = pins; high = onLevel; low = offLevel;
    for (byte i = 0; i < COUNT; ++i) {
      digitalWrite(channels[i], low); pinMode(channels[i], OUTPUT);
    }
    states = 0;
  }
  static bool validId(long id) { return id >= FIRST_ID && id < FIRST_ID + COUNT; }
  bool set(long id, bool on) {
    if (!validId(id)) return false;
    byte index = byte(id - FIRST_ID), mask = byte(1U << index);
    states = on ? states | mask : states & ~mask;
    digitalWrite(channels[index], on ? high : low); return true;
  }
  void allOff() { for (byte i = 0; i < COUNT; ++i) set(FIRST_ID + i, false); }
  void writeStatus(Print &out) const {
    const byte snapshot = states;
    for (byte i = 0; i < COUNT; ++i) {
      out.print(F(",\"sol")); out.print(FIRST_ID + i); out.print(F("\":"));
      out.print((snapshot >> i) & 1);
    }
  }

 private:
  const byte *channels = nullptr;
  byte states = 0, high = HIGH, low = LOW;
};

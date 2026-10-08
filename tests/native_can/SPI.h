#pragma once
#include "Arduino.h"
#include <assert.h>
#include <array>

#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings {
  uint32_t clock;
  SPISettings(uint32_t value, uint8_t, uint8_t) : clock(value) {}
};

// Small register fixture for the actual pinned MCP2515 driver. CANSTAT exposes
// REQOP bits 7:5, never CANCTRL.OSM bit 3 (MCP2515 data sheet, registers 10-1/10-2).
// This is deliberately below the library API, where the one-shot bug occurred.
class SPIClass {
 public:
  std::array<uint8_t, 128> reg{};
  std::array<unsigned, 128> reads{};
  std::array<unsigned, 128> slowReads{};
  uint8_t fastCorruptAddress = 0xff;
  bool missing = false, ignoreOsm = false, refuseNormal = false, holdAbort = false;
  bool transaction = false, selected = false;
  unsigned long clockUs = 0;
  unsigned configRequests = 0, failConfigRequest = 0, requests = 0, writes = 0;

  void begin() {}
  void beginTransaction(SPISettings settings) {
    assert(!transaction); transaction = true; clockHz_ = settings.clock;
  }
  void endTransaction() { assert(transaction && !selected); transaction = false; }
  void select(bool active) {
    selected = active;
    if (active) { assert(transaction); phase_ = 0; }
  }
  uint8_t transfer(uint8_t value) {
    assert(transaction && selected);
    clockUs += 10;
    if (missing) return 0xff;
    if (phase_++ == 0) {
      instruction_ = value;
      if (value == 0xc0) { reg.fill(0); reg[0x0e] = 0x80; reg[0x0f] = 0x87; }
      return 0;
    }
    if (instruction_ == 0xa0) {  // READ STATUS: RX flags and TXREQ bits.
      uint8_t result = reg[0x2c] & 3;
      for (unsigned i = 0; i < 3; ++i)
        if (reg[0x30 + 16 * i] & 8) result |= 4 << (2 * i);
      return result;
    }
    if (phase_ == 2) { address_ = value; return 0; }
    assert(address_ < reg.size());
    if (instruction_ == 3) {
      ++reads[address_];
      if (clockHz_ == 1000000) ++slowReads[address_];
      // CANSTAT/CANCTRL are mirrored at xE/xF throughout the register map.
      const uint8_t source = (address_ & 0x0f) >= 0x0e ? address_ & 0x0f : address_;
      const uint8_t value = reg[source] ^
          (clockHz_ == 8000000 && address_ == fastCorruptAddress ? 1 : 0);
      ++address_;
      return value;
    }
    if (instruction_ == 2) { put(address_++, value); return 0; }
    assert(instruction_ == 5);  // BIT MODIFY.
    if (phase_ == 3) mask_ = value;
    else put(address_, (reg[address_] & ~mask_) | (value & mask_));
    return 0;
  }

 private:
  uint32_t clockHz_ = 0;
  uint8_t instruction_ = 0, address_ = 0, mask_ = 0, phase_ = 0;
  void put(uint8_t address, uint8_t value) {
    ++writes;
    reg[address] = value;
    if (address == 0x0f) {
      if ((value & 0xe0) == 0x80) ++configRequests;
      const bool reject = (refuseNormal && !(value & 0xe0)) ||
                          (failConfigRequest && configRequests == failConfigRequest);
      reg[0x0e] = reject ? uint8_t((value & 0xe0) ^ 0x80) : value & 0xe0;
      if (ignoreOsm) reg[address] &= ~8;
      if ((value & 0x10) && !holdAbort)
        for (unsigned i = 0; i < 3; ++i) reg[0x30 + 16 * i] &= ~8;
    }
    if ((address == 0x30 || address == 0x40 || address == 0x50) && (value & 8))
      ++requests;
  }
};
extern SPIClass SPI;

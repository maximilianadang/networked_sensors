#pragma once
#include <vector>
#include <utility>
#define SPI_ETHERNET_SETTINGS 0
namespace SnIR { constexpr uint8_t SEND_OK = 16, TIMEOUT = 8; }
constexpr uint8_t Sock_SEND = 32, Sock_CLOSE = 16;
struct FakeSPI {
  unsigned begins = 0, ends = 0;
  void beginTransaction(int) { ++begins; }
  void endTransaction() { ++ends; }
};
extern FakeSPI SPI;
struct FakeW5100 {
  static constexpr uint16_t SSIZE = 2048, SMASK = 2047;
  uint8_t irq = 0;
  uint16_t pointer = 0;
  unsigned sends = 0, closes = 0;
  std::vector<std::pair<uint16_t, std::vector<uint8_t>>> writes;
  uint8_t readSnIR(uint8_t) { return irq; }
  void writeSnIR(uint8_t, uint8_t flags) { irq &= ~flags; }
  uint16_t readSnTX_WR(uint8_t) { return pointer; }
  void writeSnTX_WR(uint8_t, uint16_t value) { pointer = value; }
  static uint16_t SBASE(uint8_t socket) { return 0x4000 + socket*2048; }
  void write(uint16_t address, const uint8_t *data, uint16_t size) {
    writes.push_back({address, std::vector<uint8_t>(data, data+size)});
  }
  void execCmdSn(uint8_t, uint8_t command) {
    if (command == Sock_SEND) ++sends;
    else if (command == Sock_CLOSE) ++closes;
  }
};
extern FakeW5100 W5100;

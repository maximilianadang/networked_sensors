#pragma once
#include <algorithm>
#include <cassert>
#include <stdint.h>
#include <string>
using byte = uint8_t;
using std::min;
#define F(text) text
constexpr int INPUT = 0, FALLING = 2;
extern unsigned long testNow;
extern volatile uint8_t testPort;
extern void (*testClock)();
inline unsigned long millis() { return testNow; }
inline void pinMode(byte pin, int mode) {
  assert((pin == 2 || pin == 6) && mode == INPUT);
}
inline byte digitalPinToPort(byte pin) { assert(pin == 6); return 0; }
inline byte digitalPinToBitMask(byte pin) { assert(pin == 6); return 1 << 3; }
inline volatile uint8_t *portInputRegister(byte) { return &testPort; }
inline int digitalPinToInterrupt(byte pin) { assert(pin == 2); return 0; }
inline void attachInterrupt(int interrupt, void (*handler)(), int edge) {
  assert(interrupt == 0 && edge == FALLING);
  testClock = handler;
}
class Print {
 public:
  std::string text;
  void print(const char *value) { text += value; }
  template <typename T> void print(T value) { text += std::to_string(value); }
};

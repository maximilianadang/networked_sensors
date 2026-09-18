#pragma once
// Native behavioral harness only: no real ports, clocks, timers or devices.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
using byte = uint8_t;
struct __FlashStringHelper {};
#define F(s) reinterpret_cast<const __FlashStringHelper *>(s)
#define F_CPU 16000000UL
#define ISR(name) void name()
#define _BV(n) (1U << (n))
enum {LOW=0, HIGH=1, INPUT=0, OUTPUT=1, INPUT_PULLUP=2};
enum {PB6=6, PB7=7, PCINT6=6, PCIF0=0, PCIE0=0,
      WGM12=3, WGM31=1, WGM32=3, WGM33=4, CS11=1, CS10=0, CS31=1,
      OCIE1A=1, OCF1A=1, TOIE3=0, OCIE3A=1, TOV3=0, OCF3A=1};
static uint8_t PINB, PCMSK0, PCIFR, PCICR, TCCR1A, TCCR1B, TIMSK1,
               TIFR1, TCCR3A, TCCR3B, TIMSK3, TIFR3;
static uint16_t OCR1A, TCNT1, TCNT3, ICR3, OCR3A;
static unsigned long nowUs;
static int pins[32];
inline unsigned long micros() { return nowUs; }
inline unsigned long millis() { return nowUs / 1000; }
inline int digitalRead(int pin) { return pins[pin]; }
inline void digitalWrite(int pin, int level) { pins[pin] = level; }
inline void pinMode(int, int) {}
inline void delayMicroseconds(unsigned int) {}
class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(const uint8_t *, size_t) = 0;
};
class Stream : public Print {
 public:
  std::string input, output;
  int capacity = 64;
  bool connected = true;
  unsigned int flushes = 0;
  void begin(long) {}
  int available() { return input.size(); }
  int read() { int c = input.front(); input.erase(0, 1); return c; }
  int availableForWrite() { return capacity; }
  explicit operator bool() const { return connected; }
  size_t write(const uint8_t *data, size_t size) override {
    size_t count = std::min(size, size_t(std::max(capacity, 0)));
    output.append(reinterpret_cast<const char *>(data), count);
    return count;
  }
  void flush() { ++flushes; }
  void print(const __FlashStringHelper *s) { output += reinterpret_cast<const char *>(s); }
  void print(const char *s) { output += s; }
  template<class T> void print(T v) { output += std::to_string(v); }
  template<class T> void println(T v) { print(v); output += '\n'; }
  void println() { output += '\n'; }
};
static Stream Serial, Serial1;

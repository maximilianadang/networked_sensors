#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <sstream>
#include <string>
#include <functional>

#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define F(value) value
unsigned long millis();
unsigned long micros();
void delay(unsigned long ms);
void pinMode(uint8_t, uint8_t);
void digitalWrite(uint8_t, uint8_t);

// Only the Print surface used by CubeMarsCan::writeStatus is needed here.
class Print {
 public:
  std::string text;
  std::function<void()> beforePrint;
  template <typename T> void print(T value) {
    if (beforePrint) beforePrint();
    std::ostringstream stream; stream << value; text += stream.str();
  }
  void print(uint8_t value) { print(unsigned(value)); }
  void print(int8_t value) { print(int(value)); }
};

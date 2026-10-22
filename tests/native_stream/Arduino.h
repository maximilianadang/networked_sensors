#pragma once
#include <stdint.h>
#include <stddef.h>
#include <algorithm>
#include <sstream>
using std::min;
class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t value) = 0;
  size_t write(const uint8_t *data, size_t size) {
    size_t sent = 0;
    while (sent < size && write(data[sent])) ++sent;
    return sent;
  }
  size_t print(const char *text) {
    size_t sent = 0;
    while (*text && write(uint8_t(*text++))) ++sent;
    return sent;
  }
  template <class T> size_t print(T value) {
    std::ostringstream out; out << value; return print(out.str().c_str());
  }
  size_t println(const char *text) { return print(text) + print("\r\n"); }
};

#pragma once
#include <stddef.h>

// C++11 constexpr: checked at build time, with no runtime registry or allocation.
template <size_t N>
constexpr bool distinctPins(const int (&pins)[N], size_t i = 0, size_t j = 1) {
  return i >= N ? true :
      pins[i] < 0 ? false :
      j >= N ? distinctPins(pins, i + 1, i + 2) :
      pins[i] != pins[j] && distinctPins(pins, i, j + 1);
}

// Reserved board aliases may refer to the same pin (e.g. SS and Ethernet CS).
template <size_t N, size_t M>
constexpr bool avoidsPins(const int (&pins)[N], const int (&reserved)[M],
                          size_t i = 0, size_t j = 0) {
  return i >= N ? true :
      j >= M ? avoidsPins(pins, reserved, i + 1, 0) :
      pins[i] != reserved[j] && avoidsPins(pins, reserved, i, j + 1);
}

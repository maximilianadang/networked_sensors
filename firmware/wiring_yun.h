#pragma once
#include <Arduino.h>
#include "wiring_checks.h"

// Arduino Yún pin numbers. Electrical/physical direction calibration remains
// fixed in the sketch; this map changes terminal assignment only.
const int PIN_STEP = 3;
const int PIN_DRIVER_DIR = 2;
const int PIN_RUN = 4;
const int PIN_DIR = 5;
const int PIN_LIMIT_POS = 6;
const int PIN_LIMIT_NEG = 8;  // D7 is reserved by the Yún Linux handshake.
const int PIN_DRIVER_ENABLE_NEG = 9;
const int PIN_DRO_CLOCK = 10;
const int PIN_DRO_DATA = 11;
const int PIN_ESC_SIGNAL = 12;

// The DRO ISR directly reads PINB bits 6/7 and enables PCINT6. Moving these
// requires a new capture implementation, not just different pin constants.
static_assert(PIN_DRO_CLOCK == 10 && PIN_DRO_DATA == 11,
              "Yun DRO capture requires D10/PB6 and D11/PB7");
constexpr int YUN_PINS[] = {PIN_STEP, PIN_DRIVER_DIR, PIN_RUN, PIN_DIR,
                          PIN_LIMIT_POS, PIN_LIMIT_NEG, PIN_DRIVER_ENABLE_NEG,
                          PIN_DRO_CLOCK, PIN_DRO_DATA, PIN_ESC_SIGNAL,
                          0, 1, 7};  // Linux Serial1 and handshake reserved
static_assert(distinctPins(YUN_PINS), "Yun pin collision (including Linux interface)");

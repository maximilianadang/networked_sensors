#pragma once
#include <Arduino.h>
#include "wiring_checks.h"

// MAXI Automation / SRX02-S common-anode wiring. Arduino pin numbers, not
// industrial terminal numbers. Used by both motion and bring-up sketches.
constexpr byte PIN_STEP = 3;    // X1 DO1 -> STEP-
constexpr byte PIN_DIR = 5;     // X1 DO3 -> DIR-
constexpr byte PIN_ENABLE = 7;  // X1 DO5 -> EN-
constexpr byte PIN_ESC = 12;    // ESC receiver signal; confirm physical landing
// DRO via the existing level shifter, on X1 logic-level signals:
constexpr byte PIN_DRO_CLOCK = 21; // X1 SCL / chip pin 43 / PD0 (reserved for DRO, not I2C)
constexpr byte PIN_DRO_DATA = 6;   // Digital 4 / chip pin 15 / PH3
static_assert(digitalPinToInterrupt(PIN_DRO_CLOCK) != NOT_AN_INTERRUPT,
              "DRO clock must support an external interrupt");
// X1 5 V -> STEP+, DIR+, EN+. EN optocoupler active means driver disabled.
constexpr byte STEP_IDLE = HIGH, STEP_ACTIVE = LOW;
constexpr byte DRIVER_ENABLED = HIGH, DRIVER_DISABLED = LOW;
constexpr byte DIR_FORWARD = HIGH, DIR_REVERSE = LOW;  // verified 2026-09-03

// Board/core owns W5100 CS and SPI configuration.
constexpr int CONTROLLINO_PINS[] = {PIN_STEP, PIN_DIR, PIN_ENABLE, PIN_ESC,
                                    PIN_DRO_CLOCK, PIN_DRO_DATA};
constexpr int ETHERNET_PINS[] = {PIN_SPI_SS_ETHERNET_LIB, MISO, MOSI, SCK, SS};
static_assert(distinctPins(CONTROLLINO_PINS) &&
              avoidsPins(CONTROLLINO_PINS, ETHERNET_PINS),
              "Controllino pin collision (including Ethernet/SPI)");

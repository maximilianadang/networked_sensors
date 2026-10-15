#pragma once
#include <Arduino.h>
#include "wiring_checks.h"
#include "controllino_firmware.h"

// MAXI Automation / SRX02-S common-anode wiring. Arduino pin numbers, not
// industrial terminal numbers. Used by both motion and bring-up sketches.
constexpr byte PIN_STEP = 3;    // X1 DO1 -> STEP-
constexpr byte PIN_DIR = 5;     // X1 DO3 -> DIR-
constexpr byte PIN_ENABLE = 7;  // X1 DO5 -> EN-
constexpr byte PIN_SOLENOID4 = 27; // terminal R5 (official CONTROLLINO_R5 mapping)
constexpr byte PIN_SOLENOID5 = 25, PIN_SOLENOID6 = 26; // terminals R3, R4
constexpr byte SOLENOID_PINS[] = {PIN_SOLENOID4, PIN_SOLENOID5, PIN_SOLENOID6};
constexpr byte SOLENOID_ON = HIGH, SOLENOID_OFF = LOW;
constexpr byte PIN_ESC = 12;    // ESC receiver signal; confirm physical landing
constexpr byte PIN_SERVO = 4;  // X1 Digital 2 / DO2, 5 V logic-level signal
#ifndef CONTROLLINO_CAN_CS
#define CONTROLLINO_CAN_CS 9
#endif
// CS is an Arduino logic-level pin, NOT a 24 V industrial output. Confirm landing.
constexpr byte PIN_CAN_CS = CONTROLLINO_CAN_CS;
static_assert(CONTROLLINO_CAN_CS >= 0 && CONTROLLINO_CAN_CS < NUM_DIGITAL_PINS,
              "Invalid Arduino CAN CS pin");
constexpr byte PIN_RTC_CS = 72; // MAXI Automation RTC select is active HIGH.
constexpr byte PIN_AUX = AUX_IS_CUBEMARS ? PIN_CAN_CS : AUX_IS_SERVO ? PIN_SERVO : PIN_ESC;
// LinACE PW: direct PWM/Status inputs, no pull-ups. Legacy reader is selectable.
constexpr byte PIN_DRO_CLOCK = 21; // X1 SCL / chip pin 43 / PD0 (reserved for DRO, not I2C)
constexpr byte PIN_DRO_DATA = 6;   // Digital 4 / chip pin 15 / PH3
constexpr byte PIN_DRO_STATUS = 42; // X2 Digital 12 / PL7 (Arduino 42, not 12)
constexpr byte PIN_DRO_SECONDARY = DRO_IS_LINACE ? PIN_DRO_STATUS : PIN_DRO_DATA;
// P1..P7: screw AI10, AI11, AI12, AI13, AI0, AI1, AI2. AI12/13 are A14/15.
constexpr byte PRESSURE_PINS[] = {A10, A11, A14, A15, A0, A1, A2};
// Operator convention: Forward/up is positive; Reverse/down is negative.
constexpr byte PIN_LIMIT_NEG = A12; // DI0: bottom / Reverse / negative pulses
constexpr byte PIN_LIMIT_POS = A13; // DI1: top / Forward / positive pulses
static_assert(digitalPinToInterrupt(PIN_DRO_CLOCK) != NOT_AN_INTERRUPT,
              "DRO clock must support an external interrupt");
// X1 5 V -> STEP+, DIR+, EN+. EN optocoupler active means driver disabled.
constexpr byte STEP_IDLE = HIGH, STEP_ACTIVE = LOW;
constexpr byte DRIVER_ENABLED = HIGH, DRIVER_DISABLED = LOW;
constexpr byte DIR_POSITIVE = HIGH, DIR_NEGATIVE = LOW;  // electrical mapping, verified 2026-09-03

// Board/core owns W5100 CS and SPI configuration.
constexpr int CONTROLLINO_PINS[] = {PIN_STEP, PIN_DIR, PIN_ENABLE, PIN_AUX,
    PIN_DRO_CLOCK, PIN_DRO_SECONDARY, PIN_SOLENOID4, PIN_SOLENOID5, PIN_SOLENOID6,
    A10, A11, A14, A15, A0, A1, A2, PIN_LIMIT_POS, PIN_LIMIT_NEG};
constexpr int ETHERNET_PINS[] = {PIN_SPI_SS_ETHERNET_LIB, MISO, MOSI, SCK, SS, PIN_RTC_CS};
static_assert(distinctPins(CONTROLLINO_PINS) &&
              avoidsPins(CONTROLLINO_PINS, ETHERNET_PINS),
              "Controllino pin collision (including Ethernet/SPI)");

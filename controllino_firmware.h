#pragma once

// Single source, explicit build capability. Override with -DCONTROLLINO_AUX_SERVO=0
// for the brushless ESC build; the dashboard follows telemetry, not filenames.
constexpr char FIRMWARE_VERSION[] = "1.1.0";
#ifndef CONTROLLINO_AUX_SERVO
#define CONTROLLINO_AUX_SERVO 1
#endif
static_assert(CONTROLLINO_AUX_SERVO == 0 || CONTROLLINO_AUX_SERVO == 1,
              "Select ESC (0) or position servo (1)");
constexpr bool AUX_IS_SERVO = CONTROLLINO_AUX_SERVO;
// Pulse-position calibration; no angular travel is assumed without a servo model.
constexpr unsigned int SERVO_MIN_US = 1000, SERVO_MAX_US = 2000;
constexpr unsigned int SERVO_DEFAULT_US = 1500;
static_assert(SERVO_MIN_US > 0 && SERVO_MIN_US < SERVO_MAX_US &&
              SERVO_MAX_US < 20000 && SERVO_DEFAULT_US >= SERVO_MIN_US &&
              SERVO_DEFAULT_US <= SERVO_MAX_US, "Invalid servo pulse calibration");

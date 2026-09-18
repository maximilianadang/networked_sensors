#pragma once

// Single source, explicit build capability. Override with -DCONTROLLINO_AUX_SERVO=0
// for the brushless ESC build; the dashboard follows telemetry, not filenames.
constexpr char FIRMWARE_VERSION[] = "1.2.0";
#ifndef CONTROLLINO_AUX_SERVO
#define CONTROLLINO_AUX_SERVO 1
#endif
static_assert(CONTROLLINO_AUX_SERVO == 0 || CONTROLLINO_AUX_SERVO == 1,
              "Select ESC (0) or position servo (1)");
constexpr bool AUX_IS_SERVO = CONTROLLINO_AUX_SERVO;
// Miuzei MS62: nominal 270 degrees over 500–2500 us.
constexpr unsigned int SERVO_MIN_US = 500, SERVO_MAX_US = 2500;
constexpr unsigned int SERVO_DEFAULT_US = 1500;
static_assert(SERVO_MIN_US > 0 && SERVO_MIN_US < SERVO_MAX_US &&
              SERVO_MAX_US < 20000 && SERVO_DEFAULT_US >= SERVO_MIN_US &&
              SERVO_DEFAULT_US <= SERVO_MAX_US, "Invalid servo pulse calibration");

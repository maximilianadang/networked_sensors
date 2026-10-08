#pragma once

// One auxiliary backend per build. Keep the previous 0/1 build flag compatible.
// The dashboard discovers the selected capability through telemetry.
constexpr char FIRMWARE_VERSION[] = "1.4.1";
#define AUX_BACKEND_ESC 0
#define AUX_BACKEND_MS62 1
#define AUX_BACKEND_CUBEMARS 2
#ifndef CONTROLLINO_AUX_KIND
#ifdef CONTROLLINO_AUX_SERVO
#define CONTROLLINO_AUX_KIND CONTROLLINO_AUX_SERVO
#else
#define CONTROLLINO_AUX_KIND AUX_BACKEND_MS62
#endif
#endif
#ifdef CONTROLLINO_AUX_SERVO
static_assert(CONTROLLINO_AUX_SERVO == 0 || CONTROLLINO_AUX_SERVO == 1,
              "Legacy CONTROLLINO_AUX_SERVO must be 0 or 1");
static_assert(CONTROLLINO_AUX_KIND == CONTROLLINO_AUX_SERVO,
              "Conflicting auxiliary build selectors");
#endif
static_assert(CONTROLLINO_AUX_KIND >= AUX_BACKEND_ESC &&
              CONTROLLINO_AUX_KIND <= AUX_BACKEND_CUBEMARS,
              "Select ESC (0), MS62 (1), or CubeMars AK80-6 (2)");
constexpr bool AUX_IS_SERVO = CONTROLLINO_AUX_KIND == AUX_BACKEND_MS62;
constexpr bool AUX_IS_ESC = CONTROLLINO_AUX_KIND == AUX_BACKEND_ESC;
constexpr bool AUX_IS_CUBEMARS = CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS;
// Miuzei MS62: nominal 270 degrees over 500–2500 us.
constexpr unsigned int SERVO_MIN_US = 500, SERVO_MAX_US = 2500;
constexpr unsigned int SERVO_DEFAULT_US = 1500;
static_assert(SERVO_MIN_US > 0 && SERVO_MIN_US < SERVO_MAX_US &&
              SERVO_MAX_US < 20000 && SERVO_DEFAULT_US >= SERVO_MIN_US &&
              SERVO_DEFAULT_US <= SERVO_MAX_US, "Invalid servo pulse calibration");

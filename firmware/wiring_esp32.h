#pragma once
#include <Arduino.h>
#include "wiring_checks.h"

// Feather ESP32-S3: GPIO numbers. Array order is the existing API channel order.
constexpr int I2C_SDA = 3, I2C_SCL = 4;
constexpr int SOLENOID_PINS[] = {5, 6, 9, 10};
constexpr int SOLENOID_COUNT = sizeof(SOLENOID_PINS) / sizeof(SOLENOID_PINS[0]);
constexpr bool RELAY_ACTIVE_LOW = true;
constexpr uint8_t PRESSURE_ADC_ADDRESS = 0x48, FLOW_ADC_ADDRESS = 0x49;
constexpr int PRESSURE_CHANNELS[] = {0, 1, 2};  // ADS1115 A0-A2
constexpr int FLOW_CHANNELS[] = {0, 1, 2};      // ADS1115 A0-A2

constexpr int ESP32_PINS[] = {I2C_SDA, I2C_SCL, SOLENOID_PINS[0],
                             SOLENOID_PINS[1], SOLENOID_PINS[2],
                             SOLENOID_PINS[3], LED_BUILTIN};
static_assert(SOLENOID_COUNT == 4, "Telemetry v3 requires four solenoid channels");
static_assert(distinctPins(ESP32_PINS), "ESP32 pin collision (including status LED)");
static_assert(PRESSURE_ADC_ADDRESS != FLOW_ADC_ADDRESS,
              "Pressure and flow ADCs need distinct I2C addresses");

// Channel count belongs to the v3 protocol; rewiring changes order, not count.
static_assert(sizeof(PRESSURE_CHANNELS) / sizeof(int) == 3 &&
              sizeof(FLOW_CHANNELS) / sizeof(int) == 3,
              "Telemetry v3 requires three channels per ADC");
static_assert(distinctPins(PRESSURE_CHANNELS) && distinctPins(FLOW_CHANNELS) &&
              PRESSURE_CHANNELS[0] < 4 && PRESSURE_CHANNELS[1] < 4 &&
              PRESSURE_CHANNELS[2] < 4 && FLOW_CHANNELS[0] < 4 &&
              FLOW_CHANNELS[1] < 4 && FLOW_CHANNELS[2] < 4,
              "ADS1115 channels must be distinct values in 0..3");
static_assert(PRESSURE_ADC_ADDRESS >= 0x48 && PRESSURE_ADC_ADDRESS <= 0x4b &&
              FLOW_ADC_ADDRESS >= 0x48 && FLOW_ADC_ADDRESS <= 0x4b,
              "ADS1115 addresses must be in 0x48..0x4b");

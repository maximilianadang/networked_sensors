#pragma once
#include <stdint.h>

// Shared by Yún and Controllino: qualify assertions, release immediately.
// Fixed-width subtraction remains correct across the AVR micros() rollover.
struct QualifiedLimitInput {
  static constexpr uint32_t QUALIFY_US = 5000;
  bool qualifiedActive;
  bool assertionPending;
  uint32_t assertionStartedUs;
  uint8_t rejectedGlitches;

  void initialize(bool rawActive, uint32_t nowUs) {
    qualifiedActive = false;
    assertionPending = rawActive;
    assertionStartedUs = nowUs;
    rejectedGlitches = 0;
  }

  bool update(bool rawActive, uint32_t nowUs) {
    if (qualifiedActive) {
      if (!rawActive) qualifiedActive = assertionPending = false;
    } else if (!rawActive) {
      if (assertionPending && rejectedGlitches < 255) ++rejectedGlitches;
      assertionPending = false;
    } else if (!assertionPending) {
      assertionPending = true;
      assertionStartedUs = nowUs;
    } else if (uint32_t(nowUs - assertionStartedUs) >= QUALIFY_US) {
      qualifiedActive = true;
      assertionPending = false;
    }
    return qualifiedActive;
  }
};

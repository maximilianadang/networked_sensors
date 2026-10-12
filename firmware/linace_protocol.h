#pragma once
#include <stdint.h>

// CHE12PWA450E05BA00: 122.07 Hz / 8192 us, 10 um units, 450 mm travel.
// Timer ticks are 1/16 us. A fine timer does NOT remove software ISR latency;
// especially the 0.125 us minimum pulse needs hardware validation.
namespace Linace {
constexpr uint32_t PERIOD_TICKS = 131072UL, STALE_MS = 250UL;
// Broad acquisition sanity window, not a manufacturer oscillator tolerance.
constexpr uint32_t MIN_PERIOD_TICKS = PERIOD_TICKS * 9 / 10;
constexpr uint32_t MAX_PERIOD_TICKS = PERIOD_TICKS * 11 / 10;
constexpr int32_t MAX_POSITION = 45000; // 0.01 mm

struct Sample {
  uint32_t highTicks = 0, periodTicks = 0, sampledMs = 0;
  bool statusOk = false;
};

inline bool decode(const Sample &s, int32_t &position) {
  if (!s.statusOk || s.periodTicks < MIN_PERIOD_TICKS ||
      s.periodTicks > MAX_PERIOD_TICKS || !s.highTicks ||
      s.highTicks >= s.periodTicks) return false;
  // Datasheet: (ton / period * 65536 - 1) * 10 um. Round to the nearest
  // encoder count without a float or intermediate 32-bit multiplication wrap.
  const uint64_t scaled = uint64_t(s.highTicks) * 65536UL;
  if (scaled < s.periodTicks) return false; // Shorter than the zero-position pulse.
  uint32_t code = uint32_t((scaled + s.periodTicks / 2) / s.periodTicks);
  if (!code || code > uint32_t(MAX_POSITION) + 1) return false;
  position = int32_t(code - 1);
  return true;
}

// ISR-side edge bookkeeping only; division/validation happen in the main loop.
// A completed high pulse is paired with its following rising edge (period).
class Capture {
 public:
  void reset() { haveRise = haveFall = false; }
  bool edge(bool high, bool status, uint32_t ticks, uint32_t nowMs, Sample &s) {
    if (high) {
      bool complete = haveRise && haveFall && uint32_t(nowMs - riseMs) <= STALE_MS;
      if (complete) {
        s.highTicks = highTicks; s.periodTicks = ticks - riseTicks;
        s.sampledMs = riseMs; s.statusOk = riseStatus;
      }
      riseTicks = ticks; riseMs = nowMs; riseStatus = status;
      haveRise = true; haveFall = false;
      return complete;
    }
    if (haveRise && !haveFall) { highTicks = ticks - riseTicks; haveFall = true; }
    else reset(); // Duplicate/missed edges must resynchronize before publishing.
    return false;
  }

 private:
  bool haveRise = false, haveFall = false, riseStatus = false;
  uint32_t riseTicks = 0, riseMs = 0, highTicks = 0;
};
} // namespace Linace

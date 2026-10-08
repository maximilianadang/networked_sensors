#pragma once
#include <stdint.h>

// CubeMars AK-series driver manual V1.0.17, sections 5.1.2, 5.1.4, 5.2.1:
// https://www.cubemars.com/images/file/20250522/1747899365958473.pdf
// AK80-6 KV100: 21 pole pairs, 6:1 reduction => output RPM * 126 = ERPM.
// This header is independent of Arduino/SPI so framing and run expiry are testable.
#ifndef CUBEMARS_NODE_ID
#define CUBEMARS_NODE_ID 104
#endif
#ifndef CUBEMARS_MAX_RPM
#define CUBEMARS_MAX_RPM 400
#endif

namespace CubeMars {
constexpr uint8_t NODE_ID = CUBEMARS_NODE_ID;
constexpr int32_t ERPM_PER_OUTPUT_RPM = 126;
constexpr int32_t MAX_RPM = CUBEMARS_MAX_RPM;
constexpr uint32_t BITRATE = 500000UL;
constexpr uint32_t MIN_DURATION_MS = 20, MAX_DURATION_MS = 5000;
constexpr uint32_t FEEDBACK_TIMEOUT_MS = 500, REFRESH_MS = 20;
constexpr uint32_t MANUAL_LEASE_MS = 1500;
constexpr int32_t MAX_RAMP_RPM_S = 80, DEFAULT_RAMP_RPM_S = 80;
static_assert(CUBEMARS_NODE_ID >= 0 && CUBEMARS_NODE_ID <= 255, "Invalid CubeMars node ID");
static_assert(MAX_RPM > 0 && MAX_RPM <= 100000L / ERPM_PER_OUTPUT_RPM,
              "CubeMars speed limit exceeds the protocol's ERPM range");
static_assert(DEFAULT_RAMP_RPM_S > 0 && DEFAULT_RAMP_RPM_S <= MAX_RAMP_RPM_S &&
              MAX_RAMP_RPM_S <= (0xffffffffUL - 999) / (ERPM_PER_OUTPUT_RPM * MANUAL_LEASE_MS),
              "Invalid CubeMars ramp limit or accumulator overflow");

constexpr uint32_t commandId(uint8_t function) {
  return (uint32_t(function) << 8) | NODE_ID;
}
inline void encodeInt32(int32_t value, uint8_t *out) {
  const uint32_t bits = uint32_t(value);
  out[0] = bits >> 24; out[1] = bits >> 16; out[2] = bits >> 8; out[3] = bits;
}
inline int16_t signed16(const uint8_t *data) {
  const uint16_t value = (uint16_t(data[0]) << 8) | data[1];
  return value <= 0x7fff ? int16_t(value) : int16_t(int32_t(value) - 65536L);
}
struct Feedback {
  int32_t erpm = 0;
  int16_t centiamps = 0;
  int8_t temperature = 0;
  uint8_t fault = 0;
};
enum Reply : uint8_t { IGNORED, FEEDBACK, RESTART };
inline Reply decodeReply(uint32_t id, bool extended, bool remote, uint8_t length,
                         const uint8_t *data, Feedback &out) {
  if (!extended || remote) return IGNORED;
  const uint32_t function = id >> 8;
  const bool startup = function == 0x2c && (length == 4 || length == 8) &&
      data[0] == 0xfa && data[1] == 0xfb && data[2] == 0xfc && data[3] == 0xfd;
  // Captured before node 104 was applied. Conservatively stop on this bus-wide
  // startup marker; other node-zero traffic must never refresh motor feedback.
  if (startup && id == 0x2c00) return RESTART;
  if ((id & 0xff) != NODE_ID) return IGNORED;
  // These are startup indications, never valid speed/current feedback.
  if (function == 0x09 && length == 8) return RESTART;
  if (startup) return RESTART;
  if (function != 0x29 || length != 8) return IGNORED;
  out.erpm = int32_t(signed16(data + 2)) * 10;
  out.centiamps = signed16(data + 4);
  out.temperature = data[6] <= 127 ? int8_t(data[6]) : int8_t(int16_t(data[6]) - 256);
  out.fault = data[7];
  return FEEDBACK;
}

struct RunState {
  Feedback feedback;
  bool seenFeedback = false, active = false;
  int32_t commandedRpm = 0;
  uint32_t feedbackAt = 0, startedAt = 0, durationMs = 0;
  uint32_t manualToken = 0, leaseAt = 0, rampAt = 0, rampRemainder = 0;
  uint32_t retiredManualToken = 0;
  int32_t rampRate = 0, appliedErpm = 0;
  const char *reason = "boot";

  bool fresh(uint32_t now) const {
    return seenFeedback && uint32_t(now - feedbackAt) <= FEEDBACK_TIMEOUT_MS;
  }
  uint32_t remaining(uint32_t now) const {
    const uint32_t elapsed = now - (manualToken ? leaseAt : startedAt);
    const uint32_t limit = manualToken ? MANUAL_LEASE_MS : durationMs;
    return active && elapsed < limit ? limit - elapsed : 0;
  }
  void stop(const char *why) {
    if (manualToken) retiredManualToken = manualToken;
    active = false; commandedRpm = 0; durationMs = 0; reason = why;
    manualToken = 0; rampRate = 0; appliedErpm = 0; rampRemainder = 0;
  }
  void receive(const Feedback &value, uint32_t now) {
    feedback = value; feedbackAt = now; seenFeedback = true;
    if (feedback.fault) stop("motor_fault");
  }
  void invalidate(const char *why) { seenFeedback = false; stop(why); }
  const char *checkTarget(int32_t rpm, uint32_t now) const {
    if (!rpm || rpm < -MAX_RPM || rpm > MAX_RPM) return "can_speed_range";
    if (!fresh(now)) return "can_feedback_stale";
    if (feedback.fault) return "can_motor_fault";
    return nullptr;
  }
  const char *start(int32_t rpm, uint32_t duration, uint32_t now) {
    if (active && manualToken) return "can_manual_active";
    if (!rpm || rpm < -MAX_RPM || rpm > MAX_RPM) return "can_speed_range";
    if (duration < MIN_DURATION_MS || duration > MAX_DURATION_MS) return "can_duration_range";
    const char *error = checkTarget(rpm, now);
    if (error) return error;
    commandedRpm = rpm; startedAt = now; durationMs = duration;
    if (manualToken) retiredManualToken = manualToken;
    manualToken = 0; rampRate = 0; rampRemainder = 0;
    appliedErpm = rpm * ERPM_PER_OUTPUT_RPM;
    active = true; reason = "running";
    return nullptr;
  }
  const char *checkManual(int32_t rpm, int32_t rate, uint32_t token, uint32_t now) const {
    if (!token || token > 0x7fffffffUL) return "can_manual_token";
    if (rate < 1 || rate > MAX_RAMP_RPM_S) return "can_ramp_range";
    return checkTarget(rpm, now);
  }
  const char *startManual(int32_t rpm, int32_t rate, uint32_t token, uint32_t now) {
    if (active) return "can_manual_active";
    const char *error = checkManual(rpm, rate, token, now);
    if (error) return error;
    if (token == retiredManualToken) return "can_manual_token";
    const int32_t limit = MAX_RPM * ERPM_PER_OUTPUT_RPM;
    if (feedback.erpm < -limit || feedback.erpm > limit) return "can_feedback_speed_range";
    commandedRpm = rpm; rampRate = rate; manualToken = token;
    leaseAt = rampAt = startedAt = now; durationMs = 0; rampRemainder = 0;
    appliedErpm = feedback.erpm;
    active = true; reason = "running";
    return nullptr;
  }
  const char *checkLease(uint32_t token, uint32_t now) {
    service(now);  // Expired/faulted runs cannot be revived by late requests.
    if (!active || !manualToken) return "can_manual_inactive";
    if (token != manualToken) return "can_manual_token";
    return nullptr;
  }
  const char *updateManual(int32_t rpm, int32_t rate, uint32_t token, uint32_t now) {
    const char *error = checkLease(token, now);
    if (error) return error;
    error = checkManual(rpm, rate, token, now);
    if (error) return error;
    // Preserve the applied setpoint; discard a sub-ERPM remainder on reversal.
    const int32_t oldDelta = commandedRpm * ERPM_PER_OUTPUT_RPM - appliedErpm;
    const int32_t newDelta = rpm * ERPM_PER_OUTPUT_RPM - appliedErpm;
    if ((oldDelta < 0 && newDelta > 0) || (oldDelta > 0 && newDelta < 0)) rampRemainder = 0;
    commandedRpm = rpm; rampRate = rate; leaseAt = now;
    return nullptr;
  }
  const char *renewManual(uint32_t token, uint32_t now) {
    const char *error = checkLease(token, now);
    if (error) return error;
    leaseAt = now;
    return nullptr;
  }
  void service(uint32_t now) {
    if (!active) return;
    if (feedback.fault) stop("motor_fault");
    else if (!fresh(now)) stop("feedback_stale");
    else if (!remaining(now)) stop(manualToken ? "control_timeout" : "duration_elapsed");
    else if (manualToken) {
      const uint32_t elapsed = now - rampAt;
      rampAt = now;
      // Lease bounds elapsed; fractional ERPM accumulation avoids rounding a
      // slow ramp to zero when service is called more often than CAN refresh.
      const uint32_t allowance = uint32_t(rampRate) * ERPM_PER_OUTPUT_RPM * elapsed + rampRemainder;
      const uint32_t step = allowance / 1000;
      rampRemainder = allowance % 1000;
      const int32_t delta = commandedRpm * ERPM_PER_OUTPUT_RPM - appliedErpm;
      const uint32_t distance = delta < 0 ? uint32_t(-delta) : uint32_t(delta);
      if (step >= distance) {
        appliedErpm = commandedRpm * ERPM_PER_OUTPUT_RPM; rampRemainder = 0;
      } else appliedErpm += delta < 0 ? -int32_t(step) : int32_t(step);
    }
  }
};
}  // namespace CubeMars

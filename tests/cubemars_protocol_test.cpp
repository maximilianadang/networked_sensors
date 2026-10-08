#include "cubemars_protocol.h"
#include <assert.h>
#include <initializer_list>
#include <limits.h>
#include <string.h>

using namespace CubeMars;

static void expectBytes(int32_t value, const uint8_t (&expected)[4]) {
  uint8_t actual[4];
  encodeInt32(value, actual);
  assert(memcmp(actual, expected, sizeof(actual)) == 0);
}

static void testWireFormat() {
  // Independent fixtures from the manual's signed, big-endian wire definition.
  assert(commandId(3) == 0x368);  // RPM command to the motor read as node 104.
  assert(commandId(1) == 0x168);  // Zero-current release uses current mode.
  expectBytes(0, {0, 0, 0, 0});
  expectBytes(100000, {0, 1, 0x86, 0xa0});
  expectBytes(-100000, {0xff, 0xfe, 0x79, 0x60});
  expectBytes(INT32_MAX, {0x7f, 0xff, 0xff, 0xff});
  expectBytes(INT32_MIN, {0x80, 0, 0, 0});
  expectBytes(10 * ERPM_PER_OUTPUT_RPM, {0, 0, 4, 0xec});
  expectBytes(-10 * ERPM_PER_OUTPUT_RPM, {0xff, 0xff, 0xfb, 0x14});

  Feedback value;
  const uint8_t forward[] = {0x12, 0x34, 0x04, 0xd2, 0x01, 0xf4, 31, 0};
  assert(decodeReply(0x2968, true, false, 8, forward, value) == FEEDBACK);
  assert(value.erpm == 12340 && value.centiamps == 500);
  assert(value.temperature == 31 && value.fault == 0);
  const uint8_t reverse[] = {0xab, 0xcd, 0xfb, 0x2e, 0xfe, 0x0c, 0xec, 6};
  assert(decodeReply(0x2968, true, false, 8, reverse, value) == FEEDBACK);
  assert(value.erpm == -12340 && value.centiamps == -500);
  assert(value.temperature == -20 && value.fault == 6);
  const uint8_t upper[] = {0, 0, 0x7f, 0xff, 0x7f, 0xff, 0x7f, 0xff};
  const uint8_t lower[] = {0, 0, 0x80, 0, 0x80, 0, 0x80, 0};
  decodeReply(0x2968, true, false, 8, upper, value);
  assert(value.erpm == 327670 && value.centiamps == 32767);
  assert(value.temperature == 127 && value.fault == 255);
  decodeReply(0x2968, true, false, 8, lower, value);
  assert(value.erpm == -327680 && value.centiamps == -32768);
  assert(value.temperature == -128 && value.fault == 0);
}

static void testFrameAdmission() {
  const uint8_t data[] = {0, 0, 0, 1, 0, 2, 3, 4};
  Feedback value;
  value.erpm = 99;
  assert(decodeReply(0x2968, false, false, 8, data, value) == IGNORED);
  assert(decodeReply(0x2968, true, true, 8, data, value) == IGNORED);
  assert(decodeReply(0x2969, true, false, 8, data, value) == IGNORED);
  assert(decodeReply(0x2868, true, false, 8, data, value) == IGNORED);
  for (uint8_t length = 0; length <= 9; ++length) {
    if (length != 8)
      assert(decodeReply(0x2968, true, false, length, data, value) == IGNORED);
  }
  assert(value.erpm == 99);  // Unrelated traffic cannot refresh motor state.

  assert(decodeReply(0x0968, true, false, 8, data, value) == RESTART);
  assert(decodeReply(0x0968, true, false, 4, data, value) == IGNORED);
  const uint8_t startup[] = {0xfa, 0xfb, 0xfc, 0xfd, 0, 0, 0, 0};
  assert(decodeReply(0x2c68, true, false, 4, startup, value) == RESTART);
  assert(decodeReply(0x2c68, true, false, 8, startup, value) == RESTART);
  assert(decodeReply(0x2c68, true, false, 3, startup, value) == IGNORED);
  assert(decodeReply(0x2c68, true, false, 8, data, value) == IGNORED);
  assert(decodeReply(0x2c69, true, false, 4, startup, value) == IGNORED);
  assert(decodeReply(0x2c68, true, true, 4, startup, value) == IGNORED);
  // Captured at power-up before the motor applied its configured node ID 104.
  assert(decodeReply(0x2c00, true, false, 4, startup, value) == RESTART);
  assert(decodeReply(0x2c00, true, false, 8, startup, value) == RESTART);
  assert(decodeReply(0x2c00, false, false, 4, startup, value) == IGNORED);
  assert(decodeReply(0x2c00, true, true, 4, startup, value) == IGNORED);
  assert(decodeReply(0x2c00, true, false, 4, data, value) == IGNORED);
  for (uint8_t length = 0; length <= 9; ++length) {
    if (length != 4 && length != 8)
      assert(decodeReply(0x2c00, true, false, length, startup, value) == IGNORED);
  }
  assert(decodeReply(0x2900, true, false, 8, data, value) == IGNORED);
  assert(decodeReply(0x0900, true, false, 8, data, value) == IGNORED);
  assert(value.erpm == 99);  // Startup frames are not speed measurements.
}

static void assertStopped(const RunState &state, const char *reason) {
  assert(!state.active && state.commandedRpm == 0 && state.remaining(0) == 0);
  assert(strcmp(state.reason, reason) == 0);
}

static void testRunAdmissionAndDuration() {
  RunState state;
  assertStopped(state, "boot");
  assert(strcmp(state.start(1, 100, 0), "can_feedback_stale") == 0);
  state.receive(Feedback(), 100);
  for (int32_t rpm : {int32_t(0), -MAX_RPM - 1, MAX_RPM + 1, int32_t(INT32_MIN), int32_t(INT32_MAX)})
    assert(strcmp(state.start(rpm, 100, 100), "can_speed_range") == 0);
  for (uint32_t duration : {uint32_t(0), MIN_DURATION_MS - 1, MAX_DURATION_MS + 1, uint32_t(UINT32_MAX)})
    assert(strcmp(state.start(1, duration, 100), "can_duration_range") == 0);
  assert(!state.active);

  assert(state.start(-MAX_RPM, MIN_DURATION_MS, 100) == nullptr);
  assert(state.active && state.commandedRpm == -MAX_RPM);
  assert(state.remaining(100) == MIN_DURATION_MS);
  state.service(100 + MIN_DURATION_MS - 1);
  assert(state.active && state.remaining(100 + MIN_DURATION_MS - 1) == 1);
  state.service(100 + MIN_DURATION_MS);
  assertStopped(state, "duration_elapsed");
  state.receive(Feedback(), 200);
  state.service(200);
  assertStopped(state, "duration_elapsed");  // Fresh feedback never starts motion.

  assert(state.start(MAX_RPM, MAX_DURATION_MS, 200) == nullptr);
  for (uint32_t now = 300; now < 200 + MAX_DURATION_MS; now += 100) {
    state.receive(Feedback(), now);
    state.service(now);
    assert(state.active);
  }
  state.receive(Feedback(), 200 + MAX_DURATION_MS);
  state.service(200 + MAX_DURATION_MS);
  assertStopped(state, "duration_elapsed");
}

static void testLossFaultAndRestart() {
  RunState state;
  state.receive(Feedback(), 100);
  assert(state.start(1, 2000, 100) == nullptr);
  state.service(100 + FEEDBACK_TIMEOUT_MS);
  assert(state.active);  // Explicit freshness boundary, then loss of feedback.
  state.service(101 + FEEDBACK_TIMEOUT_MS);
  assertStopped(state, "feedback_stale");
  state.receive(Feedback(), 700);
  state.service(700);
  assertStopped(state, "feedback_stale");
  assert(state.start(-1, 1000, 700) == nullptr);  // Requires a new command.

  Feedback faulty;
  faulty.fault = 2;
  state.receive(faulty, 701);
  assertStopped(state, "motor_fault");
  assert(strcmp(state.start(1, 100, 701), "can_motor_fault") == 0);
  state.receive(Feedback(), 702);
  state.service(702);
  assertStopped(state, "motor_fault");
  assert(state.start(1, 1000, 702) == nullptr);
  state.invalidate("motor_restart");
  assertStopped(state, "motor_restart");
  assert(!state.fresh(702));
  assert(strcmp(state.start(1, 100, 702), "can_feedback_stale") == 0);
  state.receive(Feedback(), 703);
  state.service(703);
  assertStopped(state, "motor_restart");
  assert(state.start(1, 100, 703) == nullptr);
  state.stop("user_stop");
  state.receive(Feedback(), 704);
  state.service(704);
  assertStopped(state, "user_stop");
}

static void testClockRollover() {
  RunState state;
  state.receive(Feedback(), UINT32_MAX - 100);
  assert(state.start(1, 200, UINT32_MAX - 50) == nullptr);
  assert(state.remaining(49) == 100);
  state.service(148);
  assert(state.active && state.remaining(148) == 1);
  state.service(149);
  assertStopped(state, "duration_elapsed");
  assert(state.fresh(399));
  assert(!state.fresh(400));

  state.receive(Feedback(), UINT32_MAX - 100);
  assert(state.start(1, 1000, UINT32_MAX - 50) == nullptr);
  state.service(399);
  assert(state.active);
  state.service(400);
  assertStopped(state, "feedback_stale");
}

static void testManualAdmissionAndSlew() {
  RunState state;
  assert(strcmp(state.startManual(3, 2, 123, 100), "can_feedback_stale") == 0);
  Feedback measured; measured.erpm = 126;
  state.receive(measured, 100);
  for (uint32_t token : {uint32_t(0), uint32_t(0x80000000), uint32_t(UINT32_MAX)})
    assert(strcmp(state.startManual(3, 2, token, 100), "can_manual_token") == 0);
  for (int32_t rate : {int32_t(0), MAX_RAMP_RPM_S + 1})
    assert(strcmp(state.startManual(3, rate, 123, 100), "can_ramp_range") == 0);
  measured.erpm = MAX_RPM * ERPM_PER_OUTPUT_RPM + 1;
  state.receive(measured, 100);
  assert(strcmp(state.startManual(3, 2, 123, 100), "can_feedback_speed_range") == 0);
  measured.erpm = 126; state.receive(measured, 100);
  assert(state.startManual(3, 2, 123, 100) == nullptr);
  assert(state.appliedErpm == 126 && state.manualToken == 123);
  assert(strcmp(state.startManual(4, 2, 124, 100), "can_manual_active") == 0);
  state.service(350);
  assert(state.appliedErpm == 189);  // 1 RPM + 2 RPM/s * 0.25 s.
  assert(strcmp(state.updateManual(-2, 1, 124, 350), "can_manual_token") == 0);
  assert(state.commandedRpm == 3);
  assert(state.updateManual(-2, 1, 123, 350) == nullptr);
  assert(state.appliedErpm == 189);  // Target update never jumps the setpoint.
  state.receive(Feedback(), 850); state.service(850);
  assert(state.appliedErpm == 126);
  assert(state.updateManual(-2, 4, 123, 850) == nullptr);
  state.service(1100); assert(state.appliedErpm == 0);
  state.receive(Feedback(), 1600); state.service(1600);
  assert(state.appliedErpm == -252);  // Reversal is limited and clamps at target.
  state.stop("operator_stop");
  assert(!state.manualToken && !state.rampRate && !state.appliedErpm);
}

static void testManualFractionalLeaseAndReplay() {
  RunState state;
  state.receive(Feedback(), 0);
  assert(state.startManual(1, 1, 7, 0) == nullptr);
  // 1-ms service calls must accumulate sub-ERPM increments, not stall the ramp.
  for (uint32_t now = 1; now <= 1000; ++now) {
    if (now % 100 == 0) state.receive(Feedback(), now);
    if (now % 250 == 0) assert(state.renewManual(7, now) == nullptr);
    state.service(now);
  }
  assert(state.appliedErpm == 126 && state.remaining(1000) == 1500);
  state.receive(Feedback(), 1499); state.service(1499);
  state.receive(Feedback(), 1999); state.service(1999);
  state.receive(Feedback(), 2499); state.service(2499);
  assert(state.active && state.remaining(2499) == 1);
  assert(strcmp(state.renewManual(7, 2500), "can_manual_inactive") == 0);
  assertStopped(state, "control_timeout");
  assert(!state.manualToken && !state.rampRate && !state.appliedErpm);
  assert(strcmp(state.updateManual(2, 2, 7, 2500), "can_manual_inactive") == 0);
  assert(strcmp(state.startManual(1, 1, 7, 2500), "can_manual_token") == 0);
  assert(state.startManual(1, 1, 8, 2500) == nullptr);
  assert(strcmp(state.renewManual(7, 2501), "can_manual_token") == 0);
  assert(state.remaining(2501) == 1499);
  // CSV/timed starts cannot replace a live manual session.
  assert(strcmp(state.start(1, 20, 2501), "can_manual_active") == 0);
  assert(state.active && state.manualToken == 8);
  state.stop("operator_stop");
  // The existing timed path remains available after the manual session ends.
  assert(state.start(1, 20, 2501) == nullptr);
  assert(!state.manualToken && !state.rampRate && state.appliedErpm == 126);
  state.service(2521); assertStopped(state, "duration_elapsed");
  assert(strcmp(state.startManual(1, 1, 8, 2521), "can_manual_token") == 0);
}

static void testManualFaultsAndRollover() {
  for (unsigned cause = 0; cause < 3; ++cause) {
    RunState state; state.receive(Feedback(), 100);
    assert(state.startManual(1, 1, 99, 100) == nullptr);
    if (cause == 0) { Feedback fault; fault.fault = 2; state.receive(fault, 101); }
    else if (cause == 1) state.invalidate("motor_restart");
    else state.service(601);
    assert(!state.active && !state.manualToken);
    state.receive(Feedback(), 700);
    assert(strcmp(state.renewManual(99, 700), "can_manual_inactive") == 0);
    assert(strcmp(state.updateManual(1, 1, 99, 700), "can_manual_inactive") == 0);
    assert(strcmp(state.startManual(1, 1, 99, 700), "can_manual_token") == 0);
    assert(state.startManual(1, 1, 100, 700) == nullptr);
  }
  RunState state; state.receive(Feedback(), UINT32_MAX - 99);
  assert(state.startManual(1, 1, 42, UINT32_MAX - 99) == nullptr);
  assert(state.renewManual(42, 100) == nullptr);
  assert(state.appliedErpm == 25 && state.remaining(100) == 1500);
  state.receive(Feedback(), 600); state.service(600);
  state.receive(Feedback(), 1100); state.service(1100);
  state.receive(Feedback(), 1600);
  assert(strcmp(state.renewManual(42, 1600), "can_manual_inactive") == 0);
  assertStopped(state, "control_timeout");
}

static void testFullSpeedRampAndReversal() {
  assert(MAX_RPM == 400 && MAX_RAMP_RPM_S == 80 && DEFAULT_RAMP_RPM_S == 80);
  RunState state; state.receive(Feedback(), 0);
  assert(strcmp(state.startManual(401, 80, 555, 0), "can_speed_range") == 0);
  assert(strcmp(state.startManual(-401, 80, 555, 0), "can_speed_range") == 0);
  assert(strcmp(state.startManual(400, 81, 555, 0), "can_ramp_range") == 0);
  assert(state.startManual(400, 80, 555, 0) == nullptr);
  for (uint32_t now = 250; now <= 5000; now += 250) {
    state.receive(Feedback(), now);
    assert(state.renewManual(555, now) == nullptr);
    if (now % 1000 == 0) assert(state.appliedErpm == int32_t(now / 1000) * 10080);
  }
  assert(state.appliedErpm == 50400 && state.active);
  assert(state.updateManual(-400, 80, 555, 5000) == nullptr);
  assert(state.appliedErpm == 50400);
  for (uint32_t now = 5250; now <= 15000; now += 250) {
    state.receive(Feedback(), now);
    assert(state.renewManual(555, now) == nullptr);
    if (now % 1000 == 0)
      assert(state.appliedErpm == 50400 - int32_t((now - 5000) / 1000) * 10080);
  }
  assert(state.appliedErpm == -50400 && state.active);
}

int main() {
  testWireFormat();
  testFrameAdmission();
  testRunAdmissionAndDuration();
  testLossFaultAndRestart();
  testClockRollover();
  testManualAdmissionAndSlew();
  testManualFractionalLeaseAndReplay();
  testManualFaultsAndRollover();
  testFullSpeedRampAndReversal();
}

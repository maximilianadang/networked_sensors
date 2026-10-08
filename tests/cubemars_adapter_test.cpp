#include "cubemars_can.h"
#include <assert.h>
#include <stdio.h>

SPIClass SPI;
unsigned long millis() { return SPI.clockUs / 1000; }
unsigned long micros() { return SPI.clockUs; }
void delay(unsigned long ms) { SPI.clockUs += ms * 1000; }
void pinMode(uint8_t, uint8_t) {}
void digitalWrite(uint8_t pin, uint8_t value) { assert(pin == 9); SPI.select(value == LOW); }

static void testActualDependencyAndInitialization() {
  SPI = SPIClass();
  MCP2515 library(9, 8000000, &SPI);
  assert(library.reset() == MCP2515::ERROR_OK);
  // Reproduce 1.3.1's bug: it compares CANSTAT & 0xe0 with OSM's 0x08.
  assert(library.setNormalOneShotMode() == MCP2515::ERROR_FAIL);
  assert((SPI.reg[0x0f] & 0xe8) == 8 && !(SPI.reg[0x0e] & 0xe0));

  SPI = SPIClass();
  CubeMarsCan motor(9);
  motor.begin();
  assert(motor.initialized && (SPI.reg[0x0f] & 0xe8) == 8);
  // Independent expected settings: 16 MHz oscillator, 500 kbps; extended node 104.
  assert(SPI.reg[0x2a] == 0 && SPI.reg[0x29] == 0xf0 && SPI.reg[0x28] == 0x86);
  for (unsigned address : {0x20u, 0x24u}) {
    assert(SPI.reg[address] == 0 && SPI.reg[address + 1] == 8);
    assert(SPI.reg[address + 2] == 0 && SPI.reg[address + 3] == 0xff);
  }
  for (unsigned address : {0u, 4u, 8u, 0x10u, 0x14u, 0x18u}) {
    assert(SPI.reg[address] == 0 && SPI.reg[address + 1] == 8);
    // RXF0 admits the observed node-zero startup; other filters admit node 104.
    assert(SPI.reg[address + 2] == 0 && SPI.reg[address + 3] == (address == 0 ? 0 : 104));
  }
  assert(SPI.requests == 0);  // Initialization alone queues no motor command.
}

static void testInitializationFailures() {
  for (unsigned failure = 0; failure < 4; ++failure) {
    SPI = SPIClass();
    SPI.missing = failure == 0;
    SPI.ignoreOsm = failure == 1;
    SPI.refuseNormal = failure == 2;
    // Reset programs six filters and two masks; its next config request is bitrate.
    SPI.failConfigRequest = failure == 3 ? 9 : 0;
    CubeMarsCan motor(9);
    motor.begin();
    assert(!motor.initialized && !motor.run.active);
    assert(strcmp(motor.run.reason, "can_init_failed") == 0);
    assert(strcmp(motor.start(5, 100, millis()), "can_unavailable") == 0);
    assert(SPI.requests == 0);
  }
}

static void expectQueued(uint8_t function, const uint8_t (&bytes)[4]) {
  // First hardware TX buffer: SIDH/SIDL/EID8/EID0 + DLC + payload.
  assert(SPI.reg[0x30] & 8);
  assert(SPI.reg[0x31] == 0 && SPI.reg[0x32] == 8);
  assert(SPI.reg[0x33] == function && SPI.reg[0x34] == 104 && SPI.reg[0x35] == 4);
  assert(memcmp(&SPI.reg[0x36], bytes, 4) == 0);
  assert(!(SPI.reg[0x40] & 8) && !(SPI.reg[0x50] & 8));
}

static void injectFeedback(uint8_t buffer = 0) {
  // Real library RX path: extended 0x2968, -1260 ERPM, -1 A, 31 C, no fault.
  const uint8_t received[] = {0, 8, 0x29, 104, 8, 0, 0, 0xff, 0x82, 0xff, 0x9c, 31, 0};
  memcpy(&SPI.reg[0x61 + 16 * buffer], received, sizeof(received));
  SPI.reg[0x2c] |= 1 << buffer;
}

static void testObservedStartupStopsWithoutAutomaticRestart() {
  for (uint8_t startupBuffer : {uint8_t(0), uint8_t(1)}) {
    SPI = SPIClass();
    CubeMarsCan motor(9);
    motor.begin();
    // Node-zero feedback is admitted by RXF0 but cannot establish readiness.
    injectFeedback(); SPI.reg[0x64] = 0;
    motor.service(millis());
    assert(!motor.run.seenFeedback);
    injectFeedback(); motor.service(millis());
    assert(motor.start(5, 1000, millis()) == nullptr);
    const uint32_t sequence = motor.commandSequence;

    // Both RX orders are possible with rollover. Fresh feedback in the same
    // polling cycle must not mask a reboot or restore the previous speed.
    const uint8_t startup[] = {0, 8, 0x2c, 0, 4, 0xfa, 0xfb, 0xfc, 0xfd};
    memcpy(&SPI.reg[0x61 + 16 * startupBuffer], startup, sizeof(startup));
    SPI.reg[0x2c] |= 1 << startupBuffer;
    injectFeedback(1 - startupBuffer);
    motor.service(millis());
    assert(!motor.run.active && motor.run.commandedRpm == 0);
    assert(strcmp(motor.run.reason, "motor_restart") == 0);
    assert(motor.commandSequence == sequence);
    expectQueued(1, {0, 0, 0, 0});

    delay(21); injectFeedback(); motor.service(millis());
    assert(motor.run.fresh(millis()) && !motor.run.active);
    assert(strcmp(motor.run.reason, "motor_restart") == 0);
    expectQueued(1, {0, 0, 0, 0});
    assert(motor.start(5, 1000, millis()) == nullptr);  // New explicit command only.
    expectQueued(3, {0, 0, 2, 0x76});  // 5 output RPM = 630 ERPM.
  }
}

static void testExtendedCommandsAndAbort() {
  SPI = SPIClass();
  CubeMarsCan motor(9);
  motor.begin();
  injectFeedback();
  motor.service(millis());
  assert(motor.run.seenFeedback && motor.run.feedback.erpm == -1260);
  assert(motor.run.feedback.centiamps == -100 && motor.run.feedback.temperature == 31);
  assert(!(SPI.reg[0x2c] & 1));  // Library acknowledged the hardware RX buffer.
  assert(motor.start(-10, 1000, millis()) == nullptr);
  expectQueued(3, {0xff, 0xff, 0xfb, 0x14});  // -1260 ERPM.
  assert(motor.commandSequence == 1 && motor.requestedRpm == -10);
  motor.release("operator_stop");
  expectQueued(1, {0, 0, 0, 0});
  assert(!motor.run.active && motor.commandSequence == 2 && motor.requestedRpm == 0);
  assert((SPI.reg[0x0f] & 0x18) == 8);  // Abort cleared, one-shot retained.

  SPI.holdAbort = true;
  const unsigned sent = SPI.requests;
  motor.release("operator_stop");
  assert(SPI.requests == sent && (SPI.reg[0x0f] & 0x10));
  assert(!motor.run.active && strcmp(motor.run.reason, "can_tx_failed") == 0);
  SPI.holdAbort = false;
  delay(21);
  motor.service(millis());
  expectQueued(1, {0, 0, 0, 0});
  assert(!(SPI.reg[0x0f] & 0x10) && !motor.run.active);
}

static void expectText(const Print &out, const char *text) {
  if (out.text.find(text) == std::string::npos)
    fprintf(stderr, "Missing %s in status: %s\n", text, out.text.c_str());
  assert(out.text.find(text) != std::string::npos);
}

static void testDiagnosticsAreReadOnlyAndPreserveEvidence() {
  SPI = SPIClass();
  CubeMarsCan motor(9);
  Print uninitialized;
  motor.writeStatus(uninitialized, millis());
  expectText(uninitialized, "\"ef\":-1");
  expectText(uninitialized, "\"tec\":-1");
  expectText(uninitialized, "\"rec\":-1");
  expectText(uninitialized, "\"reg\":\"\",\"slow\":\"\"");
  expectText(uninitialized, "\"rx\":0");
  expectText(uninitialized, "\"id\":-1");
  assert(SPI.reads[0x2d] == 0 && SPI.reads[0x1c] == 0 && SPI.reads[0x1d] == 0);

  motor.begin();
  injectFeedback();
  SPI.reg[0x2d] = 0x15;  // TX passive + warning + aggregate warning.
  SPI.reg[0x1c] = 128;
  SPI.reg[0x1d] = 7;
  motor.service(millis());
  assert(!motor.run.seenFeedback);
  Print out;
  const auto reads = SPI.reads;
  const unsigned writes = SPI.writes, requests = SPI.requests;
  motor.writeStatus(out, millis());
  expectText(out, "\"ca\":-1");
  expectText(out, "\"cx\":\"can_bus_error\"");
  expectText(out, "\"ef\":21");
  expectText(out, "\"tec\":128");
  expectText(out, "\"rec\":7");
  expectText(out, "\"rx\":1");
  expectText(out, "\"id\":10600");  // 0x2968.
  expectText(out, "\"ext\":1");
  expectText(out, "\"rtr\":0");
  expectText(out, "\"dlc\":8");
  expectText(out, "\"data\":\"0000FF82FF9C1F00\"");
  assert(SPI.writes == writes && SPI.requests == requests);
  for (unsigned address : {0x2du, 0x1cu, 0x1du})
    assert(SPI.reads[address] == reads[address] + (address == 0x2d ? 2 : 1));

  // An ignored standard frame is still useful raw diagnostic evidence.
  const uint8_t ignored[] = {0x24, 0x60, 0, 0, 3, 0xab, 0, 0xfe};
  memcpy(&SPI.reg[0x61], ignored, sizeof(ignored));
  SPI.reg[0x2c] |= 1;
  SPI.reg[0x2d] = 0;
  motor.service(millis());
  Print ignoredStatus;
  motor.writeStatus(ignoredStatus, millis());
  expectText(ignoredStatus, "\"rx\":2");
  expectText(ignoredStatus, "\"id\":291");  // Standard ID 0x123.
  expectText(ignoredStatus, "\"ext\":0");
  expectText(ignoredStatus, "\"rtr\":0");
  expectText(ignoredStatus, "\"dlc\":3");
  expectText(ignoredStatus, "\"data\":\"AB00FE\"");
  assert(!motor.run.seenFeedback);

  SPI.reg[0x60] |= 8;  // RTR keeps its requested DLC but carries no data.
  SPI.reg[0x2c] |= 1;
  motor.service(millis());
  Print remoteStatus;
  motor.writeStatus(remoteStatus, millis());
  expectText(remoteStatus, "\"rx\":3");
  expectText(remoteStatus, "\"rtr\":1");
  expectText(remoteStatus, "\"dlc\":3");
  expectText(remoteStatus, "\"data\":\"\"");
}

static std::string hexField(const Print &out, const char *key) {
  const std::string prefix = std::string("\"") + key + "\":\"";
  const size_t start = out.text.find(prefix);
  assert(start != std::string::npos);
  const size_t value = start + prefix.size();
  return out.text.substr(value, out.text.find('"', value) - value);
}

static void testDiagnosticRegisterReadIntegrityAndReceiveFailures() {
  SPI = SPIClass();
  CubeMarsCan motor(9);
  motor.begin();
  // Inject a fast-SPI read fault without changing the hardware configuration.
  SPI.fastCorruptAddress = 0x29;
  SPI.reg[0x30] = 0x50; SPI.reg[0x40] = 0x20; SPI.reg[0x50] = 8;
  SPI.reg[0x60] = 4; SPI.reg[0x70] = 1;
  Print out;
  const unsigned writes = SPI.writes, requests = SPI.requests;
  motor.writeStatus(out, millis());
  const std::string fast = hexField(out, "reg"), slow = hexField(out, "slow");
  assert(fast.size() == 30 && slow.size() == 16);
  assert(fast.substr(0, 6) == "86F100");
  assert(slow.substr(0, 6) == "86F000");
  assert(slow.substr(12) == "000F");  // Normal CANSTAT; one-shot CANCTRL alias.
  assert(fast.substr(20) == "5020080401");
  assert(SPI.reg[0x29] == 0xf0 && SPI.slowReads[0x29] == 1);
  assert(SPI.writes == writes && SPI.requests == requests);

  SPI.fastCorruptAddress = 0xff;
  injectFeedback();
  SPI.reg[0x65] = 15;  // Impossible DLC: library fails and leaves RX0IF set.
  motor.service(millis());
  Print failed;
  motor.writeStatus(failed, millis());
  expectText(failed, "\"rxfail\":1");
  expectText(failed, "\"rx\":0");
  assert((SPI.reg[0x2c] & 1) == 1);
  SPI.reg[0x2c] = 0;
  motor.service(millis());
  Print empty;
  motor.writeStatus(empty, millis());
  expectText(empty, "\"rxfail\":1");  // No-message is not a read failure.
}

static void testDiagnosticSnapshotSurvivesServicedPrinting() {
  SPI = SPIClass();
  CubeMarsCan motor(9);
  motor.begin();
  injectFeedback();
  motor.service(millis());
  assert(motor.start(5, 1000, millis()) == nullptr);
  Print out;
  bool changed = false;
  out.beforePrint = [&]() {
    if (changed) return;
    changed = true;
    SPI.reg[0x2d] = 0x15;
    SPI.reg[0x1c] = 128;
    injectFeedback();
    SPI.reg[0x6c] = 50;
    motor.service(millis());
  };
  motor.writeStatus(out, millis());
  assert(changed && !motor.run.active && motor.run.feedback.temperature == 50);
  expectText(out, "\"ce\":1");
  expectText(out, "\"ct\":31");
  expectText(out, "\"ef\":0");
  expectText(out, "\"tec\":0");
  expectText(out, "\"rx\":1");
  expectText(out, "\"data\":\"0000FF82FF9C1F00\"");
  expectText(out, "\"le\":0,\"ls\":0");
  expectText(out, "\"sr\":\"\"");
  expectText(out, "\"sc\":0");
}

static uint32_t numericField(const Print &out, const char *key) {
  const std::string prefix = std::string("\"") + key + "\":";
  const size_t start = out.text.find(prefix);
  assert(start != std::string::npos);
  return uint32_t(std::stoul(out.text.substr(start + prefix.size())));
}

static void testStopEvidenceSurvivesOverflowClearAndRecovery() {
  SPI = SPIClass();
  CubeMarsCan motor(9); motor.begin();
  injectFeedback(); motor.service(millis());
  assert(motor.start(5, 1000, millis()) == nullptr);
  delay(21);
  const uint32_t stoppedAt = millis();
  SPI.reg[0x2d] = 0x40; SPI.reg[0x1c] = 8; SPI.reg[0x1d] = 3;
  motor.service(stoppedAt);
  assert(SPI.reg[0x2d] == 0);  // Overflow cleared, evidence must survive it.
  assert(!motor.run.active);
  Print stopped; motor.writeStatus(stopped, millis());
  expectText(stopped, "\"ef\":0");
  expectText(stopped, "\"le\":64,\"ls\":64,\"lt\":8,\"lr\":3,\"lst\":8,\"lsr\":3");
  expectText(stopped, "\"sr\":\"can_bus_error\",\"se\":64,\"ss\":64,\"st\":8,\"srec\":3,\"sst\":8,\"ssrec\":3");
  assert(numericField(stopped, "sms") == stoppedAt && numericField(stopped, "sq") == 1);
  assert(numericField(stopped, "ec") == 1 && numericField(stopped, "sc") == 1);
  assert(numericField(stopped, "sg") >= 21000);
  assert(numericField(stopped, "mg") >= numericField(stopped, "sg"));
  assert(numericField(stopped, "spi") == 8000000);

  // A healthy poll ends the error episode. Later idle errors update only the
  // last-error observation, not the original active-to-stopped event.
  motor.service(millis());
  SPI.reg[0x2d] = 0x0b; SPI.reg[0x1c] = 0; SPI.reg[0x1d] = 128;
  motor.service(millis()); motor.service(millis());
  Print idle; motor.writeStatus(idle, millis());
  expectText(idle, "\"le\":11,\"ls\":11");
  expectText(idle, "\"sr\":\"can_bus_error\",\"se\":64");
  assert(numericField(idle, "sms") == stoppedAt && numericField(idle, "sq") == 1);
  assert(numericField(idle, "ec") == 2 && numericField(idle, "sc") == 1);
  SPI.reg[0x2d] = SPI.reg[0x1d] = 0;
  injectFeedback(); motor.service(millis());
  motor.release("operator_stop");
  assert(strcmp(motor.start(0, 100, millis()), "can_speed_range") == 0);
  Print recovered; motor.writeStatus(recovered, millis());
  expectText(recovered, "\"sr\":\"can_bus_error\",\"se\":64");
  assert(numericField(recovered, "sc") == 1 && numericField(recovered, "sms") == stoppedAt);
  assert(motor.run.fresh(millis()) && !motor.run.active);

  assert(motor.start(6, 100, millis()) == nullptr);
  Print restarted; motor.writeStatus(restarted, millis());
  expectText(restarted, "\"sr\":\"\",\"se\":0,\"ss\":0");
  assert(numericField(restarted, "sms") == 0 && numericField(restarted, "sg") == 0);
  assert(numericField(restarted, "mg") == 0 && numericField(restarted, "sc") == 1);
  // Last error is retained across explicit new runs for idle diagnosis too.
  expectText(restarted, "\"le\":11,\"ls\":11");
}

static void testErrorClockComparisonAndFirstCause() {
  SPI = SPIClass();
  CubeMarsCan motor(9); motor.begin();
  const auto healthyReads = SPI.reads;
  motor.service(millis());
  // Healthy polling does not add counter or slow error reads to the hot path.
  assert(SPI.reads[0x1c] == healthyReads[0x1c] && SPI.reads[0x1d] == healthyReads[0x1d]);
  assert(SPI.slowReads[0x2d] == 0);
  // A fast-only EFLG artifact remains visible against the slow read even though
  // the unchanged safety policy still treats the first read as a bus error.
  SPI.fastCorruptAddress = 0x2d;
  motor.service(millis());
  Print idle; motor.writeStatus(idle, millis());
  expectText(idle, "\"le\":1,\"ls\":0");
  expectText(idle, "\"sr\":\"\"");
  assert(numericField(idle, "sc") == 0);
  SPI.fastCorruptAddress = 0xff;
  injectFeedback(); motor.service(millis());
  assert(motor.manual(5, 1, 10, millis(), false) == nullptr);

  // Motor fault is encountered before the CAN overflow in the same poll. Its
  // first-cause latch must survive the later reason overwrite and failed stop.
  injectFeedback(); SPI.reg[0x6d] = 4;
  SPI.reg[0x2d] = 0x40; SPI.reg[0x1c] = 128;
  SPI.fastCorruptAddress = 0x1c;
  SPI.holdAbort = true;
  motor.service(millis());
  assert(!motor.run.active && strcmp(motor.run.reason, "can_tx_failed") == 0);
  Print stopped; motor.writeStatus(stopped, millis());
  expectText(stopped, "\"sr\":\"motor_fault\",\"se\":64,\"ss\":64,\"st\":129,\"srec\":0,\"sst\":128,\"ssrec\":0");
  assert(numericField(stopped, "sc") == 1 && numericField(stopped, "sq") == 1);
  SPI.fastCorruptAddress = 0xff; SPI.holdAbort = false;
  injectFeedback(); motor.service(millis());
  assert(motor.manual(5, 1, 11, millis(), false) == nullptr);
  Print newManual; motor.writeStatus(newManual, millis());
  expectText(newManual, "\"sr\":\"\"");
  assert(numericField(newManual, "sc") == 1);
}

static void testAllStopPathsAndServiceGapRollover() {
  const char *expected[] = {"duration_elapsed", "control_timeout", "feedback_stale",
                            "motor_restart", "operator_stop", "can_tx_failed"};
  for (unsigned path = 0; path < 6; ++path) {
    SPI = SPIClass();
    CubeMarsCan motor(9); motor.begin();
    injectFeedback(); motor.service(millis());
    if (path == 1) assert(motor.manual(5, 1, 20, millis(), false) == nullptr);
    else assert(motor.start(5, path == 0 ? 20 : 5000, millis()) == nullptr);
    if (path == 0) { delay(21); injectFeedback(); motor.service(millis()); }
    if (path == 1) { delay(1501); injectFeedback(); motor.service(millis()); }
    if (path == 2) { delay(501); motor.service(millis()); }
    if (path == 3) {
      const uint8_t startup[] = {0, 8, 0x2c, 0, 4, 0xfa, 0xfb, 0xfc, 0xfd};
      memcpy(&SPI.reg[0x61], startup, sizeof(startup)); SPI.reg[0x2c] |= 1;
      motor.service(millis());
    }
    if (path == 4) { SPI.holdAbort = true; motor.release("operator_stop"); }
    if (path == 5) {
      for (unsigned address : {0x30u, 0x40u, 0x50u}) SPI.reg[address] |= 8;
      delay(21); injectFeedback(); motor.service(millis());
    }
    assert(!motor.run.active);
    Print stopped; motor.writeStatus(stopped, millis());
    assert(hexField(stopped, "sr") == expected[path]);
    assert(numericField(stopped, "sc") == 1);
    assert(numericField(stopped, "sq") == (path == 4 ? 2u : 1u));
    assert(numericField(stopped, "se") == 0 && numericField(stopped, "ss") == 0);
  }

  SPI = SPIClass();
  CubeMarsCan motor(9); motor.begin();
  SPI.clockUs = 0xfffff000UL;
  injectFeedback(); motor.service(millis());
  assert(motor.manual(5, 1, 30, millis(), false) == nullptr);
  SPI.clockUs += 10000;  // Cross the 32-bit micros boundary, not millis.
  injectFeedback(); motor.service(millis());
  Print active; motor.writeStatus(active, millis());
  const uint32_t gap = numericField(active, "mg");
  assert(gap >= 10000 && gap < 20000);
  assert(motor.manual(6, 2, 30, millis(), true) == nullptr);
  assert(motor.renew(30, millis()) == nullptr);
  Print updated; motor.writeStatus(updated, millis());
  assert(numericField(updated, "mg") == gap);  // Updates/renewals don't reset it.
  motor.release("operator_stop");
}

static void testManualAdapterLeaseAndTelemetry() {
  SPI = SPIClass();
  CubeMarsCan motor(9); motor.begin();
  injectFeedback(); motor.service(millis());
  assert(motor.manual(5, 1, 123, millis(), false) == nullptr);
  expectQueued(3, {0xff, 0xff, 0xfb, 0x14});  // Start at measured -10 RPM.
  assert(motor.commandSequence == 1 && motor.requestedRpm == 5);
  Print initial; motor.writeStatus(initial, millis());
  expectText(initial, "\"cm\":123"); expectText(initial, "\"cleasems\":1500");
  expectText(initial, "\"cramp\":1"); expectText(initial, "\"cap\":-1260");
  expectText(initial, "\"crmax\":80"); expectText(initial, "\"crdefault\":80");
  assert(strcmp(motor.manual(6, 1, 124, millis(), false), "can_manual_active") == 0);
  assert(strcmp(motor.manual(6, 1, 124, millis(), true), "can_manual_token") == 0);
  assert(strcmp(motor.renew(124, millis()), "can_manual_token") == 0);
  assert(motor.commandSequence == 1 && motor.requestedRpm == 5);
  assert(motor.manual(6, 2, 123, millis(), true) == nullptr);
  assert(motor.commandSequence == 2 && motor.requestedRpm == 6);
  assert(motor.renew(123, millis()) == nullptr);
  assert(motor.commandSequence == 3);

  // CAN feedback remains healthy, but the browser lease is not renewed.
  for (unsigned i = 0; i < 3; ++i) {
    delay(400); injectFeedback(); motor.service(millis());
    for (unsigned address : {0x30u, 0x40u, 0x50u}) SPI.reg[address] &= ~8;
  }
  delay(301); injectFeedback(); motor.service(millis());
  assert(!motor.run.active && motor.run.manualToken == 0);
  assert(strcmp(motor.run.reason, "control_timeout") == 0);
  expectQueued(1, {0, 0, 0, 0});
  assert(strcmp(motor.renew(123, millis()), "can_manual_inactive") == 0);
  assert(strcmp(motor.manual(6, 2, 123, millis(), true), "can_manual_inactive") == 0);
  assert(strcmp(motor.manual(6, 2, 123, millis(), false), "can_manual_token") == 0);
  assert(motor.commandSequence == 3);
  Print expired; motor.writeStatus(expired, millis());
  expectText(expired, "\"cm\":0"); expectText(expired, "\"cramp\":0");
  expectText(expired, "\"cap\":0");
  assert(motor.manual(1, 1, 124, millis(), false) == nullptr);
  motor.release("operator_stop");
  expectQueued(1, {0, 0, 0, 0});
  assert(motor.commandSequence == 5 && !motor.run.manualToken && !motor.requestedRpm);
  assert(motor.manual(1, 1, 125, millis(), false) == nullptr);
  for (unsigned address : {0x30u, 0x40u, 0x50u}) SPI.reg[address] |= 8;
  delay(21); injectFeedback(); motor.service(millis());  // All TX buffers busy.
  assert(!motor.run.active && !motor.run.manualToken);
  assert(strcmp(motor.run.reason, "can_tx_failed") == 0);
  expectQueued(1, {0, 0, 0, 0});  // Abort older speed frames, then release.
  assert(strcmp(motor.renew(125, millis()), "can_manual_inactive") == 0);
}

int main() {
  testActualDependencyAndInitialization();
  testInitializationFailures();
  testExtendedCommandsAndAbort();
  testObservedStartupStopsWithoutAutomaticRestart();
  testDiagnosticsAreReadOnlyAndPreserveEvidence();
  testDiagnosticRegisterReadIntegrityAndReceiveFailures();
  testDiagnosticSnapshotSurvivesServicedPrinting();
  testManualAdapterLeaseAndTelemetry();
  testStopEvidenceSurvivesOverflowClearAndRecovery();
  testErrorClockComparisonAndFirstCause();
  testAllStopPathsAndServiceGapRollover();
}

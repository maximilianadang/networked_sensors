#pragma once
#include <mcp2515.h>  // autowp-mcp2515 1.3.1; only the CubeMars build needs it.
#include "cubemars_protocol.h"

// Poll from the main loop, never an ISR. Both this driver and Ethernet use SPI
// transactions. INT is unnecessary. CS/RTC/Ethernet deselection belongs to setup.
class CubeMarsCan {
 public:
  CubeMars::RunState run;
  bool initialized = false;
  int32_t requestedRpm = 0;   // Last accepted request, retained after timed expiry.
  uint32_t commandSequence = 0;

  explicit CubeMarsCan(uint8_t cs) : cs_(cs), controller_(cs, SPI_CLOCK, &SPI) {}

  void begin() {
    SPI.begin();
    static_assert(CubeMars::BITRATE == 500000UL || CubeMars::BITRATE == 1000000UL,
                  "Add the MCP2515 mapping for this CAN bitrate");
    bool ok = controller_.reset() == MCP2515::ERROR_OK &&
        controller_.setBitrate(CubeMars::BITRATE == 500000UL ? CAN_500KBPS : CAN_1000KBPS,
                               MCP_16MHZ) == MCP2515::ERROR_OK;
    // RXF0 admits node zero because this motor emits 0x2C00 before applying its
    // node ID at startup. Software accepts only that exact startup marker from it.
    for (uint8_t i = 0; ok && i < 2; ++i)
      ok = controller_.setFilterMask(MCP2515::MASK(i), true, 0xff) == MCP2515::ERROR_OK;
    for (uint8_t i = 0; ok && i < 6; ++i)
      ok = controller_.setFilter(MCP2515::RXF(i), true, i == 0 ? 0 : CubeMars::NODE_ID) == MCP2515::ERROR_OK;
    // A frame that loses arbitration or ACK must not be retried indefinitely
    // after its timed run has expired. We refresh deliberately every 20 ms.
    // autowp 1.3.1's setNormalOneShotMode compares CANSTAT.OPMOD (bits 7:5)
    // against 0x08, so it always reports failure. Verify normal mode through the
    // supported API, then set/read CANCTRL.OSM separately (it is not an OPMOD bit).
    initialized = ok && controller_.setNormalMode() == MCP2515::ERROR_OK;
    if (initialized) {
      modifyControl(0x08, 0x08);
      initialized = (readControl() & 0xe8) == 0x08;
    }
    run.stop(initialized ? "boot" : "can_init_failed");
    lastTransmit_ = millis() - CubeMars::REFRESH_MS;
  }

  const char *start(int32_t rpm, uint32_t duration, uint32_t now) {
    service(now);
    if (!initialized) return "can_unavailable";
    if (!healthy_) return "can_bus_error";
    const char *error = run.start(rpm, duration, now);
    if (error) return error;
    return queueRun(rpm, now, true);
  }

  const char *manual(int32_t rpm, int32_t ramp, uint32_t token, uint32_t now, bool update) {
    service(now);
    if (!initialized) return "can_unavailable";
    if (!healthy_) return "can_bus_error";
    const char *error = update ? run.updateManual(rpm, ramp, token, now)
                               : run.startManual(rpm, ramp, token, now);
    if (error) return error;
    return queueRun(rpm, now, !update);
  }

  const char *renew(uint32_t token, uint32_t now) {
    service(now);
    if (!initialized) return "can_unavailable";
    if (!healthy_) return "can_bus_error";
    const char *error = run.renewManual(token, now);
    if (!error) ++commandSequence;
    return error;
  }

 private:
  const char *queueRun(int32_t rpm, uint32_t now, bool newRun) {
    // Abort older queued commands before a replacement setpoint is sent.
    if (!abortPending() || !send(3, run.appliedErpm)) {
      stop("can_tx_failed");
      return "can_tx_failed";
    }
    lastTransmit_ = now;
    requestedRpm = rpm;
    ++commandSequence;
    if (newRun) {
      stopEvent_ = StopEvent();
      maxServiceGapUs_ = serviceGapUs_ = 0;
      lastServiceUs_ = micros();
      seenService_ = true;
    }
    return nullptr;  // Queued successfully, NOT proof of actual shaft motion.
  }

 public:
  void release(const char *why, bool explicitCommand = true) {
    requestedRpm = 0;
    if (explicitCommand) ++commandSequence;
    stop(why);
  }

  void stop(const char *why) {
    if (run.active) captureStop(why, millis());
    run.stop(why);
    if (initialized) {
      // Command 1 at 0 mA requests torque release/coast, not active braking.
      // It remains possible for a disconnected motor to miss this command.
      if (!abortPending() || !send(1, 0)) run.reason = "can_tx_failed";
      lastTransmit_ = millis();
    }
  }

  void service(uint32_t now) {
    if (!initialized) return;
    const uint32_t serviceUs = micros();
    serviceGapUs_ = seenService_ ? uint32_t(serviceUs - lastServiceUs_) : 0;
    if (serviceGapUs_ > maxServiceGapUs_) maxServiceGapUs_ = serviceGapUs_;
    lastServiceUs_ = serviceUs; seenService_ = true;
    const bool wasActive = run.active;
    const char *firstStopReason = nullptr;
    struct can_frame frame;
    // Bounded drain: the MCP2515 has two RX buffers; service can be called often.
    for (uint8_t i = 0; i < 8; ++i) {
      const MCP2515::ERROR result = controller_.readMessage(&frame);
      if (result != MCP2515::ERROR_OK) {
        if (result != MCP2515::ERROR_NOMSG) ++rawRx_.readErrors;
        break;
      }
      ++rawRx_.count; rawRx_.seen = true; rawRx_.last = frame;
      CubeMars::Feedback value;
      CubeMars::Reply reply = CubeMars::decodeReply(
          frame.can_id & CAN_EFF_MASK, frame.can_id & CAN_EFF_FLAG,
          frame.can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG), frame.can_dlc, frame.data, value);
      const bool active = run.active;
      if (reply == CubeMars::FEEDBACK) run.receive(value, now);
      else if (reply == CubeMars::RESTART) run.invalidate("motor_restart");
      if (active && !run.active) firstStopReason = run.reason;
    }
    const uint8_t errors = controller_.getErrorFlags();
    healthy_ = errors == 0;
    ErrorSample sample;
    if (errors) {
      sample = readErrorSample(errors);
      observeError(sample, now);
      if (run.active) firstStopReason = "can_bus_error";
      run.invalidate("can_bus_error");
    } else errorEpisode_ = false;
    run.service(now);
    const bool stopped = wasActive && !run.active;
    if (stopped) {
      if (!errors) sample = readErrorSample(errors);
      captureStop(firstStopReason ? firstStopReason : run.reason, now, &sample);
    }
    // Preserve the original error evidence before clearing sticky RX overflow.
    if (errors) controller_.clearRXnOVRFlags();
    if (stopped) {
      stop(run.reason);
      return;
    }
    if (uint32_t(now - lastTransmit_) < CubeMars::REFRESH_MS) return;
    lastTransmit_ = now;
    if (run.active) {
      if (!send(3, run.appliedErpm)) stop("can_tx_failed");
    } else {
      // Continue requesting release while idle, including after a transient bus
      // failure. A recovered bus never resumes the previous velocity command.
      if (abortPending()) send(1, 0);
    }
  }

  void writeStatus(Print &out, uint32_t now) {
    // The output wrapper services CAN during printing. Snapshot every changing
    // field before the first CAN byte so an expiry cannot split one report.
    const CubeMars::RunState snapshot = run;
    const uint32_t sequence = commandSequence;
    const int32_t requested = requestedRpm;
    const RawRx raw = rawRx_;
    const ErrorSample lastError = lastError_;
    const StopEvent stopped = stopEvent_;
    const uint32_t errorAt = errorAt_, errorCount = errorCount_, stopCount = stopCount_;
    const uint32_t maxGap = maxServiceGapUs_;
    // Read-only register evidence, captured before serviced printing can mutate
    // state. reg: 28..2F, TEC/REC (1C/1D), TXBnCTRL (30/40/50), RXBnCTRL (60/70).
    // slow: 28..2F at 1 MHz. Compare fixed configuration only: counters, interrupt
    // flags and CANSTAT interrupt codes can legitimately change between reads.
    // A burst is sequential, not an atomic latch of the CAN hardware state.
    // Masks/filters read as zero outside configuration mode, so omit them.
    uint8_t registers[15] = {}, slow[8] = {};
    if (initialized) {
      readRegisters(0x28, registers, 8);
      readRegisters(0x1c, registers + 8, 2);
      for (uint8_t i = 0; i < 5; ++i)
        readRegisters(0x30 + 0x10 * i, registers + 10 + i, 1);
      readRegisters(0x28, slow, sizeof(slow), 1000000UL);
    }
    const int flags = initialized ? registers[5] : -1;
    const int txErrors = initialized ? registers[8] : -1;
    const int rxErrors = initialized ? registers[9] : -1;
    out.print(F(",\"cb\":")); out.print(initialized);
    out.print(F(",\"cid\":")); out.print(CubeMars::NODE_ID);
    out.print(F(",\"cbr\":")); out.print(CubeMars::BITRATE);
    out.print(F(",\"cmax\":")); out.print(CubeMars::MAX_RPM);
    out.print(F(",\"cdmax\":")); out.print(CubeMars::MAX_DURATION_MS);
    out.print(F(",\"cstale\":")); out.print(CubeMars::FEEDBACK_TIMEOUT_MS);
    out.print(F(",\"cq\":")); out.print(sequence);
    out.print(F(",\"cr\":")); out.print(requested);
    out.print(F(",\"ce\":")); out.print(snapshot.active);
    out.print(F(",\"cm\":")); out.print(snapshot.manualToken);
    out.print(F(",\"cleasems\":")); out.print(CubeMars::MANUAL_LEASE_MS);
    out.print(F(",\"cramp\":")); out.print(snapshot.rampRate);
    out.print(F(",\"cap\":")); out.print(snapshot.appliedErpm);
    out.print(F(",\"crmax\":")); out.print(CubeMars::MAX_RAMP_RPM_S);
    out.print(F(",\"crdefault\":")); out.print(CubeMars::DEFAULT_RAMP_RPM_S);
    out.print(F(",\"ca\":"));
    if (snapshot.seenFeedback) out.print(uint32_t(now - snapshot.feedbackAt));
    else out.print(-1);
    out.print(F(",\"cv\":")); out.print(snapshot.feedback.erpm);
    out.print(F(",\"ci\":")); out.print(snapshot.feedback.centiamps);
    out.print(F(",\"ct\":")); out.print(snapshot.feedback.temperature);
    out.print(F(",\"cf\":")); out.print(snapshot.feedback.fault);
    out.print(F(",\"cx\":\"")); out.print(snapshot.reason);
    out.print(F("\",\"cd\":")); out.print(snapshot.remaining(now));
    out.print(F(",\"cdiag\":{\"ef\":")); out.print(flags);
    out.print(F(",\"tec\":")); out.print(txErrors);
    out.print(F(",\"rec\":")); out.print(rxErrors);
    out.print(F(",\"rxfail\":")); out.print(raw.readErrors);
    out.print(F(",\"le\":")); out.print(lastError.flags);
    out.print(F(",\"ls\":")); out.print(lastError.slowFlags);
    out.print(F(",\"lt\":")); out.print(lastError.tec);
    out.print(F(",\"lr\":")); out.print(lastError.rec);
    out.print(F(",\"lst\":")); out.print(lastError.slowTec);
    out.print(F(",\"lsr\":")); out.print(lastError.slowRec);
    out.print(F(",\"let\":")); out.print(errorAt);
    out.print(F(",\"ec\":")); out.print(errorCount);
    out.print(F(",\"sr\":\"")); out.print(stopped.reason);
    out.print(F("\",\"se\":")); out.print(stopped.sample.flags);
    out.print(F(",\"ss\":")); out.print(stopped.sample.slowFlags);
    out.print(F(",\"st\":")); out.print(stopped.sample.tec);
    out.print(F(",\"srec\":")); out.print(stopped.sample.rec);
    out.print(F(",\"sst\":")); out.print(stopped.sample.slowTec);
    out.print(F(",\"ssrec\":")); out.print(stopped.sample.slowRec);
    out.print(F(",\"sms\":")); out.print(stopped.at);
    out.print(F(",\"sq\":")); out.print(stopped.sequence);
    out.print(F(",\"sc\":")); out.print(stopCount);
    out.print(F(",\"mg\":")); out.print(maxGap);
    out.print(F(",\"sg\":")); out.print(stopped.gapUs);
    out.print(F(",\"spi\":")); out.print(SPI_CLOCK);
    out.print(F(",\"reg\":\""));
    if (initialized) writeHex(out, registers, sizeof(registers));
    out.print(F("\",\"slow\":\""));
    if (initialized) writeHex(out, slow, sizeof(slow));
    out.print(F("\""));
    out.print(F(",\"rx\":")); out.print(raw.count);
    out.print(F(",\"id\":"));
    if (raw.seen) out.print(raw.last.can_id & CAN_EFF_MASK); else out.print(-1);
    out.print(F(",\"ext\":")); out.print(bool(raw.last.can_id & CAN_EFF_FLAG));
    out.print(F(",\"rtr\":")); out.print(bool(raw.last.can_id & CAN_RTR_FLAG));
    out.print(F(",\"dlc\":")); out.print(raw.last.can_dlc);
    out.print(F(",\"data\":\""));
    if (!(raw.last.can_id & CAN_RTR_FLAG))
      writeHex(out, raw.last.data, raw.last.can_dlc < 8 ? raw.last.can_dlc : 8);
    out.print(F("\"}"));
  }

 private:
  struct RawRx {
    uint32_t count = 0;
    uint32_t readErrors = 0;
    bool seen = false;
    struct can_frame last = {};
  } rawRx_;
  // Snapshots are read sequentially, not atomically: changing counters can
  // differ between clock speeds without proving an SPI fault.
  struct ErrorSample {
    uint8_t flags = 0, tec = 0, rec = 0;
    uint8_t slowFlags = 0, slowTec = 0, slowRec = 0;
  } lastError_;
  struct StopEvent {
    const char *reason = "";
    ErrorSample sample;
    uint32_t at = 0, sequence = 0, gapUs = 0;
  } stopEvent_;
  // Error episodes end on a zero-EFLG service poll. Last-error evidence and
  // lifetime counts survive new runs; only the first-stop latch/gap reset there.
  uint32_t errorAt_ = 0, errorCount_ = 0, stopCount_ = 0;
  uint32_t lastServiceUs_ = 0, serviceGapUs_ = 0, maxServiceGapUs_ = 0;
  bool errorEpisode_ = false, seenService_ = false;

  ErrorSample readErrorSample(uint8_t flags) {
    ErrorSample sample;
    sample.flags = flags;
    uint8_t counters[2];
    readRegisters(0x1c, counters, 2);
    sample.tec = counters[0]; sample.rec = counters[1];
    readRegisters(0x2d, &sample.slowFlags, 1, 1000000UL);
    readRegisters(0x1c, counters, 2, 1000000UL);
    sample.slowTec = counters[0]; sample.slowRec = counters[1];
    return sample;
  }
  void observeError(const ErrorSample &sample, uint32_t now) {
    lastError_ = sample; errorAt_ = now;
    if (!errorEpisode_) ++errorCount_;
    errorEpisode_ = true;
  }
  void captureStop(const char *why, uint32_t now, const ErrorSample *sample = nullptr) {
    ++stopCount_;
    if (*stopEvent_.reason) return;  // Idle recovery/release never replaces it.
    ErrorSample current;
    if (!sample) {
      current = readErrorSample(controller_.getErrorFlags());
      if (current.flags) observeError(current, now);
      sample = &current;
    }
    stopEvent_.reason = why; stopEvent_.sample = *sample;
    stopEvent_.at = now; stopEvent_.sequence = commandSequence;
    // The entry-to-entry gap preceding the latest service, including rollover.
    stopEvent_.gapUs = serviceGapUs_;
  }
  static constexpr uint32_t SPI_CLOCK = 8000000UL;
  uint8_t cs_;
  MCP2515 controller_;
  bool healthy_ = true;
  uint32_t lastTransmit_ = 0;

  static void writeHex(Print &out, const uint8_t *bytes, uint8_t count) {
    const char hex[] = "0123456789ABCDEF";
    for (uint8_t i = 0; i < count; ++i) {
      out.print(hex[bytes[i] >> 4]); out.print(hex[bytes[i] & 0x0f]);
    }
  }

  bool send(uint8_t function, int32_t value) {
    struct can_frame frame = {};
    frame.can_id = CAN_EFF_FLAG | CubeMars::commandId(function);
    frame.can_dlc = 4;
    CubeMars::encodeInt32(value, frame.data);
    return controller_.sendMessage(&frame) == MCP2515::ERROR_OK;
  }

  void modifyControl(uint8_t mask, uint8_t value) {
    // MCP2515 BIT MODIFY, CANCTRL. The library lacks a public register/abort API.
    SPI.beginTransaction(SPISettings(SPI_CLOCK, MSBFIRST, SPI_MODE0));
    digitalWrite(cs_, LOW);
    SPI.transfer(0x05); SPI.transfer(0x0f); SPI.transfer(mask); SPI.transfer(value);
    digitalWrite(cs_, HIGH);
    SPI.endTransaction();
  }
  uint8_t readControl() {
    uint8_t value;
    readRegisters(0x0f, &value, 1);
    return value;
  }
  void readRegisters(uint8_t address, uint8_t *bytes, uint8_t count,
                     uint32_t clock = SPI_CLOCK) {
    SPI.beginTransaction(SPISettings(clock, MSBFIRST, SPI_MODE0));
    digitalWrite(cs_, LOW);
    SPI.transfer(0x03); SPI.transfer(address);
    for (uint8_t i = 0; i < count; ++i) bytes[i] = SPI.transfer(0);
    digitalWrite(cs_, HIGH);
    SPI.endTransaction();
  }
  bool abortPending() {
    modifyControl(0x10, 0x10);
    const uint32_t started = micros();
    // Read-status bits 2/4/6 are TXREQ for TXB0/1/2. A frame already on the wire
    // may finish; no pending velocity frame may follow our release command.
    while (controller_.getStatus() & 0x54) {
      if (uint32_t(micros() - started) >= 1000) return false;
    }
    modifyControl(0x10, 0);
    return true;
  }
};

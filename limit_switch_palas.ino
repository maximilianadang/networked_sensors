#include <AccelStepper.h>

AccelStepper stepper(AccelStepper::DRIVER, 3, 2);  // STEP=D3, DIR=D2

// --- Mechanism calibration ---
// CALIBRATED pulse conversion (2026-07-13): the mechanism advances
// 0.00396875 mm for each pulse accepted by the DM542T PUL input. This must be
// travel per DRIVER PULSE, not travel per native motor full-step; changing the
// DM542T microstep switches changes this conversion. The reciprocal is
// 251.96850394 pulses/mm. Keep this exact physical quantity alongside the
// reciprocal used by the command engine so the unit convention is explicit.
const float MM_PER_DRIVER_PULSE = 0.00396875;
const float STEPS_PER_MM = 1.0 / MM_PER_DRIVER_PULSE;

// PHYSICAL STROKE CALIBRATION (2026-07-13): an external DRO measured
// 137.18 mm between the two installed magnetic-limit trip positions:
//   D8 end: Normal mapping, D5 Reverse/LOW, D8 LOW, D6 HIGH
//   D6 end: Normal mapping, D5 Forward/HIGH, D6 LOW, D8 HIGH
// Home optionally approaches D8 and zeroes the diagnostic pulse counter. The
// measured stroke bounds one relative command and the Home search, but no
// absolute open-loop coordinate or software margin authorizes motion. D6/D8
// remain the directional travel stops if later DRO testing refines pulses/mm.
const float MEASURED_LIMIT_TO_LIMIT_TRAVEL_MM = 137.18;
const long MAX_TRAVEL_STEPS = 34565L;       // nearest pulse to 137.18 mm
const long HOME_SEARCH_MARGIN_STEPS = 1260L;  // approximately 5 mm
const long MAX_HOME_SEARCH_STEPS =
    MAX_TRAVEL_STEPS + HOME_SEARCH_MARGIN_STEPS;

// --- Fixed motion settings ---
// Acceleration is intentionally a firmware setting rather than an operator
// field. The page controls speed and distance, while this conservative value
// stays reviewable in source. The DM542T has already run smoothly with the
// existing wiring; setMinPulseWidth(5) preserves a comfortably wide STEP pulse.
const long MIN_SPEED_SPS = 25L;        // nearest pulse rate to 0.1 mm/s
const long MAX_SPEED_SPS = 2520L;      // nearest pulse rate to 10.0 mm/s
const long DEFAULT_SPEED_SPS = 378L;   // nearest pulse rate to 1.5 mm/s
const long HOME_SPEED_SPS = 378L;      // fixed conservative homing speed
const float FIXED_ACCELERATION_SPS2 = 1260.0;  // approximately 5 mm/s^2
long targetSpeedSps = DEFAULT_SPEED_SPS;

// --- Electrical direction mapping ---
// Logical positive always means toward D6 and logical negative always means
// toward D8. directionSign changes only the electrical DIR polarity. It never
// changes coordinates, limit meanings, or the D5 authorization direction.
const int DEFAULT_DIRECTION_SIGN = 1;
int directionSign = DEFAULT_DIRECTION_SIGN;

// --- Switch pins ---
const int PIN_RUN = 4;
const int PIN_DIR = 5;
const int PIN_LIMIT_POS = 6;
const int PIN_LIMIT_NEG = 8;  // D7 is reserved by the Yún Linux handshake.

// All four inputs use INPUT_PULLUP. The installed magnetic switches are
// passive normally-open contacts to GND: open/clear is HIGH and magnet-active
// is LOW. A broken limit wire therefore looks clear and is not fail-safe.
const int POS_LIMIT_ACTIVE_LEVEL = LOW;
const int NEG_LIMIT_ACTIVE_LEVEL = LOW;

// --- Control modes ---
// LOCAL_VELOCITY preserves the original switch behavior:
//   D4 LOW runs continuously; D4 HIGH stops; D5 chooses Forward/Reverse.
// WEB_POSITION gives the switches deliberately different roles:
//   D4 LOW arms motion and D4 HIGH immediately aborts it.
//   The page supplies a positive magnitude and D5 selects Forward/Reverse. The
//   supervisor resolves that to a signed wire command; firmware checks D5 and
//   aborts immediately if it changes during motion.
// Mode changes are accepted only while D4 is OFF/HIGH and motion is stopped.
// The board boots in LOCAL_VELOCITY so a reset cannot reinterpret an already
// closed D4 switch as permission for a queued web command.
enum ControlMode : byte {
  LOCAL_VELOCITY = 0,
  WEB_POSITION = 1,
};

enum MotionState : byte {
  STATE_LOCAL_STOPPED = 0,
  STATE_LOCAL_MOVING = 1,
  STATE_WEB_UNHOMED = 2,
  STATE_HOMING = 3,
  STATE_WEB_MOVING = 4,
  STATE_WEB_READY = 5,
  STATE_WEB_COMPLETED = 6,
  STATE_WEB_ABORTED = 7,
  STATE_LIMIT_BLOCKED = 8,
  STATE_EMERGENCY_STOP = 9,
};

ControlMode controlMode = LOCAL_VELOCITY;
MotionState motionState = STATE_LOCAL_STOPPED;
// Legacy status flag: D8 has zeroed the diagnostic pulse counter. It is never
// consulted to authorize motion.
bool positionHomed = false;
bool d4OffObservedSinceBoot = false;
bool activeWebMotion = false;
bool homingMotion = false;
int activeLogicalDirection = 0;
unsigned int activeCommandId = 0;
long reportedTargetSteps = 0;
const char *motionReason = "run_off";

bool positiveLimitLatched = false;
bool negativeLimitLatched = false;
bool emergencyStopLatched = false;

// --- Mutating-transport ownership ---
// USB and the Yún Linux network bridge share one command engine, but may not
// configure/start motion concurrently. A mutating command claims its transport.
// The claim releases only after motion is stopped, D4 is physically OFF, and
// the owner has been idle for two seconds. Stop and software E-STOP commands
// are intentionally accepted from either transport and never steal ownership.
enum CommandTransport : byte {
  TRANSPORT_NONE = 0,
  TRANSPORT_USB = 1,
  TRANSPORT_NETWORK = 2,
};
const unsigned long OWNER_IDLE_RELEASE_MS = 2000UL;
CommandTransport controlOwner = TRANSPORT_NONE;
unsigned long ownerLastActivityMs = 0UL;
bool lastCommandAccepted = true;
const char *lastCommandError = "none";

// --- USB command/status contract ---
// Commands are newline-terminated ASCII:
//   V1 S25..2520                 configure speed while D4 is OFF
//   V1 D0|1                      normal/inverted DIR while D4 is OFF
//   V1 M0|1                      Local Velocity / Web Position, D4 OFF
//   V1 H                         home to D8 in Web Position mode
//   V1 G<signed_steps>,<sps>,<id> bounded relative move in Web Position mode
//   V1 X                         immediate Web Position abort
//   V1 E1                        latch software E-STOP in either control mode
//   V1 E0                        reset E-STOP while D4 is OFF and motion stopped
// Home/move require D4 ON/LOW. Home additionally requires D5 Reverse/LOW;
// signed internal moves require D5 to match their sign. Home is optional and is
// never a motion prerequisite. No command is retained over reset, and boot
// always returns to stopped Local Velocity mode.
//
// Compact status keeps Serial work bounded. In addition to the established
// fields, m is control mode, h is homed, mv is moving, st is MotionState,
// a means D4 OFF has been observed since boot, p/g are current/target steps,
// c is the active command number, and e is the software E-STOP latch.
const unsigned long STATUS_HEARTBEAT_MS = 1000UL;
const unsigned long STATUS_MOTION_MS = 100UL;
unsigned long statusSequence = 0;
bool statusDirty = true;

// --- Emitted STEP instrumentation ---
// AccelStepper's speed() reports its scheduled rate, not how often run()/
// runSpeed() actually reached the STEP output. Measure the change in its
// emitted-step counter over a fixed window so the dashboard can distinguish a
// requested rate from the pulses the cooperative loop really generated. This
// is still an open-loop electrical measurement: it proves D3 pulse attempts,
// not DM542T acceptance or physical piston travel.
const unsigned long PULSE_RATE_WINDOW_US = 250000UL;
long measuredPulseRateSps = 0L;
long pulseRateWindowStartPosition = 0L;
unsigned long pulseRateWindowStartUs = 0UL;
bool pulseRateWindowMoving = false;

const byte USB_COMMAND_BUFFER_SIZE = 48;
char usbCommandBuffer[USB_COMMAND_BUFFER_SIZE];
byte usbCommandLength = 0;

// Linux-side transport. The AR9331 service owns /dev/ttyATH0 and the ATmega
// uses Serial1 at 115200 baud. Both RX and TX are bounded per loop. Outgoing
// JSON is drained only into currently available UART capacity so a missing or
// restarting Linux service cannot block STEP generation.
const byte NETWORK_COMMAND_BUFFER_SIZE = USB_COMMAND_BUFFER_SIZE;
char networkCommandBuffer[NETWORK_COMMAND_BUFFER_SIZE];
byte networkCommandLength = 0;
char networkTxActive[256];
unsigned int networkTxActiveLength = 0;
unsigned int networkTxActiveOffset = 0;
char networkTxPendingAck[80];
bool networkAckPending = false;

void setElectricalDirectionMapping() {
  stepper.setPinsInverted(directionSign == -1, false, false);
}

void reportLimitLevels(int positiveRaw, int negativeRaw) {
  Serial.print(F("Limit inputs: D6="));
  Serial.print(positiveRaw == HIGH ? F("HIGH (open)") : F("LOW (closed)"));
  Serial.print(F(", D8="));
  Serial.println(negativeRaw == HIGH ? F("HIGH (open)") : F("LOW (closed)"));
}

void stopStepperImmediately() {
  long current = stepper.currentPosition();
  // setCurrentPosition sets current and target to the same value and clears
  // AccelStepper's internal speed. It therefore aborts without emitting the
  // deceleration pulses that stop() would schedule.
  stepper.setCurrentPosition(current);
  reportedTargetSteps = current;
  activeWebMotion = false;
  homingMotion = false;
  activeLogicalDirection = 0;
}

void abortWebMotion(MotionState state, const char *reason) {
  stopStepperImmediately();
  motionState = state;
  motionReason = reason;
  statusDirty = true;
}

void establishD8Reference() {
  stepper.setCurrentPosition(0L);
  reportedTargetSteps = 0L;
  positionHomed = true;
}

bool parseLongExact(char *text, long *value) {
  if (text == NULL || *text == '\0') return false;
  char *end = NULL;
  long parsed = strtol(text, &end, 10);
  if (end == text || *end != '\0') return false;
  *value = parsed;
  return true;
}

bool stoppedWithD4Off() {
  return digitalRead(PIN_RUN) == HIGH && !activeWebMotion;
}

void rejectCommand(const __FlashStringHelper *message) {
  lastCommandAccepted = false;
  lastCommandError = "rejected";
  Serial.print(F("Command rejected: "));
  Serial.println(message);
  statusDirty = true;
}

void releaseExpiredOwner() {
  if (controlOwner != TRANSPORT_NONE && stoppedWithD4Off() &&
      millis() - ownerLastActivityMs >= OWNER_IDLE_RELEASE_MS) {
    controlOwner = TRANSPORT_NONE;
    statusDirty = true;
  }
}

bool claimTransport(CommandTransport transport) {
  releaseExpiredOwner();
  if (controlOwner != TRANSPORT_NONE && controlOwner != transport) {
    lastCommandAccepted = false;
    lastCommandError = controlOwner == TRANSPORT_USB
        ? "owned_by_usb"
        : "owned_by_network";
    Serial.println(controlOwner == TRANSPORT_USB
        ? F("Command rejected: control is owned by USB.")
        : F("Command rejected: control is owned by network."));
    statusDirty = true;
    return false;
  }
  controlOwner = transport;
  ownerLastActivityMs = millis();
  statusDirty = true;
  return true;
}

void startHomeCommand() {
  if (emergencyStopLatched) {
    rejectCommand(F("reset the software E-STOP first."));
    return;
  }
  if (controlMode != WEB_POSITION) {
    rejectCommand(F("select Web Position mode first."));
    return;
  }
  if (activeWebMotion) {
    rejectCommand(F("stepper is busy."));
    return;
  }
  if (digitalRead(PIN_RUN) != LOW) {
    rejectCommand(F("turn D4 ON to arm Home."));
    return;
  }
  if (!d4OffObservedSinceBoot) {
    rejectCommand(F("cycle D4 OFF before arming Home."));
    return;
  }
  if (digitalRead(PIN_DIR) != LOW) {
    rejectCommand(F("D5 must authorize Reverse for Home."));
    return;
  }

  if (digitalRead(PIN_LIMIT_NEG) == NEG_LIMIT_ACTIVE_LEVEL) {
    establishD8Reference();
    motionState = STATE_WEB_READY;
    motionReason = "home_complete";
    negativeLimitLatched = true;
    statusDirty = true;
    return;
  }

  activeCommandId++;
  if (activeCommandId == 0) activeCommandId = 1;
  activeWebMotion = true;
  homingMotion = true;
  activeLogicalDirection = -1;
  positiveLimitLatched = false;
  motionState = STATE_HOMING;
  motionReason = "none";
  stepper.setMaxSpeed((float)HOME_SPEED_SPS);
  stepper.setAcceleration(FIXED_ACCELERATION_SPS2);
  stepper.moveTo(stepper.currentPosition() - MAX_HOME_SEARCH_STEPS);
  reportedTargetSteps = 0L;
  statusDirty = true;
  Serial.println(F("Home accepted: approaching D8 at fixed 1.5 mm/s."));
}

void startMoveCommand(long deltaSteps, long speedSps, long commandId) {
  if (emergencyStopLatched) {
    rejectCommand(F("reset the software E-STOP first."));
    return;
  }
  if (controlMode != WEB_POSITION) {
    rejectCommand(F("select Web Position mode first."));
    return;
  }
  if (activeWebMotion) {
    rejectCommand(F("stepper is busy."));
    return;
  }
  if (digitalRead(PIN_RUN) != LOW) {
    rejectCommand(F("turn D4 ON to arm the move."));
    return;
  }
  if (!d4OffObservedSinceBoot) {
    rejectCommand(F("cycle D4 OFF before arming the move."));
    return;
  }
  if (deltaSteps == 0 || labs(deltaSteps) > MAX_TRAVEL_STEPS) {
    rejectCommand(F("distance exceeds one measured stroke."));
    return;
  }
  if (speedSps < MIN_SPEED_SPS || speedSps > MAX_SPEED_SPS) {
    rejectCommand(F("speed must be 25..2520 steps/s."));
    return;
  }
  if (commandId < 1 || commandId > 65535L) {
    rejectCommand(F("command id must be 1..65535."));
    return;
  }

  int requestedDirection = deltaSteps > 0 ? 1 : -1;
  bool d5AuthorizesPositive = digitalRead(PIN_DIR) == HIGH;
  if ((requestedDirection > 0) != d5AuthorizesPositive) {
    rejectCommand(F("D5 does not authorize the signed direction."));
    return;
  }
  if ((requestedDirection > 0 && positiveLimitLatched) ||
      (requestedDirection < 0 && negativeLimitLatched)) {
    rejectCommand(F("the requested direction is limit-blocked."));
    return;
  }

  if (requestedDirection < 0) positiveLimitLatched = false;
  if (requestedDirection > 0) negativeLimitLatched = false;
  // Each command owns a fresh relative pulse counter. This counter controls
  // only the requested travel quantity; it is not an absolute-position safety
  // input. D6/D8 remain the travel safety decisions.
  stepper.setCurrentPosition(0L);
  long target = deltaSteps;
  targetSpeedSps = speedSps;
  activeCommandId = (unsigned int)commandId;
  activeWebMotion = true;
  homingMotion = false;
  activeLogicalDirection = requestedDirection;
  reportedTargetSteps = target;
  motionState = STATE_WEB_MOVING;
  motionReason = "none";
  stepper.setMaxSpeed((float)targetSpeedSps);
  stepper.setAcceleration(FIXED_ACCELERATION_SPS2);
  stepper.moveTo(target);
  statusDirty = true;
  Serial.println(F("Bounded Web Position move accepted."));
}

void processCommandBody(char *commandBuffer, CommandTransport transport) {
  lastCommandAccepted = true;
  lastCommandError = "none";

  // E1 is intentionally accepted before all mode and D4 checks. It is a
  // latched software stop for both Local Velocity and Web Position motion.
  // This serial/firmware path is not a substitute for a hardwired,
  // safety-rated emergency-stop circuit that removes hazardous energy.
  if (strcmp(commandBuffer, "V1 E1") == 0) {
    emergencyStopLatched = true;
    stopStepperImmediately();
    stepper.setSpeed(0.0);
    motionState = STATE_EMERGENCY_STOP;
    motionReason = "emergency_stop";
    statusDirty = true;
    Serial.println(F("Software E-STOP latched; step pulses inhibited."));
    return;
  }

  bool claimsOwnership =
      strcmp(commandBuffer, "V1 E0") == 0 ||
      strncmp(commandBuffer, "V1 S", 4) == 0 ||
      strcmp(commandBuffer, "V1 D0") == 0 ||
      strcmp(commandBuffer, "V1 D1") == 0 ||
      strcmp(commandBuffer, "V1 M0") == 0 ||
      strcmp(commandBuffer, "V1 M1") == 0 ||
      strcmp(commandBuffer, "V1 H") == 0 ||
      strncmp(commandBuffer, "V1 G", 4) == 0;
  if (claimsOwnership && !claimTransport(transport)) return;

  if (strcmp(commandBuffer, "V1 E0") == 0) {
    // Requiring D4 OFF prevents reset from immediately restarting Local
    // Velocity motion when the physical run switch was left ON.
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF before resetting the software E-STOP."));
      return;
    }
    emergencyStopLatched = false;
    motionState = controlMode == WEB_POSITION
        ? STATE_WEB_READY
        : STATE_LOCAL_STOPPED;
    motionReason = "run_off";
    statusDirty = true;
    Serial.println(F("Software E-STOP reset; D4 remains OFF."));
    return;
  }

  if (strncmp(commandBuffer, "V1 S", 4) == 0) {
    long requestedSpeedSps = 0;
    if (!parseLongExact(commandBuffer + 4, &requestedSpeedSps)) {
      rejectCommand(F("speed must be an integer."));
      return;
    }
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF and stop motion before changing speed."));
      return;
    }
    if (requestedSpeedSps < MIN_SPEED_SPS ||
        requestedSpeedSps > MAX_SPEED_SPS) {
      rejectCommand(F("speed must be 25..2520 steps/s."));
      return;
    }
    targetSpeedSps = requestedSpeedSps;
    statusDirty = true;
    Serial.println(F("Speed setpoint accepted."));
    return;
  }

  if (strcmp(commandBuffer, "V1 D0") == 0 ||
      strcmp(commandBuffer, "V1 D1") == 0) {
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF and stop motion before changing mapping."));
      return;
    }
    directionSign = commandBuffer[4] == '1' ? -1 : 1;
    setElectricalDirectionMapping();
    statusDirty = true;
    Serial.println(F("Electrical direction mapping accepted."));
    return;
  }

  if (strcmp(commandBuffer, "V1 M0") == 0 ||
      strcmp(commandBuffer, "V1 M1") == 0) {
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF and stop motion before changing mode."));
      return;
    }
    controlMode = commandBuffer[4] == '1' ? WEB_POSITION : LOCAL_VELOCITY;
    stopStepperImmediately();
    motionState = controlMode == WEB_POSITION
        ? STATE_WEB_READY
        : STATE_LOCAL_STOPPED;
    motionReason = "run_off";
    statusDirty = true;
    Serial.println(controlMode == WEB_POSITION
        ? F("Web Position mode accepted.")
        : F("Local Velocity mode accepted."));
    return;
  }

  if (strcmp(commandBuffer, "V1 H") == 0) {
    startHomeCommand();
    return;
  }

  if (strcmp(commandBuffer, "V1 X") == 0) {
    if (controlMode != WEB_POSITION) {
      rejectCommand(F("web Stop is available only in Web Position mode."));
      return;
    }
    abortWebMotion(STATE_WEB_ABORTED, "operator_stop");
    Serial.println(F("Web Position motion stopped."));
    return;
  }

  if (strncmp(commandBuffer, "V1 G", 4) == 0) {
    char *deltaText = commandBuffer + 4;
    char *firstComma = strchr(deltaText, ',');
    if (firstComma == NULL) {
      rejectCommand(F("move grammar is V1 Gsteps,speed,id."));
      return;
    }
    *firstComma = '\0';
    char *speedText = firstComma + 1;
    char *secondComma = strchr(speedText, ',');
    if (secondComma == NULL) {
      rejectCommand(F("move grammar is V1 Gsteps,speed,id."));
      return;
    }
    *secondComma = '\0';
    char *commandText = secondComma + 1;
    long deltaSteps = 0;
    long speedSps = 0;
    long commandId = 0;
    if (!parseLongExact(deltaText, &deltaSteps) ||
        !parseLongExact(speedText, &speedSps) ||
        !parseLongExact(commandText, &commandId)) {
      rejectCommand(F("move fields must be integers."));
      return;
    }
    startMoveCommand(deltaSteps, speedSps, commandId);
    return;
  }

  rejectCommand(F("unknown version-1 command."));
}

void processCommand(char *commandBuffer, CommandTransport transport) {
  CommandTransport ownerBefore = controlOwner;
  unsigned long ownerActivityBefore = ownerLastActivityMs;
  processCommandBody(commandBuffer, transport);
  // A syntactically or physically rejected command must not acquire an idle
  // controller. Existing ownership is retained across a rejected command from
  // that same owner, but a new claim is committed only by acceptance.
  if (!lastCommandAccepted && ownerBefore == TRANSPORT_NONE &&
      controlOwner == transport) {
    controlOwner = TRANSPORT_NONE;
    ownerLastActivityMs = ownerActivityBefore;
  }
}

void processUsbCommand() {
  usbCommandBuffer[usbCommandLength] = '\0';
  processCommand(usbCommandBuffer, TRANSPORT_USB);
}

void pollUsbCommands() {
  // Bound serial work per loop so a noisy host cannot monopolize stepping.
  for (byte readCount = 0;
       readCount < 20 && Serial.available() > 0;
       ++readCount) {
    char incoming = (char)Serial.read();
    if (incoming == '\r') continue;
    if (incoming == '\n') {
      if (usbCommandLength > 0) processUsbCommand();
      usbCommandLength = 0;
      continue;
    }
    if (usbCommandLength < USB_COMMAND_BUFFER_SIZE - 1) {
      usbCommandBuffer[usbCommandLength++] = incoming;
    } else {
      usbCommandLength = 0;
      rejectCommand(F("line too long."));
    }
  }
}

bool queueNetworkStatus(const char *line) {
  // Status is periodic. If a previous line or higher-priority acknowledgement
  // is still draining, drop this snapshot rather than queueing RAM or blocking.
  if (networkTxActiveOffset < networkTxActiveLength || networkAckPending) {
    return false;
  }
  strncpy(networkTxActive, line, sizeof(networkTxActive) - 2);
  networkTxActive[sizeof(networkTxActive) - 2] = '\0';
  strncat(networkTxActive, "\n", sizeof(networkTxActive) -
      strlen(networkTxActive) - 1);
  networkTxActiveLength = strlen(networkTxActive);
  networkTxActiveOffset = 0;
  return true;
}

void queueNetworkAcknowledgement() {
  snprintf(
      networkTxPendingAck,
      sizeof(networkTxPendingAck),
      "{\"v\":1,\"t\":\"a\",\"ok\":%d,\"e\":\"%s\"}\n",
      lastCommandAccepted ? 1 : 0,
      lastCommandError);
  networkAckPending = true;
}

void beginNextNetworkTransmission() {
  if (networkTxActiveOffset < networkTxActiveLength) return;
  networkTxActiveLength = 0;
  networkTxActiveOffset = 0;
  const char *next = NULL;
  if (networkAckPending) {
    next = networkTxPendingAck;
    networkAckPending = false;
  }
  if (next == NULL) return;
  strncpy(networkTxActive, next, sizeof(networkTxActive) - 1);
  networkTxActive[sizeof(networkTxActive) - 1] = '\0';
  networkTxActiveLength = strlen(networkTxActive);
}

void flushNetworkOutput() {
  beginNextNetworkTransmission();
  if (networkTxActiveOffset >= networkTxActiveLength) return;
  int available = Serial1.availableForWrite();
  if (available <= 0) return;
  unsigned int remaining = networkTxActiveLength - networkTxActiveOffset;
  unsigned int chunk = remaining < (unsigned int)available
      ? remaining
      : (unsigned int)available;
  size_t written = Serial1.write(
      (const uint8_t *)networkTxActive + networkTxActiveOffset,
      chunk);
  networkTxActiveOffset += written;
}

void processNetworkCommand() {
  networkCommandBuffer[networkCommandLength] = '\0';
  processCommand(networkCommandBuffer, TRANSPORT_NETWORK);
  queueNetworkAcknowledgement();
}

void pollNetworkCommands() {
  // Match USB's bounded-per-loop work. The Linux service already validates the
  // outer HTTP request, but the ATmega parser remains the authority.
  for (byte readCount = 0;
       readCount < 20 && Serial1.available() > 0;
       ++readCount) {
    char incoming = (char)Serial1.read();
    if (incoming == '\r') continue;
    if (incoming == '\n') {
      if (networkCommandLength > 0) processNetworkCommand();
      networkCommandLength = 0;
      continue;
    }
    if (networkCommandLength < NETWORK_COMMAND_BUFFER_SIZE - 1) {
      networkCommandBuffer[networkCommandLength++] = incoming;
    } else {
      networkCommandLength = 0;
      lastCommandAccepted = false;
      lastCommandError = "line_too_long";
      queueNetworkAcknowledgement();
    }
  }
}

void updateMeasuredPulseRate(bool moving) {
  unsigned long nowUs = micros();
  long currentPosition = stepper.currentPosition();

  if (!moving) {
    measuredPulseRateSps = 0L;
    pulseRateWindowStartPosition = currentPosition;
    pulseRateWindowStartUs = nowUs;
    pulseRateWindowMoving = false;
    return;
  }

  if (!pulseRateWindowMoving) {
    pulseRateWindowStartPosition = currentPosition;
    pulseRateWindowStartUs = nowUs;
    pulseRateWindowMoving = true;
    return;
  }

  unsigned long elapsedUs = nowUs - pulseRateWindowStartUs;
  if (elapsedUs < PULSE_RATE_WINDOW_US) return;

  long signedPulseCount = currentPosition - pulseRateWindowStartPosition;
  unsigned long pulseCount = signedPulseCount < 0
      ? (unsigned long)(-signedPulseCount)
      : (unsigned long)signedPulseCount;
  // 64-bit intermediate prevents overflow if other work delays this window.
  unsigned long long scaledPulses =
      (unsigned long long)pulseCount * 1000000ULL + elapsedUs / 2UL;
  measuredPulseRateSps = (long)(scaledPulses / elapsedUs);
  pulseRateWindowStartPosition = currentPosition;
  pulseRateWindowStartUs = nowUs;
}

bool reportMachineStatus(
    int runRaw,
    int directionRaw,
    int positiveRaw,
    int negativeRaw,
    bool blocked,
    const char *reason,
    long effectiveLogicalSpeedSps,
    bool moving) {
  // Preserve the established meaning of sps as electrical signed rate. The
  // laptop multiplies by ds to recover logical positive/negative travel.
  long electricalSpeedSps = effectiveLogicalSpeedSps * directionSign;
  char line[256];
  snprintf(
      line,
      sizeof(line),
      "{\"v\":1,\"t\":\"s\",\"q\":%lu,\"d4\":%d,\"d5\":%d,"
      "\"d6\":%d,\"d8\":%d,\"lp\":%d,\"ln\":%d,\"b\":%d,"
      "\"r\":\"%s\",\"sps\":%ld,\"csps\":%ld,\"aps\":%ld,\"ds\":%d,"
      "\"m\":%d,\"h\":%d,\"a\":%d,\"e\":%d,\"mv\":%d,\"st\":%d,\"p\":%ld,"
      "\"g\":%ld,\"c\":%u,\"o\":%d}",
      ++statusSequence,
      runRaw,
      directionRaw,
      positiveRaw,
      negativeRaw,
      positiveLimitLatched ? 1 : 0,
      negativeLimitLatched ? 1 : 0,
      blocked ? 1 : 0,
      reason,
      electricalSpeedSps,
      targetSpeedSps,
      measuredPulseRateSps,
      directionSign,
      (int)controlMode,
      positionHomed ? 1 : 0,
      d4OffObservedSinceBoot ? 1 : 0,
      emergencyStopLatched ? 1 : 0,
      moving ? 1 : 0,
      (int)motionState,
      stepper.currentPosition(),
      reportedTargetSteps,
      activeCommandId,
      (int)controlOwner);
  if (Serial) Serial.println(line);
  return queueNetworkStatus(line);
}

void setup() {
  Serial.begin(9600);
  Serial1.begin(115200);

  pinMode(PIN_RUN, INPUT_PULLUP);
  pinMode(PIN_DIR, INPUT_PULLUP);
  pinMode(PIN_LIMIT_POS, INPUT_PULLUP);
  pinMode(PIN_LIMIT_NEG, INPUT_PULLUP);
  d4OffObservedSinceBoot = digitalRead(PIN_RUN) == HIGH;

  stepper.setMaxSpeed((float)MAX_SPEED_SPS);
  stepper.setAcceleration(FIXED_ACCELERATION_SPS2);
  stepper.setMinPulseWidth(5);
  setElectricalDirectionMapping();

  Serial.println(F("Stepper ready in stopped Local Velocity mode."));
  Serial.println(F("D4/D5 run Local Velocity; Web Position uses D4 arm and D5 direction."));
  Serial.println(F("USB and Yún-Linux controls share V1 S, D, M, H, G, X, E1, E0."));
  reportLimitLevels(digitalRead(PIN_LIMIT_POS), digitalRead(PIN_LIMIT_NEG));
}

void loop() {
  pollUsbCommands();
  pollNetworkCommands();
  flushNetworkOutput();

  int runRaw = digitalRead(PIN_RUN);
  int directionRaw = digitalRead(PIN_DIR);
  int positiveRaw = digitalRead(PIN_LIMIT_POS);
  int negativeRaw = digitalRead(PIN_LIMIT_NEG);
  bool d4On = runRaw == LOW;
  if (!d4On) d4OffObservedSinceBoot = true;
  releaseExpiredOwner();
  bool d4MotionArmed = d4On && d4OffObservedSinceBoot;
  bool d5Reverse = directionRaw == LOW;
  bool positiveLimitActive = positiveRaw == POS_LIMIT_ACTIVE_LEVEL;
  bool negativeLimitActive = negativeRaw == NEG_LIMIT_ACTIVE_LEVEL;

  static int lastPositiveRaw = -1;
  static int lastNegativeRaw = -1;
  if (positiveRaw != lastPositiveRaw || negativeRaw != lastNegativeRaw) {
    reportLimitLevels(positiveRaw, negativeRaw);
    lastPositiveRaw = positiveRaw;
    lastNegativeRaw = negativeRaw;
  }

  if (positiveLimitActive) positiveLimitLatched = true;
  if (negativeLimitActive) negativeLimitLatched = true;

  bool blocked = false;
  const char *statusReason = motionReason;
  long effectiveLogicalSpeedSps = 0L;
  bool moving = false;

  if (emergencyStopLatched) {
    // Enforce the latch on every loop, independent of control mode, D4, D5,
    // command state, or a later host disconnect. Reset is the only exit.
    if (activeWebMotion || motionState != STATE_EMERGENCY_STOP) {
      stopStepperImmediately();
    }
    stepper.setSpeed(0.0);
    blocked = true;
    statusReason = "emergency_stop";
    motionState = STATE_EMERGENCY_STOP;
    motionReason = statusReason;
  } else if (controlMode == LOCAL_VELOCITY) {
    int logicalDirection = d5Reverse ? -1 : 1;
    if (d4MotionArmed && logicalDirection < 0) positiveLimitLatched = false;
    if (d4MotionArmed && logicalDirection > 0) negativeLimitLatched = false;

    blocked =
        (logicalDirection > 0 && positiveLimitLatched) ||
        (logicalDirection < 0 && negativeLimitLatched);
    if (blocked) {
      statusReason = logicalDirection > 0 ? "positive_limit" : "negative_limit";
      motionState = STATE_LIMIT_BLOCKED;
      stepper.setSpeed(0.0);
    } else if (!d4MotionArmed) {
      statusReason = d4On ? "boot_disarmed" : "run_off";
      motionState = STATE_LOCAL_STOPPED;
      stepper.setSpeed(0.0);
    } else {
      statusReason = "none";
      motionState = STATE_LOCAL_MOVING;
      effectiveLogicalSpeedSps = logicalDirection * targetSpeedSps;
      stepper.setSpeed((float)effectiveLogicalSpeedSps);
      stepper.runSpeed();
      moving = true;
      // AccelStepper counts every locally generated pulse. The count is only
      // published as position after an endpoint has established the origin.
      reportedTargetSteps = stepper.currentPosition();
    }
    motionReason = statusReason;
  } else {
    if (activeWebMotion) {
      bool d5AuthorizesActiveDirection =
          (activeLogicalDirection > 0 && !d5Reverse) ||
          (activeLogicalDirection < 0 && d5Reverse);
      if (!d4MotionArmed) {
        abortWebMotion(STATE_WEB_ABORTED, "d4_abort");
      } else if (!d5AuthorizesActiveDirection) {
        abortWebMotion(STATE_WEB_ABORTED, "direction_auth");
      } else if (activeLogicalDirection > 0 && positiveLimitActive) {
        abortWebMotion(STATE_LIMIT_BLOCKED, "positive_limit");
      } else if (activeLogicalDirection < 0 && negativeLimitActive) {
        if (homingMotion || motionState == STATE_HOMING) {
          establishD8Reference();
          stopStepperImmediately();
          motionState = STATE_WEB_READY;
          motionReason = "home_complete";
          statusDirty = true;
        } else {
          abortWebMotion(STATE_LIMIT_BLOCKED, "negative_limit");
        }
      } else {
        stepper.run();
        moving = true;
        effectiveLogicalSpeedSps = (long)stepper.speed();
        if (stepper.distanceToGo() == 0) {
          activeWebMotion = false;
          homingMotion = false;
          activeLogicalDirection = 0;
          moving = false;
          effectiveLogicalSpeedSps = 0L;
          reportedTargetSteps = stepper.currentPosition();
          motionState = STATE_WEB_COMPLETED;
          motionReason = "move_complete";
          statusDirty = true;
        }
      }
    }

    if (!activeWebMotion) {
      moving = false;
      effectiveLogicalSpeedSps = 0L;
      // Show the selected D5 direction even while idle; it does not itself
      // start motion, and changing it during motion aborts rather than reverses.
      if ((!d5Reverse && positiveLimitLatched) ||
          (d5Reverse && negativeLimitLatched)) {
        blocked = true;
        statusReason = d5Reverse ? "negative_limit" : "positive_limit";
      } else if (motionState == STATE_WEB_ABORTED ||
                 motionState == STATE_LIMIT_BLOCKED) {
        statusReason = motionReason;
      } else if (!d4MotionArmed) {
        statusReason = d4On ? "boot_disarmed" : "run_off";
      } else {
        statusReason = motionReason;
      }
    } else {
      statusReason = motionReason;
    }
  }

  updateMeasuredPulseRate(moving);

  unsigned long statusSignature =
      ((unsigned long)(runRaw == HIGH) << 0) |
      ((unsigned long)(directionRaw == HIGH) << 1) |
      ((unsigned long)(positiveRaw == HIGH) << 2) |
      ((unsigned long)(negativeRaw == HIGH) << 3) |
      ((unsigned long)positiveLimitLatched << 4) |
      ((unsigned long)negativeLimitLatched << 5) |
      ((unsigned long)blocked << 6) |
      ((unsigned long)positionHomed << 7) |
      ((unsigned long)activeWebMotion << 8) |
      ((unsigned long)controlMode << 9) |
      ((unsigned long)motionState << 10) |
      ((unsigned long)d4OffObservedSinceBoot << 14) |
      ((unsigned long)emergencyStopLatched << 15);
  static unsigned long lastStatusSignature = 0xFFFFFFFFUL;
  static unsigned long lastStatusAtMs = 0UL;
  unsigned long nowMs = millis();
  bool motionUpdateDue = moving && nowMs - lastStatusAtMs >= STATUS_MOTION_MS;
  if (statusDirty || statusSignature != lastStatusSignature || motionUpdateDue ||
      nowMs - lastStatusAtMs >= STATUS_HEARTBEAT_MS) {
    reportMachineStatus(
        runRaw,
        directionRaw,
        positiveRaw,
        negativeRaw,
        blocked,
        statusReason,
        effectiveLogicalSpeedSps,
        moving);
    lastStatusSignature = statusSignature;
    lastStatusAtMs = nowMs;
    // A status snapshot may be dropped if an acknowledgement or older status
    // is still draining. Do not fast-loop the report: repeated 9600-baud USB
    // debug writes could otherwise delay STEP generation. The next bounded
    // heartbeat refreshes the Linux cache within STATUS_HEARTBEAT_MS.
    statusDirty = false;
  }
  flushNetworkOutput();
}

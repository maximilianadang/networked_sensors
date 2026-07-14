#include <AccelStepper.h>
#include <util/atomic.h>

const int PIN_STEP = 3;
const int PIN_DRIVER_DIR = 2;
AccelStepper stepper(
    AccelStepper::DRIVER,
    PIN_STEP,
    PIN_DRIVER_DIR);  // STEP=D3, DIR=D2

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

// --- Timer-backed Local Velocity pulses ---
// AccelStepper::runSpeed() can emit at most one pulse per cooperative loop.
// USB/network/status work therefore capped measured output below the requested
// rate. Timer1 now owns only Local Velocity pulse timing. The main loop still
// checks D4, D5, D6, D8, and E-STOP continuously and disables this interrupt
// before dropping D9/ENA-. Web Position retains AccelStepper acceleration and
// distance behavior and never enables this timer path.
const unsigned long LOCAL_PULSE_TIMER_HZ = F_CPU / 64UL;
volatile bool localPulseTimerEnabled = false;
volatile int localPulseTimerDirection = 1;
volatile long localPulseTimerPosition = 0L;
volatile long localPulseTimerSpeedSps = 0L;

// --- Fixed physical direction calibration ---
// This Normal mapping was physically verified across the complete stroke:
// positive motion goes toward D6 and negative motion goes toward D8. It is a
// safety property, not an operator setting. Do not add a runtime DIR-inversion
// command: changing electrical polarity without changing physical endpoint
// semantics can make motion approach D8 while the firmware checks D6.
const int FIXED_DIRECTION_SIGN = 1;

// --- Switch pins ---
const int PIN_RUN = 4;
const int PIN_DIR = 5;
const int PIN_LIMIT_POS = 6;
const int PIN_LIMIT_NEG = 8;  // D7 is reserved by the Yún Linux handshake.
const int PIN_DRIVER_ENABLE_NEG = 9;

// All four inputs use INPUT_PULLUP. The installed magnetic switches are
// passive normally-open contacts to GND: open/clear is HIGH and magnet-active
// is LOW. A broken limit wire therefore looks clear and is not fail-safe.
const int POS_LIMIT_ACTIVE_LEVEL = LOW;
const int NEG_LIMIT_ACTIVE_LEVEL = LOW;

// DM542T common-anode enable wiring (verified against its V4.0 manual):
// ENA+ remains at Yún 5 V and ENA- connects to D9. LOW places 5 V across the
// opto-isolated ENA input and disables the motor output stage; HIGH produces
// approximately 0 V differential and enables it. The manual requires at least
// 200 ms from enable to motion. Driver 24 V remains present when disabled, but
// motor winding/holding current is removed.
const int DRIVER_OUTPUT_DISABLED_LEVEL = LOW;
const int DRIVER_OUTPUT_ENABLED_LEVEL = HIGH;
const unsigned long DRIVER_ENABLE_DELAY_MS = 200UL;
bool driverOutputEnabled = false;
unsigned long driverEnabledAtMs = 0UL;

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
int activePhysicalDirection = 0;
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
// The scheduled speed is not proof of how often D3 was actually pulsed.
// Measure the change in the shared pulse-position counter over a fixed window:
// Timer1 advances it in Local Velocity, and AccelStepper advances it in Web
// Position. This remains open-loop electrical evidence: it proves D3 pulse
// attempts, not DM542T acceptance or physical piston travel.
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
unsigned int usbTxActiveOffset = 0;
bool networkTxActiveSharedWithUsb = false;
char networkTxPendingAck[80];
bool networkAckPending = false;

ISR(TIMER1_COMPA_vect) {
  if (!localPulseTimerEnabled) return;
  digitalWrite(PIN_STEP, HIGH);
  delayMicroseconds(5);
  digitalWrite(PIN_STEP, LOW);
  localPulseTimerPosition += localPulseTimerDirection;
}

bool localPulseTimerIsEnabled() {
  bool enabled = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    enabled = localPulseTimerEnabled;
  }
  return enabled;
}

long currentPulsePosition() {
  bool enabled = false;
  long position = 0L;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    enabled = localPulseTimerEnabled;
    position = localPulseTimerPosition;
  }
  return enabled ? position : stepper.currentPosition();
}

void stopLocalPulseTimerAndSync() {
  bool wasEnabled = false;
  long finalPosition = 0L;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    wasEnabled = localPulseTimerEnabled;
    localPulseTimerEnabled = false;
    TIMSK1 &= ~_BV(OCIE1A);
    finalPosition = localPulseTimerPosition;
    localPulseTimerSpeedSps = 0L;
  }
  if (!wasEnabled) return;
  digitalWrite(PIN_STEP, LOW);
  stepper.setCurrentPosition(finalPosition);
}

void startLocalPulseTimer(long signedSpeedSps) {
  int direction = signedSpeedSps >= 0L ? 1 : -1;
  long speedMagnitude = labs(signedSpeedSps);
  bool alreadyConfigured = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    alreadyConfigured = localPulseTimerEnabled &&
        localPulseTimerDirection == direction &&
        localPulseTimerSpeedSps == speedMagnitude;
  }
  if (alreadyConfigured || speedMagnitude == 0L) return;

  stopLocalPulseTimerAndSync();
  long initialPosition = stepper.currentPosition();
  unsigned long timerTicks =
      (LOCAL_PULSE_TIMER_HZ + (unsigned long)speedMagnitude / 2UL) /
      (unsigned long)speedMagnitude;
  if (timerTicks < 2UL) timerTicks = 2UL;
  if (timerTicks > 65536UL) timerTicks = 65536UL;

  digitalWrite(PIN_DRIVER_DIR, direction > 0 ? HIGH : LOW);
  digitalWrite(PIN_STEP, LOW);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    localPulseTimerPosition = initialPosition;
    localPulseTimerDirection = direction;
    localPulseTimerSpeedSps = speedMagnitude;
    OCR1A = (uint16_t)(timerTicks - 1UL);
    TCNT1 = 0;
    TIFR1 = _BV(OCF1A);
    localPulseTimerEnabled = true;
    TIMSK1 |= _BV(OCIE1A);
  }
}

constexpr bool limitBlocksPhysicalDirection(
    int physicalDirection,
    bool positiveLimit,
    bool negativeLimit) {
  return (physicalDirection > 0 && positiveLimit) ||
      (physicalDirection < 0 && negativeLimit);
}

// Compile-time physical safety matrix. Any future edit that swaps endpoint
// meanings or blocks retreat makes the AVR build fail before it can be flashed.
static_assert(
    limitBlocksPhysicalDirection(1, true, false),
    "D6 must block positive travel toward D6");
static_assert(
    !limitBlocksPhysicalDirection(1, false, true),
    "D8 must not block positive retreat away from D8");
static_assert(
    limitBlocksPhysicalDirection(-1, false, true),
    "D8 must block negative travel toward D8");
static_assert(
    !limitBlocksPhysicalDirection(-1, true, false),
    "D6 must not block negative retreat away from D6");
static_assert(
    limitBlocksPhysicalDirection(1, true, true) &&
        limitBlocksPhysicalDirection(-1, true, true),
    "either selected direction must block when both endpoints are active");
static_assert(
    !limitBlocksPhysicalDirection(0, true, true),
    "a stopped direction must not select an endpoint");

const char *physicalLimitReason(int physicalDirection) {
  return physicalDirection > 0 ? "positive_limit" : "negative_limit";
}

void clearOppositeLimitLatch(int physicalDirection) {
  if (physicalDirection < 0) positiveLimitLatched = false;
  if (physicalDirection > 0) negativeLimitLatched = false;
}

void updatePhysicalEndpointLatches(
    bool positiveLimitActive,
    bool negativeLimitActive) {
  // The carriage cannot physically occupy both ends of its stroke. When one
  // raw endpoint is exclusively active, it is authoritative and clears stale
  // history from the opposite endpoint. Without this rule, visiting D8 and
  // later reaching D6 leaves both latches set; each selected direction then
  // appears blocked even though the raw switches correctly identify D6.
  //
  // If both raw inputs are LOW simultaneously, retain both latches and block
  // both directions. That state is treated conservatively as a wiring/sensor
  // fault rather than guessing which endpoint is real.
  if (positiveLimitActive && negativeLimitActive) {
    positiveLimitLatched = true;
    negativeLimitLatched = true;
  } else if (positiveLimitActive) {
    positiveLimitLatched = true;
    negativeLimitLatched = false;
  } else if (negativeLimitActive) {
    positiveLimitLatched = false;
    negativeLimitLatched = true;
  }
}

void disableDriverOutput() {
  stopLocalPulseTimerAndSync();
  digitalWrite(PIN_DRIVER_ENABLE_NEG, DRIVER_OUTPUT_DISABLED_LEVEL);
  if (driverOutputEnabled) statusDirty = true;
  driverOutputEnabled = false;
}

void requestDriverOutputEnable() {
  if (driverOutputEnabled) return;
  digitalWrite(PIN_DRIVER_ENABLE_NEG, DRIVER_OUTPUT_ENABLED_LEVEL);
  driverOutputEnabled = true;
  driverEnabledAtMs = millis();
  statusDirty = true;
}

bool driverReadyForMotion() {
  requestDriverOutputEnable();
  return millis() - driverEnabledAtMs >= DRIVER_ENABLE_DELAY_MS;
}

void reportLimitLevels(int positiveRaw, int negativeRaw) {
  // Never delay a limit response while USB CDC waits for the host. The compact
  // status frame reports the same raw levels after motion has been inhibited.
  if (driverOutputEnabled || !Serial) return;
  Serial.print(F("Limit inputs: D6="));
  Serial.print(positiveRaw == HIGH ? F("HIGH (open)") : F("LOW (closed)"));
  Serial.print(F(", D8="));
  Serial.println(negativeRaw == HIGH ? F("HIGH (open)") : F("LOW (closed)"));
}

void stopStepperImmediately() {
  stopLocalPulseTimerAndSync();
  long current = stepper.currentPosition();
  // setCurrentPosition sets current and target to the same value and clears
  // AccelStepper's internal speed. It therefore aborts without emitting the
  // deceleration pulses that stop() would schedule.
  stepper.setCurrentPosition(current);
  reportedTargetSteps = current;
  activeWebMotion = false;
  homingMotion = false;
  activePhysicalDirection = 0;
  disableDriverOutput();
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
  if (!driverOutputEnabled && Serial) {
    Serial.print(F("Command rejected: "));
    Serial.println(message);
  }
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
    if (!driverOutputEnabled && Serial) {
      Serial.println(controlOwner == TRANSPORT_USB
          ? F("Command rejected: control is owned by USB.")
          : F("Command rejected: control is owned by network."));
    }
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
  activePhysicalDirection = -1;
  positiveLimitLatched = false;
  motionState = STATE_HOMING;
  motionReason = "none";
  stepper.setMaxSpeed((float)HOME_SPEED_SPS);
  stepper.setAcceleration(FIXED_ACCELERATION_SPS2);
  stepper.moveTo(stepper.currentPosition() - MAX_HOME_SEARCH_STEPS);
  reportedTargetSteps = 0L;
  requestDriverOutputEnable();
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
  if (limitBlocksPhysicalDirection(
          requestedDirection,
          positiveLimitLatched,
          negativeLimitLatched)) {
    rejectCommand(F("the requested direction is limit-blocked."));
    return;
  }

  clearOppositeLimitLatch(requestedDirection);
  // Each command owns a fresh relative pulse counter. This counter controls
  // only the requested travel quantity; it is not an absolute-position safety
  // input. D6/D8 remain the travel safety decisions.
  stepper.setCurrentPosition(0L);
  long target = deltaSteps;
  targetSpeedSps = speedSps;
  activeCommandId = (unsigned int)commandId;
  activeWebMotion = true;
  homingMotion = false;
  activePhysicalDirection = requestedDirection;
  reportedTargetSteps = target;
  motionState = STATE_WEB_MOVING;
  motionReason = "none";
  stepper.setMaxSpeed((float)targetSpeedSps);
  stepper.setAcceleration(FIXED_ACCELERATION_SPS2);
  stepper.moveTo(target);
  requestDriverOutputEnable();
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
  // One immutable buffer feeds both transports with independent offsets. This
  // avoids a second 256-byte AVR buffer and keeps USB transmission off the
  // blocking Serial.println path. If either consumer is still draining, drop
  // this periodic snapshot; the next bounded heartbeat supplies a fresh one.
  bool usbBusy = networkTxActiveSharedWithUsb &&
      usbTxActiveOffset < networkTxActiveLength;
  if (networkTxActiveOffset < networkTxActiveLength || usbBusy ||
      networkAckPending) {
    return false;
  }
  strncpy(networkTxActive, line, sizeof(networkTxActive) - 2);
  networkTxActive[sizeof(networkTxActive) - 2] = '\0';
  strncat(networkTxActive, "\n", sizeof(networkTxActive) -
      strlen(networkTxActive) - 1);
  networkTxActiveLength = strlen(networkTxActive);
  networkTxActiveOffset = 0;
  usbTxActiveOffset = Serial ? 0 : networkTxActiveLength;
  networkTxActiveSharedWithUsb = true;
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
  bool usbBusy = networkTxActiveSharedWithUsb &&
      usbTxActiveOffset < networkTxActiveLength;
  if (networkTxActiveOffset < networkTxActiveLength || usbBusy) return;
  networkTxActiveLength = 0;
  networkTxActiveOffset = 0;
  usbTxActiveOffset = 0;
  networkTxActiveSharedWithUsb = false;
  const char *next = NULL;
  if (networkAckPending) {
    next = networkTxPendingAck;
    networkAckPending = false;
  }
  if (next == NULL) return;
  strncpy(networkTxActive, next, sizeof(networkTxActive) - 1);
  networkTxActive[sizeof(networkTxActive) - 1] = '\0';
  networkTxActiveLength = strlen(networkTxActive);
  usbTxActiveOffset = networkTxActiveLength;
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

void flushUsbOutput() {
  if (!networkTxActiveSharedWithUsb ||
      usbTxActiveOffset >= networkTxActiveLength) return;
  if (!Serial) {
    usbTxActiveOffset = networkTxActiveLength;
    return;
  }
  int available = Serial.availableForWrite();
  if (available <= 1) return;
  unsigned int remaining = networkTxActiveLength - usbTxActiveOffset;
  unsigned int immediatelyWritable = (unsigned int)available - 1U;
  unsigned int chunk = remaining < immediatelyWritable
      ? remaining
      : immediatelyWritable;
  // Never fill the endpoint exactly: Arduino AVR's USB_Send() then waits for
  // a zero-length packet. Flush the partial packet explicitly; USB_Flush only
  // releases the endpoint and does not wait for the host to consume it.
  size_t written = Serial.write(
      (const uint8_t *)networkTxActive + usbTxActiveOffset,
      chunk);
  usbTxActiveOffset += written;
  Serial.flush();
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

void serviceTransports() {
  flushUsbOutput();
  pollUsbCommands();
  pollNetworkCommands();
  flushNetworkOutput();
}

void updateMeasuredPulseRate(bool moving) {
  unsigned long nowUs = micros();
  long currentPosition = currentPulsePosition();

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
    long effectivePhysicalSpeedSps,
    bool moving) {
  // The fixed, physically verified Normal mapping makes the electrical and
  // physical signed rates identical. ds remains in status as read-only
  // compatibility telemetry; it is no longer a command capability.
  long electricalSpeedSps = effectivePhysicalSpeedSps;
  char line[256];
  snprintf(
      line,
      sizeof(line),
      "{\"v\":1,\"t\":\"s\",\"q\":%lu,\"d4\":%d,\"d5\":%d,"
      "\"d6\":%d,\"d8\":%d,\"lp\":%d,\"ln\":%d,\"b\":%d,"
      "\"r\":\"%s\",\"sps\":%ld,\"csps\":%ld,\"aps\":%ld,\"ds\":%d,"
      "\"en\":%d,"
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
      FIXED_DIRECTION_SIGN,
      driverOutputEnabled ? 1 : 0,
      (int)controlMode,
      positionHomed ? 1 : 0,
      d4OffObservedSinceBoot ? 1 : 0,
      emergencyStopLatched ? 1 : 0,
      moving ? 1 : 0,
      (int)motionState,
      currentPulsePosition(),
      reportedTargetSteps,
      activeCommandId,
      (int)controlOwner);
  return queueNetworkStatus(line);
}

void setup() {
  Serial.begin(9600);
  Serial1.begin(115200);

  pinMode(PIN_RUN, INPUT_PULLUP);
  pinMode(PIN_DIR, INPUT_PULLUP);
  pinMode(PIN_LIMIT_POS, INPUT_PULLUP);
  pinMode(PIN_LIMIT_NEG, INPUT_PULLUP);
  // Set the output latch LOW before enabling the pin driver so D9 cannot
  // produce an enable glitch during setup.
  digitalWrite(PIN_DRIVER_ENABLE_NEG, DRIVER_OUTPUT_DISABLED_LEVEL);
  pinMode(PIN_DRIVER_ENABLE_NEG, OUTPUT);
  driverOutputEnabled = false;
  d4OffObservedSinceBoot = digitalRead(PIN_RUN) == HIGH;

  stepper.setMaxSpeed((float)MAX_SPEED_SPS);
  stepper.setAcceleration(FIXED_ACCELERATION_SPS2);
  stepper.setMinPulseWidth(5);
  stepper.setPinsInverted(false, false, false);

  // Timer1 CTC at F_CPU/64. The compare interrupt remains disabled until an
  // authorized Local Velocity run has completed the 200 ms driver wake-up.
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  TIMSK1 &= ~_BV(OCIE1A);
  digitalWrite(PIN_STEP, LOW);

  Serial.println(F("Stepper ready in stopped Local Velocity mode."));
  Serial.println(F("D4/D5 run Local Velocity; Web Position uses D4 arm and D5 direction."));
  Serial.println(F("USB and Yún-Linux controls share V1 S, M, H, G, X, E1, E0."));
  Serial.println(F("DIR is fixed Normal; D9 disables DM542T holding current while stopped."));
  reportLimitLevels(digitalRead(PIN_LIMIT_POS), digitalRead(PIN_LIMIT_NEG));
}

void loop() {
  // Hardware D4/D5/D6/D8 checks and STEP scheduling run on every pass. USB and
  // UART command service is bounded to 1 ms while energized, keeping software
  // E-STOP latency small. Timer1 pulse timing is independent of this cadence.
  static unsigned long lastTransportServiceUs = 0UL;
  unsigned long transportNowUs = micros();
  if (!driverOutputEnabled ||
      transportNowUs - lastTransportServiceUs >= 1000UL) {
    serviceTransports();
    lastTransportServiceUs = transportNowUs;
  }

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

  updatePhysicalEndpointLatches(positiveLimitActive, negativeLimitActive);

  bool blocked = false;
  const char *statusReason = motionReason;
  long effectivePhysicalSpeedSps = 0L;
  bool moving = false;

  if (emergencyStopLatched) {
    // Enforce the latch on every loop, independent of control mode, D4, D5,
    // command state, or a later host disconnect. Reset is the only exit.
    if (activeWebMotion || motionState != STATE_EMERGENCY_STOP) {
      stopStepperImmediately();
    }
    stepper.setSpeed(0.0);
    disableDriverOutput();
    blocked = true;
    statusReason = "emergency_stop";
    motionState = STATE_EMERGENCY_STOP;
    motionReason = statusReason;
  } else if (controlMode == LOCAL_VELOCITY) {
    int physicalDirection = d5Reverse ? -1 : 1;
    if (d4MotionArmed) clearOppositeLimitLatch(physicalDirection);

    blocked = limitBlocksPhysicalDirection(
        physicalDirection,
        positiveLimitLatched,
        negativeLimitLatched);
    if (blocked) {
      statusReason = physicalLimitReason(physicalDirection);
      motionState = STATE_LIMIT_BLOCKED;
      stepper.setSpeed(0.0);
      disableDriverOutput();
    } else if (!d4MotionArmed) {
      statusReason = d4On ? "boot_disarmed" : "run_off";
      motionState = STATE_LOCAL_STOPPED;
      stepper.setSpeed(0.0);
      disableDriverOutput();
    } else if (!driverReadyForMotion()) {
      statusReason = "driver_wakeup";
      motionState = STATE_LOCAL_STOPPED;
      stepper.setSpeed(0.0);
    } else {
      statusReason = "none";
      motionState = STATE_LOCAL_MOVING;
      effectivePhysicalSpeedSps = physicalDirection * targetSpeedSps;
      startLocalPulseTimer(effectivePhysicalSpeedSps);
      moving = localPulseTimerIsEnabled();
      // The Timer1 pulse counter is synchronized back into AccelStepper when
      // Local Velocity stops. Position is diagnostic open-loop telemetry only.
      reportedTargetSteps = currentPulsePosition();
    }
    motionReason = statusReason;
  } else {
    if (activeWebMotion) {
      bool d5AuthorizesActiveDirection =
          (activePhysicalDirection > 0 && !d5Reverse) ||
          (activePhysicalDirection < 0 && d5Reverse);
      if (!d4MotionArmed) {
        abortWebMotion(STATE_WEB_ABORTED, "d4_abort");
      } else if (!d5AuthorizesActiveDirection) {
        abortWebMotion(STATE_WEB_ABORTED, "direction_auth");
      } else if (limitBlocksPhysicalDirection(
                     activePhysicalDirection,
                     positiveLimitActive,
                     negativeLimitActive)) {
        if (activePhysicalDirection < 0 &&
            (homingMotion || motionState == STATE_HOMING)) {
          establishD8Reference();
          stopStepperImmediately();
          motionState = STATE_WEB_READY;
          motionReason = "home_complete";
          statusDirty = true;
        } else {
          abortWebMotion(
              STATE_LIMIT_BLOCKED,
              physicalLimitReason(activePhysicalDirection));
        }
      } else if (!driverReadyForMotion()) {
        statusReason = "driver_wakeup";
      } else {
        motionReason = "none";
        stepper.run();
        moving = true;
        effectivePhysicalSpeedSps = (long)stepper.speed();
        if (stepper.distanceToGo() == 0) {
          activeWebMotion = false;
          homingMotion = false;
          activePhysicalDirection = 0;
          moving = false;
          effectivePhysicalSpeedSps = 0L;
          reportedTargetSteps = stepper.currentPosition();
          motionState = STATE_WEB_COMPLETED;
          motionReason = "move_complete";
          disableDriverOutput();
          statusDirty = true;
        }
      }
    }

    if (!activeWebMotion) {
      moving = false;
      effectivePhysicalSpeedSps = 0L;
      disableDriverOutput();
      // Show the selected D5 direction even while idle; it does not itself
      // start motion, and changing it during motion aborts rather than reverses.
      int selectedPhysicalDirection = d5Reverse ? -1 : 1;
      if (limitBlocksPhysicalDirection(
              selectedPhysicalDirection,
              positiveLimitLatched,
              negativeLimitLatched)) {
        blocked = true;
        statusReason = physicalLimitReason(selectedPhysicalDirection);
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
      ((unsigned long)emergencyStopLatched << 15) |
      ((unsigned long)driverOutputEnabled << 16);
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
        effectivePhysicalSpeedSps,
        moving);
    lastStatusSignature = statusSignature;
    lastStatusAtMs = nowMs;
    // A status snapshot may be dropped if an older USB or network frame is
    // still draining. Do not fast-loop the report; the next bounded heartbeat
    // refreshes each consumer without delaying STEP generation.
    statusDirty = false;
  }
}

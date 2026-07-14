#include <AccelStepper.h>

AccelStepper stepper(AccelStepper::DRIVER, 3, 2);  // STEP=D3, DIR=D2

// --- Mechanism calibration ---
// PROVISIONAL pulse calibration: 200 pulses/rev and a presumed 2 mm lead give
// 100 steps/mm. The signed-distance engine uses this value, but it is not yet
// physically accepted: count the emitted STEP pulses during a DRO-measured
// displacement and update STEPS_PER_MM if those measurements disagree.
const float STEPS_PER_MM = 100.0;

// PHYSICAL STROKE CALIBRATION (2026-07-13): an external DRO measured
// 137.18 mm between the two installed magnetic-limit trip positions:
//   D8 end: Normal mapping, D5 Reverse/LOW, D8 LOW, D6 HIGH
//   D6 end: Normal mapping, D5 Forward/HIGH, D6 LOW, D8 HIGH
// Home optionally approaches D8 and zeroes the diagnostic pulse counter. The
// measured stroke bounds one relative command and the Home search, but no
// absolute open-loop coordinate or software margin authorizes motion. D6/D8
// are the directional travel stops even if the provisional step/mm is wrong.
const float MEASURED_LIMIT_TO_LIMIT_TRAVEL_MM = 137.18;
const long MAX_TRAVEL_STEPS = 13718L;
const long MAX_HOME_SEARCH_STEPS = MAX_TRAVEL_STEPS + 500L;

// --- Fixed motion settings ---
// Acceleration is intentionally a firmware setting rather than an operator
// field. The page controls speed and distance, while this conservative value
// stays reviewable in source. The DM542T has already run smoothly with the
// existing wiring; setMinPulseWidth(5) preserves a comfortably wide STEP pulse.
const long MIN_SPEED_SPS = 10L;       // 0.1 mm/s at provisional calibration
const long MAX_SPEED_SPS = 1000L;     // 10.0 mm/s, still a provisional ceiling
const long DEFAULT_SPEED_SPS = 150L;  // 1.5 mm/s
const long HOME_SPEED_SPS = 150L;     // fixed conservative homing speed
const float FIXED_ACCELERATION_SPS2 = 500.0;  // 5 mm/s^2 provisionally
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

// --- USB command/status contract ---
// Commands are newline-terminated ASCII:
//   V1 S10..1000                 configure speed while D4 is OFF
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

const byte USB_COMMAND_BUFFER_SIZE = 48;
char usbCommandBuffer[USB_COMMAND_BUFFER_SIZE];
byte usbCommandLength = 0;

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
  Serial.print(F("Command rejected: "));
  Serial.println(message);
  statusDirty = true;
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
    rejectCommand(F("speed must be 10..1000 steps/s."));
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

void processUsbCommand() {
  usbCommandBuffer[usbCommandLength] = '\0';

  // E1 is intentionally accepted before all mode and D4 checks. It is a
  // latched software stop for both Local Velocity and Web Position motion.
  // This serial/firmware path is not a substitute for a hardwired,
  // safety-rated emergency-stop circuit that removes hazardous energy.
  if (strcmp(usbCommandBuffer, "V1 E1") == 0) {
    emergencyStopLatched = true;
    stopStepperImmediately();
    stepper.setSpeed(0.0);
    motionState = STATE_EMERGENCY_STOP;
    motionReason = "emergency_stop";
    statusDirty = true;
    Serial.println(F("Software E-STOP latched; step pulses inhibited."));
    return;
  }

  if (strcmp(usbCommandBuffer, "V1 E0") == 0) {
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

  if (strncmp(usbCommandBuffer, "V1 S", 4) == 0) {
    long requestedSpeedSps = 0;
    if (!parseLongExact(usbCommandBuffer + 4, &requestedSpeedSps)) {
      rejectCommand(F("speed must be an integer."));
      return;
    }
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF and stop motion before changing speed."));
      return;
    }
    if (requestedSpeedSps < MIN_SPEED_SPS ||
        requestedSpeedSps > MAX_SPEED_SPS) {
      rejectCommand(F("speed must be 10..1000 steps/s."));
      return;
    }
    targetSpeedSps = requestedSpeedSps;
    statusDirty = true;
    Serial.println(F("Speed setpoint accepted."));
    return;
  }

  if (strcmp(usbCommandBuffer, "V1 D0") == 0 ||
      strcmp(usbCommandBuffer, "V1 D1") == 0) {
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF and stop motion before changing mapping."));
      return;
    }
    directionSign = usbCommandBuffer[4] == '1' ? -1 : 1;
    setElectricalDirectionMapping();
    statusDirty = true;
    Serial.println(F("Electrical direction mapping accepted."));
    return;
  }

  if (strcmp(usbCommandBuffer, "V1 M0") == 0 ||
      strcmp(usbCommandBuffer, "V1 M1") == 0) {
    if (!stoppedWithD4Off()) {
      rejectCommand(F("turn D4 OFF and stop motion before changing mode."));
      return;
    }
    controlMode = usbCommandBuffer[4] == '1' ? WEB_POSITION : LOCAL_VELOCITY;
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

  if (strcmp(usbCommandBuffer, "V1 H") == 0) {
    startHomeCommand();
    return;
  }

  if (strcmp(usbCommandBuffer, "V1 X") == 0) {
    if (controlMode != WEB_POSITION) {
      rejectCommand(F("web Stop is available only in Web Position mode."));
      return;
    }
    abortWebMotion(STATE_WEB_ABORTED, "operator_stop");
    Serial.println(F("Web Position motion stopped."));
    return;
  }

  if (strncmp(usbCommandBuffer, "V1 G", 4) == 0) {
    char *deltaText = usbCommandBuffer + 4;
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

void reportMachineStatus(
    int runRaw,
    int directionRaw,
    int positiveRaw,
    int negativeRaw,
    bool blocked,
    const char *reason,
    long effectiveLogicalSpeedSps,
    bool moving) {
  if (!Serial) return;

  // Preserve the established meaning of sps as electrical signed rate. The
  // laptop multiplies by ds to recover logical positive/negative travel.
  long electricalSpeedSps = effectiveLogicalSpeedSps * directionSign;
  char line[256];
  snprintf(
      line,
      sizeof(line),
      "{\"v\":1,\"t\":\"s\",\"q\":%lu,\"d4\":%d,\"d5\":%d,"
      "\"d6\":%d,\"d8\":%d,\"lp\":%d,\"ln\":%d,\"b\":%d,"
      "\"r\":\"%s\",\"sps\":%ld,\"csps\":%ld,\"ds\":%d,"
      "\"m\":%d,\"h\":%d,\"a\":%d,\"e\":%d,\"mv\":%d,\"st\":%d,\"p\":%ld,"
      "\"g\":%ld,\"c\":%u}",
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
      directionSign,
      (int)controlMode,
      positionHomed ? 1 : 0,
      d4OffObservedSinceBoot ? 1 : 0,
      emergencyStopLatched ? 1 : 0,
      moving ? 1 : 0,
      (int)motionState,
      stepper.currentPosition(),
      reportedTargetSteps,
      activeCommandId);
  Serial.println(line);
}

void setup() {
  Serial.begin(9600);

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
  Serial.println(F("USB controls: V1 S, D, M, H, G, X, E1, E0; compact JSON status at 9600 baud."));
  reportLimitLevels(digitalRead(PIN_LIMIT_POS), digitalRead(PIN_LIMIT_NEG));
}

void loop() {
  pollUsbCommands();

  int runRaw = digitalRead(PIN_RUN);
  int directionRaw = digitalRead(PIN_DIR);
  int positiveRaw = digitalRead(PIN_LIMIT_POS);
  int negativeRaw = digitalRead(PIN_LIMIT_NEG);
  bool d4On = runRaw == LOW;
  if (!d4On) d4OffObservedSinceBoot = true;
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
    if (Serial) {
      lastStatusSignature = statusSignature;
      lastStatusAtMs = nowMs;
      statusDirty = false;
    }
  }
}

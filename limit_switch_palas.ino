#include <math.h>
#include <util/atomic.h>

const int PIN_STEP = 3;
const int PIN_DRIVER_DIR = 2;
// STEP=D3, DIR=D2. Timer1 is the sole STEP-edge owner in every motion mode.

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
// stays reviewable in source. Timer1 preserves a 5 us STEP pulse.
const long MIN_SPEED_SPS = 25L;        // nearest pulse rate to 0.1 mm/s
const long MAX_SPEED_SPS = 2520L;      // nearest pulse rate to 10.0 mm/s
const long DEFAULT_SPEED_SPS = 378L;   // nearest pulse rate to 1.5 mm/s
const long HOME_SPEED_SPS = 378L;      // fixed conservative homing speed
const float FIXED_ACCELERATION_SPS2 = 1260.0;  // approximately 5 mm/s^2
long targetSpeedSps = DEFAULT_SPEED_SPS;

// --- Unified Timer1 pulse engine ---
// Timer1 owns every STEP edge in Local Velocity, bounded Web Position, and
// Home. Modes differ only in authorization and whether a finite target exists;
// they cannot silently acquire different speed implementations. The main loop
// checks D4, D5, qualified D6/D8, and E-STOP continuously. It updates the
// acceleration ramp without blocking, while the ISR applies pending compare
// values only immediately after a pulse so shortening an interval cannot miss
// a compare event. Finite targets stop in the ISR at the exact signed count.
const unsigned long PULSE_TIMER_HZ = F_CPU / 64UL;
const unsigned long PULSE_RAMP_UPDATE_US = 1000UL;
const unsigned long PULSE_RAMP_MAX_UPDATE_US = 10000UL;
const long PULSE_RAMP_START_SPS = 50L;  // approximately sqrt(2 * 1260)

enum PulseEngineMode : byte {
  PULSE_ENGINE_IDLE = 0,
  PULSE_ENGINE_LOCAL = 1,
  PULSE_ENGINE_POSITION = 2,
  PULSE_ENGINE_HOME = 3,
};

volatile bool pulseTimerEnabled = false;
volatile PulseEngineMode pulseTimerMode = PULSE_ENGINE_IDLE;
volatile bool pulseTimerFiniteTarget = false;
volatile bool pulseTimerTargetReached = false;
volatile int pulseTimerDirection = 1;
volatile long pulseTimerPosition = 0L;
volatile long pulseTimerTarget = 0L;
volatile long pulseTimerScheduledSpeedSps = 0L;
volatile uint16_t pulseTimerPendingCompare = 0;
volatile long pulseTimerPendingSpeedSps = 0L;
volatile bool pulseTimerComparePending = false;

float pulseRampSpeedSps = 0.0;
long pulseRampCruiseSpeedSps = 0L;
unsigned long pulseRampLastUpdateUs = 0UL;

// --- Fixed physical direction calibration ---
// This Normal mapping was physically verified across the complete stroke:
// positive motion goes toward D6 and negative motion goes toward D8. It is a
// safety property, not an operator setting. Do not add a runtime DIR-inversion
// command: changing electrical polarity without changing physical endpoint
// semantics can make motion approach D8 while the firmware checks D6.
const int FIXED_DIRECTION_SIGN = 1;
// Physical bring-up after explicit D2 output initialization established the
// required electrical polarity: D2 LOW moved toward D6, so physical
// positive/toward-D6 must drive LOW and physical negative/toward-D8 must drive
// HIGH. Keep this compile-time calibration separate from the immutable physical
// contract reported by FIXED_DIRECTION_SIGN; it is not a runtime inversion.
const int DRIVER_DIR_POSITIVE_LEVEL = LOW;
const int DRIVER_DIR_NEGATIVE_LEVEL = HIGH;

// --- Switch pins ---
const int PIN_RUN = 4;
const int PIN_DIR = 5;
const int PIN_LIMIT_POS = 6;
const int PIN_LIMIT_NEG = 8;  // D7 is reserved by the Yún Linux handshake.
const int PIN_DRIVER_ENABLE_NEG = 9;
const int PIN_DRO_CLOCK = 10;
const int PIN_DRO_DATA = 11;

// --- Read-only AbsoluteDRO Plus input ---
// The level shifter presents 5 V logic to the Yún:
//   D10/PB6/PCINT6 = scale-generated clock
//   D11/PB7        = scale-generated data
// The AbsoluteDRO Plus sends 52 bits continuously while its internal REQ pad
// is grounded. Four leading 0xF nibbles make a self-synchronizing header, so
// capture does not depend on a guessed inter-frame delay. D10 and D11 are
// diagnostic inputs only; no DRO value participates in a motion decision.
const byte DRO_FRAME_BITS = 52;
const byte DRO_HEADER_BITS = 16;
const byte DRO_FRAME_BYTES = 7;
const unsigned long DRO_STALE_MS = 250UL;
const unsigned long DRO_STATUS_MS = 200UL;

volatile byte droCaptureFrame[DRO_FRAME_BYTES];
volatile byte droCompletedFrame[DRO_FRAME_BYTES];
volatile byte droCaptureBit = 0;
volatile byte droHeaderOnes = 0;
volatile bool droFrameReady = false;
volatile byte droDroppedFrames = 0;

bool droHasPosition = false;
long droPositionHundredthsMm = 0L;
long droReferenceHundredthsMm = 0L;
unsigned long droLastValidAtMs = 0UL;
unsigned long droValidFrames = 0UL;
byte droRejectedFrames = 0;

// All four inputs use INPUT_PULLUP. The installed magnetic switches are
// passive normally-open contacts to GND: open/clear is HIGH and magnet-active
// is LOW. A broken limit wire therefore looks clear and is not fail-safe.
const int POS_LIMIT_ACTIVE_LEVEL = LOW;
const int NEG_LIMIT_ACTIVE_LEVEL = LOW;

// --- Magnetic-limit input qualification ---
// A raw LOW must remain continuous for this bounded interval before it may
// stop motion or set a persistent endpoint latch. Bench recordings captured
// isolated raw edges shortly after driver enable which disappeared before the
// next status frame but nevertheless latched an endpoint. A 5 ms assertion
// qualification rejects those electrical transients while adding at most
// 0.05 mm of stopping travel at the 10 mm/s firmware maximum. Releases remain
// immediate; the persistent directional latch still prevents renewed travel
// into a confirmed endpoint until motion away from that endpoint is selected.
// No delay() is used, so D4, D5, E-STOP, transport, and STEP service continue.
const unsigned long LIMIT_ASSERT_QUALIFY_US = 5000UL;

struct QualifiedLimitInput {
  bool qualifiedActive;
  bool assertionPending;
  unsigned long assertionStartedUs;
  byte rejectedGlitches;
};

QualifiedLimitInput positiveLimitInput = {false, false, 0UL, 0};
QualifiedLimitInput negativeLimitInput = {false, false, 0UL, 0};

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
long activePulseTargetSteps = 0;
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
// c is the active command number, e is the software E-STOP latch, and lx packs
// qualified limit state plus diagnostic-only rejected-edge counters. dc marks
// the read-only DRO decoder; df is freshness; dr/dd are absolute/reference
// displacement in 0.01 mm; da is sample age; dq counts valid frames; and dx
// packs rejected frames in its high byte and ISR-overrun drops in its low byte.
const unsigned long STATUS_HEARTBEAT_MS = 1000UL;
const unsigned long STATUS_MOTION_MS = 100UL;
unsigned long statusSequence = 0;
bool statusDirty = true;

// --- Emitted STEP instrumentation ---
// The scheduled speed is not proof of how often D3 was actually pulsed.
// Measure the change in the shared pulse-position counter over a fixed window:
// The same Timer1 counter advances it in every mode. This remains open-loop
// electrical evidence: it proves D3 pulse
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
// The compact status has a checked upper bound below this size, including its
// newline and terminator. Keeping one named size prevents the formatter and
// shared transport buffer from drifting apart as telemetry evolves.
const unsigned int STATUS_FRAME_SIZE = 384;
char networkTxActive[STATUS_FRAME_SIZE];
unsigned int networkTxActiveLength = 0;
unsigned int networkTxActiveOffset = 0;
unsigned int usbTxActiveOffset = 0;
bool networkTxActiveSharedWithUsb = false;
char networkTxPendingAck[80];
bool networkAckPending = false;

ISR(PCINT0_vect) {
  // PCINT0_vect fires on both D10 edges. AbsoluteDRO/Digimatic data is valid
  // on the falling clock edge, so rising edges return immediately. Direct
  // port access keeps this ISR short enough to coexist with Timer1 stepping.
  byte portB = PINB;
  if (portB & _BV(PB6)) return;
  bool dataHigh = (portB & _BV(PB7)) != 0;

  if (droCaptureBit == 0) {
    if (!dataHigh) {
      droHeaderOnes = 0;
      return;
    }
    if (droHeaderOnes < DRO_HEADER_BITS) ++droHeaderOnes;
    if (droHeaderOnes == DRO_HEADER_BITS) {
      // The first two bytes are the known 16-one header. Clear the remainder
      // before collecting bits 16..51 LSB-first.
      droCaptureFrame[0] = 0xFF;
      droCaptureFrame[1] = 0xFF;
      for (byte index = 2; index < DRO_FRAME_BYTES; ++index) {
        droCaptureFrame[index] = 0;
      }
      droCaptureBit = DRO_HEADER_BITS;
    }
    return;
  }

  if (dataHigh) {
    droCaptureFrame[droCaptureBit >> 3] |=
        _BV(droCaptureBit & 0x07);
  }
  ++droCaptureBit;
  if (droCaptureBit < DRO_FRAME_BITS) return;

  if (!droFrameReady) {
    for (byte index = 0; index < DRO_FRAME_BYTES; ++index) {
      droCompletedFrame[index] = droCaptureFrame[index];
    }
    droFrameReady = true;
  } else if (droDroppedFrames < 255) {
    ++droDroppedFrames;
  }
  droCaptureBit = 0;
  droHeaderOnes = 0;
}

ISR(TIMER1_COMPA_vect) {
  if (!pulseTimerEnabled) return;
  digitalWrite(PIN_STEP, HIGH);
  delayMicroseconds(5);
  digitalWrite(PIN_STEP, LOW);
  pulseTimerPosition += pulseTimerDirection;

  if (pulseTimerFiniteTarget &&
      ((pulseTimerDirection > 0 &&
        pulseTimerPosition >= pulseTimerTarget) ||
       (pulseTimerDirection < 0 &&
        pulseTimerPosition <= pulseTimerTarget))) {
    pulseTimerEnabled = false;
    pulseTimerTargetReached = true;
    pulseTimerScheduledSpeedSps = 0L;
    pulseTimerComparePending = false;
    TIMSK1 &= ~_BV(OCIE1A);
    return;
  }

  // OCR1A is changed only at this pulse boundary. Updating a shorter compare
  // value asynchronously while TCNT1 is already beyond it can otherwise defer
  // the next match until timer wrap and create a large speed discontinuity.
  if (pulseTimerComparePending) {
    OCR1A = pulseTimerPendingCompare;
    pulseTimerScheduledSpeedSps = pulseTimerPendingSpeedSps;
    pulseTimerComparePending = false;
  }
}

byte droNibble(const byte frame[DRO_FRAME_BYTES], byte digitIndex) {
  byte packed = frame[digitIndex >> 1];
  if (digitIndex & 0x01) packed >>= 4;
  return packed & 0x0F;
}

bool decodeDroFrame(
    const byte frame[DRO_FRAME_BYTES],
    long *positionHundredthsMm) {
  // AbsoluteDRO Plus follows the 13-nibble Digimatic ordering, with every
  // nibble transmitted least-significant bit first:
  //   d1..d4=F, d5=sign, d6..d11=xxxx.xx, d12=2 decimals, d13=millimetres.
  for (byte digit = 0; digit < 4; ++digit) {
    if (droNibble(frame, digit) != 0x0F) return false;
  }
  byte sign = droNibble(frame, 4);
  if (sign != 0 && sign != 8) return false;

  long magnitudeHundredthsMm = 0L;
  for (byte digit = 5; digit <= 10; ++digit) {
    byte value = droNibble(frame, digit);
    if (value > 9) return false;
    magnitudeHundredthsMm = magnitudeHundredthsMm * 10L + value;
  }
  if (droNibble(frame, 11) != 2) return false;
  if (droNibble(frame, 12) != 0) return false;

  *positionHundredthsMm =
      sign == 8 ? -magnitudeHundredthsMm : magnitudeHundredthsMm;
  return true;
}

void pollDroFrames() {
  byte frame[DRO_FRAME_BYTES];
  bool ready = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (droFrameReady) {
      for (byte index = 0; index < DRO_FRAME_BYTES; ++index) {
        frame[index] = droCompletedFrame[index];
      }
      droFrameReady = false;
      ready = true;
    }
  }
  if (!ready) return;

  long decodedHundredthsMm = 0L;
  if (!decodeDroFrame(frame, &decodedHundredthsMm)) {
    if (droRejectedFrames < 255) ++droRejectedFrames;
    return;
  }

  unsigned long nowMs = millis();
  if (!droHasPosition) {
    droReferenceHundredthsMm = decodedHundredthsMm;
    droHasPosition = true;
    statusDirty = true;
  }
  droPositionHundredthsMm = decodedHundredthsMm;
  droLastValidAtMs = nowMs;
  if (droValidFrames < 0xFFFFFFFFUL) ++droValidFrames;
}

bool droIsFresh(unsigned long nowMs) {
  return droHasPosition && nowMs - droLastValidAtMs <= DRO_STALE_MS;
}

long droSampleAgeMs(unsigned long nowMs) {
  if (!droHasPosition) return -1L;
  unsigned long ageMs = nowMs - droLastValidAtMs;
  return ageMs > 0x7FFFFFFFUL ? 0x7FFFFFFFL : (long)ageMs;
}

unsigned long packedDroDiagnostics() {
  byte dropped = 0;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    dropped = droDroppedFrames;
  }
  return ((unsigned long)droRejectedFrames << 8) | dropped;
}

uint16_t pulseCompareForSpeed(long speedSps) {
  unsigned long magnitude = (unsigned long)labs(speedSps);
  if (magnitude < 1UL) magnitude = 1UL;
  unsigned long timerTicks =
      (PULSE_TIMER_HZ + magnitude / 2UL) / magnitude;
  if (timerTicks < 2UL) timerTicks = 2UL;
  if (timerTicks > 65536UL) timerTicks = 65536UL;
  return (uint16_t)(timerTicks - 1UL);
}

bool pulseEngineIsEnabled() {
  bool enabled = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    enabled = pulseTimerEnabled;
  }
  return enabled;
}

long currentPulsePosition() {
  long position = 0L;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    position = pulseTimerPosition;
  }
  return position;
}

void setPulsePosition(long position) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pulseTimerPosition = position;
  }
}

long pulseEngineSignedScheduledSpeed() {
  long speed = 0L;
  int direction = 1;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    speed = pulseTimerScheduledSpeedSps;
    direction = pulseTimerDirection;
  }
  return direction * speed;
}

bool pulseEngineTargetWasReached() {
  bool reached = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    reached = pulseTimerTargetReached;
  }
  return reached;
}

void stopPulseEngine() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pulseTimerEnabled = false;
    TIMSK1 &= ~_BV(OCIE1A);
    pulseTimerMode = PULSE_ENGINE_IDLE;
    pulseTimerFiniteTarget = false;
    pulseTimerTargetReached = false;
    pulseTimerScheduledSpeedSps = 0L;
    pulseTimerPendingSpeedSps = 0L;
    pulseTimerComparePending = false;
  }
  digitalWrite(PIN_STEP, LOW);
  pulseRampSpeedSps = 0.0;
  pulseRampCruiseSpeedSps = 0L;
}

bool pulseEngineMatches(
    PulseEngineMode mode,
    int direction,
    long target,
    long cruiseSpeedSps) {
  bool matches = false;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    matches = pulseTimerEnabled &&
        pulseTimerMode == mode &&
        pulseTimerDirection == direction &&
        (!pulseTimerFiniteTarget || pulseTimerTarget == target) &&
        pulseRampCruiseSpeedSps == cruiseSpeedSps;
  }
  return matches;
}

void startPulseEngine(
    PulseEngineMode mode,
    int direction,
    bool finiteTarget,
    long target,
    long cruiseSpeedSps) {
  stopPulseEngine();
  long initialSpeedSps = cruiseSpeedSps < PULSE_RAMP_START_SPS
      ? cruiseSpeedSps
      : PULSE_RAMP_START_SPS;
  if (initialSpeedSps < 1L) initialSpeedSps = 1L;
  uint16_t initialCompare = pulseCompareForSpeed(initialSpeedSps);

  digitalWrite(
      PIN_DRIVER_DIR,
      direction > 0
          ? DRIVER_DIR_POSITIVE_LEVEL
          : DRIVER_DIR_NEGATIVE_LEVEL);
  digitalWrite(PIN_STEP, LOW);
  pulseRampSpeedSps = (float)initialSpeedSps;
  pulseRampCruiseSpeedSps = cruiseSpeedSps;
  pulseRampLastUpdateUs = micros();
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pulseTimerMode = mode;
    pulseTimerFiniteTarget = finiteTarget;
    pulseTimerTargetReached = false;
    pulseTimerDirection = direction;
    pulseTimerTarget = target;
    pulseTimerScheduledSpeedSps = initialSpeedSps;
    pulseTimerPendingSpeedSps = initialSpeedSps;
    pulseTimerComparePending = false;
    OCR1A = initialCompare;
    TCNT1 = 0;
    TIFR1 = _BV(OCF1A);
    pulseTimerEnabled = true;
    TIMSK1 |= _BV(OCIE1A);
  }
}

void queuePulseEngineSpeed(long speedSps) {
  if (speedSps < 1L) speedSps = 1L;
  uint16_t compare = pulseCompareForSpeed(speedSps);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (pulseTimerEnabled) {
      pulseTimerPendingCompare = compare;
      pulseTimerPendingSpeedSps = speedSps;
      pulseTimerComparePending = true;
    }
  }
}

void updatePulseEngineRamp() {
  bool enabled = false;
  bool finiteTarget = false;
  long position = 0L;
  long target = 0L;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    enabled = pulseTimerEnabled;
    finiteTarget = pulseTimerFiniteTarget;
    position = pulseTimerPosition;
    target = pulseTimerTarget;
  }
  if (!enabled) return;

  unsigned long nowUs = micros();
  unsigned long elapsedUs = nowUs - pulseRampLastUpdateUs;
  if (elapsedUs < PULSE_RAMP_UPDATE_US) return;
  pulseRampLastUpdateUs = nowUs;
  if (elapsedUs > PULSE_RAMP_MAX_UPDATE_US) {
    elapsedUs = PULSE_RAMP_MAX_UPDATE_US;
  }

  float desiredSpeedSps = (float)pulseRampCruiseSpeedSps;
  if (finiteTarget) {
    unsigned long remainingPulses = (unsigned long)labs(target - position);
    float brakingSpeedSps = sqrt(
        2.0 * FIXED_ACCELERATION_SPS2 * (float)remainingPulses);
    if (brakingSpeedSps < desiredSpeedSps) {
      desiredSpeedSps = brakingSpeedSps;
    }
  }

  float maxChangeSps =
      FIXED_ACCELERATION_SPS2 * ((float)elapsedUs / 1000000.0);
  if (pulseRampSpeedSps < desiredSpeedSps) {
    pulseRampSpeedSps += maxChangeSps;
    if (pulseRampSpeedSps > desiredSpeedSps) {
      pulseRampSpeedSps = desiredSpeedSps;
    }
  } else if (pulseRampSpeedSps > desiredSpeedSps) {
    pulseRampSpeedSps -= maxChangeSps;
    if (pulseRampSpeedSps < desiredSpeedSps) {
      pulseRampSpeedSps = desiredSpeedSps;
    }
  }

  long requestedSpeedSps = (long)(pulseRampSpeedSps + 0.5);
  if (requestedSpeedSps < 1L) requestedSpeedSps = 1L;
  queuePulseEngineSpeed(requestedSpeedSps);
}

constexpr bool limitBlocksPhysicalDirection(
    int physicalDirection,
    bool positiveLimit,
    bool negativeLimit) {
  return (physicalDirection > 0 && positiveLimit) ||
      (physicalDirection < 0 && negativeLimit);
}

// Compile-time electrical-to-physical direction calibration. The D6-end
// bring-up established that D2 LOW is the physical positive/toward-D6 level;
// D2 HIGH is therefore the physical negative/toward-D8 retreat level.
static_assert(
    DRIVER_DIR_POSITIVE_LEVEL == LOW,
    "physical positive/toward-D6 must drive D2 LOW");
static_assert(
    DRIVER_DIR_NEGATIVE_LEVEL == HIGH,
    "physical negative/toward-D8 must drive D2 HIGH");

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

void initializeQualifiedLimit(
    QualifiedLimitInput *input,
    bool rawActive,
    unsigned long nowUs) {
  input->qualifiedActive = false;
  input->assertionPending = rawActive;
  input->assertionStartedUs = nowUs;
  input->rejectedGlitches = 0;
}

bool updateQualifiedLimit(
    QualifiedLimitInput *input,
    bool rawActive,
    unsigned long nowUs) {
  if (input->qualifiedActive) {
    // Release immediately so a carriage can retreat from a confirmed end.
    // The endpoint latch remains set and continues to block travel into it.
    if (!rawActive) {
      input->qualifiedActive = false;
      input->assertionPending = false;
      statusDirty = true;
    }
    return input->qualifiedActive;
  }

  if (!rawActive) {
    if (input->assertionPending) {
      input->assertionPending = false;
      if (input->rejectedGlitches < 255) ++input->rejectedGlitches;
      statusDirty = true;
    }
    return false;
  }

  if (!input->assertionPending) {
    input->assertionPending = true;
    input->assertionStartedUs = nowUs;
    return false;
  }

  // Unsigned subtraction is intentionally wrap-safe across micros() rollover.
  if (nowUs - input->assertionStartedUs >= LIMIT_ASSERT_QUALIFY_US) {
    input->qualifiedActive = true;
    input->assertionPending = false;
    statusDirty = true;
  }
  return input->qualifiedActive;
}

void updatePhysicalEndpointLatches(
    bool positiveLimitActive,
    bool negativeLimitActive) {
  // The carriage cannot physically occupy both ends of its stroke. When one
  // qualified endpoint is exclusively active, it is authoritative and clears
  // stale history from the opposite endpoint. Without this rule, visiting D8 and
  // later reaching D6 leaves both latches set; each selected direction then
  // appears blocked even though the raw switches correctly identify D6.
  //
  // If both qualified inputs are active simultaneously, retain both latches
  // and block both directions. That state is treated conservatively as a
  // wiring/sensor fault rather than guessing which endpoint is real.
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
  stopPulseEngine();
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
  stopPulseEngine();
  long current = currentPulsePosition();
  reportedTargetSteps = current;
  activePulseTargetSteps = current;
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
  setPulsePosition(0L);
  reportedTargetSteps = 0L;
  activePulseTargetSteps = 0L;
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
  return digitalRead(PIN_RUN) == HIGH &&
      !activeWebMotion &&
      !pulseEngineIsEnabled();
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

  if (negativeLimitInput.qualifiedActive) {
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
  stopPulseEngine();
  setPulsePosition(0L);
  activePulseTargetSteps = -MAX_HOME_SEARCH_STEPS;
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
  stopPulseEngine();
  setPulsePosition(0L);
  long target = deltaSteps;
  targetSpeedSps = speedSps;
  activeCommandId = (unsigned int)commandId;
  activeWebMotion = true;
  homingMotion = false;
  activePhysicalDirection = requestedDirection;
  reportedTargetSteps = target;
  activePulseTargetSteps = target;
  motionState = STATE_WEB_MOVING;
  motionReason = "none";
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

bool networkStatusBufferAvailable() {
  bool usbBusy = networkTxActiveSharedWithUsb &&
      usbTxActiveOffset < networkTxActiveLength;
  return networkTxActiveOffset >= networkTxActiveLength &&
      !usbBusy &&
      !networkAckPending;
}

void startNetworkStatusTransmission(unsigned int formattedLength) {
  // The status formatter writes directly into the one immutable transport
  // buffer. Avoiding a same-sized local copy preserves AVR stack headroom while
  // USB and Serial1 continue to drain it with independent offsets.
  networkTxActive[formattedLength] = '\n';
  networkTxActive[formattedLength + 1] = '\0';
  networkTxActiveLength = formattedLength + 1;
  networkTxActiveOffset = 0;
  usbTxActiveOffset = Serial ? 0 : networkTxActiveLength;
  networkTxActiveSharedWithUsb = true;
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
  // lx packs qualified state and two saturating diagnostic-only counters
  // without expanding this AVR frame excessively:
  //   bit 17=D6 qualified active, bit 16=D8 qualified active,
  //   bits 15..8=D6 rejected edges, bits 7..0=D8 rejected edges.
  // The counters never participate in a motion decision. They make rejected
  // raw edges visible even when short raw LOW/HIGH frames are overwritten in
  // transport.
  unsigned long packedLimitDiagnostics =
      ((unsigned long)(positiveLimitInput.qualifiedActive ? 1 : 0) << 17) |
      ((unsigned long)(negativeLimitInput.qualifiedActive ? 1 : 0) << 16) |
      ((unsigned long)positiveLimitInput.rejectedGlitches << 8) |
      (unsigned long)negativeLimitInput.rejectedGlitches;
  unsigned long statusNowMs = millis();
  long droDisplacementHundredthsMm = droHasPosition
      ? droPositionHundredthsMm - droReferenceHundredthsMm
      : 0L;
  // A busy transport keeps its immutable frame. The next bounded status
  // interval will format a fresh snapshot after that frame has drained.
  if (!networkStatusBufferAvailable()) return false;
  int formattedLength = snprintf(
      networkTxActive,
      sizeof(networkTxActive),
      "{\"v\":1,\"t\":\"s\",\"q\":%lu,\"d4\":%d,\"d5\":%d,"
      "\"d6\":%d,\"d8\":%d,\"lx\":%lu,\"lp\":%d,\"ln\":%d,\"b\":%d,"
      "\"r\":\"%s\",\"sps\":%ld,\"csps\":%ld,\"aps\":%ld,\"ds\":%d,"
      "\"en\":%d,\"ut\":1,\"dc\":1,\"df\":%d,\"dr\":%ld,\"dd\":%ld,"
      "\"da\":%ld,\"dq\":%lu,\"dx\":%lu,"
      "\"m\":%d,\"h\":%d,\"a\":%d,\"e\":%d,\"mv\":%d,\"st\":%d,\"p\":%ld,"
      "\"g\":%ld,\"c\":%u,\"o\":%d}",
      ++statusSequence,
      runRaw,
      directionRaw,
      positiveRaw,
      negativeRaw,
      packedLimitDiagnostics,
      positiveLimitLatched ? 1 : 0,
      negativeLimitLatched ? 1 : 0,
      blocked ? 1 : 0,
      reason,
      electricalSpeedSps,
      targetSpeedSps,
      measuredPulseRateSps,
      FIXED_DIRECTION_SIGN,
      driverOutputEnabled ? 1 : 0,
      droIsFresh(statusNowMs) ? 1 : 0,
      droHasPosition ? droPositionHundredthsMm : 0L,
      droDisplacementHundredthsMm,
      droSampleAgeMs(statusNowMs),
      droValidFrames,
      packedDroDiagnostics(),
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
  // Never place syntactically truncated JSON on either transport. The desktop
  // contract test also constructs the numeric worst case and keeps it below
  // STATUS_FRAME_SIZE, so this guard is a final fail-closed invariant.
  if (formattedLength < 0 ||
      (unsigned int)formattedLength >= sizeof(networkTxActive) - 1) {
    return false;
  }
  startNetworkStatusTransmission((unsigned int)formattedLength);
  return true;
}

void setup() {
  Serial.begin(9600);
  Serial1.begin(115200);

  pinMode(PIN_RUN, INPUT_PULLUP);
  pinMode(PIN_DIR, INPUT_PULLUP);
  pinMode(PIN_LIMIT_POS, INPUT_PULLUP);
  pinMode(PIN_LIMIT_NEG, INPUT_PULLUP);
  // SparkFun BOB-12009 already supplies the level-shifter pull-ups. Do not
  // enable the Yún's 5 V internal pull-ups on these translated inputs.
  pinMode(PIN_DRO_CLOCK, INPUT);
  pinMode(PIN_DRO_DATA, INPUT);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    PCMSK0 |= _BV(PCINT6);
    PCIFR = _BV(PCIF0);
    PCICR |= _BV(PCIE0);
  }
  unsigned long limitInitUs = micros();
  initializeQualifiedLimit(
      &positiveLimitInput,
      digitalRead(PIN_LIMIT_POS) == POS_LIMIT_ACTIVE_LEVEL,
      limitInitUs);
  initializeQualifiedLimit(
      &negativeLimitInput,
      digitalRead(PIN_LIMIT_NEG) == NEG_LIMIT_ACTIVE_LEVEL,
      limitInitUs);
  // Set the output latch LOW before enabling the pin driver so D9 cannot
  // produce an enable glitch during setup.
  digitalWrite(PIN_DRIVER_ENABLE_NEG, DRIVER_OUTPUT_DISABLED_LEVEL);
  pinMode(PIN_DRIVER_ENABLE_NEG, OUTPUT);
  driverOutputEnabled = false;
  d4OffObservedSinceBoot = digitalRead(PIN_RUN) == HIGH;

  // Timer1 drives STEP/DIR directly, so their GPIO direction must be owned
  // explicitly here. The former AccelStepper object configured these pins as
  // an implicit constructor side effect; removing that object while
  // unifying the pulse engine left D2/D3 as inputs and reduced digitalWrite()
  // to pull-up control. Keep the driver disabled above, preload both output
  // latches LOW, and only then enable the pin drivers so setup cannot create a
  // spurious step or direction transition at the DM542T.
  digitalWrite(PIN_STEP, LOW);
  digitalWrite(PIN_DRIVER_DIR, LOW);
  pinMode(PIN_STEP, OUTPUT);
  pinMode(PIN_DRIVER_DIR, OUTPUT);

  // Timer1 CTC at F_CPU/64. The compare interrupt remains disabled until an
  // authorized motion in either mode has completed the 200 ms driver wake-up.
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  TIMSK1 &= ~_BV(OCIE1A);

  Serial.println(F("Stepper ready in stopped Local Velocity mode."));
  Serial.println(F("D4/D5 run Local Velocity; Web Position uses D4 arm and D5 direction."));
  Serial.println(F("USB and Yún-Linux controls share V1 S, M, H, G, X, E1, E0."));
  Serial.println(F("Timer1 owns Local/Web/Home STEP timing; DIR is fixed Normal."));
  Serial.println(F("D9 disables DM542T holding current while stopped."));
  Serial.println(F("D10 clock/D11 data read AbsoluteDRO Plus diagnostically only."));
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
  pollDroFrames();

  int runRaw = digitalRead(PIN_RUN);
  int directionRaw = digitalRead(PIN_DIR);
  int positiveRaw = digitalRead(PIN_LIMIT_POS);
  int negativeRaw = digitalRead(PIN_LIMIT_NEG);
  bool d4On = runRaw == LOW;
  if (!d4On) d4OffObservedSinceBoot = true;
  releaseExpiredOwner();
  bool d4MotionArmed = d4On && d4OffObservedSinceBoot;
  bool d5Reverse = directionRaw == LOW;
  unsigned long limitNowUs = micros();
  bool positiveLimitActive = updateQualifiedLimit(
      &positiveLimitInput,
      positiveRaw == POS_LIMIT_ACTIVE_LEVEL,
      limitNowUs);
  bool negativeLimitActive = updateQualifiedLimit(
      &negativeLimitInput,
      negativeRaw == NEG_LIMIT_ACTIVE_LEVEL,
      limitNowUs);

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
      disableDriverOutput();
    } else if (!d4MotionArmed) {
      statusReason = d4On ? "boot_disarmed" : "run_off";
      motionState = STATE_LOCAL_STOPPED;
      disableDriverOutput();
    } else if (!driverReadyForMotion()) {
      statusReason = "driver_wakeup";
      motionState = STATE_LOCAL_STOPPED;
      stopPulseEngine();
    } else {
      statusReason = "none";
      motionState = STATE_LOCAL_MOVING;
      if (!pulseEngineMatches(
              PULSE_ENGINE_LOCAL,
              physicalDirection,
              0L,
              targetSpeedSps)) {
        startPulseEngine(
            PULSE_ENGINE_LOCAL,
            physicalDirection,
            false,
            0L,
            targetSpeedSps);
      }
      updatePulseEngineRamp();
      effectivePhysicalSpeedSps = pulseEngineSignedScheduledSpeed();
      moving = pulseEngineIsEnabled();
      // Position is diagnostic open-loop telemetry only.
      reportedTargetSteps = currentPulsePosition();
    }
    motionReason = statusReason;
  } else {
    if (activeWebMotion) {
      // Keep clearing the endpoint behind an armed Web Position move. Clearing
      // only in handleMoveCommand() is too early: the 5 ms qualified input can
      // still be active during initial departure and re-latch before the
      // carriage releases the switch. The destination check below uses the
      // current qualified inputs, so this never suppresses the limit ahead.
      if (d4MotionArmed) clearOppositeLimitLatch(activePhysicalDirection);

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
          stopStepperImmediately();
          establishD8Reference();
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
        stopPulseEngine();
      } else {
        motionReason = "none";
        if (pulseEngineTargetWasReached()) {
          long finalPosition = currentPulsePosition();
          bool exhaustedHomeSearch = homingMotion;
          stopPulseEngine();
          activeWebMotion = false;
          homingMotion = false;
          activePhysicalDirection = 0;
          moving = false;
          effectivePhysicalSpeedSps = 0L;
          reportedTargetSteps = finalPosition;
          activePulseTargetSteps = finalPosition;
          motionState = exhaustedHomeSearch
              ? STATE_WEB_ABORTED
              : STATE_WEB_COMPLETED;
          motionReason = exhaustedHomeSearch
              ? "home_search_exhausted"
              : "move_complete";
          disableDriverOutput();
          statusDirty = true;
        } else {
          PulseEngineMode requiredMode = homingMotion
              ? PULSE_ENGINE_HOME
              : PULSE_ENGINE_POSITION;
          long requiredSpeedSps = homingMotion
              ? HOME_SPEED_SPS
              : targetSpeedSps;
          bool engineMatches = pulseEngineMatches(
              requiredMode,
              activePhysicalDirection,
              activePulseTargetSteps,
              requiredSpeedSps);
          // If the ISR reached the finite target between the earlier check and
          // this point, leave it stopped for completion handling next loop.
          // Never restart a just-completed bounded move.
          if (!engineMatches && !pulseEngineTargetWasReached()) {
            startPulseEngine(
                requiredMode,
                activePhysicalDirection,
                true,
                activePulseTargetSteps,
                requiredSpeedSps);
          }
          updatePulseEngineRamp();
          moving = pulseEngineIsEnabled();
          effectivePhysicalSpeedSps = pulseEngineSignedScheduledSpeed();
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

  unsigned long nowMs = millis();
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
      ((unsigned long)driverOutputEnabled << 16) |
      ((unsigned long)droIsFresh(nowMs) << 17);
  static unsigned long lastStatusSignature = 0xFFFFFFFFUL;
  static unsigned long lastStatusAtMs = 0UL;
  static unsigned long lastStatusDroValidFrames = 0UL;
  bool motionUpdateDue = moving && nowMs - lastStatusAtMs >= STATUS_MOTION_MS;
  bool droUpdateDue = droValidFrames != lastStatusDroValidFrames &&
      nowMs - lastStatusAtMs >= DRO_STATUS_MS;
  if (statusDirty || statusSignature != lastStatusSignature || motionUpdateDue ||
      droUpdateDue ||
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
    lastStatusDroValidFrames = droValidFrames;
    // A status snapshot may be dropped if an older USB or network frame is
    // still draining. Do not fast-loop the report; the next bounded heartbeat
    // refreshes each consumer without delaying STEP generation.
    statusDirty = false;
  }
}

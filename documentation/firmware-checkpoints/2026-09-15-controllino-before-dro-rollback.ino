// CONTROLLINO MAXI Automation motion controller (100.101.00)
// Lean successor to limit_switch_palas.ino; the original remains unchanged.
//
// X1 wiring (5 V common-anode SRX02-S inputs):
//   DO1 / Arduino D3 -> STEP-    X1 5 V -> STEP+
//   DO3 / Arduino D5 -> DIR-     X1 5 V -> DIR+
//   DO5 / Arduino D7 -> EN-      X1 5 V -> EN+
//   DO2 / Arduino D4 -> continuous servo signal; external 9V supply, common GND
//
// DRO: X1 DO0 / D2 = clock (HV1), DO4 / D6 = data (HV2), both INPUT.
// Shifter HV=5V, LV and DRO +=3V3, common GND; reader REQ grounded.
// Installed hardware has no physical run/direction switches or limits.
// V1 R-1/R0/R1 provides software Reverse/Stop/Forward in Local Speed mode.
// H remains protocol-compatible but is rejected until a home switch exists.
// D6/D8=-1 report absent limits. DRO is read-only, never a motion interlock.

#include <SPI.h>
#include <Ethernet.h>
#include <math.h>
#include <util/atomic.h>

// ---------- 1. Pin map and fixed configuration ----------
constexpr byte PIN_STEP = 3, PIN_DIR = 5, PIN_ENABLE = 7, PIN_SERVO = 4;
constexpr byte PIN_DRO_CLOCK = 2, PIN_DRO_DATA = 6;
constexpr byte PIN_RELAY5 = 27;  // Solenoid 4: R5, not X1 DO5
bool relay5On = false;
void setRelay5(bool on) { relay5On = on; digitalWrite(PIN_RELAY5, on ? HIGH : LOW); }
constexpr byte STEP_IDLE = HIGH, STEP_ACTIVE = LOW;
constexpr byte DRIVER_ENABLED = HIGH, DRIVER_DISABLED = LOW;
constexpr byte DIR_FORWARD = HIGH, DIR_REVERSE = LOW;  // verified 2026-09-03

constexpr long MIN_SPS = 25, MAX_SPS = 2520, DEFAULT_SPS = 1000;
constexpr long MAX_RELATIVE_PULSES = 34565;  // conservative; recalibrate SRX/motor
constexpr float ACCEL_SPS2 = 1260.0f;
constexpr unsigned long STEP_TIMER_HZ = F_CPU / 64UL;
constexpr unsigned long DRIVER_WAKE_MS = 250, OWNER_RELEASE_MS = 2000;
// Legacy B/P commands and bo/bp telemetry now control a continuous servo.
// Neutral is a starting calibration, NOT a measured guarantee of no rotation.
constexpr unsigned int SERVO_NEUTRAL_US = 1500;
// Nominal 270 degrees over 500–2500 us: 75° and 195° are 120° apart.
constexpr unsigned int SERVO_ON_US = 1056, SERVO_OFF_US = 1944;
constexpr unsigned int ESC_OFF_US = SERVO_NEUTRAL_US;
constexpr unsigned int ESC_TICKS_PER_US = F_CPU / 8UL / 1000000UL;

byte mac[] = {0x02, 0x43, 0x4F, 0x4E, 0x54, 0x01};
IPAddress ip(10, 77, 0, 10), dns(10, 77, 0, 2), gateway(10, 77, 0, 2);
IPAddress subnet(255, 255, 255, 0);
EthernetServer server(80);
// WIZnet has no software TCP accept backlog. Reserve listeners for the normal
// status poll, an operator command, and two diagnostic connections. Four is
// supported even by W5100; this firmware opens no outbound Ethernet sockets.
constexpr byte HTTP_LISTENERS = 4;

// ---------- 2. Hardware-timed STEP and positional servo outputs ----------
// esc* and bo/bp names retain the shared telemetry contract (no Servo library).
volatile bool pulseOn = false, pulseFinite = false, pulseReached = false;
volatile int8_t pulseDirection = 1;
volatile long pulsePosition = 0, pulseTarget = 0, scheduledSps = 0;
volatile uint16_t pendingCompare = 0;
volatile long pendingSps = 0;
volatile bool comparePending = false;
long cruiseSps = DEFAULT_SPS;
float rampSps = 0;
unsigned long rampAtUs = 0;

volatile unsigned int escTicks = ESC_OFF_US * ESC_TICKS_PER_US;
unsigned int escOnUs = SERVO_NEUTRAL_US;  // Run is neutral until explicitly adjusted
volatile bool escOn = false;
constexpr byte SERVO_OFF_SETTLE_FRAMES = 50;  // about 1 second at 50 Hz
volatile byte servoOffFrames = 0;
bool servoSwitchOn = false;  // last requested switch state, not measured position

uint16_t compareFor(long sps) {
  unsigned long ticks = (STEP_TIMER_HZ + sps / 2) / sps;
  return uint16_t(constrain(ticks, 2UL, 65536UL) - 1);
}

bool moving() { bool v; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { v = pulseOn; } return v; }
bool targetReached() { bool v; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { v = pulseReached; } return v; }
long position() { long v; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { v = pulsePosition; } return v; }
long signedSps() { long v; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { v = scheduledSps * pulseDirection; } return v; }

void stopPulses() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pulseOn = pulseFinite = pulseReached = comparePending = false;
    scheduledSps = 0;
    TIMSK1 &= ~_BV(OCIE1A);
  }
  digitalWrite(PIN_STEP, STEP_IDLE);
  rampSps = 0;
}

void startPulses(int8_t direction, bool finite, long target) {
  stopPulses();
  rampSps = min(cruiseSps, 50L);
  rampAtUs = micros();
  digitalWrite(PIN_DIR, direction > 0 ? DIR_FORWARD : DIR_REVERSE);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pulseDirection = direction; pulseFinite = finite; pulseTarget = target;
    pulseReached = false; scheduledSps = long(rampSps);
    OCR1A = compareFor(scheduledSps); TCNT1 = 0; TIFR1 = _BV(OCF1A);
    pulseOn = true; TIMSK1 |= _BV(OCIE1A);
  }
}

void serviceRamp() {
  if (!moving()) return;
  unsigned long now = micros(), elapsed = now - rampAtUs;
  if (elapsed < 1000) return;
  rampAtUs = now; elapsed = min(elapsed, 10000UL);
  float desired = cruiseSps;
  bool finite; long target;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { finite = pulseFinite; target = pulseTarget; }
  if (finite) desired = min(desired,
      sqrt(2.0f * ACCEL_SPS2 * labs(target - position())));
  float change = ACCEL_SPS2 * elapsed / 1000000.0f;
  rampSps += constrain(desired - rampSps, -change, change);
  long next = max(1L, long(rampSps + 0.5f));
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pendingCompare = compareFor(next); pendingSps = next; comparePending = true;
  }
}

void setEsc(bool on, byte offFrames = 0) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    escOn = on;
    servoOffFrames = on ? offFrames : 0;
    escTicks = (on ? escOnUs : ESC_OFF_US) * ESC_TICKS_PER_US;
  }
}

ISR(TIMER1_COMPA_vect) {
  if (!pulseOn) return;
  digitalWrite(PIN_STEP, STEP_ACTIVE); delayMicroseconds(5);
  digitalWrite(PIN_STEP, STEP_IDLE); pulsePosition += pulseDirection;
  if (pulseFinite && ((pulseDirection > 0 && pulsePosition >= pulseTarget) ||
      (pulseDirection < 0 && pulsePosition <= pulseTarget))) {
    pulseOn = false; pulseReached = true; scheduledSps = 0;
    TIMSK1 &= ~_BV(OCIE1A);
  } else if (comparePending) {
    OCR1A = pendingCompare; scheduledSps = pendingSps; comparePending = false;
  }
}
ISR(TIMER3_OVF_vect) {
  if (!escOn) { digitalWrite(PIN_SERVO, LOW); return; }
  digitalWrite(PIN_SERVO, HIGH); OCR3A = escTicks;
}
void finishServoFrame() {
  if (servoOffFrames && !--servoOffFrames) escOn = false;
}
ISR(TIMER3_COMPA_vect) {
  digitalWrite(PIN_SERVO, LOW);
  finishServoFrame();  // Off releases PWM independently of Ethernet/main loop
}

// ---------- 3. Machine state and safety transitions ----------
enum Mode : byte { LOCAL_SPEED, WEB_POSITION };
enum State : byte { LOCAL_STOPPED, LOCAL_MOVING, WEB_UNHOMED, HOMING,
  WEB_MOVING, WEB_READY, WEB_COMPLETE, ABORTED, LIMIT_BLOCKED, ESTOPPED };
enum Source : byte { OWNER_NONE, OWNER_USB, OWNER_NETWORK };

Mode mode = LOCAL_SPEED;
State state = LOCAL_STOPPED;
Source owner = OWNER_NONE;
int8_t localCommand = 0;
bool estop = false, driverEnabled = false, accepted = true;
long target = 0;
unsigned int commandId = 0;
unsigned long enabledAtMs = 0, ownerAtMs = 0, lastStatusMs = 0;
const char *reason = "boot", *error = "none";

void halt(State next, const char *why, bool clearLocal = true) {
  stopPulses();
  digitalWrite(PIN_ENABLE, DRIVER_DISABLED);
  driverEnabled = false;
  if (clearLocal) localCommand = 0;
  state = estop ? ESTOPPED : next;
  reason = why;
}

bool driverReady() {
  if (!driverEnabled) {
    digitalWrite(PIN_ENABLE, DRIVER_ENABLED);
    driverEnabled = true; enabledAtMs = millis();
  }
  return millis() - enabledAtMs >= DRIVER_WAKE_MS;
}

void serviceMotion() {
  if (estop) {
    if (moving() || driverEnabled) halt(ESTOPPED, "emergency_stop");
    return;
  }
  int8_t wanted = mode == LOCAL_SPEED ? localCommand :
      (state == WEB_MOVING ? (target > 0 ? 1 : -1) : 0);
  if (!wanted) {
    if (moving() || driverEnabled) halt(state, reason, false);
    return;
  }
  if (!driverReady()) return;
  if (targetReached()) { halt(WEB_COMPLETE, "move_complete", false); return; }
  int8_t active; ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { active = pulseDirection; }
  if (!moving() || active != wanted) {
    startPulses(wanted, mode == WEB_POSITION, target);
    state = mode == WEB_POSITION ? WEB_MOVING : LOCAL_MOVING;
    reason = "none";
  }
  serviceRamp();
}

// ---------- 4. V1 command parser and compact status contract ----------
bool parseLong(char *text, long &value) {
  if (!text || !*text) return false;
  char *end; value = strtol(text, &end, 10);
  return end != text && !*end;
}
void reject(const char *why) { accepted = false; error = why; }
bool claimable(byte source) {
  if (owner == OWNER_NONE || owner == source) return true;
  reject(owner == OWNER_USB ? "owned_by_usb" : "owned_by_network"); return false;
}
void accept(byte source, const char *why = "none") {
  accepted = true; error = "none"; reason = why;
  owner = Source(source); ownerAtMs = millis();
}

void processCommand(char *line, byte source) {
  accepted = true; error = "none";
  if (!strcmp(line, "V1 E1")) {
    estop = true; setEsc(false); setRelay5(false); halt(ESTOPPED, "emergency_stop"); return;
  }
  if (!strcmp(line, "V1 X")) { halt(ABORTED, "operator_stop"); return; }
  if (!strcmp(line, "V1 B0")) { setEsc(false); return; }  // Stop bypasses ownership
  if (!strcmp(line, "V1 K0")) { setRelay5(false); return; }
  if (!claimable(source)) return;
  if (!strcmp(line, "V1 K1")) {
    if (estop) return reject("emergency_stop");
    setRelay5(true); return accept(source);
  }
  if (!strcmp(line, "V1 E0")) {
    if (moving()) return reject("busy");
    estop = false; state = mode == WEB_POSITION ? WEB_READY : LOCAL_STOPPED;
    return accept(source, "estop_reset");
  }
  if (!strcmp(line, "V1 T1400") || !strcmp(line, "V1 T1600")) {
    if (estop) return reject("emergency_stop");
    servoSwitchOn = !strcmp(line, "V1 T1400");
    escOnUs = servoSwitchOn ? SERVO_ON_US : SERVO_OFF_US;
    setEsc(true, servoSwitchOn ? 0 : SERVO_OFF_SETTLE_FRAMES); return accept(source);
  }
  if (!strcmp(line, "V1 B1") || !strncmp(line, "V1 J", 4) || !strncmp(line, "V1 P", 4))
    return reject("positional_servo");
  if (estop) return reject("emergency_stop");
  if (!strncmp(line, "V1 S", 4)) {
    long value;
    if (moving() || driverEnabled) return reject("busy");
    if (!parseLong(line + 4, value) || value < MIN_SPS || value > MAX_SPS)
      return reject("speed_range");
    cruiseSps = value; return accept(source);
  }
  if (!strcmp(line, "V1 M0") || !strcmp(line, "V1 M1")) {
    if (moving() || driverEnabled) return reject("busy");
    mode = line[4] == '1' ? WEB_POSITION : LOCAL_SPEED; localCommand = 0;
    state = mode == WEB_POSITION ? WEB_READY : LOCAL_STOPPED;
    return accept(source, "mode_changed");
  }
  if (!strncmp(line, "V1 R", 4)) {
    long value;
    if (mode != LOCAL_SPEED) return reject("wrong_mode");
    if (!parseLong(line + 4, value) || value < -1 || value > 1)
      return reject("run_range");
    localCommand = value;
    if (!value) halt(LOCAL_STOPPED, "run_off");
    return accept(source, value ? "none" : "run_off");
  }
  if (!strcmp(line, "V1 H")) return reject("home_switch_unavailable");
  if (!strncmp(line, "V1 G", 4)) {
    if (mode != WEB_POSITION) return reject("wrong_mode");
    if (moving() || driverEnabled) return reject("busy");
    char *first = strchr(line + 4, ',');
    char *second = first ? strchr(first + 1, ',') : nullptr;
    if (!first || !second) return reject("move_grammar");
    *first = *second = 0;
    long delta, sps, id;
    if (!parseLong(line + 4, delta) || !parseLong(first + 1, sps) ||
        !parseLong(second + 1, id)) return reject("move_grammar");
    if (!delta || labs(delta) > MAX_RELATIVE_PULSES) return reject("distance_range");
    if (sps < MIN_SPS || sps > MAX_SPS) return reject("speed_range");
    if (id < 1 || id > 65535) return reject("id_range");
    cruiseSps = sps; target = delta; commandId = id;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { pulsePosition = 0; }
    state = WEB_MOVING; return accept(source);
  }
  reject("unknown_command");
}

// ---------- 4b. Read-only AbsoluteDRO Plus (same 52-bit format as the Yun) ----------
constexpr byte DRO_FRAME_BYTES = 7, DRO_FRAME_BITS = 52, DRO_HEADER_BITS = 16;
constexpr unsigned long DRO_STALE_MS = 250;
volatile byte droCaptureFrame[DRO_FRAME_BYTES], droCompletedFrame[DRO_FRAME_BYTES];
volatile byte droCaptureBit = 0, droHeaderOnes = 0, droDroppedFrames = 0;
volatile bool droFrameReady = false;
volatile unsigned long droCompletedAtMs = 0;
volatile unsigned long droEdges = 0;  // wraps; diagnostic only, not a sample count
bool droHasPosition = false;
long droPositionHundredthsMm = 0, droReferenceHundredthsMm = 0;
unsigned long droLastValidAtMs = 0, droValidFrames = 0;
byte droRejectedFrames = 0;

void captureDroClock() {
  // D6 is PH3 on the MAXI Automation. Sample directly on D2's falling edge;
  // no parsing, printing or motor-timer changes inside this interrupt.
  bool high = (PINH & _BV(PH3)) != 0;
  ++droEdges;
  if (!droCaptureBit) {
    if (!high) { droHeaderOnes = 0; return; }
    if (++droHeaderOnes == DRO_HEADER_BITS) {
      droCaptureFrame[0] = droCaptureFrame[1] = 0xFF;
      for (byte i = 2; i < DRO_FRAME_BYTES; ++i) droCaptureFrame[i] = 0;
      droCaptureBit = DRO_HEADER_BITS;
    }
    return;
  }
  if (high) droCaptureFrame[droCaptureBit >> 3] |= _BV(droCaptureBit & 7);
  if (++droCaptureBit < DRO_FRAME_BITS) return;
  if (!droFrameReady) {
    for (byte i = 0; i < DRO_FRAME_BYTES; ++i) droCompletedFrame[i] = droCaptureFrame[i];
    droCompletedAtMs = millis();
    droFrameReady = true;
  } else if (droDroppedFrames < 255) ++droDroppedFrames;
  droCaptureBit = droHeaderOnes = 0;
}

byte droNibble(const byte *frame, byte digit) {
  return (frame[digit >> 1] >> ((digit & 1) * 4)) & 15;
}

bool decodeDroFrame(const byte *frame, long *positionMm100) {
  // Four F nibbles, sign (0/8), six BCD digits, two decimals, metric units.
  for (byte i = 0; i < 4; ++i) if (droNibble(frame, i) != 15) return false;
  byte sign = droNibble(frame, 4);
  if ((sign != 0 && sign != 8) || droNibble(frame, 11) != 2 ||
      droNibble(frame, 12) != 0) return false;
  long value = 0;
  for (byte i = 5; i <= 10; ++i) {
    byte digit = droNibble(frame, i);
    if (digit > 9) return false;
    value = value * 10 + digit;
  }
  *positionMm100 = sign == 8 ? -value : value;
  return true;
}

void pollDroFrames() {
  byte frame[DRO_FRAME_BYTES]; unsigned long capturedAt;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (!droFrameReady) return;
    for (byte i = 0; i < DRO_FRAME_BYTES; ++i) frame[i] = droCompletedFrame[i];
    capturedAt = droCompletedAtMs;
    droFrameReady = false;
  }
  long value;
  if (!decodeDroFrame(frame, &value)) {
    if (droRejectedFrames < 255) ++droRejectedFrames;
    return;
  }
  if (!droHasPosition) droReferenceHundredthsMm = value;
  droHasPosition = true;
  droPositionHundredthsMm = value;
  droLastValidAtMs = capturedAt;  // delayed processing must not make old data fresh
  if (droValidFrames < 0xFFFFFFFFUL) ++droValidFrames;
}

long droSampleAgeMs() {
  if (!droHasPosition) return -1;
  unsigned long age = millis() - droLastValidAtMs;
  return age > 0x7FFFFFFFUL ? 0x7FFFFFFFL : long(age);
}

unsigned long droClockEdges() {
  unsigned long edges;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { edges = droEdges; }
  return edges;
}

void writeStatus(Print &out, bool ack = false) {
  if (ack) {
    out.print(F("{\"v\":1,\"t\":\"a\",\"ok\":")); out.print(accepted);
    out.print(F(",\"e\":\"")); out.print(error); out.println(F("\"}")); return;
  }
  out.print(F("{\"v\":1,\"t\":\"s\",\"d4\":")); out.print(localCommand ? 0 : 1);
  out.print(F(",\"d5\":")); out.print(localCommand < 0 ? 0 : 1);
  out.print(F(",\"d6\":-1,\"d8\":-1,\"lx\":0,\"lp\":0,\"ln\":0,\"b\":0,\"r\":\"")); out.print(reason);
  out.print(F("\",\"sps\":")); out.print(signedSps());
  out.print(F(",\"csps\":")); out.print(cruiseSps);
  out.print(F(",\"aps\":")); out.print(moving() ? labs(signedSps()) : 0);
  out.print(F(",\"ds\":1,\"en\":")); out.print(driverEnabled);
  long age = droSampleAgeMs();
  out.print(F(",\"ut\":1,\"dc\":1,\"df\":")); out.print(age >= 0 && age <= long(DRO_STALE_MS) ? 1 : 0);
  out.print(F(",\"dr\":")); out.print(droPositionHundredthsMm);
  out.print(F(",\"dd\":")); out.print(droPositionHundredthsMm - droReferenceHundredthsMm);
  out.print(F(",\"da\":")); out.print(age);
  out.print(F(",\"dq\":")); out.print(droValidFrames);
  out.print(F(",\"dx\":")); out.print((static_cast<unsigned int>(droRejectedFrames) << 8) | droDroppedFrames);
  out.print(F(",\"dce\":")); out.print(droClockEdges());
  out.print(F(",\"dcl\":")); out.print(digitalRead(PIN_DRO_CLOCK));
  out.print(F(",\"ddl\":")); out.print(digitalRead(PIN_DRO_DATA));
  out.print(F(",\"m\":")); out.print(mode);
  out.print(F(",\"h\":0,\"a\":1,\"e\":")); out.print(estop);
  out.print(F(",\"bo\":")); out.print(escOn); out.print(F(",\"bp\":")); out.print(escOnUs);
  out.print(F(",\"sv\":4"));
  out.print(F(",\"sn\":")); out.print(SERVO_NEUTRAL_US);
  out.print(F(",\"so\":")); out.print(servoSwitchOn);
  out.print(F(",\"r5\":")); out.print(relay5On);
  out.print(F(",\"mv\":")); out.print(moving()); out.print(F(",\"st\":")); out.print(state);
  out.print(F(",\"p\":")); out.print(position()); out.print(F(",\"g\":")); out.print(target);
  out.print(F(",\"c\":")); out.print(commandId); out.print(F(",\"o\":")); out.print(owner);
  out.println('}');
}

// ---------- 5. USB/Ethernet service and safe startup ----------
// Reuse the formatter with bounded storage, never a heap-allocated String.
class MessageBuffer : public Print {
public:
  uint8_t bytes[512];
  size_t length = 0;
  bool overflow = false;
  using Print::write;
  size_t write(uint8_t value) override {
    if (length == sizeof(bytes)) { overflow = true; return 0; }
    bytes[length++] = value;
    return 1;
  }
};

MessageBuffer serialOutput;
constexpr size_t SERIAL_ACK_RESERVE = 96;  // exceeds the longest V1 acknowledgement

void serviceSerialOutput() {
  // Arduino min() is a macro: snapshot changing UART capacity exactly once.
  const size_t available = Serial.availableForWrite();
  size_t count = min(serialOutput.length, available);
  if (!count) return;  // never wait for the 9600-baud UART to free space
  size_t sent = Serial.write(serialOutput.bytes, count);
  serialOutput.length -= sent;
  memmove(serialOutput.bytes, serialOutput.bytes + sent, serialOutput.length);
}

constexpr byte COMMAND_SIZE = 48;
char usbBuffer[COMMAND_SIZE];
byte usbLength = 0;

void serviceUsb() {
  // Preserve complete status/ACK frames in order. Apply backpressure to USB
  // input only if another acknowledgement would not fit; Ethernet still runs.
  while (Serial.available() &&
         serialOutput.length <= sizeof(serialOutput.bytes) - SERIAL_ACK_RESERVE) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      usbBuffer[usbLength] = 0;
      if (usbLength) processCommand(usbBuffer, OWNER_USB);
      writeStatus(serialOutput, true); usbLength = 0;
    } else if (usbLength + 1 < COMMAND_SIZE) usbBuffer[usbLength++] = c;
    else { usbLength = 0; reject("command_too_long"); }
  }
}

void decodeUrl(char *text) {
  char *read = text, *write = text;
  while (*read) {
    if (*read == '+') { *write++ = ' '; ++read; }
    else if (*read == '%' && read[1] && read[2]) {
      char hex[3] = {read[1], read[2], 0};
      *write++ = strtol(hex, nullptr, 16); read += 3;
    } else *write++ = *read++;
  }
  *write = 0;
}

// Print's flash-string overload writes one byte at a time. Buffer the complete
// HTTP response before touching Ethernet, whose write() performs a TCP send.
// Fixed storage avoids heap fragmentation; overflow never emits partial JSON.
void writeDroDiagnostic(Print &out) {
  // Snapshot registers directly: no digitalRead/PWM side effects, no pin writes.
  const byte input = PINE, direction = DDRE, pullup = PORTE;
  out.print(F("{\"pine\":")); out.print(input);
  out.print(F(",\"ddre\":")); out.print(direction);
  out.print(F(",\"porte\":")); out.print(pullup);
  out.print(F(",\"eimsk\":")); out.print(EIMSK);
  out.print(F(",\"eicrb\":")); out.print(EICRB);
  out.print(F(",\"tccr3a\":")); out.print(TCCR3A);
  out.print(F(",\"clock_bit\":")); out.print((input >> PE4) & 1);
  out.print(F(",\"pinh\":")); out.print(PINH);
  out.print(F(",\"ddrh\":")); out.print(DDRH);
  out.print(F(",\"edges\":")); out.print(droClockEdges());
  out.println('}');
}
void writeHttpResponse(Print &client, bool ack, bool diagnostic = false) {
  static MessageBuffer response;
  response.length = 0; response.overflow = false;
  response.println(F("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\n"));
  if (diagnostic) writeDroDiagnostic(response); else writeStatus(response, ack);
  if (response.overflow) {
    const char failure[] = "HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\nResponse buffer overflow\r\n";
    client.write(reinterpret_cast<const uint8_t *>(failure), sizeof(failure) - 1);
  } else {
    client.write(response.bytes, response.length);
  }
}

void serviceNetwork() {
  EthernetClient client = server.available();
  if (!client) return;
  pollDroFrames();
  char request[112] = {}; byte length = 0;
  unsigned long deadline = millis() + 100;
  while (client.connected() && long(deadline - millis()) > 0) {
    serviceMotion();
    if (!client.available()) continue;
    char c = client.read();
    if (c == '\n') break;
    if (c != '\r' && length + 1 < sizeof(request)) request[length++] = c;
  }
  char *value = strstr(request, "/command?value=");
  if (value) {
    value += 15; char *space = strchr(value, ' '); if (space) *space = 0;
    decodeUrl(value); processCommand(value, OWNER_NETWORK);
  }
  writeHttpResponse(client, value != nullptr, !strncmp(request, "GET /diagnostic ", 16));
  delay(1); client.stop();
  server.begin();  // replenish before the next loop iteration
}

void setup() {
  setRelay5(false); pinMode(PIN_RELAY5, OUTPUT);
  digitalWrite(PIN_ENABLE, DRIVER_DISABLED); pinMode(PIN_ENABLE, OUTPUT);
  digitalWrite(PIN_STEP, STEP_IDLE); digitalWrite(PIN_DIR, DIR_REVERSE);
  pinMode(PIN_STEP, OUTPUT); pinMode(PIN_DIR, OUTPUT);
  pinMode(PIN_DRO_CLOCK, INPUT); pinMode(PIN_DRO_DATA, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_DRO_CLOCK), captureDroClock, FALLING);
  digitalWrite(PIN_SERVO, LOW); pinMode(PIN_SERVO, OUTPUT);

  TCCR1A = 0; TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  TIMSK1 &= ~_BV(OCIE1A);
  TCCR3A = _BV(WGM31); TCCR3B = _BV(WGM33) | _BV(WGM32) | _BV(CS31);
  ICR3 = 20000U * ESC_TICKS_PER_US - 1; OCR3A = escTicks;
  TIMSK3 = _BV(TOIE3) | _BV(OCIE3A);

  Serial.begin(9600);
  Ethernet.begin(mac, ip, dns, gateway, subnet);
  for (byte i = 0; i < HTTP_LISTENERS; ++i) server.begin();
  Serial.println(F("CONTROLLINO motion control ready: stopped and disabled."));
}

void loop() {
  pollDroFrames(); serviceUsb(); serviceNetwork(); serviceMotion();
  if (owner != OWNER_NONE && !moving() && !localCommand &&
      millis() - ownerAtMs >= OWNER_RELEASE_MS) owner = OWNER_NONE;
  unsigned long interval = moving() ? 100 : 200;  // fresh DRO telemetry even while stopped
  // Coalesce telemetry instead of building a backlog when UART bandwidth is
  // insufficient. USB ACKs share this FIFO and cannot interleave a JSON frame.
  if (!serialOutput.length && millis() - lastStatusMs >= interval) {
    writeStatus(serialOutput); lastStatusMs = millis();
  }
  serviceSerialOutput();
}

// CONTROLLINO MAXI Automation motion controller (100.101.00)
// Version and auxiliary-output capability: controllino_firmware.h
// Shares the AbsoluteDRO protocol with the Yún controller.
//
// Wiring and electrical polarity: wiring_controllino.h
//
// Installed hardware has no physical run/direction switches or limits.
// V1 R-1/R0/R1 provides software Reverse/Stop/Forward in Local Speed mode.
// H remains protocol-compatible but is rejected until a home switch exists.
// D6/D8=-1 report absent limits. DRO freshness comes from validated input frames.

#include <SPI.h>
#include <Ethernet.h>
#include <math.h>
#include <util/atomic.h>
#include "wiring_controllino.h"
#include "absolute_dro_avr.h"

// ---------- 1. Pin map and fixed configuration ----------
constexpr long MIN_SPS = 25, MAX_SPS = 2520, DEFAULT_SPS = 1000;
constexpr long MAX_RELATIVE_PULSES = 34565;  // conservative; recalibrate SRX/motor
constexpr unsigned long STEP_TIMER_HZ = F_CPU / 64UL;
constexpr unsigned long DRIVER_WAKE_MS = 250, OWNER_RELEASE_MS = 2000;
constexpr unsigned int ESC_OFF_US = 1000, ESC_MAX_US = 2000;
constexpr unsigned int ESC_TICKS_PER_US = F_CPU / 8UL / 1000000UL;

byte mac[] = {0x02, 0x43, 0x4F, 0x4E, 0x54, 0x01};
IPAddress ip(10, 77, 0, 10), dns(10, 77, 0, 2), gateway(10, 77, 0, 2);
IPAddress subnet(255, 255, 255, 0);
EthernetServer server(80);

// Read-only DRO: X1 SCL clock / Digital 4 data (see wiring_controllino.h).
AbsoluteDroReader dro;
void onDroClock() { dro.onClock(); }

// ---------- 2. Hardware-timed STEP and ESC outputs ----------
volatile bool pulseOn = false, pulseFinite = false, pulseReached = false;
volatile int8_t pulseDirection = 1;
volatile long pulsePosition = 0, pulseTarget = 0, scheduledSps = 0;
volatile uint16_t pendingCompare = 0;
volatile long pendingSps = 0;
volatile bool comparePending = false;
long cruiseSps = DEFAULT_SPS;

volatile unsigned int auxTicks = ESC_OFF_US * ESC_TICKS_PER_US;
unsigned int escOnUs = 1200;
bool escOn = false;
volatile bool auxPulseEnabled = !AUX_IS_SERVO;
unsigned int servoPulseUs = SERVO_DEFAULT_US;
volatile byte servoReleaseFrames = 0;  // 50 Hz countdown, independent of network/USB.

void setServoPulse(unsigned int pulseUs, byte releaseFrames = 0) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    servoReleaseFrames = pulseUs ? releaseFrames : 0;
    auxPulseEnabled = pulseUs != 0;
    if (pulseUs) {
      servoPulseUs = pulseUs;
      auxTicks = pulseUs * ESC_TICKS_PER_US;
    } else digitalWrite(PIN_AUX, LOW);
  }
}
bool solenoid4On = false;

void setSolenoid4(bool on) {
  solenoid4On = on;
  digitalWrite(PIN_SOLENOID4, on ? SOLENOID_ON : SOLENOID_OFF);
}

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
}

void startPulses(int8_t direction, bool finite, long target) {
  stopPulses();
  digitalWrite(PIN_DIR, direction > 0 ? DIR_FORWARD : DIR_REVERSE);
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    pulseDirection = direction; pulseFinite = finite; pulseTarget = target;
    pulseReached = false; scheduledSps = cruiseSps;
    OCR1A = compareFor(scheduledSps); TCNT1 = 0; TIFR1 = _BV(OCF1A);
    pulseOn = true; TIMSK1 |= _BV(OCIE1A);
  }
}

// Apply speed changes at a pulse boundary, without acceleration or deceleration.
void serviceSpeed() {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    if (pulseOn && scheduledSps != cruiseSps) {
      pendingCompare = compareFor(cruiseSps);
      pendingSps = cruiseSps;
      comparePending = true;
    }
  }
}

void setEsc(bool on) {
  escOn = on;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    auxTicks = (on ? escOnUs : ESC_OFF_US) * ESC_TICKS_PER_US;
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
  if (servoReleaseFrames && --servoReleaseFrames == 0) auxPulseEnabled = false;
  if (auxPulseEnabled) digitalWrite(PIN_AUX, HIGH);
  OCR3A = auxTicks;
}
ISR(TIMER3_COMPA_vect) { digitalWrite(PIN_AUX, LOW); }

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
bool statusPending = false;
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
  serviceSpeed();
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
  statusPending = true;  // Publish actual state after servicing this command.
  accepted = true; error = "none";
  if (!strcmp(line, "V1 E1")) {
    estop = true;
    if (AUX_IS_SERVO) setServoPulse(0); else setEsc(false); setSolenoid4(false); halt(ESTOPPED, "emergency_stop"); return;
  }
  if (!strcmp(line, "V1 X")) { halt(ABORTED, "operator_stop"); return; }
  if (!claimable(source)) return;
  if (!strcmp(line, "V1 E0")) {
    if (moving()) return reject("busy");
    estop = false; state = mode == WEB_POSITION ? WEB_READY : LOCAL_STOPPED;
    return accept(source, "estop_reset");
  }
  if (!strcmp(line, "V1 L4,0") || !strcmp(line, "V1 L4,1")) {
    bool on = line[6] == '1';
    if (on && estop) return reject("emergency_stop");
    setSolenoid4(on); return accept(source);
  }
  if (!strncmp(line, "V1 A", 4)) {
    if (!AUX_IS_SERVO) return reject("servo_unavailable");
    long pulse, releaseMs = 0;
    char *separator = strchr(line + 4, ',');
    if (separator) {
      *separator = '\0';
      if (!parseLong(separator + 1, releaseMs) || releaseMs < 20 || releaseMs > 5000)
        return reject("servo_release_range");
    }
    if (!parseLong(line + 4, pulse) ||
        (pulse != 0 && (pulse < SERVO_MIN_US || pulse > SERVO_MAX_US)))
      return reject("servo_pulse_range");
    if (pulse && estop) return reject("emergency_stop");
    if (!pulse && separator) return reject("servo_release_range");
    setServoPulse(pulse, (releaseMs + 19) / 20); return accept(source);
  }
  if (!strcmp(line, "V1 B0") || !strcmp(line, "V1 B1")) {
    if (AUX_IS_SERVO) return reject("esc_unavailable");
    bool on = line[4] == '1';
    if (on && estop) return reject("emergency_stop");
    setEsc(on); return accept(source);
  }
  if (!strncmp(line, "V1 P", 4)) {
    if (AUX_IS_SERVO) return reject("esc_unavailable");
    long value;
    if (!parseLong(line + 4, value) || value < ESC_OFF_US || value > ESC_MAX_US)
      return reject("pulse_range");
    escOnUs = value; setEsc(escOn); return accept(source);
  }
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
  out.print(F(",\"ut\":1"));
  out.print(F(",\"sol4\":")); out.print(solenoid4On);
  dro.writeStatus(out, millis());
  out.print(F(",\"m\":")); out.print(mode);
  out.print(F(",\"h\":0,\"a\":1,\"e\":")); out.print(estop);
  out.print(F(",\"fw\":\"")); out.print(FIRMWARE_VERSION);
  out.print(F("\",\"aux\":\"")); out.print(AUX_IS_SERVO ? F("servo") : F("esc"));
  out.print(F("\",\"apin\":")); out.print(PIN_AUX);
  if (AUX_IS_SERVO) {
    out.print(F(",\"sv\":")); out.print(auxPulseEnabled);
    out.print(F(",\"sp\":")); out.print(servoPulseUs);
    out.print(F(",\"smin\":")); out.print(SERVO_MIN_US);
    out.print(F(",\"smax\":")); out.print(SERVO_MAX_US);
    out.print(F(",\"srel\":1,\"sr\":")); out.print(servoReleaseFrames);
  } else {
  out.print(F(",\"bo\":")); out.print(escOn); out.print(F(",\"bp\":")); out.print(escOnUs);
  }
  out.print(F(",\"mv\":")); out.print(moving()); out.print(F(",\"st\":")); out.print(state);
  out.print(F(",\"p\":")); out.print(position()); out.print(F(",\"g\":")); out.print(target);
  out.print(F(",\"c\":")); out.print(commandId); out.print(F(",\"o\":")); out.print(owner);
  out.println('}');
}

// ---------- 5. USB/Ethernet service and safe startup ----------
constexpr byte COMMAND_SIZE = 48;
char usbBuffer[COMMAND_SIZE];
byte usbLength = 0;

void serviceUsb() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      usbBuffer[usbLength] = 0;
      if (usbLength) processCommand(usbBuffer, OWNER_USB);
      writeStatus(Serial, true); usbLength = 0;
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

void serviceNetwork() {
  EthernetClient client = server.available();
  if (!client) return;
  char request[112] = {}; byte length = 0;
  unsigned long deadline = millis() + 100;
  while (client.connected() && long(deadline - millis()) > 0) {
    dro.poll();
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
  client.println(F("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\n"));
  writeStatus(client, value != nullptr); delay(1); client.stop();
}

void setup() {
  digitalWrite(PIN_SOLENOID4, SOLENOID_OFF); pinMode(PIN_SOLENOID4, OUTPUT);
  digitalWrite(PIN_ENABLE, DRIVER_DISABLED); pinMode(PIN_ENABLE, OUTPUT);
  digitalWrite(PIN_STEP, STEP_IDLE); digitalWrite(PIN_DIR, DIR_REVERSE);
  pinMode(PIN_STEP, OUTPUT); pinMode(PIN_DIR, OUTPUT);
  digitalWrite(PIN_AUX, LOW); pinMode(PIN_AUX, OUTPUT);

  TCCR1A = 0; TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);
  TIMSK1 &= ~_BV(OCIE1A);
  TCCR3A = _BV(WGM31); TCCR3B = _BV(WGM33) | _BV(WGM32) | _BV(CS31);
  ICR3 = 20000U * ESC_TICKS_PER_US - 1; OCR3A = auxTicks;
  TIMSK3 = _BV(TOIE3) | _BV(OCIE3A);

  dro.begin(PIN_DRO_CLOCK, PIN_DRO_DATA, onDroClock);
  Serial.begin(9600);
  Ethernet.begin(mac, ip, dns, gateway, subnet); server.begin();
  Serial.println(F("CONTROLLINO motion control ready: stopped and disabled."));
}

void loop() {
  dro.poll();
  serviceUsb(); serviceNetwork(); serviceMotion();
  if (owner != OWNER_NONE && !moving() && !localCommand &&
      millis() - ownerAtMs >= OWNER_RELEASE_MS) owner = OWNER_NONE;
  unsigned long interval = moving() ? 100 : 1000;
  if (statusPending || millis() - lastStatusMs >= interval) {
    writeStatus(Serial); lastStatusMs = millis(); statusPending = false;
  }
}

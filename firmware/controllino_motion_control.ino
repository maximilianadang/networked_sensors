// CONTROLLINO MAXI Automation motion controller (100.101.00)
// Version and auxiliary-output capability: controllino_firmware.h
// LinACE PWM by default; the legacy AbsoluteDRO reader remains selectable.
//
// Wiring and electrical polarity: wiring_controllino.h
//
// Installed hardware has no physical run/direction switches.
// V1 R-1/R0/R1 requests negative/stop/positive pulses in Local Speed mode.
// DI0/A12: bottom/Reverse; DI1/A13: top/Forward. HIGH clear, LOW actuated.
// Home retracts to the bottom (negative) limit at the requested pulse rate.
// Legacy d6/d8 fields represent positive/negative pulse directions, not pins.

#include <SPI.h>
#include <Ethernet.h>
#include <math.h>
#include <errno.h>
#include <limits.h>
#include <util/atomic.h>
#include "wiring_controllino.h"
#if CONTROLLINO_DRO_KIND == DRO_BACKEND_LINACE
#include "linace_avr.h"
#else
#include "absolute_dro_avr.h"
#endif
#include "pressure_adc.h"
#include "relay_bank.h"
#include "directional_limits.h"
#include "stepper_pwm_avr.h"
#include "stream_io.h"
#include "ethernet_stream.h"
static_assert(PIN_STEP == 3, "Timer3 OC3C STEP requires Arduino D3");
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
#include "cubemars_can.h"
CubeMarsCan cubeMars(PIN_CAN_CS);
#endif

// ---------- 1. Pin map and fixed configuration ----------
// E09050: 5.08 mm/rev, SRX SW5–SW8 ON/ON/ON/OFF = 1000 pulses/rev.
// Host speed bounds: 0.1–10 mm/s; nominal 1.5 mm/s at boot.
constexpr long MIN_SPS = 20, MAX_SPS = 1969, DEFAULT_SPS = 295;
constexpr unsigned long DRIVER_WAKE_MS = 250, OWNER_RELEASE_MS = 2000;
constexpr unsigned int ESC_OFF_US = 1000, ESC_MAX_US = 2000;
constexpr unsigned int ESC_TICKS_PER_US = F_CPU / 8UL / 1000000UL;

byte mac[] = {0x02, 0x43, 0x4F, 0x4E, 0x54, 0x01};
IPAddress ip(10, 77, 0, 10), dns(10, 77, 0, 2), gateway(10, 77, 0, 2);
IPAddress subnet(255, 255, 255, 0);
EthernetServer server(80);

// Read-only encoder uses the same position/freshness contract for both readers.
#if CONTROLLINO_DRO_KIND == DRO_BACKEND_LINACE
LinaceReader dro;
ISR(TIMER5_OVF_vect) { dro.onOverflow(); }
#else
AbsoluteDroReader dro;
#endif
void onDroClock() { dro.onClock(); }
PressureAdc pressures;
RelayBank solenoids;
DirectionalLimits limits;

// ---------- 2. Timer3 hardware STEP PWM and Timer1 auxiliary output ----------
StepperPwm stepper;
long cruiseSps = DEFAULT_SPS;

volatile unsigned int auxTicks = ESC_OFF_US * ESC_TICKS_PER_US;
unsigned int escOnUs = 1200;
bool escOn = false;
volatile bool auxPulseEnabled = AUX_IS_ESC;
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
bool moving() { return stepper.moving(); }
bool targetReached() { return stepper.reached(); }
long position() { return stepper.position(); }
long signedSps() { return stepper.signedSps(); }

void stopPulses() {
  stepper.stop();
}

void startPulses(int8_t direction, bool finite, long target) {
  stopPulses();
  digitalWrite(PIN_DIR, direction > 0 ? DIR_POSITIVE : DIR_NEGATIVE);
  stepper.start(direction, finite, target, cruiseSps);
}

// Apply speed changes at a pulse boundary, without acceleration or deceleration.
void serviceSpeed() {
  stepper.setSpeed(cruiseSps);
}

void setEsc(bool on) {
  escOn = on;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    auxTicks = (on ? escOnUs : ESC_OFF_US) * ESC_TICKS_PER_US;
  }
}

ISR(TIMER3_COMPC_vect) { stepper.onStep(); }
ISR(TIMER3_OVF_vect) { stepper.onPeriodEnd(); }
ISR(TIMER1_OVF_vect) {
  if (servoReleaseFrames && --servoReleaseFrames == 0) auxPulseEnabled = false;
  if (auxPulseEnabled) digitalWrite(PIN_AUX, HIGH);
  OCR1A = auxTicks;
}
ISR(TIMER1_COMPA_vect) { digitalWrite(PIN_AUX, LOW); }

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
      (state == HOMING ? -1 : (state == WEB_MOVING ? (target > 0 ? 1 : -1) : 0));
  if (!wanted) {
    if (moving() || driverEnabled) halt(state, reason, false);
    return;
  }
  if (limits.blocks(wanted)) {
    if (state == HOMING) halt(WEB_READY, "home_complete");
    else halt(LIMIT_BLOCKED, wanted > 0 ? "positive_limit" : "negative_limit");
    statusPending = true;
    return;
  }
  if (!driverReady()) return;
  if (targetReached()) { halt(WEB_COMPLETE, "move_complete", false); return; }
  const int8_t active = stepper.direction();
  if (!moving() || active != wanted) {
    const bool homing = state == HOMING;
    startPulses(wanted, mode == WEB_POSITION && !homing, target);
    state = homing ? HOMING : (mode == WEB_POSITION ? WEB_MOVING : LOCAL_MOVING);
    reason = "none";
  }
  serviceSpeed();
}

void serviceRealtime() {
  if (limits.poll(micros())) statusPending = true;
  dro.poll();
  pressures.poll(millis());
  serviceMotion();
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
  cubeMars.service(millis());
#endif
}

// ---------- 4. V1 command parser and compact status contract ----------
bool parseLong(char *text, long &value) {
  if (!text || !*text) return false;
  char *end; errno = 0; value = strtol(text, &end, 10);
  return end != text && !*end && errno != ERANGE;
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
    if (AUX_IS_SERVO) setServoPulse(0); else if (AUX_IS_ESC) setEsc(false);
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
    cubeMars.release("emergency_stop", false);
#endif
    solenoids.allOff(); halt(ESTOPPED, "emergency_stop"); return;
  }
  if (!strcmp(line, "V1 X")) {
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
    cubeMars.release("operator_stop", false);
#endif
    halt(ABORTED, "operator_stop"); return;
  }
  // Torque release is available to either transport, even while faulted/E-stopped.
  if (!strcmp(line, "V1 C0")) {
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
    cubeMars.release("operator_stop"); return;
#else
    return reject("cubemars_unavailable");
#endif
  }
  if (!claimable(source)) return;
  if (!strcmp(line, "V1 E0")) {
    if (moving()) return reject("busy");
    estop = false; state = mode == WEB_POSITION ? WEB_READY : LOCAL_STOPPED;
    return accept(source, "estop_reset");
  }
  if (!strncmp(line, "V1 L", 4)) {
    char *separator = strchr(line + 4, ',');
    if (!separator) return reject("solenoid_command_grammar");
    *separator = '\0'; long id, value;
    if (!parseLong(line + 4, id) || !parseLong(separator + 1, value) ||
        !RelayBank::validId(id) || (value != 0 && value != 1))
      return reject("solenoid_command_grammar");
    bool on = value == 1;
    if (on && estop) return reject("emergency_stop");
    solenoids.set(id, on); return accept(source);
  }
  if (!strncmp(line, "V1 C", 4)) {
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
    long rpm, duration;
    char *separator = strchr(line + 4, ',');
    if (!separator) return reject("can_command_grammar");
    *separator = '\0';
    if (!parseLong(line + 4, rpm) || !parseLong(separator + 1, duration))
      return reject("can_command_grammar");
    if (estop) return reject("emergency_stop");
    if (duration < long(CubeMars::MIN_DURATION_MS) || duration > long(CubeMars::MAX_DURATION_MS))
      return reject("can_duration_range");
    const char *failure = cubeMars.start(rpm, uint32_t(duration), millis());
    if (failure) return reject(failure);
    return accept(source);
#else
    return reject("cubemars_unavailable");
#endif
  }
  // M0/M1 are existing stepper mode commands; the manual motor M has operands.
  if ((!strncmp(line, "V1 M", 4) && strchr(line + 4, ',')) ||
      !strncmp(line, "V1 U", 4) || !strncmp(line, "V1 K", 4)) {
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
    if (estop) return reject("emergency_stop");
    long rpm = 0, ramp = 0, token;
    const bool renew = line[3] == 'K', update = line[3] == 'U';
    char *tokenText = line + 4;
    if (!renew) {
      char *first = strchr(line + 4, ',');
      char *second = first ? strchr(first + 1, ',') : nullptr;
      if (!second) return reject("can_command_grammar");
      *first = *second = '\0'; tokenText = second + 1;
      if (!parseLong(line + 4, rpm) || !parseLong(first + 1, ramp))
        return reject("can_command_grammar");
    }
    if (!parseLong(tokenText, token) || token < 1) return reject("can_manual_token");
    const char *failure = renew ? cubeMars.renew(uint32_t(token), millis())
        : cubeMars.manual(rpm, ramp, uint32_t(token), millis(), update);
    if (failure) return reject(failure);
    return accept(source);
#else
    return reject("cubemars_unavailable");
#endif
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
    if (!AUX_IS_ESC) return reject("esc_unavailable");
    bool on = line[4] == '1';
    if (on && estop) return reject("emergency_stop");
    setEsc(on); return accept(source);
  }
  if (!strncmp(line, "V1 P", 4)) {
    if (!AUX_IS_ESC) return reject("esc_unavailable");
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
    if (limits.blocks(value)) return reject(value > 0 ? "positive_limit" : "negative_limit");
    localCommand = value;
    if (!value) halt(LOCAL_STOPPED, "run_off");
    return accept(source, value ? "none" : "run_off");
  }
  if (!strncmp(line, "V1 H", 4)) {
    if (mode != WEB_POSITION) return reject("wrong_mode");
    if (moving() || driverEnabled || state == HOMING || state == WEB_MOVING) return reject("busy");
    long sps;
    if (!parseLong(line + 4, sps) || sps < MIN_SPS || sps > MAX_SPS)
      return reject("speed_range");
    cruiseSps = sps; target = 0;
    state = HOMING; return accept(source);
  }
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
    if (!delta || delta == LONG_MIN) return reject("distance_range");
    if (sps < MIN_SPS || sps > MAX_SPS) return reject("speed_range");
    if (id < 1 || id > 65535) return reject("id_range");
    const int8_t direction = delta > 0 ? 1 : -1;
    if (limits.blocks(direction)) return reject(direction > 0 ? "positive_limit" : "negative_limit");
    cruiseSps = sps; target = delta; commandId = id;
    stepper.zero();
    state = WEB_MOVING; return accept(source);
  }
  reject("unknown_command");
}

void writeStatus(Print &out, bool ack = false, uint32_t requestId = 0) {
  if (ack) {
    out.print(F("{\"v\":1,\"t\":\"a\",\"ok\":")); out.print(accepted);
    out.print(F(",\"id\":")); out.print(requestId);
    out.print(F(",\"e\":\"")); out.print(error); out.println(F("\"}")); return;
  }
  out.print(F("{\"v\":1,\"t\":\"s\",\"d4\":")); out.print(localCommand ? 0 : 1);
  out.print(F(",\"d5\":")); out.print(localCommand < 0 ? 0 : 1);
  limits.writeStatus(out);
  out.print(F(",\"b\":")); out.print(state == LIMIT_BLOCKED);
  out.print(F(",\"r\":\"")); out.print(reason);
  out.print(F("\",\"sps\":")); out.print(signedSps());
  out.print(F(",\"csps\":")); out.print(cruiseSps);
  out.print(F(",\"aps\":")); out.print(moving() ? labs(signedSps()) : 0);
  out.print(F(",\"ds\":1,\"en\":")); out.print(driverEnabled);
  out.print(F(",\"ut\":1"));
  solenoids.writeStatus(out);
  out.print(F(",\"dkind\":\"")); out.print(DRO_IS_LINACE ? F("linace") : F("absolute_dro"));
  out.print('"');
#if CONTROLLINO_DRO_KIND == DRO_BACKEND_LINACE
  dro.writeStatus(out, millis());
#else
  // Keep the shared Yún legacy reader untouched; buffered printing may poll
  // the live reader, so serialize an independent copy of its reading.
  const AbsoluteDroReader reading = dro;
  reading.writeStatus(out, millis());
#endif
  pressures.writeStatus(out, millis());
  out.print(F(",\"m\":")); out.print(mode);
  out.print(F(",\"h\":0,\"a\":1,\"e\":")); out.print(estop);
  out.print(F(",\"fw\":\"")); out.print(FIRMWARE_VERSION);
  out.print(F("\",\"aux\":\"")); out.print(AUX_IS_CUBEMARS ? F("cubemars") : AUX_IS_SERVO ? F("servo") : F("esc"));
  out.print(F("\",\"apin\":")); out.print(PIN_AUX);
  if (AUX_IS_SERVO) {
    out.print(F(",\"sv\":")); out.print(auxPulseEnabled);
    out.print(F(",\"sp\":")); out.print(servoPulseUs);
    out.print(F(",\"smin\":")); out.print(SERVO_MIN_US);
    out.print(F(",\"smax\":")); out.print(SERVO_MAX_US);
    out.print(F(",\"srel\":1,\"sr\":")); out.print(servoReleaseFrames);
  } else if (AUX_IS_ESC) {
  out.print(F(",\"bo\":")); out.print(escOn); out.print(F(",\"bp\":")); out.print(escOnUs);
  }
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
  cubeMars.writeStatus(out, millis());
#endif
  out.print(F(",\"net\":\"tcp\",\"hc\":1"));
  out.print(F(",\"mv\":")); out.print(moving()); out.print(F(",\"st\":")); out.print(state);
  out.print(F(",\"p\":")); out.print(position()); out.print(F(",\"g\":")); out.print(target);
  out.print(F(",\"c\":")); out.print(commandId); out.print(F(",\"o\":")); out.print(owner);
  out.println('}');
}

// ---------- 5. USB/Ethernet service and safe startup ----------
StreamIO::Line usbLine, networkLine;
StreamIO::Output usbOutput, networkOutput;
EthernetStream network;

void receiveCommand(StreamIO::Line &reader, StreamIO::Output &output, char c, byte source) {
  const StreamIO::Line::Result result = reader.push(c);
  if (result == StreamIO::Line::MORE) return;
  uint32_t id = 0;
  char *line = reader.data();
  if (result == StreamIO::Line::TOO_LONG) reject("command_too_long");
  else if (!StreamIO::request(line, id)) reject("request_id_invalid");
  else processCommand(line, source);
  StreamIO::Frame<StreamIO::ACK_BYTES> &reply = output.reply();
  reply.clear(); writeStatus(reply, true, id);
  if (!output.commitReply() && source == OWNER_NETWORK) network.close();
}

void prepareStatus(StreamIO::Output &output) {
  if (!output.statusDue || !output.canStatus()) return;
  output.status.clear(); writeStatus(output.status); output.commitStatus();
}

void serviceUsb() {
  for (uint8_t budget = 64; budget && usbOutput.canReply() && Serial.available(); --budget)
    receiveCommand(usbLine, usbOutput, Serial.read(), OWNER_USB);
  prepareStatus(usbOutput);
  const uint16_t count = min(usbOutput.size(), uint16_t(Serial.availableForWrite()));
  if (count) usbOutput.advance(Serial.write(usbOutput.data(), count));
}

void serviceNetwork() {
  // accept() also replenishes the listener. Call it every loop, including
  // while the current client's TCP send is awaiting acknowledgement.
  EthernetClient incoming = server.accept();
  if (incoming) {
    if (network.connected()) EthernetStream::close(incoming);
    else {
      network.close(); network.client = incoming;
      networkLine.clear(); networkOutput.clear();
    }
  }
  if (!network.connected()) {
    network.close(); networkLine.clear(); networkOutput.clear(); return;
  }
  const int completed = network.completed();
  if (completed < 0) { networkLine.clear(); networkOutput.clear(); return; }
  if (completed) networkOutput.advance(completed);
  for (uint8_t budget = 64; budget && networkOutput.canReply() && network.client.available(); --budget)
    receiveCommand(networkLine, networkOutput, network.client.read(), OWNER_NETWORK);
  prepareStatus(networkOutput);
  if (!network.busy()) network.send(networkOutput.data(), networkOutput.size());
}

void setup() {
  solenoids.begin(SOLENOID_PINS, SOLENOID_ON, SOLENOID_OFF);
  pressures.begin(PRESSURE_PINS);
  digitalWrite(PIN_ENABLE, DRIVER_DISABLED); pinMode(PIN_ENABLE, OUTPUT);
  digitalWrite(PIN_STEP, STEP_IDLE); digitalWrite(PIN_DIR, DIR_NEGATIVE);
  pinMode(PIN_DIR, OUTPUT); // stepper.begin() seeds and enables the STEP output.
  limits.begin(PIN_LIMIT_POS, PIN_LIMIT_NEG);
  digitalWrite(PIN_AUX, AUX_IS_CUBEMARS ? HIGH : LOW); pinMode(PIN_AUX, OUTPUT);
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
  digitalWrite(PIN_RTC_CS, LOW); pinMode(PIN_RTC_CS, OUTPUT);
  digitalWrite(PIN_SPI_SS_ETHERNET_LIB, HIGH); pinMode(PIN_SPI_SS_ETHERNET_LIB, OUTPUT);
#endif

  stepper.begin();
  TCCR1A = 0; TCCR1B = 0; TIMSK1 = 0;
  if (!AUX_IS_CUBEMARS) {
    TCCR1A = _BV(WGM11); TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS11);
    ICR1 = 20000U * ESC_TICKS_PER_US - 1; OCR1A = auxTicks;
    TIMSK1 = _BV(TOIE1) | _BV(OCIE1A);
  }

  dro.begin(PIN_DRO_CLOCK, PIN_DRO_SECONDARY, onDroClock);
  Serial.begin(AUX_IS_CUBEMARS ? 115200 : 9600);
  Ethernet.begin(mac, ip, dns, gateway, subnet); server.begin();
  // Normal LAN TCP retries. Hardware transmission is polled, so these cannot
  // hold the control loop while a peer is slow or disconnected.
  Ethernet.setRetransmissionTimeout(200); Ethernet.setRetransmissionCount(3);
  usbOutput.status.service(serviceRealtime);
  networkOutput.status.service(serviceRealtime);
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
  cubeMars.begin();
#endif
  Serial.println(F("CONTROLLINO motion control ready: stopped and disabled."));
}

void loop() {
  serviceRealtime();
  serviceUsb(); serviceNetwork(); serviceRealtime();
  if (owner != OWNER_NONE && !moving() && !localCommand &&
#if CONTROLLINO_AUX_KIND == AUX_BACKEND_CUBEMARS
      !cubeMars.run.active &&
#endif
      millis() - ownerAtMs >= OWNER_RELEASE_MS) owner = OWNER_NONE;
  unsigned long interval = AUX_IS_CUBEMARS || moving() ? 100 : 1000;
  if (statusPending || millis() - lastStatusMs >= interval) {
    usbOutput.statusDue = networkOutput.statusDue = true;
    lastStatusMs = millis(); statusPending = false;
  }
}

#include <cassert>
#include <vector>
#include <utility>
#include <iostream>
#include <limits>
#include "linace_protocol.h"
#include "linace_avr.h"
#include "pressure_adc.h"
#include "relay_bank.h"
#include "directional_limits.h"
#include "stepper_pwm_avr.h"

uint8_t TCCR5A = 0, TCCR5B = 0, TIFR5 = 0, TIMSK5 = 0;
uint16_t TCNT5 = 0;
uint8_t TCCR3A = 0, TCCR3B = 0, TCCR3C = 0, TIFR3 = 0, TIMSK3 = 0;
uint16_t TCNT3 = 0, OCR3A = 0, OCR3C = 0;
static uint32_t nowMs = 0;
static volatile uint8_t gpio[80] = {};
static std::vector<std::pair<int, int>> writes, modes;
static std::vector<int> adcReads;
static int adcGeneration = 0;
unsigned long millis() { return nowMs; }
unsigned long micros() { return nowMs * 1000; }
void delay(unsigned long) {}
void pinMode(uint8_t pin, uint8_t mode) { modes.push_back({pin, mode}); }
void digitalWrite(uint8_t pin, uint8_t level) { writes.push_back({pin, level}); }
int digitalRead(uint8_t pin) { return gpio[pin] ? HIGH : LOW; }
int analogRead(uint8_t pin) {
  adcReads.push_back(pin);
  // The first conversion is deliberately wrong after each channel change.
  return adcReads.size() % 2 ? 1023 : adcGeneration + pin;
}
volatile uint8_t *portInputRegister(uint8_t pin) { return &gpio[pin]; }
uint8_t digitalPinToPort(uint8_t pin) { return pin; }
uint8_t digitalPinToBitMask(uint8_t) { return 1; }
uint8_t digitalPinToInterrupt(uint8_t pin) { return pin; }
void attachInterrupt(uint8_t pin, void (*)(), int edge) { assert(pin == 21 && edge == CHANGE); }
static void edgeCallback() {}

static Linace::Sample frame(uint32_t high, uint32_t period = Linace::PERIOD_TICKS,
                            bool healthy = true) {
  Linace::Sample f; f.highTicks = high; f.periodTicks = period; f.statusOk = healthy;
  return f;
}
static void testProtocol() {
  int32_t position = -1;
  assert(Linace::decode(frame(2), position) && position == 0);
  assert(Linace::decode(frame(8002), position) && position == 4000);
  assert(Linace::decode(frame(90002), position) && position == 45000);
  // Same duty cycle at a slightly different measured period gives same position.
  assert(Linace::decode(frame(7814, 128000), position) && position == 4000);
  assert(!Linace::decode(frame(90004), position)); // outside this450mm encoder
  assert(!Linace::decode(frame(0), position));
  assert(!Linace::decode(frame(1), position));
  assert(!Linace::decode(frame(Linace::PERIOD_TICKS), position));
  assert(!Linace::decode(frame(8002, Linace::MIN_PERIOD_TICKS - 1), position));
  assert(!Linace::decode(frame(8002, Linace::MAX_PERIOD_TICKS + 1), position));
  assert(!Linace::decode(frame(8002, Linace::PERIOD_TICKS, false), position));
  assert(!Linace::decode(frame(0xffffffffUL, 0xffffffffUL), position));
  for (int32_t expected = 0; expected <= 45000; expected += 37)
    assert(Linace::decode(frame(uint32_t(expected + 1) * 2), position) && position == expected);

  Linace::Capture capture;
  Linace::Sample result;
  const uint32_t start = 0xffff0000UL, ms = 0xfffffff0UL;
  assert(!capture.edge(true, true, start, ms, result));
  assert(!capture.edge(false, false, start + 8002, ms + 1, result));
  // Status at the rising edge owns this position, even if Status changes midpulse.
  assert(capture.edge(true, false, start + Linace::PERIOD_TICKS, ms + 9, result));
  assert(result.highTicks == 8002 && result.periodTicks == Linace::PERIOD_TICKS);
  assert(result.sampledMs == ms && Linace::decode(result, position) && position == 4000);
  assert(!capture.edge(false, true, start + Linace::PERIOD_TICKS + 8002, ms + 10, result));
  assert(capture.edge(true, true, start + 2 * Linace::PERIOD_TICKS, ms + 17, result));
  assert(!Linace::decode(result, position)); // previous rising Status wasLOW
  capture.reset();
  assert(!capture.edge(false, true, 1, 1, result));
  assert(!capture.edge(true, true, 2, 2, result));
  assert(!capture.edge(true, true, 3, 3, result)); // missed falling edge
  assert(!capture.edge(false, true, 8005, 4, result));
  assert(!capture.edge(true, true, 3 + Linace::PERIOD_TICKS, 300, result)); // oldcycle
}

static std::string status(LinaceReader &r, uint32_t at) {
  Print out; r.writeStatus(out, at); return out.text;
}
static void testReader() {
  modes.clear(); writes.clear();
  LinaceReader r; gpio[21] = 0; gpio[42] = 1; r.begin(21, 42, edgeCallback);
  assert((modes == std::vector<std::pair<int, int>>{{21, INPUT}, {42, INPUT}}));
  assert(writes.empty()); // Neither encoder input is driven or pulled up.
  assert(TCCR5A == 0 && TCCR5B == _BV(CS50) && TIMSK5 == _BV(TOIE5));
  assert(status(r, 0) == ",\"dc\":1,\"df\":0,\"dr\":0,\"dd\":0,\"da\":-1,\"dt\":0,\"dq\":0,\"dx\":0,\"dpwm\":0,\"dstat\":1,\"dedge\":0");
  r.poll(); // Status is readable HIGH even without any captured PWM frame.
  assert(status(r, 0).find("\"dstat\":1,\"dedge\":0") != std::string::npos);
  gpio[42] = 0; r.poll();
  assert(status(r, 0).find("\"dstat\":0,\"dedge\":0") != std::string::npos);
  gpio[42] = 1; r.poll();
  assert(status(r, 0).find("\"dstat\":1,\"dedge\":0") != std::string::npos);
  uint32_t overflow = 0;
  auto edge = [&](uint32_t ticks, bool high, bool ok, uint32_t ms) {
    while (overflow < ticks / 65536) { r.onOverflow(); ++overflow; }
    TIFR5 = 0; TCNT5 = uint16_t(ticks); gpio[21] = high; gpio[42] = ok; nowMs = ms;
    r.onClock();
  };
  edge(100, true, true, 100);
  edge(8102, false, true, 101);
  edge(131172, true, true, 109); r.poll();
  assert(status(r, 110).find("\"df\":1,\"dr\":4000,\"dd\":0,\"da\":10,\"dt\":100,\"dq\":1") != std::string::npos);
  assert(status(r, 110).find("\"dpwm\":1,\"dstat\":1,\"dedge\":3") != std::string::npos);
  assert(status(r, 351).find("\"df\":0") != std::string::npos);
  gpio[42] = 0; r.poll(); // noPWMedge needed to detect power/statusloss
  assert(status(r, 111).find("\"df\":0") != std::string::npos);
  assert(status(r, 111).find("\"dstat\":0,\"dedge\":3") != std::string::npos);
  gpio[42] = 1; r.poll();
  assert(status(r, 111).find("\"df\":0") != std::string::npos); // noautomaticfreshness
  edge(262244, true, true, 116);
  edge(272246, false, true, 117);
  edge(393316, true, true, 125); r.poll();
  assert(status(r, 126).find("\"dt\":116,\"dq\":2") != std::string::npos);
  assert(status(r, 126).find("\"df\":1,\"dr\":5000,\"dd\":1000") != std::string::npos);

  // A status write remains coherent even when service invalidates midprint.
  Print out; bool once = false;
  out.beforePrint = [&] { if (!once) { once = true; gpio[42] = 0; r.poll(); } };
  r.writeStatus(out, 126);
  assert(out.text.find("\"df\":1,\"dr\":5000,\"dd\":1000") != std::string::npos);
  assert(status(r, 127).find("\"df\":0") != std::string::npos);

  // Malformed measured periods clear freshness, without advancing valid count.
  edge(524388, true, true, 133);
  edge(524488, false, true, 133);
  edge(524888, true, true, 134); r.poll();
  assert(status(r, 134).find("\"df\":0") != std::string::npos);
  assert(status(r, 134).find("\"dq\":2,\"dx\":256") != std::string::npos);
  // A busy loop retains the latest complete reading and exposes mailbox loss.
  edge(655000, true, true, 140);
  edge(663002, false, true, 141);
  edge(786072, true, true, 149);
  edge(794074, false, true, 150);
  edge(917144, true, true, 157); r.poll();
  assert(status(r, 158).find("\"dq\":3,\"dx\":257") != std::string::npos);
  // Sample ages remain correct across the millis counter rollover as well.
  gpio[42] = 0; r.poll();
  edge(1048216, true, true, 0xfffffff8UL);
  edge(1056218, false, true, 0xfffffff9UL);
  edge(1179288, true, true, 0); r.poll();
  assert(status(r, 2).find("\"da\":10,\"dt\":4294967288,\"dq\":4") != std::string::npos);

  // Pending Timer5 overflow must be counted even before its ISR runs.
  LinaceReader pending; gpio[42] = 1; pending.begin(21, 42, edgeCallback);
  TIFR5 = 0; TCNT5 = 65530; gpio[21] = 1; nowMs = 10; pending.onClock();
  TIFR5 = _BV(TOV5); TCNT5 = 7996; gpio[21] = 0; nowMs = 11; pending.onClock();
  pending.onOverflow(); pending.onOverflow();
  TIFR5 = 0; TCNT5 = 65530; gpio[21] = 1; nowMs = 18; pending.onClock(); pending.poll();
  assert(status(pending, 19).find("\"df\":1,\"dr\":4000") != std::string::npos);

  // A later LOW invalidates current health, not the preceding valid frame.
  LinaceReader statusDrop; gpio[42] = 1; statusDrop.begin(21, 42, edgeCallback);
  TIFR5 = 0; TCNT5 = 100; gpio[21] = 1; nowMs = 100; statusDrop.onClock();
  TCNT5 = 8102; gpio[21] = 0; nowMs = 101; statusDrop.onClock();
  statusDrop.onOverflow(); statusDrop.onOverflow();
  TCNT5 = 100; gpio[21] = 1; nowMs = 109; statusDrop.onClock();
  gpio[42] = 0; statusDrop.poll();
  assert(status(statusDrop, 110).find("\"df\":0,\"dr\":4000,\"dd\":0,\"da\":10,\"dt\":100,\"dq\":1") != std::string::npos);
  assert(status(statusDrop, 110).find("\"dstat\":0") != std::string::npos);
  gpio[42] = 1; statusDrop.poll();
  assert(status(statusDrop, 111).find("\"df\":0") != std::string::npos);
}

static void testStepperPwm() {
  StepperPwm s; modes.clear(); writes.clear(); s.begin();
  assert((modes == std::vector<std::pair<int, int>>{{3, INPUT}, {3, OUTPUT}}));
  assert(!s.moving() && !s.reached() && s.position() == 0);
  assert(TCCR3A == 0 && TCCR3B == 0 && TIMSK3 == 0);
  assert(TCCR3C == _BV(FOC3C)); // Seed HIGH before attaching the hardware output.
  s.start(1, true, 3, 1000);
  assert(OCR3A == 249 && OCR3C == 125);
  assert(TCCR3A == (_BV(COM3C1) | _BV(WGM31) | _BV(WGM30)));
  assert(TCCR3B == (_BV(WGM33) | _BV(WGM32) | _BV(CS31) | _BV(CS30)));
  writes.clear();
  for (int i = 1; i <= 3; ++i) {
    s.onStep(); assert(s.position() == i && s.moving());
    assert(writes.empty()); // Counting interrupts never toggle STEP.
    s.onPeriodEnd();
  }
  assert(!s.moving() && s.reached() && s.position() == 3 && s.signedSps() == 0);
  assert(TCCR3A == 0 && TCCR3B == 0 && TIMSK3 == 0);
  assert((writes == std::vector<std::pair<int, int>>{{3, HIGH}}));
  s.onStep(); s.onPeriodEnd(); assert(s.position() == 3);

  s.zero(); s.start(-1, true, -1, 378); writes.clear();
  assert(OCR3A == 660 && OCR3C == 330);
  s.onStep(); assert(s.position() == -1 && s.moving() && writes.empty());
  s.onPeriodEnd(); assert(!s.moving() && s.reached() && s.position() == -1);

  s.zero(); s.start(1, false, 0, 1000); writes.clear();
  TCNT3 = 100; s.setSpeed(500);
  assert(OCR3A == 249 && OCR3C == 125 && TCNT3 == 100);
  s.onStep(); // Stage the next cycle without resetting TCNT3 or GPIO.
  assert(OCR3A == 499 && OCR3C == 250 && s.signedSps() == 1000 && TCNT3 == 100);
  s.onPeriodEnd(); s.onStep();
  assert(s.signedSps() == 500 && s.position() == 2 && writes.empty());
  s.stop(); assert(!s.moving() && !s.reached());
  s.onStep(); s.onPeriodEnd(); assert(s.position() == 2);
  for (long speed : {20L, 1969L}) {
    s.start(1, false, 0, speed);
    assert(OCR3A >= 126 && OCR3A <= 12499 && OCR3C > 0 && OCR3C < OCR3A);
    s.stop();
  }
}

static void testAdc() {
  const byte pins[7] = {64, 65, 68, 69, 54, 55, 56};
  PressureAdc adc; adc.begin(pins); adcReads.clear();
  Print initial; adc.writeStatus(initial, 0);
  assert(initial.text == ",\"adc\":[0,0,0,0,0,0,0],\"adcage\":-1,\"adcseq\":0");
  adc.poll(9); assert(adcReads.empty());
  for (uint32_t i = 1; i <= 6; ++i) { adc.poll(i * 10); adc.poll(i * 10); }
  assert(adcReads.size() == 12);
  Print partial; adc.writeStatus(partial, 60); assert(partial.text == initial.text);
  adc.poll(70);
  assert(adcReads == std::vector<int>({64,64,65,65,68,68,69,69,54,54,55,55,56,56}));
  Print complete; adc.writeStatus(complete, 75);
  assert(complete.text == ",\"adc\":[64,65,68,69,54,55,56],\"adcage\":65,\"adcseq\":1");
  adcGeneration = 100;
  for (uint32_t i = 8; i <= 13; ++i) adc.poll(i * 10);
  Print concurrent; bool once = false;
  concurrent.beforePrint = [&] { if (!once) { once = true; adc.poll(140); } };
  adc.writeStatus(concurrent, 135);
  assert(concurrent.text == ",\"adc\":[64,65,68,69,54,55,56],\"adcage\":125,\"adcseq\":1");
  Print next; adc.writeStatus(next, 145);
  assert(next.text == ",\"adc\":[164,165,168,169,154,155,156],\"adcage\":65,\"adcseq\":2");
  // Unsigned millis rollover continues scheduled acquisition and age arithmetic.
  PressureAdc roll; roll.begin(pins);
  for (uint32_t i = 0; i < 7; ++i) roll.poll(uint32_t(0xffffffd0UL + i * 10));
  Print rolled; roll.writeStatus(rolled, 20);
  assert(rolled.text.find("\"adcage\":68,\"adcseq\":1") != std::string::npos);
}

static void testRelays() {
  const byte pins[3] = {27, 25, 26};
  RelayBank relays; writes.clear(); modes.clear();
  relays.begin(pins, HIGH, LOW);
  assert((writes == std::vector<std::pair<int,int>>({{27,0},{25,0},{26,0}})));
  assert((modes == std::vector<std::pair<int,int>>({{27,OUTPUT},{25,OUTPUT},{26,OUTPUT}})));
  assert(!relays.set(3,true) && !relays.set(7,true));
  assert(writes.size() == 3);
  assert(relays.set(4,true) && relays.set(5,true) && relays.set(6,true));
  Print on; relays.writeStatus(on); assert(on.text == ",\"sol4\":1,\"sol5\":1,\"sol6\":1");
  relays.allOff();
  Print off; relays.writeStatus(off); assert(off.text == ",\"sol4\":0,\"sol5\":0,\"sol6\":0");
  assert(writes[writes.size()-3] == std::make_pair(27,0));
  assert(writes[writes.size()-2] == std::make_pair(25,0));
  assert(writes[writes.size()-1] == std::make_pair(26,0));
  relays.set(5,true);
  Print snapshot; bool once = false;
  snapshot.beforePrint = [&] { if (!once) { once = true; relays.allOff(); } };
  relays.writeStatus(snapshot); assert(snapshot.text == ",\"sol4\":0,\"sol5\":1,\"sol6\":0");
}

static void testLimits() {
  DirectionalLimits limits;
  modes.clear(); nowMs = 0; gpio[67] = gpio[66] = HIGH;
  limits.begin(67, 66); // DI1/top is positive; DI0/bottom is negative.
  assert((modes == std::vector<std::pair<int,int>>({{67,INPUT},{66,INPUT}})));
  assert(!limits.poll(0) && !limits.blocks(1) && !limits.blocks(-1));
  gpio[66] = LOW;
  assert(limits.poll(1000));
  assert(!limits.poll(5999) && !limits.blocks(-1));
  assert(limits.poll(6000) && limits.blocks(-1) && !limits.blocks(1));
  assert(limits.negative.qualifiedActive);
  Print active; limits.writeStatus(active);
  assert(active.text == ",\"d6\":1,\"d8\":0,\"lx\":65536,\"lp\":0,\"ln\":0,\"lppin\":67,\"lnpin\":66");
  gpio[66] = HIGH; limits.poll(6001);
  assert(!limits.negative.qualifiedActive && !limits.blocks(-1)); // HIGH releases without reset/retreat.

  gpio[67] = LOW; limits.poll(7000); gpio[67] = HIGH; limits.poll(7999);
  assert(!limits.blocks(1) && limits.positive.rejectedGlitches == 1);
  gpio[67] = LOW; limits.poll(8000); limits.poll(13000);
  assert(limits.blocks(1) && !limits.blocks(-1));
  limits.poll(13001);
  assert(limits.blocks(1));
  gpio[66] = LOW; limits.poll(14000); limits.poll(19000);
  assert(limits.blocks(1) && limits.blocks(-1) && !limits.blocks(0));

  // LOW at boot (including disconnected NC wiring) qualifies without an edge.
  nowMs = 0; limits.begin(67, 66); limits.poll(5000);
  assert(limits.blocks(1) && limits.blocks(-1));
  Print snapshot;
  snapshot.beforePrint = [&] { gpio[67] = gpio[66] = HIGH; limits.poll(5001); };
  limits.writeStatus(snapshot);
  assert(snapshot.text.find("\"d6\":0,\"d8\":0,\"lx\":196608") != std::string::npos);
  assert(!limits.blocks(1) && !limits.blocks(-1)); // Both HIGH clear all limit blocks.

  QualifiedLimitInput input = {};
  input.initialize(true, 0xfffffff0UL);
  assert(!input.update(true, uint32_t(0xfffffff0UL + 4999)));
  assert(input.update(true, uint32_t(0xfffffff0UL + 5000)));
  assert(!input.update(false, 6000));
  input.initialize(false, 0);
  for (uint32_t i = 0; i < 300; ++i) {
    assert(!input.update(true, i * 100));
    assert(!input.update(false, i * 100 + 1));
  }
  assert(input.rejectedGlitches == 255);
}
int main() {
  testProtocol(); testReader(); testStepperPwm(); testAdc(); testRelays(); testLimits();
  std::cout << "Controllino IO invariants passed\n";
}
